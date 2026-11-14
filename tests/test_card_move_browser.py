"""Optional real-shell proof: Move to… is visible on the Deck board and in the drawer, on desktop and phone,
and moving a card to In review is one PATCH that never opens a question form."""

import pytest

from tests.test_deck_board_browser import browser, open_board  # noqa: F401

SIZES = [(1440, 900), (390, 844)]


@pytest.mark.parametrize("size", SIZES, ids=["desktop", "phone"])
def test_board_card_moves_in_two_clicks(browser, size):  # noqa: F811
    page = browser.new_page(viewport={"width": size[0], "height": size[1]}, reduced_motion="reduce")
    try:
        writes = open_board(page)
        # Card 3 is Active; its Move to… button is on the card with its words, not behind hover.
        button = page.locator('.card[data-card="3"] .card-more')
        assert button.inner_text().strip() == "Move to…"
        assert button.is_visible()
        button.click()
        names = [t.strip() for t in page.locator(".card-menu [data-menu-move]").all_inner_texts()]
        assert [n.split("\n")[0] for n in names] == ["Active", "In review", "Shipped"]
        page.locator('.card-menu [data-menu-move="review"]').click()
        patches = [(path, body) for path, body in writes if path == "/api/cards/3"]
        assert patches == [("/api/cards/3", {"version": 3, "status": "review"})]
        assert page.locator('#card-panel form[data-form="review"]').count() == 0
    finally:
        page.close()


@pytest.mark.parametrize("size", SIZES, ids=["desktop", "phone"])
def test_drawer_has_a_visible_move_to_control(browser, size):  # noqa: F811
    page = browser.new_page(viewport={"width": size[0], "height": size[1]}, reduced_motion="reduce")
    try:
        open_board(page)
        page.locator('.card[data-card="3"] h3 a').click()
        move = page.locator('.deck-card-actions [data-action="card-menu"]')
        move.wait_for()
        assert move.inner_text().strip() == "Move to…" and move.is_visible()
    finally:
        page.close()
