"""Guarded passive admission over actual pinned Paramiko socketpairs only.

All custody, pins, receipts, scopes and cache bytes are synthetic. No TCP, LAN,
OS cache, external provider, deployment, review or gate. The integration maintainer runs the tests;
the component author runs only static checks and normal commit hooks.
"""

import json
import multiprocessing
import os
import socket
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from commonplace.compute import discovery_service
from commonplace.compute.discovery import CacheSnapshot, OSCacheSupplier, PassiveCollector
from commonplace.compute.discovery_service import DiscoveryService, GatewayDiscoveryReceipt
from commonplace.compute.discovery_store import digest, gateway_document
from commonplace.compute.discovery_transport import DiscoveryOutcome, PassiveSSHTransport
from commonplace.compute.executor import ExecutionService
from commonplace.compute.fence import LOCAL_DOMAINS, FenceDenied, _held
from commonplace.compute.protocol import (
    ADMISSION_MODE,
    PASSIVE_PURPOSE,
    GatewayHelper,
    HostKeyPin,
    PassiveAdmissionChallenge,
    PassiveDiscoveryRequest,
    ProtocolDenied,
    ReplayJournal,
    admission_message,
    canonical,
    decode_frame,
    encode_frame,
)
from commonplace.compute.scopes import DiscoveryLimits
from commonplace.compute.transport import ParamikoDriver
from tests.test_compute_control import compute_env as compute_env
from tests.test_compute_discovery_api import API
from tests.test_compute_executor import execution_env as execution_env
from tests.test_compute_fence import Child, local_state as local_state
from tests.test_compute_ssh_fixture import SocketPairAdmissionSSH, _server_key, paramiko
from tests.test_compute_transport import setup as setup

pytestmark = pytest.mark.skipif(os.name != "posix", reason="guarded private POSIX custody")


@pytest.fixture(autouse=True)
def synthetic_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("passive admission fixture attempted TCP, DNS or a host cache")

    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyaddr"):
        monkeypatch.setattr(socket, name, forbidden)
    monkeypatch.setattr(ParamikoDriver, "connect_gateway", forbidden)
    monkeypatch.setattr(OSCacheSupplier, "read", forbidden)


def _outer(state):
    with state.env.store._journal() as conn:
        row, metadata = state.env.store._gateway(conn, state.env.principal, state.gateway["id"])
        return state.env.store._enrollment(row, metadata)


def _receipt(state):
    receipt = GatewayDiscoveryReceipt(
        "fixture-provisioner",
        state.env.workspace,
        state.gateway["id"],
        digest(gateway_document(_outer(state))),
        state.env.clock.now,
        state.env.clock.now + 300,
        b"",
    )
    return replace(receipt, signature=state.provisioner.sign(receipt.signed_bytes()))


def _submit(state, request_id="passive-one"):
    return state.discovery.store.submit(
        state.env.principal,
        **state.env.auth,
        scope_id=state.scope,
        sources=["linux_arp"],
        request_id=request_id,
    )[0]


def _row(state, discovery):
    with state.discovery.store._journal() as conn:
        return dict(state.discovery.store._row(conn, discovery))


def _permits(state):
    with state.discovery.store._journal() as conn:
        return conn.execute("SELECT count(*) FROM discovery_permits").fetchone()[0]


def _writer(state, action):
    # Mutation children install real guards but start no remote worker.
    config = replace(
        state.config,
        workspaces=(
            replace(
                state.config.workspaces[0],
                admission_mode=None,
                discovery_enabled=False,
            ),
        ),
    )
    return Child(
        SimpleNamespace(**{**vars(state), "config": config}),
        action,
        credential=state.credentials[0],
    )


@pytest.fixture
def passive_state(local_state, monkeypatch):
    state, env = local_state, local_state.env
    state.gateway_host = _server_key()
    with env.store._journal() as conn:
        metadata = json.loads(
            conn.execute(
                "SELECT metadata FROM gateways WHERE id=?", (state.gateway["id"],)
            ).fetchone()[0]
        )
        metadata["host_key"] = HostKeyPin(
            state.gateway_host.get_name(), state.gateway_host.asbytes()
        ).document()
        conn.execute(
            "UPDATE gateways SET metadata=? WHERE id=?",
            (canonical(metadata).decode(), state.gateway["id"]),
        )
    state.config = replace(
        state.config,
        workspaces=(
            replace(
                state.config.workspaces[0],
                admission_mode=ADMISSION_MODE,
                discovery_enabled=True,
                discovery_sources=("linux_arp",),
            ),
        ),
    )
    env.db._compute_config = state.config
    with monkeypatch.context() as patch:
        patch.setattr(DiscoveryService, "_work", lambda self: None)
        discovery_service.install_lifecycle(env.gateway)
    state.discovery = discovery_service.configured_service(env.gateway, env.workspace)
    env.store = state.discovery.control
    env.store.clock = lambda: env.clock.now
    state.discovery.job_limits = DiscoveryLimits(1, 1, 2, 3, 1)
    state.discovery._accept_enrollment(
        env.principal, state.gateway["id"], **env.auth, receipt=_receipt(state)
    )
    assert state.discovery.running
    with env.store._journal() as conn:
        state.protected_scope = state.discovery.store._scope(conn, env.principal, state.scope)[1]
    state.helper_scope = state.protected_scope
    fixtures = []

    def ssh(**kwargs):
        fixture = PassiveSocketSSH(state, **kwargs)
        fixtures.append(fixture)
        state.discovery.driver = fixture.driver
        return fixture

    state.ssh = ssh
    yield state
    state.discovery.stop()
    for fixture in fixtures:
        fixture.close()


class PassiveSocketSSH(SocketPairAdmissionSSH):
    """Use the existing forced-subsystem server/auth helpers, without a target."""

    def __init__(self, state, **kwargs):
        super().__init__(state, state.gateway_host, None, **kwargs)
        self.calls = []

    def _supplier(self):
        fixture = self

        class Supplier:
            sources = frozenset({"linux_arp"})

            def read(self, source, *, max_bytes, max_lines, checkpoint):
                fixture.hit("cache")
                # A separate connection observes committed replay consumption.
                with sqlite3.connect(fixture.helper_path) as conn:
                    record = conn.execute(
                        "SELECT purpose,state FROM compute_passive_journal WHERE discovery=?",
                        (fixture.request.discovery,),
                    ).fetchone()
                    assert record == (PASSIVE_PURPOSE, "consumed")
                fixture.calls.append(source)
                checkpoint()
                return CacheSnapshot(
                    source,
                    b"10.20.0.2 0x1 0x2 02:00:00:00:00:02 * eth0\n",
                    fixture.state.env.clock.now,
                )

        return Supplier()

    def serve_gateway(self, sock, gateway):
        fixture = self
        transport = paramiko.Transport(sock)
        self.resources.append(transport)
        transport.add_server_key(self.gateway_host)
        server = self.server("gateway")
        transport.start_server(server=server)
        channel = transport.accept(5)
        if channel is None or not server.subsystem.wait(5):
            return

        class Helper(GatewayHelper):
            def prepare_passive(self, request):
                fixture.request = request
                fixture.hit("ready")
                return super().prepare_passive(request)

            def challenge_passive(self, request, ready, go):
                fixture.hit("before_challenge")
                return super().challenge_passive(request, ready, go)

            def admit_passive(self, request, challenge, permit):
                fixture.hit("before_helper_permit")
                if fixture.fault == "expired_permit":
                    fixture.state.env.clock.now = challenge.start_by
                if fixture.fault == "revoked_scope":
                    fixture.state.helper_scope = replace(fixture.state.helper_scope, revoked=True)
                return super().admit_passive(request, challenge, permit)

        class Wire:
            def settimeout(self, value):
                channel.settimeout(value)

            def recv(self, count):
                fixture.hit("helper_read")
                return channel.recv(count)

            def sendall(self, data):
                fixture.hit("helper_send")
                message = decode_frame(data)
                fixture.messages.append(message["kind"])
                if message["kind"] == "passive_challenge":
                    if fixture.fault == "missing_challenge":
                        channel.close()
                        raise EOFError
                    if fixture.fault in {"nonce", "channel", "digest"}:
                        message[fixture.fault] = "f" * 64
                    elif fixture.fault in {"authority_epoch", "gateway_epoch", "admission_v", "v"}:
                        message[fixture.fault] += 1
                    elif fixture.fault == "purpose":
                        message["purpose"] = "compute-execution"
                    elif fixture.fault == "expiry":
                        message["start_by"] -= 10
                    elif fixture.fault == "unknown_mode":
                        message["passive_admission_mode"] = "unknown"
                if message["kind"] == "passive_observations":
                    if fixture.fault in {"lost_result", "partial_result"}:
                        if fixture.fault == "partial_result":
                            channel.sendall(data[:7])
                        channel.close()
                        raise EOFError
                    if fixture.fault == "wrong_result":
                        message["nonce"] = "f" * 64
                channel.sendall(encode_frame(message))

            def close(self):
                channel.close()

        with sqlite3.connect(self.helper_path) as conn:
            helper = Helper(
                gateway,
                [],
                ReplayJournal(conn),
                lambda request, phase: request.context == self.state.protected_scope.context,
                lambda *args: pytest.fail("passive helper opened a target"),
                binding=(gateway.workspace, gateway.gateway),
                enabled=True,
                wall=lambda: self.state.env.clock.now,
                passive_collector=PassiveCollector(
                    self._supplier(), wall=lambda: self.state.env.clock.now
                ),
                passive_ceiling=self.state.protected_scope,
                passive_scope=lambda request: self.state.helper_scope,
                passive_admission_mode=ADMISSION_MODE,
            )
            helper.serve(Wire())

    def _driver(self):
        fixture = self

        class Driver(ParamikoDriver):
            def connect_gateway(self, gateway, timeout):
                fixture.hit("connect")
                client, server = socket.socketpair()
                fixture.resources.extend((client, server))
                fixture.spawn(lambda: fixture.serve_gateway(server, gateway))
                return client

            def handshake(self, channel, pin, hop, timeout):
                fixture.hit("pin:" + hop)
                assert hop == "gateway"
                return super().handshake(channel, pin, hop, timeout)

            def authenticate(self, transport, account, key, timeout):
                fixture.hit("before_auth")
                return super().authenticate(transport, account, key, timeout)

            def helper(self, transport, timeout):
                channel = super().helper(transport, timeout)

                class ControllerWire:
                    def settimeout(self, value):
                        channel.settimeout(value)

                    def recv(self, count):
                        fixture.hit("controller_read")
                        return channel.recv(count)

                    def sendall(self, data):
                        fixture.hit("controller_send")
                        message = decode_frame(data)
                        if message["kind"] == "passive_permit":
                            if fixture.fault in {"missing_permit", "partial_permit"}:
                                if fixture.fault == "partial_permit":
                                    channel.sendall(data[:7])
                                channel.close()
                                raise EOFError
                            if fixture.fault == "tamper_permit":
                                data = encode_frame({**message, "nonce": "f" * 64})
                        channel.sendall(data)

                    def close(self):
                        channel.close()

                return ControllerWire()

            def command(self, *args):
                pytest.fail("passive collection attempted a command")

        return Driver()


def _claimed(state):
    item = _submit(state)
    request, gateway, deadline = state.discovery._claim()
    assert request.discovery == item["id"]
    assert state.discovery._authorize(request, "collect")
    challenge = PassiveAdmissionChallenge(request, "c" * 64, "a" * 64, request.start_by)
    return request, gateway, deadline, challenge


def test_actual_outer_ssh_collection_and_current_owner_rest_status(passive_state):
    state = passive_state
    fixture = state.ssh()
    item = _submit(state)
    assert state.discovery._run_once()
    row = _row(state, item["id"])
    assert row["state"] == "completed" and row["slot"] == 0, row
    assert fixture.messages == ["passive_ready", "passive_challenge", "passive_observations"]
    assert fixture.authenticated == ["gateway"] and fixture.commands == [] and fixture.opened == []
    assert fixture.calls == ["linux_arp"] and _permits(state) == 1
    status, view = API(state.env).request(path="/" + item["id"])
    assert status == 200 and view["discovery"]["state"] == "completed"
    assert view["discovery"]["observations"][0]["address"] == "10.20.0.2"
    assert not state.discovery._run_once()
    child = _writer(state, "session")
    try:
        child.go()
        assert child.receive() == "committed"
        assert API(state.env).request(path="/" + item["id"])[0] == 401
    finally:
        child.close()


def test_outer_pin_precedes_auth_and_no_permit_or_cache_on_mismatch(passive_state):
    state = passive_state
    wrong = _server_key()
    with state.env.store._journal() as conn:
        metadata = json.loads(
            conn.execute(
                "SELECT metadata FROM gateways WHERE id=?", (state.gateway["id"],)
            ).fetchone()[0]
        )
        metadata["host_key"] = HostKeyPin(wrong.get_name(), wrong.asbytes()).document()
        conn.execute(
            "UPDATE gateways SET metadata=? WHERE id=?",
            (canonical(metadata).decode(), state.gateway["id"]),
        )
    state.discovery._accept_enrollment(
        state.env.principal, state.gateway["id"], **state.env.auth, receipt=_receipt(state)
    )
    fixture = state.ssh()
    item = _submit(state)
    assert state.discovery._run_once()
    row = _row(state, item["id"])
    assert row["reason"] == "gateway_hostkey_mismatch" and row["slot"] == 0
    assert fixture.authenticated == [] and fixture.calls == [] and _permits(state) == 0


@pytest.mark.parametrize(
    "fault",
    [
        "missing_challenge",
        "nonce",
        "channel",
        "digest",
        "authority_epoch",
        "gateway_epoch",
        "purpose",
        "admission_v",
        "v",
        "expiry",
        "unknown_mode",
        "missing_permit",
        "partial_permit",
        "tamper_permit",
        "expired_permit",
        "revoked_scope",
        "lost_result",
        "partial_result",
        "wrong_result",
    ],
)
def test_actual_bad_or_lost_exchange_retains_only_issued_permits_and_never_recollects(
    passive_state, fault
):
    state = passive_state
    fixture = state.ssh(fault=fault)
    item = _submit(state)
    assert state.discovery._run_once()
    row = _row(state, item["id"])
    issued = fault in {
        "missing_permit",
        "partial_permit",
        "tamper_permit",
        "expired_permit",
        "revoked_scope",
        "lost_result",
        "partial_result",
        "wrong_result",
    }
    assert _permits(state) == int(issued)
    assert (row["state"], row["slot"]) == (("uncertain", 1) if issued else ("failed", 0)), row
    assert fixture.calls == (
        ["linux_arp"]
        if fault
        in {
            "lost_result",
            "partial_result",
            "wrong_result",
        }
        else []
    )
    assert not state.discovery._run_once()
    state.env.clock.now += 20
    with state.discovery.store._journal() as conn:
        state.discovery._recover(conn)
    assert _row(state, item["id"])["slot"] == int(issued)
    assert _submit(state)["id"] == item["id"]
    assert not state.discovery._run_once()


@pytest.mark.parametrize(
    "action",
    [
        "session",
        "role",
        "task",
        "assignment",
        "approval",
        "blocker",
        "scope",
        "credential",
        "rotation",
    ],
)
def test_multiprocess_revocation_committed_before_permit_wins(passive_state, action):
    state = passive_state
    child = _writer(state, action)
    request, _, _, challenge = _claimed(state)
    try:
        child.go()
        assert child.receive() == "committed"
        with pytest.raises(ProtocolDenied, match="authority_denied"):
            state.discovery._wire_admission(request, challenge)
        assert _permits(state) == 0
    finally:
        child.close()


@pytest.mark.parametrize(
    "change", ["discovery", "scope", "context", "limits", "authority_epoch", "gateway_epoch"]
)
def test_controller_permit_requires_exact_reserved_immutable_attempt(passive_state, change):
    state = passive_state
    request, _, _, challenge = _claimed(state)
    value = {
        "discovery": "foreign",
        "scope": "foreign",
        "context": replace(request.context, agent="foreign"),
        "limits": replace(request.limits, hosts=2),
        "authority_epoch": request.authority_epoch + 1,
        "gateway_epoch": request.gateway_epoch + 1,
    }[change]
    changed = replace(request, **{change: value})
    with pytest.raises(ProtocolDenied):
        state.discovery._wire_admission(changed, replace(challenge, request=changed))
    assert _permits(state) == 0
    assert state.discovery._wire_admission(request, challenge) == challenge.message(
        "passive_permit"
    )


@pytest.mark.parametrize("when", ["before", "after"])
def test_actual_revocation_order_and_io_outside_authority_locks(passive_state, when):
    state = passive_state
    child = _writer(state, "session")
    fired = []

    def hook(phase, fixture):
        if phase == ("before_challenge" if when == "before" else "before_helper_permit"):
            child.go()
            assert child.receive() == "committed"
            fired.append(phase)

    try:
        fixture = state.ssh(hook=hook)
        item = _submit(state)
        assert state.discovery._run_once()
        assert len(fired) == 1 and _permits(state) == int(when == "after")
        # An already issued permit may finish; revoked audience receives no text.
        assert fixture.calls == (["linux_arp"] if when == "after" else [])
        row = _row(state, item["id"])
        assert row["slot"] == 0 and row["observations"] == "[]"
        assert row["state"] == ("failed" if when == "before" else "completed")
    finally:
        child.close()


def test_one_use_immutable_permit_and_restart_keep_capacity(passive_state):
    state = passive_state
    request, _, _, challenge = _claimed(state)
    permit = state.discovery._wire_admission(request, challenge)
    assert permit == challenge.message("passive_permit")
    assert not state.fence.active and not state.env.db._lock._is_owned()
    with pytest.raises(ProtocolDenied):
        state.discovery._wire_admission(request, challenge)
    with state.discovery.store._journal() as conn:
        for table in ("discovery_attempts", "discovery_permits"):
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute(f"DELETE FROM {table}")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE discovery_permits SET issued_at=1")
        assert (
            json.loads(conn.execute("SELECT permit FROM discovery_permits").fetchone()[0]) == permit
        )
    state.discovery._finish(request, DiscoveryOutcome(reason="ssh_transport_failed"))
    assert _row(state, request.discovery)["slot"] == 1
    # A fresh service identity cannot reclaim a reserved attempt or reissue.
    restarted = DiscoveryService(
        state.discovery.store,
        enabled=True,
        fence=state.fence,
        wrapping_key_path=state.key_path,
        provisioning_keys=state.discovery._keys,
        sources=("linux_arp",),
        passive_admission_mode=ADMISSION_MODE,
    )
    try:
        assert restarted.start(background=False)
        assert not restarted._run_once()
        with pytest.raises(ProtocolDenied):
            restarted._wire_admission(request, challenge)
        assert _permits(state) == 1
    finally:
        restarted.stop()
        state.discovery.store.service = state.discovery


def _crash_after_permit(control_path, config, now, channel):
    from pathlib import Path

    from tests._compute_filesystem import private_filesystem

    with private_filesystem(Path(control_path).parent):
        _crash_after_permit_with_filesystem(control_path, config, now, channel)


def _crash_after_permit_with_filesystem(control_path, config, now, channel):
    """Separate process: genuine durable admission, then abrupt process death."""
    from commonplace import compute
    from commonplace.control_db import ControlDB

    # Bound workers remain explicitly pumped in this isolated child process.
    ExecutionService._work = lambda self: None
    DiscoveryService._work = lambda self: None
    db = ControlDB(control_path, compute_config=config)
    workspace = config.workspaces[0]
    root = workspace.directory.parent
    gateway = SimpleNamespace(
        db=db,
        supervisor=SimpleNamespace(workspace_dir=lambda wid: root / wid),
        workspace_dir=lambda wid: str(root / wid),
        add_route=lambda *args: None,
        add_prefix_route=lambda *args: None,
    )
    compute.install(gateway)
    service = discovery_service.configured_service(gateway, workspace.workspace)
    service.control.clock = lambda: now
    request, _, _ = service._claim()
    assert service._authorize(request, "collect")
    challenge = PassiveAdmissionChallenge(request, "c" * 64, "a" * 64, request.start_by)
    permit = service._wire_admission(request, challenge)
    channel.send((request.discovery, permit))
    os._exit(29)


def test_process_crash_after_permit_keeps_unknown_reservation_on_reopen(passive_state):
    state = passive_state
    item = _submit(state)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(
        target=_crash_after_permit,
        args=(state.env.db.path, state.config, state.env.clock.now, child),
    )
    process.start()
    child.close()
    try:
        assert parent.poll(15), "permit-issuing crash child did not reply"
        discovery, permit = parent.recv()
        assert discovery == item["id"] and permit["purpose"] == PASSIVE_PURPOSE
        process.join(10)
        assert process.exitcode == 29
        assert _permits(state) == 1
        state.env.clock.now += 20
        with state.discovery.store._journal() as conn:
            state.discovery._recover(conn)
        row = _row(state, discovery)
        assert row["state"] == "uncertain" and row["slot"] == 1
        assert not state.discovery._run_once()
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        parent.close()


def test_cancel_before_go_and_after_permit_are_distinct(passive_state):
    state = passive_state
    item = _submit(state)
    state.discovery.store.cancel(state.env.principal, item["id"], **state.env.auth)
    assert _row(state, item["id"])["state"] == "cancelled"
    assert not state.discovery._run_once()
    item = _submit(state, "cancel-after-permit")

    def hook(phase, fixture):
        if phase == "before_helper_permit":
            state.discovery.store.cancel(state.env.principal, item["id"], **state.env.auth)

    fixture = state.ssh(fault="lost_result", hook=hook)
    assert state.discovery._run_once()
    row = _row(state, item["id"])
    assert row["cancel_requested"] == 1 and row["state"] == "uncertain" and row["slot"] == 1
    assert _permits(state) == 1
    assert not state.discovery._run_once() and len(fixture.calls) <= 1


def _helper(state, request, path):
    conn = sqlite3.connect(path, isolation_level=None)
    helper = GatewayHelper(
        _outer(state),
        [],
        ReplayJournal(conn),
        lambda *args: True,
        lambda *args: pytest.fail("passive permit attempted target open"),
        binding=(request.context.workspace, request.context.gateway),
        enabled=True,
        wall=lambda: state.env.clock.now,
        passive_collector=PassiveCollector(_NoRead(), wall=lambda: state.env.clock.now),
        passive_ceiling=state.protected_scope,
        passive_scope=lambda req: state.helper_scope,
        passive_admission_mode=ADMISSION_MODE,
    )
    return helper, conn


class _NoRead:
    sources = frozenset({"linux_arp"})

    def read(self, *args, **kwargs):
        pytest.fail("unadmitted supplier access")


def _challenge(helper, request):
    ready = helper.prepare_passive(request)
    message = helper.challenge_passive(request, ready, {**ready, "kind": "passive_go"})
    return PassiveAdmissionChallenge.from_message(message, request, ready=ready)


@pytest.mark.parametrize(
    "field,value",
    [
        ("nonce", "f" * 64),
        ("channel", "f" * 64),
        ("digest", "f" * 64),
        ("authority_epoch", 999999),
        ("gateway_epoch", 999999),
        ("purpose", "execution"),
        ("admission_v", 2),
        ("admission_v", True),
        ("v", True),
        ("passive_admission_mode", None),
        ("start_by", 1),
        ("run_until", 1),
    ],
)
def test_helper_rejects_tamper_before_supplier_and_consumes_challenge(
    passive_state, tmp_path, field, value
):
    state = passive_state
    request, _, _, _ = _claimed(state)
    helper, conn = _helper(state, request, tmp_path / "helper.sqlite3")
    try:
        challenge = _challenge(helper, request)
        with pytest.raises(ProtocolDenied):
            helper.admit_passive(
                request, challenge, {**challenge.message("passive_permit"), field: value}
            )
        with pytest.raises(ProtocolDenied):
            helper.admit_passive(request, challenge, challenge.message("passive_permit"))
        assert conn.execute("SELECT count(*) FROM compute_passive_journal").fetchone()[0] == 0
    finally:
        conn.close()


def test_helper_replay_and_unknown_capacity_survive_reopen_without_execution_damage(
    passive_state, tmp_path
):
    state = passive_state
    request, _, _, _ = _claimed(state)
    path = tmp_path / "helper.sqlite3"
    helper, conn = _helper(state, request, path)
    challenge = _challenge(helper, request)
    helper.journal.consume_passive(
        request, challenge, challenge.message("passive_permit"), concurrency=2
    )
    helper.journal.finish_passive(request)
    conn.execute(
        "INSERT INTO compute_transport_journal(workspace,gateway,execution,attempt,digest,nonce,state) VALUES (?,?,?,?,?,?,?)",
        (
            request.context.workspace,
            request.context.gateway,
            request.discovery,
            "fixed",
            "e" * 64,
            "f" * 64,
            "uncertain",
        ),
    )
    conn.close()
    helper, conn = _helper(state, request, path)
    try:
        fresh = _challenge(helper, request)
        with pytest.raises(ProtocolDenied, match="replayed_discovery"):
            helper.admit_passive(request, fresh, fresh.message("passive_permit"))
        assert (
            conn.execute("SELECT state FROM compute_passive_journal").fetchone()[0] == "uncertain"
        )
        assert (
            conn.execute("SELECT state FROM compute_transport_journal").fetchone()[0] == "uncertain"
        )
        assert conn.execute("SELECT count(*) FROM compute_transport_journal").fetchone()[0] == 1
        # A fresh ID does not reclaim an unknown helper slot, even on reopen.
        helper.limits = replace(helper.limits, tunnels=1)
        other = replace(request, discovery="another")
        next_challenge = _challenge(helper, other)
        with pytest.raises(ProtocolDenied, match="collection_capacity_exceeded"):
            helper.admit_passive(other, next_challenge, next_challenge.message("passive_permit"))
    finally:
        conn.close()


def test_helper_expiry_and_protected_scope_ceiling_precede_supplier(passive_state, tmp_path):
    state = passive_state
    request, _, _, _ = _claimed(state)
    helper, conn = _helper(state, request, tmp_path / "helper.sqlite3")
    tick = [1.0]
    helper.monotonic = lambda: tick[0]
    try:
        challenge = _challenge(helper, request)
        tick[0] += 5
        state.env.clock.now -= 1
        with pytest.raises(ProtocolDenied):
            helper.admit_passive(request, challenge, challenge.message("passive_permit"))
        state.env.clock.now += 1
        state.helper_scope = replace(state.helper_scope, limits=DiscoveryLimits(1, 1, 1, 1, 1))
        with pytest.raises(ProtocolDenied):
            helper.prepare_passive(request)
        assert conn.execute("SELECT count(*) FROM compute_passive_journal").fetchone()[0] == 0
    finally:
        conn.close()


def test_mode_is_bound_to_intake_and_config_change_cannot_upgrade_old_work(passive_state):
    state = passive_state
    item = _submit(state)
    assert (
        json.loads(_row(state, item["id"])["binding"])["passive_admission_mode"] == ADMISSION_MODE
    )
    with state.fence.hold(state.env.workspace) as lease:
        assert lease.domains == LOCAL_DOMAINS
    with pytest.raises(FenceDenied):
        with _held(state.fence, state.env.workspace):
            pass
    state.env.db._compute_config = replace(
        state.config,
        workspaces=(
            replace(
                state.config.workspaces[0],
                admission_mode=None,
            ),
        ),
    )
    assert not state.discovery.available()
    with pytest.raises(FenceDenied):
        state.discovery._claim()
    assert _row(state, item["id"])["state"] == "queued"


def test_old_durable_intake_and_legacy_wire_never_gain_passive_permission(passive_state, tmp_path):
    state = passive_state
    item = _submit(state)
    old = _row(state, item["id"])
    old["id"], old["request_id"] = "legacy", "legacy"
    intake, binding = json.loads(old["intake"]), json.loads(old["binding"])
    intake["request_id"] = "legacy"
    del binding["passive_admission_mode"]
    old["intake"], old["binding"] = canonical(intake).decode(), canonical(binding).decode()
    with state.discovery.store._journal() as conn:
        conn.execute(
            f"INSERT INTO discoveries({','.join(old)}) VALUES ({','.join('?' for _ in old)})",
            tuple(old.values()),
        )
    state.discovery.store.cancel(state.env.principal, item["id"], **state.env.auth)
    assert state.discovery._claim() is None
    assert _row(state, "legacy")["state"] == "denied"
    # Use a new immutable attempt to test exact typed serialization and downgrade.
    _submit(state, "new-mode")
    request, _, _ = state.discovery._claim()
    assert (
        PassiveDiscoveryRequest.from_message(decode_frame(encode_frame(request.message())))
        == request
    )
    legacy = replace(request, passive_admission_mode=None, start_by=None, run_until=None)
    assert legacy.digest != request.digest
    helper, conn = _helper(state, request, tmp_path / "helper.sqlite3")
    try:
        with pytest.raises(ProtocolDenied):
            helper.prepare_passive(legacy)
        with pytest.raises(ProtocolDenied):
            helper.collect_passive(legacy)
        with pytest.raises(ProtocolDenied, match="admission_permit_required"):
            helper.collect_passive(request)
        assert conn.execute("SELECT count(*) FROM compute_passive_journal").fetchone()[0] == 0
    finally:
        conn.close()


def test_defaultoff_never_touches_custody_and_fixed_wire_is_unchanged(
    local_state, setup, monkeypatch
):
    state = local_state

    def forbidden(*args, **kwargs):
        pytest.fail("unconfigured discovery touched custody")

    monkeypatch.setattr(DiscoveryService, "_custody_store", forbidden)
    workspace = replace(
        state.config.workspaces[0], discovery_enabled=True, discovery_sources=("linux_arp",)
    )
    state.env.db._compute_config = replace(state.config, workspaces=(workspace,))
    discovery_service.install_lifecycle(state.env.gateway)
    service = discovery_service.configured_service(state.env.gateway, state.env.workspace)
    assert service.passive_admission_mode is None and not service.running
    assert not service.available()
    message = admission_message("ready", setup.request, "c" * 64, "a" * 64, setup.request.start_by)
    assert set(message) == {
        "v",
        "kind",
        "execution",
        "attempt",
        "digest",
        "channel",
        "nonce",
        "start_by",
        "run_until",
    }
    assert "passive_admission_mode" not in setup.request.message()
    with pytest.raises(ProtocolDenied):
        PassiveSSHTransport(
            authorize=lambda *args: True,
            credential=forbidden,
            passive_admission_mode=ADMISSION_MODE,
        )
    with pytest.raises(ProtocolDenied):
        DiscoveryService(service.store, passive_admission_mode="unknown")
