"""Signed-in standalone notes in both looks, on desktop and phone."""

import json
import os
from pathlib import Path

import pytest

from tests._chat_helpers import chat_environment, chat_hub_fixture  # noqa: F401
from tests.test_commonplace_boards import service


@pytest.mark.parametrize("look", ["deck", "classic"])
@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("role", ["owner", "member"])
def test_add_note_verbatim_search_and_drawer(chat_hub, look, width, role):
    playwright = pytest.importorskip("playwright.sync_api", reason="optional browser dependency")
    from playwright.sync_api import expect

    h = chat_hub
    owner = service(h, h.person(h.admin))
    owner.board_add(slug="research", name="Research", wing="project_research")
    owner.board_add(slug="private-notes", name="Private notes", wing="private")
    origin = f"http://127.0.0.1:{h.port}"
    auth = h.owner if role == "owner" else h.member
    cookie_name, value = auth["Cookie"].split("=", 1)
    text = "  Harbor canary café e\u0301 漢字 🧭\ttwo  spaces\nsecond line stays.  "
    baseline = os.environ.get("CP_NOTE_BASELINE_STATIC")
    evidence = os.environ.get("CP_NOTE_EVIDENCE")
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport={"width": width, "height": 844}, reduced_motion="reduce"
        )
        context.add_cookies([{"name": cookie_name, "value": value, "url": origin}])
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        if baseline:

            def base_asset(route):
                path = Path(baseline) / route.request.url.split("/app/", 1)[1].split("?", 1)[0]
                if path.is_file():
                    route.fulfill(path=path)
                else:
                    route.continue_()

            page.route("**/app/*.js", base_asset)
            page.route("**/app/*.css", base_asset)

        def capture(phase):
            if evidence:
                out = Path(evidence)
                out.mkdir(parents=True, exist_ok=True)
                stem = f"582-{phase}-{role}-{look}-{width}"
                page.screenshot(path=str(out / f"{stem}.png"), full_page=True)
                metrics = page.locator(".memory-search").evaluate("""el => ({
                    text: el.innerText,
                    controls: [...el.querySelectorAll('button,textarea,select,a')].map(n => {
                        const r = n.getBoundingClientRect();
                        return {text: n.innerText || n.getAttribute('name'), x:r.x,y:r.y,width:r.width,height:r.height};
                    }), overflow: document.documentElement.scrollWidth > innerWidth
                })""")
                (out / f"{stem}.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

        try:
            page.goto(f"{origin}/app/?ui={look}#/memory")
            expect(page.locator("[data-memory-search]")).to_be_visible()
            capture("before" if baseline else "after")
            if baseline:
                expect(page.get_by_role("button", name="Add a note", exact=True)).to_have_count(0)
                return
            page.get_by_role("button", name="Add a note", exact=True).click()
            note = page.get_by_role("textbox", name="Note", exact=True)
            expect(note).to_be_visible()
            expect(page.locator('[name="note-wing"]')).to_have_value("shared")
            # Empty project wings are selectable before they contain a note.
            expect(
                page.locator('[name="note-wing"] option[value="project_research"]')
            ).to_have_count(1)
            expect(page.locator('[name="note-wing"] option[value="private"]')).to_have_count(
                1 if role == "owner" else 0
            )
            page.get_by_role("button", name="Save", exact=True).click()
            expect(page.locator(".memory-note [role=alert]")).to_have_text("Write a note first.")
            note.fill(text)
            page.get_by_text("Preview exact words", exact=True).click()
            assert page.locator("[data-note-preview]").text_content() == text
            capture("composer")

            # A retryable server answer keeps the draft and never auto-submits a write.
            def busy(route):
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps({"error": "busy", "retry_after_s": 2}),
                )

            page.route("**/api/memory", busy)
            page.get_by_role("button", name="Save", exact=True).click()
            expect(page.locator(".memory-note [role=alert]")).to_contain_text(
                "Try again in about 2 seconds"
            )
            expect(note).to_have_value(text)
            page.unroute("**/api/memory", busy)
            with page.expect_response(
                lambda response: response.url == origin + "/api/memory" and response.status == 201
            ) as response:
                page.get_by_role("button", name="Save", exact=True).click()
            saved = response.value.json()
            expect(page.locator(".memory-note-saved")).to_contain_text("Saved to")
            expect(page.locator(".memory-note-saved")).to_be_in_viewport()
            capture("saved")
            page.get_by_role("link", name="Open drawer", exact=True).click()
            expect(page.locator(".memory-panel .msg-b")).to_have_text(text)
            # The drawer link also survives a fresh page load.
            page.goto(f"{origin}/app/?ui={look}#/memory?drawer={saved['id']}")
            expect(page.locator(".memory-panel .msg-b")).to_have_text(text)
            page.locator('[data-memory-action="hide"]').click()
            query = page.get_by_role("searchbox")
            query.fill("Harbor canary")
            query.press("Enter")
            expect(page.locator(".memory-results")).to_contain_text(text)
            capture("search")
            assert errors == []
        finally:
            context.close()
            browser.close()
