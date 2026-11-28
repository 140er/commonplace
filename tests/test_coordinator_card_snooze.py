"""Current board coordinators can park work, with a reason and no stalled-board nudge."""

import json

import pytest

from commonplace import events
from commonplace.boards.service import BoardService
from commonplace.boards.store import BoardStore
from commonplace.extensions import ToolError
from commonplace.orch import durable, stall, store as orch_store
from commonplace.snooze import store as snooze
from commonplace.tool_policy import store as tool_policy
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_card_snooze import LATER, fake_clock  # noqa: F401
from tests.test_commonplace_board_authority import fresh as fresh
from tests.test_commonplace_boards import api, mcp
from tests.test_commonplace_origin_holder_release import edit, policy
from tests.test_orch_durable import BOARD, ROLE, T0, Env, at
from tests.test_projects_seat_lease import Settings

REASON = "Waiting for the dependency's release; keep the exact words."


def coordinator(fresh):
    seat = fresh.connect("coordinator")
    fresh.owner.board_set("work", seat.principal.actor)
    return seat


def call(hub, seat, transport, name, number, **arguments):
    if transport == "tool":
        return mcp(hub, seat.token, name, task=number, **arguments)
    path = f"/api/card-snooze/{number}"
    if name == "task_unsnooze":
        path += "/cancel"
    status, body = api(hub, seat.token, "POST", path, arguments)
    return status == 200, body


@pytest.mark.parametrize("transport", ["tool", "rest"])
@pytest.mark.parametrize("status", ["backlog", "todo"])
@pytest.mark.parametrize("assigned", [False, True])
def test_coordinator_parks_and_wakes_another_cards_work(hub, fresh, transport, status, assigned):
    seat = coordinator(fresh)
    n = fresh.owner.task_add(board="work", title="Park this work", status=status)["number"]
    if assigned:
        edit(fresh.owner, n, assignee=fresh.connect("worker").principal.actor)
    ok, reply = call(hub, seat, transport, "task_snooze", n, until=LATER, reason=REASON)
    assert ok, reply
    card = fresh.owner.task_show(n)
    assert card["status"] == status
    assert card["snoozed"]["reason"] == REASON
    assert card["snoozed"]["by"] == seat.principal.actor
    assert REASON in card["snoozed"]["text"]
    ok, reply = mcp(hub, seat.token, "task_show", task=n)
    assert ok and REASON in reply, reply
    for path in (f"/api/cards/{n}", f"/api/card-snooze/{n}"):
        code, payload = api(hub, seat.token, "GET", path)
        assert code == 200 and payload["snoozed"]["reason"] == REASON
    listed = fresh.owner.task_list(board="work", all_statuses=True)
    assert next(c for c in listed if c["number"] == n)["snoozed"]["reason"] == REASON
    history = card["events"][-1]
    assert history["actor"] == seat.principal.actor and history["reason"] == REASON
    assert REASON in history["text"]
    recorded = [e for e in events.after(hub.db, fresh.workspace, 0) if e["kind"] == "card.snoozed"]
    assert recorded[-1]["actor"] == seat.principal.actor
    assert recorded[-1]["data"]["snoozed"]["reason"] == REASON
    ok, reply = call(hub, seat, transport, "task_unsnooze", n)
    assert ok, reply
    assert "snoozed" not in fresh.owner.task_show(n)


@pytest.mark.parametrize("transport", ["tool", "rest"])
@pytest.mark.parametrize("reason", [None, "", " \n\t", 7, True, {}, ["later"], "x" * 501])
def test_coordinator_reason_is_required_validated_and_refusal_changes_nothing(
    hub, fresh, transport, reason
):
    seat = coordinator(fresh)
    n = fresh.owner.task_add(board="work", title="Do not hide silently")["number"]
    before = fresh.owner.task_show(n)
    arguments = {} if reason is None else {"reason": reason}
    ok, reply = call(hub, seat, transport, "task_snooze", n, until=LATER, **arguments)
    assert not ok and "reason" in str(reply), reply
    assert fresh.owner.task_show(n) == before


def test_reason_limit_and_verbatim_storage(hub, fresh):
    seat = coordinator(fresh)
    n = fresh.owner.task_add(board="work", title="Exact reason")["number"]
    reason = "  Wait <for this> & keep\n" + "x" * (500 - len("  Wait <for this> & keep\n"))
    card = seat.work.task_snooze(n, until=LATER, reason=reason)
    assert card["snoozed"]["reason"] == reason
    shown = fresh.owner.task_show(n)
    assert shown["events"][-1]["reason"] == reason
    assert shown["snoozed"]["reason"] == reason


@pytest.mark.parametrize("holder", ["owner", "assignee"])
def test_owner_and_assignee_keep_optional_reasons_and_owner_can_wake_coordinator_snooze(
    hub, fresh, holder
):
    seat = coordinator(fresh)
    n = fresh.owner.task_add(board="work", title="Optional reason")["number"]
    if holder == "assignee":
        edit(fresh.owner, n, assignee=seat.principal.actor)
    work = fresh.owner if holder == "owner" else seat.work
    assert work.task_snooze(n, until=LATER)["snoozed"]["reason"] is None
    work.task_unsnooze(n)
    edit(fresh.owner, n, assignee=None)
    seat.work.task_snooze(n, until=LATER, reason=REASON)
    assert "snoozed" not in fresh.owner.task_unsnooze(n)


@pytest.mark.parametrize("transport", ["tool", "rest"])
@pytest.mark.parametrize("status", ["doing", "review", "done"])
def test_coordinator_cannot_park_started_or_finished_cards(hub, fresh, transport, status):
    seat = coordinator(fresh)
    n = fresh.owner.task_add(board="work", title="Not parked work")["number"]
    changes = {"status": status}
    if status == "doing":
        changes["assignee"] = fresh.connect("worker").principal.actor
    elif status == "review":
        changes.update(ask="Proceed?", ask_to=fresh.owner.principal.actor)
    edit(fresh.owner, n, **changes)
    before = fresh.owner.task_show(n)
    ok, reply = call(hub, seat, transport, "task_snooze", n, until=LATER, reason=REASON)
    assert not ok and "Backlog or Todo" in str(reply), reply
    assert fresh.owner.task_show(n) == before


@pytest.mark.parametrize("transport", ["tool", "rest"])
@pytest.mark.parametrize("loss", ["other_board", "former", "connection", "member"])
def test_authority_does_not_spread_to_other_boards_or_callers(hub, fresh, transport, loss):
    seat = coordinator(fresh)
    board = "work"
    if loss == "other_board":
        board = "other"
        fresh.owner.board_add(slug=board, wing="shared")
    n = fresh.owner.task_add(board=board, title="Still visible")["number"]
    fresh.owner.task_snooze(n, until=LATER)
    if loss == "former":
        fresh.owner.board_set("work", fresh.connect("replacement").principal.actor)
    elif loss == "connection":
        seat = fresh.connect("ordinary-connection")
    elif loss == "member":
        user = hub.db.create_user("member@example.invalid")
        hub.db.add_member(fresh.workspace, user, "member", actor=fresh.user)
        seat = fresh.connect("member", user_ref=user)
    before = fresh.owner.task_show(n)
    for name, arguments in (
        ("task_snooze", {"until": LATER, "reason": REASON}),
        ("task_unsnooze", {}),
    ):
        ok, reply = call(hub, seat, transport, name, n, **arguments)
        assert not ok and "coordinator" in str(reply), reply
        assert fresh.owner.task_show(n) == before


@pytest.mark.parametrize(
    "loss", ["expired", "released", "replaced", "other_board", "other_connection"]
)
def test_lease_authority_is_checked_on_each_snooze_and_unsnooze(hub, loss):
    env = Env(hub)
    lease = env.resume(env.a)
    seat = BoardService(env.ctx(env.a))
    n = env.card()
    seat.task_snooze(n, until=LATER, reason=REASON)
    if loss == "expired":
        with env.runner_db() as conn:
            conn.execute("UPDATE orch_leases SET expires_at='2020-01-01T00:00:00Z'")
    elif loss == "released":
        durable.release(env.ctx(env.a), BOARD, ROLE, lease["epoch"], lease["token"], now=T0)
    elif loss == "replaced":
        env.resume(env.b, now=at(durable.TTL_SECONDS + 1))
        replacement = BoardService(env.ctx(env.b))
        assert "snoozed" not in replacement.task_unsnooze(n)
        replacement.task_snooze(n, until=LATER, reason=REASON)
    elif loss == "other_board":
        n = env.owner_board.task_add(board="plain", title="Different board")["number"]
        env.owner_board.task_snooze(n, until=LATER)
    else:
        seat = BoardService(env.ctx(env.b))
    before = env.owner_board.task_show(n)
    with pytest.raises(ToolError, match="coordinator"):
        seat.task_snooze(n, until=LATER, reason=REASON)
    with pytest.raises(ToolError, match="coordinator"):
        seat.task_unsnooze(n)
    assert env.owner_board.task_show(n) == before


def test_project_settings_seat_can_park_and_wake_through_both_paths(hub):
    settings = Settings(hub)
    token = settings.seat()
    ok, reply = settings.resume(token)
    assert ok, reply
    n = settings.w.card()
    ok, reply = mcp(hub, token, "task_snooze", task=n, until=LATER, reason=REASON)
    assert ok and REASON in reply, reply
    code, payload = api(hub, token, "POST", f"/api/card-snooze/{n}/cancel", {})
    assert code == 200 and "snoozed" not in payload


def test_coordinator_snoozes_suppress_the_stall_event_until_unsnoozed(hub):
    env = Env(hub)
    env.resume(env.a)
    seat = BoardService(env.ctx(env.a))
    numbers = [env.card(), env.card()]
    for n in numbers:
        seat.task_snooze(n, until=LATER, reason=REASON)
    with env.boards_db() as conn:
        board = dict(conn.execute("SELECT * FROM boards WHERE slug=?", (BOARD,)).fetchone())
    store = orch_store.open_store(hub.gateway, env.ws)
    now = "2030-01-07T08:21:00Z"
    assert stall.evaluate(hub.gateway, env.ws, store, board, 3, now) is None
    assert not [e for e in events.after(hub.db, env.ws, 0) if e["kind"] == "board.stalled"]
    seat.task_unsnooze(numbers[0])
    nudged = stall.evaluate(hub.gateway, env.ws, store, board, 3, now)
    assert nudged is not None and nudged["todo"] == [numbers[0]]
    assert len([e for e in events.after(hub.db, env.ws, 0) if e["kind"] == "board.stalled"]) == 1


def test_existing_snoozes_survive_the_additive_reason_migration(hub, fresh):
    n = fresh.owner.task_add(board="work", title="Legacy snooze")["number"]
    fresh.owner.task_snooze(n, until=LATER)
    before = fresh.owner.task_show(n)
    with fresh.owner.store.transaction() as conn:
        conn.execute("ALTER TABLE card_snoozes DROP COLUMN reason")
    for _ in range(2):
        store = BoardStore(fresh.owner.ctx.workspace_dir())
        with store.connection() as conn:
            record = snooze.active(conn, n)
            assert record["reason"] is None
            assert record["until"] == LATER and record["by"] == fresh.owner.principal.actor
    after = fresh.owner.task_show(n)
    assert after == before


def test_snooze_tool_schema_exposes_a_bounded_optional_reason(hub, fresh):
    seat = coordinator(fresh)
    tools = hub.rpc(seat.token, "tools/list")["result"]["tools"]
    schema = next(tool["inputSchema"] for tool in tools if tool["name"] == "task_snooze")
    assert schema["properties"]["reason"]["type"] == "string"
    assert schema["properties"]["reason"]["maxLength"] == 500
    assert "reason" not in schema["required"]
    assert "orchestrator" not in json.dumps(next(t for t in tools if t["name"] == "task_snooze"))


@pytest.mark.parametrize("name", ["task_snooze", "task_unsnooze"])
@pytest.mark.parametrize("restriction", ["hidden", "read_only", "owner_off", "owner_ask"])
def test_coordinator_rest_keeps_the_tool_and_write_policy_gates(hub, fresh, name, restriction):
    seat = coordinator(fresh)
    n = fresh.owner.task_add(board="work", title="Keep the permission boundary")["number"]
    fresh.owner.task_snooze(n, until=LATER)
    if restriction == "hidden":
        visible = [t for t in seat.principal.policy.visible_tools() if t != name]
        policy(hub, seat, tools=visible)
    elif restriction == "read_only":
        policy(hub, seat, scopes=["memory:read"])
    else:
        tool_policy.set_modes(
            hub.db,
            fresh.workspace,
            {name: "off" if restriction == "owner_off" else "ask"},
            actor=fresh.owner.principal.actor,
            now=1.0,
        )
    before = fresh.owner.task_show(n)
    arguments = {"until": LATER, "reason": REASON} if name == "task_snooze" else {}
    ok, reply = call(hub, seat, "rest", name, n, **arguments)
    assert not ok, reply
    assert fresh.owner.task_show(n) == before
