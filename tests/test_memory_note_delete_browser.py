"""Browser confirmation round trips on the two Memory layouts and screen widths."""

import os
from pathlib import Path

import pytest

from tests._chat_helpers import chat_environment, chat_hub_fixture  # noqa: F401
from tests.test_memory_note_delete import note


@pytest.mark.parametrize("look", ["deck", "classic"])
@pytest.mark.parametrize("width", [1280, 390])
def test_delete_note_confirm_cancel_and_close(chat_hub, look, width):
    playwright = pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect

    h = chat_hub
    identifier = note(h)
    origin = f"http://127.0.0.1:{h.port}"
    cookie, value = h.owner["Cookie"].split("=", 1)
    evidence = os.environ.get("CP_DELETE_EVIDENCE")
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport={"width": width, "height": 844}, reduced_motion="reduce"
        )
        context.add_cookies([{"name": cookie, "value": value, "url": origin}])
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def capture(phase):
            if evidence:
                target = Path(evidence)
                target.mkdir(parents=True, exist_ok=True)
                page.screenshot(
                    path=str(target / f"delete-{look}-{width}-{phase}.png"), full_page=True
                )
                (target / f"delete-{look}-{width}-{phase}.txt").write_text(
                    page.locator("body").inner_text()
                )
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

        try:
            page.goto(f"{origin}/app/?ui={look}#/memory?drawer={identifier}")
            expect(page.locator(".memory-panel .thread .msg-b")).to_have_text(
                "deletioncanary exact words\nsecond line"
            )
            capture("before")
            page.get_by_role("button", name="Delete this note", exact=True).click()
            dialog = page.locator("[data-note-delete]")
            expect(dialog).to_be_visible()
            expect(dialog).to_contain_text("Delete this note? This cannot be undone.")
            expect(dialog.locator("pre")).to_have_text("deletioncanary exact words\nsecond line")
            capture("confirm")
            dialog.get_by_role("button", name="Cancel", exact=True).click()
            expect(dialog).to_have_count(0)
            expect(page.locator(".memory-panel")).to_be_visible()
            assert h.get(h.owner, f"/api/memory/{identifier}")[0] == 200
            page.get_by_role("button", name="Delete this note", exact=True).click()
            with page.expect_response(
                lambda response: (
                    response.url.endswith(f"/{identifier}/delete") and response.status == 200
                )
            ):
                page.locator("[data-note-delete]").get_by_role(
                    "button", name="Delete", exact=True
                ).click()
            expect(page.locator(".memory-panel")).to_have_count(0)
            expect(page.get_by_text("Note deleted", exact=True)).to_be_visible()
            capture("after")
            assert h.get(h.owner, f"/api/memory/{identifier}")[0] == 404
            assert errors == []
            # A member can read a new note but gets no destructive control.
            other = note(h)
            context.clear_cookies()
            cookie, value = h.member["Cookie"].split("=", 1)
            context.add_cookies([{"name": cookie, "value": value, "url": origin}])
            page.goto(f"{origin}/app/?ui={look}#/memory?drawer={other}")
            expect(page.locator(".memory-panel .thread .msg-b")).to_be_visible()
            expect(page.get_by_role("button", name="Delete this note", exact=True)).to_have_count(0)
        finally:
            context.close()
            browser.close()
