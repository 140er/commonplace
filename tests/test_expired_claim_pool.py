"""Expired agent work is recoverable without crossing waits or session ownership."""

import json
from pathlib import Path

import pytest

from commonplace.boards.service import BoardService
from commonplace.boards.store import _MIGRATED, BoardStore
from commonplace.extensions import ToolError
from commonplace.orch import durable, triggers
from commonplace.runner import registry
from mempalace.sqlite_crypto import connect
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests._split_helpers import blockers, world
from tests.test_commonplace_boards import mcp
from tests.test_orch_durable import BOARD, ROLE, T0, Env, at


@pytest.fixture
def w(hub):
    return world(hub, on=False, runner=True)


def expired(w, *, session="", reserved=False):
    ctx = w.ctx(w.a)
    ctx.extra["claim_session"] = session
    worker = BoardService(ctx)
    n = worker.task_add(title="Recover abandoned work", board=BOARD, claim=True)["number"]
    with worker.store.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET claim_until=?,claim_expired_at=?,restricted_origin=? WHERE id=?",
            (T0, T0, int(reserved), n),
        )
    return worker, n


def clock(monkeypatch, seconds):
    monkeypatch.setattr("commonplace.boards.store.now", lambda: at(seconds))


def test_expired_claim_returns_after_grace_and_fires_existing_trigger(w, monkeypatch):
    worker, n = expired(w)
    clock(monkeypatch, 1799)
    assert worker.task_show(n)["status"] == "doing"
    triggers.run(w.js, now=at(1799))
    assert not w.jobs_for(n)
    clock(monkeypatch, 1800)
    # No card read is needed to make an abandoned claim startable.
    triggers.run(w.js, now=at(1800))
    card = worker.task_show(n)
    assert card["status"] == "todo" and card["assignee"] is None
    assert card["claim_until"] is None and card["claim_expired_at"] == T0
    events = [e for e in card["events"] if e.get("text") == "claim expired, returned to the pool"]
    assert len(events) == 1
    with worker.store.connection() as conn:
        event = conn.execute(
            "SELECT * FROM task_events WHERE task_id=? AND kind='release'", (n,)
        ).fetchone()
        assert event["from_status"] == "doing" and event["to_status"] == "todo"
    assert len(w.jobs_for(n)) == 1
    triggers.run(w.js, now=at(1801))
    assert len(w.jobs_for(n)) == 1
    assert BoardService(w.ctx(w.b)).task_take_next(task=n)["status"] == "doing"


@pytest.mark.parametrize(
    "guard", ["person", "review", "snooze", "dependency_snooze", "blocked", "question"]
)
def test_expired_claim_does_not_return_protected_work(w, monkeypatch, guard):
    worker, n = expired(w)
    with worker.store.transaction() as conn:
        if guard == "person":
            conn.execute("UPDATE tasks SET assignee=? WHERE id=?", (w.owner.actor, n))
        elif guard in ("review", "question"):
            conn.execute(
                "UPDATE tasks SET status='review',claim_until=NULL,ask=?,ask_to=? WHERE id=?",
                (
                    "Which permission is required?",
                    w.owner.actor if guard == "review" else w.b.actor,
                    n,
                ),
            )
        elif guard == "snooze":
            conn.execute(
                "INSERT INTO card_snoozes(task_id,until,by,at) VALUES (?,?,?,?)",
                (n, at(7200), w.owner.actor, T0),
            )
    if guard in ("blocked", "dependency_snooze"):
        dependency = w.card(status="backlog")
        if guard == "blocked":
            blockers(w, n, dependency)
        else:
            with worker.store.transaction() as conn:
                conn.execute(
                    "INSERT INTO card_snoozes(task_id,after_card,by,at) VALUES (?,?,?,?)",
                    (n, dependency, w.owner.actor, T0),
                )
    clock(monkeypatch, 3600)
    worker.task_show(n)
    triggers.run(w.js, now=at(3600))
    card = worker.task_show(n)
    assert card["status"] == ("review" if guard in ("review", "question") else "doing")
    assert not w.jobs_for(n)


def test_workspace_grace_setting_and_read_only_checkpoint(w, monkeypatch):
    worker, n = expired(w)
    directory = Path(w.gw.workspace_dir(w.ws))
    (directory / "workspace.json").write_text(json.dumps({"claim_expiry_grace_minutes": 60}))
    clock(monkeypatch, 3599)
    worker.task_show(n)
    assert worker.task_show(n)["status"] == "doing"
    before = w.boards.store.changes
    cp = durable.checkpoint(w.ctx(w.b), BOARD, ROLE, now=at(3599))
    assert n in {c["card"] for c in cp["cards"]["expired"]}
    assert w.boards.store.changes == before
    clock(monkeypatch, 3600)
    worker.task_show(n)
    cp = durable.checkpoint(w.ctx(w.b), BOARD, ROLE, now=at(3600))
    assert n not in {c["card"] for c in cp["cards"]["expired"]}
    assert n in {c["card"] for c in cp["unstarted"]}


def test_expired_restricted_claim_returns_to_reservation_holder(w, monkeypatch):
    worker, n = expired(w, reserved=True)
    # Release grants work authority, but never transfers this expired reservation.
    w.boards.task_approve(n, reason="Allow implementation by its assigned connection")
    w.boards.board_set(BOARD, w.b.actor)
    clock(monkeypatch, 1800)
    card = worker.task_show(n)
    assert card["status"] == "todo" and card["assignee"] == w.a.actor
    with pytest.raises(ToolError):
        BoardService(w.ctx(w.b)).task_take_next(task=n)
    assert worker.task_take_next(task=n)["status"] == "doing"


def test_second_session_cannot_renew_or_retake_first_sessions_claim(hub):
    e = Env(hub)
    first = e.ctx(e.a)
    first.extra["claim_session"] = "run-one"
    second = e.ctx(e.a)
    second.extra["claim_session"] = "run-two"
    worker = BoardService(first)
    n = worker.task_add(title="Session owned work", board=BOARD, claim=True)["number"]
    assert "claim_session" not in worker.task_show(n)
    with worker.store.transaction() as conn:
        conn.execute("UPDATE tasks SET claim_until=? WHERE id=?", (at(600), n))
    original = worker.task_show(n)["claim_until"]
    result = durable.resume(second, BOARD, ROLE, now=T0)
    assert n in {c["card"] for c in result["checkpoint"]["cards"]["not_held_by_caller"]}
    assert worker.task_show(n)["claim_until"] == original
    for operation in (
        lambda: BoardService(second).task_heartbeat(n),
        lambda: BoardService(second).task_take_next(task=n),
        lambda: BoardService(second).task_update(n, status_line="Another session"),
    ):
        with pytest.raises(ToolError, match="session"):
            operation()
    worker.task_heartbeat(n)
    assert worker.task_show(n)["claim_until"] != original


def test_same_session_resume_and_legacy_claims_still_renew(hub):
    e = Env(hub)
    ctx = e.ctx(e.a)
    ctx.extra["claim_session"] = "run-one"
    for current in (ctx, e.ctx(e.a)):
        worker = BoardService(current)
        n = worker.task_add(title="Renew own work", board=BOARD, claim=True)["number"]
        with worker.store.transaction() as conn:
            conn.execute("UPDATE tasks SET claim_until=? WHERE id=?", (at(600), n))
    # Explicit session matches its own claim and can also renew session-less legacy rows.
    result = durable.resume(ctx, BOARD, ROLE, now=T0)
    assert (
        len([c for c in result["checkpoint"]["cards"]["held"] if c.get("heartbeat") == "renewed"])
        == 2
    )


def test_public_mcp_session_identity_survives_tool_dispatch(w):
    token = w.hub.token(
        "session-worker",
        profile="full",
        trust="trusted",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared"],
        write_wings=["shared"],
    )
    principal = w.gw.authenticate(f"Bearer {token}")
    registry.patch(
        w.db,
        w.owner,
        w.machine_id,
        {
            "requesters": [w.a.connection_id, w.b.connection_id, principal.connection_id],
        },
    )
    n = w.card(status="todo")
    ok, reply = mcp(w.hub, token, "task_take_next", task=n, session_id="run-one")
    assert ok, reply
    assert not mcp(w.hub, token, "task_heartbeat", task=n, session_id="run-two")[0]
    assert not mcp(w.hub, token, "task_heartbeat", task=n)[0]
    assert mcp(w.hub, token, "task_heartbeat", task=n, session_id="run-one")[0]
    ok, reply = mcp(w.hub, token, "orch_resume", board=BOARD, role=ROLE, session_id="run-two")
    assert ok, reply
    checkpoint = json.loads(reply)["checkpoint"]
    assert n in {c["card"] for c in checkpoint["cards"]["not_held_by_caller"]}


def test_existing_database_migrates_session_default_idempotently(tmp_path):
    schema = (Path(__file__).resolve().parents[1] / "commonplace/boards/schema.sql").read_text()
    path = tmp_path / "boards.sqlite3"
    conn = connect(str(path))
    conn.executescript(schema)
    # Keep a genuine populated database lacking the new column, even after this
    # change becomes the base for a future checkout.
    if "claim_session" in {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}:
        conn.execute("ALTER TABLE tasks DROP COLUMN claim_session")
    conn.execute(
        "INSERT INTO boards(slug,name,wing,stamp_user,stamp_trust,created_at) VALUES ('old','Old','shared','owner','trusted',?)",
        (T0,),
    )
    conn.execute(
        "INSERT INTO tasks(board_id,title,status,position,assignee,claim_until,stamp_user,stamp_trust,created_at,updated_at,updated_by) VALUES (1,'Existing claim','doing',1,'conn:worker',?,'owner','trusted',?,?, 'conn:worker')",
        (at(600), T0, T0),
    )
    conn.commit()
    conn.close()
    for _ in range(2):
        _MIGRATED.pop(str(path), None)  # Exercise the migration again, not just its cache.
        store = BoardStore(tmp_path)
        with store.connection() as conn:
            columns = {r[1]: r for r in conn.execute("PRAGMA table_info(tasks)")}
            assert "claim_session" in columns
            assert columns["claim_session"][4] == "''"
            row = conn.execute("SELECT * FROM tasks WHERE id=1").fetchone()
            assert row["claim_session"] == "" and row["claim_until"] == at(600)
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_standard_mcp_sessions_automatically_scope_new_claims(w):
    import http.client

    token = w.hub.token(
        "transport-worker",
        profile="full",
        trust="trusted",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared"],
        write_wings=["shared"],
    )

    def rpc(method, params, session="", connection=None):
        conn = connection or http.client.HTTPConnection("127.0.0.1", w.hub.port, timeout=10)
        try:
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
            if session:
                headers["Mcp-Session-Id"] = session
            conn.request(
                "POST",
                "/mcp",
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}),
                headers=headers,
            )
            response = conn.getresponse()
            result = json.loads(response.read())
            assert response.status == 200, result
            return response.getheader("Mcp-Session-Id"), result
        finally:
            if connection is None:
                conn.close()

    initialize = {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "claim-test", "version": "1"},
    }
    first, response = rpc("initialize", initialize)
    second, _ = rpc("initialize", initialize)
    assert "result" in response and first and second and first != second
    n = w.card(status="todo")
    _, claimed = rpc("tools/call", {"name": "task_take_next", "arguments": {"task": n}}, first)
    assert not claimed["result"].get("isError"), claimed
    for session in (second, ""):
        _, refused = rpc(
            "tools/call", {"name": "task_heartbeat", "arguments": {"task": n}}, session
        )
        assert refused["result"].get("isError"), refused
    _, renewed = rpc("tools/call", {"name": "task_heartbeat", "arguments": {"task": n}}, first)
    assert not renewed["result"].get("isError"), renewed
    # Header scope must also end between requests on one persistent connection.
    connection = http.client.HTTPConnection("127.0.0.1", w.hub.port, timeout=10)
    try:
        for session in (first, "", second, first):
            _, result = rpc(
                "tools/call",
                {"name": "task_heartbeat", "arguments": {"task": n}},
                session,
                connection=connection,
            )
            assert bool(result["result"].get("isError")) == (session != first)
    finally:
        connection.close()


@pytest.mark.parametrize("acquire", ["take", "update", "add"])
def test_all_claim_acquisitions_bind_session_and_comments_cannot_renew(w, acquire):
    ctx = w.ctx(w.a)
    ctx.extra["claim_session"] = "own-run"
    worker = BoardService(ctx)
    if acquire == "add":
        n = worker.task_add(title="New scoped claim", board=BOARD, claim=True)["number"]
    else:
        n = w.card(status="todo")
        if acquire == "take":
            worker.task_take_next(task=n)
        else:
            worker.task_update(n, status="doing")
    with worker.store.transaction() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (n,)).fetchone()
        assert row["claim_session"] == "own-run"
        conn.execute("UPDATE tasks SET claim_until=? WHERE id=?", (at(600), n))
    BoardService(w.ctx(w.a)).task_comment(n, text="A different session contributes context")
    assert worker.task_show(n)["claim_until"] == at(600)
    worker.task_heartbeat(n)
    assert worker.task_show(n)["claim_until"] != at(600)


def test_blocker_resolution_allows_a_previously_expired_claim_to_return(w, monkeypatch):
    worker, n = expired(w)
    dependency = w.card(status="backlog")
    blockers(w, n, dependency)
    clock(monkeypatch, 1800)
    assert worker.task_show(n)["status"] == "doing"
    with worker.store.transaction() as conn:
        conn.execute("UPDATE tasks SET status='done' WHERE id=?", (dependency,))
    assert worker.task_show(n)["status"] == "todo"
