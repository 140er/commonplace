"""Optional real-shell proof of the Deck polish items: Waiting on you wording and confirmation, phone chrome,
the Add card sheet and the Escape order. Fixture-served, so no hub and no timing is involved."""

import json
import mimetypes
from urllib.parse import urlsplit

import pytest

from tests.test_deck_board_browser import (  # noqa: F401
    BOARD,
    BOT,
    CARDS,
    STATIC,
    browser,
    open_board,
)

ASK = {
    "number": 21,
    "version": 2,
    "board": "product",
    "status": "review",
    "title": "Which colour for the badge?",
    "body": "Pick one so the work can go on.",
    "actions": ["answer"],
    "ask_options": ["Blue", "Orange"],
    "updated_at": "2026-01-01T12:00:00Z",
}
OPEN_ASK = {
    "number": 23,
    "version": 1,
    "board": "product",
    "status": "review",
    "title": "What should the release note say?",
    "body": "Write it in your own words.",
    "actions": ["answer"],
    "updated_at": "2026-01-01T12:00:00Z",
}
APPROVAL = {
    "number": 22,
    "version": 5,
    "board": "product",
    "status": "review",
    "title": "Ship the consent change",
    "body": "Ready for your yes.",
    "actions": ["approve", "reject"],
    "updated_at": "2026-01-01T12:00:00Z",
}


def open_today(page):
    responses = {
        "/api/me": {
            "actor": "user:owner",
            "name": "Owner",
            "workspace": "demo",
            "can_admin": True,
            "role": "owner",
        },
        "/api/boards": {"boards": [BOARD]},
        "/api/needs-me": {"count": 3, "asks": [ASK, OPEN_ASK], "approvals": [APPROVAL]},
        "/api/bots": {"bots": [BOT]},
        "/api/rules/count": {"waiting": 0},
        "/api/events": {"events": [], "seq": 0, "epoch": "fixture"},
    }
    writes = []

    def serve(route):
        path = urlsplit(route.request.url).path
        if path.startswith("/api/"):
            if route.request.method not in ("GET", "HEAD"):
                writes.append((path, route.request.post_data_json))
            route.fulfill(body=json.dumps(responses.get(path, {})), content_type="application/json")
        else:
            file = STATIC / (path.removeprefix("/app/") or "index.html")
            if file.is_file():
                route.fulfill(
                    body=file.read_bytes(),
                    content_type=mimetypes.guess_type(file.name)[0] or "application/octet-stream",
                )
            else:
                route.fulfill(status=404, body="")

    page.route("**/*", serve)
    # The served origin is not a secure context, which has no randomUUID.
    page.add_init_script("crypto.randomUUID ||= () => 'k' + Math.random().toString(16).slice(2)")
    page.goto("http://example.test/app/?ui=deck#/")
    page.wait_for_selector('[data-bt-card="21"]')
    page.evaluate("document.fonts.ready")
    return writes


@pytest.mark.parametrize("size", [(1280, 800), (390, 844)], ids=["desktop", "phone"])
def test_today_answer_is_named_for_what_the_person_does(browser, size):  # noqa: F811
    page = browser.new_page(viewport={"width": size[0], "height": size[1]}, reduced_motion="reduce")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        writes = open_today(page)
        # A question without offered answers is answered in words: its main button, dialog and send are all "Answer".
        card = page.locator('[data-bt-card="23"]')
        assert card.locator(".bt-primary").inner_text().strip() == "Answer"
        card.locator(".bt-primary").click()
        dialog = page.locator("dialog.bt-reply[open]")
        assert dialog.locator("h2").inner_text() == "Answer"
        assert dialog.locator('[type="submit"]').inner_text() == "Send answer"
        assert (
            dialog.locator("textarea")
            .evaluate("e => e.closest('label').innerText")
            .startswith("Your answer")
        )
        dialog.locator("textarea").fill("Consent is asked first.")
        dialog.locator('[type="submit"]').click()
        confirm = page.locator('[data-bt="confirm"]')
        confirm.wait_for(state="visible")
        text = confirm.inner_text()
        assert "Answer sent to Shipping" in text and "Board" in text, text
        assert writes and writes[0][0] == "/api/cards/23/decide", writes
        # Every answer the bot offered is its own button, and one click sends it.
        offered = page.locator('[data-bt-card="21"] .bt-actions button[data-bt-choice]')
        assert offered.all_inner_texts() == ["Blue", "Orange"]
        offered.nth(1).click()
        page.wait_for_function(
            "document.querySelector('[data-bt=confirm]').textContent.includes('Shipping')"
        )
        assert writes[1][0] == "/api/cards/21/decide", writes
        assert writes[1][1]["choice"] == "Orange", writes
        assert "Answer sent to Shipping" in page.locator('[data-bt="confirm"]').inner_text()
        assert errors == []
    finally:
        page.close()


def test_today_reply_keeps_its_own_name(browser):  # noqa: F811
    page = browser.new_page(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    try:
        open_today(page)
        page.locator('[data-bt-card="21"] .bt-desktop-reply').click()
        dialog = page.locator("dialog.bt-reply[open]")
        assert dialog.locator("h2").inner_text() == "Reply"
        assert dialog.locator('[type="submit"]').inner_text() == "Send reply"
    finally:
        page.close()


def test_today_approve_says_what_it_does(browser):  # noqa: F811
    page = browser.new_page(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    try:
        open_today(page)
        note = page.locator('[data-bt-card="22"] .bt-approve-note')
        assert "Approve means yes" in note.inner_text()
        assert page.locator('[data-bt-card="21"] .bt-approve-note').count() == 0
    finally:
        page.close()


MEASURE_CHROME = """() => {
  const box = e => { const b = e.getBoundingClientRect(); return {l: b.left, t: b.top, r: b.right, b: b.bottom}; };
  const bar = box(document.querySelector('.m-tabs'));
  const tabs = [...document.querySelectorAll('.m-tabs > a')].map(e => ({...box(e), label: e.textContent.trim()}));
  const apart = tabs.every((t, i) => i === 0 || t.l >= tabs[i - 1].r - 0.5);
  const inside = tabs.every(t => t.l >= 0 && t.r <= innerWidth && t.t >= bar.t && t.b <= bar.b && t.b <= innerHeight);
  // Settings is one way in on the phone: the header's More button, which opens the sheet that lists it first.
  const opens = [...document.querySelectorAll('.m-top, .m-tabs')].flatMap(host =>
    [...host.querySelectorAll('button, a')].filter(e => /settings/i.test(e.getAttribute('aria-label') || e.textContent) && e.getClientRects().length));
  return { labels: tabs.map(t => t.label.replace(/\\d.*$/, '')), apart, inside, minHeight: Math.min(...tabs.map(t => t.b - t.t)),
    mic: document.querySelectorAll('.m-tabs [data-deck-shell-action="mic"], .m-tabs .deck-tab-more').length, gears: opens.length };
}"""


@pytest.mark.parametrize("height", [667, 844])
def test_phone_tab_bar_is_clear_and_settings_has_one_way_in(browser, height):  # noqa: F811
    page = browser.new_page(viewport={"width": 390, "height": height}, reduced_motion="reduce")
    try:
        open_board(page)
        measured = page.evaluate(MEASURE_CHROME)
        assert measured["labels"] == ["Waiting", "Bots", "Work", "Memory"], measured
        assert measured["apart"] and measured["inside"], measured
        assert measured["minHeight"] >= 44, measured
        assert measured["mic"] == 0, "no mic or More tab sits in the bar"
        assert measured["gears"] == 1, measured
        page.locator('[data-deck-shell-action="more"]').click()
        sheet = page.locator("#deck-more")
        sheet.wait_for(state="visible")
        links = sheet.locator('a[href="#/settings"]')
        assert (
            links.count() == 1
            and sheet.locator("a, button").first.inner_text().strip() == "Settings"
        )
    finally:
        page.close()


def add_card_sheet(page):
    open_board(page)
    page.locator(".deck-board-add:visible").first.click()
    page.wait_for_selector("form[data-board-add] .composer-hint")
    return page.locator("form[data-board-add]")


def test_phone_add_card_sheet_is_opaque_and_has_no_key_hint(browser):  # noqa: F811
    page = browser.new_page(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    try:
        sheet = add_card_sheet(page)
        assert sheet.get_attribute("aria-modal") == "true"
        background = sheet.evaluate("e => getComputedStyle(e).backgroundColor")
        assert background.startswith("rgb(") and not background.startswith("rgba("), background
        assert not sheet.get_by_text("Enter adds").is_visible()
    finally:
        page.close()


def test_coarse_pointer_hides_the_key_hint_and_a_keyboard_keeps_it(browser):  # noqa: F811
    touch = browser.new_context(viewport={"width": 1024, "height": 800}, has_touch=True)
    page = touch.new_page()
    try:
        sheet = add_card_sheet(page)
        assert page.evaluate("matchMedia('(pointer: coarse)').matches")
        assert not sheet.get_by_text("Enter adds").is_visible()
    finally:
        touch.close()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        sheet = add_card_sheet(page)
        assert sheet.get_by_text("Enter adds · Esc cancels").is_visible()
    finally:
        page.close()


def test_escape_closes_the_move_menu_first_then_the_card_panel(browser):  # noqa: F811
    page = browser.new_page(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    try:
        open_board(page)
        page.locator('.card[data-card="3"]').click()
        page.wait_for_selector("#card-panel .deck-card-actions")
        move = page.locator("#card-panel").get_by_role("button", name="Move to…")
        move.click()
        page.wait_for_selector("dialog.card-menu[open]")
        page.keyboard.press("Escape")
        page.wait_for_selector("dialog.card-menu", state="detached")
        assert page.locator("#card-panel:not([hidden])").count() == 1, "the panel stays open"
        assert page.evaluate("document.activeElement.dataset.action") == "card-menu"
        page.keyboard.press("Escape")
        page.wait_for_selector("#card-panel[hidden]", state="attached")
    finally:
        page.close()
