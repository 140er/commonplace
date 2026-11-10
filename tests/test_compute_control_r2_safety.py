"""Operator recovery, fencing and exec-stage safety for round two."""

import sqlite3
import subprocess
import sys
from dataclasses import replace

import pytest

from commonplace.compute.control import StoreError
from commonplace.compute.fence import FenceDenied
from commonplace.compute.protocol import ProtocolDenied, ReplayJournal, encode_frame
from tests.test_compute_admission import admission_state as admission_state
from tests.test_compute_control import compute_env as compute_env
from tests.test_compute_control_r2 import Controller, _operator
from tests.test_compute_executor import execution_env as execution_env
from tests.test_compute_fence import local_state as local_state
from tests.test_compute_passive_admission import (
    _claimed,
    _row,
    _submit,
    _writer,
    passive_state as passive_state,
)
from tests.test_compute_transport import BytesChannel, make_helper, setup as setup

pytestmark = pytest.mark.usefixtures("compute_private_filesystem")


def _helper_cli(root, path, kind, workspace, gateway, object_id):
    args = [
        "--kind",
        kind,
        "--journal",
        str(path),
        "--workspace",
        workspace,
        "--gateway",
        gateway,
        "--note",
        "Verified helper process and sockets stopped",
        object_id,
    ]
    script = "import sys; from tests._compute_filesystem import private_filesystem; from commonplace.compute.operator import main; \nwith private_filesystem(sys.argv[1]): main(sys.argv[2:])"
    return subprocess.run(
        [sys.executable, "-c", script, str(root), *args], text=True, capture_output=True, timeout=15
    )


def test_close_failure_remains_counted_until_operator_confirms_stop(setup):
    class Target(BytesChannel):
        def close(self):
            raise OSError("synthetic close failure")

    helper, journal, _ = make_helper(setup, opener=lambda *args: Target())
    with pytest.raises(OSError, match="synthetic close failure"):
        helper.serve(
            Controller(encode_frame(setup.request.message())), wait_readable=lambda *args: []
        )
    assert journal.state(setup.request) == "uncertain"
    journal.operator_release(
        setup.request.workspace,
        setup.request.gateway,
        setup.request.execution,
        "Helper stopped independently",
    )
    journal.uncertain(setup.request)  # late worker cannot re-reserve a reconciled row
    assert journal.state(setup.request) == "operator_closed"


@pytest.mark.parametrize("state", ["consumed", "uncertain"])
def test_helper_cli_reconciles_named_row_and_preserves_replay(setup, tmp_path, state):
    path = tmp_path / "helper.sqlite3"
    conn = sqlite3.connect(path)
    path.chmod(0o600)
    journal = ReplayJournal(conn)
    journal.consume(setup.request, "a" * 64)
    if state == "uncertain":
        journal.uncertain(setup.request)
    try:
        completed = _helper_cli(
            tmp_path,
            path,
            "tunnel",
            setup.request.workspace,
            setup.request.gateway,
            setup.request.execution,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == "helper_operator_closed"
        assert journal.state(setup.request) == "operator_closed"
        assert (
            conn.execute("SELECT note FROM compute_helper_operator_audit").fetchone()[0]
            == "Verified helper process and sockets stopped"
        )
        with pytest.raises(ProtocolDenied, match="replayed_execution"):
            journal.consume(setup.request, "b" * 64)
        with pytest.raises(ProtocolDenied, match="counted_helper_row_required"):
            journal.operator_release(
                setup.request.workspace, setup.request.gateway, setup.request.execution, "Duplicate"
            )
        journal.consume(replace(setup.request, execution="next", attempt="next"), "c" * 64)
    finally:
        conn.close()


def test_helper_operator_obeys_admission_transaction_fence(setup, tmp_path):
    path = tmp_path / "helper.sqlite3"
    conn = sqlite3.connect(path, isolation_level=None)
    path.chmod(0o600)
    journal = ReplayJournal(conn)
    journal.consume(setup.request, "a" * 64)
    conn.execute("BEGIN IMMEDIATE")
    try:
        completed = _helper_cli(
            tmp_path,
            path,
            "tunnel",
            setup.request.workspace,
            setup.request.gateway,
            setup.request.execution,
        )
        assert completed.returncode != 0
        assert journal.state(setup.request) == "consumed"
    finally:
        conn.rollback()
        conn.close()


def test_passive_helper_has_operator_recovery_too(passive_state, tmp_path):
    state = passive_state
    request, _, _, challenge = _claimed(state)
    path = tmp_path / "passive-helper.sqlite3"
    conn = sqlite3.connect(path)
    path.chmod(0o600)
    journal = ReplayJournal(conn)
    permit = challenge.message("passive_permit")
    journal.consume_passive(request, challenge, permit, concurrency=2)
    journal.finish_passive(request)
    try:
        completed = _helper_cli(
            tmp_path,
            path,
            "passive",
            request.context.workspace,
            request.context.gateway,
            request.discovery,
        )
        assert completed.returncode == 0, completed.stderr
        journal.finish_passive(request)  # late completion cannot re-reserve it
        assert (
            conn.execute("SELECT state FROM compute_passive_journal").fetchone()[0]
            == "operator_closed"
        )
        assert (
            conn.execute("SELECT purpose FROM compute_helper_operator_audit").fetchone()[0]
            == "compute-passive-collection"
        )
        with pytest.raises(ProtocolDenied, match="replayed_discovery"):
            journal.consume_passive(request, challenge, permit, concurrency=2)
    finally:
        conn.close()


def test_discovery_cli_settles_lost_response_and_keeps_usage_charged(passive_state):
    state = passive_state
    item = _submit(state)
    state.ssh(fault="lost_result")
    assert state.discovery._run_once()
    before = _row(state, item["id"])
    assert before["state"] == "uncertain" and before["slot"] == 1
    state.discovery._worker = "new-worker"
    completed = _operator(state, "--kind", "discovery", execution=item["id"])
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "discovery_operator_settled"
    row = _row(state, item["id"])
    assert (row["state"], row["reason"], row["slot"]) == (
        "operator_settled",
        "discovery_operator_settled",
        0,
    )
    assert row["usage"] == before["usage"] and row["observations"] == "[]"
    with state.discovery.store._journal() as conn:
        assert (
            conn.execute("SELECT note FROM discovery_operator_audit").fetchone()[0]
            == "Independent reconciliation evidence"
        )
        assert conn.execute("SELECT count(*) FROM discovery_attempts").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM discovery_permits").fetchone()[0] == 1
    assert not state.discovery._run_once()


@pytest.mark.parametrize("kind", ["missing_fence", "not_uncertain", "blank_note", "too_long"])
def test_discovery_operator_requires_fence_state_and_note(passive_state, kind):
    state = passive_state
    item = _submit(state)
    request, _, _ = state.discovery._claim()
    if kind != "not_uncertain":
        with state.discovery.store._journal() as conn:
            conn.execute(
                "UPDATE discoveries SET state='uncertain' WHERE id=?", (request.discovery,)
            )
    if kind == "missing_fence":
        state.discovery.control._authority_fence = None
    note = " " if kind == "blank_note" else "x" * 1001 if kind == "too_long" else "Confirmed stop"
    with pytest.raises((StoreError, FenceDenied)):
        state.discovery.settle_uncertain(request.discovery, note)
    assert _row(state, item["id"])["slot"] == 1


@pytest.mark.parametrize("kind", ["blank_note", "unknown_row", "not_counted"])
def test_helper_operator_requires_named_counted_row_and_note(setup, kind):
    _, journal, _ = make_helper(setup)
    journal.consume(setup.request, "a" * 64)
    if kind == "not_counted":
        journal.closed(setup.request)
    with pytest.raises(ProtocolDenied):
        journal.operator_release(
            setup.request.workspace,
            setup.request.gateway,
            "unknown" if kind == "unknown_row" else setup.request.execution,
            " " if kind == "blank_note" else "External evidence",
        )
    assert journal.state(setup.request) == ("closed" if kind == "not_counted" else "consumed")


def test_scope_revocation_is_ordered_and_denies_the_scoped_discovery(passive_state):
    state = passive_state
    request, _, _, challenge = _claimed(state)
    child = _writer(state, "scope")
    try:
        with state.discovery._guard():
            child.go()
            assert not child.channel.poll(0.1)
        assert child.receive() == "committed"
        with pytest.raises(ProtocolDenied, match="authority_denied"):
            state.discovery._wire_admission(request, challenge)
        assert _row(state, request.discovery)["slot"] == 1
    finally:
        child.close()
