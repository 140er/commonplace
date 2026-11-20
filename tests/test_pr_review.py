"""Auto-addressed PR review comments, slice 1: intake, mapping, statuses, API, tools, trigger.

Real local HTTP and real MCP calls over the console fixture. No call leaves the machine: the hub
never talks to GitHub, so the feeder is played by these tests posting to the intake route.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from commonplace.control_db import ControlError
from commonplace.pr_review import intake, routes, status as st, store as review_store, trigger
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
    return mcp(h, token or h.t1, "pr_review_list", card=number, take=True)


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
    assert {"pr_review_list", "pr_review_report"} <= names
    assert not {
        n
        for n in names
        if n.startswith("pr_review") and any(w in n for w in ("merge", "resolve", "dismiss"))
    }
    schema = next(
        t for t in h.rpc(h.t1, "tools/list")["result"]["tools"] if t["name"] == "pr_review_report"
    )
    item = schema["inputSchema"]["properties"]["reports"]["items"]
    assert set(item["properties"]) == {"comment", "rev", "outcome", "commit", "reply", "note"}
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
