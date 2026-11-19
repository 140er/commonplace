"""The card panel's Previews section in a real browser: every state, desktop and phone, one click to open.

The shell runs on its static files with a fixed API (the deck board fixture), so no server state
is needed. Set CARD_PREVIEWS_SHOTS to a directory to keep a screenshot of the card panel in every
state at desktop (1280x800) and phone (390x844) size.
"""

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from tests import test_deck_board_browser as shell
from tests.test_deck_board_browser import browser  # noqa: F401

PREVIEW = {
    "id": "pv_0123456789abcdef",
    "name": "home",
    "kind": "static",
    "placement": "hub",
    "revision": 2,
    "state": "ready",
    "reason": "published",
    "reason_text": "Published and open to people who can open this card.",
    "next": "",
    "availability": "reachable",
    "size_bytes": 2048,
    "file_count": 3,
    "pr": None,
    "published_by": "release-coworker",
    "by": "coworker",
    "published_at": "2026-01-01T11:00:00Z",
    "stops_at": None,
    "last_changed_at": "2026-01-01T11:00:00Z",
    "url": "/p/pv_0123456789abcdef/",
}
STATES = {
    6: ("ready", [PREVIEW]),
    7: ("none", []),
    8: (
        "building",
        [
            {
                **PREVIEW,
                "id": "pv_0000000000000008",
                "state": "building",
                "reason": "storing",
                "reason_text": "The files are being saved.",
                "next": "Wait a moment.",
                "revision": 0,
                "url": None,
            }
        ],
    ),
    9: (
        "stopped",
        [
            {
                **PREVIEW,
                "id": "pv_0000000000000009",
                "state": "stopped",
                "reason": "stopped_by_person",
                "reason_text": "Stopped by a person. The files are kept.",
                "next": "Use Open again to serve it.",
                "url": "/p/pv_0000000000000009/",
            }
        ],
    ),
    10: (
        "failed",
        [
            {
                **PREVIEW,
                "id": "pv_0000000000000010",
                "state": "failed",
                "reason": "no_index_html",
                "reason_text": "The bundle has no index.html at its top level.",
                "next": "Publish again with an index.html.",
                "revision": 0,
                "url": None,
            }
        ],
    ),
    11: ("offline", None),
    12: ("refused", None),
}
RULE = {
    "stop_days_after_pr": None,
    "max_age_days": None,
    "updated_at": None,
    "text": "Previews are served until a person or the Coworker that published them stops them.",
}


def listing(previews):
    return {
        "previews": previews,
        "usage": {"ready": 1, "bytes": 2048},
        "limits": {"max_ready": 25, "max_workspace_bytes": 500 * 1024 * 1024},
        "rule": RULE,
        "can_set_rule": True,
    }


@pytest.mark.parametrize("size", [(1280, 800), (390, 844)], ids=["desktop", "phone"])
def test_every_preview_state_reads_in_words_and_opens_in_one_click(browser, size, tmp_path):  # noqa: F811
    evidence = Path(os.environ.get("CARD_PREVIEWS_SHOTS") or tmp_path)
    evidence.mkdir(parents=True, exist_ok=True)
    width, height = size
    page = browser.new_page(viewport={"width": width, "height": height}, reduced_motion="reduce")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    cards = [dict(card) for card in shell.CARDS] + [
        {**shell.CARD, "number": n, "title": f"Preview card {label}", "ask": None, "need": "idle"}
        for n, (label, _) in STATES.items()
    ]
    shell.open_board(page, cards=cards)
    writes = []

    def previews_api(route):
        url = urlsplit(route.request.url)
        if url.path != "/api/previews":
            writes.append((url.path, route.request.method))
            route.fulfill(json={})
            return
        number = int(parse_qs(url.query)["card"][0])
        label, previews = STATES[number]
        if label == "offline":
            route.abort()
        elif label == "refused":
            route.fulfill(
                status=404, json={"error": "Preview or card not found", "reason": "not_found"}
            )
        else:
            route.fulfill(json=listing(previews))

    page.route("**/api/previews**", previews_api)
    # On the context, so the new tab the Open link makes is answered too.
    page.context.route(
        "**/p/**",
        lambda route: route.fulfill(
            body="<!doctype html><title>Preview page</title><h1>Preview page</h1>",
            content_type="text/html",
        ),
    )
    panel = page.locator("#card-panel")
    look = "desktop" if width > 700 else "phone"
    for number, (label, _previews) in STATES.items():
        page.evaluate(f"location.hash='#/card/{number}'")
        page.wait_for_selector(
            f'#card-panel .workbench-details[data-card="{number}"]:not([aria-busy])'
        )
        section = panel.locator("[data-card-previews]")
        if label == "refused":
            # A card the person cannot see behaves like a missing card: no Previews section at all.
            section.wait_for(state="hidden")
        else:
            section.locator("h3", has_text="Previews").wait_for()
            words = {
                "ready": "Published and open to people who can open this card.",
                "none": "No preview yet.",
                "building": "The files are being saved.",
                "stopped": "Use Open again to serve it.",
                "failed": "The bundle has no index.html at its top level.",
                "offline": "Could not load the previews.",
            }[label]
            section.get_by_text(words).wait_for()
            section.scroll_into_view_if_needed()
            for control in section.locator("button:visible, a.btn:visible").all():
                box = control.bounding_box()
                assert box and box["width"] > 0
                if look == "phone":
                    assert box["height"] >= 44, (label, control.inner_text(), box)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        panel.screenshot(path=str(evidence / f"previews-{look}-{label}.png"))

    # One click from the card opens the preview in a new tab.
    page.evaluate("location.hash='#/card/6'")
    page.wait_for_selector('#card-panel .workbench-details[data-card="6"]:not([aria-busy])')
    opener = panel.locator("[data-preview-open]")
    opener.wait_for()
    with page.context.expect_page() as opened:
        opener.click()
    tab = opened.value
    tab.wait_for_load_state()
    assert tab.url.endswith("/p/pv_0123456789abcdef/") and tab.title() == "Preview page"
    tab.close()
    # The keyboard reaches Stop and uses it.
    stop = panel.locator("[data-preview-stop]")
    stop.focus()
    page.keyboard.press("Enter")
    page.wait_for_function("() => true")
    assert ("/api/previews/pv_0123456789abcdef/stop", "POST") in writes
    assert errors == [], json.dumps(errors)
