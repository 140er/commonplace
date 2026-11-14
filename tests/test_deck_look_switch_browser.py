"""Look switch journeys in a real browser (optional: skips where no browser is available)."""

import pytest

from tests.test_deck_settings_browser import browser, open_settings  # noqa: F401

AGENT = {
    "id": "c1",
    "name": "my-agent",
    "auth": "pat",
    "client": "codex",
    "read_wings": [],
    "revoked_at": None,
    "expires_at": None,
    "last_used_at": None,
}
LOOK = '[data-deck-action="look"]'


def watch(page):
    errors, renders = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda r: renders.append(1) if r.url.endswith("/api/plan") else None)
    page.evaluate("window.__beat = 0; setInterval(() => window.__beat++, 50)")
    return errors, renders


def settle(page, look):
    """Wait for the redrawn Settings page, then prove the page is alive and the screen is unchanged."""
    page.wait_for_selector(f'{LOOK}[data-look="{look}"][aria-pressed="true"]')
    before = page.evaluate("window.__beat")
    page.wait_for_timeout(600)
    assert page.evaluate("window.__beat") - before >= 5, "the page froze after switching"
    assert page.evaluate("location.hash") == "#/settings"
    assert page.locator(".head h1").inner_text() == "Settings"
    stored = page.evaluate("localStorage.getItem('cmmn.ui')")
    assert stored == look
    assert page.evaluate("document.documentElement.dataset.ui || ''") == (
        "deck" if look == "deck" else ""
    )
    # No canvas may be larger than the window (the runaway resize loop).
    assert page.evaluate(
        "[...document.querySelectorAll('canvas')].every(c => c.width <= innerWidth * 2 + 2)"
    )


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("start", ["deck", "classic"])
def test_look_is_at_the_top_and_switches_both_ways_on_the_same_screen(browser, width, start):  # noqa: F811
    context, page = open_settings(browser, width, look=start, connections=[AGENT])
    try:
        look = page.locator("#look")
        assert look.is_visible()
        top = look.bounding_box()["y"]
        assert top < (844 if width == 390 else 800) / 2, "Look is on the first screen of Settings"
        for later in ["#phone-push", '[data-form="agent"]', '.settings .opt[data-action="logout"]']:
            assert top < page.locator(later).bounding_box()["y"], later
        assert page.get_by_role("group", name="Look").get_by_role("button").all_inner_texts() == [
            "Deck",
            "Classic",
        ]
        errors, reads = watch(page)
        other = "classic" if start == "deck" else "deck"
        for look_name in (other, start, other):
            reads.clear()
            page.locator(f'{LOOK}[data-look="{look_name}"]').click()
            settle(page, look_name)
            assert len(reads) == 1, "one redraw per switch"
            assert page.locator("#look").bounding_box()["y"] < top + 200, "still at the top"
        page.locator(f'{LOOK}[data-look="{other}"]').click()
        page.wait_for_timeout(300)
        assert len(reads) == 1, "choosing the look already shown does nothing"
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_rotate_and_revoke_are_collapsed_away_from_everyday_settings(browser, width):  # noqa: F811
    context, page = open_settings(browser, width, connections=[AGENT])
    try:
        rotate = page.locator('[data-action="agent-rotate"]')
        group = page.locator("details.agent-tokens")
        assert group.count() == 1
        assert not rotate.is_visible(), "closed until asked for"
        assert page.locator("details.agent-tokens > summary").inner_text() == "Access tokens"
        group.locator("summary").click()
        assert rotate.is_visible() and page.locator('[data-action="agent-revoke"]').is_visible()
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
    finally:
        context.close()
