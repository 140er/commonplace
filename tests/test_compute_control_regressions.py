"""PR 290 control regressions: synthetic data and pinned SSH socketpairs only."""

import json
import sqlite3
from dataclasses import replace

import pytest

from commonplace.boards.store import BoardStore
from commonplace.compute.executor import ExecutionService
from commonplace.compute.protocol import (
    ADMISSION_MODE,
    AdmissionChallenge,
    ProtocolDenied,
    decode_frame,
    encode_frame,
)
from commonplace.compute.script_sandbox import ScriptResult
from commonplace.compute.scripts import ScriptDenied
from commonplace.compute.transport import EphemeralKey, ExecutionResult, SSHTransport
from commonplace.policy import Policy
from tests.test_compute_admission import admission_state as admission_state
from tests.test_compute_control import compute_env as compute_env
from tests.test_compute_discovery_service import discovery_env as discovery_env
from tests.test_compute_executor import execution_env as execution_env, run_row
from tests.test_compute_fence import local_state as local_state
from tests.test_compute_passive_admission import (
    _row,
    _submit,
    passive_state as passive_state,
)
from tests.test_compute_script_integration import (
    WireOnlyTarget,
    propose_script,
    script_ssh as script_ssh,
    script_state as script_state,
)
from tests.test_compute_transport import (
    BytesChannel,
    Driver,
    make_helper,
    setup as setup,
)

pytestmark = pytest.mark.usefixtures("compute_private_filesystem")


def test_a1_clean_helper_sessions_release_concurrency_but_keep_replay_denial(setup):
    helper, journal, opened = make_helper(setup)

    class Controller(BytesChannel):
        def sendall(self, data):
            message = decode_frame(data)
            if message["kind"] == "ready":
                self.incoming.extend(encode_frame({**message, "kind": "go"}))
            super().sendall(data)

    requests = [replace(setup.request, execution=f"run-{i}", attempt=f"try-{i}") for i in range(3)]
    for request in requests:
        setup.request = request
        wire = Controller(encode_frame(request.message()))
        helper.serve(wire, wait_readable=lambda sources, timeout: [sources[1]])
        assert journal.state(request) == "closed"
        assert wire.closed
    assert len(opened) == 3
    with pytest.raises(ProtocolDenied, match="replayed_execution"):
        journal.consume(requests[0], "f" * 64)


@pytest.mark.parametrize(
    "kind", ["pat_use", "heartbeat", "task_version", "user_name", "session_created"]
)
def test_a2_bookkeeping_preserves_epoch_dispatch_and_published_evidence(local_state, kind):
    state, env = local_state, local_state.env
    _, token = env.db.create_token(
        workspace_ref=env.workspace,
        user_ref=env.owner,
        name="routine-pat",
        client="test",
        policy=Policy.build(scopes=["memory:read"]),
        actor=env.owner,
    )
    request, _, _ = state.service._claim()
    assert state.service._authorize(request, "connect")
    if kind == "pat_use":
        assert env.db.authenticate_token(token) is not None
    elif kind in ("heartbeat", "task_version"):
        with BoardStore(env.path.parent, create=False).transaction() as conn:
            update = (
                "heartbeat_at='2026-11-10T00:00:00Z'"
                if kind == "heartbeat"
                else "version=version+1"
            )
            conn.execute(f"UPDATE tasks SET {update} WHERE id=?", (state.blocker,))
    else:
        with env.db._lock, env.db._tx() as conn:
            if kind == "user_name":
                conn.execute("UPDATE users SET name='Fixture rename' WHERE id=?", (env.owner,))
            else:
                conn.execute("UPDATE sessions SET created_at='2026-11-09T00:00:00Z'")
    with state.fence.hold(env.workspace) as lease:
        assert lease.epoch == request.authority_epoch
    for phase in ("dispatch", "target_auth", "execute"):
        assert state.service._authorize(request, phase)
    state.service._finish(
        request, ExecutionResult("completed", "exit_observed", 0, b"fixture output")
    )
    row = run_row(state, state.proposal)
    assert (row["phase"], row["reserved"]) == ("terminal", 0)
    assert json.loads(row["result"])["output"] == "fixture output"


def test_a2_permission_write_still_invalidates_inflight_attempt(local_state):
    state = local_state
    request, _, _ = state.service._claim()
    assert state.service._authorize(request, "connect")
    with BoardStore(state.env.path.parent, create=False).transaction() as conn:
        conn.execute("UPDATE tasks SET status='done' WHERE id=?", (state.env.task,))
    with state.fence.hold(state.env.workspace) as lease:
        assert lease.epoch > request.authority_epoch
    assert not state.service._authorize(request, "dispatch")


def test_a2_no_permit_means_no_reservation_even_after_dispatch(admission_state):
    state = admission_state
    request, _, _ = state.service._claim()
    for phase in ("connect", "dispatch"):
        assert state.service._authorize(request, phase)
    state.service._finish(request, ExecutionResult("uncertain", "authority_denied"))
    row = run_row(state, state.proposal)
    assert (row["phase"], row["certainty"], row["reserved"]) == ("terminal", "known", 0)


def test_a2_crashed_stage_zero_has_no_remote_effect_or_reservation(local_state):
    state = local_state
    request, _, _ = state.service._claim()
    state.env.clock.now = request.run_until
    reopened = ExecutionService(state.env.store, enabled=True, fence=state.fence)
    with state.env.store._journal() as conn:
        reopened._recover(conn)
    row = run_row(state, state.proposal)
    assert (row["phase"], row["certainty"], row["reserved"]) == ("terminal", "known", 0)


def test_a2_wire_denial_before_permit_is_not_started(setup):
    request = replace(setup.request, admission_mode=ADMISSION_MODE, approval_digest="e" * 64)
    setup.request = request
    driver = Driver(setup)

    class Channel(BytesChannel):
        def sendall(self, data):
            message = decode_frame(data)
            if message["kind"] == "open_enrolled_ssh":
                self.ready = AdmissionChallenge(
                    request, "c" * 64, "a" * 64, request.start_by
                ).message("ready")
                self.incoming.extend(encode_frame(self.ready))
            elif message["kind"] == "go":
                self.incoming.extend(encode_frame({**self.ready, "kind": "admission_challenge"}))
            super().sendall(data)

    channel = Channel()
    driver.helper = lambda *args: channel

    def deny(*args):
        raise ProtocolDenied("authority_denied")

    transport = SSHTransport(
        lambda *args: True,
        enabled=True,
        driver=driver,
        wall=setup.clock,
        monotonic=setup.clock,
        admission_mode=ADMISSION_MODE,
        gateway_admission=deny,
    )
    result = transport.execute(
        request,
        setup.gateway,
        setup.route,
        EphemeralKey(b"synthetic-gateway", credential_id="gateway-key"),
        EphemeralKey(b"synthetic-target", credential_id="target-key"),
    )
    assert result.state == "not_started"
    assert result.reason == "authority_denied"


def test_a2_uncertain_reservation_has_trusted_operator_release(local_state):
    state = local_state
    request, _, _ = state.service._claim()
    for phase in ("connect", "dispatch"):
        assert state.service._authorize(request, phase)
    assert state.service._gateway_admission(request, "admit")
    assert state.service._authorize(request, "target_auth")
    state.service._finish(request, ExecutionResult("uncertain", "ssh_transport_failed"))
    assert run_row(state, state.proposal)["reserved"] == 1
    with sqlite3.connect(state.env.path) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE execution_runs SET reserved=0")
    reopened = ExecutionService(state.env.store, enabled=True, fence=state.fence)
    # The reviewed code has no participating reconciliation operation. Observe
    # the leaked reservation as the baseline failure, never an AttributeError.
    settle = getattr(reopened, "settle_uncertain", None)
    if settle is not None:
        settle(request.execution, "External evidence confirms termination")
    row = run_row(state, state.proposal)
    assert row["reserved"] == 0
    assert (row["phase"], row["reason"], row["certainty"]) == (
        "terminal",
        "operator_settled",
        "known",
    )
    assert row["operator_note"] == "External evidence confirms termination"
    with state.env.store._journal() as conn:
        assert (
            conn.execute(
                "SELECT action FROM audit WHERE object_id=? ORDER BY id DESC", (request.execution,)
            ).fetchone()[0]
            == "execution.operator_settled"
        )
    with pytest.raises(ValueError):
        settle(request.execution, "Again")


@pytest.mark.parametrize("revoked", [False, True])
def test_b2_permit_bound_discovery_settles_despite_lost_publication_authority(
    passive_state, revoked, monkeypatch
):
    state = passive_state
    item = _submit(state)
    original = state.discovery._finish

    def finish(request, outcome):
        assert outcome.response is not None
        if revoked:
            state.env.store.revoke_scope(state.env.principal, state.scope, **state.env.auth)
        else:
            # A permission write on a different task advances the global epoch.
            with BoardStore(state.env.path.parent, create=False).transaction() as conn:
                conn.execute("UPDATE tasks SET status='done' WHERE id=?", (state.blocker,))
        return original(request, outcome)

    monkeypatch.setattr(state.discovery, "_finish", finish)
    state.ssh()
    assert state.discovery._run_once()
    row = _row(state, item["id"])
    assert row["slot"] == 0
    assert row["state"] == "completed"
    assert len(json.loads(row["observations"])) == (0 if revoked else 1)


def test_b2_queued_discovery_uses_fresh_epoch_at_admission(discovery_env):
    state = discovery_env
    item, _ = state.submit()
    state.fence.advance()
    assert state.service._run_once()
    assert state.get(item["id"])["state"] == "completed"


def test_b2_no_durable_permit_releases_discovery_slot(passive_state, monkeypatch):
    state = passive_state
    item = _submit(state)

    def deny(*args):
        raise ProtocolDenied("authority_denied")

    monkeypatch.setattr(state.discovery._transport, "gateway_admission", deny)
    state.ssh()
    assert state.discovery._run_once()
    row = _row(state, item["id"])
    assert (row["state"], row["slot"]) == ("failed", 0)


@pytest.mark.parametrize(
    "code",
    [
        "runtime_digest_mismatch",
        "binding_mismatch",
        "replay_denied",
        "target_capacity",
        "sandbox_unavailable",
        "cancelled_before_start",
        "admission_expired",
    ],
)
def test_b4_authenticated_target_nonruns_release_reservation(
    script_state, script_ssh, monkeypatch, code
):
    state = script_state
    proposal = propose_script(state)

    def nonrun(self, request, script, **kwargs):
        if code in ("cancelled_before_start", "admission_expired"):
            return ScriptResult(
                "cancelled" if code == "cancelled_before_start" else "denied",
                code,
                termination_confirmed=True,
            )
        raise ScriptDenied(code)

    monkeypatch.setattr(WireOnlyTarget, "execute_verified", nonrun)
    fixture = script_ssh()
    assert state.service._run_once()
    assert fixture.authenticated == ["gateway", "target"]
    row = run_row(state, proposal)
    assert (row["phase"], row["certainty"], row["reserved"]) == ("terminal", "known", 0)
    assert row["reason"] == code
