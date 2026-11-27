"""Decision announcements distinguish saved comments from the card's confirmed transition."""

import os
from pathlib import Path

import pytest

from tests.test_card_reviews_browser import EVERY, cards, open_shell, view
from tests.test_deck_board_browser import browser  # noqa: F401


@pytest.mark.parametrize("look", ["deck", "classic"])
@pytest.mark.parametrize("size", ["desktop", "phone"])
@pytest.mark.parametrize("result", ["confirmed", "unrelated_question", "card_refused"])
def test_the_card_transition_announcement_matches_the_confirmed_result(
    browser,  # noqa: F811
    look,
    size,
    result,
    tmp_path,
):
    page, errors, _ = open_shell(browser, look, size, cards())
    answered = False
    posts = []

    def reviews_api(route):
        nonlocal answered
        if route.request.method == "POST":
            posts.append(route.request.post_data_json)
            answered = True
            route.fulfill(
                json={
                    "choice": "mine",
                    "card_answered": result == "confirmed",
                    "card_answer_reason": None if result == "confirmed" else result,
                    "card_question_pending": result != "confirmed",
                }
            )
        else:
            route.fulfill(json=view([] if answered else [EVERY[4]]))

    page.route("**/api/pr-review/cards/**", reviews_api)
    page.evaluate("location.hash='#/card/7'")
    section = page.locator("#card-panel [data-card-reviews]")
    section.locator('[data-review-answer="mine"]').click()
    status = section.locator("[data-review-status]")
    status.get_by_text("The bot stops answering these comments", exact=False).wait_for()
    text = status.inner_text()
    if result == "confirmed":
        assert "the card moved to Backlog" in text
    else:
        assert "the card moved to Backlog" not in text, text
        assert "The card still has" in text, text
        assert "answer it on the card" in text, text
        if result == "unrelated_question":
            assert "another question" in text, text
        else:
            assert "could not be saved" in text, text
    assert posts == [{"choice": "mine"}]
    assert section.locator("[data-review-answer]").count() == 0
    assert page.evaluate(
        "document.activeElement?.getAttribute('data-review-heading') === 'reviews'"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    status.scroll_into_view_if_needed()
    evidence = Path(os.environ.get("CARD_REVIEWS_SHOTS") or tmp_path)
    evidence.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(evidence / f"decision-{result}-{look}-{size}.png"), full_page=True)
    assert not errors, errors
    page.close()
