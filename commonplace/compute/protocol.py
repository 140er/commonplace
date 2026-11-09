"""Inert Compute helper protocol v1; constructing a value grants no authority.

Public integration seams: Limits, HostKeyPin, GatewayEnrollment, EnrolledRoute,
ExecutionRequest, compile_operation, encode_frame/decode_frame, FrameIO,
ReplayJournal and GatewayHelper. GatewayHelper receives protected installation
bindings, an authenticated SSH-key workspace/gateway binding, a current-authority
callback and an explicitly supplied SQLite connection. Explicit
controller-ssh-permit-v1 mode adds a fresh controller admission round trip on the
same authenticated SSH stream. Passive permits require their own explicit
passive_admission_mode and use a separate purpose/replay namespace.
It never reads config,
credentials or inventory and never creates a listener. Passive collection needs
an explicitly configured trusted collector, independent ceiling and live scope
resolver; it is unavailable by default. Active discovery/scripts are unsupported.

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

from .discovery import DiscoveryDenied, PassiveCollector, _scope, validate_sources
from .scopes import DiscoveryContext, DiscoveryLimits, NetworkScope, ScopeDenied

VERSION = 1
ADMISSION_MODE = "controller-ssh-permit-v1"
ADMISSION_VERSION = 1
PASSIVE_PURPOSE = "compute-passive-collection"
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
class PassiveDiscoveryRequest:
    """Exact selection of an authenticated stored scope, never a wire grant.

    The trusted resolver must fetch ``scope`` in this exact context and check
    current authority/epoch. Caller-supplied CIDRs, approval and paths are absent.
    """

    discovery: str
    scope: str
    gateway_epoch: int
    authority_epoch: int
    context: DiscoveryContext
    sources: tuple[str, ...]
    limits: DiscoveryLimits
    output_bytes: int = MAX_FRAME_BYTES
    method: str = "passive"
    passive_admission_mode: str | None = None
    start_by: float | None = None
    run_until: float | None = None

    def __post_init__(self):
        identifier(self.discovery)
        identifier(self.scope)
        positive_integer(self.gateway_epoch)
        positive_integer(self.authority_epoch)
        if type(self.context) is not DiscoveryContext or type(self.limits) is not DiscoveryLimits:
            raise ProtocolDenied("invalid_discovery_contract")
        for name in self.context.__dataclass_fields__:
            identifier(getattr(self.context, name))
        if self.method != "passive":
            raise ProtocolDenied("unsupported_discovery_method")
        positive_integer(self.output_bytes, MAX_FRAME_BYTES)
        try:
            object.__setattr__(self, "sources", validate_sources(self.sources))
        except DiscoveryDenied:
            raise ProtocolDenied("unsupported_passive_source") from None
        if self.passive_admission_mode is not None:
            if self.passive_admission_mode != ADMISSION_MODE:
                raise ProtocolDenied("unsupported_admission_mode")
            finite(self.start_by)
            finite(self.run_until)
            if self.start_by > self.run_until:
                raise ProtocolDenied("admission_expired")
        elif self.start_by is not None or self.run_until is not None:
            raise ProtocolDenied("unsupported_admission_mode")

    @property
    def digest(self):
        return hashlib.sha256(canonical(self.message())).hexdigest()

    def message(self):
        result = {"v": VERSION, "kind": "collect_passive", **asdict(self)}
        if self.passive_admission_mode is None:
            for name in ("passive_admission_mode", "start_by", "run_until"):
                del result[name]
        return result

    @classmethod
    def from_message(cls, value):
        fields = {"v", "kind", *cls.__dataclass_fields__}
        if type(value) is dict and "passive_admission_mode" not in value:
            fields -= {"passive_admission_mode", "start_by", "run_until"}
        exact_fields(value, fields)
        if "passive_admission_mode" in value and value["passive_admission_mode"] != ADMISSION_MODE:
            raise ProtocolDenied("unsupported_admission_mode")
        if (
            type(value["v"]) is not int
            or value["v"] != VERSION
            or value["kind"] != "collect_passive"
        ):
            raise ProtocolDenied("unsupported_protocol")
        fields = {name: value[name] for name in cls.__dataclass_fields__ if name in value}
        exact_fields(fields["context"], DiscoveryContext.__dataclass_fields__)
        exact_fields(fields["limits"], DiscoveryLimits.__dataclass_fields__)
        try:
            fields["context"] = DiscoveryContext(**fields["context"])
            fields["limits"] = DiscoveryLimits(**fields["limits"])
            return cls(**fields)
        except ScopeDenied:
            raise ProtocolDenied("invalid_discovery_contract") from None


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
    if operation == "workspace.script.v1":
        if type(arguments) not in (dict, MappingProxyType) or set(arguments) != {
            "bundle_id",
            "manifest_digest",
            "script_digest",
        }:
            raise ProtocolDenied("unsupported_operation")
        identifier(arguments["bundle_id"])
        for name in ("manifest_digest", "script_digest"):
            if type(arguments[name]) is not str or not _HEX.fullmatch(arguments[name]):
                raise ProtocolDenied("invalid_digest")
        from .target_helper import TARGET_HELPER_COMMAND

        return TARGET_HELPER_COMMAND
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
    admission_mode: str | None = None
    approval_digest: str | None = None

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
        if self.admission_mode is not None or self.approval_digest is not None:
            if self.admission_mode != ADMISSION_MODE:
                raise ProtocolDenied("unsupported_admission_mode")
            if type(self.approval_digest) is not str or not _HEX.fullmatch(self.approval_digest):
                raise ProtocolDenied("invalid_digest")
        compile_operation(self.operation, self.arguments)
        if self.operation == "workspace.script.v1" and self.admission_mode != ADMISSION_MODE:
            raise ProtocolDenied("unsupported_admission_mode")
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))

    def document(self):
        result = {name: getattr(self, name) for name in self.__dataclass_fields__}
        result["arguments"] = dict(self.arguments)
        result["limits"] = asdict(self.limits)
        if self.admission_mode is None:
            del result["admission_mode"], result["approval_digest"]
        return result

    @property
    def digest(self):
        return hashlib.sha256(canonical(self.document())).hexdigest()

    def message(self):
        return {"v": VERSION, "kind": "open_enrolled_ssh", **self.document()}

    @classmethod
    def from_message(cls, value):
        if type(value) is not dict:
            raise ProtocolDenied("invalid_fields")
        fields = set(cls.__dataclass_fields__)
        if "admission_mode" not in value and "approval_digest" not in value:
            fields -= {"admission_mode", "approval_digest"}
        exact_fields(value, {"v", "kind", *fields})
        if (
            type(value["v"]) is not int
            or value["v"] != VERSION
            or value["kind"] != "open_enrolled_ssh"
        ):
            raise ProtocolDenied("unsupported_protocol")
        fields = {name: value[name] for name in fields}
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

    def __init__(self, channel, deadline, *, monotonic=time.monotonic, byte_limit=None):
        if byte_limit is not None:
            positive_integer(byte_limit, 1048576)
        self.byte_limit = byte_limit
        self.channel = channel
        self.deadline = deadline
        self.monotonic = monotonic
        self.used = 0

    def _timeout(self):
        remaining = self.deadline - self.monotonic()
        if remaining <= 0:
            raise ProtocolDenied("deadline_exceeded")
        self.channel.settimeout(remaining)

    def _budget(self, offered):
        if self.byte_limit is not None and self.used + offered > self.byte_limit:
            raise ProtocolDenied("tunnel_budget_exceeded")

    def _read(self, count):
        self._budget(count)
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
        self._budget(len(data))
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
    message = {
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
    if request.admission_mode is not None:
        message.update(
            admission_mode=request.admission_mode,
            admission_v=ADMISSION_VERSION,
            authority_epoch=request.authority_epoch,
            approval_digest=request.approval_digest,
        )
    if request.operation == "workspace.script.v1":
        message["purpose"] = request.operation
    return message


def validate_admission(message, kind, request):
    fields = _ADMISSION_FIELDS
    if request.admission_mode is not None:
        fields = fields | {"admission_mode", "admission_v", "authority_epoch", "approval_digest"}
    if request.operation == "workspace.script.v1":
        fields = fields | {"purpose"}
    exact_fields(message, fields)
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
    if request.admission_mode is not None and (
        type(message["admission_v"]) is not int
        or type(message["authority_epoch"]) is not int
        or message
        != admission_message(
            kind, request, message["channel"], message["nonce"], message["start_by"]
        )
    ):
        raise ProtocolDenied("admission_mismatch")


@dataclass(frozen=True)
class AdmissionChallenge:
    """Immutable identity received on the pinned, authenticated SSH channel.

    This value grants no authority. Only the controller's fresh fenced journal
    commit can produce its permit. SSH provides peer authentication/integrity;
    no extra shared key or bearer token is introduced.
    """

    request: ExecutionRequest
    channel: str
    nonce: str
    start_by: float

    def __post_init__(self):
        if (
            type(self.request) is not ExecutionRequest
            or self.request.admission_mode != ADMISSION_MODE
        ):
            raise ProtocolDenied("unsupported_admission_mode")
        validate_admission(self.message(), "admission_challenge", self.request)
        if self.start_by > self.request.start_by:
            raise ProtocolDenied("admission_expired")

    def message(self, kind="admission_challenge"):
        return admission_message(kind, self.request, self.channel, self.nonce, self.start_by)

    def validate_time(self, now):
        finite(now)
        if (
            not now
            < self.start_by
            <= min(self.request.start_by, now + self.request.limits.admission_seconds)
        ):
            raise ProtocolDenied("admission_expired")

    @classmethod
    def from_message(cls, message, request, ready):
        validate_admission(message, "admission_challenge", request)
        validate_admission(ready, "ready", request)
        if message != {**ready, "kind": "admission_challenge"}:
            raise ProtocolDenied("admission_mismatch")
        return cls(request, message["channel"], message["nonce"], message["start_by"])


@dataclass(frozen=True)
class PassiveAdmissionChallenge:
    """Purpose-separated identity on the existing pinned outer SSH subsystem.

    A discovery ID names its sole immutable reserved attempt. This is a typed
    challenge, never authority; the controller must commit its permit locally.
    """

    request: PassiveDiscoveryRequest
    channel: str
    nonce: str
    start_by: float

    def __post_init__(self):
        if (
            type(self.request) is not PassiveDiscoveryRequest
            or self.request.passive_admission_mode != ADMISSION_MODE
        ):
            raise ProtocolDenied("unsupported_admission_mode")
        for value in (self.channel, self.nonce):
            if type(value) is not str or not _HEX.fullmatch(value):
                raise ProtocolDenied("invalid_nonce")
        finite(self.start_by)
        if self.start_by > self.request.start_by:
            raise ProtocolDenied("admission_expired")

    def message(self, kind="passive_challenge"):
        return {
            "v": VERSION,
            "kind": kind,
            "purpose": PASSIVE_PURPOSE,
            "admission_v": ADMISSION_VERSION,
            "passive_admission_mode": self.request.passive_admission_mode,
            "discovery": self.request.discovery,
            "scope": self.request.scope,
            "digest": self.request.digest,
            "gateway_epoch": self.request.gateway_epoch,
            "authority_epoch": self.request.authority_epoch,
            "channel": self.channel,
            "nonce": self.nonce,
            "start_by": self.start_by,
            "run_until": self.request.run_until,
        }

    def validate_time(self, now):
        finite(now)
        if not (
            now < self.start_by <= min(self.request.start_by, now + 5)
            and self.start_by
            <= self.request.run_until
            <= now + self.request.limits.duration_seconds
        ):
            raise ProtocolDenied("admission_expired")

    @classmethod
    def from_message(cls, message, request, *, kind="passive_challenge", ready=None):
        if type(message) is not dict:
            raise ProtocolDenied("admission_mismatch")
        challenge = cls(
            request, message.get("channel"), message.get("nonce"), message.get("start_by")
        )
        if (
            type(message.get("v")) is not int
            or type(message.get("admission_v")) is not int
            or type(message.get("gateway_epoch")) is not int
            or type(message.get("authority_epoch")) is not int
            or message != challenge.message(kind)
            or (ready is not None and message != {**ready, "kind": kind})
        ):
            raise ProtocolDenied("admission_mismatch")
        return challenge


def passive_result(response, challenge):
    """Validate the exact permit-bound result envelope; return legacy payload."""
    header = challenge.message("passive_observations")
    fields = {"observations", "truncated", "reason", "source_bytes", "source_lines"}
    exact_fields(response, set(header) | fields)
    if len(canonical(response)) > challenge.request.output_bytes:
        raise ProtocolDenied("output_limit")
    if any(type(response[k]) is not type(v) or response[k] != v for k, v in header.items()):
        raise ProtocolDenied("admission_mismatch")
    return {
        "v": VERSION,
        "kind": "passive_observations",
        "discovery": challenge.request.discovery,
        **{k: response[k] for k in fields},
    }


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
        # Preserve older replay records; an additive migration never clears
        # consumed executions or uncertain reservations on helper restart.
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(compute_transport_journal)")
        }
        for name in ("channel", "permit_digest"):
            if name not in columns:
                connection.execute(f"ALTER TABLE compute_transport_journal ADD COLUMN {name} TEXT")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS compute_transport_channel "
            "ON compute_transport_journal(channel)"
        )
        connection.commit()
        self._passive_schema()

    def consume(self, request, nonce, *, channel=None, permit=None):
        if request.admission_mode is not None:
            validate_admission(permit, "admission_permit", request)
            if permit["channel"] != channel or permit["nonce"] != nonce:
                raise ProtocolDenied("admission_mismatch")
        elif channel is not None or permit is not None:
            raise ProtocolDenied("admission_mismatch")
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
                "INSERT INTO compute_transport_journal(workspace,gateway,execution,attempt,digest,"
                "nonce,state,channel,permit_digest) VALUES (?,?,?,?,?,?, 'consumed',?,?)",
                (
                    request.workspace,
                    request.gateway,
                    request.execution,
                    request.attempt,
                    request.digest,
                    nonce,
                    channel,
                    hashlib.sha256(canonical(permit)).hexdigest() if permit is not None else None,
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

    def _passive_schema(self):
        # A separate purpose namespace in the SAME protected replay database.
        # Existing execution consumption and unknown reservations are untouched.
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS compute_passive_journal (
                workspace TEXT NOT NULL, gateway TEXT NOT NULL, discovery TEXT NOT NULL,
                scope TEXT NOT NULL, digest TEXT NOT NULL, purpose TEXT NOT NULL,
                channel TEXT NOT NULL UNIQUE, nonce TEXT NOT NULL UNIQUE,
                permit TEXT NOT NULL, state TEXT NOT NULL,
                PRIMARY KEY(workspace,gateway,discovery));
            CREATE TRIGGER IF NOT EXISTS compute_passive_no_delete
                BEFORE DELETE ON compute_passive_journal
                BEGIN SELECT RAISE(ABORT,'passive replay cannot be deleted'); END;
            CREATE TRIGGER IF NOT EXISTS compute_passive_no_rebind BEFORE UPDATE OF
                workspace,gateway,discovery,scope,digest,purpose,channel,nonce,permit
                ON compute_passive_journal
                BEGIN SELECT RAISE(ABORT,'passive replay cannot be rebound'); END;
        """)

    def consume_passive(self, request, challenge, permit, *, concurrency):
        if self.connection.in_transaction:
            raise ProtocolDenied("journal_transaction_active")
        if type(challenge) is not PassiveAdmissionChallenge or challenge.request != request:
            raise ProtocolDenied("admission_mismatch")
        PassiveAdmissionChallenge.from_message(permit, request, kind="passive_permit")
        if permit != challenge.message("passive_permit"):
            raise ProtocolDenied("admission_mismatch")
        positive_integer(concurrency)
        self._passive_schema()
        db = self.connection
        try:
            db.execute("BEGIN IMMEDIATE")
            if (
                db.execute(
                    "SELECT count(*) FROM compute_passive_journal WHERE workspace=? AND gateway=? "
                    "AND state IN ('consumed','uncertain')",
                    (request.context.workspace, request.context.gateway),
                ).fetchone()[0]
                >= concurrency
            ):
                raise ProtocolDenied("collection_capacity_exceeded")
            db.execute(
                "INSERT INTO compute_passive_journal VALUES (?,?,?,?,?,?,?,?,?,'consumed')",
                (
                    request.context.workspace,
                    request.context.gateway,
                    request.discovery,
                    request.scope,
                    request.digest,
                    PASSIVE_PURPOSE,
                    challenge.channel,
                    challenge.nonce,
                    canonical(permit).decode(),
                ),
            )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            raise ProtocolDenied("replayed_discovery") from None
        except BaseException:
            db.rollback()
            raise

    def finish_passive(self, request, *, known=False):
        if self.connection.in_transaction:
            raise ProtocolDenied("journal_transaction_active")
        with self.connection:
            self.connection.execute(
                "UPDATE compute_passive_journal SET state=? WHERE workspace=? AND gateway=? AND discovery=?",
                (
                    "completed" if known else "uncertain",
                    request.context.workspace,
                    request.context.gateway,
                    request.discovery,
                ),
            )


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
        passive_collector=None,
        passive_ceiling=None,
        passive_scope=None,
        admission_mode=None,
        passive_admission_mode=None,
        scripts_enabled=False,
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
        if admission_mode not in (None, ADMISSION_MODE):
            raise ProtocolDenied("unsupported_admission_mode")
        if passive_admission_mode not in (None, ADMISSION_MODE):
            raise ProtocolDenied("unsupported_admission_mode")
        self.passive_admission_mode = passive_admission_mode
        self.scripts_enabled = scripts_enabled is True
        self._passive_pending = self._passive_admitted = None
        self.admission_mode = admission_mode
        self.gateway, self.routes, self.journal = gateway, MappingProxyType(copied), journal
        self.authorize, self.open_route = authorize, open_route
        self.limits, self.enabled = limits, enabled is True
        self.wall, self.monotonic, self.nonce = wall, monotonic, nonce
        configured = (passive_collector, passive_ceiling, passive_scope)
        if any(x is not None for x in configured) and (
            type(passive_collector) is not PassiveCollector
            or type(passive_ceiling) is not NetworkScope
            or not callable(passive_scope)
            or (passive_ceiling.context.workspace, passive_ceiling.context.gateway) != binding
        ):
            raise ProtocolDenied("invalid_passive_configuration")
        self.passive_collector = passive_collector
        self.passive_ceiling, self.passive_scope = passive_ceiling, passive_scope
        self._pending = None

    def collect_passive(self, request, *, cancelled=lambda: False):
        """Return bounded observations without touching route/enrollment state.

        The existing authenticated authority callback must understand discovery
        requests and the trusted resolver must return current stored authority.
        The protected ceiling is checked independently for every observation.
        """
        if not self.enabled:
            raise ProtocolDenied("transport_disabled")
        if self.passive_collector is None:
            raise ProtocolDenied("passive_source_unavailable")
        if type(request) is not PassiveDiscoveryRequest:
            raise ProtocolDenied("invalid_discovery_contract")
        if (request.context.workspace, request.context.gateway) != (
            self.gateway.workspace,
            self.gateway.gateway,
        ) or request.gateway_epoch != self.gateway.epoch:
            raise ProtocolDenied("invalid_controller_binding")

        if request.passive_admission_mode != self.passive_admission_mode:
            raise ProtocolDenied("unsupported_admission_mode")
        admitted, self._passive_admitted = self._passive_admitted, None
        header = {"v": VERSION, "kind": "passive_observations", "discovery": request.discovery}
        if self.passive_admission_mode is not None:
            if admitted is None or admitted[0].request != request:
                raise ProtocolDenied("admission_permit_required")
            header = admitted[0].message("passive_observations")

        def current_scope():
            return self._passive_scope(request)

        def stopped():
            return cancelled() or (
                admitted is not None
                and (self.wall() >= request.run_until or self.monotonic() >= admitted[1])
            )

        try:
            result = self.passive_collector.collect(
                context=request.context,
                limits=request.limits,
                sources=request.sources,
                ceiling=self.passive_ceiling,
                current_scope=current_scope,
                output_bytes=min(request.output_bytes, self.limits.output_bytes),
                envelope_bytes=len(canonical(header)) - 1,
                cancelled=stopped,
            )
        except ScopeDenied:
            raise ProtocolDenied("passive_collection_denied") from None
        return {**header, **result.document()}

    def _passive_scope(self, request):
        if not self.enabled or self.passive_collector is None:
            raise ProtocolDenied("passive_source_unavailable")
        if request.passive_admission_mode != self.passive_admission_mode or (
            request.context.workspace,
            request.context.gateway,
            request.gateway_epoch,
        ) != (self.gateway.workspace, self.gateway.gateway, self.gateway.epoch):
            raise ProtocolDenied("invalid_controller_binding")
        try:
            now = finite(self.wall())
            if request.run_until is not None and now >= request.run_until:
                raise ProtocolDenied("admission_expired")
            if self.authorize(request, "collect_passive") is not True:
                raise ProtocolDenied("authority_denied")
            current = self.passive_scope(request)
            now = finite(self.wall())
            _scope(self.passive_ceiling, request.context, request.limits, now)
            _scope(current, request.context, request.limits, now)
            if not set(request.sources) <= self.passive_collector.sources:
                raise ProtocolDenied("passive_source_unavailable")
            return current
        except ProtocolDenied:
            raise
        except ScopeDenied:
            raise ProtocolDenied("passive_collection_denied") from None
        except Exception:
            raise ProtocolDenied("authority_unavailable") from None

    def prepare_passive(self, request):
        if (
            type(request) is not PassiveDiscoveryRequest
            or request.passive_admission_mode != ADMISSION_MODE
            or self.passive_admission_mode != ADMISSION_MODE
            or self._passive_pending is not None
        ):
            raise ProtocolDenied("unsupported_admission_mode")
        self._passive_scope(request)
        challenge = PassiveAdmissionChallenge(
            request, self.nonce(), self.nonce(), min(request.start_by, self.wall() + 5)
        )
        challenge.validate_time(self.wall())
        now, tick = self.wall(), self.monotonic()
        self._passive_pending = (
            "ready",
            challenge,
            tick + challenge.start_by - now,
            tick + request.run_until - now,
        )
        return challenge.message("passive_ready")

    def challenge_passive(self, request, ready, go):
        pending, self._passive_pending = self._passive_pending, None
        challenge = PassiveAdmissionChallenge.from_message(ready, request, kind="passive_ready")
        PassiveAdmissionChallenge.from_message(go, request, kind="passive_go", ready=ready)
        if pending is None or pending[:2] != ("ready", challenge) or self.monotonic() >= pending[2]:
            raise ProtocolDenied("admission_mismatch")
        challenge.validate_time(self.wall())
        self._passive_scope(request)
        self._passive_pending = ("challenge", *pending[1:])
        return challenge.message()

    def admit_passive(self, request, challenge, permit):
        pending, self._passive_pending = self._passive_pending, None
        if (
            self.passive_admission_mode != ADMISSION_MODE
            or type(challenge) is not PassiveAdmissionChallenge
            or challenge.request != request
            or pending is None
            or pending[:2] != ("challenge", challenge)
        ):
            raise ProtocolDenied("admission_mismatch")
        PassiveAdmissionChallenge.from_message(permit, request, kind="passive_permit")
        if permit != challenge.message("passive_permit"):
            raise ProtocolDenied("admission_mismatch")
        current = self._passive_scope(request)
        challenge.validate_time(self.wall())
        if self.monotonic() >= pending[2]:
            raise ProtocolDenied("admission_expired")
        self.journal.consume_passive(
            request,
            challenge,
            permit,
            concurrency=min(
                self.limits.tunnels,
                self.passive_ceiling.limits.concurrency,
                current.limits.concurrency,
            ),
        )
        try:
            challenge.validate_time(self.wall())
            if self.monotonic() >= pending[2]:
                raise ProtocolDenied("admission_expired")
            self._passive_admitted = (challenge, pending[3])
            return self.collect_passive(request)
        except BaseException:
            self.journal.finish_passive(request)
            raise
        finally:
            self._passive_admitted = None

    def _serve_passive(self, io, request):
        if request.passive_admission_mode != self.passive_admission_mode:
            raise ProtocolDenied("unsupported_admission_mode")
        if self.passive_admission_mode is None:
            io.deadline = self.monotonic() + request.limits.duration_seconds
            io.send(self.collect_passive(request))
            return
        ready = self.prepare_passive(request)
        pending = self._passive_pending
        io.deadline = min(io.deadline, pending[2])
        io.send(ready)
        message = self.challenge_passive(request, ready, io.receive())
        io.send(message)
        challenge = PassiveAdmissionChallenge.from_message(message, request, ready=ready)
        permit = io.receive()
        try:
            result = self.admit_passive(request, challenge, permit)
            io.deadline = pending[3]
            io.send(result)
            self.journal.finish_passive(request, known=True)
        except BaseException:
            self.journal.finish_passive(request)
            raise

    def _route(self, request, phase):
        if not self.enabled:
            raise ProtocolDenied("transport_disabled")
        if request.operation == "workspace.script.v1" and not self.scripts_enabled:
            raise ProtocolDenied("unsupported_operation")
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
        if request.admission_mode != self.admission_mode:
            raise ProtocolDenied("unsupported_admission_mode")
        if self._pending is not None:
            raise ProtocolDenied("admission_mismatch")
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
        if self.admission_mode is not None or request.admission_mode is not None:
            raise ProtocolDenied("admission_permit_required")
        return self._open(request, self._take_go(request, ready, go), go)

    def _take_go(self, request, ready, go):
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
        return pending

    def challenge(self, request, ready, go):
        if self.admission_mode != ADMISSION_MODE or request.admission_mode != ADMISSION_MODE:
            raise ProtocolDenied("unsupported_admission_mode")
        pending = self._take_go(request, ready, go)
        challenge = AdmissionChallenge.from_message(
            {**ready, "kind": "admission_challenge"}, request, ready
        )
        challenge.validate_time(self.wall())
        self._pending = (challenge, pending)
        return challenge.message()

    def admit_permit(self, request, challenge, permit):
        pending, self._pending = self._pending, None
        if (
            self.admission_mode != ADMISSION_MODE
            or type(challenge) is not AdmissionChallenge
            or challenge.request != request
            or pending is None
            or pending[0] != challenge
        ):
            raise ProtocolDenied("admission_mismatch")
        validate_admission(permit, "admission_permit", request)
        if permit != challenge.message("admission_permit"):
            raise ProtocolDenied("admission_mismatch")
        challenge.validate_time(self.wall())
        return self._open(request, pending[1], permit)

    def _open(self, request, pending, go):
        route = self._route(request, "admit")
        if self.monotonic() >= pending[2] or self.wall() >= go["start_by"]:
            raise ProtocolDenied("admission_expired")
        self.journal.consume(
            request,
            go["nonce"],
            channel=go["channel"] if request.admission_mode is not None else None,
            permit=go if request.admission_mode is not None else None,
        )
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
        io = FrameIO(
            channel, deadline, monotonic=self.monotonic, byte_limit=self.limits.tunnel_bytes
        )
        message = io.receive()
        if message.get("kind") == "collect_passive" and (
            self.passive_collector is not None
            or set(message) != {"v", "kind", "workspace", "gateway"}
        ):
            request = PassiveDiscoveryRequest.from_message(message)
            return self._serve_passive(io, request)
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
                    "methods": ["describe", "open_enrolled_ssh"]
                    + (["collect_passive"] if self.passive_collector is not None else []),
                    "passive_sources": sorted(self.passive_collector.sources)
                    if self.passive_collector is not None
                    else [],
                    "limits": asdict(self.limits),
                }
            )
            return None
        request = ExecutionRequest.from_message(message)
        io.byte_limit = min(io.byte_limit, request.limits.tunnel_bytes)
        io._budget(0)
        ready = self.prepare(request)
        io.send(ready)
        io.deadline = min(deadline, self.monotonic() + max(0, ready["start_by"] - self.wall()))
        go = io.receive()
        if io.used + len(encode_frame({**ready, "kind": "opened"})) > request.limits.tunnel_bytes:
            raise ProtocolDenied("tunnel_budget_exceeded")
        if self.admission_mode == ADMISSION_MODE:
            challenge_message = self.challenge(request, ready, go)
            io.send(challenge_message)
            permit = io.receive()
            challenge = AdmissionChallenge.from_message(challenge_message, request, ready)
            if (
                io.used + len(encode_frame({**ready, "kind": "opened"}))
                > request.limits.tunnel_bytes
            ):
                raise ProtocolDenied("tunnel_budget_exceeded")
            target = self.admit_permit(request, challenge, permit)
        else:
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
