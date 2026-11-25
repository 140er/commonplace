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
                "next": "This takes a few seconds; the card updates by itself.",
                "revision": 0,
                "url": None,
                "published_at": None,
                "started_at": "2026-01-01T11:00:00Z",
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
                "reason": "contains_secret_like_text",
                "reason_text": "Nothing was published: a file looked like it holds a credential.",
                "next": "Ask the bot that published it to publish again without the "
                "credential in that file. Reply on this card.",
                "revision": 0,
                "url": None,
                "published_at": None,
                "started_at": "2026-01-01T11:00:00Z",
                "failure": {"file": "config/env.js", "kind": "github_token"},
            }
        ],
    ),
    11: ("offline", None),
    12: ("refused", None),
    13: (
        "long-name",
        [{**PREVIEW, "id": "pv_0000000000000013", "name": "a-very-long-preview-name-" * 5}],
    ),
    16: (
        "finish-deleting",
        [
            {
                **PREVIEW,
                "id": "pv_0000000000000016",
                "state": "stopped",
                "reason": "files_deleted",
                "reason_text": "The preview is stopped, but some files could not be removed.",
                "next": "The workspace owner can use Finish deleting on this card to try again.",
                "url": None,
                "files_remaining": True,
            }
        ],
    ),
    17: (
        "deleted",
        [
            {
                **PREVIEW,
                "id": "pv_0000000000000017",
                "state": "stopped",
                "reason": "files_deleted",
                "reason_text": "A workspace owner deleted the files of this preview.",
                "next": "Ask the Coworker to publish again if it is still wanted.",
                "url": None,
                "files_remaining": False,
            }
        ],
    ),
}
SIZES = {
    "desktop": {"viewport": {"width": 1280, "height": 800}},
    "phone": {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True},
    "narrow-320": {"viewport": {"width": 320, "height": 700}, "has_touch": True, "is_mobile": True},
    "tablet-768-touch": {"viewport": {"width": 768, "height": 1024}, "has_touch": True},
}
RULE = {
    "stop_days_after_pr": None,
    "max_age_days": None,
    "updated_at": None,
    "text": "Previews are served until a person or the bot that published them stops them.",
}


def listing(previews):
    return {
        "previews": previews,
        "usage": {"ready": 1, "bytes": 2048},
        "limits": {"max_ready": 25, "max_workspace_bytes": 500 * 1024 * 1024},
        "rule": RULE,
        "can_set_rule": True,
        "can_delete": True,
    }


@pytest.mark.parametrize("look", list(SIZES))
def test_every_preview_state_reads_in_words_and_opens_in_one_click(browser, look, tmp_path):  # noqa: F811
    evidence = Path(os.environ.get("CARD_PREVIEWS_SHOTS") or tmp_path)
    evidence.mkdir(parents=True, exist_ok=True)
    page = browser.new_page(reduced_motion="reduce", **SIZES[look])
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    cards = [dict(card) for card in shell.CARDS] + [
        {**shell.CARD, "number": n, "title": f"Preview card {label}", "ask": None, "need": "idle"}
        for n, (label, _) in STATES.items()
    ]
    shell.open_board(page, cards=cards)
    writes = []
    states = {
        number: (label, [dict(p) for p in previews or []] if previews is not None else None)
        for number, (label, previews) in STATES.items()
    }

    def previews_api(route):
        url = urlsplit(route.request.url)
        if url.path != "/api/previews":
            writes.append((url.path, route.request.method))
            if url.path.endswith("/stop"):
                ready = states[6][1][0]
                ready.update(
                    state="stopped",
                    reason="stopped_by_person",
                    reason_text="Stopped by a person. The files are kept.",
                    next="Use Open again to serve it.",
                )
                route.fulfill(json=ready)
                return
            route.fulfill(json={})
            return
        number = int(parse_qs(url.query)["card"][0])
        label, previews = states[number]
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
                "none": "No previews yet.",
                "building": "The files are being saved.",
                "stopped": "Use Open again to serve it.",
                "failed": "Nothing was published: a file looked like it holds a credential.",
                "offline": "Could not load the previews.",
                "long-name": "Published and open to people who can open this card.",
                "finish-deleting": "The preview is stopped, but some files could not be removed.",
                "deleted": "A workspace owner deleted the files of this preview.",
            }[label]
            section.get_by_text(words).first.wait_for()
            section.scroll_into_view_if_needed()
            for control in section.locator("button:visible, a.btn:visible").all():
                box = control.bounding_box()
                assert box and box["width"] > 0
                if look != "desktop":
                    assert box["height"] >= 44, (look, label, control.inner_text(), box)
            if look != "desktop":
                for text in section.locator("h3, p, .run-state, button").all():
                    if text.is_visible() and text.inner_text():
                        assert (
                            float(text.evaluate("e => parseFloat(getComputedStyle(e).fontSize)"))
                            >= 14
                        ), (look, label, text.inner_text())
            if label == "offline":
                assert section.inner_text().count("Could not load the previews.") == 1
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
    # Delete has a real question, named for assistive technology, before anything is sent.
    panel.locator("[data-preview-delete]").click()
    question = panel.locator(".preview-confirm p")
    confirm = panel.locator("[data-preview-delete-confirm]")
    assert confirm.get_attribute("aria-describedby") == question.get_attribute("id")
    assert "this cannot be undone" in question.inner_text()
    assert panel.locator("[data-preview-status]").inner_text() == question.inner_text()
    assert panel.locator("[data-preview-delete-cancel]").evaluate(
        "e => e === document.activeElement"
    )
    panel.screenshot(path=str(evidence / f"previews-{look}-delete-confirmation.png"))
    panel.locator("[data-preview-delete-cancel]").click()
    # The owner's rule form, open.
    rule = panel.locator("[data-preview-rule]")
    rule.locator("summary").click()
    rule.locator("input").first.wait_for()
    if look != "desktop":
        for control in rule.locator("summary, input, button").all():
            assert control.bounding_box()["height"] >= 44, control.evaluate("e => e.outerHTML")
    panel.screenshot(path=str(evidence / f"previews-{look}-rule-open.png"))
    rule.locator("summary").click()
    tab.close()
    # The keyboard reaches Stop and uses it.
    stop = panel.locator("[data-preview-stop]")
    stop.focus()
    with page.expect_request("**/api/previews/pv_0123456789abcdef/stop") as sent:
        page.keyboard.press("Enter")
    assert sent.value.method == "POST"
    # The list reloads and shows what is true now, with the result in words.
    section = panel.locator("[data-card-previews]")
    section.get_by_text("Stopped. The files are kept; use Open again to serve it.").wait_for()
    section.locator("[data-preview-reopen]").wait_for()
    # Focus moved to the control that replaced Stop, so the keyboard can go on from there.
    page.wait_for_function(
        "document.activeElement?.dataset?.previewReopen === 'pv_0123456789abcdef'"
    )
    assert section.locator('[data-state="stopped"] .run-state').inner_text() == "Stopped"
    assert ("/api/previews/pv_0123456789abcdef/stop", "POST") in writes
    assert errors == [], json.dumps(errors)


def test_focus_stays_on_the_same_control_when_the_section_follows_a_publish(browser):  # noqa: F811
    """While a preview is being published the section asks again by itself; the control a
    person is on keeps focus through that redraw."""
    page = browser.new_page(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    cards = [dict(card) for card in shell.CARDS] + [
        {
            **shell.CARD,
            "number": 14,
            "title": "Preview being published",
            "ask": None,
            "need": "idle",
        }
    ]
    shell.open_board(page, cards=cards)
    building = {
        **PREVIEW,
        "id": "pv_0000000000000014",
        "name": "docs",
        "state": "building",
        "reason": "storing",
        "reason_text": "The files are being saved.",
        "next": "This takes a few seconds; the card updates by itself.",
        "revision": 0,
        "url": None,
        "published_at": None,
        "started_at": "2026-01-01T11:00:00Z",
    }
    calls = []

    def previews_api(route):
        calls.append(route.request.url)
        later = {
            **building,
            "state": "ready",
            "reason": "published",
            "revision": 1,
            "reason_text": PREVIEW["reason_text"],
            "next": "",
            "url": "/p/pv_0000000000000014/",
            "published_at": "2026-01-01T11:01:00Z",
        }
        route.fulfill(json=listing([PREVIEW, building if len(calls) == 1 else later]))

    page.route("**/api/previews**", previews_api)
    page.evaluate("location.hash='#/card/14'")
    panel = page.locator("#card-panel")
    opener = panel.locator('[data-preview-open="pv_0123456789abcdef"]')
    opener.wait_for()
    opener.focus()
    # The section asks again by itself and redraws with the second preview ready.
    panel.locator('[data-preview="pv_0000000000000014"][data-state="ready"]').wait_for(
        timeout=15000
    )
    assert page.evaluate("document.activeElement?.dataset?.previewOpen") == "pv_0123456789abcdef"


def test_the_stop_rule_form_keeps_its_state_through_a_panel_redraw(browser):  # noqa: F811
    """The owner opens the rule and types; a feed event makes the shell refresh the card in the
    background. The form stays open with what was typed and keeps focus (while it is in use the
    shell leaves the panel alone); an open form that is not being used survives a real redraw."""
    page = browser.new_page(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    cards = [dict(card) for card in shell.CARDS] + [
        {**shell.CARD, "number": 15, "title": "Rule card", "ask": None, "need": "idle"}
    ]
    shell.open_board(page, cards=cards)
    page.route("**/api/previews**", lambda route: route.fulfill(json=listing([PREVIEW])))
    delivered = {"count": 0}

    def feed(route):
        # One event, once; then quiet again.
        if delivered["count"] == 0:
            delivered["count"] = 1
            route.fulfill(
                json={"events": [{"kind": "comment", "seq": 1}], "seq": 1, "epoch": "fixture"}
            )
        else:
            route.fulfill(json={"events": [], "seq": 1, "epoch": "fixture"})

    page.evaluate("location.hash='#/card/15'")
    panel = page.locator("#card-panel")
    rule = panel.locator("[data-preview-rule]")
    rule.locator("summary").click()
    field = rule.locator('input[name="max_age_days"]')
    field.fill("30")
    field.focus()
    section = panel.locator("[data-card-previews]")
    section.evaluate("e => { e.dataset.mark = 'same-element' }")
    # The feed event arrives while the form is in use; wait for the shell to have handled it.
    page.route("**/api/events**", feed)
    with page.expect_request(lambda r: "/api/events" in r.url and "after=1" in r.url):
        pass
    assert page.evaluate("document.querySelector('[data-preview-rule]').open") is True
    assert field.input_value() == "30"
    assert page.evaluate("document.activeElement?.name") == "max_age_days"
    # Nothing replaced the section: the form in use protected the panel.
    assert section.evaluate("e => e.dataset.mark") == "same-element"
    # Open but unused (focus elsewhere, nothing typed): a real redraw still keeps it open.
    field.fill("")
    panel.locator("[data-preview-open]").focus()
    panel.locator(".workbench-details").evaluate("e => {e.dataset.draw = 'before-refresh'}")
    delivered["count"] = 0
    with page.expect_request(lambda r: "/api/cards/15" in r.url):
        pass
    # The card redraws, while the actual preview section (and its status node) is retained.
    page.wait_for_function(
        "document.querySelector('.workbench-details')?.dataset.draw !== 'before-refresh'"
    )
    assert section.evaluate("e => e.dataset.mark") == "same-element"
    assert page.evaluate("document.querySelector('[data-preview-rule]')?.open") is True


def test_a_pending_delete_keeps_its_result_through_a_real_card_redraw(browser):  # noqa: F811
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    shell.open_board(page)
    page.evaluate(
        """async ({card, found}) => {
      const {createDetails} = await import('/app/details.js');
      const mount = document.createElement('div');
      mount.id = 'preview-redraw-fixture'; document.body.append(mount);
      let deleted = false;
      const api = async (url, options) => {
        if (options?.method === 'DELETE') return await new Promise(resolve => {
          window.finishPreviewDelete = () => {deleted = true; resolve({files_remaining:true});};
        });
        if (url.startsWith('/api/previews?')) return {...found, previews: found.previews.map(p => deleted
          ? {...p, state:'stopped', reason:'files_deleted', url:null, files_remaining:true}
          : p)};
        return {};
      };
      const details = createDetails({mount, api});
      await details.show(card);
      window.redrawPreviewCard = () => details.show(card);
    }""",
        {"card": {**shell.CARD, "number": 25, "board": "work"}, "found": listing([PREVIEW])},
    )
    section = page.locator("#preview-redraw-fixture [data-card-previews]")
    section.locator("[data-preview-delete]").click()
    section.locator("[data-preview-delete-confirm]").click()
    section.locator("[data-preview-status]", has_text="Deleting the files…").wait_for()
    section.evaluate("e => {window.keptPreviewStatus = e.querySelector('[data-preview-status]')}")
    page.evaluate("redrawPreviewCard()")
    assert section.locator("[data-preview-status]").evaluate("e => e === window.keptPreviewStatus")
    page.evaluate("finishPreviewDelete()")
    section.locator("[data-preview-status]", has_text="Some files could not be removed").wait_for()
    section.get_by_role("button", name="Finish deleting files for preview home").wait_for()
    assert section.locator("[data-preview-delete]").evaluate("e => e === document.activeElement")
    page.close()


def test_invalid_rule_days_name_the_field_and_keep_the_value(browser):  # noqa: F811
    page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
    shell.open_board(page)
    writes = []

    def previews_api(route):
        if route.request.method != "GET":
            writes.append(route.request.method)
        route.fulfill(json=listing([PREVIEW]))

    page.route("**/api/previews**", previews_api)
    page.evaluate("location.hash='#/card/1'")
    section = page.locator("[data-card-previews]")
    section.locator("summary").click()
    field = section.locator('input[name="stop_days_after_pr"]')
    field.fill("366")
    section.get_by_role("button", name="Save rule").click()
    assert field.input_value() == "366" and field.get_attribute("aria-invalid") == "true"
    error = section.locator('[data-preview-field-error="stop_days_after_pr"]')
    assert error.is_visible() and "1 to 365" in error.inner_text()
    assert error.get_attribute("id") in field.get_attribute("aria-describedby").split()
    assert field.evaluate("e => e === document.activeElement")
    assert writes == []
    page.close()
