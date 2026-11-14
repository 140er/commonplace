"""Optional real-shell proof: Add card shows the new card at once, the panel never shows another card, Escape closes it.

Skips cleanly where no browser is installed; tests/test_board_add_card_selection.py holds the browser-free proof.
"""

import json
import mimetypes
import re
from urllib.parse import urlsplit

from tests.test_deck_board_browser import (  # noqa: F401
    BOARD,
    BOT,
    CARD,
    CARDS,
    STATIC,
    browser,
)

NEW = {
    **CARD,
    "number": 9,
    "title": "Pay the invoice",
    "status": "todo",
    "need": "idle",
    "ask": None,
    "ask_to": None,
    "ask_options": None,
    "assignee": "user:owner",
}


def open_shell(page, held, slow):
    cards = list(CARDS)
    responses = {
        "/api/me": {
            "actor": "user:owner",
            "name": "Owner",
            "workspace": "demo",
            "can_admin": True,
            "people": {},
            "role": "owner",
        },
        "/api/boards": {"boards": [BOARD]},
        "/api/chat/product": {"board": "product", "messages": []},
        "/api/needs-me": {"count": 1},
        "/api/bots": {"bots": [BOT]},
        "/api/rules/count": {"waiting": 0},
        "/api/events": {"events": [], "seq": 0, "epoch": "fixture"},
    }

    def serve(route):
        path = urlsplit(route.request.url).path
        method = route.request.method
        if path == "/api/boards/product/cards" and method == "POST":
            cards.append(NEW)
            route.fulfill(status=201, body=json.dumps(NEW), content_type="application/json")
        elif path == "/api/boards/product/cards" and slow["on"]:
            slow["routes"].append(
                route
            )  # the slow refresh after the add: answered later by the test
        elif path == "/api/boards/product/cards":
            body = {"cards": cards, "children": [], "linked": []}
            route.fulfill(body=json.dumps(body), content_type="application/json")
        elif re.fullmatch(r"/api/cards/\d+", path):
            number = int(path.rsplit("/", 1)[-1])
            if number == NEW["number"] and method == "GET":
                held.append(route)  # the slow read: answered later by the test
                return
            card = next(card for card in cards if card["number"] == number)
            route.fulfill(body=json.dumps(card), content_type="application/json")
        elif path.startswith("/api/"):
            route.fulfill(body=json.dumps(responses.get(path, {})), content_type="application/json")
        else:
            file = STATIC / (path.removeprefix("/app/") or "index.html")
            if file.is_file():
                type_ = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                route.fulfill(body=file.read_bytes(), content_type=type_)
            else:
                route.fulfill(status=404, body="")

    page.route("**/*", serve)
    page.goto("http://example.test/app/?ui=deck#/board/product")
    page.wait_for_selector('.card[data-card="1"]')


def test_add_card_selects_new_card_and_escape_closes_panel(browser):  # noqa: F811
    page = browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    errors, held, slow = [], [], {"on": False, "routes": []}
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        open_shell(page, held, slow)
        panel = page.locator("#card-panel")
        # Open another card (it has a live Approve), then close it with Escape: the panel keeps that card's markup.
        page.locator('.card[data-card="1"]').click()
        page.wait_for_selector(
            "#card-panel [data-action='decide'], #card-panel [data-deck-release], #card-panel .deck-card-actions"
        )
        assert panel.get_by_role("button", name="Approve", exact=True).count() == 1
        page.keyboard.press("Escape")
        page.wait_for_selector("#card-panel", state="hidden")
        # Add a card while the project's refresh and the new card's own read are both slow.
        slow["on"] = True
        page.locator(".deck-board-add:visible").first.click()
        page.fill('[data-board-add] input[name="title"]', NEW["title"])
        page.click('[data-board-add] button[type="submit"]')
        page.wait_for_selector('.card[data-card="9"].is-selected')
        assert page.locator("[data-board-add]").count() == 0, "the form closes"
        assert page.locator('.card[data-card="9"]').count() == 1
        assert page.evaluate("location.hash") == "#/card/9"
        page.wait_for_selector("#card-panel:not([hidden])")
        text = panel.inner_text()
        assert "Loading card" in text and CARD["title"] not in text
        assert panel.get_by_role("button", name="Approve", exact=True).count() == 0
        assert "Added" in page.locator("[data-board-added]").inner_text()
        page.wait_for_function("document.querySelector('#card-panel [data-slow]')")
        # The reads arrive: the panel shows the new card.
        slow["on"] = False
        body = json.dumps({"cards": [*CARDS, NEW], "children": [], "linked": []})
        for route in slow["routes"]:
            route.fulfill(body=body, content_type="application/json")
        for _ in range(50):
            if held:
                break
            page.wait_for_timeout(100)
        assert held, "the card read starts once the refresh is answered"
        held[0].fulfill(body=json.dumps(NEW), content_type="application/json")
        page.wait_for_function(
            "(t) => document.querySelector('#card-panel')?.innerText.includes(t)", arg=NEW["title"]
        )
        assert panel.get_by_role("button", name="Approve", exact=True).count() == 0
        page.locator('.card[data-card="9"]').focus()
        page.keyboard.press("Escape")
        page.wait_for_selector("#card-panel", state="hidden")
        assert errors == []
    finally:
        page.close()
