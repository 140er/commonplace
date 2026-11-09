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
def test_encrypted_custody_to_actual_two_hop_ssh_without_network(tmp_path, wrong_pin):
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
