"""Actual two-hop SSH over socketpairs; no network endpoints or operator keys."""

import base64
import io
import socket
import sqlite3
import threading
import time
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commonplace.compute.credentials import (
    CredentialBinding,
    CredentialStore,
    initialize_wrapping_key,
    internal_transport_private_bytes,
)
from commonplace.compute.protocol import (
    EnrolledRoute,
    ExecutionRequest,
    GatewayEnrollment,
    GatewayHelper,
    HostKeyPin,
    ProtocolDenied,
    ReplayJournal,
    enrollment_digest,
)
from commonplace.compute.transport import EphemeralKey, ParamikoDriver, SSHTransport

paramiko = pytest.importorskip("paramiko")


def _server_key():
    raw = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    )
    return paramiko.Ed25519Key.from_private_key(io.StringIO(raw.decode("ascii")))


def _assert_result(result, wrong_pin, authenticated, commands):
    if wrong_pin == "gateway":
        assert result.state == "not_started" and result.reason == "gateway_hostkey_mismatch"
        assert authenticated == [] and commands == []
    elif wrong_pin == "target":
        assert result.state == "uncertain" and result.reason == "target_hostkey_mismatch"
        assert authenticated == ["gateway"] and commands == []
    else:
        assert result.state == "completed", (result.state, result.reason)
        assert result.exit_code == 0 and result.stdout == b"Linux isolated-fixture x86_64\n"
        assert authenticated == ["gateway", "target"]
        assert commands == [b"/usr/bin/uname -srm"]


def _selected_pins(gateway, route, wrong_pin):
    if wrong_pin:
        bad = _server_key()
        pin = HostKeyPin(bad.get_name(), bad.asbytes())
        if wrong_pin == "gateway":
            gateway = replace(gateway, pin=pin)
        else:
            route = replace(route, pin=pin)

    return gateway, route


@pytest.mark.parametrize("wrong_pin", [None, "gateway", "target"])
def test_encrypted_custody_to_actual_two_hop_ssh_without_network(
    tmp_path, wrong_pin, compute_private_filesystem
):
    data, keys = tmp_path / "data", tmp_path / "keys"
    data.mkdir(mode=0o700)
    keys.mkdir(mode=0o700)
    database, keyfile = data / "compute.sqlite3", keys / "wrapping"
    context = object()
    initialize_wrapping_key(keyfile, data, database_path=database)
    store = CredentialStore.initialize(database, keyfile, data, lambda req, ctx: ctx is context)
    threads, resources, errors = [], [], []
    stop = threading.Event()
    authenticated, commands = [], []
    outer_host, target_host = _server_key(), _server_key()

    def spawn(fn):
        def guarded():
            try:
                fn()
            except (EOFError, OSError, paramiko.SSHException):
                pass  # Expected peer teardown when an independent pin is refused.
            except Exception as exc:
                errors.append(type(exc).__name__)

        thread = threading.Thread(target=guarded, daemon=True)
        threads.append(thread)
        thread.start()

    enrollments = []
    for purpose in ("gateway", "target"):
        binding = CredentialBinding(
            "workspace",
            "owner",
            "gateway",
            "node" if purpose == "target" else "",
            "compute",
            purpose,
            1,
        )
        enrollment = store.generate(binding, context)
        store.activate(enrollment.credential_id, binding, context)
        enrollments.append(enrollment)
    gateway = GatewayEnrollment(
        "workspace",
        "gateway",
        "192.0.2.10",
        22,
        "compute",
        HostKeyPin(outer_host.get_name(), outer_host.asbytes()),
        1,
        1,
        enrollments[0].credential_id,
    )
    route = EnrolledRoute(
        "workspace",
        "gateway",
        "node",
        "10.77.0.9",
        22,
        "eth0",
        "compute",
        HostKeyPin(target_host.get_name(), target_host.asbytes()),
        1,
        1,
        enrollments[1].credential_id,
    )
    gateway, route = _selected_pins(gateway, route, wrong_pin)

    class Server(paramiko.ServerInterface):
        def __init__(self, hop, enrollment):
            self.hop, self.enrollment = hop, enrollment
            self.command = threading.Event()

        def check_auth_publickey(self, username, key):
            expected = base64.b64decode(self.enrollment.public_key.split()[1])
            if username == "compute" and key.asbytes() == expected:
                authenticated.append(self.hop)
                return paramiko.AUTH_SUCCESSFUL
            return paramiko.AUTH_FAILED

        def get_allowed_auths(self, username):
            return "publickey"

        def check_channel_request(self, kind, chanid):
            return (
                paramiko.OPEN_SUCCEEDED
                if kind == "session"
                else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
            )

        def check_channel_subsystem_request(self, channel, name):
            return self.hop == "gateway" and name == "commonplace-compute-v1"

        def check_channel_exec_request(self, channel, command):
            if self.hop != "target" or command != b"/usr/bin/uname -srm":
                return False
            commands.append(command)
            self.command.set()
            return True

    def serve_target(sock):
        server = Server("target", enrollments[1])
        transport = paramiko.Transport(sock)
        resources.append(transport)
        transport.add_server_key(target_host)
        transport.start_server(server=server)
        channel = transport.accept(5)
        if channel is None:
            return
        assert server.command.wait(5)
        assert channel.recv(1) == b""  # Client has observed exec acknowledgement.
        channel.sendall(b"Linux isolated-fixture x86_64\n")
        channel.send_exit_status(0)
        channel.shutdown_write()
        channel.close()
        stop.wait(5)

    def open_route(enrolled, timeout):
        assert enrolled.node == "node" and enrolled.address == "10.77.0.9"
        client, server = socket.socketpair()
        resources.extend((client, server))
        spawn(lambda: serve_target(server))
        return client

    def serve_gateway(sock):
        transport = paramiko.Transport(sock)
        resources.append(transport)
        transport.add_server_key(outer_host)
        transport.start_server(server=Server("gateway", enrollments[0]))
        channel = transport.accept(5)
        if channel is None:
            return
        # A journal is local to the helper thread and never auto-replayed.
        with sqlite3.connect(":memory:") as connection:
            helper = GatewayHelper(
                gateway,
                [route],
                ReplayJournal(connection),
                lambda req, phase: True,
                open_route,
                binding=("workspace", "gateway"),
                enabled=True,
            )
            try:
                helper.serve(channel)
            except ProtocolDenied:
                pass  # A refused inner pin makes tunnel termination uncertain.

    class SocketPairDriver(ParamikoDriver):
        def connect_gateway(self, enrolled, timeout):
            assert enrolled == gateway
            client, server = socket.socketpair()
            resources.extend((client, server))
            spawn(lambda: serve_gateway(server))
            return client

    now = time.time()
    request = ExecutionRequest(
        workspace="workspace",
        project="project",
        user="owner",
        agent="worker",
        task="task",
        execution="execution",
        attempt="attempt",
        gateway="gateway",
        node="node",
        gateway_epoch=1,
        node_epoch=1,
        gateway_credential_version=1,
        target_credential_version=1,
        authority_epoch=1,
        enrollment=enrollment_digest(gateway, route),
        start_by=now + 5,
        run_until=now + 20,
    )
    try:
        ephemeral = []
        for enrollment in enrollments:
            with store.acquire(enrollment.credential_id, enrollment.binding, context) as handle:
                ephemeral.append(
                    EphemeralKey(
                        internal_transport_private_bytes(handle),
                        credential_id=enrollment.credential_id,
                    )
                )
        result = SSHTransport(
            lambda req, phase: True, enabled=True, driver=SocketPairDriver()
        ).execute(request, gateway, route, *ephemeral)
        _assert_result(result, wrong_pin, authenticated, commands)
        assert all(not key.available for key in ephemeral)
    finally:
        stop.set()
        for resource in reversed(resources):
            resource.close()
        for thread in threads:
            thread.join(timeout=6)
            assert not thread.is_alive()
        store.close()
    assert errors == []


class SocketPairAdmissionSSH:
    """Reusable actual SSH fixture for the guarded executor admission tests.

    All keys are synthetic. No TCP listener, address resolution, ambient SSH
    configuration or operator file is used. Hooks run outside controller locks.
    """

    output = b"Linux isolated-fixture x86_64\n"

    def __init__(self, state, gateway_host, target_host, *, fault=None, hook=None):
        self.state, self.gateway_host, self.target_host = state, gateway_host, target_host
        self.fault, self.hook = fault, hook
        self.authenticated, self.commands, self.opened, self.messages = [], [], [], []
        self.resources, self.threads, self.errors = [], [], []
        self.refusals = []
        self.stop = threading.Event()
        self.helper_path = state.env.path.parent / "fixture-helper-replay.sqlite3"
        self.request = None
        self.driver = self._driver()

    def hit(self, phase):
        assert not self.state.fence.active
        assert not self.state.env.db._lock._is_owned()
        if self.hook:
            self.hook(phase, self)

    def spawn(self, fn):
        def guarded():
            try:
                fn()
            except (EOFError, OSError, paramiko.SSHException, ProtocolDenied) as exc:
                import traceback

                self.refusals.append(
                    (
                        type(exc).__name__,
                        [
                            (frame.name, frame.lineno)
                            for frame in traceback.extract_tb(exc.__traceback__)
                        ],
                    )
                )
            except Exception as exc:
                self.errors.append(type(exc).__name__)

        thread = threading.Thread(target=guarded, daemon=True)
        self.threads.append(thread)
        thread.start()

    def server(self, hop):
        fixture = self
        enrollment = self.state.credentials[0 if hop == "gateway" else 1]

        class Server(paramiko.ServerInterface):
            def __init__(self):
                self.command = threading.Event()
                self.subsystem = threading.Event()

            def check_auth_publickey(self, username, key):
                expected = base64.b64decode(enrollment.public_key.split()[1])
                if username == enrollment.binding.account and key.asbytes() == expected:
                    fixture.hit("auth:" + hop)
                    fixture.authenticated.append(hop)
                    return paramiko.AUTH_SUCCESSFUL
                return paramiko.AUTH_FAILED

            def get_allowed_auths(self, username):
                return "publickey"

            def check_channel_request(self, kind, chanid):
                return (
                    paramiko.OPEN_SUCCEEDED
                    if kind == "session"
                    else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
                )

            def check_channel_subsystem_request(self, channel, name):
                if hop == "gateway" and name == "commonplace-compute-v1":
                    self.subsystem.set()
                    return True
                return False

            def check_channel_exec_request(self, channel, command):
                if hop != "target" or command != b"/usr/bin/uname -srm":
                    return False
                fixture.hit("effect")
                fixture.commands.append(command)
                self.command.set()
                return True

        return Server()

    def serve_target(self, sock):
        server = self.server("target")
        transport = paramiko.Transport(sock)
        self.resources.append(transport)
        transport.add_server_key(self.target_host)
        transport.start_server(server=server)
        channel = transport.accept(5)
        if channel is None:
            return
        if not server.command.wait(5):
            return
        assert channel.recv(1) == b""
        channel.sendall(self.output)
        channel.send_exit_status(0)
        channel.shutdown_write()
        channel.close()
        self.stop.wait(5)

    def open_route(self, route, timeout):
        self.hit("open_route")
        with sqlite3.connect(self.helper_path) as conn:
            assert (
                conn.execute(
                    "SELECT state FROM compute_transport_journal WHERE execution=?",
                    (self.request.execution,),
                ).fetchone()[0]
                == "consumed"
            )
        self.opened.append(route.node)
        client, server = socket.socketpair()
        self.resources.extend((client, server))
        self.spawn(lambda: self.serve_target(server))
        return client

    def serve_gateway(self, sock, gateway, route):
        from commonplace.compute.protocol import ADMISSION_MODE, decode_frame, encode_frame

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
            def prepare(self, request):
                fixture.request = request
                fixture.hit("ready")
                return super().prepare(request)

            def challenge(self, request, ready, go):
                fixture.hit("before_challenge")
                return super().challenge(request, ready, go)

            def admit_permit(self, request, challenge, permit):
                fixture.hit("before_helper_permit")
                if fixture.fault == "expired_permit":
                    fixture.state.env.clock.now = challenge.start_by
                return super().admit_permit(request, challenge, permit)

        class Wire:
            """Faults alter control frames only, never nested encrypted SSH."""

            def __init__(self):
                self.control_done = False

            def settimeout(self, timeout):
                channel.settimeout(timeout)

            def recv(self, count):
                return channel.recv(count)

            def sendall(self, data):
                # FrameIO sends exactly one bounded frame. Relay sends arbitrary
                # encrypted data, which this adapter leaves untouched.
                message = None
                if (
                    not self.control_done
                    and len(data) >= 4
                    and int.from_bytes(data[:4], "big") == len(data) - 4
                ):
                    message = decode_frame(data)
                if message is not None:
                    fixture.messages.append(message["kind"])
                    if message["kind"] == "admission_challenge":
                        if fixture.fault == "missing_challenge":
                            channel.close()
                            return
                        if fixture.fault == "early_opened":
                            message["kind"] = "opened"
                        elif fixture.fault in {"nonce", "channel", "digest", "approval_digest"}:
                            message[fixture.fault] = "f" * 64
                        elif fixture.fault in {"authority_epoch", "admission_v"}:
                            message[fixture.fault] += 1
                        elif fixture.fault == "expiry":
                            message["start_by"] -= 10
                        elif fixture.fault == "unknown_mode":
                            message["admission_mode"] = "unknown"
                        elif fixture.fault == "timeout":
                            channel.settimeout(0.05)
                            channel.recv(1)  # bounded, no sleep/poll loop
                        data = encode_frame(message)
                    elif message["kind"] == "opened" and fixture.fault == "lost_opened":
                        channel.close()
                        raise EOFError
                    if message["kind"] == "opened":
                        self.control_done = True
                channel.sendall(data)

            def fileno(self):
                return channel.fileno()

            def close(self):
                channel.close()

        with sqlite3.connect(self.helper_path) as connection:
            helper = Helper(
                gateway,
                [route],
                ReplayJournal(connection),
                lambda req, phase: (
                    (req.workspace, req.gateway) == (gateway.workspace, gateway.gateway)
                ),
                self.open_route,
                binding=(gateway.workspace, gateway.gateway),
                enabled=True,
                admission_mode=ADMISSION_MODE,
                wall=lambda: self.state.env.clock.now,
            )
            helper.serve(Wire())

    def _driver(self):
        fixture = self

        class Driver(ParamikoDriver):
            def connect_gateway(self, gateway, timeout):
                fixture.hit("connect")
                with fixture.state.service.store._journal() as conn:
                    node, nm, outer, gm = fixture.state.service.store._node(
                        conn, fixture.state.env.principal, fixture.state.node["id"]
                    )
                    route = fixture.state.service.store._route(node, nm, outer["id"])
                client, server = socket.socketpair()
                fixture.resources.extend((client, server))
                fixture.spawn(lambda: fixture.serve_gateway(server, gateway, route))
                return client

            def handshake(self, channel, pin, hop, timeout):
                fixture.hit("pin:" + hop)
                return super().handshake(channel, pin, hop, timeout)

            def authenticate(self, transport, account, key, timeout):
                fixture.hit("before_auth:" + account)
                return super().authenticate(transport, account, key, timeout)

            def helper(self, transport, timeout):
                channel = super().helper(transport, timeout)
                if fixture.fault not in {"missing_permit", "partial_permit", "tamper_permit"}:
                    return channel
                from commonplace.compute.protocol import decode_frame, encode_frame

                class ControllerWire:
                    def settimeout(self, value):
                        channel.settimeout(value)

                    def recv(self, count):
                        return channel.recv(count)

                    def sendall(self, data):
                        message = decode_frame(data)
                        if message["kind"] == "admission_permit":
                            if fixture.fault == "tamper_permit":
                                data = encode_frame({**message, "nonce": "f" * 64})
                            else:
                                if fixture.fault == "partial_permit":
                                    channel.sendall(data[:7])
                                channel.close()
                                raise EOFError
                        channel.sendall(data)

                    def close(self):
                        channel.close()

                return ControllerWire()

            def command(self, transport, command, timeout):
                fixture.hit("command")
                return super().command(transport, command, timeout)

        return Driver()

    def close(self):
        self.stop.set()
        for resource in reversed(tuple(self.resources)):
            resource.close()
        for thread in self.threads:
            thread.join(timeout=6)
            assert not thread.is_alive()
        assert self.errors == []
