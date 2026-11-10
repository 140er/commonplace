"""Stub for eval slice 1: commands exist and return fixed wrong results."""

from __future__ import annotations

from ..control_db import ControlError

SCHEMA = 1
EVAL_RUN_MAX_BYTES = 16 * 1024 * 1024
EVAL_CHUNK_BYTES = 256 * 1024
EVAL_WORKSPACE_QUOTA = 1024 * 1024 * 1024
EVAL_KEY_TOMBSTONE_RETENTION = 30 * 24 * 60 * 60
EVAL_LOCK_WAIT_S = 5.0


class EvalError(ControlError):
    """Stub."""


def key_digest(workspace_id: str, key: str) -> str:
    return "0" * 64
