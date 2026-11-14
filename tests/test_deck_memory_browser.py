"""Optional signed-in Memory views; browser-free contracts cover the same mapping."""

import os
import shutil
from pathlib import Path

import pytest

from tests._chat_helpers import chat_environment, chat_hub_fixture  # noqa: F401
from tests.test_commonplace_boards import service
from tests.test_commonplace_import_files import file_body


@pytest.fixture
def memory_browser():
    if os.environ.get("COMMONPLACE_BROWSER_TESTS") == "off":
        pytest.skip("optional browser checks disabled")
    playwright = pytest.importorskip("playwright.sync_api", reason="optional browser dependency")
    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(executable_path=shutil.which("google-chrome"))
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("optional browser executable unavailable")
            raise
        yield browser
        browser.close()


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["desktop", "phone"])
@pytest.mark.parametrize("role", ["owner", "member"])
def test_memory_views_restore_rooms_and_consent(memory_browser, chat_hub, size, role):
    from playwright.sync_api import expect

    h = chat_hub
    owner = service(h, h.person(h.admin))
    owner.board_add(slug="private-work", wing="private")
    quote = "Exact words stay here.\nThe second line stays too."
    owner.space_post("work", quote)
    owner.space_post("work", "Exact words can be searched again.")
    owner.space_post("private-work", "Exact words in private history.")
    auth = h.owner if role == "owner" else h.member
    origin = f"http://{h.server.server_address[0]}:{h.port}"
    cookie_name, cookie_value = auth["Cookie"].split("=", 1)
    context = memory_browser.new_context(
        viewport={"width": size[0], "height": size[1]},
        device_scale_factor=2,
        reduced_motion="reduce",
    )
    context.add_cookies([{"name": cookie_name, "value": cookie_value, "url": origin}])
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    shots = os.environ.get("CP_MEMORY_SHOTS")

    def shot(name):
        if shots and role == "owner":
            target = Path(shots) / f"{name}-{size[0]}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(target))
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

    try:
        page.goto(origin + "/app/?ui=deck#/memory")
        query = page.get_by_role("searchbox", name="Search saved memory")
        expect(query).to_be_visible()
        expect(page.get_by_text("Kept from last time", exact=True)).to_have_count(0)
        query.fill("Exact words")
        query.press("Enter")
        expect(page.locator(".memory-results")).to_contain_text(quote)
        expect(query).to_be_focused()
        expect(page.get_by_text("Kept from last time", exact=True)).to_have_count(0)
        if role == "member":
            expect(page.locator(".memory-results")).not_to_contain_text("private history")
            expect(page.locator('[data-memory-room="private-work"]')).to_have_count(0)
        page.locator('[data-memory-room="work"]').click()
        expect(page.locator('[data-memory-room="work"]')).to_have_attribute("aria-pressed", "true")
        expect(page.locator(".memory-results")).not_to_contain_text("private history")
        expect(query).to_have_value("Exact words")
        page.reload()
        expect(page.get_by_text("Kept from last time", exact=True)).to_be_visible()
        expect(query).to_have_value("Exact words")
        expect(page.locator(".memory-results")).to_contain_text(quote)
        query.fill("Exact")
        expect(page.get_by_text("Kept from last time", exact=True)).to_have_count(0)
        query.press("Enter")
        expect(page.locator(".memory-results")).to_have_attribute("aria-busy", "false")
        shot("memory")
        if size[0] == 390:
            expect(page.locator(".m-top .deck-brand b")).to_have_text("Memory")
            metrics = page.locator(".memory-rooms button").evaluate_all(
                "nodes => nodes.map(n => n.getBoundingClientRect().height)"
            )
            assert min(metrics) >= 44
            assert page.locator(".deck-memory").bounding_box()["x"] == 16
        page.locator('[data-memory-result="0"]').click()
        expect(page.locator(".memory-source-details")).to_be_visible()
        assert page.locator(".memory-source-details").get_attribute("open") is None
        page.locator('[data-memory-action="hide"]').click()
        page.get_by_role("button", name="Import memory files", exact=True).click()
        dialog = page.get_by_role("dialog", name="Import memory files")
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text("Mark each memory with the file it came from.")
        if role == "member":
            expect(dialog.locator('option[value="private"]')).to_have_count(0)
        shot("import")
        page.keyboard.press("Escape")
        expect(dialog).to_have_count(0)
        expect(page.get_by_role("button", name="Import memory files", exact=True)).to_be_focused()
        page.get_by_role("button", name="Import memory files", exact=True).click()
        page.locator('[name="format"]').select_option("2")
        page.locator('[name="file"]').set_input_files(
            {
                "name": "conversation.json",
                "mimeType": "application/json",
                "buffer": file_body()["transcript"].encode(),
            }
        )
        page.get_by_role("button", name="Preview file", exact=True).click()
        confirm = page.locator('[data-import-action="confirm"]')
        expect(confirm).to_be_visible()
        expect(confirm).to_be_disabled()
        consent = page.get_by_role("checkbox", name="I own this export and agree to import it.")
        consent.check()
        expect(confirm).to_be_enabled()
        consent.uncheck()
        expect(confirm).to_be_disabled()
        shot("preview")
        consent.check()
        confirm.click()
        expect(page.locator(".memory-import")).to_contain_text("Import complete.")
        page.get_by_role("button", name="Done", exact=True).click()
        expect(query).to_have_value("Exact")
        assert not errors, errors
    finally:
        context.close()
