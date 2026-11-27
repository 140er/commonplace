"""Common settings and reservation removal stay directly reachable at both widths."""

import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_board_authority import fresh as fresh
from tests.test_deck_settings_browser import STATIC, browser as browser_fixture, open_settings
from tests.test_remove_assignment import reservation

browser = browser_fixture


def evidence(page, name, width, values):
    folder = os.environ.get("SETTINGS_ASSIGNMENT_EVIDENCE")
    if folder:
        target = Path(folder)
        target.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target / f"{name}-{width}.png"))
        (target / f"{name}-{width}.json").write_text(json.dumps(values, indent=2))


@pytest.mark.parametrize("width", [1280, 390])
def test_common_settings_before_collapsed_advanced(browser, width):
    static = Path(os.environ.get("SETTINGS_ASSIGNMENT_STATIC", STATIC))
    context, page = open_settings(browser, width, static=static, late_voice=True)
    try:
        positions = page.locator(
            ".settings h2, #deck-settings-nav, #deck-settings-advanced > summary"
        ).evaluate_all(
            "els=>els.map(el=>({text:el.textContent,top:el.getBoundingClientRect().top,visible:el.checkVisibility()}))"
        )
        evidence(page, "settings", width, positions)
        advanced = page.locator("#deck-settings-advanced")
        assert advanced.count() == 1
        assert advanced.get_attribute("open") is None
        assert not page.locator("[data-tool-policy-filter]").is_visible()
        titles = page.locator(".deck-settings-main > section > h2").all_text_contents()
        assert titles[:4] == ["Phone notifications", "Email for Today", "Look", "Connected apps"]
        assert page.locator("#deck-settings-nav").bounding_box()["y"] < 350
        assert page.locator("#phone-push").bounding_box()["y"] < 550
        for label, target in [
            ("Notifications", "phone-push"),
            ("Connected apps", "deck-connections"),
            ("Appearance", "deck-appearance"),
        ]:
            page.locator("#deck-settings-nav").get_by_role("button", name=label, exact=True).click()
            assert page.locator(f"#{target}").evaluate("el=>document.activeElement===el")
        page.locator('[data-deck-setting="tool-policy"]').click()
        assert advanced.get_attribute("open") is not None
        field = page.get_by_role("searchbox", name="Search permissions")
        field.fill("task_add")
        assert page.locator('[data-tool-policy-row="task_add"]').is_visible()
        advanced.locator("summary").click()
        assert not field.is_visible()
        page.locator('[data-deck-setting="tool-policy"]').click()
        assert field.input_value() == "task_add", "Collapsing must preserve form state"
        evidence(page, "advanced", width, {"open": True, "search": field.input_value()})
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
    finally:
        context.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_removal_without_edit_disclosure_uses_handoff(browser, hub, fresh, width):
    hub.gateway.rate_limits = False
    card, _ = reservation(fresh)
    host, port = hub.server.server_address
    origin = f"http://{host}:{port}"
    context = browser.new_context(viewport={"width": width, "height": 844 if width == 390 else 800})
    name, value = fresh.cookie["Cookie"].split("=", 1)
    context.add_cookies([{"name": name, "value": value, "url": origin}])
    page = context.new_page()
    static = os.environ.get("SETTINGS_ASSIGNMENT_STATIC")
    if static:

        def serve(route):
            name = urlsplit(route.request.url).path.removeprefix("/app/") or "index.html"
            file = Path(static) / name
            route.fulfill(
                status=200 if file.is_file() else 404,
                body=file.read_bytes() if file.is_file() else b"",
                content_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
            )

        page.route("**/app/**", serve)
    try:
        page.goto(origin + "/app/?ui=deck#/board/work")
        page.locator(f'.card[data-card="{card["number"]}"]').click()
        page.locator(".workbench-details:not([data-card-preview])").wait_for()
        action = page.locator('[data-action="remove-assignment"]')
        assert page.locator("[data-deck-card-controls]").get_attribute("open") is None
        evidence(
            page, "card", width, {"action": action.bounding_box(), "text": action.inner_text()}
        )
        assert action.is_visible()
        action.click()
        form = page.locator('[data-form="remove-assignment"]')
        assert form.get_by_text("This card goes back to the pool.", exact=False).is_visible()
        assert form.locator('[name="to"]').count() == 0
        evidence(page, "confirmation", width, {"text": form.inner_text()})
        form.get_by_label("Reason (optional)", exact=True).fill("Ready for the pool")
        with page.expect_response(lambda reply: reply.url.endswith("/handoff")) as response:
            form.get_by_role("button", name="Drop reservation", exact=True).click()
        assert response.value.request.post_data_json == {
            "to": "anyone",
            "note": "Ready for the pool",
            "version": card["version"],
        }
        assert response.value.status == 200
        saved = fresh.owner.task_show(card["number"])
        assert saved["assignee"] is None and saved["claim_until"] is None
        assert saved["events"][-1]["kind"] == "handoff"
    finally:
        context.close()
