"""Optional real-shell Board/drawer interactions and responsive layout proof."""

import json
import mimetypes
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import pytest

STATIC = Path(__file__).resolve().parents[1] / "commonplace/web/static"
BOARD = {"slug": "product", "name": "Product", "cards": 6, "wing": "product"}
BOT = {"id": "shipping", "name": "Shipping", "board": "product", "kind": "project"}
CARD = {
    "number": 1,
    "version": 3,
    "board": "product",
    "status": "review",
    "need": "you",
    "title": "Ship consent for the phone feed",
    "body": "Ask before reading activity. The change is ready for your decision.",
    "assignee": "conn:worker",
    "ask": "Ship this change?",
    "ask_to": "user:owner",
    "ask_options": ["Approve", "Hold"],
    "events": [{"kind": "pr"}, {"kind": "approve"}],
    "head_sha": "abc1234",
    "checks": "pass",
    "updated_at": "2026-01-01T12:00:00Z",
}
CARDS = [
    CARD,
    {
        **CARD,
        "number": 2,
        "title": "Agree the action list",
        "status": "todo",
        "assignee": "conn:reserved",
        "ask": None,
        "need": "idle",
    },
    {
        **CARD,
        "number": 3,
        "title": "Share access per person",
        "status": "doing",
        "need": "working",
        "ask": None,
    },
    {**CARD, "number": 4, "title": "Check the import flow", "need": "idle", "ask": None},
    {
        **CARD,
        "number": 5,
        "title": "Import memory files",
        "status": "done",
        "need": "done",
        "ask": None,
    },
]


@pytest.fixture
def browser():
    if os.environ.get("COMMONPLACE_BROWSER_TESTS") == "off":
        pytest.skip("optional browser checks disabled")
    playwright = pytest.importorskip("playwright.sync_api", reason="optional browser dependency")
    with playwright.sync_playwright() as manager:
        try:
            instance = manager.chromium.launch(executable_path=shutil.which("google-chrome"))
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("optional browser executable unavailable")
            raise
        yield instance
        instance.close()


def open_board(page, *, look="deck"):
    responses = {
        "/api/me": {
            "actor": "user:owner",
            "name": "Owner",
            "workspace": "demo",
            "can_admin": True,
            "people": {"conn:worker": "Release coworker"},
            "role": "owner",
        },
        "/api/boards": {"boards": [BOARD]},
        "/api/boards/product/cards": {"cards": CARDS, "children": [], "linked": []},
        "/api/needs-me": {"count": 1},
        "/api/bots": {"bots": [BOT]},
        "/api/rules/count": {"waiting": 0},
        "/api/events": {"events": [], "seq": 0, "epoch": "fixture"},
    }
    writes = []

    def serve(route):
        path = urlsplit(route.request.url).path
        if path.startswith("/api/"):
            if route.request.method not in ("GET", "HEAD"):
                writes.append((path, route.request.post_data_json))
            data = responses.get(path, {})
            if path.startswith("/api/cards/") and path.rsplit("/", 1)[-1].isdigit():
                data = next(
                    card for card in CARDS if str(card["number"]) == path.rsplit("/", 1)[-1]
                )
            route.fulfill(body=json.dumps(data), content_type="application/json")
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
    page.goto(f"http://example.test/app/?ui={look}#/board/product")
    page.wait_for_selector('.card[data-card="1"]')
    page.evaluate("document.fonts.ready")
    return writes


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["desktop", "phone"])
def test_board_drawer_and_existing_controls(browser, size, tmp_path):
    page = browser.new_page(
        viewport={"width": size[0], "height": size[1]},
        device_scale_factor=2,
        reduced_motion="reduce",
    )
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        writes = open_board(page)
        assert page.locator(".deck-board .col").count() == 4
        assert page.locator(".deck-board .col .n").count() == 1
        assert page.locator(".card.is-you").count() == 1
        for card in page.locator(".deck-board .card").all():
            css = card.evaluate(
                "el=>{const s=getComputedStyle(el);return [s.backgroundColor,s.borderLeftWidth,s.borderLeftColor]}"
            )
            assert css[0] == "rgba(20, 19, 25, 0.94)"
            assert (css[1] == "2px") == (card.get_attribute("data-card") == "1")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if size[0] == 390:
            geometry = page.locator(".deck-board .board").evaluate(
                "el=>({x:el.getBoundingClientRect().x,width:el.clientWidth,scroll:el.scrollWidth})"
            )
            assert geometry["x"] == 16 and geometry["width"] == 358
            assert geometry["scroll"] > geometry["width"]
            page.locator(".deck-board .board").evaluate("el=>el.scrollLeft=el.scrollWidth")
            assert page.locator("#col-done").is_visible()
            page.locator(".deck-board .board").evaluate("el=>el.scrollLeft=0")
        shots = Path(os.environ.get("CAPY_D_SHOTS", str(tmp_path)))
        shots.mkdir(parents=True, exist_ok=True)
        suffix = "phone" if size[0] == 390 else "desktop"
        page.screenshot(path=str(shots / f"board-{suffix}.png"))
        page.locator('.card[data-card="1"] h3').click()
        page.wait_for_selector(".deck-card-drawer")
        drawer = page.locator("#card-panel")
        assert drawer.get_attribute("role") == "dialog"
        for text in ["Summary", "Bot", "Coworker", "Version", "Progress"]:
            assert drawer.get_by_text(text, exact=True).count() >= 1
        assert drawer.locator(".deck-card-facts code").inner_text() == "abc1234"
        assert (
            drawer.locator(".deck-card-actions")
            .get_by_role("button", name="Approve", exact=True)
            .is_visible()
        )
        assert (
            drawer.locator(".deck-card-actions")
            .get_by_role("button", name="Hold", exact=True)
            .is_visible()
        )
        if size[0] == 390:
            box = drawer.bounding_box()
            assert box == {"x": 0, "y": 0, "width": 390, "height": 844}
            for control in drawer.locator("button, a, input, select, textarea").all():
                if control.is_visible():
                    assert control.bounding_box()["height"] >= 44
        else:
            assert drawer.evaluate("el=>getComputedStyle(el).borderRadius") == "14px"
        page.screenshot(path=str(shots / f"drawer-{suffix}.png"))
        drawer.locator('.deck-card-actions [data-choice="Hold"]').click()
        page.wait_for_function("true")
        page.wait_for_timeout(100)
        assert ("/api/cards/1/decide", {"choice": "Hold", "then": "return", "version": 3}) in writes
        drawer.locator(".hide").click()
        page.locator('.card[data-card="2"] h3').click()
        page.wait_for_selector('.deck-card-drawer[data-card="2"]')
        drawer.locator(".deck-card-controls summary").click()
        removal = drawer.get_by_role("button", name="Remove assignment", exact=True)
        assert removal.is_visible()
        assert drawer.locator("#card-status").is_visible()
        assert drawer.get_by_role("button", name="Hand off", exact=True).is_visible()
        removal.click()
        page.wait_for_selector("form.assignment-removal")
        assert page.locator('form.assignment-removal [name="version"]').input_value() == "3"
        assert not errors, errors
    finally:
        page.close()
