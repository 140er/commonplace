"""Round-two control regressions; private synthetic stores and SSH socketpairs."""

import json
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from commonplace import sessions
from commonplace.boards.service import BoardService
from commonplace.boards.store import BoardStore
from commonplace.compute.protocol import (
    AdmissionChallenge,
    PassiveAdmissionChallenge,
    ProtocolDenied,
    decode_frame,
    encode_frame,
)
from commonplace.compute.transport import ExecutionResult
from commonplace.extensions import ToolContext
from commonplace.policy import Policy
from tests.test_compute_admission import admission_state as admission_state
from tests.test_compute_control import compute_env as compute_env
from tests.test_compute_executor import execution_env as execution_env, prepare, run_row
from tests.test_compute_fence import local_state as local_state
from tests.test_compute_passive_admission import _row, _submit, passive_state as passive_state
from tests.test_compute_script_integration import (
    propose_script,
    script_ssh as script_ssh,
    script_state as script_state,
)
from tests.test_compute_transport import BytesChannel, make_helper, setup as setup

pytestmark = pytest.mark.usefixtures("compute_private_filesystem")


def _agent(state):
    env = state.env
    _, token = env.db.create_token(
        workspace_ref=env.workspace,
        user_ref=env.owner,
        name="r2-holder",
        client="test",
        policy=Policy.build(
            scopes=["memory:read", "memory:write"],
            read_wings=["*"],
            write_wings=["*"],
            trust="trusted",
        ),
        actor=env.owner,
    )
    principal = env.db.authenticate_token(token)
    return principal, BoardService(ToolContext(principal=principal, gateway=env.gateway))


def _holder(state):
    principal, service = _agent(state)
    with BoardStore(state.env.path.parent, create=False).transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status='doing',assignee=?,claim_until='2099-01-01T00:00:00Z' WHERE id=?",
            (principal.actor, state.blocker),
        )
    return service


def _unrelated(state, kind, holder):
    if kind == "heartbeat":
        holder.task_heartbeat(state.blocker)
    elif kind == "sign_in":
        sessions.start_session(state.env.db, state.env.owner, state.env.workspace)
    else:
        state.env.service.task_add("Unrelated new card", board="compute")


@pytest.mark.parametrize("kind", ["heartbeat", "sign_in", "new_card"])
def test_major1_real_writes_during_wire_execution_keep_evidence(admission_state, kind):
    state = admission_state
    holder = _holder(state)
    fired = []

    def hook(phase, fixture):
        if phase in ("before_challenge", "auth:target", "command"):
            _unrelated(state, kind, holder)
            fired.append(phase)

    fixture = state.ssh(hook=hook)
    assert state.service._run_once()
    row = run_row(state, state.proposal)
    assert row["stage"] == 4
    assert row["phase"] == "terminal" and row["reserved"] == 0
    assert json.loads(row["result"])["output"]
    assert len(fired) == 3 and len(fixture.commands) == 1
    request = json.loads(row["request"])
    with state.env.store._journal() as conn:
        assert (
            conn.execute(
                "SELECT authority_epoch FROM gateway_admissions WHERE execution_id=?",
                (request["execution"],),
            ).fetchone()[0]
            == request["authority_epoch"]
        )


@pytest.mark.parametrize("kind", ["heartbeat", "sign_in", "new_card"])
def test_major1_real_writes_during_discovery_still_dispatch(passive_state, kind):
    state = passive_state
    holder = _holder(state)
    item = _submit(state)
    fired = []

    def hook(phase, fixture):
        if phase == "before_challenge":
            _unrelated(state, kind, holder)
            fired.append(phase)

    state.ssh(hook=hook)
    assert state.discovery._run_once()
    row = _row(state, item["id"])
    assert fired and row["state"] == "completed" and row["slot"] == 0
    assert json.loads(row["observations"])
    with state.discovery.store._journal() as conn:
        request = json.loads(
            conn.execute(
                "SELECT request FROM discovery_attempts WHERE discovery_id=?", (item["id"],)
            ).fetchone()[0]
        )
        assert (
            conn.execute(
                "SELECT authority_epoch FROM discovery_permits WHERE discovery_id=?", (item["id"],)
            ).fetchone()[0]
            == request["authority_epoch"]
        )


def test_major1_stage_three_denial_is_a_nonrun(admission_state):
    state = admission_state
    request, _, _ = state.service._claim()
    for phase in ("connect", "dispatch"):
        assert state.service._authorize(request, phase)
    challenge = AdmissionChallenge(request, "c" * 64, "a" * 64, request.start_by)
    assert state.service._gateway_admission(request, "admit", challenge=challenge)
    assert state.service._authorize(request, "target_auth")
    with BoardStore(state.env.path.parent, create=False).transaction() as conn:
        conn.execute("UPDATE tasks SET status='done' WHERE id=?", (state.env.task,))
    assert not state.service._authorize(request, "execute")
    state.service._finish(request, ExecutionResult("uncertain", "authority_denied"))
    row = run_row(state, state.proposal)
    assert (row["stage"], row["phase"], row["certainty"], row["reserved"]) == (
        3,
        "terminal",
        "known",
        0,
    )


def test_major1_stage_four_commit_precedes_fixed_exec_channel(admission_state, monkeypatch):
    state = admission_state
    fixture = state.ssh()
    original = fixture.driver.command
    observed = []

    def command(*args):
        with sqlite3.connect(state.env.path) as conn:
            stage = conn.execute(
                "SELECT stage FROM execution_runs WHERE execution_id=?", (state.proposal["id"],)
            ).fetchone()[0]
        observed.append(stage)
        assert stage == 4 and not state.fence.active
        return original(*args)

    monkeypatch.setattr(fixture.driver, "command", command)
    assert state.service._run_once()
    assert observed == [4]
    assert run_row(state, state.proposal)["exit_code"] == 0


def test_major1_stage_four_commit_precedes_script_exec_channel(
    script_state, script_ssh, monkeypatch
):
    state = script_state
    proposal = propose_script(state)
    fixture = script_ssh()
    original = fixture.driver.script_command
    observed = []

    def command(*args):
        with sqlite3.connect(state.env.path) as conn:
            stage = conn.execute(
                "SELECT stage FROM execution_runs WHERE execution_id=?", (proposal["id"],)
            ).fetchone()[0]
        observed.append(stage)
        assert stage == 4 and not state.fence.active
        return original(*args)

    monkeypatch.setattr(fixture.driver, "script_command", command)
    assert state.service._run_once()
    assert observed == [4]
    assert run_row(state, proposal)["exit_code"] == 0


def _released_origin(state):
    principal, _ = _agent(state)
    with BoardStore(state.env.path.parent, create=False).transaction() as conn:
        conn.execute(
            "UPDATE tasks SET restricted_origin=1,proposal_status='approved',approved_by=? WHERE id=?",
            (principal.actor, state.env.task),
        )
    return principal


def _revoke(state, kind, releaser):
    env = state.env
    if kind == "session":
        sessions.end_session(env.db, env.cookie)
    elif kind == "connection":
        env.db.revoke(releaser.connection_id, actor=env.owner, workspace_ref=env.workspace)
    elif kind == "membership":
        with env.db._lock, env.db._tx() as conn:
            conn.execute(
                "UPDATE memberships SET role='member' WHERE workspace_id=? AND user_id=?",
                (env.workspace, env.owner),
            )
    elif kind == "credential":
        credential = state.credentials[0]
        state.custody.revoke(credential.credential_id, credential.binding, state.context)
    elif kind == "task":
        with BoardStore(env.path.parent, create=False).transaction() as conn:
            conn.execute("UPDATE tasks SET status='done' WHERE id=?", (env.task,))
    elif kind == "origin_release":
        with BoardStore(env.path.parent, create=False).transaction() as conn:
            conn.execute("UPDATE tasks SET approved_by=NULL WHERE id=?", (env.task,))


@pytest.mark.parametrize(
    "kind",
    ["session", "connection", "membership", "consent", "task", "origin_release", "credential"],
)
def test_major1_execution_real_revocation_still_denies(admission_state, kind):
    state = admission_state
    state.env.store.cancel_execution(state.env.principal, state.proposal["id"], **state.env.auth)
    releaser = _released_origin(state)
    proposal = prepare(state, verified=False, request_id="r2-revocation")
    request, _, _ = state.service._claim()
    assert request.execution == proposal["id"]
    assert state.service._authorize(request, "connect")
    if kind == "consent":
        state.env.store.cancel_execution(state.env.principal, proposal["id"], **state.env.auth)
    else:
        _revoke(state, kind, releaser)
    assert not state.service._authorize(request, "dispatch")


@pytest.mark.parametrize(
    "kind",
    ["session", "connection", "membership", "consent", "task", "origin_release", "credential"],
)
def test_major1_discovery_real_revocation_still_denies(passive_state, kind):
    state = passive_state
    releaser = _released_origin(state)
    item = _submit(state)
    request, _, _ = state.discovery._claim()
    assert state.discovery._authorize(request, "connect")
    if kind == "consent":
        state.env.store.revoke_scope(state.env.principal, state.scope, **state.env.auth)
    else:
        _revoke(state, kind, releaser)
    assert not state.discovery._authorize(request, "collect")
    assert _row(state, item["id"])["slot"] == 1


class Controller(BytesChannel):
    def sendall(self, data):
        message = decode_frame(data)
        if message["kind"] == "ready":
            self.incoming.extend(encode_frame({**message, "kind": "go"}))
        super().sendall(data)


def test_major2_failed_opens_release_capacity_and_still_deny_replay(setup):
    def unreachable(*args):
        raise ConnectionRefusedError("synthetic offline target")

    helper, journal, _ = make_helper(setup, opener=unreachable)
    first = setup.request
    for index in range(3):
        request = replace(first, execution=f"open-{index}", attempt=f"try-{index}")
        setup.request = request
        with pytest.raises(ConnectionRefusedError):
            helper.serve(Controller(encode_frame(request.message())))
        assert journal.state(request) == "closed"
    with pytest.raises(ProtocolDenied, match="replayed_execution"):
        journal.consume(
            replace(first, execution="open-0", attempt="try-0"),
            "f" * 64,
        )


def test_major2_abnormal_relay_releases_after_successful_close(setup):
    helper, journal, _ = make_helper(setup)
    wire = Controller(encode_frame(setup.request.message()))
    with pytest.raises(ProtocolDenied, match="deadline_exceeded"):
        helper.serve(wire, wait_readable=lambda *args: [])
    assert journal.state(setup.request) == "closed"


@pytest.mark.parametrize("state", ["consumed", "uncertain"])
def test_major2_helper_operator_releases_counted_row(setup, state):
    _, journal, _ = make_helper(setup)
    journal.consume(setup.request, "a" * 64)
    if state == "uncertain":
        journal.uncertain(setup.request)
    release = getattr(journal, "operator_release", None)
    if release is not None:
        release(
            setup.request.workspace,
            setup.request.gateway,
            setup.request.execution,
            "Confirmed helper process and tunnel stopped",
        )
    assert journal.state(setup.request) == "operator_closed"
    with pytest.raises(ProtocolDenied, match="replayed_execution"):
        journal.consume(setup.request, "b" * 64)
    next_request = replace(setup.request, execution="next", attempt="next")
    journal.consume(next_request, "c" * 64)
    assert journal.state(next_request) == "consumed"


def test_major3_discovery_operator_releases_restart_uncertainty(passive_state):
    state = passive_state
    item = _submit(state)
    request, _, _ = state.discovery._claim()
    assert state.discovery._authorize(request, "collect")
    challenge = PassiveAdmissionChallenge(request, "c" * 64, "a" * 64, request.start_by)
    state.discovery._wire_admission(request, challenge)
    state.env.clock.now = request.run_until + 1
    with state.discovery.store._journal() as conn:
        state.discovery._recover(conn)
    state.discovery._worker = "restarted-worker"
    settle = getattr(state.discovery, "settle_uncertain", None)
    if settle is not None:
        settle(request.discovery, "Confirmed passive helper exited")
    row = _row(state, item["id"])
    assert (row["state"], row["reason"], row["slot"]) == (
        "operator_settled",
        "discovery_operator_settled",
        0,
    )
    assert row["operator_note"] == "Confirmed passive helper exited"
    assert not state.discovery._run_once()


def test_minor1_stage_four_legacy_unknown_keeps_reservation(execution_env):
    state = execution_env
    proposal = prepare(state)
    request, _, _ = state.service._claim()
    for phase in ("connect", "dispatch", "target_auth", "execute"):
        assert state.service._authorize(request, phase)
    state.service._finish(request, ExecutionResult("uncertain", "exit_evidence_missing"))
    row = run_row(state, proposal)
    assert (row["phase"], row["certainty"], row["reserved"]) == ("uncertain", "unknown", 1)


def _operator(state, *extra, execution="missing-execution"):
    args = [
        "--control-db",
        state.env.db.path,
        "--authority-directory",
        str(state.config.authority_directory),
        "--workspace-directory",
        str(state.env.path.parent),
        "--workspace",
        state.env.workspace,
        "--note",
        "Independent reconciliation evidence",
        *extra,
        execution,
    ]
    script = "import sys; from tests._compute_filesystem import private_filesystem; from commonplace.compute.operator import main; \nwith private_filesystem(sys.argv[1]): main(sys.argv[2:])"
    return subprocess.run(
        [sys.executable, "-c", script, str(Path(state.env.db.path).parent), *args],
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.mark.parametrize("domain", ["control", "boards", "compute", "custody"])
def test_minor3_operator_refuses_missing_guard_before_any_attach(local_state, domain):
    state = local_state
    paths = {
        "control": Path(state.env.db.path),
        "boards": state.env.path.parent / "boards.sqlite3",
        "compute": state.env.path,
        "custody": state.env.path.parent / "compute-credentials.sqlite3",
    }
    with sqlite3.connect(paths[domain]) as conn:
        name = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'compute_authority_v1_%' ORDER BY name"
        ).fetchone()[0]
        conn.execute(f"DROP TRIGGER {name}")
    before = {}
    for key, path in paths.items():
        with sqlite3.connect(path) as conn:
            before[key] = list(
                conn.execute(
                    "SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
                )
            )
    completed = _operator(state)
    assert completed.returncode != 0
    for key, path in paths.items():
        with sqlite3.connect(path) as conn:
            assert (
                list(
                    conn.execute(
                        "SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
                    )
                )
                == before[key]
            )
