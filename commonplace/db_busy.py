"""Lock contention on the live SQLite files: what counts as busy, and a bounded retry.

A busy error is not a crash: the caller can try again. ``begin_immediate`` retries only
the lock acquisition (nothing has been written yet), so a retry can never apply a
write twice.
"""

from __future__ import annotations

import sqlite3
import time

# Seconds a retryable error tells the client to wait.
RETRY_AFTER_S = 2
BUSY_TIMEOUT_S = 10
BEGIN_ATTEMPTS = 3
BEGIN_BACKOFF_S = (0.05, 0.2)
# An attempt that already waited this long used the busy handler; waiting again will not help.
FAST_FAIL_S = 1.0

_BUSY_TEXTS = frozenset({"database is locked", "database table is locked"})
_BUSY_NAMES = ("SQLITE_BUSY", "SQLITE_LOCKED")
_BUSY_CODES = (5, 6)  # SQLITE_BUSY, SQLITE_LOCKED (extended codes keep these low bits)


def is_busy(exc) -> bool:
    """True for a SQLite "database is locked" / busy error (sqlite3 or sqlcipher3)."""
    if not (isinstance(exc, sqlite3.Error) or type(exc).__module__.startswith("sqlcipher3")):
        return False
    name = getattr(exc, "sqlite_errorname", None)
    if isinstance(name, str) and name.startswith(_BUSY_NAMES):
        return True
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int) and code & 0xFF in _BUSY_CODES:
        return True
    return len(exc.args) == 1 and exc.args[0] in _BUSY_TEXTS


def begin_immediate(conn) -> None:
    """``BEGIN IMMEDIATE`` with a short bounded retry on an instant busy refusal."""
    for attempt in range(BEGIN_ATTEMPTS):
        started = time.monotonic()
        try:
            conn.execute("BEGIN IMMEDIATE")
            return
        except Exception as exc:
            last = attempt == BEGIN_ATTEMPTS - 1
            if last or not is_busy(exc) or time.monotonic() - started >= FAST_FAIL_S:
                raise
            time.sleep(BEGIN_BACKOFF_S[min(attempt, len(BEGIN_BACKOFF_S) - 1)])
