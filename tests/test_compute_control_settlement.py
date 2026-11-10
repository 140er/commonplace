"""Settlement safety, legacy guard migration and trusted operator CLI coverage."""

import subprocess
import sys
from pathlib import Path

import pytest

from commonplace.compute.control import StoreError
from commonplace.compute.fence import FenceDenied, _guards
from commonplace.compute.protocol import ProtocolDenied, decode_frame, encode_frame
from commonplace.compute.script_sandbox import ScriptResult
from commonplace.compute.scripts import ScriptDenied
from commonplace.compute.transport import ExecutionResult
from tests.test_compute_admission import admission_state as admission_state
from tests.test_compute_control import compute_env as compute_env
from tests.test_compute_executor import execution_env as execution_env, run_row
from tests.test_compute_fence import local_state as local_state
from tests.test_compute_script_integration import (
    WireOnlyTarget,
    propose_script,
    script_ssh as script_ssh,
    script_state as script_state,
)
from tests.test_compute_transport import BytesChannel, make_helper, setup as setup

pytestmark = pytest.mark.usefixtures("compute_private_filesystem")


def test_legacy_guards_are_narrowed_without_releasing_claims(local_state):
    state, db = local_state, local_state.env.db
    request, _, _ = state.service._claim()
    coordinator = state.coordinator
    with coordinator.locked():
        # Recreate the exact persisted guard SQL from the reviewed revision.
        for name, sql in _guards("control", coordinator.identity, legacy=True):
            db._conn.execute(f"DROP TRIGGER {name}")
            db._conn.execute(sql)
        coordinator.attach(db._conn, db.path, "control")
    with state.fence.hold(state.env.workspace) as lease:
        before = lease.epoch
    with db._lock:
        db._conn.execute("UPDATE connections SET last_used_at='2026-11-10T00:00:00Z'")
    with state.fence.hold(state.env.workspace) as lease:
        assert lease.epoch == before
    assert run_row(state, state.proposal)["attempt_id"] == request.attempt
    assert run_row(state, state.proposal)["reserved"] == 1
    assert state.service._authorize(request, "connect")


def test_unknown_guard_sql_is_not_migrated(local_state):
    state, db = local_state, local_state.env.db
    with state.coordinator.locked():
        name = "compute_authority_v1_connections_update"
        db._conn.execute(f"DROP TRIGGER {name}")
        db._conn.execute(f"CREATE TRIGGER {name} BEFORE UPDATE ON connections BEGIN SELECT 1; END")
        with pytest.raises(FenceDenied, match="authority_guard_mismatch"):
            state.coordinator.attach(db._conn, db.path, "control")


def _uncertain(state):
    request, _, _ = state.service._claim()
    for phase in ("connect", "dispatch"):
        assert state.service._authorize(request, phase)
    assert state.service._gateway_admission(request, "admit")
    assert state.service._authorize(request, "target_auth")
    state.service._finish(request, ExecutionResult("uncertain", "ssh_transport_failed"))
    assert run_row(state, state.proposal)["reserved"] == 1
    return request


def test_operator_cli_settles_real_uncertainty_without_opening_keys(local_state):
    state = local_state
    request = _uncertain(state)
    # A separate trusted process participates in the real coordinator. Its only
    # test seam models ownership of container ancestors, as the fixture does.
    state.key_path.unlink()
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
        "Inspected the target and confirmed termination",
        request.execution,
    ]
    script = (
        "import sys; from tests._compute_filesystem import private_filesystem; "
        "from commonplace.compute.operator import main; "
        "\nwith private_filesystem(sys.argv[1]): main(sys.argv[2:])"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(Path(state.env.db.path).parent),
            *args,
        ],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "operator_settled"
    row = run_row(state, state.proposal)
    assert (row["phase"], row["certainty"], row["reserved"]) == ("terminal", "known", 0)
    assert row["operator_note"] == args[-2]
    assert row["exit_code"] is None
    assert not state.key_path.exists()
    assert not state.service._authorize(request, "execute")


@pytest.mark.parametrize("kind", ["missing_fence", "wrong_state", "blank_note", "oversized_note"])
def test_operator_settle_requires_fence_uncertain_row_and_bounded_note(local_state, kind):
    state = local_state
    request = _uncertain(state)
    note = "Independently confirmed termination"
    if kind == "missing_fence":
        state.service.fence = None
    elif kind == "wrong_state":
        state.service.settle_uncertain(request.execution, note)
    elif kind == "blank_note":
        note = "  "
    else:
        note = "x" * 1001
    with pytest.raises((StoreError, FenceDenied)):
        state.service.settle_uncertain(request.execution, note)
    if kind != "wrong_state":
        assert run_row(state, state.proposal)["reserved"] == 1


def test_pre_target_auth_permit_failure_is_known(admission_state):
    state = admission_state
    request, _, _ = state.service._claim()
    for phase in ("connect", "dispatch"):
        assert state.service._authorize(request, phase)
    from commonplace.compute.protocol import AdmissionChallenge

    challenge = AdmissionChallenge(request, "c" * 64, "a" * 64, request.start_by)
    assert state.service._gateway_admission(request, "admit", challenge=challenge)
    state.service._finish(request, ExecutionResult("uncertain", "target_hostkey_mismatch"))
    row = run_row(state, state.proposal)
    assert (row["phase"], row["reserved"]) == ("terminal", 0)


def test_abnormally_ended_helper_stays_reserved(setup):
    helper, journal, _ = make_helper(setup)

    class Controller(BytesChannel):
        def sendall(self, data):
            message = decode_frame(data)
            if message["kind"] == "ready":
                self.incoming.extend(encode_frame({**message, "kind": "go"}))
            super().sendall(data)

    wire = Controller(encode_frame(setup.request.message()))
    with pytest.raises(ProtocolDenied, match="deadline_exceeded"):
        helper.serve(wire, wait_readable=lambda *args: [])
    assert journal.state(setup.request) == "uncertain"
    assert wire.closed
    journal.closed(setup.request)
    assert journal.state(setup.request) == "uncertain"


@pytest.mark.parametrize(
    "kind", ["helper_unavailable", "invalid_frame", "unconfirmed", "local_binding_mismatch"]
)
def test_script_unknowns_and_local_errors_keep_reservations(
    script_state, script_ssh, monkeypatch, kind
):
    state = script_state
    proposal = propose_script(state)

    def uncertain(self, request, script, **kwargs):
        if kind == "unconfirmed":
            return ScriptResult("denied", "admission_expired", termination_confirmed=False)
        raise ScriptDenied(kind if kind != "local_binding_mismatch" else "helper_unavailable")

    monkeypatch.setattr(WireOnlyTarget, "execute_verified", uncertain)
    if kind == "local_binding_mismatch":
        from commonplace.compute import script_transport

        def local_error(*args):
            raise ScriptDenied("binding_mismatch")

        monkeypatch.setattr(script_transport, "exchange", local_error)
    script_ssh()
    assert state.service._run_once()
    row = run_row(state, proposal)
    assert (row["phase"], row["certainty"], row["reserved"]) == ("uncertain", "unknown", 1)
