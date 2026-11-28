"""Optional real-shell proof: a card action finished behind the open panel changes the card's lane without a
reload, with one board read and one queue read per action, and the board header keeps its layout beside the panel.

Skips cleanly where no browser is installed. The fake workspace changes a card only when the write arrives, and the
owner queue lists exactly the cards that still wait on the owner.
"""

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.test_deck_board_browser import BOARD, CARDS, browser, open_board  # noqa: F401

SHOTS = os.environ.get("CARD_REFRESH_SHOTS")
WIDTHS = [1280, 1024]
RESERVED = {
    **CARDS[1],
    "number": 6,
    "title": "Agent card reserved for another agent",
    "status": "todo",
    "assignee": None,
    "ask": None,
    "need": "idle",
    "awaiting_release": True,
    "can_approve": True,
    "creator": {"name": "Outside agent", "trust": "untrusted"},
}


def workspace():
    """A fresh copy of the cards and the numbers the owner queue lists (a review card, one to move and the reserved one)."""
    cards = [json.loads(json.dumps(card)) for card in [*CARDS, RESERVED]]
    next(card for card in cards if card["number"] == 4)["review_kind"] = "moved"
    return cards, {1, 4, 6}


def serve_writes(page, cards, queue, reads):
    """Apply card writes to the fake workspace, keep the owner queue in step and count the reads after them."""

    def handler(route):
        request, path = route.request, urlsplit(route.request.url).path
        match = re.fullmatch(r"/api/cards/(\d+)(?:/(handoff|approve))?", path)
        if request.method == "GET" and path in ("/api/boards/product/cards", "/api/needs-me"):
            reads.append(path)
        if path == "/api/needs-me" and request.method == "GET":
            listed = [c for c in cards if c["number"] in queue]
            route.fulfill(
                body=json.dumps({"count": len(listed), "approvals": listed}),
                content_type="application/json",
            )
        elif match and request.method in ("PATCH", "POST"):
            reads.append("WRITE")
            card = next(c for c in cards if c["number"] == int(match.group(1)))
            body = request.post_data_json or {}
            if match.group(2) == "approve":
                card["awaiting_release"] = False
            elif match.group(2) == "handoff":
                card["assignee"] = None
            elif "status" in body:
                card["status"] = body["status"]
            card["version"] += 1
            queue.discard(card["number"])
            route.fulfill(body=json.dumps(card), content_type="application/json")
        else:
            route.fallback()

    page.route("**/api/**", handler)


# A squeezed header wraps its words one per line: report every visible text node under the selector
# that spans more than one line, once the fonts are in.
WRAPPED_TEXT = """async (selector) => {
  await document.fonts.ready;
  const root = document.querySelector(selector), walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const wrapped = [];
  let nodes = 0;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!node.textContent.trim()) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    const tops = new Set([...range.getClientRects()].map((rect) => Math.round(rect.top)));
    if (!tops.size) continue;
    nodes += 1;
    if (tops.size > 1) wrapped.push(node.textContent.trim());
  }
  return { nodes, wrapped };
}"""


def reads_after_write(reads, path):
    """Reads of one path that came after the action's own write, so no earlier refresh is counted."""
    return reads[reads.index("WRITE") + 1 :].count(path)


def lane(page, number):
    return page.evaluate(
        "n=>document.querySelector(`.deck-board .card[data-card='${n}']`)"
        "?.closest('.col')?.querySelector('h2')?.textContent.trim()",
        number,
    )


def open_card(page, number):
    page.locator(f'.card[data-card="{number}"] h3 a').click()
    page.wait_for_selector("#card-panel .deck-card-actions")


def start(page, cards, queue, reads):
    open_board(page, cards=cards)
    serve_writes(page, cards, queue, reads)
    page.reload()
    page.wait_for_selector('.card[data-card="1"]')
    page.evaluate("window.__loadedOnce = true")
    reads.clear()


def untouched(page):
    return page.evaluate("window.__loadedOnce === true")


@pytest.mark.parametrize("width", WIDTHS)
def test_moving_a_card_changes_its_lane_behind_the_open_panel(browser, width):  # noqa: F811
    page = browser.new_page(viewport={"width": width, "height": 800}, reduced_motion="reduce")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    cards, queue = workspace()
    reads = []
    try:
        start(page, cards, queue, reads)
        page.wait_for_function(
            "()=>document.querySelector(\".deck-board .card[data-card='4']\")"
            "?.closest('.col')?.querySelector('h2')?.textContent.trim() === 'Waiting on you'",
            timeout=5000,
        )
        open_card(page, 4)
        page.locator('#card-panel [data-action="card-menu"]').click()
        page.locator('.card-menu [data-menu-move="doing"]').wait_for()
        page.locator('.card-menu [data-menu-move="doing"]').click()
        page.wait_for_function(
            "()=>document.querySelector(\".deck-board .card[data-card='4']\")"
            "?.closest('.col')?.querySelector('h2')?.textContent.trim() === 'Active'",
            timeout=5000,
        )
        assert not page.locator("#card-panel").is_hidden(), "the panel stays open"
        page.wait_for_load_state("networkidle")
        assert reads_after_write(reads, "/api/boards/product/cards") == 1, reads
        assert reads_after_write(reads, "/api/needs-me") == 1, reads
        assert untouched(page), "the page was reloaded"
        assert errors == []
    finally:
        page.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_releasing_a_card_changes_its_lane_behind_the_open_panel(browser, width):  # noqa: F811
    page = browser.new_page(viewport={"width": width, "height": 800}, reduced_motion="reduce")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    cards, queue = workspace()
    reads = []
    try:
        start(page, cards, queue, reads)
        assert lane(page, 6) == "Waiting on you"
        open_card(page, 6)
        form = page.locator('#card-panel form[data-form="release-origin"]')
        form.locator('input[name="reason"]').fill("Checked the source")
        form.locator("[data-release-origin]").click()
        page.wait_for_function(
            "()=>document.querySelector(\".deck-board .card[data-card='6']\")"
            "?.closest('.col')?.querySelector('h2')?.textContent.trim() === 'Active'",
            timeout=5000,
        )
        assert not page.locator("#card-panel").is_hidden(), "the panel stays open"
        page.wait_for_load_state("networkidle")
        assert reads_after_write(reads, "/api/boards/product/cards") == 1, reads
        assert reads_after_write(reads, "/api/needs-me") == 1, reads
        assert untouched(page), "the page was reloaded"
        assert errors == []
    finally:
        page.close()


def test_released_panel_is_ready_before_the_owner_queue_refresh_answers(browser):  # noqa: F811
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    cards, queue = workspace()
    held = []
    try:
        start(page, cards, queue, [])
        open_card(page, 6)
        page.wait_for_load_state("networkidle")
        page.route("**/api/needs-me", lambda route: held.append(route))
        form = page.locator('#card-panel form[data-form="release-origin"]')
        form.locator('input[name="reason"]').fill("Checked the source")
        with page.expect_request("**/api/needs-me"):
            form.locator("[data-release-origin]").click()
        expect(form).to_have_count(0)
        expect(page.locator("#card-panel .deck-card-state")).to_contain_text("Ready")
        assert held, "the owner queue read is still unanswered"
        page.unroute("**/api/needs-me")
        for route in held:
            route.fallback()
        held.clear()
        page.wait_for_load_state("networkidle")
        expect(page.locator("#card-panel .deck-card-state")).to_contain_text("Ready")
    finally:
        page.close()


@pytest.mark.parametrize("number", [6, 2], ids=["release-reason", "priority"])
def test_same_version_panel_read_keeps_live_controls(browser, number):  # noqa: F811
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    cards, queue = workspace()
    next(card for card in cards if card["number"] == number).update(
        can_prioritize=True, priority="high"
    )
    held = []
    try:
        start(page, cards, queue, [])
        page.route("**/api/boards/product/cards", lambda route: held.append(route))
        open_card(page, number)
        if number == 6:
            field = page.locator('form[data-form="release-origin"] input[name="reason"]')
            draft = "Checked the source"
            field.fill(draft)
        else:
            page.locator("#card-panel summary", has_text="Edit card").click()
            field = page.locator("#card-priority")
            draft = "urgent"
            field.select_option(draft)
        assert held, "the project read is still holding the initial panel refresh"
        page.unroute("**/api/boards/product/cards")
        for route in held:
            route.fallback()
        held.clear()
        page.wait_for_load_state("networkidle")
        expect(field).to_have_value(draft)
    finally:
        page.close()


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("named", [True, False], ids=["named", "unnamed"])
def test_board_header_keeps_its_layout_with_the_panel_open(browser, width, named, monkeypatch):  # noqa: F811
    monkeypatch.setitem(BOARD, "name", BOARD["name"] if named else "")
    page = browser.new_page(viewport={"width": width, "height": 800}, reduced_motion="reduce")
    try:
        open_board(page)
        open_card(page, 2)
        metrics = page.evaluate(WRAPPED_TEXT, ".deck-board-heading")
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            page.screenshot(
                path=str(Path(SHOTS) / f"header-{width}-{'named' if named else 'unnamed'}.png")
            )
        assert metrics["nodes"] > 0 and metrics["wrapped"] == [], f"the header wraps: {metrics}"
    finally:
        page.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_classic_header_keeps_its_layout_with_the_panel_open(browser, width):  # noqa: F811
    page = browser.new_page(viewport={"width": width, "height": 800}, reduced_motion="reduce")
    try:
        open_board(page, look="classic")
        page.locator('.card[data-card="2"] h3 a').click()
        page.wait_for_selector("#card-panel:not([hidden]) .workbench-details")
        metrics = page.evaluate(WRAPPED_TEXT, "#title")
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(SHOTS) / f"header-{width}-classic.png"))
        assert metrics["nodes"] > 0 and metrics["wrapped"] == [], f"the title wraps: {metrics}"
    finally:
        page.close()
