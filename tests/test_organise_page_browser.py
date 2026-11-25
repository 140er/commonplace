"""The Memory link and the organise memory page in a real browser, at desktop and phone widths."""

import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests._chat_helpers import chat_environment, chat_hub_fixture  # noqa: F401
from tests.test_deck_memory_browser import _signed_in_page
from tests.test_deck_settings_browser import STATIC, browser as browser_fixture

browser = browser_fixture
WORDS = "Stored exactly as saved.\n  Indented line with $& and <b>markup</b>."
FACT = {
    "id": "t1",
    "subject": "Avery",
    "predicate": "works_at",
    "object": "Acme",
    "valid_from": "2024-01-01",
    "valid_to": None,
    "current": True,
    "wing": "shared",
    "sensitivity": "internal",
    "sources": [{"source_thread": "mcp:t1", "source_seq_start": 3}],
}
ENDED = {**FACT, "id": "t0", "object": "Initech", "valid_to": "2024-01-01", "current": False}
PLACES = {
    "wings": [
        {"wing": "shared", "rooms": [{"room": "alpha", "count": 1}, {"room": "diary", "count": 1}]}
    ]
}
DRAWERS = {
    "wing": "shared",
    "room": "alpha",
    "drawers": [
        {
            "id": "d1",
            "wing": "shared",
            "room": "alpha",
            "preview": "Stored exactly as saved.",
            "filed_at": "2026-11-02T10:00:00Z",
            "added_by": "agent",
            "source_file": None,
        }
    ],
    "total": 1,
    "offset": 0,
    "limit": 20,
}
TUNNEL = {
    "id": "tun1",
    "source": {"wing": "shared", "room": "alpha"},
    "target": {"wing": "shared", "room": "beta"},
    "label": "same topic",
    "access_count": 2,
    "created_at": "2026-11-01T10:00:00Z",
}


def open_organise(browser, width, view, *, answers=None):
    context = browser.new_context(
        viewport={"width": width, "height": 844 if width == 390 else 800},
        device_scale_factor=2,
        timezone_id="UTC",
    )
    page = context.new_page()
    page.set_default_timeout(10000)
    posts, state = [], {"ended": False}
    answers = answers or {}

    def serve(route):
        request = route.request
        path = urlsplit(request.url).path

        def reply(body, status=200):
            route.fulfill(status=status, body=json.dumps(body), content_type="application/json")

        if path == "/api/organise/facts":
            reply(
                {
                    "entity": None,
                    "facts": [{**FACT, "valid_to": "2026-11-18"}, ENDED]
                    if state["ended"]
                    else [FACT, ENDED],
                    "total": 2,
                    "offset": 0,
                    "limit": 100,
                }
            )
        elif path == "/api/organise/facts/invalidate":
            body = json.loads(request.post_data)
            posts.append(body)
            if not body.get("confirm"):
                reply(
                    {
                        "effect_id": "e1",
                        "preview": {
                            "action": "invalidate",
                            "fact": FACT,
                            "ended": "2026-11-18",
                            "lines": [
                                "Marks this fact as no longer true from 2026-11-18; it stops "
                                "being returned as current.",
                                "The fact, its start date, its wing and its sources stay stored "
                                "and stay in its history.",
                                "No stored memory text is deleted or changed.",
                            ],
                        },
                    }
                )
            elif answers.get("refuse"):
                reply(
                    {"error": "This change no longer matches what was shown. Nothing was changed."},
                    409,
                )
            else:
                state["ended"] = True
                reply({"done": True})
        elif path == "/api/organise/places":
            reply(PLACES)
        elif path == "/api/organise/sources":
            reply(DRAWERS)
        elif path == "/api/organise/source":
            reply({"id": "d1", "text": WORDS})
        elif path == "/api/organise/links":
            reply(
                {
                    "tunnels": [TUNNEL],
                    "total": 1,
                    "shown": 1,
                    "hallways": {"rows": [], "total": 7},
                }
            )
        elif path.startswith("/api/"):
            reply({"error": "unexpected"}, 500)
        else:
            name = path.removeprefix("/app/")
            file = STATIC / name
            route.fulfill(
                status=200 if file.is_file() else 404,
                body=file.read_bytes() if file.is_file() else b"",
                content_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
            )

    page.route("**/*", serve)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"http://example.test/app/organise.html?view={view}&ui=deck")
    page.wait_for_selector(".organise-section, [data-organise-retry]")
    page.evaluate("document.fonts.ready")
    return context, page, posts, errors


def shot(page, name):
    target = os.environ.get("ORGANISE_SHOTS_DIR")
    if target:
        page.screenshot(path=str(Path(target) / f"{name}.png"), full_page=True)


def no_sideways_scroll(page):
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["desktop", "phone"])
def test_memory_page_has_a_named_entry_to_organise_memory(browser, chat_hub, size):
    from playwright.sync_api import expect

    context, page, origin = _signed_in_page(browser, chat_hub, size)
    try:
        page.goto(origin + "/app/?ui=deck#/memory")
        link = page.get_by_role("link", name="Organise memory")
        expect(link).to_be_visible(timeout=15000)
        assert link.get_attribute("href") == "/app/organise.html"
        no_sideways_scroll(page)
        shot(page, f"memory-entry-{size[0]}")
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_facts_end_a_fact_cancel_then_confirm(browser, width):
    from playwright.sync_api import expect

    context, page, posts, errors = open_organise(browser, width, "facts")
    try:
        expect(page.locator(".organise-fact")).to_have_count(2)
        expect(page.locator(".organise-list")).to_contain_text("From 2024-01-01 · Ended 2024-01-01")
        assert page.locator("[data-organise-end]").count() == 1  # only the current fact
        no_sideways_scroll(page)
        shot(page, f"facts-{width}")
        page.locator("[data-organise-end]").click()
        panel = page.locator("[data-organise-confirm-panel]")
        expect(panel).to_contain_text("No stored memory text is deleted or changed.")
        shot(page, f"facts-confirm-{width}")
        page.get_by_role("button", name="Cancel, change nothing").click()
        expect(panel).to_have_count(0)
        assert posts == [
            {"subject": "Avery", "predicate": "works_at", "object": "Acme", "wing": "shared"}
        ]  # the preview only; cancelling sent nothing
        page.locator("[data-organise-end]").click()
        page.locator("[data-organise-confirm]").click()
        expect(page.locator(".organise-notice")).to_contain_text("End this fact: done")
        expect(page.locator("[data-organise-end]")).to_have_count(0)
        assert posts[-1]["confirm"] is True and posts[-1]["effect_id"] == "e1"
        shot(page, f"facts-done-{width}")
        assert errors == []
    finally:
        context.close()


def test_a_refused_confirm_keeps_the_old_state_and_says_so(browser):
    from playwright.sync_api import expect

    context, page, posts, errors = open_organise(browser, 390, "facts", answers={"refuse": True})
    try:
        page.locator("[data-organise-end]").click()
        page.locator("[data-organise-confirm]").click()
        expect(page.locator("[data-organise-confirm-panel]")).to_contain_text(
            "no longer matches what was shown. Nothing was changed."
        )
        page.get_by_role("button", name="Cancel, change nothing").click()
        expect(page.locator("[data-organise-end]")).to_have_count(1)  # the fact is still current
        shot(page, "facts-refused-390")
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_sources_show_words_exactly_and_links_list_both_ends(browser, width):
    from playwright.sync_api import expect

    context, page, _, errors = open_organise(browser, width, "sources")
    try:
        page.goto(
            "http://example.test/app/organise.html?view=sources&wing=shared&room=alpha&ui=deck"
        )
        expect(page.locator(".organise-source")).to_have_count(1)
        page.get_by_role("button", name="Read the whole text").click()
        words = page.locator(".organise-source .organise-words")
        expect(words).to_have_text(WORDS)
        assert page.locator(".organise-source b").count() == 0  # markup stays text
        no_sideways_scroll(page)
        shot(page, f"sources-{width}")
        page.goto("http://example.test/app/organise.html?view=links&ui=deck")
        expect(page.locator(".organise-link")).to_contain_text("shared / alpha ↔ shared / beta")
        expect(page.locator(".organise-link")).to_contain_text("Label: same topic")
        no_sideways_scroll(page)
        shot(page, f"links-{width}")
        assert errors == []
    finally:
        context.close()
