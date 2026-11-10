"""Bound-card lease renewal with real stores, fencing and passive SSH permits."""

import json
from contextlib import contextmanager, nullcontext

import pytest

from commonplace.boards.store import BoardStore, expire_claims
from tests.test_compute_control import (
    approve,
    compute_env as compute_env,
    scope_payload,
)
from tests.test_compute_control_r2 import _agent
from tests.test_compute_executor import execution_env as execution_env, prepare, run_row
from tests.test_compute_fence import local_state as local_state
from tests.test_compute_passive_admission import _row, _submit, passive_state as passive_state
from tests.test_compute_transport import setup as setup

pytestmark = pytest.mark.usefixtures("compute_private_filesystem")


@contextmanager
def _old_authority(env, monkeypatch):
    """Create pre-upgrade bindings through the real insert/approval/claim paths.

    Only the removed bookkeeping field is restored. No immutable stored binding,
    approval digest, attempt or permit is rewritten to simulate the upgrade.
    """
    authority = env.store._authority

    def old(*args, **kwargs):
        result = authority(*args, **kwargs)
        if "task" in result:
            with env.service.store.connection() as conn:
                result["task"]["claim_until"] = conn.execute(
                    "SELECT claim_until FROM tasks WHERE id=?", (result["task"]["number"],)
                ).fetchone()[0]
        return result

    with monkeypatch.context() as patch:
        patch.setattr(env.store, "_authority", old)
        yield


@pytest.fixture(params=["current", "pre_upgrade"])
def bound(passive_state, request):
    state, env = passive_state, passive_state.env
    principal, service = _agent(state)
    with BoardStore(env.path.parent, create=False).transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status='doing',assignee=?,claim_until='2099-01-01T00:00:00Z' WHERE id=?",
            (principal.actor, env.task),
        )
    scope = env.store.create_scope(
        env.principal,
        **env.auth,
        **scope_payload(env, state.gateway["id"], agent_id=principal.connection_id),
    )
    state.scope = scope["id"]
    with env.store._journal() as conn:
        state.protected_scope = state.discovery.store._scope(conn, env.principal, state.scope)[1]
    state.helper_scope = state.protected_scope
    state.holder = service
    state.old_binding = request.param == "pre_upgrade"
    return state


def _submit_bound(state, monkeypatch):
    with _old_authority(state.env, monkeypatch) if state.old_binding else nullcontext():
        item = _submit(state)
    if state.old_binding:
        assert "claim_until" in json.loads(_row(state, item["id"])["binding"])["authority"]["task"]
    return item


def _renew(state, kind):
    env = state.env
    with env.service.store.connection() as conn:
        before = dict(conn.execute("SELECT * FROM tasks WHERE id=?", (env.task,)).fetchone())
    if kind == "heartbeat":
        state.holder.task_heartbeat(env.task)
    else:
        state.holder.task_comment(env.task, "still working on this card")
    with env.service.store.connection() as conn:
        after = dict(conn.execute("SELECT * FROM tasks WHERE id=?", (env.task,)).fetchone())
    assert before["claim_until"] != after["claim_until"]
    for field in ("assignee", "status", "claim_expired_at"):
        assert before[field] == after[field]


@pytest.mark.parametrize("window", ["queued", "before_challenge", "cache"])
@pytest.mark.parametrize("kind", ["heartbeat", "comment"])
def test_holder_renewal_on_bound_card_keeps_dispatch_and_evidence(bound, monkeypatch, window, kind):
    state = bound
    item = _submit_bound(state, monkeypatch)
    binding = _row(state, item["id"])["binding"]
    fired = []

    def write():
        _renew(state, kind)
        fired.append(window)

    if window == "queued":
        write()

    def hook(phase, fixture):
        if phase == window:
            write()

    fixture = state.ssh(hook=hook)
    assert state.discovery._run_once()
    row = _row(state, item["id"])
    assert fired == [window]
    assert (row["state"], row["reason"], row["slot"]) == ("completed", "collection_complete", 0)
    observations = json.loads(row["observations"])
    assert observations and observations[0]["address"] == "10.20.0.2"
    assert fixture.calls == ["linux_arp"]
    assert row["binding"] == binding
    with state.discovery.store._journal() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM discovery_permits WHERE discovery_id=?", (item["id"],)
            ).fetchone()[0]
            == 1
        )
    assert not state.discovery._run_once()


def _revoke(state, kind):
    env = state.env
    with BoardStore(env.path.parent, create=False).transaction() as conn:
        if kind == "holder":
            conn.execute("UPDATE tasks SET assignee=? WHERE id=?", ("user:" + env.member, env.task))
        elif kind == "status":
            # Both todo and doing are startable; their authority still differs.
            conn.execute("UPDATE tasks SET status='todo' WHERE id=?", (env.task,))
        else:
            conn.execute(
                "UPDATE tasks SET claim_until='2000-01-01T00:00:00Z' WHERE id=?", (env.task,)
            )
            board_id = conn.execute(
                "SELECT board_id FROM tasks WHERE id=?", (env.task,)
            ).fetchone()[0]
            expired = expire_claims(conn, board_id)
            assert [row["id"] for row in expired] == [env.task]
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (env.task,)).fetchone()
            assert row["status"] == "doing" and row["assignee"] == state.holder.principal.actor
            assert row["claim_expired_at"] is not None


@pytest.mark.parametrize("window", ["queued", "before_challenge", "cache"])
@pytest.mark.parametrize("kind", ["holder", "status", "expired_lease"])
def test_bound_card_revocation_still_denies(bound, monkeypatch, window, kind):
    state = bound
    item = _submit_bound(state, monkeypatch)
    fired = []

    def revoke():
        _revoke(state, kind)
        fired.append(window)

    if window == "queued":
        revoke()

    def hook(phase, fixture):
        if phase == window:
            revoke()

    fixture = state.ssh(hook=hook)
    assert state.discovery._run_once() is (window != "queued")
    row = _row(state, item["id"])
    assert fired == [window]
    assert row["slot"] == 0 and json.loads(row["observations"]) == []
    assert (
        row["state"]
        == {
            "queued": "denied",
            "before_challenge": "failed",
            "cache": "completed",
        }[window]
    )
    assert fixture.calls == (["linux_arp"] if window == "cache" else [])
    with state.discovery.store._journal() as conn:
        assert conn.execute(
            "SELECT count(*) FROM discovery_permits WHERE discovery_id=?", (item["id"],)
        ).fetchone()[0] == (1 if window == "cache" else 0)
    assert not state.discovery._run_once()


@pytest.mark.parametrize("window", ["awaiting_approval", "approved", "claimed"])
def test_pre_upgrade_execution_keeps_original_binding_and_consent(
    execution_env, monkeypatch, window
):
    state, env = execution_env, execution_env.env
    with _old_authority(env, monkeypatch):
        proposal = prepare(state, approval=window != "awaiting_approval")
        claimed = state.service._claim() if window == "claimed" else None
        if window == "claimed":
            assert claimed is not None
    with env.store._journal() as conn:
        before = dict(env.store._row(conn, "executions", proposal["id"]))
        assert "claim_until" in json.loads(before["binding"])["authority"]["task"]
    if window == "awaiting_approval":
        approve(env, proposal)
    with monkeypatch.context() as patch:
        if claimed is not None:
            patch.setattr(state.service, "_claim", lambda: claimed)
        assert state.service._run_once()
    row = run_row(state, proposal)
    assert row["phase"] == "terminal" and row["reserved"] == 0
    assert json.loads(row["result"])["output"]
    with env.store._journal() as conn:
        after = env.store._row(conn, "executions", proposal["id"])
        assert (after["binding"], after["digest"]) == (before["binding"], before["digest"])
    assert not state.service._run_once()
