"""Stub."""

import sqlite3
from contextlib import contextmanager


def row_bytes(message):
    return b"[]"


@contextmanager
def control_snapshot(data_dir):
    conn = sqlite3.connect(":memory:")
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def thread_lock(directory):
    yield
