"""The Settings link and the read-only status page in a real browser, at desktop and phone widths."""

import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.test_deck_settings_browser import STATIC, browser as browser_fixture, open_settings

browser = browser_fixture
COST = [{"currency": "USD", "micros": 123456, "display": "0.123456"}]
FULL = {
    "generated_at": "2026-11-18T10:00:00Z",
    "viewer": {"role": "owner"},
    "skills": {
        "state": "ready",
        "notice": "Skill text is quoted data. Nothing here is executed or activated.",
        "more": 0,
        "items": [
            {
                "id": "s1",
                "name": "Release checks",
                "version": 2,
                "description": "Ship a release safely",
                "scope": ["Whole workspace"],
            },
            {
                "id": "s2",
                "name": "Bare",
                "version": 1,
                "description": "",
                "description_note": "This skill has no description line.",
                "scope": ["Project work"],
            },
        ],
    },
    "bridge": {
        "state": "off",
        "reason": "No chat platform is connected to this workspace, so the chat bridge is off. "
        "The owner connects one from the bridge install routes.",
    },
    "meetings": {
        "state": "off",
        "reason": "Meeting notes are switched off for this workspace, so no bot joins a meeting "
        "and nothing is recorded. Only the owner can switch them on.",
    },
    "costs": {
        "state": "ready",
        "period": "The current UTC calendar month, from 2026-11-01T00:00:00Z to "
        "2026-12-01T00:00:00Z (end excluded)",
        "scope": "workspace",
        "scope_text": "Every run in this workspace.",
        "totals": {"runs": 3, "cost": COST, "unknown_cost_runs": 1},
        "unknown_text": "A run whose machine reported no cost is counted as unknown and "
        "is not counted as zero.",
        "by_project": [{"project": "demo", "runs": 3, "unknown_cost_runs": 1, "cost": COST}],
    },
}


def open_status(browser, width, answer, *, status=200):
    context = browser.new_context(
        viewport={"width": width, "height": 844 if width == 390 else 800},
        device_scale_factor=2,
        timezone_id="UTC",
    )
    page = context.new_page()
    page.set_default_timeout(10000)
    writes = []

    def serve(route):
        path = urlsplit(route.request.url).path
        if path == "/api/status":
            if route.request.method != "GET":
                writes.append(route.request.method)
            route.fulfill(status=status, body=json.dumps(answer), content_type="application/json")
        elif path.startswith("/api/"):
            writes.append(path)
            route.fulfill(status=500, body="{}", content_type="application/json")
        else:
            name = path.removeprefix("/app/")
            file = STATIC / name
            route.fulfill(
                status=200 if file.is_file() else 404,
                body=file.read_bytes() if file.is_file() else b"",
                content_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
            )

    page.route("**/*", serve)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://example.test/app/status.html")
    page.wait_for_selector(".status-section, [data-status-retry]")
    page.evaluate("document.fonts.ready")
    return context, page, writes, errors


def shot(page, name):
    target = os.environ.get("STATUS_SHOTS_DIR")
    if target:
        page.screenshot(path=str(Path(target) / f"{name}.png"), full_page=True)


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("look", ["deck", "classic"])
def test_settings_links_to_the_status_page(browser, width, look):
    context, page = open_settings(browser, width, look=look)
    try:
        link = page.get_by_role("link", name="Open capabilities and status")
        link.scroll_into_view_if_needed()
        assert link.is_visible()
        assert link.get_attribute("href") == "/app/status.html"
        assert page.locator("#capabilities-status").inner_text().count("Read-only") == 1
        shot(page, f"settings-{look}-{width}")
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_status_page_shows_every_section_and_writes_nothing(browser, width):
    context, page, writes, errors = open_status(browser, width, FULL)
    try:
        for heading in ("Skills", "Chat bridge", "Meeting notes", "Cost summary"):
            assert page.get_by_role("heading", name=heading, exact=True).is_visible()
        body = page.locator("main").inner_text()
        assert "Ship a release safely" in body and "Scope: Whole workspace" in body
        assert "chat bridge is off" in body and "switched off for this workspace" in body
        assert "Period: The current UTC calendar month" in body
        assert "Scope: Every run in this workspace." in body and "0.123456 USD" in body
        assert page.locator("main input, main select, main textarea, main form").count() == 0
        # No horizontal scroll on a phone.
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        assert page.get_by_role("link", name="Back to Settings").get_attribute("href") == (
            "/app/#/settings"
        )
        assert writes == [] and errors == []
        shot(page, f"status-{width}")
    finally:
        context.close()


def test_a_failed_read_is_not_reported_as_signed_out(browser):
    context, page, _, errors = open_status(browser, 390, {"error": "busy"}, status=503)
    try:
        text = page.locator("main").inner_text()
        assert "could not be read just now" in text and "does not mean you are signed out" in text
        assert "Sign in to see this" not in text
        assert page.get_by_role("button", name="Try again").is_visible()
        shot(page, "status-failed-390")
    finally:
        context.close()


def test_signed_out_says_so_and_offers_sign_in(browser):
    context, page, _, _ = open_status(browser, 390, {"error": "Sign in"}, status=401)
    try:
        assert page.get_by_role("heading", name="Sign in to see this").is_visible()
        assert page.get_by_role("link", name="Sign in").get_attribute("href") == "/app/"
    finally:
        context.close()
