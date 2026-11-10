"""Stub."""

import sqlite3
import time

from . import EVAL_KEY_TOMBSTONE_RETENTION, EVAL_LOCK_WAIT_S, EVAL_WORKSPACE_QUOTA  # noqa: F401


def _now():
    return time.time()


def checkpoint(stage):
    """Stub."""


class Capability:
    armed = False


def open_write(data_dir, directory, *, create):
    return sqlite3.connect(":memory:"), Capability()


def purge(conn, capability, run_id, workspace_id):
    return None


def physical_bound(conn, db):
    return 0


def _insert_chunk(conn, run_id, name, idx, data):
    return None
