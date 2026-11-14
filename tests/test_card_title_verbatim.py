"""A card title is stored and returned exactly as typed, '#NNN' tokens included."""

import pytest

from tests.test_commonplace_boards import (
    api,
    env as env,
    hub_fixture as hub_fixture,
)

TITLES = ["#12 foo", "foo #12 bar", "#599 retest", "PR #7 and #8, see #9", "  #3 padded  "]


@pytest.mark.parametrize("title", TITLES)
def test_title_round_trips_verbatim_through_the_cards_api(hub, env, title):
    _token, _principal, _person, cookie, _owner = env
    status, created = api(hub, cookie, "POST", "/api/boards/work/cards", {"title": title})
    assert status in (200, 201) and created["title"] == title
    status, listed = api(hub, cookie, "GET", "/api/boards/work/cards")
    stored = [card["title"] for card in listed["cards"] if card["number"] == created["number"]]
    assert status == 200 and stored == [title]
