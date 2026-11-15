"""Optional real-shell proof: opening a card shows what is known at once, reads beside the refresh, and never squeezes the board.

Skips cleanly where no browser is installed; the reads are held by the test, so no timing is involved.
"""

from urllib.parse import urlsplit

import pytest

from tests.test_deck_board_browser import CARDS, browser, open_board  # noqa: F401

CARD = next(card for card in CARDS if card["number"] == 3)
MIN_COLUMN = 220


def hold(page, *paths):
    """Hold GET reads of the given paths until the test releases them."""
    state = {"on": False, "held": []}

    def handler(route):
        if state["on"] and urlsplit(route.request.url).path in paths:
            state["held"].append(route)
        else:
            route.fallback()

    page.route("**/api/**", handler)
    return state


def release(state):
    state["on"] = False
    held, state["held"] = state["held"], []
    for route in held:
        route.fallback()


def columns(page):
    return page.evaluate(
        "[...document.querySelectorAll('.deck-board .col')].map(c=>c.getBoundingClientRect().width)"
    )


@pytest.mark.parametrize("width", [1280, 1024])
def test_board_columns_stay_readable_with_the_card_panel_open(browser, width):  # noqa: F811
    page = browser.new_page(viewport={"width": width, "height": 800}, reduced_motion="reduce")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        open_board(page)
        reads = hold(page, "/api/cards/3")
        reads["on"] = True
        page.locator('.card[data-card="3"]').click()
        page.wait_for_selector("#card-panel:not([hidden])")
        assert "Loading card" in page.locator("#card-panel").inner_text()
        assert min(columns(page)) >= MIN_COLUMN, "columns while the card loads"
        release(reads)
        page.wait_for_selector("#card-panel .deck-card-actions")
        assert min(columns(page)) >= MIN_COLUMN, "columns once the card is drawn"
        assert errors == []
    finally:
        page.close()


def test_known_fields_show_before_the_card_read_answers(browser):  # noqa: F811
    page = browser.new_page(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        open_board(page)
        reads = hold(page, "/api/cards/3")
        reads["on"] = True
        page.locator('.card[data-card="3"]').click()
        page.wait_for_selector("#card-panel:not([hidden])")
        panel = page.locator("#card-panel")
        text = panel.inner_text()
        assert CARD["title"] in text and "Building" in text, text
        assert "Ask before reading activity" in text, "the summary comes from the list"
        assert "Loading card" in text
        assert panel.get_by_role("button", name="Move to…").count() == 0, (
            "no control before the read"
        )
        assert panel.get_by_role("button", name="Close card").count() == 1
        release(reads)
        page.wait_for_selector("#card-panel .deck-card-actions")
        assert panel.get_by_role("button", name="Move to…").count() == 1
        assert "Loading card" not in panel.inner_text()
        assert errors == []
    finally:
        page.close()


def test_card_read_does_not_wait_for_the_project_refresh(browser):  # noqa: F811
    page = browser.new_page(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        open_board(page)
        reads = hold(page, "/api/boards/product/cards")
        reads["on"] = True
        page.locator('.card[data-card="3"]').click()
        # The refresh is still unanswered; the card's own read has already been drawn.
        page.wait_for_selector("#card-panel .deck-card-actions", timeout=5000)
        assert reads["held"], "the project refresh is still waiting"
        release(reads)
        page.wait_for_selector('.card[data-card="3"].is-selected')
        assert page.locator("#card-panel .deck-card-actions").count() == 1
        assert errors == []
    finally:
        page.close()
