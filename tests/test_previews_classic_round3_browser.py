"""A Classic phone's comment footer must never cover preview deletion controls."""

import pytest

from tests import test_deck_board_browser as shell
from tests.test_card_previews_browser import PREVIEW, listing
from tests.test_deck_board_browser import browser  # noqa: F401


@pytest.mark.parametrize("width", [320, 390])
def test_classic_phone_delete_confirmation_stays_above_the_comment_footer(browser, width):  # noqa: F811
    page = browser.new_page(viewport={"width": width, "height": 844}, has_touch=True)
    try:
        cards = [dict(c) for c in shell.CARDS] + [
            {**shell.CARD, "number": 6, "title": "Preview deletion", "ask": None, "need": "idle"}
        ]
        # This fixture waits for a Deck board card. Load Classic only after its routes exist.
        shell.open_board(page, cards=cards)
        found = {**listing([PREVIEW]), "can_delete": True}
        page.route("**/api/previews**", lambda route: route.fulfill(json=found))
        page.goto("http://example.test/app/?ui=classic#/card/6")
        section = page.locator("#card-panel [data-card-previews]")
        section.locator("[data-preview-delete]").click()
        cancel = section.locator("[data-preview-delete-cancel]")
        cancel.scroll_into_view_if_needed()
        assert cancel.evaluate("""e => {
          const r = e.getBoundingClientRect();
          return e.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
        }"""), "the comment footer covers Cancel in the deletion confirmation"
        cancel.click()
        assert section.locator("[data-preview-delete]").evaluate(
            "e => e === document.activeElement"
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    finally:
        page.close()
