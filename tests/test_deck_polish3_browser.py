"""Optional real-shell proof of the third Deck polish slice: matching bot initials in the sidebar and the
Bots list, and a gap between the phone notification buttons. Fixture-served, so no hub or timing."""

import json
import mimetypes
from urllib.parse import urlsplit

import pytest

from tests.test_deck_board_browser import BOARD, STATIC, browser  # noqa: F401
from tests.test_deck_settings_browser import ACTIONS

ME = {
    "actor": "user:owner",
    "name": "Owner",
    "workspace": "demo",
    "can_admin": True,
    "role": "owner",
    "workspace_name": "Example",
    "people": {},
}
BOTS = [
    {"id": "commonplace", "name": "commonplace", "board": "product", "kind": "project"},
    {"id": "coworker", "name": "Coworker", "board": "product", "kind": "project"},
    {"id": "zed", "name": "Zed", "board": "other", "kind": "project"},
]
SIZES = [(1280, 800), (390, 844)]


def open_page(page, hash_, extra):
    responses = {
        "/api/me": ME,
        "/api/boards": {"boards": [BOARD]},
        "/api/needs-me": {"count": 0},
        "/api/bots": {"bots": BOTS},
        "/api/rules/count": {"waiting": 0},
        "/api/events": {"events": [], "seq": 0, "epoch": "fixture"},
        **extra,
    }

    def serve(route):
        path = urlsplit(route.request.url).path
        if path.startswith("/api/"):
            route.fulfill(body=json.dumps(responses.get(path, {})), content_type="application/json")
            return
        file = STATIC / (path.removeprefix("/app/") or "index.html")
        if file.is_file():
            route.fulfill(
                body=file.read_bytes(),
                content_type=mimetypes.guess_type(file.name)[0] or "application/octet-stream",
            )
        else:
            route.fulfill(status=404, body="")

    page.route("**/*", serve)
    page.set_default_timeout(15000)
    page.goto(f"http://example.test/app/?ui=deck{hash_}")


@pytest.mark.parametrize("size", SIZES, ids=["desktop", "phone"])
def test_bots_list_shows_the_same_initials_as_the_sidebar(browser, size):  # noqa: F811
    page = browser.new_page(viewport={"width": size[0], "height": size[1]}, reduced_motion="reduce")
    open_page(page, "#/bots", {})
    page.wait_for_selector(".cb-bot-row .cb-avatar")
    page.evaluate("document.fonts.ready")
    page.wait_for_function("document.querySelectorAll('.cb-bot-row .cb-avatar').length === 3")
    listed = page.eval_on_selector_all(
        ".cb-bot-row",
        "rows => rows.map(r => [r.querySelector('b').textContent, r.querySelector('.cb-avatar').textContent])",
    )
    sidebar = page.eval_on_selector_all(
        "nav [class*=avatar]", "els => els.map(el => el.textContent.trim())"
    )
    labels = dict(listed)
    assert labels == {"commonplace": "CO", "Coworker": "CW", "Zed": "Z"}, labels
    assert sidebar == ["CO", "CW", "Z"], sidebar


@pytest.mark.parametrize("size", SIZES, ids=["desktop", "phone"])
def test_phone_notification_buttons_are_not_run_together(browser, size):  # noqa: F811
    page = browser.new_page(viewport={"width": size[0], "height": size[1]}, reduced_motion="reduce")
    extra = {
        "/api/voice": {"available": True, "enabled": False, "owner": True},
        "/api/connections": {"connections": [], "wings": []},
        "/api/webhooks": {"webhooks": []},
        "/api/plan": {
            "enabled": True,
            "plan": "free",
            "storage": {"used": 0, "limit": 1048576},
            "transcription": {"used_today": 0, "limit": 60},
            "sync": {"interval_seconds": 60},
        },
        "/api/phone-push": {
            "available": True,
            "eligible": True,
            "subscription": {"server": "https://ntfy.example.test", "topic": "abc"},
        },
        "/api/chat-settings": {
            "front": {"enabled": False},
            "usage": {"used_today": 0, "user_used_today": 0},
        },
        "/api/settings/orchestration": {"enabled": True},
        "/api/tool-policy": {
            "actions": ACTIONS,
            "modes": ["allowed", "ask", "off"],
            "approval": {"approval_ttl_s": 600},
        },
        "/api/agent-schedules": {
            "schedules": [],
            "limits": {"max_prompt_chars": 4000, "min_interval_minutes": 5, "max_schedules": 10},
        },
        "/api/bots/settings": {"sharing": False, "can_configure": True},
    }
    open_page(page, "#/settings", extra)
    page.wait_for_selector("#phone-push [data-action=phone-push-test]", state="attached")
    page.evaluate("document.fonts.ready")
    gap = page.evaluate(
        """() => {
          const test = document.querySelector('[data-action=phone-push-test]').getBoundingClientRect();
          const off = document.querySelector('[data-action=phone-push-off]').getBoundingClientRect();
          return Math.abs(test.top - off.top) < 2 ? off.left - test.right : null;
        }"""
    )
    assert gap is None or gap >= 12, f"Test and Turn off touch: {gap}px apart"
