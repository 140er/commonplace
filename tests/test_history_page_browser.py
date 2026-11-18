"""The Memory link and the history and handoff page in a real browser, at desktop and phone widths."""

import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from tests._chat_helpers import chat_environment, chat_hub_fixture  # noqa: F401
from tests.test_deck_memory_browser import _signed_in_page
from tests.test_deck_settings_browser import STATIC, browser as browser_fixture

browser = browser_fixture
THREAD = {
    "id": "thr_new",
    "message_count": 30,
    "created_at": "2026-11-02T10:00:00Z",
    "created_by": "conn:agent-one",
}
OLDER = {"id": "thr_old", "message_count": 12, "created_at": "2026-11-01T10:00:00Z"}
READY = {
    "thread": "thr_new",
    "state": "ready",
    "handoff_id": "hof_1",
    "continues": {"thread_id": "mcp:live", "agent": "codex:vm", "task": "#142"},
}
DENIED = {
    "thread": "thr_new",
    "state": "unavailable",
    "can_start": False,
    "reason": "Starting a handoff needs a signed-in workspace member, or an agent connection with "
    "handoff:write and trusted trust. You can still read this history.",
    "next_step": "",
}
PREVIEW = {
    "preview": True,
    "thread": "thr_new",
    "continues": READY["continues"],
    "destination": {"provider": "codex", "model": "gpt-6.1-sol", "effort": "high"},
    "note": "Keep going with the tests.",
    "carried": {"earlier_threads": 2, "earlier_messages": 42, "history": ["thr_new", "thr_old"]},
}
STARTED = {
    "preview": False,
    "thread": "thr_new",
    "handoff_id": "hof_2",
    "stored_thread_id": "thr_next",
    "state": "done",
    "successor": {"agent_ref": "t3:mcp:successor"},
    "source_settled": True,
}
REFUSAL = {
    "error": "successor launch failed; source is still running; inspect T3 before relaunch",
    "next_step": "The old thread is still running and nothing was lost. Check that T3 is running "
    "on the machine that hosts this workspace, then try again.",
}


def open_history(browser, width, offer, *, thread="thr_new", starts=()):
    context = browser.new_context(
        viewport={"width": width, "height": 844 if width == 390 else 800},
        device_scale_factor=2,
        timezone_id="UTC",
    )
    page = context.new_page()
    page.set_default_timeout(10000)
    posts, answers = [], list(starts)

    def serve(route):
        request = route.request
        url = urlsplit(request.url)
        path = url.path

        def reply(body, status=200):
            route.fulfill(status=status, body=json.dumps(body), content_type="application/json")

        if path == f"/api/threads/{thread}":
            reply(THREAD)
        elif path == f"/api/threads/{thread}/chain":
            reply([THREAD, OLDER])
        elif path == "/api/handoff/start" and request.method == "GET":
            assert parse_qs(url.query) == {"thread": [thread]}
            reply(offer)
        elif path == "/api/handoff/start":
            posts.append(json.loads(request.post_data))
            status, body = answers.pop(0)
            reply(body, status)
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
    page.goto(f"http://example.test/app/history.html?thread={thread}&ui=deck")
    page.wait_for_selector("[data-history-chain], [data-history-retry]")
    page.evaluate("document.fonts.ready")
    return context, page, posts, errors


def shot(page, name):
    target = os.environ.get("HISTORY_SHOTS_DIR")
    if target:
        page.screenshot(path=str(Path(target) / f"{name}.png"), full_page=True)


def no_sideways_scroll(page):
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["desktop", "phone"])
def test_memory_page_has_a_named_entry_to_saved_threads(browser, chat_hub, size):
    from playwright.sync_api import expect

    context, page, origin = _signed_in_page(browser, chat_hub, size)
    try:
        page.goto(origin + "/app/?ui=deck#/memory")
        link = page.get_by_role("link", name="Saved threads")
        expect(link).to_be_visible(timeout=15000)
        assert link.get_attribute("href") == "/app/history.html"
        no_sideways_scroll(page)
        shot(page, f"memory-entry-{size[0]}")
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_chain_and_a_handoff_from_review_to_started(browser, width):
    context, page, posts, errors = open_history(
        browser, width, READY, starts=[(200, PREVIEW), (409, REFUSAL), (200, STARTED)]
    )
    try:
        chain = page.locator("[data-history-chain]")
        assert "these 2 saved threads (42 messages)" in chain.inner_text()
        assert (
            "newest first" in chain.inner_text() and "Nothing is summarised" in chain.inner_text()
        )
        assert chain.get_by_role("link", name="Read the messages").first.get_attribute("href") == (
            "/app/threads/thr_new"
        )
        no_sideways_scroll(page)
        shot(page, f"chain-{width}")
        page.get_by_label("Start the new agent on").fill("codex/gpt-6.1-sol")
        page.get_by_label("Effort (optional)").fill("high")
        page.get_by_label("Your own words for the new agent (optional, kept exactly)").fill(
            "Keep going with the tests."
        )
        page.get_by_role("button", name="Review the handoff").click()
        offer = page.locator("[data-history-offer]")
        offer.get_by_role("heading", name="Confirm the handoff").wait_for()
        text = offer.inner_text()
        for needle in (
            "codex / gpt-6.1-sol, effort high",
            "mcp:live",
            "word for word",
            "stay linked",
            "Keep going with the tests.",
            "old one keeps running",
        ):
            assert needle in text, needle
        assert posts == [
            {
                "thread": "thr_new",
                "to": "codex/gpt-6.1-sol",
                "effort": "high",
                "note": "Keep going with the tests.",
            }
        ]
        no_sideways_scroll(page)
        shot(page, f"confirm-{width}")
        page.get_by_role("button", name="Start the handoff").click()
        offer.get_by_text("nothing was lost", exact=False).wait_for()
        assert "Check that T3 is running" in offer.inner_text()
        assert "signed out" not in offer.inner_text()
        shot(page, f"failure-{width}")
        page.get_by_role("button", name="Start the handoff").click()
        offer.get_by_text("Handoff started.").wait_for()
        assert posts[-1]["confirm"] is True and len(posts) == 3
        assert offer.get_by_role("link", name="Open the saved thread").get_attribute("href") == (
            "/app/history.html?thread=thr_next"
        )
        shot(page, f"started-{width}")
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_a_person_without_launch_authority_sees_the_history_and_no_action(browser, width):
    context, page, posts, errors = open_history(browser, width, DENIED)
    try:
        assert page.get_by_role("heading", name="History this would use").is_visible()
        body = page.locator("main").inner_text()
        assert "You can still read this history" in body
        assert page.locator("main form, main textarea, [data-history-confirm]").count() == 0
        assert posts == [] and errors == []
        no_sideways_scroll(page)
        shot(page, f"denied-{width}")
    finally:
        context.close()


def test_an_unavailable_handoff_says_why_and_never_renders_empty(browser):
    offer = {
        "thread": "thr_new",
        "state": "unavailable",
        "reason": "Handoff is switched off for this work (set at workspace level).",
        "next_step": "Ask a workspace owner to turn Handoff on in Memory settings.",
        "settings_link": "/app/settings/memory",
    }
    context, page, posts, _ = open_history(browser, 390, offer)
    try:
        text = page.locator("[data-history-offer]").inner_text()
        assert "switched off" in text and "Ask a workspace owner" in text
        assert (
            page.get_by_role("link", name="Memory settings").get_attribute("href")
            == "/app/settings/memory"
        )
        assert page.locator("main form").count() == 0 and posts == []
        shot(page, "unavailable-390")
    finally:
        context.close()
