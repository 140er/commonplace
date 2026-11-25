"""Immutable opaque envelopes and bounded errors; no cryptography or authority."""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, cast

RecordKind = Literal["memory", "printed-recovery"]
WriterTrust = Literal["trusted", "standard", "untrusted"]
ErrorCode = Literal[
    "INVALID_RECORD",
    "NOT_INITIALIZED",
    "UNSAFE_PATH",
    "WORKSPACE_MISMATCH",
    "UNSUPPORTED_SCHEMA",
    "CONFLICT",
    "STORAGE_UNAVAILABLE",
]

MAX_CIPHERTEXT_BYTES = 1_048_576
_ID = re.compile(r"[0-9a-f]{32}\Z")
_FIELDS = frozenset({"format_version", "kind", "record_id", "key_id", "nonce", "ciphertext"})
_MESSAGES: dict[ErrorCode, str] = {
    "INVALID_RECORD": "Invalid sealed record or cursor.",
    "NOT_INITIALIZED": "Sealed store is not initialized; creation must be explicit.",
    "UNSAFE_PATH": "Secure sealed-store opening is unavailable or the path is unsafe.",
    "WORKSPACE_MISMATCH": "Sealed store belongs to a different workspace.",
    "UNSUPPORTED_SCHEMA": "Unrecognized sealed store; preserve it without migration.",
    "CONFLICT": "Sealed record conflicts with an existing commitment.",
    "STORAGE_UNAVAILABLE": "Sealed store unavailable; preserve it and retry after 2 seconds.",
}


class SealedStoreError(Exception):
    """A typed refusal whose message contains no caller data or driver details."""

    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(_MESSAGES[code])


def validate_kind(kind: object) -> RecordKind:
    if type(kind) is not str or kind not in ("memory", "printed-recovery"):
        raise SealedStoreError("INVALID_RECORD")
    return cast(RecordKind, kind)


def validate_id(value: object) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise SealedStoreError("INVALID_RECORD")
    return value


@dataclass(frozen=True, slots=True)
class SealedEnvelope:
    format_version: Literal[1]
    kind: RecordKind
    record_id: str
    key_id: str
    nonce: bytes = field(repr=False)
    ciphertext: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.format_version) is not int or self.format_version != 1:
            raise SealedStoreError("INVALID_RECORD")
        validate_kind(self.kind)
        validate_id(self.record_id)
        validate_id(self.key_id)
        if type(self.nonce) is not bytes or len(self.nonce) != 12:
            raise SealedStoreError("INVALID_RECORD")
        if type(self.ciphertext) is not bytes:
            raise SealedStoreError("INVALID_RECORD")
        size = len(self.ciphertext)
        if not 16 <= size <= MAX_CIPHERTEXT_BYTES:
            raise SealedStoreError("INVALID_RECORD")
        if self.kind == "printed-recovery" and size != 48:
            raise SealedStoreError("INVALID_RECORD")

    def to_wire(self) -> dict[str, object]:
        return {
            "format_version": self.format_version,
            "kind": self.kind,
            "record_id": self.record_id,
            "key_id": self.key_id,
            "nonce": base64.b64encode(self.nonce).decode("ascii"),
            "ciphertext": base64.b64encode(self.ciphertext).decode("ascii"),
        }


@dataclass(frozen=True, slots=True)
class ServerWriter:
    user_id: str
    connection_id: str
    trust: WriterTrust

    def __post_init__(self) -> None:
        if type(self.user_id) is not str or not self.user_id:
            raise SealedStoreError("INVALID_RECORD")
        if type(self.connection_id) is not str:
            raise SealedStoreError("INVALID_RECORD")
        if type(self.trust) is not str or self.trust not in ("trusted", "standard", "untrusted"):
            raise SealedStoreError("INVALID_RECORD")


@dataclass(frozen=True, slots=True)
class StoredSealedRecord:
    workspace_id: str
    sequence: int
    envelope: SealedEnvelope
    writer: ServerWriter
    written_at: str


@dataclass(frozen=True, slots=True)
class PutReceipt:
    record: StoredSealedRecord
    inserted: bool


@dataclass(frozen=True, slots=True)
class SealedPage:
    records: tuple[StoredSealedRecord, ...]
    through: int
    next_after: int | None


def _binary(value: object) -> bytes:
    if type(value) is not str or len(value) > 4 * ((MAX_CIPHERTEXT_BYTES + 2) // 3):
        raise SealedStoreError("INVALID_RECORD")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise SealedStoreError("INVALID_RECORD") from None
    if base64.b64encode(decoded).decode("ascii") != value:
        raise SealedStoreError("INVALID_RECORD")
    return decoded


def parse_envelope(packet: Mapping[str, object]) -> SealedEnvelope:
    if not isinstance(packet, Mapping) or set(packet) != _FIELDS:
        raise SealedStoreError("INVALID_RECORD")
    version = packet["format_version"]
    if type(version) is not int or version != 1:
        raise SealedStoreError("INVALID_RECORD")
    return SealedEnvelope(
        1,
        validate_kind(packet["kind"]),
        validate_id(packet["record_id"]),
        validate_id(packet["key_id"]),
        _binary(packet["nonce"]),
        _binary(packet["ciphertext"]),
    )
