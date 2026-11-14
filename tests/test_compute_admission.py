"""Authenticated SSH admission, guarded stores and synthetic socketpairs only.

The integration maintainer runs this suite; its author runs lint/syntax only.
No LAN, listeners, device keys, model calls or independently granted authority.
"""

import json
import os
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from commonplace import compute
from commonplace.compute.executor import ExecutionService
from commonplace.compute.fence import LOCAL_DOMAINS, FenceDenied, _held
from commonplace.compute.protocol import (
    ADMISSION_MODE,
    AdmissionChallenge,
    ExecutionRequest,
    GatewayHelper,
    Limits,
    ProtocolDenied,
    ReplayJournal,
    canonical,
    decode_frame,
    encode_frame,
)
from commonplace.compute.transport import ExecutionResult, SSHTransport
from tests.test_compute_control import compute_env as compute_env
from tests.test_compute_executor import (
    execution_env as execution_env,
    prepare,
    run_row,
    view,
)
from tests.test_compute_fence import Child, local_state as local_state
from tests.test_compute_ssh_fixture import SocketPairAdmissionSSH, _server_key
from tests.test_compute_transport import BytesChannel, setup as setup

pytestmark = pytest.mark.skipif(os.name != "posix", reason="guarded POSIX custody")


def _wire_helper(setup, path, *, opener=None):
    request = replace(setup.request, admission_mode=ADMISSION_MODE, approval_digest="e" * 64)
    connection = sqlite3.connect(path)
    journal = ReplayJournal(connection)
    opened = []

    def open_route(route, timeout):
        assert journal.state(request) == "consumed"
        opened.append(route)
        return BytesChannel()

    helper = GatewayHelper(
        setup.gateway,
        [setup.route],
        journal,
        lambda req, phase: True,
        opener or open_route,
        binding=(request.workspace, request.gateway),
        enabled=True,
        admission_mode=ADMISSION_MODE,
        wall=setup.clock,
        monotonic=setup.clock,
    )
    return request, helper, connection, journal, opened


def _challenge(helper, request):
    ready = helper.prepare(request)
    message = helper.challenge(request, ready, {**ready, "kind": "go"})
    return ready, AdmissionChallenge.from_message(message, request, ready)


def test_wire_mode_is_explicit_exact_and_digest_bound(setup):
    request = replace(setup.request, admission_mode=ADMISSION_MODE, approval_digest="e" * 64)
    assert request.digest != setup.request.digest
    assert ExecutionRequest.from_message(decode_frame(encode_frame(request.message()))) == request
    assert "admission_mode" not in setup.request.message()
    for mode in ("unknown", "", True, 1):
        with pytest.raises(ProtocolDenied):
            replace(request, admission_mode=mode)
    with pytest.raises(ProtocolDenied):
        replace(request, approval_digest=None)
    message = request.message()
    del message["approval_digest"]
    with pytest.raises(ProtocolDenied):
        ExecutionRequest.from_message(message)
    with pytest.raises(ProtocolDenied):
        SSHTransport(lambda *args: True, admission_mode=ADMISSION_MODE)
    with pytest.raises(ProtocolDenied):
        SSHTransport(lambda *args: True, gateway_admission=lambda *args: {})


@pytest.mark.parametrize(
    "field,value",
    [
        ("v", 2),
        ("admission_v", 2),
        ("admission_v", True),
        ("admission_mode", "unknown"),
        ("authority_epoch", 9),
        ("authority_epoch", True),
        ("execution", "other"),
        ("attempt", "other"),
        ("digest", "a" * 64),
        ("approval_digest", "a" * 64),
        ("nonce", "a" * 64),
        ("channel", "a" * 64),
        ("start_by", 1003),
        ("run_until", 1040),
        ("kind", "opened"),
    ],
)
def test_helper_rejects_tampered_permit_without_open(setup, tmp_path, field, value):
    request, helper, conn, journal, opened = _wire_helper(setup, tmp_path / "helper.sqlite3")
    try:
        _, challenge = _challenge(helper, request)
        permit = {**challenge.message("admission_permit"), field: value}
        with pytest.raises(ProtocolDenied):
            helper.admit_permit(request, challenge, permit)
        assert not opened and journal.state(request) is None
        with pytest.raises(ProtocolDenied):
            helper.admit_permit(request, challenge, challenge.message("admission_permit"))
    finally:
        conn.close()


def test_helper_requires_go_then_exact_challenge_and_never_legacy_falls_back(setup, tmp_path):
    request, helper, conn, journal, opened = _wire_helper(setup, tmp_path / "helper.sqlite3")
    try:
        with pytest.raises(ProtocolDenied, match="unsupported_admission_mode"):
            helper.prepare(setup.request)
        ready = helper.prepare(request)
        forged = AdmissionChallenge(request, ready["channel"], ready["nonce"], ready["start_by"])
        with pytest.raises(ProtocolDenied):
            helper.admit_permit(request, forged, forged.message("admission_permit"))
        ready = helper.prepare(request)
        with pytest.raises(ProtocolDenied, match="admission_permit_required"):
            helper.admit(request, ready, {**ready, "kind": "go"})
        with pytest.raises(ProtocolDenied):
            helper.challenge(request, ready, {**ready, "kind": "admission_permit"})
        assert journal.state(request) is None and not opened
    finally:
        conn.close()


def test_helper_wall_expiry_is_exclusive(setup, tmp_path):
    request, helper, conn, journal, opened = _wire_helper(setup, tmp_path / "helper.sqlite3")
    try:
        _, challenge = _challenge(helper, request)
        setup.clock.now = challenge.start_by
        with pytest.raises(ProtocolDenied):
            helper.admit_permit(request, challenge, challenge.message("admission_permit"))
        assert not opened and journal.state(request) is None
    finally:
        conn.close()


def test_helper_monotonic_expiry_survives_wall_clock_rollback(setup, tmp_path):
    request, helper, conn, journal, opened = _wire_helper(setup, tmp_path / "helper.sqlite3")
    tick = [setup.clock.now]
    helper.monotonic = lambda: tick[0]
    try:
        _, challenge = _challenge(helper, request)
        tick[0] = challenge.start_by
        setup.clock.now -= 1
        with pytest.raises(ProtocolDenied):
            helper.admit_permit(request, challenge, challenge.message("admission_permit"))
        assert not opened and journal.state(request) is None
    finally:
        conn.close()


def test_helper_control_budget_denies_before_ready_or_route(setup, tmp_path):
    request, helper, conn, journal, opened = _wire_helper(setup, tmp_path / "helper.sqlite3")
    request = replace(request, limits=Limits(tunnel_bytes=100))
    channel = BytesChannel(encode_frame(request.message()))
    try:
        with pytest.raises(ProtocolDenied, match="tunnel_budget_exceeded"):
            helper.serve(channel)
        assert not channel.sent and not opened and journal.state(request) is None
    finally:
        conn.close()


def test_helper_durable_replay_survives_restart_and_does_not_free_unknown(setup, tmp_path):
    path = tmp_path / "helper.sqlite3"
    request, helper, conn, journal, opened = _wire_helper(setup, path)
    _, challenge = _challenge(helper, request)
    helper.admit_permit(request, challenge, challenge.message("admission_permit"))
    journal.uncertain(request)
    conn.close()
    assert len(opened) == 1
    request, helper, conn, journal, opened = _wire_helper(setup, path)
    try:
        assert journal.state(request) == "uncertain"
        _, fresh = _challenge(helper, request)
        with pytest.raises(ProtocolDenied):
            helper.admit_permit(request, fresh, challenge.message("admission_permit"))
        _, fresh = _challenge(helper, request)
        with pytest.raises(ProtocolDenied, match="replayed_execution"):
            helper.admit_permit(request, fresh, fresh.message("admission_permit"))
        assert not opened and journal.state(request) == "uncertain"
    finally:
        conn.close()


@pytest.fixture
def admission_state(local_state, monkeypatch):
    """Reuse real provisioned guarded stores, selecting the trusted new mode.

    The actual lifecycle starts its threads; their entry is inert in the fixture
    so each test controls exactly when _run_once begins. No fake authority fence
    or fake transport is used for the socketpair cases.
    """
    state = local_state
    state.gateway_host, state.target_host = _server_key(), _server_key()
    with state.env.store._journal() as conn:
        for table, record, host in (
            ("gateways", state.gateway, state.gateway_host),
            ("nodes", state.node, state.target_host),
        ):
            metadata = json.loads(
                conn.execute(
                    f"SELECT metadata FROM {table} WHERE id=?", (record["id"],)
                ).fetchone()[0]
            )
            from commonplace.compute.protocol import HostKeyPin

            metadata["host_key"] = HostKeyPin(host.get_name(), host.asbytes()).document()
            conn.execute(
                f"UPDATE {table} SET metadata=? WHERE id=?",
                (canonical(metadata).decode(), record["id"]),
            )
    state.config = replace(
        state.config,
        workspaces=(replace(state.config.workspaces[0], admission_mode=ADMISSION_MODE),),
    )
    state.env.db._compute_config = state.config
    state.env.gateway.compute_installed = False
    with monkeypatch.context() as patch:
        patch.setattr(ExecutionService, "_work", lambda self: None)
        compute.install(state.env.gateway)
    state.service = state.env.gateway._compute_services[state.env.workspace]
    state.fence = state.env.gateway._compute_fences[state.env.workspace]
    assert state.service.admission_mode == ADMISSION_MODE
    state.env.store = state.service.store
    state.env.store.clock = lambda: state.env.clock.now
    state.proposal = prepare(state, request_id="ssh-admission")
    fixtures = []

    def ssh(**kwargs):
        fixture = SocketPairAdmissionSSH(state, state.gateway_host, state.target_host, **kwargs)
        fixtures.append(fixture)
        state.service._driver = fixture.driver
        return fixture

    state.ssh = ssh
    yield state
    state.service.stop()
    for fixture in fixtures:
        fixture.close()


def _writer(state, action):
    # The mutation child installs the same genuine local guards but never starts
    # an execution worker. Its legacy/off mode grants no remote participation.
    config = replace(
        state.config,
        workspaces=(replace(state.config.workspaces[0], admission_mode=None),),
    )
    return Child(SimpleNamespace(**{**vars(state), "config": config}), action)


def _claimed_challenge(state):
    request, gateway, route = state.service._claim()
    assert state.service._authorize(request, "connect")
    assert state.service._authorize(request, "dispatch")
    return request, AdmissionChallenge(request, "c" * 64, "a" * 64, request.start_by)


def _admissions(state):
    with state.env.store._journal() as conn:
        return conn.execute("SELECT count(*) FROM gateway_admissions").fetchone()[0]


def test_trusted_mode_is_bound_to_canonical_approval_and_local_lease_stays_local(admission_state):
    state = admission_state
    with state.env.store._journal() as conn:
        row = conn.execute(
            "SELECT binding FROM executions WHERE id=?", (state.proposal["id"],)
        ).fetchone()
        assert json.loads(row[0])["admission_mode"] == ADMISSION_MODE
    with state.fence.hold(state.env.workspace) as lease:
        assert lease.domains == LOCAL_DOMAINS
    with pytest.raises(FenceDenied):
        with _held(state.fence, state.env.workspace):
            pass
    request, _ = _claimed_challenge(state)
    assert request.admission_mode == ADMISSION_MODE
    assert request.approval_digest
    assert not state.service._authorize(request, "target_auth")  # no wire permit
    assert not state.service._gateway_admission(request, "admit")
    assert _admissions(state) == 0


def test_declared_mode_without_trusted_canonical_configuration_is_no_permission(local_state):
    state = local_state
    service = ExecutionService(
        state.env.store,
        enabled=True,
        fence=state.fence,
        wrapping_key_path=state.key_path,
        admission_mode=ADMISSION_MODE,
    )
    assert not service._run_once()
    assert run_row(state, state.proposal) is None
    assert _admissions(state) == 0
    assert not state.driver.calls


def test_mode_change_and_old_approval_never_gain_dispatch(admission_state):
    state = admission_state
    state.env.db._compute_config = replace(
        state.config, workspaces=(replace(state.config.workspaces[0], admission_mode=None),)
    )
    assert state.service._claim() is None
    assert run_row(state, state.proposal) is None


@pytest.mark.parametrize("change", ["attempt", "authority_epoch", "approval_digest", "node_epoch"])
def test_controller_binds_one_immutable_attempt(admission_state, change):
    state = admission_state
    request, challenge = _claimed_challenge(state)
    changed = replace(
        request,
        **{
            change: "other"
            if change == "attempt"
            else ("b" * 64 if change == "approval_digest" else getattr(request, change) + 1)
        },
    )
    forged = replace(challenge, request=changed)
    assert not state.service._gateway_admission(changed, "admit", challenge=forged)
    assert _admissions(state) == 0
    assert state.service._gateway_admission(request, "admit", challenge=challenge)
    assert _admissions(state) == 1


def test_permit_is_durable_one_use_and_wrong_result_cannot_release_it(admission_state):
    state = admission_state
    request, challenge = _claimed_challenge(state)
    permit = state.service._wire_admission(request, challenge)
    assert permit == challenge.message("admission_permit")
    assert not state.fence.active and not state.env.db._lock._is_owned()
    assert not state.service._gateway_admission(request, "admit", challenge=challenge)
    with state.env.store._journal() as conn:
        record = conn.execute(
            "SELECT * FROM gateway_admissions WHERE execution_id=?", (request.execution,)
        ).fetchone()
        assert record["helper_nonce"] == challenge.nonce
        assert record["helper_channel"] == challenge.channel
        assert json.loads(record["wire_permit"]) == permit
        for sql in ("DELETE FROM gateway_admissions", "UPDATE gateway_admissions SET issued_at=1"):
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute(sql)
    # A genuinely uncertain target operation must have reached the committed
    # exec boundary. A misleading not_started result cannot override stage 4.
    assert state.service._authorize(request, "target_auth")
    assert state.service._authorize(request, "execute")
    state.service._finish(request, ExecutionResult("not_started", "ssh_transport_failed"))
    assert run_row(state, state.proposal)["reserved"] == 1
    # Reopened controller identity cannot mint another attempt or permit.
    restarted = ExecutionService(
        state.env.store,
        enabled=True,
        fence=state.fence,
        wrapping_key_path=state.key_path,
        admission_mode=ADMISSION_MODE,
    )
    assert restarted._claim() is None
    assert not restarted._gateway_admission(request, "admit", challenge=challenge)
    assert _admissions(state) == 1


@pytest.mark.parametrize(
    "action",
    [
        "session",
        "role",
        "task",
        "assignment",
        "approval",
        "blocker",
        "binding",
        "consent",
        "credential",
        "rotation",
    ],
)
def test_multiprocess_revocation_committed_before_permit_wins(admission_state, action):
    state = admission_state
    request, challenge = _claimed_challenge(state)
    child = _writer(state, action)
    try:
        child.go()
        assert child.receive() == "committed"
        assert not state.service._gateway_admission(request, "admit", challenge=challenge)
        assert _admissions(state) == 0
    finally:
        child.close()


def test_multiprocess_writer_excluded_through_permit_commit_then_can_revoke(admission_state):
    state = admission_state
    request, challenge = _claimed_challenge(state)
    child = _writer(state, "session")
    try:
        with state.service._guard():
            child.go()
            assert not child.channel.poll(0.1)
            permit = state.service._wire_admission(request, challenge)
            assert not child.channel.poll(0)
            assert permit == challenge.message("admission_permit")
        assert child.receive() == "committed"
        assert _admissions(state) == 1
        assert not state.service._authorize(request, "target_auth")
        assert not state.service._gateway_admission(request, "admit", challenge=challenge)
    finally:
        child.close()


def test_actual_guarded_two_hop_ssh_approved_probe_and_redacted_result(admission_state):
    state = admission_state
    fixture = state.ssh()
    fixture.output = (
        b"Linux isolated-fixture x86_64\nsecret: sk-test-abcdefghijklmnopqrstuvwxyz1234567890\n"
    )
    assert state.service._run_once()
    run = run_row(state, state.proposal)
    assert run["phase"] == "terminal" and run["certainty"] == "known", (
        run["reason"],
        fixture.messages,
        fixture.errors,
        fixture.refusals,
    )
    assert run["exit_code"] == 0 and run["reserved"] == 0
    assert fixture.messages == ["ready", "admission_challenge", "opened"]
    assert fixture.authenticated == ["gateway", "target"]
    assert fixture.commands == [b"/usr/bin/uname -srm"]
    assert len(fixture.opened) == 1 and _admissions(state) == 1
    result = json.loads(run["result"])
    assert "sk-test-abcdefghijklmnopqrstuvwxyz1234567890" not in result["output"]
    assert len(run["result"].encode()) <= 8192
    assert view(state, state.proposal)["state"] == "approved"
    assert not state.service._run_once()


@pytest.mark.parametrize("hop", ["gateway", "target"])
def test_actual_both_hop_pins_precede_their_authentication(admission_state, hop):
    state = admission_state
    # Re-sign/re-approve the intentionally wrong pin, so this exercises transport
    # pin refusal rather than failing the earlier exact-action authority check.
    table, record = ("gateways", state.gateway) if hop == "gateway" else ("nodes", state.node)
    from commonplace.compute.protocol import HostKeyPin

    bad = _server_key()
    with state.env.store._journal() as conn:
        metadata = json.loads(
            conn.execute(f"SELECT metadata FROM {table} WHERE id=?", (record["id"],)).fetchone()[0]
        )
        metadata["host_key"] = HostKeyPin(bad.get_name(), bad.asbytes()).document()
        conn.execute(
            f"UPDATE {table} SET metadata=? WHERE id=?",
            (canonical(metadata).decode(), record["id"]),
        )
    state.proposal = prepare(state, request_id="wrong-pin")
    fixture = state.ssh()
    assert state.service._run_once()
    run = run_row(state, state.proposal)
    assert run["reason"] == hop + "_hostkey_mismatch", dict(run)
    assert fixture.authenticated == ([] if hop == "gateway" else ["gateway"])
    assert fixture.commands == []
    assert _admissions(state) == (0 if hop == "gateway" else 1)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_challenge",
        "early_opened",
        "nonce",
        "channel",
        "digest",
        "approval_digest",
        "authority_epoch",
        "admission_v",
        "expiry",
        "unknown_mode",
        "timeout",
        "lost_opened",
        "missing_permit",
        "partial_permit",
        "tamper_permit",
        "expired_permit",
    ],
)
def test_actual_wire_failures_before_target_auth_settle_without_retry(admission_state, fault):
    state = admission_state
    fixture = state.ssh(fault=fault)
    assert state.service._run_once()
    run = run_row(state, state.proposal)
    assert run["stage"] < 3
    assert run["phase"] == "terminal" and run["reserved"] == 0, dict(run)
    assert fixture.commands == []
    expected = 1 if fault == "lost_opened" else 0
    issued = fault in {
        "lost_opened",
        "missing_permit",
        "partial_permit",
        "tamper_permit",
        "expired_permit",
    }
    assert len(fixture.opened) == expected and _admissions(state) == int(issued)
    assert not state.service._run_once()
    assert len(fixture.opened) == expected


@pytest.mark.parametrize("when", ["before", "after"])
def test_actual_wire_revocation_ordering_has_no_network_under_lock(admission_state, when):
    state = admission_state
    child = _writer(state, "session")
    fired = []

    def hook(phase, fixture):
        target = "before_challenge" if when == "before" else "before_helper_permit"
        if phase == target:
            assert not fired
            child.go()
            assert child.receive() == "committed"
            fired.append(phase)

    try:
        fixture = state.ssh(hook=hook)
        assert state.service._run_once()
        assert fired
        assert _admissions(state) == (0 if when == "before" else 1)
        assert len(fixture.opened) == (0 if when == "before" else 1)
        assert fixture.commands == []  # current target auth still refuses
        assert run_row(state, state.proposal)["stage"] < 3
        assert run_row(state, state.proposal)["reserved"] == 0
    finally:
        child.close()
