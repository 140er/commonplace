"""Auto-addressed PR review comments, slice 1: intake, mapping, statuses, API, tools, trigger.

Real local HTTP and real MCP calls over the console fixture. No call leaves the machine: the hub
never talks to GitHub, so the feeder is played by these tests posting to the intake route.
"""

import json
import logging
import threading
import types
from datetime import datetime, timedelta, timezone

import pytest

from commonplace.control_db import ControlError
from commonplace.pr_review import (
    intake,
    mapping,
    routes,
    status as st,
    store as review_store,
    trigger,
)
from commonplace.rules import enforce
from tests._console_helpers import console_hub_fixture, successful  # noqa: F401
from tests.test_commonplace_boards import mcp, service

REPO = "acme/widgets"
PR_URL = f"https://github.com/{REPO}/pull/7"
HEAD = "a" * 40
FIX = "b" * 40


def when(minutes=-5):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def comment(cid, body="Please rename this variable.", **kw):
    base = {
        "kind": "inline",
        "id": str(cid),
        "thread": f"t{cid}",
        "path": "app/main.py",
        "line": 12,
        "commit": HEAD,
        "author": {"login": "reviewer1", "type": "User", "association": "MEMBER"},
        "body": body,
        "created_at": when(-30),
        "updated_at": when(-30),
    }
    base.update(kw)
    return base


def batch(*comments, pr=7, head=HEAD, **kw):
    return {
        "repo": REPO,
        "pr": pr,
        "head": head,
        "pr_state": "open",
        "fetched_at": when(-1),
        **kw,
        "comments": list(comments),
    }


@pytest.fixture
def world(console_hub, monkeypatch):
    h = console_hub
    h.feeder = h.token("review-feed", profile="slim", scopes=["pr_review:write"], trust="untrusted")
    h.store = review_store.open_review(h.gateway, h.workspace)
    return h


def post(h, payload, token=None, auth=None):
    return h.request(auth or token or h.feeder, "POST", "/api/pr-review/comments", payload)


def card(h, title="Fix the thing", pr_url=PR_URL, branch="feat/thing", hold=False):
    number = successful(h, h.t1, "task_add", title=title, board="work")["number"]
    fields = {"pr": pr_url, "branch": branch}
    ok, text = mcp(h, h.t1, "task_update", task=number, **fields)
    assert ok, text
    if hold:
        ok, text = mcp(h, h.t1, "task_update", task=number, status="doing")
        assert ok, text
    return number


def view(h, number, auth=None):
    return h.request(auth or h.owner, "GET", f"/api/pr-review/cards/{number}")


def rows(h, pr=7):
    return {c["gh_id"]: c for c in h.store.comments(REPO, pr)}


# ── intake: idempotency, edits, validation ──


def test_a_comment_is_stored_once_and_a_repeated_request_changes_nothing(world):
    h = world
    status, body, _ = post(
        h, batch(comment(1), comment(2, kind="conversation", path=None, line=None))
    )
    assert status == 200, body
    assert body["accepted"] == 2 and body["duplicates"] == 0 and body["unmapped"] == 2
    again = post(h, batch(comment(1), comment(2, kind="conversation", path=None, line=None)))
    assert again[0] == 200 and again[1]["accepted"] == 0 and again[1]["duplicates"] == 2
    assert {c["status"] for c in rows(h).values()} == {"new"}
    assert len(h.store.history(REPO, 7)) == 2  # one arrival each, nothing more


def test_an_edit_with_a_changed_body_is_revision_two_and_new_again(world):
    h = world
    post(h, batch(comment(1)))
    first = rows(h)["1"]
    assert first["rev"] == 1
    edited = comment(1, "Actually, rename it to something clearer.", updated_at=when(-1))
    status, body, _ = post(h, batch(edited))
    assert status == 200 and body["edited"] == 1 and body["accepted"] == 0
    second = rows(h)["1"]
    assert second["rev"] == 2 and second["status"] == "new"
    assert "clearer" in second["text"]
    assert [e["kind"] for e in h.store.history(REPO, 7)] == ["arrived", "edited"]


def test_a_new_edit_time_with_the_same_words_changes_nothing(world):
    h = world
    post(h, batch(comment(1)))
    status, body, _ = post(h, batch(comment(1, updated_at=when(-1))))
    assert status == 200 and body["edited"] == 0 and body["duplicates"] == 1
    assert rows(h)["1"]["rev"] == 1
    assert [e["kind"] for e in h.store.history(REPO, 7)] == ["arrived"]


def test_a_removed_comment_is_skipped_unless_a_fix_is_linked(world):
    h = world
    post(h, batch(comment(1), comment(2)))
    post(h, batch(comment(1, removed=True)))
    gone = rows(h)["1"]
    assert gone["status"] == "skipped" and gone["reason"] == "removed_by_reviewer"
    assert rows(h)["2"]["status"] == "new"


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda b: b.update(extra=1), "holds only"),
        (lambda b: b.update(repo="not a repo"), "owner/name"),
        (lambda b: b.update(pr=0), "whole number"),
        (lambda b: b.update(head="abc"), "full lowercase"),
        (lambda b: b.update(pr_state="draft"), "pr_state"),
        (lambda b: b.update(fetched_at="yesterday"), "ISO 8601"),
        (lambda b: b["comments"].__setitem__(0, {**b["comments"][0], "kind": "wild"}), "kind"),
        (
            lambda b: b["comments"].__setitem__(0, {**b["comments"][0], "body": "x" * 65_537}),
            "longer",
        ),
        (
            lambda b: b["comments"].__setitem__(0, {**b["comments"][0], "body": "bad\x00text"}),
            "plain text",
        ),
        (lambda b: b["comments"].append(b["comments"][0]), "twice"),
        (lambda b: b.update(comments=[comment(i) for i in range(101)]), "at most 100"),
        (
            lambda b: b["comments"].__setitem__(
                0, {**b["comments"][0], "created_at": "2999-01-01T00:00:00Z"}
            ),
            "future",
        ),
    ],
)
def test_a_bad_request_is_a_plain_400_and_stores_nothing(world, mutate, fragment):
    h = world
    payload = batch(comment(1))
    mutate(payload)
    status, body, _ = post(h, payload)
    assert status == 400 and fragment in body["error"], body
    assert h.store.comments(REPO, 7) == [] and h.store.prs() == []


def test_one_bad_comment_stores_none_of_the_batch(world):
    h = world
    status, _, _ = post(h, batch(comment(1), comment(2, kind="nope")))
    assert status == 400
    assert h.store.comments(REPO, 7) == []


def test_the_feeder_token_reaches_only_intake_and_watch(world):
    h = world
    for method, path in (
        ("GET", "/api/pr-review/prs"),
        ("GET", "/api/pr-review/cards/1"),
        ("GET", "/api/pipeline/prs"),
        ("POST", "/api/pipeline/events"),
        ("GET", "/api/boards"),
        ("POST", "/mcp"),
    ):
        status, body, _ = h.request(h.feeder, method, path, {} if method == "POST" else None)
        assert status == 403, (method, path, status, body)
        assert "review comments" in body["error"]
    assert h.request(h.feeder, "GET", "/api/pr-review/watch")[0] == 200
    assert post(h, batch(comment(1)))[0] == 200


def test_who_may_post_comments(world):
    h = world
    assert post(h, batch(comment(1)), auth=h.owner)[0] == 200
    assert post(h, batch(comment(2)), auth=h.member)[0] == 403
    assert post(h, batch(comment(3)), auth=h.t1)[0] == 403  # a Coworker token has no write here
    assert h.request(None, "POST", "/api/pr-review/comments", batch(comment(4)))[0] == 401


def test_a_feeder_token_is_alone_owned_by_an_owner_and_one_per_workspace(world):
    h = world
    with pytest.raises(ControlError, match="only the pr_review:write scope"):
        h.token(
            "mixed", profile="slim", scopes=["pr_review:write", "memory:read"], trust="untrusted"
        )
    with pytest.raises(ControlError, match="already has a review-comment token"):
        h.token("second", profile="slim", scopes=["pr_review:write"], trust="untrusted")
    with pytest.raises(ControlError, match="only the pr_review:write scope"):
        h.token(
            "member-feed",
            user=h.member_user,
            profile="slim",
            scopes=["pr_review:write"],
            trust="untrusted",
        )


# ── who is heard ──


def test_only_trusted_authors_are_work_and_the_rest_is_shown_with_a_reason(world):
    h = world
    stranger = {"login": "drive-by", "type": "User", "association": "NONE"}
    robot = {"login": "ci-bot", "type": "Bot", "association": None}
    owner_reply = comment(5, "Fixed.\n<!-- cmmn:reply c=1 -->")
    verdict = comment(
        6,
        "Reviewed head: " + HEAD + "\nVerdict: CHANGES",
        kind="review",
        path=None,
        line=None,
        review_state="COMMENTED",
    )
    post(
        h,
        batch(
            comment(1), comment(2, author=stranger), comment(3, author=robot), owner_reply, verdict
        ),
    )
    got = rows(h)
    assert got["1"]["status"] == "new"
    assert (got["2"]["status"], got["2"]["reason"]) == ("skipped", "author_not_trusted")
    assert (got["3"]["status"], got["3"]["reason"]) == ("skipped", "author_not_trusted")
    assert (got["5"]["status"], got["5"]["reason"]) == ("skipped", "own_reply")
    assert (got["6"]["status"], got["6"]["reason"]) == ("skipped", "verdict_line")
    assert got["2"]["reason_text"] and got["2"]["next"]


def test_an_owner_can_trust_a_login_and_its_waiting_comments_become_work(world):
    h = world
    stranger = {"login": "Helper", "type": "User", "association": "CONTRIBUTOR"}
    post(h, batch(comment(1, author=stranger)))
    assert rows(h)["1"]["status"] == "skipped"
    status, body, _ = h.request(
        h.owner, "PUT", "/api/pr-review/people", {"login": "helper", "role": "trusted"}
    )
    assert status == 200, body
    assert rows(h)["1"]["status"] == "new"
    assert (
        h.request(h.member, "PUT", "/api/pr-review/people", {"login": "x", "role": "trusted"})[0]
        == 403
    )


# ── mapping and who may read ──


def test_comments_with_no_card_are_kept_unmapped_and_map_once_a_card_links_the_pr(world):
    h = world
    post(h, batch(comment(1)))
    status, body, _ = h.request(h.owner, "GET", "/api/pr-review/prs")
    only = body["prs"][0]
    assert (only["card"], only["card_reason"]) == (None, "no_card")
    number = card(h)
    status, body, _ = h.request(h.owner, "GET", "/api/pr-review/prs")
    assert body["prs"][0]["card"] == number
    status, shown, _ = view(h, number)
    assert status == 200 and shown["counts"]["new"] == 1 and shown["comments"][0]["gh_id"] == "1"


def test_two_cards_with_the_pr_link_are_ambiguous_and_name_both(world):
    h = world
    first, second = card(h), card(h, title="Again")
    post(h, batch(comment(1)))
    _, body, _ = h.request(h.owner, "GET", "/api/pr-review/prs")
    only = body["prs"][0]
    assert only["card"] is None and only["card_reason"] == "ambiguous_card"
    assert set(only["candidates"]) == {first, second}


def test_a_card_that_cannot_be_seen_is_the_same_404_as_one_that_does_not_exist(world):
    h = world
    number = card(h)
    post(h, batch(comment(1)))
    assert view(h, number)[0] == 200
    missing = view(h, 99_999)
    foreign = view(h, number, auth=h.other)
    assert missing[0] == foreign[0] == 404 and missing[2] == foreign[2]
    assert view(h, number, auth=h.t1)[0] == 403  # a Coworker uses the tools, not the page


def test_the_stored_words_are_verbatim_and_everything_else_shows_them_redacted(world, caplog):
    h = world
    number = card(h)
    secret = "ghp_" + "A1b2" * 9  # built at run time: no token-shaped literal in the source
    text = f"Use token {secret} here and rename x."
    with caplog.at_level(logging.DEBUG):
        post(h, batch(comment(1, text)))
        _, shown, _ = view(h, number)
        h.request(h.owner, "GET", "/api/pr-review/watch")
    assert secret not in json.dumps(shown) and shown["comments"][0]["text_redacted"] is True
    cid = shown["comments"][0]["id"]
    status, original, _ = h.request(
        h.owner, "GET", f"/api/pr-review/cards/{number}/comments/{cid}/original"
    )
    assert status == 200 and original["text"] == text
    assert secret not in caplog.text and "rename x" not in caplog.text


def test_intake_failures_log_no_comment_text(world, caplog, monkeypatch):
    h = world

    def boom(self, request, now):
        raise RuntimeError("storage exploded: " + request["comments"][0]["body"])

    monkeypatch.setattr(review_store.ReviewStore, "record", boom)
    with caplog.at_level(logging.DEBUG):
        status, body, _ = post(h, batch(comment(1, "my private words")))
    assert status == 500 and body == {"error": "review comment request failed"}
    assert "my private words" not in caplog.text


# ── the Coworker's tools: take a round, report outcomes ──


def start(h, **kw):
    """A card the Coworker (a1) holds, with its PR comments posted."""
    number = card(h, hold=True, **kw)
    post(
        h,
        batch(
            comment(1),
            comment(2, "Why this approach?", thread="t2"),
            comment(3, kind="conversation", path=None, line=None),
        ),
    )
    return number


def take(h, number, token=None):
    return mcp(h, token or h.t1, "pr_review_take", card=number)


def test_taking_a_round_makes_the_waiting_comments_the_coworkers(world):
    h = world
    number = start(h)
    ok, text = mcp(h, h.t1, "pr_review_list", card=number)
    assert ok and "3 new" in text and "quoted data" in text
    ok, text = take(h, number)
    assert ok and "3 comment(s) are yours" in text
    assert {c["status"] for c in rows(h).values()} == {"working"}
    assert {c["round"] for c in rows(h).values()} == {1}
    ok, again = take(h, number)  # resumed run: the same comments, no second round
    assert ok and "3 comment(s)" in again and h.store.pr(REPO, 7)["rounds_used"] == 1


def test_only_the_holder_can_take_or_report_and_others_see_the_list(world):
    h = world
    number = start(h)
    ok, text = take(h, number, token=h.t2)
    assert not ok and "do not hold" in text
    ok, text = mcp(h, h.t2, "pr_review_list", card=number)
    assert ok and "PR #7" in text  # a reader of the wing may look at the list
    working = take(h, number)
    assert working[0]
    ok, text = mcp(
        h,
        h.t2,
        "pr_review_report",
        card=number,
        reports=[{"comment": 1, "rev": 1, "outcome": "answered", "reply": "no"}],
    )
    assert not ok and "do not hold" in text


def test_a_fix_is_addressed_when_the_commit_is_the_head_the_feeder_saw(world):
    h = world
    number = start(h)
    take(h, number)
    # the feeder sees the Coworker's push and reports which files it changed
    post(h, batch(head=FIX, changed_files=["app/main.py"]))
    ok, text = mcp(
        h,
        h.t1,
        "pr_review_report",
        card=number,
        reports=[
            {"comment": 1, "rev": 1, "outcome": "addressed", "commit": FIX, "reply": "Renamed."}
        ],
    )
    assert ok, text
    one = rows(h)["1"]
    assert (
        one["status"] == "addressed" and one["fix_commit"] == FIX and one["fix_confirmed"] is True
    )
    assert one["reply"] == "Renamed."
    # a repeated report is a no-op that returns the stored state
    ok, text = mcp(
        h,
        h.t1,
        "pr_review_report",
        card=number,
        reports=[{"comment": 1, "rev": 1, "outcome": "addressed", "commit": FIX}],
    )
    assert ok and "now addressed" in text
    assert [e["kind"] for e in h.store.history(REPO, 7) if e["comment_id"] == one["id"]] == [
        "arrived",
        "taken",
        "reported",
    ]


def test_a_fix_the_feeder_has_not_seen_waits_and_is_confirmed_or_held_later(world):
    h = world
    number = start(h)
    take(h, number)
    mcp(
        h,
        h.t1,
        "pr_review_report",
        card=number,
        reports=[{"comment": 1, "rev": 1, "outcome": "addressed", "commit": FIX}],
    )
    assert rows(h)["1"]["status"] == "working" and rows(h)["1"]["fix_confirmed"] is False
    post(h, batch(head=FIX, changed_files=["app/main.py"]))
    h.store.settle(datetime.now(timezone.utc))
    assert rows(h)["1"]["status"] == "addressed"
    # a second comment whose commit never shows up becomes one `needs you` after the grace
    mcp(
        h,
        h.t1,
        "pr_review_report",
        card=number,
        reports=[{"comment": 2, "rev": 1, "outcome": "addressed", "commit": "c" * 40}],
    )
    post(h, batch(head=FIX, fetched_at=when(2)))  # the feeder read again, still no such commit
    late = datetime.now(timezone.utc) + timedelta(minutes=11)
    h.store.settle(late)
    two = rows(h)["2"]
    assert (two["status"], two["reason"]) == ("needs_you", "commit_not_found") and two["next"]


def test_a_fix_that_changes_the_checks_is_held_for_a_person(world):
    h = world
    number = start(h)
    take(h, number)
    post(h, batch(head=FIX, changed_files=["app/main.py", ".github/workflows/ci.yml"]))
    mcp(
        h,
        h.t1,
        "pr_review_report",
        card=number,
        reports=[{"comment": 1, "rev": 1, "outcome": "addressed", "commit": FIX}],
    )
    one = rows(h)["1"]
    assert (one["status"], one["reason"]) == ("needs_you", "touches_checks")


def test_answered_and_needs_you_outcomes_and_the_one_question_on_the_card(world):
    h = world
    number = start(h)
    take(h, number)
    ok, text = mcp(
        h,
        h.t1,
        "pr_review_report",
        card=number,
        reports=[
            {
                "comment": 1,
                "rev": 1,
                "outcome": "answered",
                "reply": "Kept on purpose: it matches the API.",
            },
            {"comment": 2, "rev": 1, "outcome": "needs_you", "note": "Needs a product decision."},
            {"comment": 3, "rev": 1, "outcome": "needs_you", "note": "Also needs a person."},
        ],
    )
    assert ok, text
    got = rows(h)
    assert got["1"]["status"] == "answered" and "Kept on purpose" in got["1"]["reply"]
    assert got["2"]["status"] == "needs_you" and got["3"]["status"] == "needs_you"
    assert text.count("One question was placed") == 1
    ok, shown = mcp(h, h.t1, "task_show", task=number)
    assert ok and "Review comments on PR #7 need a decision: 2 of 3" in shown


def test_a_report_is_refused_for_a_stale_revision_a_foreign_comment_and_bad_input(world):
    h = world
    number = start(h)
    take(h, number)
    post(h, batch(comment(1, "Changed my mind entirely.", updated_at=when(-1))))
    ok, text = mcp(
        h,
        h.t1,
        "pr_review_report",
        card=number,
        reports=[{"comment": 1, "rev": 1, "outcome": "answered", "reply": "ok"}],
    )
    assert not ok and "comment_changed" in text
    assert rows(h)["1"]["status"] == "new" and rows(h)["1"]["rev"] == 2
    for report, fragment in (
        ({"comment": 999, "rev": 1, "outcome": "answered", "reply": "x"}, "not found"),
        ({"comment": 2, "rev": 1, "outcome": "addressed"}, "needs the commit"),
        ({"comment": 2, "rev": 1, "outcome": "answered"}, "needs the reply"),
        ({"comment": 2, "rev": 1, "outcome": "needs_you"}, "needs a note"),
        ({"comment": 2, "rev": 1, "outcome": "merged"}, ""),
    ):
        ok, text = mcp(h, h.t1, "pr_review_report", card=number, reports=[report])
        assert not ok and fragment in text, (report, text)


def test_the_tools_offer_nothing_that_merges_resolves_or_dismisses(world):
    h = world
    names = h.tool_names(h.t1)
    assert {"pr_review_list", "pr_review_take", "pr_review_report"} <= names
    assert not {
        n
        for n in names
        if n.startswith("pr_review") and any(w in n for w in ("merge", "resolve", "dismiss"))
    }
    schema = next(
        t for t in h.rpc(h.t1, "tools/list")["result"]["tools"] if t["name"] == "pr_review_report"
    )
    item = schema["inputSchema"]["properties"]["reports"]["items"]
    assert set(item["properties"]) == {
        "comment",
        "rev",
        "outcome",
        "commit",
        "reply",
        "note",
        "posted",
    }
    assert item["properties"]["outcome"]["enum"] == ["addressed", "answered", "needs_you"]


def test_rounds_run_out_after_two_and_a_person_can_allow_two_more(world):
    h = world
    number = start(h)
    for n in (1, 2):
        ok, text = take(h, number)
        assert ok, text
        post(h, batch(head=chr(ord("b") + n) * 40))
        reports = [
            {"comment": c["id"], "rev": c["rev"], "outcome": "answered", "reply": "Because."}
            for c in h.store.comments(REPO, 7)
            if c["status"] == "working"
        ]
        assert mcp(h, h.t1, "pr_review_report", card=number, reports=reports)[0]
        post(h, batch(comment(10 + n, "One more thing."), head=chr(ord("b") + n) * 40))
    ok, text = take(h, number)
    assert not ok and "rounds_exhausted" in text
    status, body, _ = h.request(
        h.owner, "POST", f"/api/pr-review/cards/{number}/decision", {"choice": "again"}
    )
    assert status == 200, body
    assert h.store.pr(REPO, 7)["rounds_used"] == 0
    assert (
        h.request(h.member, "POST", f"/api/pr-review/cards/{number}/decision", {"choice": "again"})[
            0
        ]
        == 403
    )


def test_the_owners_other_two_answers(world):
    h = world
    number = start(h)
    h.store.hold(REPO, 7, "rounds_exhausted", "system", datetime.now(timezone.utc))
    status, body, _ = h.request(
        h.owner, "POST", f"/api/pr-review/cards/{number}/decision", {"choice": "dismiss"}
    )
    assert status == 200 and body["counts"]["skipped"] == 3
    assert {c["reason"] for c in rows(h).values()} == {"dismissed_by_person"}
    assert (
        h.request(h.owner, "POST", f"/api/pr-review/cards/{number}/decision", {"choice": "nope"})[0]
        == 400
    )
    post(h, batch(comment(20, "Later comment.")))
    assert (
        h.request(h.owner, "POST", f"/api/pr-review/cards/{number}/decision", {"choice": "mine"})[0]
        == 200
    )
    assert (
        rows(h)["20"]["reason"] == "handled_by_person"
        and h.store.pr(REPO, 7)["loop_stopped"] is True
    )


def test_a_comment_nobody_reports_comes_back_once_then_asks_a_person(world):
    h = world
    number = start(h)
    take(h, number)
    now = datetime.now(timezone.utc)
    h.store.settle(now + timedelta(minutes=31))
    assert {c["status"] for c in rows(h).values()} == {"new"}
    assert {c["reason"] for c in rows(h).values()} == {"run_failed"}
    take_again = h.store.take_round(
        REPO, 7, h.principal(h.t1).actor, now + timedelta(minutes=32), rounds=3
    )
    assert len(take_again) == 3
    h.store.settle(now + timedelta(minutes=70))
    h.store.take_round(REPO, 7, h.principal(h.t1).actor, now + timedelta(minutes=71), rounds=3)
    h.store.settle(now + timedelta(minutes=110))
    assert {(c["status"], c["reason"]) for c in rows(h).values()} == {("needs_you", "run_lost")}


# ── the trigger ──


def board_on(h, rounds=2, auth=None):
    status, body, _ = h.request(
        auth or h.owner, "PUT", "/api/pr-review/boards/work", {"enabled": True, "rounds": rounds}
    )
    assert status == 200, body


def configured(monkeypatch, value=True):
    from commonplace.orch import runner_bridge

    monkeypatch.setattr(runner_bridge, "configured", lambda *a, **k: value)


def finished_card(h):
    """A card the Coworker finished: waiting in a completion review with its PR."""
    owner = service(h, h.person(h.admin))
    with owner.store.transaction() as conn:
        conn.execute("UPDATE boards SET done_needs_review=1 WHERE slug='work'")
    number = card(h, hold=True)
    ok, text = mcp(
        h,
        h.t1,
        "task_update",
        task=number,
        status="done",
        context="The change is made, its tests pass and the pull request is open.",
    )
    assert ok, text
    ok, shown = mcp(h, h.t1, "task_show", task=number)
    assert "review" in shown, shown
    return number


def status_of(h, number):
    ok, shown = mcp(h, h.t1, "task_show", task=number)
    assert ok, shown
    return shown.split()[1]


def test_the_loop_is_off_until_a_board_owner_switches_it_on(world, monkeypatch):
    h = world
    configured(monkeypatch)
    number = finished_card(h)
    post(h, batch(comment(1)))
    assert trigger.run(h.gateway, h.workspace) == []
    assert status_of(h, number) == "review"
    assert h.request(h.member, "PUT", "/api/pr-review/boards/work", {"enabled": True})[0] == 403
    assert h.request(h.owner, "GET", "/api/pr-review/boards/work")[1]["enabled"] is False


def test_waiting_comments_hand_a_finished_card_back_to_its_coworker_once(world, monkeypatch):
    h = world
    configured(monkeypatch)
    number = finished_card(h)
    board_on(h)
    status, body, _ = post(h, batch(comment(1)))  # intake wakes the trigger itself
    assert status == 200, body
    assert status_of(h, number) == "todo"
    again = trigger.run(h.gateway, h.workspace)
    assert [r["outcome"] for r in again] == [
        "waiting"
    ]  # a card in todo is left to the todo trigger
    ok, shown = mcp(h, h.t1, "task_show", task=number)
    assert shown.count("Review comments arrived on PR #7") == 1


def test_a_card_the_coworker_holds_keeps_its_comments_waiting(world, monkeypatch):
    h = world
    configured(monkeypatch)
    number = card(h, hold=True)
    board_on(h)
    post(h, batch(comment(1)))
    assert status_of(h, number) == "doing"
    assert rows(h)["1"]["status"] == "new"


def test_a_board_without_a_coworker_setup_asks_the_owner_once(world):
    h = world
    number = finished_card(h)
    board_on(h)
    post(h, batch(comment(1), comment(2)))
    got = rows(h)
    assert {(c["status"], c["reason"]) for c in got.values()} == {
        ("needs_you", "no_coworker_config")
    }
    assert status_of(h, number) == "review"
    ok, shown = mcp(h, h.t1, "task_show", task=number)
    assert shown.count("need a decision") <= 1


@pytest.mark.parametrize(
    "extra, reason",
    [({"head_repo": "someone/fork"}, "fork_pr"), ({"branch": "other-branch"}, "branch_not_owned")],
)
def test_a_fork_or_a_branch_that_is_not_the_cards_is_never_worked(
    world, monkeypatch, extra, reason
):
    h = world
    configured(monkeypatch)
    number = finished_card(h)
    board_on(h)
    post(h, batch(comment(1), **extra))
    assert {c["reason"] for c in rows(h).values()} == {reason}
    assert status_of(h, number) == "review"


def test_used_up_rounds_hold_the_next_comments_for_a_person(world, monkeypatch):
    h = world
    configured(monkeypatch)
    finished_card(h)
    board_on(h, rounds=1)
    h.store.set_board("work", True, h.principal(h.t1).user_id, 1, datetime.now(timezone.utc))
    post(h, batch(comment(1)))  # the first comment returns the card
    h.store.take_round(REPO, 7, "x", datetime.now(timezone.utc), rounds=1)
    post(h, batch(comment(2, "Another request.")))
    assert (rows(h)["2"]["status"], rows(h)["2"]["reason"]) == ("needs_you", "rounds_exhausted")


def test_a_merged_or_closed_pr_stops_the_loop(world, monkeypatch):
    h = world
    configured(monkeypatch)
    number = finished_card(h)
    board_on(h)
    post(h, batch(comment(1), pr_state="merged"))
    assert status_of(h, number) == "review" and rows(h)["1"]["status"] == "new"


def test_the_watch_list_names_open_prs_of_enabled_boards_only(world):
    h = world
    number = card(h)
    assert h.request(h.feeder, "GET", "/api/pr-review/watch")[1] == {"prs": []}
    board_on(h)
    assert h.request(h.feeder, "GET", "/api/pr-review/watch")[1] == {
        "prs": [{"repo": REPO, "pr": 7, "card": number}]
    }
    ok, _ = mcp(h, h.t1, "task_update", task=number, pr_state="merged")
    assert h.request(h.feeder, "GET", "/api/pr-review/watch")[1] == {"prs": []}


def test_the_status_words_and_reasons_are_complete():
    assert set(st.WORDS) == set(st.STATUSES)
    for code, (text, step) in st.REASONS.items():
        assert text and step, code
    produced = {
        "no_coworker_config",
        "rounds_exhausted",
        "fork_pr",
        "branch_not_owned",
        "touches_checks",
        "commit_not_found",
        "run_failed",
        "run_lost",
        "own_reply",
        "verdict_line",
        "author_not_trusted",
        "removed_by_reviewer",
        "dismissed_by_person",
        "handled_by_person",
        "coworker_asks",
        "card_done",
        "card_not_startable",
        "loop_owner_unavailable",
    }
    assert produced <= set(st.REASONS)


def test_intake_limits_are_fixed_and_validation_is_pure():
    assert intake.is_verdict("Reviewed head: " + HEAD + "\nVerdict: PASS")
    assert not intake.is_verdict("Reviewed head: " + HEAD + "\nVerdict: PASS\nplus words")
    assert review_store.LIMITS["request_comments"] == intake.MAX_COMMENTS == 100
    assert routes.PREFIX == "/api/pr-review"


def test_a_card_without_a_question_gets_exactly_one_for_the_owner(world):
    h = world
    number = card(h, hold=True)
    board_on(h)
    post(h, batch(comment(1), comment(2, "Second thing.")))
    assert {c["reason"] for c in rows(h).values()} == {"no_coworker_config"}
    ok, shown = mcp(h, h.t1, "task_show", task=number)
    assert (
        shown.count('asks owner@example.com: "Review comments on PR #7 need a decision: 2 of 2"')
        == 1
    )
    post(h, batch(comment(3, "Third thing.")))  # more comments later: still one question
    ok, shown = mcp(h, h.t1, "task_show", task=number)
    assert shown.count(" handoff: ") == 1


def test_a_workspace_that_never_used_the_loop_gets_no_file_from_the_sweep(world):
    h = world
    assert trigger.run(h.gateway, h.workspace) == []
    assert h.hub.db is not None and not h.store.path.exists()
    assert h.request(h.owner, "GET", "/api/pr-review/prs")[1] == {"prs": []}
    assert not h.store.path.exists()


# ── the checks rule never rests on an earlier head's file list ──


def fixed_with_unknown_files(h):
    """A fix for the PR's current head whose changed files the feeder has not reported."""
    number = start(h)
    take(h, number)
    # head A carried a harmless file list; the Coworker's push B arrives with no list at all
    post(h, batch(head=HEAD, changed_files=["app/main.py"]))
    post(h, batch(head=FIX))
    assert h.store.pr(REPO, 7)["changed_files"] == [] and h.store.pr(REPO, 7)["changed_head"] == ""
    ok, text = mcp(
        h,
        h.t1,
        "pr_review_report",
        card=number,
        reports=[{"comment": 1, "rev": 1, "outcome": "addressed", "commit": FIX}],
    )
    assert ok, text
    return number


def test_a_fix_whose_files_are_unknown_is_held_not_addressed(world):
    h = world
    fixed_with_unknown_files(h)
    one = rows(h)["1"]
    assert (one["status"], one["fix_confirmed"]) == ("working", False)
    h.store.settle(datetime.now(timezone.utc))
    assert rows(h)["1"]["status"] == "working"  # waiting for the files, inside the grace


def test_the_files_for_the_current_head_decide_when_they_arrive(world):
    h = world
    fixed_with_unknown_files(h)
    post(h, batch(head=FIX, changed_files=["app/main.py", "pyproject.toml"]))
    h.store.settle(datetime.now(timezone.utc))
    one = rows(h)["1"]
    assert (one["status"], one["reason"]) == ("needs_you", "touches_checks")


def test_harmless_files_for_the_current_head_confirm_the_fix(world):
    h = world
    fixed_with_unknown_files(h)
    post(h, batch(head=FIX, changed_files=["app/main.py"]))
    h.store.settle(datetime.now(timezone.utc))
    assert rows(h)["1"]["status"] == "addressed"


def test_files_that_never_come_ask_a_person_after_the_grace(world):
    h = world
    fixed_with_unknown_files(h)
    post(h, batch(head=FIX, fetched_at=when(2)))  # the feeder read again, still no file list
    h.store.settle(datetime.now(timezone.utc) + timedelta(minutes=11))
    one = rows(h)["1"]
    assert (one["status"], one["reason"]) == ("needs_you", "files_unknown") and one["next"]


def test_a_repeated_post_of_the_same_head_keeps_its_known_files(world):
    h = world
    post(h, batch(head=FIX, changed_files=[".github/workflows/ci.yml"]))
    post(h, batch(head=FIX))
    got = h.store.pr(REPO, 7)
    assert got["changed_files"] == [".github/workflows/ci.yml"] and got["changed_head"] == FIX


# ── one pass is bounded ──


def test_one_pass_looks_at_a_bounded_number_of_prs_longest_waiting_first(world, monkeypatch):
    h = world
    board_on(h)
    for n in (21, 22, 23):
        post(h, batch(comment(n), pr=n))
    assert [p["pr"] for p in h.store.waiting_prs(2)] == [21, 22]
    seen = []
    monkeypatch.setattr(trigger, "MAX_PRS_PER_PASS", 2)
    monkeypatch.setattr(trigger, "_consider", lambda g, w, s, b, row, now: seen.append(row["pr"]))
    trigger.run(h.gateway, h.workspace)
    assert seen == [21, 22]


# ── tenant isolation: a person sees only the PRs whose card they can read ──


def hidden_pr(h):
    """A PR whose card is on a private board: the owner reads it, a member does not."""
    owner = service(h, h.person(h.admin))
    owner.board_add(slug="hidden", wing="private.hidden", repo="https://github.com/acme/secret.git")
    number = owner.task_add("Secret work", board="hidden")["number"]
    owner.task_update(number, pr="https://github.com/acme/secret/pull/9")
    payload = batch(comment(1, "Rename the secret thing."), pr=9)
    payload["repo"] = "acme/secret"
    assert post(h, payload)[0] == 200
    return number


def test_a_member_sees_nothing_of_a_pr_whose_card_is_in_a_wing_they_cannot_read(world):
    h = world
    visible = card(h)
    secret = hidden_pr(h)
    post(h, batch(comment(2, "A comment on the visible PR.")))
    _, listed, raw = h.request(h.member, "GET", "/api/pr-review/prs")
    assert [p["pr"] for p in listed["prs"]] == [7] and listed["prs"][0]["card"] == visible
    for leaked in (b"acme/secret", b'"pr": 9', b"private", b"hidden"):
        assert leaked not in raw, leaked
    _, owner_view, _ = h.request(h.owner, "GET", "/api/pr-review/prs")
    assert {p["pr"] for p in owner_view["prs"]} == {7, 9}
    assert view(h, secret, auth=h.member)[2] == view(h, 99_999, auth=h.member)[2]
    assert view(h, secret, auth=h.member)[0] == 404 and view(h, secret)[0] == 200


def test_a_pr_with_no_card_is_the_owners_to_see_and_not_a_members(world):
    h = world
    post(h, batch(comment(1)))
    assert h.request(h.member, "GET", "/api/pr-review/prs")[1] == {"prs": []}
    _, owner_view, _ = h.request(h.owner, "GET", "/api/pr-review/prs")
    assert owner_view["prs"][0]["card_reason"] == "no_card"


def test_board_settings_and_trusted_logins_are_the_owners_pages(world):
    h = world
    assert h.request(h.member, "GET", "/api/pr-review/boards/work")[0] == 403
    assert h.request(h.member, "GET", "/api/pr-review/people")[0] == 403
    assert h.request(h.owner, "GET", "/api/pr-review/boards/work")[0] == 200
    assert h.request(h.owner, "GET", "/api/pr-review/people")[0] == 200


def test_a_failed_board_read_is_its_own_reason_and_leaks_no_words(world, monkeypatch, caplog):
    h = world
    card(h)
    post(h, batch(comment(1)))

    class Broken:
        def connection(self):
            raise RuntimeError("board exploded: my private words")

    monkeypatch.setattr(mapping, "_open", lambda gateway, workspace: Broken())
    with caplog.at_level(logging.DEBUG):
        _, listed, _ = h.request(h.owner, "GET", "/api/pr-review/prs")
    assert listed["prs"][0]["card"] is None and listed["prs"][0]["card_reason"] == "lookup_failed"
    assert st.reason_fields("lookup_failed")["next"] and "my private words" not in caplog.text
    assert h.request(h.member, "GET", "/api/pr-review/prs")[1] == {"prs": []}


# ── bounds ──


def test_the_card_view_and_the_pr_list_are_paged(world):
    h = world
    number = card(h)
    post(h, batch(*[comment(n, f"Comment {n}.") for n in range(1, 6)]))
    _, page, _ = h.request(h.owner, "GET", f"/api/pr-review/cards/{number}?limit=2")
    assert len(page["comments"]) == 2 and page["comments_total"] == 5 and page["has_more"] is True
    _, last, _ = h.request(h.owner, "GET", f"/api/pr-review/cards/{number}?limit=2&offset=4")
    assert len(last["comments"]) == 1 and last["has_more"] is False
    for query in ("limit=0", "limit=501", "limit=x", "offset=-1"):
        assert h.request(h.owner, "GET", f"/api/pr-review/cards/{number}?{query}")[0] == 400, query
    other = card(h, title="Second", pr_url="https://github.com/acme/widgets/pull/8")
    post(h, batch(comment(9), pr=8))
    assert other and len(h.request(h.owner, "GET", "/api/pr-review/prs?limit=1")[1]["prs"]) == 1
    assert h.request(h.owner, "GET", "/api/pr-review/prs?limit=101")[0] == 400


def test_the_list_tool_shows_a_bounded_page_and_says_how_many_more(world, monkeypatch):
    h = world
    from commonplace.pr_review import tools

    monkeypatch.setattr(tools, "LIST_MAX", 2)
    number = card(h)
    post(h, batch(*[comment(n, f"Comment {n}.") for n in range(1, 5)]))
    ok, text = mcp(h, h.t1, "pr_review_list", card=number)
    assert ok and text.count("rev 1") == 2 and "2 more not shown" in text


# ── the take path is a checked write of its own ──


def test_taking_a_round_is_a_checked_write_and_the_list_tool_cannot_take(world):
    h = world
    number = start(h)
    assert {"pr_review_take", "pr_review_report"} <= enforce.CHECKED_WRITES
    assert "pr_review_list" not in enforce.CHECKED_WRITES
    ok, text = mcp(h, h.t1, "pr_review_list", card=number, take=True)
    assert not ok
    assert {c["status"] for c in rows(h).values()} == {"new"}


# ── paths are normalised before the protected-path rule ──


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yml",
        "./.github/workflows/ci.yml",
        "//.github//workflows/ci.yml",
        ".GitHub/Workflows/ci.yml",
        "./PyProject.toml",
        "a/../b.py",
        "commonplace/guards/x.py",
        ".github\\workflows\\ci.yml",
    ],
)
def test_protected_paths_are_found_however_they_are_written(path):
    assert review_store.is_protected(path)


@pytest.mark.parametrize(
    "path", ["app/main.py", "pyproject.toml.bak", "docs/.github-notes.md", "tests/test_x.py"]
)
def test_ordinary_paths_are_not_protected(path):
    assert not review_store.is_protected(path)


def test_a_fix_listed_with_a_dressed_up_protected_path_is_held(world):
    h = world
    number = start(h)
    take(h, number)
    post(h, batch(head=FIX, changed_files=["app/main.py", "./.GitHub//workflows/ci.yml"]))
    mcp(
        h,
        h.t1,
        "pr_review_report",
        card=number,
        reports=[{"comment": 1, "rev": 1, "outcome": "addressed", "commit": FIX}],
    )
    assert (rows(h)["1"]["status"], rows(h)["1"]["reason"]) == ("needs_you", "touches_checks")


# ── a malformed body is answered 400 by the shared reader ──


def test_a_body_that_is_not_an_object_is_a_400_on_every_write_route(world):
    h = world
    number = card(h)
    for auth, path in (
        (h.feeder, "/api/pr-review/comments"),
        (h.owner, f"/api/pr-review/cards/{number}/decision"),
        (h.owner, "/api/pr-review/boards/work"),
    ):
        method = "PUT" if "boards" in path else "POST"
        status, body, _ = h.request(auth, method, path, [1, 2])
        assert status == 400 and body == {"error": "Invalid JSON body"}, (path, status, body)


# ── the board's rounds setting is the cap, from 1 to 3 ──


def rounds_allowed(h, monkeypatch, rounds):
    """Take and answer rounds until the board's setting refuses the next one."""
    configured(monkeypatch)
    board_on(h, rounds=rounds)
    number = start(h)
    for n in range(1, rounds + 1):
        ok, text = take(h, number)
        assert ok and "Round started" in text, (n, text)
        reports = [
            {"comment": c["id"], "rev": c["rev"], "outcome": "answered", "reply": "Because."}
            for c in h.store.comments(REPO, 7)
            if c["status"] == "working"
        ]
        assert mcp(h, h.t1, "pr_review_report", card=number, reports=reports)[0]
        post(h, batch(comment(100 + n, "One more thing.")))
    # the comment after the last allowed round waits for a person, and no round can start
    held = rows(h)[str(100 + rounds)]
    assert (held["status"], held["reason"]) == ("needs_you", "rounds_exhausted")
    actor = h.principal(h.t1).actor
    with pytest.raises(review_store.Refused, match="rounds_exhausted"):
        h.store.take_round(REPO, 7, actor, datetime.now(timezone.utc), rounds=rounds)
    assert h.store.pr(REPO, 7)["rounds_used"] == rounds
    assert view(h, number)[1]["pr_info"]["rounds_allowed"] == rounds
    return number


def test_a_board_set_to_three_rounds_permits_exactly_three(world, monkeypatch):
    h = world
    number = rounds_allowed(h, monkeypatch, 3)
    # "Try again" gives the board's rounds back, so a fourth round can start after it
    path = f"/api/pr-review/cards/{number}/decision"
    assert h.request(h.owner, "POST", path, {"choice": "again"})[0] == 200
    actor = h.principal(h.t1).actor
    again = h.store.take_round(REPO, 7, actor, datetime.now(timezone.utc), rounds=3)
    assert [c["gh_id"] for c in again] == ["103"] and h.store.pr(REPO, 7)["rounds_used"] == 1


def test_a_board_set_to_one_round_permits_exactly_one(world, monkeypatch):
    rounds_allowed(world, monkeypatch, 1)


# ── one store per workspace, even when the first requests race ──


def test_concurrent_first_opens_of_a_workspace_share_one_store(monkeypatch, tmp_path):
    threads_n = 8
    cond = threading.Condition()
    seen = {"checks": 0}

    class Watched(dict):
        """Counts every look-up, so a store can wait until all threads have looked."""

        def _count(self):
            with cond:
                seen["checks"] += 1
                cond.notify_all()

        def __contains__(self, key):
            self._count()
            return super().__contains__(key)

        def get(self, key, default=None):
            self._count()
            return super().get(key, default)

    built = []
    real = review_store.ReviewStore

    class Slow(real):
        def __init__(self, directory):
            # Hold the first construction until every thread has looked for the store; without
            # the lock each of them saw none and builds its own.
            with cond:
                cond.wait_for(lambda: seen["checks"] >= threads_n, timeout=10)
            built.append(self)
            super().__init__(directory)

    monkeypatch.setattr(review_store, "ReviewStore", Slow)
    gateway = types.SimpleNamespace(
        supervisor=types.SimpleNamespace(workspace_dir=lambda _ws: tmp_path),
        _pr_review_stores=Watched(),
    )
    start_line = threading.Barrier(threads_n)
    got = []

    def open_one():
        start_line.wait()
        got.append(review_store.open_review(gateway, "ws1"))

    workers = [threading.Thread(target=open_one) for _ in range(threads_n)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert len(built) == 1
    assert len(got) == threads_n and all(store is got[0] for store in got)


# ── slice 2: reads arriving out of order, the transaction helper, closing, reply modes ──


def test_an_older_read_arriving_late_never_rolls_the_pr_back(world):
    h = world
    post(h, batch(head=FIX, fetched_at=when(-1), changed_files=["app/main.py"]))
    status, body, _ = post(h, batch(comment(1), head=HEAD, fetched_at=when(-10), pr_state="closed"))
    assert status == 200 and body["accepted"] == 1  # its comments still count
    got = h.store.pr(REPO, 7)
    assert (got["head"], got["pr_state"], got["changed_head"]) == (FIX, "open", FIX)
    assert got["fetched_at"] > when(-2)[:16]


def test_store_writes_use_the_shared_bounded_begin(world, monkeypatch):
    h = world
    calls = []
    real = review_store.begin_immediate
    monkeypatch.setattr(review_store, "begin_immediate", lambda conn: calls.append(1) or real(conn))
    post(h, batch(comment(1)))
    assert calls


def test_closing_the_hub_closes_every_review_store(world):
    h = world
    post(h, batch(comment(1)))
    first = review_store.open_review(h.gateway, h.workspace)
    assert first._conn is not None
    review_store.close_all(h.gateway)
    assert first._conn is None
    second = review_store.open_review(h.gateway, h.workspace)
    assert second is not first and len(second.comments(REPO, 7)) == 1


def test_a_file_from_slice_one_gets_the_new_columns_once(tmp_path):
    from mempalace.sqlite_crypto import connect

    conn = connect(str(tmp_path / review_store.FILENAME))
    conn.executescript(
        "CREATE TABLE pr_review_prs (repo TEXT NOT NULL, pr INTEGER NOT NULL, "
        "created_at TEXT, updated_at TEXT, PRIMARY KEY (repo, pr));"
        "CREATE TABLE pr_review_comments (id INTEGER PRIMARY KEY, repo TEXT, pr INTEGER, "
        "status TEXT);"
        "CREATE TABLE pr_review_boards (board TEXT PRIMARY KEY, updated_at TEXT);"
    )
    conn.close()
    store = review_store.ReviewStore(tmp_path)
    assert store.pr(REPO, 7) is None
    columns = {
        table: {row[1] for row in store._conn.execute(f"PRAGMA table_info({table})")}
        for table in ("pr_review_prs", "pr_review_comments", "pr_review_boards")
    }
    assert "public" in columns["pr_review_prs"]
    assert "reply_posted" in columns["pr_review_comments"]
    assert "post_replies" in columns["pr_review_boards"]
    store.close()
    again = review_store.ReviewStore(tmp_path)
    assert again.pr(REPO, 7) is None  # a second open adds nothing and fails nothing
    again.close()


@pytest.mark.parametrize(
    "public, post_replies, mode",
    [(None, False, "show"), (True, False, "show"), (False, False, "post"), (True, True, "post")],
)
def test_replies_are_shown_on_the_card_unless_the_repository_is_private_or_the_owner_allows(
    public, post_replies, mode
):
    pr_row = None if public is None else {"public": public}
    assert review_store.reply_mode(pr_row, {"post_replies": post_replies}) == mode


def test_only_the_owner_switches_reply_posting_on_and_it_is_kept(world):
    h = world
    path = "/api/pr-review/boards/work"
    assert h.request(h.owner, "PUT", path, {"enabled": True, "post_replies": "yes"})[0] == 400
    status, body, _ = h.request(h.owner, "PUT", path, {"enabled": True, "post_replies": True})
    assert status == 200 and body["post_replies"] is True
    h.request(h.owner, "PUT", path, {"enabled": True, "rounds": 3})
    assert h.request(h.owner, "GET", path)[1]["post_replies"] is True


def test_a_dry_run_refuses_a_posted_reply_and_records_the_reply_on_the_card(world):
    h = world
    number = start(h)
    take(h, number)
    report = {"comment": 1, "rev": 1, "outcome": "answered", "reply": "On purpose."}
    ok, text = mcp(h, h.t1, "pr_review_report", card=number, reports=[{**report, "posted": True}])
    assert not ok and "dry_run" in text
    ok, text = mcp(h, h.t1, "pr_review_report", card=number, reports=[report])
    assert ok, text
    shown = view(h, number)[1]
    assert shown["pr_info"]["reply_mode"] == "show"
    one = next(c for c in shown["comments"] if c["gh_id"] == "1")
    assert one["reply"] == "On purpose." and one["reply_posted"] is False


def test_the_start_context_shows_waiting_comments_with_the_rules(world):
    h = world
    from commonplace.extensions import ToolContext
    from commonplace.runner import start_context

    number = start(h)
    ctx = ToolContext(principal=h.principal(h.t1), gateway=h.gateway)
    text = start_context.assemble(start_context.load(ctx, number))
    assert "Review comments on PR #7 (acme/widgets), waiting for you:" in text
    assert "pr_review_take(card=" in text and "never merge" in text.lower()
    assert json.dumps("Why this approach?") in text
    assert "Rounds: 0 of 2 used on this PR." in text
    other = card(h, title="Nothing waiting", pr_url="https://github.com/acme/widgets/pull/8")
    assert "Review comments" not in start_context.assemble(start_context.load(ctx, other))


def test_a_broken_review_read_never_stops_a_start_and_logs_no_words(world, monkeypatch, caplog):
    h = world
    from commonplace.extensions import ToolContext
    from commonplace.pr_review import context
    from commonplace.runner import start_context

    number = start(h)

    def boom(ctx, n):
        raise RuntimeError("store exploded: Why this approach?")

    monkeypatch.setattr(context, "for_card", boom)
    ctx = ToolContext(principal=h.principal(h.t1), gateway=h.gateway)
    with caplog.at_level(logging.DEBUG):
        text = start_context.assemble(start_context.load(ctx, number))
    assert "Card: #" in text and "Review comments on PR" not in text
    assert "Why this approach?" not in caplog.text


# ── card #629: a refused report batch stores nothing ──


@pytest.mark.parametrize(
    "bad, fragment",
    [
        ({"comment": 2, "rev": 1, "outcome": "answered", "reply": "x", "posted": True}, "dry_run"),
        ({"comment": 2, "rev": 9, "outcome": "answered", "reply": "x"}, "comment_changed"),
        ({"comment": 2, "rev": 1, "outcome": "addressed"}, "needs the commit"),
        ({"comment": 999, "rev": 1, "outcome": "answered", "reply": "x"}, "not found"),
    ],
)
def test_a_report_batch_with_one_refused_report_stores_none_of_it(world, bad, fragment):
    h = world
    number = start(h)
    take(h, number)
    good = {"comment": 1, "rev": 1, "outcome": "answered", "reply": "Kept on purpose."}
    ok, text = mcp(h, h.t1, "pr_review_report", card=number, reports=[good, bad])
    assert not ok and fragment in text, text
    one = rows(h)["1"]
    assert (one["status"], one["reply"]) == ("working", None)
    assert [e["kind"] for e in h.store.history(REPO, 7) if e["comment_id"] == one["id"]] == [
        "arrived",
        "taken",
    ]
    ok, text = mcp(h, h.t1, "pr_review_report", card=number, reports=[good])
    assert ok and rows(h)["1"]["status"] == "answered"


# ── slice 3: the three answers, wherever they are given, and the reader's status ──


def asked_card(h, monkeypatch, rounds=2):
    """A card held by its Coworker that asked the owner about two comments it cannot address."""
    configured(monkeypatch)
    board_on(h, rounds=rounds)
    number = start(h)
    take(h, number)
    ok, text = mcp(
        h,
        h.t1,
        "pr_review_report",
        card=number,
        reports=[
            {"comment": 1, "rev": 1, "outcome": "needs_you", "note": "A product decision."},
            {"comment": 2, "rev": 1, "outcome": "needs_you", "note": "Also a person's call."},
            {"comment": 3, "rev": 1, "outcome": "answered", "reply": "Done as asked."},
        ],
    )
    assert ok, text
    return number


@pytest.mark.parametrize(
    "rounds, label", [(1, "One more round"), (2, "Two more rounds"), (3, "Three more rounds")]
)
def test_the_question_offers_the_three_answers_in_plain_words(world, monkeypatch, rounds, label):
    h = world
    number = asked_card(h, monkeypatch, rounds)
    ok, shown = mcp(h, h.t1, "task_show", task=number)
    assert f'options: ["{label}", "I will do it myself", "Ignore"]' in shown


@pytest.mark.parametrize(
    "choice, status, reason, card_status",
    [
        ("again", "new", None, "todo"),
        ("mine", "skipped", "handled_by_person", "backlog"),
        ("dismiss", "skipped", "dismissed_by_person", "todo"),
    ],
)
def test_an_answer_in_the_section_answers_the_cards_question_too(
    world, monkeypatch, choice, status, reason, card_status
):
    h = world
    number = asked_card(h, monkeypatch)
    path = f"/api/pr-review/cards/{number}/decision"
    code, body, _ = h.request(h.owner, "POST", path, {"choice": choice})
    assert code == 200 and body["card_answered"] is True, body
    assert {(rows(h)[k]["status"], rows(h)[k]["reason"]) for k in ("1", "2")} == {(status, reason)}
    ok, shown = mcp(h, h.t1, "task_show", task=number)
    assert shown.split()[1] == card_status and "asks owner" not in shown.split("\n")[0]
    trigger.run(h.gateway, h.workspace)  # the answer is not applied a second time
    assert h.store.pr(REPO, 7)["decided_event"] > 0
    assert (
        len([e for e in h.store.history(REPO, 7) if e["kind"] in ("again", "mine", "dismissed")])
        == 2
    )


def test_an_answer_given_on_the_cards_question_reaches_the_comments_once(world, monkeypatch):
    h = world
    number = asked_card(h, monkeypatch)
    owner = service(h, h.person(h.admin))
    owner.decide(number, choice="Two more rounds", then="return")
    assert {rows(h)[k]["status"] for k in ("1", "2")} == {"needs_you"}  # not yet: the next pass
    trigger.run(h.gateway, h.workspace)
    assert {rows(h)[k]["status"] for k in ("1", "2")} == {"new"}
    assert h.store.pr(REPO, 7)["rounds_used"] == 0
    before = len(h.store.history(REPO, 7))
    trigger.run(h.gateway, h.workspace)
    assert len(h.store.history(REPO, 7)) == before


def test_an_unrelated_answer_on_the_card_changes_no_comment(world, monkeypatch):
    h = world
    number = asked_card(h, monkeypatch)
    owner = service(h, h.person(h.admin))
    owner.decide(number, reply="Carry on with the other work first.", then="return")
    trigger.run(h.gateway, h.workspace)
    assert {rows(h)[k]["status"] for k in ("1", "2")} == {"needs_you"}


def test_an_unrelated_question_with_the_same_answer_changes_no_comment(world, monkeypatch):
    h = world
    number = asked_card(h, monkeypatch)
    owner = service(h, h.person(h.admin))
    owner.decide(
        number, reply="Keep these comments waiting while we discuss the name.", then="return"
    )
    owner.ask(
        "Should we rename the project?",
        ["Ignore", "Rename"],
        task=number,
        person_reason="The project name needs its owner's decision.",
    )
    owner.decide(number, choice="Ignore", then="return")
    trigger.run(h.gateway, h.workspace)
    assert {rows(h)[k]["status"] for k in ("1", "2")} == {"needs_you"}


@pytest.mark.parametrize("older", [False, True])
def test_replaying_a_card_answer_cannot_discard_a_later_comment_revision(world, monkeypatch, older):
    h = world
    number = asked_card(h, monkeypatch)
    assert (
        h.request(
            h.owner, "POST", f"/api/pr-review/cards/{number}/decision", {"choice": "dismiss"}
        )[0]
        == 200
    )
    event_id = h.store.pr(REPO, 7)["decided_event"]
    assert event_id > 1
    assert (
        post(h, batch(comment(1, "Please also rename the helper.", updated_at=when(-1))))[0] == 200
    )
    assert rows(h)["1"]["status"] == "new"
    before = h.store.history(REPO, 7)
    h.store.decide(
        REPO, 7, "dismiss", "person", datetime.now(timezone.utc), event_id=event_id - int(older)
    )
    assert rows(h)["1"]["status"] == "new"
    assert h.store.history(REPO, 7) == before


def test_the_reader_status_says_what_the_owner_waits_for_without_any_token(world, monkeypatch):
    h = world
    path = "/api/pr-review/status"
    assert h.request(h.member, "GET", path)[0] == 403
    assert h.request(h.t1, "GET", path)[0] == 403
    # the fixture minted a feeder token: it exists, nothing was read yet
    code, body, raw = h.request(h.owner, "GET", path)
    assert code == 200 and body["reader"] == {"state": "waiting_first_read", "last_read_at": None}
    assert h.feeder.encode() not in raw
    post(h, batch(comment(1)))
    assert h.request(h.owner, "GET", path)[1]["reader"]["state"] == "reading"
    later = datetime.now(timezone.utc) + timedelta(minutes=13)
    monkeypatch.setattr(routes, "_now", lambda: later)
    assert h.request(h.owner, "GET", path)[1]["reader"]["state"] == "stale"
    for row in h.db.connections(h.workspace):
        if "pr_review:write" in (row["policy"] or ""):
            h.db.revoke(row["id"], actor=h.admin, workspace_ref=h.workspace)
    assert h.request(h.owner, "GET", path)[1]["reader"]["state"] == "no_token"
