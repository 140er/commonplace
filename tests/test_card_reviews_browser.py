"""The card panel's Review comments section and the Pipeline chip in a real browser.

The shell runs on its static files with a fixed API (the deck board fixture), so no server state
is needed. Every state is drawn for the owner and for a member, in the Deck and the Classic look,
at desktop (1280x800), phone (390), narrow (320) and touch tablet (768) sizes; set
CARD_REVIEWS_SHOTS to a directory to keep a screenshot of each. The owner's answer flow is run
end to end, with focus checked after the action and after a background reload.
"""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests import test_deck_board_browser as shell
from tests._console_helpers import console_hub_fixture  # noqa: F401
from tests.test_deck_board_browser import browser  # noqa: F401
from tests.test_pr_review import card as review_card

SHA = "b" * 40
ANSWERS = [
    {
        "choice": "again",
        "label": "Two more rounds",
        "explain": "The Coworker gets two more rounds on these comments and on any that still wait.",
    },
    {
        "choice": "mine",
        "label": "I will do it myself",
        "explain": "The Coworker stops answering this pull request's comments. The card moves to "
        "Backlog, so no Coworker picks it up while you work on them.",
    },
    {
        "choice": "dismiss",
        "label": "Ignore",
        "explain": "The waiting comments are set aside and nobody acts on them. The card goes "
        "back to its Coworker.",
    },
]


def comment(n, status, status_text, **extra):
    return {
        "id": n,
        "kind": "inline",
        "gh_id": str(100 + n),
        "path": "commonplace/web/static/a-rather-long-module-name-for-wrapping.js",
        "line": 12,
        "author": "reviewer-with-a-long-name",
        "text": "Please rename this variable so it says what it holds; the old name hides the unit.",
        "text_redacted": False,
        "created_at": "2026-01-01T11:00:00Z",
        "rev": 1,
        "status": status,
        "status_text": status_text,
        "round": 1,
        "fix_commit": None,
        "fix_confirmed": False,
        "reply": None,
        "reply_posted": False,
        "note": None,
        "reason": None,
        "reason_text": "",
        "next": "",
        **extra,
    }


EVERY = [
    comment(1, "new", "New"),
    comment(2, "working", "The Coworker is working on it"),
    comment(3, "addressed", "Fixed", fix_commit=SHA, fix_confirmed=True, reply="Renamed it."),
    comment(
        4,
        "answered",
        "Answered",
        kind="conversation",
        path=None,
        line=None,
        reply="It is read by two functions, so the name stays.",
    ),
    comment(
        5,
        "needs_you",
        "Needs you",
        reason="coworker_asks",
        reason_text="The Coworker needs a decision it cannot make.",
        next="Read its note and decide.",
        note="Choosing another language is a product decision.",
    ),
    comment(
        6,
        "skipped",
        "Not for the Coworker",
        reason="author_not_trusted",
        reason_text="Written by someone the workspace does not act on.",
        next="Add them as a trusted reviewer, or ignore.",
    ),
]


def view(comments, *, owner=True, enabled=True, info=True):
    counts = {key: 0 for key in ("new", "working", "addressed", "answered", "needs_you", "skipped")}
    for item in comments:
        counts[item["status"]] += 1
    return {
        "card": 0,
        "board": "product",
        "repo": "acme/widgets",
        "pr": 7,
        "loop": {"enabled": enabled, "rounds": 2},
        "can_decide": owner,
        "answers": ANSWERS if owner else [],
        "pr_info": {
            "head": SHA,
            "state": "open",
            "fetched_at": "2026-01-01T11:05:00Z",
            "rounds_used": 1,
            "rounds_allowed": 2,
            "loop_stopped": False,
            "public": True,
            "reply_mode": "show",
        }
        if info
        else None,
        "counts": counts,
        "comments": comments,
        "comments_total": len(comments),
        "has_more": False,
        "history": [],
    }


STATES = {
    6: "every-status",
    7: "needs-you",
    8: "empty",
    9: "off",
    10: "offline",
    11: "refused",
    12: "loading",
}
SIZES = {
    "desktop": {"viewport": {"width": 1280, "height": 800}},
    "phone": {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True},
    "narrow-320": {"viewport": {"width": 320, "height": 700}, "has_touch": True, "is_mobile": True},
    "tablet-768-touch": {"viewport": {"width": 768, "height": 1024}, "has_touch": True},
}
WORDS = {
    "loading": "Loading review comments…",
    "every-status": "6 comments:",
    "needs-you": "1 comment needs you.",
    "empty": "No review comments yet.",
    "off": "The Coworker does not answer review comments in this project yet.",
    "offline": "Could not load the review comments.",
}


def answer_for(label, owner):
    if label == "every-status":
        return view(EVERY, owner=owner)
    if label == "needs-you":
        return view([EVERY[4], EVERY[2]], owner=owner)
    if label == "empty":
        return view([], owner=owner)
    if label == "off":
        return view([], owner=owner, enabled=False, info=False)
    return None


def as_member(page):
    """Reload the shell as a workspace member (the owner fixture answers /api/me otherwise)."""
    page.route(
        "**/api/me",
        lambda route: route.fulfill(
            body=json.dumps(
                {
                    "actor": "user:member",
                    "name": "Member",
                    "workspace": "demo",
                    "can_admin": False,
                    "people": {},
                    "role": "member",
                }
            ),
            content_type="application/json",
        ),
    )
    page.reload()
    page.wait_for_selector('.card[data-card="1"]', state="attached")


def open_shell(browser, look, size, cards, *, member=False):  # noqa: F811
    page = browser.new_page(reduced_motion="reduce", **SIZES[size])
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    writes = shell.open_board(page, look=look, cards=cards)
    if member:
        as_member(page)
    return page, errors, writes


def cards():
    # The shell fixture waits for card 1 to be visible before a review card opens.
    # Classic phones start on Todo, so keep that unrelated anchor in the default column.
    return [
        {**card, **({"status": "todo"} if card["number"] == 1 else {})} for card in shell.CARDS
    ] + [
        {
            **shell.CARD,
            "number": n,
            "title": f"Review card {label}",
            "ask": None,
            "need": "idle",
            "status": "doing",
            "pr_url": "https://github.com/acme/widgets/pull/7",
            "pr_number": 7,
        }
        for n, label in STATES.items()
    ]


def assert_phone_text_is_readable(section, size):
    if size not in ("phone", "narrow-320"):
        return
    small = section.evaluate(
        """root => {
            const selector = 'p, button, a, label, h2, h3, b, span, code, time, select';
            const elements = [...(root.matches(selector) ? [root] : []), ...root.querySelectorAll(selector)];
            return elements.filter(e => e.getClientRects().length && e.textContent.trim()
                && parseFloat(getComputedStyle(e).fontSize) < 14).map(e => ({
                    element: e.outerHTML.slice(0, 150), font: getComputedStyle(e).fontSize
                }));
        }"""
    )
    assert small == [], (size, small)


@pytest.mark.parametrize("look", ["deck", "classic"])
@pytest.mark.parametrize("role", ["owner", "member"])
@pytest.mark.parametrize("size", list(SIZES))
def test_every_review_state_reads_in_words_for_owner_and_member(
    browser,  # noqa: F811
    look,
    role,
    size,
    tmp_path,
):
    evidence = Path(os.environ.get("CARD_REVIEWS_SHOTS") or tmp_path)
    evidence.mkdir(parents=True, exist_ok=True)
    owner = role == "owner"
    page, errors, _ = open_shell(browser, look, size, cards(), member=not owner)
    loading = []

    def reviews_api(route):
        path = urlsplit(route.request.url).path
        number = int(path.split("/")[4])
        label = STATES[number]
        if label == "loading":
            loading.append(route)
        elif label == "offline":
            route.abort()
        elif label == "refused":
            route.fulfill(status=404, json={"error": "Card not found", "reason": "not_found"})
        else:
            route.fulfill(json=answer_for(label, owner))

    page.route("**/api/pr-review/cards/**", reviews_api)
    panel = page.locator("#card-panel")
    for number, label in STATES.items():
        page.evaluate(f"location.hash='#/card/{number}'")
        page.wait_for_selector(
            f'#card-panel .workbench-details[data-card="{number}"]:not([aria-busy])'
        )
        section = panel.locator("[data-card-reviews]")
        if label == "refused":
            section.wait_for(state="hidden")
        else:
            section.locator("h3", has_text="Review comments").wait_for()
            section.get_by_text(WORDS[label]).first.wait_for()
            if label in ("every-status", "needs-you"):
                answers = section.locator("[data-review-answer]")
                assert answers.count() == (3 if owner else 0), (role, label)
                if not owner:
                    section.get_by_text("A workspace owner chooses what happens next.").wait_for()
            if label == "off":
                assert section.locator("[data-review-settings]").count() == (1 if owner else 0)
            section.scroll_into_view_if_needed()
            for control in section.locator("button:visible, a:visible").all():
                box = control.bounding_box()
                assert box and box["width"] > 0
                if size != "desktop":
                    assert box["height"] >= 44 and box["width"] >= 44, (
                        size,
                        label,
                        control.inner_text(),
                        box,
                    )
            assert_phone_text_is_readable(section, size)
            if size in ("phone", "narrow-320"):
                font = section.locator(".run-reason, .run-empty").first.evaluate(
                    "e => getComputedStyle(e).fontSize"
                )
                assert font == "14px", font
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), (
            size,
            label,
        )
        panel.screenshot(path=str(evidence / f"reviews-{look}-{role}-{size}-{label}.png"))
        if label == "loading":
            loading.pop().fulfill(json=view([], owner=owner))
            section.get_by_text(WORDS["empty"]).first.wait_for()
    assert not errors, errors
    page.close()


@pytest.mark.parametrize("role", ["owner", "member"])
@pytest.mark.parametrize("size", ["phone", "narrow-320"])
def test_long_classic_review_keeps_header_and_composer_in_place(browser, role, size):  # noqa: F811
    page, errors, _ = open_shell(browser, "classic", size, cards(), member=role == "member")
    page.route(
        "**/api/pr-review/cards/**",
        lambda route: route.fulfill(json=answer_for("needs-you", role == "owner")),
    )
    page.evaluate("location.hash='#/card/7'")
    section = page.locator("#card-panel [data-card-reviews]")
    section.get_by_text("1 comment needs you.").wait_for()
    for target in (section.locator("h3"), section.locator(".run-list > li").last):
        target.scroll_into_view_if_needed()
        geometry = page.evaluate(
            """() => Object.fromEntries(
                ['#card-panel', '.ctx-head', '.ctx-body', '.ctx-foot', '.workbench-details']
                .map(selector => {
                    const e = document.querySelector(selector), r = e.getBoundingClientRect();
                    return [selector, {top: r.top, bottom: r.bottom, scrollTop: e.scrollTop,
                        overflow: getComputedStyle(e).overflowY}];
                }))"""
        )
        assert geometry[".ctx-body"]["overflow"] == "auto", geometry
        assert geometry[".workbench-details"]["scrollTop"] == 0, geometry
        assert geometry[".ctx-head"]["top"] >= geometry["#card-panel"]["top"], geometry
        assert geometry[".ctx-foot"]["top"] >= geometry[".ctx-body"]["bottom"] - 1, geometry
        assert abs(geometry[".ctx-foot"]["bottom"] - geometry["#card-panel"]["bottom"]) <= 1, (
            geometry
        )
    assert not errors, errors
    page.close()


def test_the_owner_answers_and_focus_holds_through_the_answer_and_a_background_reload(browser):  # noqa: F811
    page, errors, _ = open_shell(browser, "deck", "desktop", cards())
    answered = {"done": False}
    posts = []

    def reviews_api(route):
        if route.request.method == "POST":
            posts.append(route.request.post_data_json)
            answered["done"] = True
            route.fulfill(json={"choice": "again", "card_answered": True, "counts": {}})
            return
        held = {**EVERY[4]}
        if answered["done"]:
            held = {
                **held,
                "status": "new",
                "status_text": "New",
                "reason": None,
                "reason_text": "",
                "next": "",
            }
        # A working comment keeps the section reloading by itself every few seconds.
        route.fulfill(json=view([held, EVERY[1]]))

    page.route("**/api/pr-review/cards/**", reviews_api)
    page.evaluate("location.hash='#/card/7'")
    section = page.locator("#card-panel [data-card-reviews]")
    section.locator("[data-review-answer]").first.wait_for()

    # A background reload keeps focus on the control that had it.
    ignore = section.locator('[data-review-answer="dismiss"]')
    ignore.focus()
    with page.expect_request(
        lambda request: "/api/pr-review/cards/7" in request.url, timeout=15000
    ):
        pass
    page.wait_for_function(
        "document.activeElement && document.activeElement.getAttribute('data-review-answer') === 'dismiss'"
    )

    # The answer: said once in the status line, and focus moves to the heading (the buttons go).
    section.locator('[data-review-answer="again"]').click()
    section.locator("[data-review-status]").get_by_text(
        "Done. The comments go back to the Coworker for another round."
    ).wait_for()
    assert posts == [{"choice": "again"}]
    page.wait_for_function(
        "document.activeElement && document.activeElement.getAttribute('data-review-heading') === 'reviews'"
    )
    assert section.locator("[data-review-answer]").count() == 0
    assert not errors, errors
    page.close()


@pytest.mark.parametrize("look", ["deck", "classic"])
@pytest.mark.parametrize("size", list(SIZES))
def test_the_settings_link_opens_real_project_settings_and_survives_reload(
    browser,  # noqa: F811
    console_hub,
    look,
    size,
    tmp_path,
):
    hub = console_hub
    number = review_card(hub, hold=True)
    origin = f"http://127.0.0.1:{hub.port}"
    context = browser.new_context(reduced_motion="reduce", **SIZES[size])
    name, value = hub.owner["Cookie"].split("=", 1)
    context.add_cookies([{"name": name, "value": value, "url": origin}])
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{origin}/app/?ui={look}#/card/{number}")
    link = page.locator("#card-panel [data-review-settings]")
    link.wait_for()
    assert link.get_attribute("href") == "#/board/work/settings"
    link.click()
    section = page.locator("[data-project-reviews-section]")
    page.wait_for_load_state("networkidle")
    assert section.is_visible()
    assert page.url.endswith("#/board/work/settings")
    assert not section.locator('[data-reviews-field="enabled"]').is_checked()
    assert not section.locator('[data-reviews-field="post_replies"]').is_checked()
    page.reload()
    section.locator('[data-reviews-reader="no_token"]').wait_for()
    assert_phone_text_is_readable(section, size)
    if size != "desktop":
        for control in section.locator("button:visible, select:visible").all():
            box = control.bounding_box()
            assert box["height"] >= 44 and box["width"] >= 44, (size, box)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    evidence = Path(os.environ.get("CARD_REVIEWS_SHOTS") or tmp_path)
    evidence.mkdir(parents=True, exist_ok=True)
    page.screenshot(
        path=str(evidence / f"reviews-settings-shell-{look}-{size}.png"), full_page=True
    )
    page.locator("[data-project-back]").click()
    page.wait_for_url("**/#/board/work")
    page.locator("#card-panel").wait_for(state="hidden")
    page.goto(f"{origin}/app/?ui={look}#/board/work/settings")
    section.wait_for()
    # Project settings use Back and Escape; the surrounding panel's close icon is hidden.
    page.keyboard.press("Escape")
    page.wait_for_url("**/#/board/work")
    page.locator("#card-panel").wait_for(state="hidden")
    assert not errors, errors
    context.close()


@pytest.mark.parametrize("size", list(SIZES))
def test_the_pipeline_shows_a_review_chip_that_leads_to_the_card(browser, size, tmp_path):  # noqa: F811
    evidence = Path(os.environ.get("CARD_REVIEWS_SHOTS") or tmp_path)
    page, errors, _ = open_shell(browser, "deck", size, cards())
    pr = {
        "repo": "acme/widgets",
        "pr": 7,
        "title": "Count things",
        "state": "running",
        "current_step": "gate",
        "updated_at": "2026-01-01T11:00:00Z",
        "lead": {"seconds": 600, "in_progress": True, "basis": "card"},
        "slowest": None,
        "longest_wait": None,
        "steps": [],
    }
    page.route(
        "**/api/pipeline/prs**", lambda route: route.fulfill(json={"demo": False, "prs": [pr]})
    )
    page.route(
        "**/api/pr-review/prs**",
        lambda route: route.fulfill(
            json={
                "prs": [
                    {
                        "repo": "acme/widgets",
                        "pr": 7,
                        "card": 7,
                        "card_reason": None,
                        "candidates": [7],
                        "counts": {"needs_you": 2, "addressed": 1},
                        "fetched_at": "2026-01-01T11:00:00Z",
                    }
                ]
            }
        ),
    )
    page.evaluate("location.hash='#/pipeline'")
    chip = page.locator("[data-pl-reviews]")
    chip.wait_for()
    assert chip.inner_text() == "2 review comments need you"
    link = page.locator("[data-pl-review-card]")
    assert link.get_attribute("href") == "#/card/7"
    if size != "desktop":
        box = link.bounding_box()
        assert box["height"] >= 44 and box["width"] >= 44, (size, box)
    assert_phone_text_is_readable(page.locator("[data-pl-reviews]"), size)
    assert_phone_text_is_readable(page.locator("[data-pl-review-card]"), size)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.screenshot(path=str(evidence / f"reviews-pipeline-{size}.png"))
    assert not errors, errors
    page.close()


SETTINGS_STYLES = (
    "app screens map mix ui decide decision-blocks rules console chat workbench chatdock bots "
    "topology topology-detail deck deck-bots deck-capy deck-settings-capy deck-coworkers "
    "deck-board deck-art deck-shell"
).split()


def settings_page(look, reader, board, people):
    data = json.dumps({"reader": reader, "board": board, "people": people})
    return (
        f'<!doctype html><html lang="en" data-ui="{look}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        + "".join(f'<link rel="stylesheet" href="/app/{name}.css">' for name in SETTINGS_STYLES)
        + '</head><body><div id="root" class="shell"><section class="workbench-project">'
        '<div class="settings"><div class="settings-inner">'
        '<section class="set-sec" data-project-reviews-section hidden><h2>Review comments</h2>'
        "<div data-project-reviews></div></section></div></div></section></div>"
        f"""<script type="module">
import {{mountReviewSettings}} from '/app/reviewsettings.js';
const data = {data};
window.sent = [];
const api = async (path, options = {{}}) => {{
  if (options.method === 'PUT') {{
    window.sent.push([path, options.body]);
    if (path.includes('/boards/')) data.board = {{...data.board, ...options.body}};
    else data.people = options.body.role ? [...data.people, {{login: options.body.login, role: options.body.role}}] : data.people.filter(p => p.login !== options.body.login);
    return {{}};
  }}
  if (path.endsWith('/status')) return {{reader: data.reader}};
  if (path.endsWith('/people')) return {{people: data.people}};
  return data.board;
}};
await mountReviewSettings(document.querySelector('[data-project-reviews-section]'), {{project: {{board: 'product'}}, api}});
window.ready = true;
</script></body></html>"""
    )


def open_settings(page, look, reader, board, people):
    html = settings_page(look, reader, board, people)

    def serve(route):
        path = urlsplit(route.request.url).path
        if path == "/probe":
            route.fulfill(body=html, content_type="text/html")
            return
        file = shell.STATIC / path.removeprefix("/app/")
        if file.is_file():
            import mimetypes

            route.fulfill(
                body=file.read_bytes(),
                content_type=mimetypes.guess_type(file.name)[0] or "application/octet-stream",
            )
        else:
            route.fulfill(status=404, body="{}")

    page.route("**/*", serve)
    page.goto("http://example.test/probe")
    page.wait_for_function("window.ready === true")


SETTINGS_STATES = {
    "off-no-token": (
        {"state": "no_token", "last_read_at": None},
        {"board": "product", "enabled": False, "rounds": 2, "post_replies": False},
        [],
    ),
    "waiting-first-read": (
        {"state": "waiting_first_read", "last_read_at": None},
        {"board": "product", "enabled": True, "rounds": 2, "post_replies": False},
        [],
    ),
    "on-reading": (
        {"state": "reading", "last_read_at": "2026-01-01T11:00:00Z"},
        {"board": "product", "enabled": True, "rounds": 3, "post_replies": True},
        [
            {"login": "helper-with-a-very-long-github-account-name", "role": "trusted"},
            {"login": "coworker-bot", "role": "own"},
        ],
    ),
    "stale": (
        {"state": "stale", "last_read_at": "2026-01-01T09:00:00Z"},
        {"board": "product", "enabled": True, "rounds": 2, "post_replies": False},
        [],
    ),
}


@pytest.mark.parametrize("look", ["deck", "classic"])
@pytest.mark.parametrize("size", list(SIZES))
def test_the_owners_review_settings_read_in_words_and_save(browser, look, size, tmp_path):  # noqa: F811
    evidence = Path(os.environ.get("CARD_REVIEWS_SHOTS") or tmp_path)
    evidence.mkdir(parents=True, exist_ok=True)
    for label, (reader, board, people) in SETTINGS_STATES.items():
        page = browser.new_page(reduced_motion="reduce", **SIZES[size])
        errors = []
        page.on("pageerror", lambda error, seen=errors: seen.append(str(error)))
        open_settings(page, look, reader, board, people)
        section = page.locator("[data-project-reviews-section]")
        section.wait_for()
        words = {
            "off-no-token": "Waiting for the owner's token.",
            "waiting-first-read": "has not read a pull request yet.",
            "on-reading": "The comment reader is working.",
            "stale": "Comments may be missing",
        }[label]
        section.get_by_text(words).first.wait_for()
        assert "token=" not in page.content() and "ghp_" not in page.content()
        for control in section.locator("button:visible, select:visible").all():
            box = control.bounding_box()
            if size != "desktop":
                assert box["height"] >= 44, (size, label, box)
        assert_phone_text_is_readable(section, size)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), (
            size,
            label,
        )
        page.screenshot(
            path=str(evidence / f"reviews-settings-{look}-{size}-{label}.png"), full_page=True
        )
        if label == "off-no-token" and size == "desktop":
            # Switch it on: the result is said once, and focus stays on Save.
            section.get_by_label(
                "Let the Coworker answer review comments on this project's pull requests"
            ).check()
            section.locator('[data-reviews-save="settings"]').click()
            section.locator("[data-reviews-status]").get_by_text(
                "Saved. The Coworker answers review comments"
            ).wait_for()
            assert page.evaluate("window.sent")[0] == [
                "/api/pr-review/boards/product",
                {"enabled": True, "rounds": 2, "post_replies": False},
            ]
            page.wait_for_function(
                "document.activeElement?.getAttribute('data-reviews-save') === 'settings'"
            )
        assert not errors, errors
        page.close()
