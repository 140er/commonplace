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
        page.wait_for_timeout(500)
        reads.clear()
        page.locator('.card-menu [data-menu-move="doing"]').click()
        page.wait_for_function(
            "()=>document.querySelector(\".deck-board .card[data-card='4']\")"
            "?.closest('.col')?.querySelector('h2')?.textContent.trim() === 'Active'",
            timeout=5000,
        )
        assert not page.locator("#card-panel").is_hidden(), "the panel stays open"
        assert reads.count("/api/boards/product/cards") == 1, reads
        assert reads.count("/api/needs-me") == 1, reads
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
        page.wait_for_timeout(500)
        reads.clear()
        form.locator("[data-release-origin]").click()
        page.wait_for_function(
            "()=>document.querySelector(\".deck-board .card[data-card='6']\")"
            "?.closest('.col')?.querySelector('h2')?.textContent.trim() === 'Active'",
            timeout=5000,
        )
        assert not page.locator("#card-panel").is_hidden(), "the panel stays open"
        assert reads.count("/api/boards/product/cards") == 1, reads
        assert reads.count("/api/needs-me") == 1, reads
        assert untouched(page), "the page was reloaded"
        assert errors == []
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
        page.wait_for_timeout(500)
        metrics = page.evaluate(
            """() => {
              const intro = document.querySelector('.deck-board-intro');
              const line = parseFloat(getComputedStyle(intro).lineHeight) || 20;
              const words = intro.textContent.trim().split(/\\s+/).length;
              return { title: document.querySelector('.deck-board-title').getBoundingClientRect().width,
                       tools: document.querySelector('.deck-board .board-tools').getBoundingClientRect().width,
                       lines: Math.round(intro.getBoundingClientRect().height / line), words };
            }"""
        )
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            page.screenshot(
                path=str(Path(SHOTS) / f"header-{width}-{'named' if named else 'unnamed'}.png")
            )
        assert metrics["lines"] <= max(1, metrics["words"] // 3), f"one word per line: {metrics}"
    finally:
        page.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_classic_header_keeps_its_layout_with_the_panel_open(browser, width):  # noqa: F811
    page = browser.new_page(viewport={"width": width, "height": 800}, reduced_motion="reduce")
    try:
        open_board(page, look="classic")
        page.locator('.card[data-card="2"] h3 a').click()
        page.wait_for_selector("#card-panel:not([hidden]) .workbench-details")
        page.wait_for_timeout(500)
        metrics = page.evaluate(
            """() => {
              const title = document.querySelector('#title'), head = title.closest('.head');
              const line = parseFloat(getComputedStyle(title).lineHeight) || 20;
              return { title: title.getBoundingClientRect().width, head: head.getBoundingClientRect().width,
                       lines: Math.round(title.getBoundingClientRect().height / line), text: title.textContent };
            }"""
        )
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(SHOTS) / f"header-{width}-classic.png"))
        assert metrics["lines"] <= 1 and metrics["title"] >= 80, f"the title is squeezed: {metrics}"
    finally:
        page.close()
