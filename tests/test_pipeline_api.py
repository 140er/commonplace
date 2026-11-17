"""``/api/pipeline``: authentication, validation, idempotency, workspace isolation, the example."""

from datetime import datetime, timedelta, timezone

import pytest

from commonplace.control_db import ControlError
from commonplace.pipeline import routes, store
from commonplace.policy import Policy
from tests import (
    test_commonplace_rules_enforce as rules_tests,
    test_spaces_contract as contract_tests,
)
from tests.test_pipeline_compute import step

env = rules_tests.env
http = contract_tests.http

REPO = "acme/widgets"
TOKENS = {}


@pytest.fixture
def world(env, tmp_path, monkeypatch):
    """The stock hub, but each workspace gets its own directory (the stock one shares one).
    The server clock stands still, so two reads of an open PR give the same numbers."""
    fixed = datetime.now(timezone.utc)
    monkeypatch.setattr(routes, "_now", lambda: fixed)
    monkeypatch.delenv("COMMONPLACE_REPO_ALIASES", raising=False)
    TOKENS["pipeline"] = pipeline_token(env, "deploy-feed")
    env.gateway.supervisor.workspace_dir = lambda workspace_id: tmp_path / "ws" / workspace_id
    return env


def when(seconds=0):
    return (
        datetime.now(timezone.utc) - timedelta(minutes=30) + timedelta(seconds=seconds)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def event(ident, step_name="gate", state="pass", seconds=0, **extra):
    return {"id": ident, "step": step_name, "state": state, "at": when(seconds), **extra}


def body(*events, repo=REPO, pr=7, title="Add a thing"):
    return {"repo": repo, "pr": pr, "title": title, "events": list(events)}


def story():
    return body(
        event("c1", "card", "running", 0),
        event("c2", "card", "pass", 30),
        event("g1", "gate", "running", 60),
        event(
            "g2", "gate", "fail", 100, ref="https://example.test/verdict/1", detail="one finding"
        ),
        event("g3", "gate", "running", 200),
        event("g4", "gate", "pass", 260),
    )


def pipeline_token(env, name, workspace=None, user=None):
    """The deploy script's token: the pipeline scope alone, as the owner mints it."""
    _, token = env.db.create_token(
        workspace_ref=workspace or env.workspace,
        user_ref=user or env.user,
        name=name,
        client="deploy",
        policy=Policy.build(profile="slim", scopes=["pipeline:write"], trust="untrusted"),
        actor=user or env.user,
    )
    return token


def post(http, payload, **kw):
    """Record events the way the deploy script does: with its own token unless told otherwise."""
    if not kw.get("anonymous") and "token" not in kw and "principal" not in kw:
        kw["token"] = TOKENS["pipeline"]
    return http("POST", "/api/pipeline/events", payload, **kw)


def other_workspace(env):
    """A second workspace: (its pipeline token, a reading agent token)."""
    user = env.db.create_user("other-owner@example.test")
    workspace = env.db.create_workspace("other-workspace", user)
    feeder = pipeline_token(env, "other-feed", workspace, user)
    _, token = env.db.create_token(
        workspace_ref=workspace,
        user_ref=user,
        name="other-agent",
        client="test",
        policy=Policy.build(
            profile="full",
            scopes=["memory:read", "memory:write"],
            read_wings=["*"],
            write_wings=["*"],
            trust="trusted",
        ),
        actor=user,
    )
    return feeder, token


def reader(env):
    _, token = env.db.create_token(
        workspace_ref=env.workspace,
        user_ref=env.user,
        name="reader",
        client="test",
        policy=Policy.build(
            profile="slim",
            scopes=["memory:read"],
            read_wings=["*"],
            write_wings=[],
            trust="trusted",
        ),
        actor=env.user,
    )
    return token


# ── Record and read ──────────────────────────────────────────────────────
def test_events_are_recorded_and_the_server_computes_the_steps(world, http):
    status, answer, _ = post(http, story())
    assert (status, answer) == (200, {"repo": REPO, "pr": 7, "accepted": 6, "duplicates": 0})
    status, listing, _ = http("GET", "/api/pipeline/prs")
    assert status == 200 and listing["demo"] is False and len(listing["prs"]) == 1
    row = listing["prs"][0]
    assert (row["repo"], row["pr"], row["title"]) == (REPO, 7, "Add a thing")
    gate = step(row, "gate")
    # The retry: the last run is shown, two attempts counted.
    assert (gate["state"], gate["attempts"], gate["duration"]) == ("pass", 2, 60)
    assert row["lead"]["in_progress"] is True and row["current_step"] == "check"
    status, one, _ = http("GET", f"/api/pipeline/prs/{REPO}/7")
    assert status == 200 and one["steps"] == row["steps"]
    # The same PR with the slash encoded.
    status, again, _ = http("GET", "/api/pipeline/prs/acme%2Fwidgets/7")
    assert status == 200 and again["pr"] == 7


def test_a_later_request_adds_events_and_a_new_title(world, http):
    post(http, story())
    post(http, body(event("k1", "check", "running", 300), title="Renamed"))
    _, one, _ = http("GET", f"/api/pipeline/prs/{REPO}/7")
    assert one["title"] == "Renamed" and step(one, "check")["state"] == "running"
    post(http, body(event("k2", "check", "pass", 330), title=""))
    _, one, _ = http("GET", f"/api/pipeline/prs/{REPO}/7")
    assert one["title"] == "Renamed" and step(one, "check")["state"] == "pass"


def test_the_list_is_newest_activity_first_and_limited(world, http):
    for pr, offset in ((1, 0), (2, 600), (3, 300)):
        post(http, body(event(f"e{pr}", "card", "pass", offset), pr=pr))
    _, listing, _ = http("GET", "/api/pipeline/prs")
    assert [row["pr"] for row in listing["prs"]] == [2, 3, 1]
    _, listing, _ = http("GET", "/api/pipeline/prs?limit=2")
    assert [row["pr"] for row in listing["prs"]] == [2, 3]


@pytest.mark.parametrize("limit", ["0", "101", "x", "-1", ""])
def test_a_bad_limit_is_a_plain_400(world, http, limit):
    status, answer, _ = http("GET", f"/api/pipeline/prs?limit={limit}")
    assert status == 400 and "limit" in answer["error"]


def test_a_pr_nothing_is_recorded_for_is_404(world, http):
    assert http("GET", f"/api/pipeline/prs/{REPO}/99")[0] == 404
    assert http("GET", f"/api/pipeline/prs/{REPO}")[0] == 404
    assert http("GET", f"/api/pipeline/prs/{REPO}/x")[0] == 404


# ── Idempotency ──────────────────────────────────────────────────────────
def test_an_event_id_already_recorded_is_a_no_op(world, http):
    post(http, story())
    before = http("GET", f"/api/pipeline/prs/{REPO}/7")[1]
    status, answer, _ = post(http, story())
    assert (status, answer["accepted"], answer["duplicates"]) == (200, 0, 6)
    # A known id with different content changes nothing either.
    status, answer, _ = post(http, body(event("g4", "gate", "fail", 999), title="Changed"))
    assert (answer["accepted"], answer["duplicates"]) == (0, 1)
    assert http("GET", f"/api/pipeline/prs/{REPO}/7")[1] == before


def test_a_repeated_id_inside_one_request_counts_once(world, http):
    status, answer, _ = post(http, body(event("a", "card", "pass"), event("a", "card", "pass")))
    assert (status, answer["accepted"], answer["duplicates"]) == (200, 1, 1)


def test_the_same_id_on_another_pr_is_its_own_event(world, http):
    post(http, body(event("same", "card", "pass"), pr=1))
    status, answer, _ = post(http, body(event("same", "card", "pass"), pr=2))
    assert answer["accepted"] == 1


# ── Validation ───────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "mutate, words",
    [
        (lambda b: b["events"][0].update(step="lunch"), "unknown step"),
        (lambda b: b["events"][0].update(state="done"), "unknown state"),
        (lambda b: b["events"][0].update(at="tomorrow"), "ISO 8601"),
        (lambda b: b["events"][0].update(at=1700000000), "ISO 8601"),
        (lambda b: b["events"][0].update(id=""), "id"),
        (lambda b: b["events"][0].update(id="has space"), "id"),
        (lambda b: b["events"][0].update(extra=1), "id, step, state"),
        (lambda b: b["events"][0].update(detail="x" * 301), "300"),
        (lambda b: b["events"][0].update(ref="y" * 301), "300"),
        (lambda b: b["events"][0].update(detail="line\x00break"), "plain text"),
        (lambda b: b["events"][0].update(detail="password = Hunter2hunter2!x"), "secret"),
        (
            lambda b: b["events"][0].update(ref="https://x.test/?token=abcdef1234567890abcdef"),
            "secret",
        ),
        (lambda b: b.update(repo="nope"), "owner/name"),
        (lambda b: b.update(repo="a/b/c"), "owner/name"),
        (lambda b: b.update(pr=0), "pr must"),
        (lambda b: b.update(pr="7"), "pr must"),
        (lambda b: b.update(pr=True), "pr must"),
        (lambda b: b.update(title=5), "title"),
        (lambda b: b.update(events=[]), "1 to 200"),
        (lambda b: b.update(events="x"), "1 to 200"),
        (lambda b: b.update(stray=1), "repo, pr, title and events"),
    ],
)
def test_bad_input_is_a_plain_400_and_stores_nothing(world, http, mutate, words):
    payload = body(event("ok", "card", "pass"), event("bad", "gate", "pass"))
    mutate(payload)
    # The mutation lands on the first event, so a good second event must not be stored either.
    status, answer, _ = post(http, payload)
    assert status == 400 and words in answer["error"], answer
    assert set(answer) == {"error"}
    assert http("GET", "/api/pipeline/prs")[1]["prs"] == []


def test_more_than_200_events_in_one_request_is_refused(world, http):
    many = [event(f"e{n}", "card", "pass") for n in range(201)]
    status, answer, _ = post(http, body(*many))
    assert status == 400 and "1 to 200" in answer["error"]
    status, answer, _ = post(http, body(*many[:200]))
    assert status == 200 and answer["accepted"] == 200


def test_a_time_more_than_five_minutes_ahead_is_refused_and_a_small_skew_is_not(world, http):
    ahead = datetime.now(timezone.utc) + timedelta(minutes=6)
    status, answer, _ = post(http, body(event("f", "card", "pass") | {"at": ahead.isoformat()}))
    assert status == 400 and "future" in answer["error"]
    near = datetime.now(timezone.utc) + timedelta(minutes=4)
    assert post(http, body(event("n", "card", "pass") | {"at": near.isoformat()}))[0] == 200


def test_ancient_times_and_other_offsets(world, http):
    assert post(http, body(event("old", "card", "pass") | {"at": "1999-01-01T00:00:00Z"}))[0] == 400
    plus_one = (datetime.now(timezone.utc) - timedelta(hours=1)).astimezone(
        timezone(timedelta(hours=1))
    )
    assert post(http, body(event("tz", "card", "pass") | {"at": plus_one.isoformat()}))[0] == 200


def test_malformed_json_is_a_plain_400(world, http):
    status, answer, _ = http("POST", "/api/pipeline/events", raw="{nope", token=TOKENS["pipeline"])
    assert status == 400 and "error" in answer
    status, answer, _ = http("POST", "/api/pipeline/events", raw="[1]", token=TOKENS["pipeline"])
    assert status == 400


def test_a_pr_holds_a_bounded_number_of_events_and_the_refusal_keeps_the_rest(
    world, http, monkeypatch
):
    monkeypatch.setattr(store, "MAX_PR_EVENTS", 3)
    assert post(http, body(*[event(f"e{n}", "gate", "pass") for n in range(3)]))[0] == 200
    status, answer, _ = post(http, body(event("one-more", "gate", "pass")))
    assert status == 400 and "at most" in answer["error"]
    _, one, _ = http("GET", f"/api/pipeline/prs/{REPO}/7")
    assert step(one, "gate")["events"] == 3


# ── Authentication and workspace isolation ───────────────────────────────
def test_no_credentials_is_401(world, http):
    assert post(http, story(), anonymous=True)[0] == 401
    assert http("GET", "/api/pipeline/prs", anonymous=True)[0] == 401
    assert http("GET", f"/api/pipeline/prs/{REPO}/7", anonymous=True)[0] == 401
    assert http("GET", "/api/pipeline/prs?demo=1", anonymous=True)[0] == 401


def test_a_bad_token_is_401(world, http):
    assert post(http, story(), token="not-a-token")[0] == 401


def test_the_owner_session_can_read_and_write(world, http):
    assert post(http, story(), principal=world.person)[0] == 200
    assert len(http("GET", "/api/pipeline/prs", principal=world.person)[1]["prs"]) == 1


def test_a_read_only_agent_reads_but_cannot_write(world, http):
    post(http, story())
    token = reader(world)
    assert len(http("GET", "/api/pipeline/prs", token=token)[1]["prs"]) == 1
    status, answer, _ = post(http, story(), token=token)
    assert status == 403 and "error" in answer


def test_an_ordinary_memory_agent_cannot_write_pipeline_events(world, http):
    # memory:write is not the deploy script's scope.
    assert post(http, story(), token=world.token)[0] == 403
    assert http("GET", "/api/pipeline/prs")[1]["prs"] == []


def test_an_agent_without_memory_scopes_is_refused(world, http):
    token, _ = world.agent("plain", scopes=["kg:read"])
    assert http("GET", "/api/pipeline/prs", token=token)[0] == 403
    assert post(http, story(), token=token)[0] == 403


def test_a_member_reads_but_cannot_write(world, http):
    from commonplace import sessions

    member = world.db.create_user("member@example.test")
    world.db.add_member(world.workspace, member, "member", actor=world.user)
    principal = sessions.person_principal(world.db, member, world.workspace)
    assert principal.role == "member"
    post(http, story())
    assert len(http("GET", "/api/pipeline/prs", principal=principal)[1]["prs"]) == 1
    assert post(http, story(), principal=principal)[0] == 403


# ── The deploy script's own token ───────────────────────────────────────
def test_the_pipeline_token_records_events_and_nothing_else(world, http):
    token = TOKENS["pipeline"]
    assert post(http, story())[0] == 200
    for method, path in (
        ("GET", "/api/pipeline/prs"),
        ("GET", f"/api/pipeline/prs/{REPO}/7"),
        ("GET", "/api/pipeline/prs?demo=1"),
        ("GET", "/api/pipeline/events"),
        ("GET", "/api/topology"),
        ("GET", "/api/connections"),
        ("GET", "/api/me"),
        ("POST", "/api/pipeline/events/"),
        ("POST", "/api/pipeline/prs"),
        ("POST", "/api/topology/proposals"),
        ("POST", "/mcp"),
        ("DELETE", "/api/pipeline/events"),
    ):
        status, answer, _ = http(method, path, {"x": 1} if method != "GET" else None, token=token)
        assert status == 403, (method, path, status)
        assert answer == {"error": "this connection can only record pipeline events"}


def test_a_revoked_pipeline_token_stops_working(world, http):
    assert post(http, story())[0] == 200
    world.db.revoke("deploy-feed", actor=world.user, workspace_ref=world.workspace)
    assert post(http, body(event("late", "check", "pass")))[0] == 401
    # The same name can be minted again once the old one is revoked, and the old token stays dead.
    TOKENS["pipeline"] = pipeline_token(world, "deploy-feed-2")
    assert post(http, body(event("again", "check", "pass")))[0] == 200


def test_an_expired_pipeline_token_stops_working(world, http):
    world.db.revoke("deploy-feed", actor=world.user, workspace_ref=world.workspace)
    _, token = world.db.create_token(
        workspace_ref=world.workspace,
        user_ref=world.user,
        name="short",
        client="deploy",
        policy=Policy.build(profile="slim", scopes=["pipeline:write"], trust="untrusted"),
        actor=world.user,
        expires_days=1,
    )
    world.db._conn.execute(
        "UPDATE connections SET expires_at='2020-01-01T00:00:00Z' WHERE name='short'"
    )
    assert post(http, story(), token=token)[0] == 401


def test_a_pipeline_token_belongs_to_its_workspace(world, http):
    other_feed, other_read = other_workspace(world)
    # Its write lands in its own workspace; ours stays empty and theirs holds the event.
    assert post(http, story(), token=other_feed)[1]["accepted"] == 6
    assert http("GET", "/api/pipeline/prs")[1]["prs"] == []
    assert len(http("GET", "/api/pipeline/prs", token=other_read)[1]["prs"]) == 1
    # And our token writes only ours.
    post(http, body(event("mine", "card", "pass"), pr=9))
    assert [r["pr"] for r in http("GET", "/api/pipeline/prs", token=other_read)[1]["prs"]] == [7]


def test_the_pipeline_scope_is_the_only_scope_and_one_token_per_workspace(world):
    policy = lambda *scopes: Policy.build(profile="slim", scopes=list(scopes), trust="untrusted")  # noqa: E731

    def mint(name, *scopes, **extra):
        return world.db.create_token(
            workspace_ref=extra.pop("workspace", world.workspace),
            user_ref=extra.pop("user", world.user),
            name=name,
            client="deploy",
            policy=policy(*scopes),
            actor=extra.pop("actor", world.user),
            **extra,
        )

    # Mixed with another scope: refused.
    with pytest.raises(ControlError, match="only the pipeline:write scope"):
        mint("mixed", "pipeline:write", "memory:read")
    # One live token per workspace (the fixture's is live).
    with pytest.raises(ControlError, match="already has a pipeline token"):
        mint("second", "pipeline:write")
    # A member cannot hold it, and an owner cannot hand it to a child connection.
    member = world.db.create_user("member@example.test")
    world.db.add_member(world.workspace, member, "member", actor=world.user)
    world.db.revoke("deploy-feed", actor=world.user, workspace_ref=world.workspace)
    with pytest.raises(ControlError, match="belongs to a workspace owner"):
        mint("members", "pipeline:write", user=member)
    with pytest.raises(ControlError, match="no parent"):
        mint("child", "pipeline:write", parent="builder")
    # After revoking, one can be minted again; another workspace is unaffected by ours.
    mint("fresh", "pipeline:write")
    other_workspace(world)


# ── The card step comes from the board ──────────────────────────────────
def board_card(world, *, pr=7, repo=REPO, board="work", board_repo=None, doing=True):
    """A card on a board with its PR linked; returns (its number, the board service)."""
    from commonplace.boards import BoardService
    from commonplace.extensions import ToolContext, ToolError

    service = BoardService(ToolContext(world.person, world.gateway))
    try:
        service.board_add(slug=board, wing="shared", repo=board_repo)
    except ToolError:  # the board is already there
        pass
    card = service.task_add(board=board, title="Build the thing")
    if doing:
        service.task_update(card["number"], status="doing")
    service.task_update(card["number"], pr=f"https://github.com/{repo or REPO}/pull/{pr}")
    if repo is None:
        with service.store.connection() as conn:
            conn.execute("UPDATE tasks SET pr_repo=NULL, pr_url=NULL WHERE id=?", (card["number"],))
    return card["number"], service


def card_claim(service, number):
    with service.store.connection() as conn:
        return conn.execute(
            "SELECT created_at FROM task_events WHERE task_id=? AND to_status='doing' "
            "ORDER BY id LIMIT 1",
            (number,),
        ).fetchone()["created_at"]


def test_the_card_step_runs_from_the_claim_to_the_pr_opening(world, http, monkeypatch):
    number, service = board_card(world)
    claimed = card_claim(service, number)
    service.task_update(number, status="todo")
    with monkeypatch.context() as clock:
        clock.setattr("commonplace.boards.store.now", lambda: when(2100))
        service.task_update(number, status="doing")
    # The PR opened about a minute after the card was claimed. Events stay inside the five
    # minutes the server accepts ahead of its clock, whatever the setup took.
    post(http, body(event("o", "pr_opened", "pass", 1860), event("g", "gate", "pass", 1920)))
    _, one, _ = http("GET", f"/api/pipeline/prs/{REPO}/7")
    box = step(one, "card")
    assert (box["state"], box["ref"], box["detail"]) == ("pass", f"#/card/{number}", None)
    assert box["start"][:19] == claimed[:19] and box["end"] == step(one, "pr_opened")["start"]
    assert one["lead"]["basis"] == "card" and one["lead"]["from"] == box["start"]


def test_a_card_claimed_after_the_pr_opened_starts_and_ends_at_the_pr(world, http):
    number, _ = board_card(world)
    post(http, body(event("o", "pr_opened", "pass", 0), event("g", "gate", "pass", 60)))
    _, one, _ = http("GET", f"/api/pipeline/prs/{REPO}/7")
    box = step(one, "card")
    assert box["start"] == box["end"] == step(one, "pr_opened")["start"]
    assert box["detail"] == "Card linked after the PR opened" and box["duration"] == 0


def test_a_pr_with_no_linked_card_says_no_card(world, http):
    post(http, body(event("o", "pr_opened", "pass", 0), event("g", "gate", "pass", 60)))
    _, one, _ = http("GET", f"/api/pipeline/prs/{REPO}/7")
    card = step(one, "card")
    assert (card["state"], card["detail"], card["ref"]) == ("skipped", "No card", None)
    assert card["start"] == step(one, "pr_opened")["start"]
    assert one["lead"]["basis"] == "first_event"


def test_a_card_for_another_pr_is_not_this_ones(world, http):
    board_card(world, pr=8)
    post(http, body(event("o", "pr_opened", "pass", 0)))
    assert step(http("GET", f"/api/pipeline/prs/{REPO}/7")[1], "card")["detail"] == "No card"


def card_ref(world, http, repo=REPO, pr=7):
    return step(http("GET", f"/api/pipeline/prs/{repo}/{pr}")[1], "card")


def test_a_card_linked_under_the_old_repository_name_is_found(world, http, monkeypatch):
    """The events say the new name; the board's card stores the name its repository had."""
    monkeypatch.setenv("COMMONPLACE_REPO_ALIASES", f"acme/old-name={REPO}")
    number, _ = board_card(world, pr=637, repo="acme/old-name")
    post(http, body(event("o", "pr_opened", "pass", 0), pr=637))
    box = card_ref(world, http, pr=637)
    assert (box["state"], box["ref"]) == ("pass", f"#/card/{number}")
    assert http("GET", f"/api/pipeline/prs/{REPO}/637")[1]["lead"]["basis"] == "card"


def test_two_cards_with_one_number_prefer_the_exact_repository(world, http):
    other, _ = board_card(world, repo="other/repo", board="other")
    exact, _ = board_card(world, repo=REPO)
    assert other < exact
    post(http, body(event("o", "pr_opened", "pass", 0)))
    assert card_ref(world, http)["ref"] == f"#/card/{exact}"


def test_two_cards_with_one_number_and_no_fit_link_nothing(world, http):
    board_card(world, repo="other/repo", board="other")
    board_card(world, repo="third/repo", board="third")
    post(http, body(event("o", "pr_opened", "pass", 0)))
    assert card_ref(world, http)["detail"] == "No card"


def test_two_cards_fall_back_to_the_board_that_holds_the_repository(world, http):
    board_card(world, repo="other/repo", board="other", board_repo="other/repo")
    mine, _ = board_card(world, repo=None, board="mine", board_repo=REPO)
    post(http, body(event("o", "pr_opened", "pass", 0)))
    assert card_ref(world, http)["ref"] == f"#/card/{mine}"


@pytest.mark.parametrize("reverse", [False, True])
def test_a_repository_alias_picks_between_cards(world, http, monkeypatch, reverse):
    board_card(world, repo="other/repo", board="other")
    old, new = "acme/old-name", REPO
    card_repo, event_repo = (new, old) if reverse else (old, new)
    renamed, _ = board_card(world, repo=card_repo, board="mine")
    post(http, body(event("o", "pr_opened", "pass", 0), repo=event_repo))
    assert card_ref(world, http, repo=event_repo)["detail"] == "No card"
    monkeypatch.setenv("COMMONPLACE_REPO_ALIASES", f"bad,x=,{old}={new}")
    assert card_ref(world, http, repo=event_repo)["ref"] == f"#/card/{renamed}"


def test_a_single_card_for_a_different_repository_is_not_borrowed(world, http):
    board_card(world, pr=133, repo="other/project")
    post(http, body(event("o", "pr_opened", "pass", 0), pr=133))
    box = card_ref(world, http, pr=133)
    assert (box["state"], box["detail"], box["ref"]) == ("skipped", "No card", None)
    assert http("GET", f"/api/pipeline/prs/{REPO}/133")[1]["lead"]["basis"] == "first_event"


@pytest.mark.parametrize("extra_card", [False, True])
def test_a_mismatched_card_repo_cannot_be_overridden_by_its_board(world, http, extra_card):
    board_card(world, repo="other/project", board_repo=REPO)
    if extra_card:
        board_card(world, repo="third/project", board="third")
    post(http, body(event("o", "pr_opened", "pass", 0)))
    assert card_ref(world, http)["detail"] == "No card"


@pytest.mark.parametrize(
    "stored_repo",
    [
        "acme/widgets",
        "github.com/acme/widgets",
        "https://github.com/Acme/Widgets.git/",
        "git@github.com:acme/widgets.git",
    ],
)
def test_card_repository_urls_and_names_are_normalized(world, http, stored_repo):
    number, service = board_card(world)
    with service.store.connection() as conn:
        conn.execute("UPDATE tasks SET pr_repo=? WHERE id=?", (stored_repo, number))
    post(http, body(event("o", "pr_opened", "pass", 0)))
    assert card_ref(world, http)["ref"] == f"#/card/{number}"


@pytest.mark.parametrize("board_repo", [None, "other/project"])
@pytest.mark.parametrize("count", [1, 2])
def test_missing_card_repo_needs_a_matching_board(world, http, board_repo, count):
    for i in range(count):
        board_card(world, repo=None, board=f"work-{i}", board_repo=board_repo)
    post(http, body(event("o", "pr_opened", "pass", 0)))
    assert card_ref(world, http)["detail"] == "No card"


@pytest.mark.parametrize("missing_repo", [False, True])
def test_multiple_matching_cards_are_ambiguous(world, http, missing_repo):
    for _ in range(2):
        board_card(world, repo=None if missing_repo else REPO, board_repo=REPO)
    post(http, body(event("o", "pr_opened", "pass", 0)))
    assert card_ref(world, http)["detail"] == "No card"


@pytest.mark.parametrize("reverse", [False, True])
def test_missing_card_repo_can_use_an_explicit_board_alias(world, http, monkeypatch, reverse):
    old, new = "acme/old-name", REPO
    board_repo, event_repo = (new, old) if reverse else (old, new)
    number, _ = board_card(world, repo=None, board_repo=board_repo)
    post(http, body(event("o", "pr_opened", "pass", 0), repo=event_repo))
    assert card_ref(world, http, repo=event_repo)["detail"] == "No card"
    monkeypatch.setenv("COMMONPLACE_REPO_ALIASES", f"https://github.com/{old}.git={new}")
    assert card_ref(world, http, repo=event_repo)["ref"] == f"#/card/{number}"


def test_the_alias_list_is_read_both_ways_and_ignores_junk(monkeypatch):
    from commonplace.pipeline.cards import aliases

    monkeypatch.setenv("COMMONPLACE_REPO_ALIASES", "a/old=a/new, b/x=b/y,junk,=,c/d=")
    assert aliases("a/new") == {"github.com/a/new", "github.com/a/old"}
    assert aliases("a/old") == {"github.com/a/new", "github.com/a/old"}
    assert aliases("b/y") == {"github.com/b/x", "github.com/b/y"}
    assert aliases("c/d") == {"github.com/c/d"}
    assert aliases("not a repo") == set() or all(
        r.startswith("github.com/") for r in aliases("x/y")
    )
    monkeypatch.delenv("COMMONPLACE_REPO_ALIASES")
    assert aliases("a/new") == {"github.com/a/new"}


def test_a_card_that_never_was_doing_starts_when_it_was_created(world, http):
    number, service = board_card(world, doing=False)
    with service.store.connection() as conn:
        created = conn.execute("SELECT created_at FROM tasks WHERE id=?", (number,)).fetchone()[0]
    post(http, body(event("o", "pr_opened", "pass", 2040)))
    box = card_ref(world, http)
    assert box["start"][:19] == created[:19] and box["ref"] == f"#/card/{number}"


def test_a_card_in_another_workspace_is_never_used(world, http):
    number, _ = board_card(world)
    feeder, reader_token = other_workspace(world)
    # Their workspace has this repository and number in its events but no card of its own.
    post(http, body(event("o", "pr_opened", "pass", 0)), token=feeder)
    post(http, body(event("o", "pr_opened", "pass", 0)))
    theirs = http("GET", f"/api/pipeline/prs/{REPO}/7", token=reader_token)[1]
    assert step(theirs, "card")["detail"] == "No card"
    assert card_ref(world, http)["ref"] == f"#/card/{number}"


def test_a_card_the_reader_cannot_see_is_not_linked(world, http):
    board_card(world)
    post(http, body(event("o", "pr_opened", "pass", 0)))
    token = reader(world)  # read_wings=["*"] sees it; a narrower reader must not
    narrow, _ = world.agent("narrow", read_wings=["inbox"], scopes=["memory:read"])
    assert (
        step(http("GET", f"/api/pipeline/prs/{REPO}/7", token=narrow)[1], "card")["state"]
        == "skipped"
    )
    assert (
        step(http("GET", f"/api/pipeline/prs/{REPO}/7", token=token)[1], "card")["state"] == "pass"
    )


# ── The example and the file ─────────────────────────────────────────────
def test_the_example_is_served_without_touching_the_store(world, http):
    status, listing, _ = http("GET", "/api/pipeline/prs?demo=1")
    assert status == 200 and listing["demo"] is True
    assert sorted(row["pr"] for row in listing["prs"]) == [101, 102, 103]
    status, one, _ = http("GET", "/api/pipeline/prs/example/app/102?demo=1")
    assert status == 200 and step(one, "gate")["attempts"] == 2
    assert http("GET", "/api/pipeline/prs/example/app/555?demo=1")[0] == 404
    assert http("GET", "/api/pipeline/prs")[1]["prs"] == []
    assert not store.open_pipeline(world.gateway, world.workspace).path.exists()


def test_reading_never_creates_the_file_and_the_first_write_does(world, http):
    path = store.open_pipeline(world.gateway, world.workspace).path
    http("GET", "/api/pipeline/prs")
    http("GET", f"/api/pipeline/prs/{REPO}/7")
    assert not path.exists()
    post(http, story())
    assert path.exists() and path.name == "pipeline.sqlite3"


def test_the_clock_is_one_replaceable_function(world, http, monkeypatch):
    post(http, story())
    fixed = datetime.now(timezone.utc) + timedelta(hours=5)
    monkeypatch.setattr(routes, "_now", lambda: fixed)
    _, one, _ = http("GET", f"/api/pipeline/prs/{REPO}/7")
    assert one["lead"]["seconds"] > 5 * 3600  # still in progress: counted to the replaced clock


# ── Minting with the existing token command ─────────────────────────────
def test_the_token_command_mints_and_revokes_the_pipeline_token(tmp_path, capsys):
    """The steps in docs/system/pr-pipeline-view.md, run for real against a fresh data folder."""
    import json

    from commonplace import cli
    from commonplace.control_db import ControlDB

    data = tmp_path / "data"
    data.mkdir()
    db = ControlDB(str(data / "control.sqlite3"))
    owner = db.create_user("owner@example.test")
    workspace = db.create_workspace("team", owner)
    db.close()
    base = ["--data", str(data)]
    mint = ["token", "add", "pipeline-feed", "--workspace", workspace, "--client", "deploy"]
    mint += ["--scopes", "pipeline:write", "--by", owner]
    assert cli.main(base + mint) == 0
    out, err = capsys.readouterr()
    token = out.strip()
    assert token and "\n" not in token
    assert json.loads(err)["scopes"] == ["pipeline:write"]
    # The token is shown once and stored hashed; listing never prints it.
    assert cli.main(base + ["token", "list", "--workspace", workspace]) == 0
    listing = capsys.readouterr().out
    assert "pipeline-feed" in listing and token not in listing
    db = ControlDB(str(data / "control.sqlite3"))
    assert token not in repr([dict(row) for row in db.connections(workspace)])
    db.close()
    # A second one is refused until the first is revoked.
    assert cli.main(base + mint[:2] + ["second"] + mint[3:]) != 0
    assert "already has a pipeline token" in capsys.readouterr().err
    assert (
        cli.main(
            base + ["token", "revoke", "pipeline-feed", "--workspace", workspace, "--by", owner]
        )
        == 0
    )
    assert cli.main(base + mint[:2] + ["second"] + mint[3:]) == 0


def test_the_pipeline_scope_is_in_no_default_scope_set(world, http):
    from commonplace.sessions import person_policy

    for role in ("owner", "member"):
        assert "pipeline:write" not in person_policy(role).scopes
    for profile in ("slim", "standard", "full"):
        assert "pipeline:write" not in Policy.build(profile=profile, trust="trusted").scopes
    # An ordinary token with several scopes is not mistaken for the deploy token.
    assert http("GET", "/api/pipeline/prs")[0] == 200
    assert http("GET", "/api/pipeline/prs", principal=world.person)[0] == 200
