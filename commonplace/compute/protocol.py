"""Inert Compute helper protocol v1; constructing a value grants no authority.

Public integration seams: Limits, HostKeyPin, GatewayEnrollment, EnrolledRoute,
ExecutionRequest, compile_operation, encode_frame/decode_frame, FrameIO,
ReplayJournal and GatewayHelper. GatewayHelper receives protected installation
bindings, an authenticated SSH-key workspace/gateway binding, a current-authority
callback and an explicitly supplied SQLite connection. It never reads config,
credentials or inventory and never creates a listener. Passive collection is
explicitly unavailable; active discovery and scripts are unsupported.

The journal consumes an execution (across all attempts) before calling the route
opener. Keep its rows across restarts/restores; deleting them re-enables replay.
Uncertain tunnels keep their capacity reservation until an administrator/service
reconciles the journal with independent evidence. This is NOT the controller's
cross-store approval/revocation fence; the parent must implement that separately.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import math
import re
import secrets
import select
import sqlite3
import struct
import time
from dataclasses import asdict, dataclass, field
from types import MappingProxyType

VERSION = 1
MAX_FRAME_BYTES = 8192
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_PRIVATE = tuple(
    ipaddress.ip_network(x) for x in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)


class ProtocolDenied(ValueError):
    """Safe, fixed reason code; never includes remote text or key material."""


def identifier(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise ProtocolDenied("invalid_identifier")
    return value


def finite(value):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ProtocolDenied("invalid_time")
    return value


def positive_integer(value, ceiling=2**31 - 1):
    if type(value) is not int or not 1 <= value <= ceiling:
        raise ProtocolDenied("invalid_integer")
    return value


def exact_fields(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise ProtocolDenied("invalid_fields")
    return value


def numeric_address(value, *, target=False):
    try:
        if type(value) is not str or "%" in value:
            raise ValueError
        address = ipaddress.ip_address(value)
        if str(address) != value or address.is_unspecified or address.is_multicast:
            raise ValueError
        if target and not any(
            address in network for network in _PRIVATE if network.version == address.version
        ):
            raise ValueError
        if address.is_loopback or address.is_link_local or getattr(address, "ipv4_mapped", None):
            raise ValueError
        if address.version == 4 and str(address).endswith(".255"):
            raise ValueError
    except ValueError:
        raise ProtocolDenied("invalid_numeric_address") from None
    return value


def canonical(value):
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("ascii")
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ProtocolDenied("invalid_json") from None


@dataclass(frozen=True)
class Limits:
    runtime_seconds: int = 30
    connect_seconds: int = 5
    admission_seconds: int = 5
    output_bytes: int = 65536
    tunnel_bytes: int = 1048576
    tunnels: int = 2

    def __post_init__(self):
        for name, ceiling in (
            ("runtime_seconds", 30),
            ("connect_seconds", 5),
            ("admission_seconds", 5),
            ("output_bytes", 65536),
            ("tunnel_bytes", 1048576),
            ("tunnels", 2),
        ):
            positive_integer(getattr(self, name), ceiling)

    def fits(self, ceiling):
        if not isinstance(ceiling, Limits) or any(
            value > getattr(ceiling, name) for name, value in asdict(self).items()
        ):
            raise ProtocolDenied("local_limit_exceeded")

    @classmethod
    def from_dict(cls, value):
        exact_fields(value, cls.__dataclass_fields__)
        return cls(**value)


@dataclass(frozen=True)
class HostKeyPin:
    algorithm: str
    key: bytes = field(repr=False)

    def __post_init__(self):
        if self.algorithm not in {
            "ssh-ed25519",
            "ecdsa-sha2-nistp256",
            "ecdsa-sha2-nistp384",
            "ecdsa-sha2-nistp521",
            "ssh-rsa",
        }:
            raise ProtocolDenied("unsupported_hostkey")
        if type(self.key) is not bytes or not 16 <= len(self.key) <= 4096:
            raise ProtocolDenied("invalid_hostkey")

    def verify(self, remote_key, hop):
        if remote_key.get_name() != self.algorithm or not hmac.compare_digest(
            remote_key.asbytes(), self.key
        ):
            raise ProtocolDenied(
                "gateway_hostkey_mismatch" if hop == "gateway" else "target_hostkey_mismatch"
            )

    def document(self):
        return {"algorithm": self.algorithm, "key": base64.b64encode(self.key).decode("ascii")}


@dataclass(frozen=True)
class GatewayEnrollment:
    workspace: str
    gateway: str
    address: str
    port: int
    account: str
    pin: HostKeyPin
    epoch: int
    credential_version: int
    credential_id: str

    def __post_init__(self):
        for value in (self.workspace, self.gateway, self.account, self.credential_id):
            identifier(value)
        numeric_address(self.address)
        positive_integer(self.port, 65535)
        positive_integer(self.epoch)
        positive_integer(self.credential_version)
        if not isinstance(self.pin, HostKeyPin):
            raise ProtocolDenied("invalid_hostkey")


@dataclass(frozen=True)
class EnrolledRoute:
    workspace: str
    gateway: str
    node: str
    address: str
    port: int
    interface: str
    account: str
    pin: HostKeyPin
    epoch: int
    credential_version: int
    credential_id: str

    def __post_init__(self):
        for value in (
            self.workspace,
            self.gateway,
            self.node,
            self.interface,
            self.account,
            self.credential_id,
        ):
            identifier(value)
        numeric_address(self.address, target=True)
        positive_integer(self.port, 65535)
        positive_integer(self.epoch)
        positive_integer(self.credential_version)
        if not isinstance(self.pin, HostKeyPin):
            raise ProtocolDenied("invalid_hostkey")


def enrollment_digest(gateway, route):
    if (gateway.workspace, gateway.gateway) != (
        route.workspace,
        route.gateway,
    ) or gateway.address == route.address:
        raise ProtocolDenied("enrollment_mismatch")
    values = []
    for binding in (gateway, route):
        document = asdict(binding)
        document["pin"] = binding.pin.document()
        values.append(document)
    return hashlib.sha256(canonical(values)).hexdigest()


def compile_operation(operation, arguments):
    """Return a literal Linux probe; no interpolation or caller shell fragments."""
    if (
        operation != "system.info.v1"
        or type(arguments) not in (dict, MappingProxyType)
        or arguments
    ):
        raise ProtocolDenied("unsupported_operation")
    return "/usr/bin/uname -srm"


@dataclass(frozen=True)
class ExecutionRequest:
    workspace: str
    project: str
    user: str
    agent: str
    task: str
    execution: str
    attempt: str
    gateway: str
    node: str
    gateway_epoch: int
    node_epoch: int
    gateway_credential_version: int
    target_credential_version: int
    authority_epoch: int
    enrollment: str
    start_by: float
    run_until: float
    limits: Limits = field(default_factory=Limits)
    operation: str = "system.info.v1"
    arguments: dict = field(default_factory=dict)

    def __post_init__(self):
        for name in (
            "workspace",
            "project",
            "user",
            "agent",
            "task",
            "execution",
            "attempt",
            "gateway",
            "node",
        ):
            identifier(getattr(self, name))
        for name in (
            "gateway_epoch",
            "node_epoch",
            "gateway_credential_version",
            "target_credential_version",
            "authority_epoch",
        ):
            positive_integer(getattr(self, name))
        if type(self.enrollment) is not str or not _HEX.fullmatch(self.enrollment):
            raise ProtocolDenied("invalid_digest")
        if not isinstance(self.limits, Limits):
            raise ProtocolDenied("invalid_limits")
        finite(self.start_by)
        finite(self.run_until)
        if self.run_until <= self.start_by:
            raise ProtocolDenied("invalid_deadline")
        compile_operation(self.operation, self.arguments)
        object.__setattr__(self, "arguments", MappingProxyType({}))

    def document(self):
        result = {name: getattr(self, name) for name in self.__dataclass_fields__}
        result["arguments"] = dict(self.arguments)
        result["limits"] = asdict(self.limits)
        return result

    @property
    def digest(self):
        return hashlib.sha256(canonical(self.document())).hexdigest()

    def message(self):
        return {"v": VERSION, "kind": "open_enrolled_ssh", **self.document()}

    @classmethod
    def from_message(cls, value):
        exact_fields(value, {"v", "kind", *cls.__dataclass_fields__})
        if (
            type(value["v"]) is not int
            or value["v"] != VERSION
            or value["kind"] != "open_enrolled_ssh"
        ):
            raise ProtocolDenied("unsupported_protocol")
        fields = {name: value[name] for name in cls.__dataclass_fields__}
        fields["limits"] = Limits.from_dict(fields["limits"])
        return cls(**fields)

    def validate_binding(self, gateway, route, ceiling, now):
        self.limits.fits(ceiling)
        finite(now)
        if now >= self.start_by or now >= self.run_until:
            raise ProtocolDenied("admission_expired")
        if (
            self.workspace,
            self.gateway,
            self.node,
            self.gateway_epoch,
            self.node_epoch,
            self.gateway_credential_version,
            self.target_credential_version,
            self.enrollment,
        ) != (
            gateway.workspace,
            gateway.gateway,
            route.node,
            gateway.epoch,
            route.epoch,
            gateway.credential_version,
            route.credential_version,
            enrollment_digest(gateway, route),
        ):
            raise ProtocolDenied("enrollment_mismatch")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolDenied("duplicate_field")
        result[key] = value
    return result


def _json_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ProtocolDenied("invalid_json")
    return result


def _json_constant(value):
    raise ProtocolDenied("invalid_json")


def encode_frame(value):
    if type(value) is not dict:
        raise ProtocolDenied("invalid_frame")
    body = canonical(value)
    if not 1 <= len(body) <= MAX_FRAME_BYTES:
        raise ProtocolDenied("frame_too_large")
    return struct.pack("!I", len(body)) + body


def decode_frame(frame):
    if type(frame) is not bytes or len(frame) < 4:
        raise ProtocolDenied("invalid_frame")
    length = struct.unpack("!I", frame[:4])[0]
    if not 1 <= length <= MAX_FRAME_BYTES or length != len(frame) - 4:
        raise ProtocolDenied("invalid_frame_size")
    try:
        result = json.loads(
            frame[4:].decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_json_constant,
            parse_float=_json_float,
        )
    except (UnicodeError, ValueError, RecursionError):
        raise ProtocolDenied("invalid_json") from None
    if type(result) is not dict:
        raise ProtocolDenied("invalid_frame")
    return result


class FrameIO:
    """Unbuffered framing: never consume bytes from the subsequent nested SSH."""

    def __init__(self, channel, deadline, *, monotonic=time.monotonic):
        self.channel = channel
        self.deadline = deadline
        self.monotonic = monotonic
        self.used = 0

    def _timeout(self):
        remaining = self.deadline - self.monotonic()
        if remaining <= 0:
            raise ProtocolDenied("deadline_exceeded")
        self.channel.settimeout(remaining)

    def _read(self, count):
        result = bytearray()
        while len(result) < count:
            self._timeout()
            part = self.channel.recv(count - len(result))
            if not part or len(part) > count - len(result):
                raise ProtocolDenied("control_disconnected")
            result.extend(part)
            self.used += len(part)
        self._timeout()
        return bytes(result)

    def receive(self):
        header = self._read(4)
        size = struct.unpack("!I", header)[0]
        if not 1 <= size <= MAX_FRAME_BYTES:
            raise ProtocolDenied("invalid_frame_size")
        return decode_frame(header + self._read(size))

    def send(self, message):
        data = encode_frame(message)
        self._timeout()
        self.used += len(data)
        self.channel.sendall(data)
        self._timeout()


_ADMISSION_FIELDS = {
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


def admission_message(kind, request, channel, nonce, start_by):
    return {
        "v": VERSION,
        "kind": kind,
        "execution": request.execution,
        "attempt": request.attempt,
        "digest": request.digest,
        "channel": channel,
        "nonce": nonce,
        "start_by": start_by,
        "run_until": request.run_until,
    }


def validate_admission(message, kind, request):
    exact_fields(message, _ADMISSION_FIELDS)
    if type(message["v"]) is not int or message["v"] != VERSION or message["kind"] != kind:
        raise ProtocolDenied("unsupported_protocol")
    if (message["execution"], message["attempt"], message["digest"], message["run_until"]) != (
        request.execution,
        request.attempt,
        request.digest,
        request.run_until,
    ):
        raise ProtocolDenied("admission_mismatch")
    for name in ("channel", "nonce"):
        if type(message[name]) is not str or not _HEX.fullmatch(message[name]):
            raise ProtocolDenied("invalid_nonce")
    finite(message["start_by"])
    finite(message["run_until"])


class ReplayJournal:
    """Durable local consumption, using a parent-supplied protected connection.

    Supply a fresh connection outside any existing transaction. No pruning,
    implicit retry or automatic uncertain-reservation release is provided.
    """

    def __init__(self, connection):
        self.connection = connection
        if connection.in_transaction:
            raise ProtocolDenied("journal_transaction_active")
        if connection.execute("PRAGMA synchronous").fetchone()[0] < 2:
            raise ProtocolDenied("journal_durability_required")
        connection.execute("""CREATE TABLE IF NOT EXISTS compute_transport_journal (
            workspace TEXT NOT NULL, gateway TEXT NOT NULL, execution TEXT NOT NULL,
            attempt TEXT NOT NULL, digest TEXT NOT NULL, nonce TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL, PRIMARY KEY(workspace, gateway, execution))""")
        connection.commit()

    def consume(self, request, nonce):
        db = self.connection
        if db.in_transaction:
            raise ProtocolDenied("journal_transaction_active")
        try:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM compute_transport_journal WHERE workspace=? AND gateway=? AND execution=?",
                (request.workspace, request.gateway, request.execution),
            ).fetchone():
                raise ProtocolDenied("replayed_execution")
            count = db.execute(
                "SELECT count(*) FROM compute_transport_journal WHERE workspace=? AND gateway=? AND state IN ('consumed', 'uncertain')",
                (request.workspace, request.gateway),
            ).fetchone()[0]
            if count >= request.limits.tunnels:
                raise ProtocolDenied("tunnel_capacity_exceeded")
            db.execute(
                "INSERT INTO compute_transport_journal VALUES (?,?,?,?,?,?, 'consumed')",
                (
                    request.workspace,
                    request.gateway,
                    request.execution,
                    request.attempt,
                    request.digest,
                    nonce,
                ),
            )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            raise ProtocolDenied("replayed_execution") from None
        except BaseException:
            db.rollback()
            raise

    def uncertain(self, request):
        if self.connection.in_transaction:
            raise ProtocolDenied("journal_transaction_active")
        with self.connection:
            self.connection.execute(
                "UPDATE compute_transport_journal SET state='uncertain' WHERE workspace=? AND gateway=? AND execution=?",
                (request.workspace, request.gateway, request.execution),
            )

    def state(self, request):
        row = self.connection.execute(
            "SELECT state FROM compute_transport_journal WHERE workspace=? AND gateway=? AND execution=?",
            (request.workspace, request.gateway, request.execution),
        ).fetchone()
        return row[0] if row else None


class GatewayHelper:
    """Single-session forced-command engine, disabled unless explicitly enabled.

    ``authorize(request, phase) is True`` must check current authority. Protected
    routes are copied at construction. ``open_route(route, timeout)`` is a trusted
    numeric/interface-bound socket capability, never supplied by a wire request.
    SSH server installation must bind the authenticated public key to ``binding``
    and forbid shell/forwarding/PTY channels. No server is installed here.
    """

    def __init__(
        self,
        gateway,
        routes,
        journal,
        authorize,
        open_route,
        *,
        binding,
        limits=None,
        enabled=False,
        wall=time.time,
        monotonic=time.monotonic,
        nonce=lambda: secrets.token_hex(32),
    ):
        limits = Limits() if limits is None else limits
        if (
            not isinstance(gateway, GatewayEnrollment)
            or not isinstance(journal, ReplayJournal)
            or not isinstance(limits, Limits)
        ):
            raise ProtocolDenied("invalid_helper_configuration")
        copied = {}
        for route in routes:
            if not isinstance(route, EnrolledRoute) or route.node in copied:
                raise ProtocolDenied("invalid_routes")
            enrollment_digest(gateway, route)
            copied[route.node] = route
        if (
            binding != (gateway.workspace, gateway.gateway)
            or not callable(authorize)
            or not callable(open_route)
        ):
            raise ProtocolDenied("invalid_controller_binding")
        self.gateway, self.routes, self.journal = gateway, MappingProxyType(copied), journal
        self.authorize, self.open_route = authorize, open_route
        self.limits, self.enabled = limits, enabled is True
        self.wall, self.monotonic, self.nonce = wall, monotonic, nonce
        self._pending = None

    def _route(self, request, phase):
        if not self.enabled:
            raise ProtocolDenied("transport_disabled")
        route = self.routes.get(request.node)
        if route is None:
            raise ProtocolDenied("node_not_enrolled")
        request.validate_binding(self.gateway, route, self.limits, self.wall())
        try:
            allowed = self.authorize(request, phase)
        except Exception:
            raise ProtocolDenied("authority_unavailable") from None
        if allowed is not True:
            raise ProtocolDenied("authority_denied")
        return route

    def prepare(self, request):
        self._route(request, "ready")
        ready = admission_message(
            "ready",
            request,
            self.nonce(),
            self.nonce(),
            min(request.start_by, self.wall() + request.limits.admission_seconds),
        )
        validate_admission(ready, "ready", request)
        self._pending = (
            request.digest,
            dict(ready),
            self.monotonic() + ready["start_by"] - self.wall(),
        )
        return ready

    def admit(self, request, ready, go):
        pending, self._pending = self._pending, None
        validate_admission(ready, "ready", request)
        validate_admission(go, "go", request)
        if (
            pending is None
            or pending[:2] != (request.digest, ready)
            or self.monotonic() >= pending[2]
            or go != {**ready, "kind": "go"}
            or self.wall() >= go["start_by"]
        ):
            raise ProtocolDenied("admission_mismatch")
        route = self._route(request, "admit")
        self.journal.consume(request, go["nonce"])
        try:
            remaining = min(
                request.limits.connect_seconds,
                go["start_by"] - self.wall(),
                pending[2] - self.monotonic(),
            )
            if remaining <= 0:
                raise ProtocolDenied("admission_expired")
            return self.open_route(route, remaining)
        except BaseException:
            self.journal.uncertain(request)
            raise

    def serve(self, channel, *, wait_readable=None):
        """Serve exactly one control exchange, then relay bounded nested SSH bytes.

        The returned connection is never retried. Any routed session ending is
        journaled uncertain: encrypted traffic cannot prove target termination.
        ``wait_readable`` is an isolated synthetic-test seam.
        """
        try:
            return self._serve(channel, wait_readable)
        finally:
            channel.close()

    def _serve(self, channel, wait_readable):
        if not self.enabled:
            raise ProtocolDenied("transport_disabled")
        deadline = self.monotonic() + self.limits.connect_seconds
        io = FrameIO(channel, deadline, monotonic=self.monotonic)
        message = io.receive()
        if message.get("kind") in {"describe", "collect_passive"}:
            exact_fields(message, {"v", "kind", "workspace", "gateway"})
            if (
                type(message["v"]) is not int
                or message["v"] != VERSION
                or (message["workspace"], message["gateway"])
                != (self.gateway.workspace, self.gateway.gateway)
            ):
                raise ProtocolDenied("invalid_controller_binding")
            if message["kind"] == "collect_passive":
                raise ProtocolDenied("passive_source_unavailable")
            io.send(
                {
                    "v": VERSION,
                    "kind": "description",
                    "methods": ["describe", "open_enrolled_ssh"],
                    "passive_sources": [],
                    "limits": asdict(self.limits),
                }
            )
            return
        request = ExecutionRequest.from_message(message)
        ready = self.prepare(request)
        io.send(ready)
        io.deadline = min(deadline, self.monotonic() + max(0, ready["start_by"] - self.wall()))
        go = io.receive()
        if io.used + len(encode_frame({**ready, "kind": "opened"})) > request.limits.tunnel_bytes:
            raise ProtocolDenied("tunnel_budget_exceeded")
        target = self.admit(request, ready, go)
        try:
            io.send({**ready, "kind": "opened"})
            runtime = min(request.limits.runtime_seconds, request.run_until - self.wall())
            self._relay(
                channel,
                target,
                self.monotonic() + runtime,
                request.limits.tunnel_bytes,
                wait_readable,
                used=io.used,
            )
        finally:
            try:
                target.close()
            finally:
                self.journal.uncertain(request)

    def _relay(self, channel, target, deadline, ceiling, wait_readable, *, used=0):
        wait = wait_readable or (
            lambda sources, timeout: select.select(sources, [], [], timeout)[0]
        )
        while True:
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise ProtocolDenied("deadline_exceeded")
            if used > ceiling:
                raise ProtocolDenied("tunnel_budget_exceeded")
            try:
                sources = wait([channel, target], remaining)
            except (OSError, ValueError):
                # A peer may close its channel while select builds descriptors.
                # The encrypted target's outcome remains uncertain in the journal.
                raise ProtocolDenied("tunnel_disconnected") from None
            if not sources:
                raise ProtocolDenied("deadline_exceeded")
            for source in sources:
                source.settimeout(max(0.001, deadline - self.monotonic()))
                data = source.recv(min(32768, ceiling - used + 1))
                if self.monotonic() >= deadline:
                    raise ProtocolDenied("deadline_exceeded")
                if not data:
                    return
                used += len(data)
                if used > ceiling:
                    raise ProtocolDenied("tunnel_budget_exceeded")
                destination = target if source is channel else channel
                destination.settimeout(max(0.001, deadline - self.monotonic()))
                destination.sendall(data)
