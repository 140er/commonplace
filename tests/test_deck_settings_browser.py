"""Settings journeys through the real shell, with optional browser availability."""

import json
import mimetypes
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import pytest

STATIC = Path(__file__).resolve().parents[1] / "commonplace/web/static"
ACTIONS = [
    {"name": name, "kind": "write", "mode": "allowed"}
    for name in [
        "task_add",
        "memory_save",
        "webhook_add",
        "webhook_list",
        "webhook_remove",
        "webhook_test",
        "rules_set",
        "session_start",
    ]
]


@pytest.fixture
def browser():
    if os.environ.get("COMMONPLACE_BROWSER_TESTS") == "off":
        pytest.skip("optional browser checks disabled")
    playwright = pytest.importorskip("playwright.sync_api", reason="optional browser dependency")
    with playwright.sync_playwright() as manager:
        try:
            browser = manager.chromium.launch(executable_path=shutil.which("google-chrome"))
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("optional browser executable unavailable")
            raise
        yield browser
        browser.close()


def open_settings(
    browser,
    width,
    *,
    look="deck",
    static=STATIC,
    available=False,
    late_voice=False,
    role="owner",
    connections=None,
):
    context = browser.new_context(
        viewport={"width": width, "height": 844 if width == 390 else 800},
        device_scale_factor=2,
        timezone_id="UTC",
    )
    page = context.new_page()
    page.set_default_timeout(10000)
    responses = {
        "/api/me": {
            "actor": "user:owner",
            "name": "Owner",
            "workspace": "demo",
            "can_admin": role == "owner",
            "role": role,
            "workspace_name": "Example",
            "people": {},
        },
        "/api/boards": {"boards": []},
        "/api/needs-me": {"count": 0},
        "/api/bots": {"bots": []},
        "/api/rules/count": {"waiting": 0},
        "/api/voice": {"available": True, "enabled": False, "owner": True},
        "/api/connections": {"connections": connections or [], "wings": []},
        "/api/webhooks": {"webhooks": []},
        "/api/plan": {
            "enabled": True,
            "plan": "free",
            "storage": {"used": 0, "limit": 1048576},
            "transcription": {"used_today": 0, "limit": 60},
            "sync": {"interval_seconds": 60},
        },
        "/api/phone-push": {"available": available, "eligible": True},
        "/api/chat-settings": {
            "front": {"enabled": False},
            "usage": {"used_today": 0, "user_used_today": 0},
        },
        "/api/settings/orchestration": {"enabled": True},
        "/api/tool-policy": {
            "actions": ACTIONS,
            "modes": ["allowed", "ask", "off"],
            "approval": {"approval_ttl_s": 600},
        },
        "/api/agent-schedules": {
            "schedules": [],
            "limits": {"max_prompt_chars": 4000, "min_interval_minutes": 5, "max_schedules": 10},
        },
        "/api/events": {"events": [], "seq": 0, "epoch": "test"},
    }

    delayed_voice = []

    def serve(route):
        path = urlsplit(route.request.url).path
        if path == "/api/voice" and late_voice:
            delayed_voice.append(route)
            return
        if path.startswith("/api/"):
            route.fulfill(body=json.dumps(responses.get(path, {})), content_type="application/json")
        else:
            name = path.removeprefix("/app/") or "index.html"
            file = static / name
            route.fulfill(
                status=200 if file.is_file() else 404,
                body=file.read_bytes() if file.is_file() else b"",
                content_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
            )

    responses["/api/bots/settings"] = {"sharing": False, "can_configure": role == "owner"}
    page.route("**/*", serve)
    page.goto(f"http://example.test/app/?ui={look}#/settings")
    page.wait_for_selector("[data-tool-policy-mode]", state="attached")
    page.wait_for_selector("[data-schedule-form]")
    if late_voice:
        for route in delayed_voice:
            route.fulfill(body=json.dumps(responses["/api/voice"]), content_type="application/json")
        page.wait_for_selector('[data-voice="enabled"]')
    page.evaluate("document.fonts.ready")
    return context, page


@pytest.mark.parametrize("width", [390, 1280])
def test_common_shortcuts_preserve_every_setting(browser, width):
    context, page = open_settings(browser, width, late_voice=True)
    try:
        shortcuts = page.locator("#deck-settings-nav")
        assert shortcuts.is_visible()
        assert (
            page.locator(".settings > :first-child").get_attribute("class")
            == "capy-settings-subtitle"
        )
        # One Settings tab, then one shortcut: two taps from any main destination.
        for label, target in [
            ("Notifications", "phone-push"),
            ("Connections", "deck-connections"),
            ("Workspace", "deck-tools"),
        ]:
            shortcuts.get_by_role("button", name=label, exact=True).click()
            assert page.locator(f"#{target}").evaluate("el => document.activeElement === el")
            # Shortcuts near the end clamp at the scroll limit; their heading stays in view.
            frame = page.locator(".settings").bounding_box()
            top = page.locator(f"#{target}").bounding_box()["y"]
            assert frame["y"] <= top <= frame["y"] + frame["height"] - 44
            assert shortcuts.locator('[aria-current="location"]').inner_text() == label
        # Every former section and form is still reachable; only two headings change.
        old_sections = [
            "Free plan",
            "Coworker management",
            "Chat",
            "Agents",
            "Scheduled prompts",
            "Tool policy",
            "Phone notifications",
            "Webhooks",
            "Look",
        ]
        renamed = {"Agents": "Connected apps", "Tool policy": "What bots may do without asking"}
        for old in old_sections:
            heading = page.get_by_role("heading", name=renamed.get(old, old), exact=True)
            heading.scroll_into_view_if_needed()
            assert heading.is_visible(), old
        for selector in [
            '[data-form="agent"]',
            "[data-schedule-form]",
            '[data-form="webhook"]',
            '[data-action="logout"]',
            '[data-deck-action="look"][data-look="classic"]',
            "[data-orchestration-enabled]",
            '[data-voice="enabled"]',
        ]:
            page.locator(".settings").locator(selector).scroll_into_view_if_needed()
            assert page.locator(".settings").locator(selector).is_visible(), selector
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
        assert page.locator(".head h1").evaluate("el => getComputedStyle(el).fontSize") == "22px"
        assert page.locator("#phone-push").evaluate(
            "el => [getComputedStyle(el).backgroundColor, getComputedStyle(el).borderRadius]"
        ) == ["rgba(20, 19, 25, 0.94)", "12px"]
        if width == 390:
            assert page.locator("#content").evaluate(
                "el => [getComputedStyle(el).paddingLeft, getComputedStyle(el).paddingRight]"
            ) == ["16px", "16px"]
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_permissions_search_disclosure_and_notification_setup(browser, width):
    context, page = open_settings(browser, width)
    try:
        field = page.get_by_role("searchbox", name="Search permissions")
        page.locator('[data-deck-setting="tool-policy"]').click()
        field.fill("notif")
        assert (
            page.locator('#deck-settings-nav [aria-current="location"]').inner_text()
            == "Action permissions"
        )
        assert page.locator('[data-tool-policy-row="webhook_add"]').is_visible()
        result_box = page.locator('[data-tool-policy-row="webhook_add"]').bounding_box()
        assert result_box["y"] + result_box["height"] <= (844 if width == 390 else 800)
        assert page.locator('[data-tool-policy-row="task_add"]').count() == 0
        for label in [
            "Send notifications to another app",
            "See where notifications are sent",
            "Stop sending to an app",
            "Send a test notification",
        ]:
            assert page.get_by_text(label, exact=True).count() == 1
        for wire in ["webhook_add", "webhook_list", "webhook_remove", "webhook_test"]:
            row = page.locator(f'[data-tool-policy-row="{wire}"]')
            assert row.get_attribute("title") == wire
            assert row.locator("small, summary, details").count() == 0
        status = page.locator("[data-tool-policy-status]")
        assert status.evaluate("el=>getComputedStyle(el).marginTop") == "12px"
        assert not page.get_by_text("webhook_add", exact=True).is_visible()
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
        assert field.evaluate(
            "el => [getComputedStyle(el).backgroundColor, getComputedStyle(el).borderRadius]"
        ) == ["rgb(27, 26, 33)", "8px"]
        page.locator('[data-deck-setting="phone-push"]').click()
        assert "Connect a notification service" in page.locator("#phone-push").inner_text()
    finally:
        context.close()
