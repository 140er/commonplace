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
    with h.db._lock:
        h.db._conn.execute("UPDATE users SET name = ? WHERE id = ?", ("Memory writer", h.admin))
        h.db._conn.commit()
    owner = service(h, h.person(h.admin))
    owner.board_add(slug="private-work", name="Private notes", wing="private")
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
        expected_count = "3" if role == "owner" else "2"
        expect(page.locator('[data-memory-room=""] .memory-room-count')).to_have_text(
            expected_count
        )
        expect(page.locator('[data-memory-room="work"] .memory-room-count')).to_have_text("2")
        page.locator('[data-memory-room="work"]').click()
        expect(page.locator('[data-memory-room="work"]')).to_have_attribute("aria-pressed", "true")
        expect(page.locator(".memory-results")).not_to_contain_text("private history")
        expect(query).to_have_value("Exact words")
        expect(page.locator('[data-memory-room=""] .memory-room-count')).to_have_text(
            expected_count
        )
        page.reload()
        expect(page.get_by_text("Kept from last time", exact=True)).to_be_visible()
        expect(query).to_have_value("Exact words")
        expect(page.locator(".memory-results")).to_contain_text(quote)
        query.fill("Exact")
        expect(page.get_by_text("Kept from last time", exact=True)).to_have_count(0)
        query.press("Enter")
        expect(page.locator(".memory-results")).to_have_attribute("aria-busy", "false")
        expect(page.locator(".memory-results .memory-saver").first).to_have_text(
            "you" if role == "owner" else "Memory writer"
        )
        expect(page.locator(".search-big button")).to_have_count(0)
        assert query.locator("..").bounding_box()["height"] == 44
        page.locator(".memory-rooms").evaluate("el => el.scrollLeft = 0")
        query.evaluate("el => el.blur()")
        assert (
            query.locator("..").evaluate("el => getComputedStyle(el).outlineColor")
            != "rgb(157, 92, 255)"
        )
        shot("memory")
        if size[0] == 390:
            scroll = page.locator(".memory-chip-scroll")
            status = page.locator(".memory-status")
            row_box, status_box = scroll.bounding_box(), status.bounding_box()
            assert status_box["y"] >= row_box["y"] + row_box["height"]
            assert status_box["x"] == row_box["x"]
            assert status.evaluate("el => getComputedStyle(el).fontSize") == "13px"
            assert status.evaluate("el => getComputedStyle(el).color") == page.locator(
                ".memory-room-count"
            ).first.evaluate("el => getComputedStyle(el).color")
            assert (
                page.locator(".memory-filterbar").evaluate(
                    "el => getComputedStyle(el, '::after').width"
                )
                == "16px"
            )
            assert scroll.evaluate("el => getComputedStyle(el).overflowX") == "auto"
            assert scroll.locator(":scope > :last-child").get_attribute("class") == "memory-filters"
            if role == "owner":
                private = page.locator('[data-memory-room="private-work"]')
                private.evaluate(
                    "el => { const row = el.closest('.memory-chip-scroll'); row.scrollLeft += el.getBoundingClientRect().left - row.getBoundingClientRect().left; }"
                )
                box = private.bounding_box()
                assert box["x"] >= row_box["x"]
                assert box["x"] + box["width"] <= row_box["x"] + row_box["width"] - 15
                expect(private).to_have_text("Private notes 1")
            scroll.evaluate("el => el.scrollLeft = el.scrollWidth")
            more = scroll.locator("summary")
            box = more.bounding_box()
            assert box["x"] + box["width"] <= row_box["x"] + row_box["width"] - 15
            more.click()
            expect(page.locator('.memory-filters select[name="source"]')).to_be_visible()
            # The popup must escape the horizontal scroll's clipping area.
            page.locator('.memory-filters select[name="source"]').click()
            page.keyboard.press("Escape")
            more.click()
            scroll.evaluate("el => el.scrollLeft = 0")
            expect(page.locator(".m-top > b")).to_have_text("Memory")
            metrics = page.locator(".memory-rooms button").evaluate_all(
                "nodes => nodes.map(n => n.getBoundingClientRect().height)"
            )
            assert min(metrics) >= 44
            assert query.bounding_box()["height"] >= 44
            assert query.bounding_box()["y"] < page.locator(".memory-results").bounding_box()["y"]
            assert (
                page.get_by_role("button", name="Import memory files", exact=True).bounding_box()[
                    "y"
                ]
                >= page.locator(".memory-results").bounding_box()["y"]
            )
            assert (
                page.locator(".memory-filters summary").evaluate(
                    "el => getComputedStyle(el).display"
                )
                == "list-item"
            )
            assert page.locator(".deck-memory").bounding_box()["x"] == 16
        page.locator('[data-memory-result="0"]').click()
        expect(page.locator(".memory-source-details")).to_be_visible()
        assert page.locator(".memory-source-details").get_attribute("open") is None
        page.locator('[data-memory-action="hide"]').click()
        page.get_by_role("button", name="Import memory files", exact=True).click()
        dialog = page.get_by_role("dialog", name="Import memory files")
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text("Each memory is marked with the file it came from.")
        # A user's translucent card setting cannot make either import step see-through.
        page.evaluate("document.documentElement.style.setProperty('--surface-a', '.4')")
        surface = dialog.evaluate("el => getComputedStyle(el).backgroundColor")
        assert surface.startswith("rgb("), surface
        page.evaluate("document.documentElement.style.removeProperty('--surface-a')")
        if size[0] == 1440:
            intro = dialog.locator(".memory-import-intro").bounding_box()
            hint = dialog.locator(".memory-import-intro + .memory-import-hint").bounding_box()
            label = dialog.locator('label[for="import-format"]').bounding_box()
            assert hint["y"] - intro["y"] - intro["height"] == 20
            assert label["y"] - hint["y"] - hint["height"] == 20
        if role == "member":
            expect(dialog.locator('option[value="private"]')).to_have_count(0)
        shot("import")
        page.keyboard.press("Escape")
        expect(dialog).to_have_count(0)
        expect(page.get_by_role("button", name="Import memory files", exact=True)).to_be_focused()
        page.get_by_role("button", name="Import memory files", exact=True).click()
        page.locator('[name="format"]').select_option("0")
        page.locator('[name="file"]').set_input_files(
            {
                "name": "conversation.json",
                "mimeType": "application/json",
                "buffer": file_body("chatgpt")["transcript"].encode(),
            }
        )
        page.get_by_role("button", name="Preview file", exact=True).click()
        confirm = page.locator('[data-import-action="confirm"]')
        expect(confirm).to_be_visible()
        expect(confirm).to_be_disabled()
        review = page.get_by_role("dialog", name="Review import")
        expect(review).to_be_visible()
        assert review.evaluate("el => getComputedStyle(el).backgroundColor") == surface
        expect(review).to_contain_text("Importing the same file here again adds nothing.")
        expect(page.locator(".memory-import-summary")).to_contain_text(
            "ChatGPT conversation (.json)"
        )
        assert page.locator(".memory-import-actions button").all_text_contents() == [
            "Choose another file",
            "Cancel",
            "Import",
        ]
        assert confirm.evaluate("el => getComputedStyle(el).opacity") == "1"
        consent = page.get_by_role("checkbox", name="I own this export and agree to import it.")
        assert consent.bounding_box()["width"] == 20
        assert consent.evaluate("el => getComputedStyle(el).borderRadius") == "6px"
        consent.check()
        expect(confirm).to_be_enabled()
        consent.uncheck()
        expect(confirm).to_be_disabled()
        review.evaluate("el => el.scrollTop = 0")
        shot("preview")
        consent.check()
        shot("consent")
        confirm.click()
        expect(page.locator(".memory-import")).to_contain_text("Import complete.")
        page.get_by_role("button", name="Done", exact=True).click()
        expect(query).to_have_value("Exact")
        assert not errors, errors
    finally:
        context.close()


def _signed_in_page(browser, h, size=(1440, 900)):
    origin = f"http://{h.server.server_address[0]}:{h.port}"
    cookie_name, cookie_value = h.owner["Cookie"].split("=", 1)
    context = browser.new_context(viewport={"width": size[0], "height": size[1]})
    context.add_cookies([{"name": cookie_name, "value": cookie_value, "url": origin}])
    return context, context.new_page(), origin


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["desktop", "phone"])
def test_one_note_request_per_query_with_a_small_limit(memory_browser, chat_hub, size):
    from playwright.sync_api import expect

    h = chat_hub
    service(h, h.person(h.admin)).space_post("work", "Exact words for one request.")
    context, page, origin = _signed_in_page(memory_browser, h, size)
    requests = []

    def note_search(route):
        requests.append(route.request.url)
        route.fulfill(json={"results": []})

    page.route("**/api/memory/search*", note_search)
    try:
        page.goto(origin + "/app/?ui=deck#/memory")
        query = page.get_by_role("searchbox", name="Search saved memory")
        query.fill("Exact words")
        query.press("Enter")
        expect(page.locator(".memory-results")).to_contain_text("Exact words for one request.")
        expect(page.locator(".memory-results")).to_have_attribute("aria-busy", "false")
        assert len(requests) == 1, requests
        assert "limit=25" in requests[0] and "theme=" not in requests[0], requests
    finally:
        context.close()


def test_a_stuck_search_ends_in_an_error_with_retry(memory_browser, chat_hub):
    from playwright.sync_api import expect

    h = chat_hub
    service(h, h.person(h.admin)).space_post("work", "Exact words after the retry.")
    context, page, origin = _signed_in_page(memory_browser, h)
    stuck = {"on": True, "seen": 0}

    def note_search(route):
        stuck["seen"] += 1
        if not stuck["on"]:
            route.fulfill(json={"results": []})

    page.route("**/api/memory/search*", note_search)
    try:
        page.goto(origin + "/app/?ui=deck#/memory")
        page.clock.install()
        query = page.get_by_role("searchbox", name="Search saved memory")
        query.fill("Exact words")
        query.press("Enter")
        expect(page.locator(".memory-loading")).to_be_visible()
        # The deadline is armed before the note request leaves; only then move the clock.
        for _ in range(100):
            if stuck["seen"]:
                break
            page.wait_for_timeout(50)
        assert stuck["seen"]
        page.clock.fast_forward(21000)
        # Chat rows that arrived in time stay under a warning; with none, an error says why.
        expect(page.locator("[role=alert]").filter(has_text="Try again")).to_be_visible()
        expect(page.locator(".memory-loading")).to_have_count(0)
        expect(page.get_by_text("No exact words match")).to_have_count(0)
        stuck["on"] = False
        page.get_by_role("button", name="Try again").click()
        expect(page.locator(".memory-results")).to_contain_text("Exact words after the retry.")
    finally:
        context.close()
