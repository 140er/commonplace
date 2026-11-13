"""Stalled split recovery uses live board-seat or delegated owner authority."""

import json
from dataclasses import replace

import pytest

from commonplace import sessions
from commonplace.boards.service import BoardService
from commonplace.orch import runner_bridge
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests._split_helpers import invoke, tool
from tests.runner_harness import http
from tests.test_projects_seat_lease import settings as settings_seed

settings_fixture = pytest.fixture(name="settings")(settings_seed.__wrapped__)


def stalled(w, status="todo"):
    root = w.card(status=status)
    with w.js.transaction() as conn:
        runner_bridge.ensure_schema(conn)
        conn.executemany(
            "INSERT INTO orch_misses(board,event_id,card,code,actor,gen,created_at) "
            "VALUES ('acme',?,?, 'project_limit',?,1,'2030-01-01T00:00:00Z')",
            [(-n, root, w.owner.actor) for n in range(1, 4)],
        )
    return root


@pytest.mark.parametrize("state", ["split", "kept"])
@pytest.mark.parametrize("status", ["todo", "backlog"])
@pytest.mark.parametrize("caller", ["seat", "bound-seat", "owner-token", "owner-session"])
def test_stuck_root_recovery(settings, caller, status, state):
    s, w = settings, settings.w
    seat_token = s.seat()
    root = stalled(w, status)
    if caller in ("seat", "bound-seat"):
        if caller == "seat":
            # The durable seat binding survives a missing legacy coordinator field.
            w.boards.board_set("acme", None)
        token = seat_token
    else:
        token = w.hub.owner_token("owner-recovery")
    before = w.boards.task_show(root)
    assert before["waiting"]["split_stuck"] is True
    refused, error = invoke(BoardService(w.ctx(w.a)).task_take_next, board="acme", task=root)
    assert refused == 409 and "split stuck" in str(error)

    if caller == "owner-session":
        code, result = s.owner(
            "POST", "/api/projects/acme/orchestrator/split", {"task": root, "state": state}
        )
        expected_actor = w.owner.actor
        assert code == 200, result
    else:
        assert "card_split_done" in w.hub.tool_names(token)
        ok, result = s.tool(token, "card_split_done", task=root, state=state)
        assert ok, result
        result = json.loads(result)
        expected_actor = w.gw.authenticate("Bearer " + token).actor
    assert result["number"] == root and result["state"] == state
    assert result["by"] == expected_actor and result["at"]

    marked = w.boards.task_show(root)
    assert marked["version"] == before["version"] + 1
    assert "split stuck" not in str(marked.get("waiting"))
    history = [e for e in marked["events"] if e["kind"] == "edit" and e["choice"] == "split"]
    assert len(history) == 1
    assert history[0]["actor"] == expected_actor and history[0]["text"] == state
    assert tool(w, "orchestrator_inbox", board="acme") == (200, [])

    # A different authorized actor repeating either choice gets the original marker.
    opposite = "kept" if state == "split" else "split"
    assert tool(w, "card_split_done", task=root, state=opposite) == (200, result)
    assert w.boards.task_show(root) == marked
    accepted, taken = invoke(BoardService(w.ctx(w.a)).task_take_next, board="acme", task=root)
    assert accepted == 200 and taken["number"] == root, taken


@pytest.mark.parametrize("auth", ["pat", "oauth"])
def test_bound_coordinator_and_owner_connection_can_read_and_mark(settings, auth):
    s, w = settings, settings.w
    token = s.seat()
    seat = w.gw.authenticate("Bearer " + token)
    root = stalled(w)
    owner = w.a
    if auth == "oauth":
        # Both authentication kinds resolve to the same stored connection authority.
        with w.db._lock:
            w.db._conn.executemany(
                "UPDATE connections SET auth='oauth' WHERE id=?",
                [(seat.connection_id,), (owner.connection_id,)],
            )
        seat = w.db.authenticate_connection(seat.connection_id)
        owner = w.db.authenticate_connection(owner.connection_id)
    for principal in (seat, owner):
        code, inbox = tool(w, "orchestrator_inbox", principal, board="acme")
        assert code == 200 and inbox[0]["number"] == root, inbox
    assert tool(w, "card_split_done", owner, task=root, state="kept")[0] == 200
    assert tool(w, "card_split_done", seat, task=root, state="split")[1]["state"] == "kept"


@pytest.mark.parametrize(
    "kind",
    [
        "member",
        "member-token",
        "worker",
        "child",
        "other-seat",
        "scope",
        "wing",
        "untrusted",
        "foreign",
        "revoked",
        "replaced-seat",
    ],
)
def test_other_connections_cannot_clear_stuck_root(settings, kind):
    s, w = settings, settings.w
    seat_token = s.seat()
    root = stalled(w)
    principal = w.gw.authenticate("Bearer " + seat_token)
    if kind in ("member", "member-token"):
        user = w.db.create_user("member@example.test")
        w.db.add_member(w.ws, user, "member", actor=w.owner.user_id)
        principal = sessions.person_principal(w.db, user, w.ws)
        if kind == "member-token":
            token = w.hub.token(
                "member",
                user=user,
                profile="standard",
                read_wings=["shared"],
                write_wings=["shared"],
                trust="trusted",
            )
            principal = w.gw.authenticate("Bearer " + token)
    elif kind == "worker":
        principal = w.db.authenticate_connection(w.machine()["worker_connection_id"])
        # Even an accidental coordinator binding must not promote a worker credential.
        with w.boards.store.transaction() as conn:
            conn.execute("UPDATE boards SET coordinator=? WHERE slug='acme'", (principal.actor,))
    elif kind == "child":
        cid, _ = w.db.create_token(
            workspace_ref=w.ws,
            user_ref=w.owner.user_id,
            actor=w.owner.user_id,
            name="child",
            client="generic",
            parent=w.a.connection_id,
            policy=w.a.policy,
        )
        principal = w.db.authenticate_connection(cid)
    elif kind == "other-seat":
        principal = w.gw.authenticate("Bearer " + s.seat("plain"))
    elif kind == "scope":
        principal = replace(principal, policy=replace(principal.policy, scopes=("memory:read",)))
    elif kind == "wing":
        principal = replace(
            principal, policy=replace(principal.policy, write_wing="elsewhere", write_wings=())
        )
    elif kind == "untrusted":
        token = w.hub.token(
            "restricted",
            scopes=["memory:read", "memory:write"],
            read_wings=["shared"],
            write_wing="shared",
            trust="untrusted",
        )
        principal = w.gw.authenticate("Bearer " + token)
    elif kind == "foreign":
        ws = w.db.create_workspace("elsewhere", w.owner.user_id)
        token = w.hub.token(
            principal.connection,
            workspace=ws,
            profile="standard",
            read_wings=["shared"],
            write_wings=["shared"],
            trust="trusted",
        )
        # Same actor spelling and an owner role cannot substitute another workspace's credential.
        principal = replace(w.gw.authenticate("Bearer " + token), workspace_id=w.ws)
    elif kind == "revoked":
        w.db.revoke(principal.connection_id, actor=w.owner.user_id, workspace_ref=w.ws)
    elif kind == "replaced-seat":
        w.boards.board_set("acme", w.b.actor)
    before = w.boards.task_show(root)
    code, result = tool(w, "card_split_done", principal, task=root, state="split")
    assert code == 403 and result["code"] == "not_allowed", result
    assert w.boards.task_show(root) == before
    with w.boards.store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM card_splits").fetchone()[0] == 0


def test_owner_token_recovery_through_existing_rest_route(settings):
    w = settings.w
    root = stalled(w)
    token = w.hub.owner_token("owner-recovery")
    code, result = http(
        w.hub.port,
        "POST",
        "/api/projects/acme/orchestrator/split",
        token=token,
        body={"task": root, "state": "split"},
    )
    assert code == 200 and result["state"] == "split", result
    assert "split stuck" not in str(w.boards.task_show(root).get("waiting"))


def test_split_recovery_does_not_release_other_holds(settings):
    s, w = settings, settings.w
    token = s.seat()
    root = stalled(w)
    with w.boards.store.transaction() as conn:
        conn.execute("UPDATE tasks SET proposal_status='pending' WHERE id=?", (root,))
    ok, text = s.tool(token, "card_split_done", task=root, state="kept")
    assert ok, text
    view = w.boards.task_show(root)
    assert view["proposal_status"] == "pending" and view["approved_by"] is None
    assert not view["waiting"].get("split_stuck")
    code, _ = invoke(BoardService(w.ctx(w.a)).task_take_next, board="acme", task=root)
    assert code >= 400


def test_off_switch_keeps_recovery_unavailable(settings):
    s, w = settings, settings.w
    token = s.seat()
    root = stalled(w)
    assert s.owner("PUT", "/api/settings/orchestration", {"enabled": False})[0] == 200
    assert "card_split_done" not in w.hub.tool_names(token)
    assert tool(w, "card_split_done", task=root, state="kept")[1]["code"] == "orchestration_off"
    assert "waiting" not in w.boards.task_show(root)


def test_owner_connection_without_write_scope_is_refused_over_rest(settings):
    w = settings.w
    root = stalled(w)
    token = w.hub.token(
        "readonly-owner",
        profile="standard",
        scopes=["memory:read"],
        read_wings=["shared"],
        trust="trusted",
    )
    assert "card_split_done" not in w.hub.tool_names(token)
    before = w.boards.task_show(root)
    code, result = http(
        w.hub.port,
        "POST",
        "/api/projects/acme/orchestrator/split",
        token=token,
        body={"task": root, "state": "kept"},
    )
    assert code == 403 and result["code"] == "not_allowed", result
    assert w.boards.task_show(root) == before


def test_foreign_owner_cannot_address_the_root_over_rest(settings):
    w = settings.w
    root = stalled(w)
    ws = w.db.create_workspace("foreign-workspace", w.owner.user_id)
    token = w.hub.token(
        "foreign-owner",
        workspace=ws,
        profile="standard",
        read_wings=["shared"],
        write_wings=["shared"],
        trust="trusted",
    )
    before = w.boards.task_show(root)
    code, result = http(
        w.hub.port,
        "POST",
        "/api/projects/acme/orchestrator/split",
        token=token,
        body={"task": root, "state": "split"},
    )
    assert code == 404 and result["code"] == "board_not_found", result
    assert w.boards.task_show(root) == before


def test_unbound_seat_can_create_keyed_children(settings):
    s, w = settings, settings.w
    token = s.seat()
    root = w.card()
    w.boards.board_set("acme", None)
    seat = w.gw.authenticate("Bearer " + token)
    service = BoardService(w.ctx(seat))
    child = service.task_add(
        "Planned child", board="acme", parent=root, labels=[f"split:{root}:build"]
    )
    inbox = tool(w, "orchestrator_inbox", seat, board="acme")[1]
    assert inbox[0]["keyed_children"][0]["number"] == child["number"]
    assert tool(w, "card_split_done", seat, task=root, state="split")[0] == 200
