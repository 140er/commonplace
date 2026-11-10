"""Deterministic transport boundary tests: no sockets, keys on disk or servers."""

import json
import pickle
import sqlite3
import struct
import threading
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

from commonplace.compute.protocol import (
    MAX_FRAME_BYTES,
    EnrolledRoute,
    ExecutionRequest,
    FrameIO,
    GatewayEnrollment,
    GatewayHelper,
    HostKeyPin,
    Limits,
    ProtocolDenied,
    ReplayJournal,
    admission_message,
    compile_operation,
    decode_frame,
    encode_frame,
    enrollment_digest,
)
from commonplace.compute.transport import (
    BudgetedChannel,
    EphemeralKey,
    ParamikoDriver,
    SSHTransport,
)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class BytesChannel:
    def __init__(self, incoming=b"", *, fragment=2**20):
        self.incoming = bytearray(incoming)
        self.sent = bytearray()
        self.closed = False
        self.timeout = None
        self.fragment = fragment

    def settimeout(self, timeout):
        self.timeout = timeout

    def send(self, data):
        self.sent.extend(data)
        return len(data)

    def sendall(self, data):
        self.send(data)

    def recv(self, size):
        size = min(size, self.fragment)
        result = bytes(self.incoming[:size])
        del self.incoming[:size]
        return result

    def close(self):
        self.closed = True


@pytest.fixture
def setup():
    clock = Clock()
    gateway = GatewayEnrollment(
        "workspace",
        "gateway",
        "192.0.2.10",
        22,
        "gateway-user",
        HostKeyPin("ssh-ed25519", b"g" * 32),
        1,
        2,
        "gateway-key",
    )
    route = EnrolledRoute(
        "workspace",
        "gateway",
        "node",
        "10.77.0.9",
        22,
        "eth0",
        "target-user",
        HostKeyPin("ssh-ed25519", b"t" * 32),
        3,
        4,
        "target-key",
    )
    request = ExecutionRequest(
        workspace="workspace",
        project="project",
        user="user",
        agent="agent",
        task="task",
        execution="execution",
        attempt="attempt",
        gateway="gateway",
        node="node",
        gateway_epoch=1,
        node_epoch=3,
        gateway_credential_version=2,
        target_credential_version=4,
        authority_epoch=8,
        enrollment=enrollment_digest(gateway, route),
        start_by=1004,
        run_until=1030,
    )
    return SimpleNamespace(clock=clock, gateway=gateway, route=route, request=request)


def make_helper(setup, *, connection=None, authorize=None, opener=None, limits=None, enabled=True):
    db = connection if connection is not None else sqlite3.connect(":memory:")
    journal = ReplayJournal(db)
    opened = []
    nonce_counter = iter(range(1, 100))

    def open_route(route, timeout):
        # Committed consumption is visible before the first route effect.
        assert journal.state(setup.request) == "consumed"
        opened.append((route, timeout))
        return BytesChannel()

    helper = GatewayHelper(
        setup.gateway,
        [setup.route],
        journal,
        authorize or (lambda request, phase: True),
        opener or open_route,
        binding=("workspace", "gateway"),
        limits=limits,
        enabled=enabled,
        wall=setup.clock,
        monotonic=setup.clock,
        nonce=lambda: f"{next(nonce_counter):064x}",
    )
    return helper, journal, opened


def test_fixed_operation_has_no_user_interpolation():
    assert compile_operation("system.info.v1", {}) == "/usr/bin/uname -srm"
    for operation, args in (
        ("shell", {}),
        ("system.info.v1; id", {}),
        ("system.info.v1", {"script": "id"}),
        ("system.info.v1", {"command": "id"}),
        ("system.info.v1", {"flags": "-a"}),
        ("system.info.v1", []),
    ):
        with pytest.raises(ProtocolDenied, match="unsupported_operation"):
            compile_operation(operation, args)


def test_request_roundtrip_is_exact_immutable_and_digest_binds_context(setup):
    request = setup.request
    assert ExecutionRequest.from_message(decode_frame(encode_frame(request.message()))) == request
    with pytest.raises(FrozenInstanceError):
        request.node = "other"
    with pytest.raises(TypeError):
        request.arguments["command"] = "id"
    for change in (
        {"task": "other"},
        {"authority_epoch": 9},
        {"attempt": "second"},
        {"limits": Limits(output_bytes=100)},
        {"run_until": 1029},
    ):
        assert replace(request, **change).digest != request.digest


@pytest.mark.parametrize(
    "name,value",
    [
        ("host", "10.77.0.10"),
        ("port", 2222),
        ("command", "id"),
        ("script", "id"),
        ("ssh_options", "ProxyCommand=id"),
        ("active", True),
    ],
)
def test_unknown_request_fields_deny_before_route(setup, name, value):
    message = {**setup.request.message(), name: value}
    with pytest.raises(ProtocolDenied, match="invalid_fields"):
        ExecutionRequest.from_message(message)


@pytest.mark.parametrize(
    "changes",
    [
        {"gateway_epoch": True},
        {"node_epoch": 0},
        {"start_by": float("nan")},
        {"run_until": float("inf")},
        {"enrollment": "f"},
        {"workspace": "\nforeign"},
        {"arguments": {"command": "id"}},
    ],
)
def test_malformed_request_values_deny(setup, changes):
    with pytest.raises(ProtocolDenied):
        ExecutionRequest.from_message({**setup.request.message(), **changes})


@pytest.mark.parametrize(
    "address",
    [
        "example.invalid",
        "127.0.0.1",
        "169.254.169.254",
        "8.8.8.8",
        "224.0.0.1",
        "0.0.0.0",
        "::1",
        "::ffff:10.77.0.9",
        "fe80::1",
        "fc00::1%eth0",
        "10.77.0.255",
    ],
)
def test_protected_routes_require_safe_numeric_private_destination(setup, address):
    with pytest.raises(ProtocolDenied):
        replace(setup.route, address=address)


def test_full_binding_pins_endpoints_accounts_versions_are_in_digest(setup):
    original = setup.request.enrollment
    for changes in (
        {"address": "10.77.0.10"},
        {"port": 2222},
        {"interface": "eth1"},
        {"account": "other"},
        {"epoch": 4},
        {"credential_version": 5},
        {"credential_id": "other-key"},
        {"pin": HostKeyPin("ssh-ed25519", b"x" * 32)},
    ):
        assert enrollment_digest(setup.gateway, replace(setup.route, **changes)) != original
    assert enrollment_digest(replace(setup.gateway, epoch=2), setup.route) != original


def test_frames_reject_duplicate_nonfinite_nested_and_size_fields():
    for body in (
        b'{"v":1,"v":1}',
        b'{"a":{"x":1,"x":2}}',
        b'{"x":NaN}',
        b'{"x":1e999}',
        b"[]",
        b"\xff",
    ):
        with pytest.raises(ProtocolDenied):
            decode_frame(struct.pack("!I", len(body)) + body)
    with pytest.raises(ProtocolDenied, match="frame_too_large"):
        encode_frame({"data": "x" * MAX_FRAME_BYTES})
    for prefix in (0, MAX_FRAME_BYTES + 1):
        with pytest.raises(ProtocolDenied):
            FrameIO(BytesChannel(struct.pack("!I", prefix)), 1001, monotonic=lambda: 1000).receive()


def test_framing_handles_fragmentation_without_eating_ssh_bytes(setup):
    wire = BytesChannel(encode_frame(setup.request.message()) + b"SSH-2.0-fixture\r\n", fragment=1)
    io = FrameIO(wire, 1001, monotonic=setup.clock)
    assert io.receive() == setup.request.message()
    assert wire.incoming == b"SSH-2.0-fixture\r\n"
    assert io.used == len(encode_frame(setup.request.message()))
    setup.clock.now = 1001
    with pytest.raises(ProtocolDenied, match="deadline_exceeded"):
        io.send({"v": 1})


def test_enrolled_route_journal_before_open_and_no_second_attempt(setup):
    helper, journal, opened = make_helper(setup)
    ready = helper.prepare(setup.request)
    helper.admit(setup.request, ready, {**ready, "kind": "go"})
    assert opened == [(setup.route, 4.0)]
    assert journal.state(setup.request) == "consumed"
    second = replace(setup.request, attempt="second")
    ready = helper.prepare(second)
    with pytest.raises(ProtocolDenied, match="replayed_execution"):
        helper.admit(second, ready, {**ready, "kind": "go"})
    assert len(opened) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"node": "sibling"},
        {"gateway": "foreign"},
        {"workspace": "foreign"},
        {"node_epoch": 4},
        {"gateway_credential_version": 3},
        {"enrollment": "0" * 64},
    ],
)
def test_only_exact_enrollment_can_prepare(setup, changes):
    helper, journal, opened = make_helper(setup)
    with pytest.raises(ProtocolDenied):
        helper.prepare(replace(setup.request, **changes))
    assert opened == []
    assert journal.state(setup.request) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("nonce", "f" * 64),
        ("channel", "e" * 64),
        ("digest", "d" * 64),
        ("attempt", "other"),
        ("run_until", 1031),
        ("start_by", 1005),
        ("extra", "x"),
    ],
)
def test_admission_tampering_cannot_open_route(setup, field, value):
    helper, journal, opened = make_helper(setup)
    ready = helper.prepare(setup.request)
    with pytest.raises(ProtocolDenied):
        helper.admit(setup.request, ready, {**ready, "kind": "go", field: value})
    assert opened == []
    assert journal.state(setup.request) is None
    # A bad attempt consumes the local challenge; it cannot later be reused.
    with pytest.raises(ProtocolDenied):
        helper.admit(setup.request, ready, {**ready, "kind": "go"})


def test_fabricated_challenge_is_not_an_admission(setup):
    helper, _, opened = make_helper(setup)
    ready = admission_message("ready", setup.request, "c" * 64, "a" * 64, 1004)
    with pytest.raises(ProtocolDenied):
        helper.admit(setup.request, ready, {**ready, "kind": "go"})
    assert not opened


def test_authority_is_rechecked_after_ready_and_expiry_is_exclusive(setup):
    allowed = [True]
    helper, journal, opened = make_helper(setup, authorize=lambda request, phase: allowed[0])
    ready = helper.prepare(setup.request)
    allowed[0] = False
    with pytest.raises(ProtocolDenied, match="authority_denied"):
        helper.admit(setup.request, ready, {**ready, "kind": "go"})
    assert journal.state(setup.request) is None
    allowed[0] = True
    ready = helper.prepare(setup.request)
    setup.clock.now = 1004
    with pytest.raises(ProtocolDenied):
        helper.admit(setup.request, ready, {**ready, "kind": "go"})
    assert not opened


def test_journal_survives_reopen_and_uncertain_routes_keep_capacity(setup, tmp_path):
    path = tmp_path / "synthetic-journal.sqlite3"
    db = sqlite3.connect(path)
    helper, journal, opened = make_helper(setup, connection=db, limits=Limits(tunnels=1))
    request = replace(setup.request, limits=Limits(tunnels=1))
    ready = helper.prepare(request)
    helper.admit(request, ready, {**ready, "kind": "go"})
    journal.uncertain(request)
    db.close()
    reopened = sqlite3.connect(path)
    try:
        journal = ReplayJournal(reopened)
        assert journal.state(request) == "uncertain"
        with pytest.raises(ProtocolDenied, match="replayed_execution"):
            journal.consume(replace(request, attempt="retry"), "d" * 64)
        with pytest.raises(ProtocolDenied, match="tunnel_capacity_exceeded"):
            journal.consume(replace(request, execution="second"), "e" * 64)
    finally:
        reopened.close()
    assert len(opened) == 1


def test_route_open_failure_releases_capacity_and_keeps_replay_denial(setup):
    def fail(route, timeout):
        raise OSError("synthetic route failure")

    helper, journal, _ = make_helper(setup, opener=fail)
    ready = helper.prepare(setup.request)
    with pytest.raises(OSError):
        helper.admit(setup.request, ready, {**ready, "kind": "go"})
    assert journal.state(setup.request) == "closed"
    with pytest.raises(ProtocolDenied, match="replayed_execution"):
        journal.consume(setup.request, "f" * 64)


def test_disabled_helper_and_unsupported_passive_never_open(setup):
    helper, _, opened = make_helper(setup, enabled=False)
    with pytest.raises(ProtocolDenied, match="transport_disabled"):
        helper.prepare(setup.request)
    helper.enabled = True
    for kind, expected in (
        ("collect_passive", "passive_source_unavailable"),
        ("active", "invalid_fields"),
    ):
        wire = BytesChannel(
            encode_frame({"v": 1, "kind": kind, "workspace": "workspace", "gateway": "gateway"})
        )
        with pytest.raises(ProtocolDenied, match=expected):
            helper.serve(wire)
    assert not opened


def test_helper_describe_reports_unsupported_sources_honestly(setup):
    helper, _, opened = make_helper(setup)
    wire = BytesChannel(
        encode_frame({"v": 1, "kind": "describe", "workspace": "workspace", "gateway": "gateway"})
    )
    helper.serve(wire)
    description = decode_frame(bytes(wire.sent))
    assert description["methods"] == ["describe", "open_enrolled_ssh"]
    assert description["passive_sources"] == []
    assert not opened


def test_helper_relay_enforces_combined_bytes_and_deadline(setup):
    helper, _, _ = make_helper(setup)
    source, target = BytesChannel(b"12345"), BytesChannel()
    with pytest.raises(ProtocolDenied, match="tunnel_budget_exceeded"):
        helper._relay(source, target, 1001, 4, lambda sources, timeout: [source])
    assert target.sent == b""
    with pytest.raises(ProtocolDenied, match="deadline_exceeded"):
        helper._relay(source, target, 1000, 100, lambda sources, timeout: sources)


def test_helper_full_session_relays_one_route_then_releases_closed_tunnel(setup):
    helper, journal, opened = make_helper(setup)
    target = BytesChannel(b"SSH-2.0-synthetic-target\r\n")

    def open_route(route, timeout):
        assert journal.state(setup.request) == "consumed"
        opened.append(route)
        return target

    helper.open_route = open_route

    class Controller(BytesChannel):
        def sendall(self, data):
            if data.startswith(b"SSH-"):
                self.sent.extend(data)
                return
            message = decode_frame(data)
            if message["kind"] == "ready":
                self.incoming.extend(encode_frame({**message, "kind": "go"}))
            self.sent.extend(data)

    wire = Controller(encode_frame(setup.request.message()))
    # Only the target becomes readable. EOF ends the relay after its banner.
    helper.serve(wire, wait_readable=lambda sources, timeout: [target])
    assert opened == [setup.route]
    assert wire.sent.endswith(b"SSH-2.0-synthetic-target\r\n")
    assert journal.state(setup.request) == "closed"
    assert wire.closed and target.closed
    with pytest.raises(ProtocolDenied, match="replayed_execution"):
        journal.consume(setup.request, "f" * 64)


def test_helper_control_overhead_counts_before_target_open(setup):
    setup.request = replace(setup.request, limits=Limits(tunnel_bytes=100))
    helper, journal, opened = make_helper(setup)

    class Controller(BytesChannel):
        def sendall(self, data):
            message = decode_frame(data)
            self.incoming.extend(encode_frame({**message, "kind": "go"}))

    wire = Controller(encode_frame(setup.request.message()))
    with pytest.raises(ProtocolDenied, match="tunnel_budget_exceeded"):
        helper.serve(wire)
    assert not opened and journal.state(setup.request) is None


def test_helper_ceiling_and_authenticated_binding_cannot_be_enlarged(setup):
    helper, journal, opened = make_helper(setup, limits=Limits(output_bytes=8))
    with pytest.raises(ProtocolDenied, match="local_limit_exceeded"):
        helper.prepare(setup.request)
    with pytest.raises(TypeError):
        helper.routes["other"] = setup.route
    with pytest.raises(ProtocolDenied, match="invalid_controller_binding"):
        GatewayHelper(
            setup.gateway,
            [setup.route],
            journal,
            lambda request, phase: True,
            lambda route, timeout: None,
            binding=("foreign", "gateway"),
        )
    assert not opened


def test_journal_rejects_unsafe_durability_and_open_transactions():
    db = sqlite3.connect(":memory:")
    try:
        db.execute("PRAGMA synchronous=OFF")
        with pytest.raises(ProtocolDenied, match="journal_durability_required"):
            ReplayJournal(db)
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN")
        with pytest.raises(ProtocolDenied, match="journal_transaction_active"):
            ReplayJournal(db)
    finally:
        db.close()


class RemoteKey:
    def __init__(self, pin):
        self.pin = pin

    def get_name(self):
        return self.pin.algorithm

    def asbytes(self):
        return self.pin.key


class Process(BytesChannel):
    def __init__(self, stdout=b"Linux fixture", stderr=b"", exit_code=0):
        super().__init__(stdout)
        self.error = bytearray(stderr)
        self.exit_code = exit_code
        self.eof_received = True
        self.status_event = threading.Event()

    def recv_ready(self):
        return bool(self.incoming)

    def recv_stderr_ready(self):
        return bool(self.error)

    def recv_stderr(self, size):
        result = bytes(self.error[:size])
        del self.error[:size]
        return result

    def exit_status_ready(self):
        return True

    def recv_exit_status(self):
        return self.exit_code


class HelperChannel(BytesChannel):
    def __init__(self, request, *, tamper=False):
        self.ready = admission_message("ready", request, "c" * 64, "a" * 64, 1004)
        super().__init__(encode_frame(self.ready))
        self.messages = []
        self.tamper = tamper

    def sendall(self, data):
        message = decode_frame(data)
        self.messages.append(message)
        if message["kind"] == "go":
            opened = {**self.ready, "kind": "opened"}
            if self.tamper:
                opened["nonce"] = "b" * 64
            self.incoming.extend(encode_frame(opened))
        super().sendall(data)


class Driver:
    def __init__(self, setup, *, bad_gateway=False, bad_target=False, process=None, tamper=False):
        self.setup = setup
        self.bad_gateway, self.bad_target = bad_gateway, bad_target
        self.calls = []
        self.helper_channel = HelperChannel(setup.request, tamper=tamper)
        self.process = process or Process()

    def connect_gateway(self, gateway, timeout):
        assert gateway is self.setup.gateway
        self.calls.append("gateway_socket")
        return BytesChannel()

    def handshake(self, channel, pin, hop, timeout):
        if hop == "target":
            assert isinstance(channel, BudgetedChannel)
            assert channel.channel is self.helper_channel
        self.calls.append(hop + "_pin")
        bad = self.bad_gateway if hop == "gateway" else self.bad_target
        pin.verify(RemoteKey(HostKeyPin(pin.algorithm, b"x" * 32) if bad else pin), hop)
        return BytesChannel()

    def authenticate(self, transport, account, key, timeout):
        key._take_for_transport()
        self.calls.append("auth:" + account)

    def helper(self, transport, timeout):
        self.calls.append("helper")
        return self.helper_channel

    def command(self, transport, command, timeout):
        assert command == "/usr/bin/uname -srm"
        self.calls.append("exec")
        return self.process


def execute(setup, driver, *, authorize=lambda request, phase: True, cancelled=None, enabled=True):
    transport = SSHTransport(
        authorize, enabled=enabled, driver=driver, wall=setup.clock, monotonic=setup.clock
    )
    return transport.execute(
        setup.request,
        setup.gateway,
        setup.route,
        EphemeralKey(b"synthetic-gateway", credential_id="gateway-key"),
        EphemeralKey(b"synthetic-target", credential_id="target-key"),
        cancelled=cancelled,
    )


def test_two_hop_order_pins_before_auth_and_no_direct_target(setup):
    driver = Driver(setup)
    phases = []
    result = execute(setup, driver, authorize=lambda request, phase: phases.append(phase) or True)
    assert (result.state, result.exit_code, result.stdout) == ("completed", 0, b"Linux fixture")
    assert driver.calls == [
        "gateway_socket",
        "gateway_pin",
        "auth:gateway-user",
        "helper",
        "target_pin",
        "auth:target-user",
        "exec",
    ]
    assert phases == ["connect", "dispatch", "target_auth", "execute"]
    assert result.tunnel_bytes == len(driver.helper_channel.sent) + len(
        encode_frame(driver.helper_channel.ready)
    ) + len(encode_frame({**driver.helper_channel.ready, "kind": "opened"}))
    assert driver.helper_channel.closed and driver.process.closed


@pytest.mark.parametrize(
    "bad_gateway,bad_target,forbidden",
    [(True, False, "auth:gateway-user"), (False, True, "auth:target-user")],
)
def test_independent_hostkey_failures_before_auth_no_fallback(
    setup, bad_gateway, bad_target, forbidden
):
    driver = Driver(setup, bad_gateway=bad_gateway, bad_target=bad_target)
    result = execute(setup, driver)
    assert result.reason == (
        "gateway_hostkey_mismatch" if bad_gateway else "target_hostkey_mismatch"
    )
    assert result.state == ("not_started" if bad_gateway else "uncertain")
    assert forbidden not in driver.calls
    assert "exec" not in driver.calls
    assert driver.calls.count("gateway_socket") == 1


def test_dispatch_denial_after_ready_sends_no_go(setup):
    driver = Driver(setup)
    result = execute(setup, driver, authorize=lambda request, phase: phase != "dispatch")
    assert result.state == "not_started"
    assert result.reason == "authority_denied"
    assert [x["kind"] for x in driver.helper_channel.messages] == ["open_enrolled_ssh"]
    assert "target_pin" not in driver.calls


def test_lost_or_tampered_open_ack_stays_uncertain_without_retry(setup):
    driver = Driver(setup, tamper=True)
    result = execute(setup, driver)
    assert (result.state, result.reason) == ("uncertain", "admission_mismatch")
    assert driver.calls.count("gateway_socket") == 1
    assert "target_pin" not in driver.calls


def test_disabled_or_initial_authority_denial_makes_no_network_calls(setup):
    driver = Driver(setup)
    with pytest.raises(ProtocolDenied, match="transport_disabled"):
        execute(setup, driver, enabled=False)
    with pytest.raises(ProtocolDenied, match="authority_denied"):
        execute(setup, driver, authorize=lambda request, phase: False)
    assert not driver.calls


def test_wrong_or_consumed_credential_is_rejected_before_connect(setup):
    driver = Driver(setup)
    transport = SSHTransport(
        lambda request, phase: True,
        enabled=True,
        driver=driver,
        wall=setup.clock,
        monotonic=setup.clock,
    )
    gateway_key = EphemeralKey(b"synthetic-gateway", credential_id="gateway-key")
    target_key = EphemeralKey(b"synthetic-target", credential_id="foreign-key")
    with pytest.raises(ProtocolDenied, match="credential_unavailable"):
        transport.execute(setup.request, setup.gateway, setup.route, gateway_key, target_key)
    target_key = EphemeralKey(b"synthetic-target", credential_id="target-key")
    target_key.discard()
    with pytest.raises(ProtocolDenied, match="credential_unavailable"):
        transport.execute(setup.request, setup.gateway, setup.route, gateway_key, target_key)
    assert not driver.calls


@pytest.mark.parametrize("stdout,stderr,limit", [(b"abcdefgh", b"", 4), (b"abc", b"defgh", 5)])
def test_combined_output_cap_retains_only_bounded_prefix(setup, stdout, stderr, limit):
    setup.request = replace(setup.request, limits=Limits(output_bytes=limit))
    result = execute(setup, Driver(setup, process=Process(stdout, stderr)))
    assert (result.state, result.reason, result.exit_code) == (
        "uncertain",
        "output_budget_exceeded",
        None,
    )
    assert len(result.stdout) + len(result.stderr) == limit


def test_exit_missing_is_never_success(setup):
    result = execute(setup, Driver(setup, process=Process(exit_code=-1)))
    assert (result.state, result.reason, result.exit_code) == (
        "uncertain",
        "exit_evidence_missing",
        None,
    )


def test_cancellation_before_go_and_after_exec_are_distinct(setup):
    cancellation = threading.Event()
    cancellation.set()
    driver = Driver(setup)
    with pytest.raises(ProtocolDenied, match="cancellation_requested"):
        execute(setup, driver, cancelled=cancellation)
    assert not driver.calls
    cancellation.clear()
    process = Process()

    def command(transport, command, timeout):
        cancellation.set()
        return process

    driver.command = command
    result = execute(setup, driver, cancelled=cancellation)
    assert result.state == "uncertain"
    assert result.cancellation_requested and result.exit_code is None
    assert process.closed


def test_expired_runtime_closes_without_claiming_kill(setup):
    driver = Driver(setup)

    def command(transport, command, timeout):
        setup.clock.now = 1030
        return driver.process

    driver.command = command
    result = execute(setup, driver)
    assert (result.state, result.reason, result.exit_code) == (
        "uncertain",
        "deadline_exceeded",
        None,
    )
    assert driver.helper_channel.closed and driver.process.closed


def test_raw_tunnel_counts_both_directions_and_enforces_timeout(setup):
    raw = BytesChannel(b"abcd")
    bounded = BudgetedChannel(raw, 6, 1001, monotonic=setup.clock)
    bounded.sendall(b"ab")
    assert bounded.recv(4) == b"abcd"
    assert bounded.used == 6
    with pytest.raises(ProtocolDenied, match="tunnel_budget_exceeded"):
        bounded.sendall(b"x")
    assert raw.sent == b"ab" and raw.closed
    second = BudgetedChannel(BytesChannel(), 10, 1001, monotonic=setup.clock)
    setup.clock.now = 1001
    with pytest.raises(ProtocolDenied, match="deadline_exceeded"):
        second.recv(1)


def test_no_secret_in_repr_pickle_or_driver_error(setup):
    canary = b"SYNTHETIC-PRIVATE-CANARY"
    key = EphemeralKey(canary, credential_id="synthetic-key")
    assert canary.decode() not in repr(key)
    with pytest.raises(TypeError, match="credential_not_serializable"):
        pickle.dumps(key)
    assert key._take_for_transport() == canary
    with pytest.raises(ProtocolDenied):
        key._take_for_transport()
    driver = Driver(setup)

    def fail(*args):
        raise RuntimeError(canary.decode())

    driver.handshake = fail
    result = execute(setup, driver)
    assert result.reason == "ssh_transport_failed"
    assert canary.decode() not in repr(result)


def test_paramiko_low_level_pin_precedes_any_auth_or_key_parsing(setup):
    events = []

    class Transport:
        def __init__(self, channel):
            assert channel is wire
            events.append("transport")

        def start_client(self, *, timeout):
            events.append("kex")

        def is_active(self):
            return True

        def get_remote_server_key(self):
            events.append("pin")
            return RemoteKey(setup.gateway.pin)

        def auth_publickey(self, account, key):
            events.append("auth")

        def is_authenticated(self):
            return True

        def close(self):
            events.append("close")

    class Key:
        @staticmethod
        def from_private_key(stream):
            assert stream.read() == "synthetic"
            events.append("parse_key")
            return object()

    module = SimpleNamespace(Transport=Transport, Ed25519Key=Key)
    driver = ParamikoDriver(module)
    wire = BytesChannel()
    connection = driver.handshake(wire, setup.gateway.pin, "gateway", 1)
    assert events == ["transport", "kex", "pin"]
    driver.authenticate(
        connection, "account", EphemeralKey(b"synthetic", credential_id="synthetic-key"), 1
    )
    assert events == ["transport", "kex", "pin", "parse_key", "auth"]
    events.clear()
    with pytest.raises(ProtocolDenied, match="target_hostkey_mismatch"):
        driver.handshake(wire, setup.route.pin, "target", 1)
    assert events == ["transport", "kex", "pin", "close"]


def test_paramiko_absence_denies_before_socket_creation(setup, monkeypatch):
    import builtins

    original_import = builtins.__import__

    def deny_paramiko(name, *args, **kwargs):
        if name == "paramiko":
            raise ImportError("synthetic missing optional dependency")
        return original_import(name, *args, **kwargs)

    def deny_socket(*args, **kwargs):
        pytest.fail("missing dependency opened a socket")

    monkeypatch.setattr(builtins, "__import__", deny_paramiko)
    monkeypatch.setattr("commonplace.compute.transport.socket.socket", deny_socket)
    with pytest.raises(ProtocolDenied, match="ssh_dependency_unavailable"):
        ParamikoDriver().connect_gateway(setup.gateway, 1)


def test_low_level_driver_rejects_arbitrary_commands_before_opening_a_channel():
    def denied_channel(**kwargs):
        pytest.fail("unsupported command opened a channel")

    transport = SimpleNamespace(open_session=denied_channel)
    for command in ("id", "/usr/bin/uname -srm; id", "sh -c id", "/usr/bin/uname -a"):
        with pytest.raises(ProtocolDenied, match="unsupported_operation"):
            ParamikoDriver(SimpleNamespace()).command(transport, command, 1)


def test_request_json_never_contains_a_pin_key_or_numeric_route(setup):
    payload = json.dumps(setup.request.message())
    assert setup.route.address not in payload
    assert setup.gateway.address not in payload
    assert "private" not in payload and "password" not in payload
