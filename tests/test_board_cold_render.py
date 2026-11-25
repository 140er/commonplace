"""A large board skips off-screen card work without removing a card from the page."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "commonplace" / "web" / "static"


def test_cards_past_the_first_screenful_skip_offscreen_work():
    source = (STATIC / "board.js").read_text()
    rules = re.findall(
        r"\.workbench-board \.cards > \.card:nth-child\(n\+(\d+)\)\s*\{([^}]*)\}", source
    )
    assert len(rules) == 1
    first_deferred, body = rules[0]
    # Twelve cards per column always paint; the rest wait until they near the viewport.
    assert first_deferred == "13"
    assert "content-visibility: auto" in body
    assert re.search(r"contain-intrinsic-block-size:\s*auto \d+px", body)


def test_deferring_is_css_only_and_never_hides_or_removes_cards():
    source = (STATIC / "board.js").read_text()
    # A card list is never sliced for the first paint: unknown or slow data must not render fewer cards.
    assert (
        "slice("
        not in source.split("export function boardView", 1)[1].split("export const ANY_AGENT", 1)[0]
    )
    assert "content-visibility: hidden" not in source
