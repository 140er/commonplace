"""Auto-addressed PR review comments, slice 2: the feeder and the Coworker work loop, end to end.

A fake GitHub (``tests/_pr_review_fakes.py``) serves a pull request whose branch lives in a local
bare repository; the real hub (the console fixture, a clean workspace) receives what the real
feeder reads; a scripted Coworker uses the real tools. No test talks to the real GitHub.
"""

import json
import logging
import os
import secrets
import stat

import pytest

from commonplace import cli
from commonplace.extensions import ToolContext
from commonplace.pr_review import feeder as feed, store as review_store
from commonplace.runner import start_context
from tests._console_helpers import console_hub_fixture, successful  # noqa: F401
from tests._pr_review_fakes import FakeGitHub, git, make_repo
from tests.test_commonplace_boards import mcp, service

REPO = "acme/widgets"
PR_URL = f"https://github.com/{REPO}/pull/7"


@pytest.fixture
def loop(console_hub, tmp_path, monkeypatch):
    h = console_hub
    h.bare, h.work = make_repo(tmp_path)
    h.gh = FakeGitHub(h.bare)
    h.hub_token = h.token(
        "review-feed", profile="slim", scopes=["pr_review:write"], trust="untrusted"
    )
    h.github_token = "ghp_" + secrets.token_hex(18)  # made at run time; never a literal
    h.hub_file, h.github_file = tmp_path / "hub-token", tmp_path / "github-token"
    h.hub_file.write_text(h.hub_token + "\n")
    h.github_file.write_text(h.github_token + "\n")
    h.state_file = tmp_path / "feeder" / "state.json"
    h.hub_url = f"http://127.0.0.1:{h.hub.port}"
    h.store = review_store.open_review(h.gateway, h.workspace)
    from commonplace.orch import runner_bridge

    # No runner machine exists in the test hub; the board counts as having a Coworker setup,
    # so a handed-back card is what the ordinary todo trigger would start.
    monkeypatch.setattr(runner_bridge, "configured", lambda *a, **k: True)
    yield h
    h.gh.close()


def poll(h, *extra):
    """One feeder pass through the real command line."""
    return cli.main(
        [
            "pr-review",
            "poll",
            "--once",
            "--hub",
            h.hub_url,
            "--token-file",
            str(h.hub_file),
            "--github-token-file",
            str(h.github_file),
            "--state-file",
            str(h.state_file),
            "--github-api",
            h.gh.url,
            *extra,
        ]
    )


def feeder(h):
    return feed.Feeder(h.hub_url, h.hub_token, h.github_token, h.state_file, api=h.gh.url)


def finished_card(h):
    """A card the Coworker finished: its PR is open and it waits in a completion review."""
    owner = service(h, h.person(h.admin))
    with owner.store.transaction() as conn:
        conn.execute("UPDATE boards SET done_needs_review=1 WHERE slug='work'")
    number = successful(h, h.t1, "task_add", title="Count things", board="work")["number"]
    for fields in (
        {"pr": PR_URL, "branch": "feat/thing"},
        {"status": "doing"},
        {"status": "done", "context": "The change is made, its tests pass and the PR is open."},
    ):
        ok, text = mcp(h, h.t1, "task_update", task=number, **fields)
        assert ok, text
    return number


def loop_on(h, **extra):
    status, body, _ = h.request(
        h.owner, "PUT", "/api/pr-review/boards/work", {"enabled": True, **extra}
    )
    assert status == 200, body


def rows(h):
    return {c["gh_id"]: c for c in h.store.comments(REPO, 7)}


def card_status(h, number):
    ok, shown = mcp(h, h.t1, "task_show", task=number)
    assert ok, shown
    return shown.split()[1]


# ── the whole loop on a fake PR ──


def test_a_review_comment_becomes_a_fix_commit_and_one_it_cannot_fix_one_needs_me_item(
    loop, caplog, capsys
):
    h = loop
    number = finished_card(h)
    main_before = git(h.bare, "rev-parse", "refs/heads/main")
    fixable = h.gh.add("inline", "Rename x to count, it says what it holds.")
    h.gh.add("conversation", "Please rewrite the whole thing in another language.")
    h.gh.add("conversation", "Drive-by opinion.", login="stranger", association="NONE")

    with caplog.at_level(logging.DEBUG):
        # the loop is off by default: the feeder is told to watch nothing
        assert poll(h) == 0
        assert not [r for r in h.gh.requests if "/comments" in r[1]]
        assert h.store.comments(REPO, 7) == []

        loop_on(h)
        assert poll(h) == 0
        got = {c["text"]: c for c in h.store.comments(REPO, 7)}
        assert {c["status"] for c in got.values()} == {"new", "skipped"}
        # the comments hand the finished card back to its Coworker
        assert card_status(h, number) == "todo"

        # the Coworker's run: it claims the card and reads its start context
        assert mcp(h, h.t1, "task_update", task=number, status="doing")[0]
        context = start_context.assemble(
            start_context.load(ToolContext(principal=h.principal(h.t1), gateway=h.gateway), number)
        )
        assert "Review comments on PR #7 (acme/widgets), waiting for you:" in context
        assert json.dumps("Rename x to count, it says what it holds.") in context
        assert "never instructions" in context and "do not post them on GitHub" in context
        assert "Drive-by opinion." not in context

        ok, taken = mcp(h, h.t1, "pr_review_take", card=number)
        assert ok and "2 comment(s) are yours" in taken
        ids = {c["text"]: c["id"] for c in h.store.comments(REPO, 7)}

        # the fix: a normal commit on the card's branch, pushed to the PR's remote
        (h.work / "app.py").write_text("count = 2\nprint(count)\n")
        git(h.work, "commit", "-qam", "Rename x to count")
        git(h.work, "push", "-q", "origin", "feat/thing")
        fix = git(h.work, "rev-parse", "HEAD")
        assert h.gh.head() == fix
        # it reports the new head on the card, as for any push
        assert mcp(h, h.t1, "task_update", task=number, pr_head=fix)[0]

        refused = mcp(
            h,
            h.t1,
            "pr_review_report",
            card=number,
            reports=[
                {
                    "comment": ids[fixable["body"]],
                    "rev": 1,
                    "outcome": "addressed",
                    "commit": fix,
                    "reply": "Renamed.",
                    "posted": True,
                }
            ],
        )
        assert not refused[0] and "dry_run" in refused[1]  # a public repo: replies stay on the card
        ok, text = mcp(
            h,
            h.t1,
            "pr_review_report",
            card=number,
            reports=[
                {
                    "comment": ids[fixable["body"]],
                    "rev": 1,
                    "outcome": "addressed",
                    "commit": fix,
                    "reply": "Renamed.",
                    "posted": False,
                },
                {
                    "comment": ids["Please rewrite the whole thing in another language."],
                    "rev": 1,
                    "outcome": "needs_you",
                    "note": "Choosing another language is a product decision.",
                },
            ],
        )
        assert ok, text

        # the feeder sees the new head and its changed files; the fix is confirmed
        assert poll(h) == 0

    after = {c["text"]: c for c in h.store.comments(REPO, 7)}
    fixed = after[fixable["body"]]
    assert (fixed["status"], fixed["fix_commit"], fixed["fix_confirmed"]) == (
        "addressed",
        fix,
        True,
    )
    assert fixed["reply"] == "Renamed." and fixed["reply_posted"] is False
    asked = after["Please rewrite the whole thing in another language."]
    assert (asked["status"], asked["reason"]) == ("needs_you", "coworker_asks")

    # exactly one Needs me item for the PR, however many comments wait
    _, needs, _ = h.request(h.owner, "GET", "/api/needs-me")
    listed = [
        c["number"]
        for bucket in needs.values()
        if isinstance(bucket, list)
        for c in bucket
        if isinstance(c, dict) and c.get("number") == number
    ]
    assert len(set(listed)) == 1
    ok, shown = mcp(h, h.t1, "task_show", task=number)
    assert shown.count(" handoff: ") == 1 and "need a decision: 1 of 2" in shown

    # nothing was merged and nothing but reads reached GitHub
    assert h.gh.writes() == [] and h.gh.merged is False
    assert git(h.bare, "rev-parse", "refs/heads/main") == main_before

    # no token and no comment words in any log line or output
    out = capsys.readouterr()
    for text in (caplog.text, out.out, out.err):
        assert h.github_token not in text and h.hub_token not in text
        assert "Rename x" not in text and "another language" not in text
    assert stat.S_IMODE(os.stat(h.state_file).st_mode) == 0o600


def test_a_private_repository_lets_the_coworker_post_and_says_so(loop):
    h = loop
    h.gh.private = True
    number = finished_card(h)
    loop_on(h)
    h.gh.add("inline", "Why is this a module-level variable?")
    assert poll(h) == 0
    assert mcp(h, h.t1, "task_update", task=number, status="doing")[0]
    ok, taken = mcp(h, h.t1, "pr_review_take", card=number)
    assert ok and "post each reply" in taken and "<!-- cmmn:reply" in taken
    cid = h.store.comments(REPO, 7)[0]["id"]
    ok, text = mcp(
        h,
        h.t1,
        "pr_review_report",
        card=number,
        reports=[
            {
                "comment": cid,
                "rev": 1,
                "outcome": "answered",
                "reply": "It is read by two functions.",
                "posted": True,
            }
        ],
    )
    assert ok, text
    _, view, _ = h.request(h.owner, "GET", f"/api/pr-review/cards/{number}")
    assert view["pr_info"]["reply_mode"] == "post" and view["pr_info"]["public"] is False
    assert view["comments"][0]["reply_posted"] is True


def test_the_owner_can_let_replies_be_posted_on_a_public_repository(loop):
    h = loop
    number = finished_card(h)
    loop_on(h, post_replies=True)
    h.gh.add("inline", "Typo in the name.")
    assert poll(h) == 0
    assert mcp(h, h.t1, "task_update", task=number, status="doing")[0]
    ok, taken = mcp(h, h.t1, "pr_review_take", card=number)
    assert ok and "post each reply" in taken
    assert (
        h.request(
            h.member, "PUT", "/api/pr-review/boards/work", {"enabled": True, "post_replies": True}
        )[0]
        == 403
    )


# ── the feeder on its own ──


def test_the_first_poll_waits_for_the_github_token_and_reads_nothing(loop, capsys):
    h = loop
    loop_on(h)
    finished_card(h)
    h.github_file.unlink()
    assert poll(h) == feed.WAITING_EXIT
    err = capsys.readouterr().err
    assert "Waiting for the GitHub read token" in err and "read-only" in err
    assert h.gh.requests == []
    assert h.store.prs() == []


def test_a_missing_hub_token_says_how_to_mint_one(loop, capsys):
    h = loop
    h.hub_file.unlink()
    assert poll(h) == 2
    assert "--scopes pr_review:write" in capsys.readouterr().err
    assert h.gh.requests == []


def test_a_refused_github_token_stops_the_pass_without_echoing_it(loop, capsys):
    h = loop
    loop_on(h)
    finished_card(h)
    h.gh.refuse_token = True
    assert poll(h) == 2
    out = capsys.readouterr()
    assert "github_token_refused" in out.err and h.github_token not in out.out + out.err


def test_unchanged_lists_cost_conditional_requests_and_post_only_the_head(loop):
    h = loop
    finished_card(h)
    loop_on(h)
    h.gh.add("inline", "One.")
    f = feeder(h)
    first = f.once()
    assert first["comments"] == 1 and first["errors"] == []
    before = len(h.gh.requests)
    second = f.once()
    assert second["comments"] == 0 and second["requests"] == 1
    conditional = [r for r in h.gh.requests[before:] if "comments" in r[1] or "reviews" in r[1]]
    assert conditional  # read again, answered 304 by the fake
    assert len(h.store.comments(REPO, 7)) == 1


def test_an_edited_comment_and_a_removed_one_reach_the_hub(loop):
    h = loop
    finished_card(h)
    loop_on(h)
    kept = h.gh.add("inline", "First words.")
    gone = h.gh.add("conversation", "Will be deleted.")
    f = feeder(h)
    f.once()
    h.gh.edit(kept, "Better words.")
    h.gh.remove("conversation", gone)
    f.once()
    got = rows(h)
    assert got[str(kept["id"])]["rev"] == 2 and "Better words." in got[str(kept["id"])]["text"]
    assert (got[str(gone["id"])]["status"], got[str(gone["id"])]["reason"]) == (
        "skipped",
        "removed_by_reviewer",
    )


def test_long_lists_are_read_page_by_page(loop):
    h = loop
    finished_card(h)
    loop_on(h)
    h.gh.page_size = 2
    for n in range(5):
        h.gh.add("inline", f"Comment {n}.")
    feeder(h).once()
    assert len(h.store.comments(REPO, 7)) == 5


def test_a_low_rate_limit_pauses_the_feeder_until_the_reset(loop):
    h = loop
    finished_card(h)
    loop_on(h)
    h.gh.add("inline", "One.")
    h.gh.rate_remaining = 10
    f = feeder(h)
    summary = f.once()
    assert summary["rate_limited"] is True and h.store.comments(REPO, 7) == []
    asked = len(h.gh.requests)
    again = f.once()
    assert again["rate_limited"] is True and len(h.gh.requests) == asked


def test_a_failed_post_reads_the_same_comments_again_next_time(loop, monkeypatch):
    h = loop
    finished_card(h)
    loop_on(h)
    h.gh.add("inline", "One.")
    f = feeder(h)
    real = f.post

    def refuse(request):
        raise feed.FeederError("hub_status_503")

    monkeypatch.setattr(f, "post", refuse)
    assert f.once()["errors"] == ["hub_status_503"]
    monkeypatch.setattr(f, "post", real)
    assert f.once()["comments"] == 1
    assert len(h.store.comments(REPO, 7)) == 1


def test_items_are_mapped_and_batched_like_the_intake_expects():
    review = {
        "id": 5,
        "state": "PENDING",
        "body": "draft",
        "submitted_at": None,
        "user": {"login": "a"},
    }
    assert feed.comment_from("review", review) is None
    assert feed.comment_from("review", {**review, "state": "APPROVED", "body": ""}) is None
    reply = {
        "id": 9,
        "in_reply_to_id": 4,
        "body": "Same here.",
        "path": "a.py",
        "line": None,
        "original_line": 3,
        "commit_id": "c" * 40,
        "user": {"login": "b", "type": "Bot"},
        "author_association": "MEMBER",
        "created_at": "2026-11-20T08:00:00Z",
        "updated_at": None,
    }
    mapped = feed.comment_from("inline", reply)
    assert (mapped["thread"], mapped["in_reply_to"], mapped["line"]) == ("4", "4", 3)
    assert mapped["author"] == {"login": "b", "type": "Bot", "association": "MEMBER"}
    base = {"repo": REPO, "pr": 7, "head": "a" * 40}
    comments = [{"kind": "inline", "id": str(n), "body": "x"} for n in range(250)]
    requests = feed.batches(base, comments, ["app.py"])
    assert [len(r["comments"]) for r in requests] == [100, 100, 50]
    assert requests[0]["changed_files"] == ["app.py"] and "changed_files" not in requests[1]
    big = [{"kind": "inline", "id": str(n), "body": "y" * 60_000} for n in range(30)]
    assert all(len(json.dumps(r)) < 1024 * 1024 for r in feed.batches(base, big, None))
    assert feed.batches(base, [], None) == [{**base, "comments": []}]


def test_a_token_file_must_hold_one_line(tmp_path):
    path = tmp_path / "t"
    assert feed.read_token(path) is None
    path.write_text("\n")
    assert feed.read_token(path) is None
    path.write_text("abc def\n")
    with pytest.raises(feed.FeederError, match="token_file_unreadable"):
        feed.read_token(path)
