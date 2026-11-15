"""Optional real-shell proof of the second Deck polish slice: the classic look on a phone, long Memory
results and the Memory warning. Fixture-served, so no hub and no timing is involved."""

import json
import mimetypes
from urllib.parse import urlsplit

import pytest

from tests.test_deck_board_browser import BOARD, BOT, CARDS, STATIC, browser  # noqa: F401

NEEDLE = "harbor lantern"
LONG_TEXT = (
    "Notes from the planning week. " * 20
    + f"The one line that matters is the {NEEDLE} decision, written down exactly. "
    + "Afterwards the team went through the rest of the agenda in order. " * 12
)
CHAT_HIT = {
    "id": 7,
    "kind": "chat",
    "text": LONG_TEXT,
    "author": "user:owner",
    "at": "2026-01-02T10:00:00Z",
}
SHORT_HIT = {
    "id": 8,
    "kind": "chat",
    "text": f"A short {NEEDLE} line.",
    "author": "user:owner",
    "at": "2026-01-01T10:00:00Z",
}


def open_page(page, hash_, *, look="deck", name=None, chat=None, notes=None, search_status=200):
    board = {**BOARD, "name": name} if name else BOARD
    responses = {
        "/api/me": {
            "actor": "user:owner",
            "name": "Owner",
            "workspace": "demo",
            "can_admin": True,
            "role": "owner",
        },
        "/api/boards": {"boards": [board]},
        "/api/boards/product/cards": {"cards": CARDS, "children": [], "linked": []},
        "/api/chat/product": {"board": "product", "messages": []},
        "/api/needs-me": {"count": 0},
        "/api/bots": {"bots": [BOT]},
        "/api/rules/count": {"waiting": 0},
        "/api/events": {"events": [], "seq": 0, "epoch": "fixture"},
        "/api/chat/product/search": {"results": chat or [], "truncated": False},
        "/api/memory/search": {"results": notes or []},
    }

    def serve(route):
        path = urlsplit(route.request.url).path
        if path.startswith("/api/"):
            status = search_status if path == "/api/chat/product/search" else 200
            route.fulfill(
                status=status,
                body=json.dumps(responses.get(path, {})),
                content_type="application/json",
            )
        else:
            file = STATIC / (path.removeprefix("/app/") or "index.html")
            if file.is_file():
                route.fulfill(
                    body=file.read_bytes(),
                    content_type=mimetypes.guess_type(file.name)[0] or "application/octet-stream",
                )
            else:
                route.fulfill(status=404, body="")

    page.route("**/*", serve)
    page.add_init_script("crypto.randomUUID ||= () => 'k' + Math.random().toString(16).slice(2)")
    page.goto(f"http://example.test/app/?ui={look}{hash_}")


def search(page, query=NEEDLE):
    page.wait_for_selector("#mq")
    page.fill("#mq", query)
    page.keyboard.press("Enter")
    page.wait_for_selector(".memory-results .res")


@pytest.mark.parametrize("look", ["deck", "classic"])
def test_long_memory_results_are_cut_with_a_toggle_and_keep_the_match(browser, look):  # noqa: F811
    page = browser.new_page(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        open_page(page, "#/memory", look=look, chat=[CHAT_HIT, SHORT_HIT])
        search(page)
        rows = page.locator(".memory-results .res")
        assert rows.count() == 2
        long_row, short_row = rows.nth(0), rows.nth(1)
        assert len(long_row.locator(".ex").inner_text()) < 300, "a wall of text is cut"
        assert long_row.bounding_box()["height"] < 260
        mark = long_row.locator(".ex mark").first
        assert mark.inner_text().lower() == NEEDLE.split()[0] or NEEDLE in mark.inner_text().lower()
        assert mark.is_visible(), "the match stays in the shown words"
        toggles = page.locator(".memory-results [data-memory-expand]")
        assert toggles.count() == 1, "only the long result has a toggle"
        assert short_row.locator(".ex").inner_text() == SHORT_HIT["text"]
        toggles.first.click()
        assert len(long_row.locator(".ex").inner_text()) >= len(LONG_TEXT) - 5
        assert page.locator("[data-memory-expand]").get_attribute("aria-expanded") == "true"
        assert page.locator(".memory-panel:visible").count() == 0, (
            "the toggle does not open the source"
        )
        page.locator("[data-memory-expand]").click()
        assert len(long_row.locator(".ex").inner_text()) < 300
        assert errors == []
    finally:
        page.close()


def test_memory_warning_names_the_source_and_the_reason(browser):  # noqa: F811
    page = browser.new_page(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    try:
        note = {
            "id": 3,
            "theme": "product",
            "author": "user:owner",
            "at": "2026-01-01T10:00:00Z",
            "text": f"A {NEEDLE} note.",
        }
        open_page(page, "#/memory", chat=[], search_status=500, notes=[note])
        search_box = page.wait_for_selector("#mq")
        search_box.fill(NEEDLE)
        page.keyboard.press("Enter")
        warning = page.locator("[data-memory-sources]")
        warning.wait_for(state="visible")
        text = warning.inner_text()
        assert "Product chat and cards" in text and "the server had a problem" in text, text
        assert text.startswith("Some sources could not be searched:"), text
    finally:
        page.close()


@pytest.mark.parametrize("width", [390, 360])
def test_classic_phone_tab_bar_is_pinned_and_the_project_name_is_not_broken(browser, width):  # noqa: F811
    page = browser.new_page(viewport={"width": width, "height": 667}, reduced_motion="reduce")
    try:
        open_page(page, "#/board/product", look="classic", name="commonplace-workspace")
        page.wait_for_selector(".m-top .projbtn")
        page.wait_for_function("document.querySelector('.m-tabs')?.getClientRects().length")
        for hash_ in ["#/board/product", "#/boards", "#/memory", "#/settings"]:
            page.evaluate(f"location.hash = '{hash_}'")
            page.wait_for_timeout(500)
            bar = page.evaluate(
                "() => { const b = document.querySelector('.m-tabs').getBoundingClientRect();"
                " return { bottom: b.bottom, height: innerHeight, shown: b.height > 0 }; }"
            )
            assert bar["shown"] and abs(bar["bottom"] - bar["height"]) <= 1, (hash_, bar)
            page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            bar = page.evaluate(
                "() => ({ bottom: document.querySelector('.m-tabs').getBoundingClientRect().bottom, height: innerHeight })"
            )
            assert abs(bar["bottom"] - bar["height"]) <= 1, ("scrolled", hash_, bar)
            page.evaluate("window.scrollTo(0, 0)")
    finally:
        page.close()


@pytest.mark.parametrize("width", [390, 360])
def test_classic_phone_project_name_is_never_broken_inside_a_word(browser, width):  # noqa: F811
    page = browser.new_page(viewport={"width": width, "height": 667}, reduced_motion="reduce")
    try:
        open_page(page, "#/board/product", look="classic", name="commonplace-workspace")
        page.wait_for_selector(".m-top .projbtn")
        lines = page.evaluate(
            "() => { const text = document.querySelector('.m-top .projbtn .pn');"
            " return { height: text.getBoundingClientRect().height, wrap: getComputedStyle(text).whiteSpace }; }"
        )
        assert lines["height"] < 30 and lines["wrap"] == "nowrap", lines
    finally:
        page.close()
