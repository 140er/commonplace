"""Trusted internal opaque storage. Importing this package activates nothing."""

from .records import (
    ErrorCode,
    PutReceipt,
    RecordKind,
    SealedEnvelope,
    SealedPage,
    SealedStoreError,
    ServerWriter,
    StoredSealedRecord,
    parse_envelope,
)
from .store import SealedStore, open_sealed_store

__all__ = [
    "ErrorCode",
    "PutReceipt",
    "RecordKind",
    "SealedEnvelope",
    "SealedPage",
    "SealedStore",
    "SealedStoreError",
    "ServerWriter",
    "StoredSealedRecord",
    "open_sealed_store",
    "parse_envelope",
]
