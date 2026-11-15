"""A full data disk, told apart and refused cleanly.

Leaf module (stdlib only), shared by the memory worker, the daemon and the
gateway. Three jobs:

* **Floor.** Below a free-space floor (default 256 MiB, ``MEMPALACE_MIN_FREE_BYTES``
  overrides, ``0`` turns it off) a new write is refused *before* it starts, with
  one retryable answer, so nothing half-writes. The warning that says so is
  logged at most once a minute.
* **Classification.** ``is_storage_full`` recognises ENOSPC / EDQUOT, SQLite's
  "database or disk is full" and the same text wrapped by a library, so a write
  that hit the wall mid-way is answered the same retryable way as one that was
  refused at the door.
* **Logging that survives it.** ``ResilientStreamHandler`` never lets a failing
  log write reach the caller, and keeps the first line it could not write so the
  first error is not lost behind the "Logging error" noise.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import sqlite3
import threading
from time import monotonic

logger = logging.getLogger(__name__)

MIN_FREE_ENV = "MEMPALACE_MIN_FREE_BYTES"
DEFAULT_MIN_FREE_BYTES = 256 * 1024 * 1024
RETRY_AFTER_S = 30
WARN_INTERVAL_S = 60.0
ERROR_CLASS = "StorageFull"
# JSON-RPC code of the refusal. -32000..-32005 are taken (see mcp_server._guards).
RPC_CODE = -32006
MESSAGE = (
    "Storage is full on this server, so this write was not saved. "
    "Nothing was changed; try again in a little while."
)

_FULL_ERRNOS = frozenset({errno.ENOSPC, errno.EDQUOT})
_SQLITE_FULL = 13
_MAX_ERROR_TEXT = 400
_FULL_TEXTS = (
    "database or disk is full",
    "no space left on device",
    "disk quota exceeded",
    "storage is full on this server",
)

_lock = threading.Lock()
_last_warning = None
_counters = {"writes_refused": 0, "log_write_failures": 0}


class StorageFull(OSError):
    """A write was refused, or could not be completed, because the disk is full.

    Carries ENOSPC so code that already handles that errno keeps working, and
    ``retry_after_s`` for the caller's answer."""

    def __init__(self, message: str = MESSAGE, *, retry_after_s: int = RETRY_AFTER_S):
        super().__init__(errno.ENOSPC, message)
        self.retry_after_s = retry_after_s

    def __str__(self) -> str:
        return str(self.strerror)


def is_storage_full_text(text) -> bool:
    """Whether a short error line names a full disk; long text may be quoted content."""
    return (
        isinstance(text, str)
        and len(text) <= _MAX_ERROR_TEXT
        and any(marker in text.lower() for marker in _FULL_TEXTS)
    )


def is_storage_full(exc) -> bool:
    """True when ``exc`` (or anything it was raised from) says the disk is full."""
    seen = set()
    while exc is not None and id(exc) not in seen and len(seen) < 8:
        seen.add(id(exc))
        if isinstance(exc, OSError) and exc.errno in _FULL_ERRNOS:
            return True
        if isinstance(exc, sqlite3.Error):
            code = getattr(exc, "sqlite_errorcode", None)
            if isinstance(code, int) and code & 0xFF == _SQLITE_FULL:
                return True
            if getattr(exc, "sqlite_errorname", None) == "SQLITE_FULL":
                return True
        # A library wraps the OS error in its own exception type and keeps only the text.
        # An input refusal (ValueError and kin) can quote what the person wrote, so its
        # text never counts.
        if not isinstance(exc, (ValueError, TypeError, KeyError)):
            try:
                if is_storage_full_text(str(exc)):
                    return True
            except Exception:
                pass
        exc = exc.__cause__ or exc.__context__
    return False


def min_free_bytes() -> int:
    """The floor, in bytes; ``0`` means no floor. A value that is not a number is the default."""
    raw = os.environ.get(MIN_FREE_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_MIN_FREE_BYTES
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return DEFAULT_MIN_FREE_BYTES


def free_bytes(path) -> int | None:
    """Bytes a writer may still use on the volume holding ``path``; ``None`` if unknown."""
    probe = os.path.abspath(os.fspath(path)) if path else os.getcwd()
    for _ in range(64):
        try:
            return shutil.disk_usage(probe).free
        except FileNotFoundError:
            parent = os.path.dirname(probe)
            if parent == probe:
                return None
            probe = parent
        except OSError:
            return None
    return None


def status(path) -> dict:
    """What the health views show: free bytes, the floor, and whether the disk is low."""
    free, floor = free_bytes(path), min_free_bytes()
    return {
        "data_free_bytes": free,
        "min_free_bytes": floor,
        "disk_low": bool(floor > 0 and free is not None and free < floor),
        "writes_refused": _counters["writes_refused"],
        "log_write_failures": _counters["log_write_failures"],
    }


def warn_rate_limited(message: str, *args, clock=monotonic) -> bool:
    """Log a WARNING at most once a minute. Returns whether this call logged."""
    global _last_warning
    now = clock()
    with _lock:
        if _last_warning is not None and now - _last_warning < WARN_INTERVAL_S:
            return False
        _last_warning = now
    try:
        logger.warning(message, *args)
    except Exception:
        pass
    return True


def require_free_space(path, *, clock=monotonic) -> None:
    """Raise :class:`StorageFull` when ``path``'s volume is below the floor.

    Unknown free space is not a refusal: a probe that cannot run must not stop
    writes the disk would have taken."""
    floor = min_free_bytes()
    if floor <= 0:
        return
    free = free_bytes(path)
    if free is None or free >= floor:
        return
    note_refused()
    warn_rate_limited(
        "data disk is low: %d MB free, floor %d MB; new writes are refused until space returns",
        free // (1024 * 1024),
        floor // (1024 * 1024),
        clock=clock,
    )
    raise StorageFull()


def note_refused() -> None:
    with _lock:
        _counters["writes_refused"] += 1


def result(**fields) -> dict:
    """A tool result for a write that was not saved because the disk is full."""
    return {
        **fields,
        "success": False,
        "error": MESSAGE,
        "error_class": ERROR_CLASS,
        "retryable": True,
        "retry_after_s": RETRY_AFTER_S,
    }


def rpc_error(req_id, tool_name=None) -> dict:
    """The JSON-RPC answer for a write refused or cut short by a full disk."""
    data = {
        "error_class": ERROR_CLASS,
        "retryable": True,
        "retry_after_s": RETRY_AFTER_S,
        "reason": "storage_full",
    }
    if tool_name:
        data["tool"] = tool_name
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": RPC_CODE, "message": MESSAGE, "data": data},
    }


class ResilientStreamHandler(logging.StreamHandler):
    """A stream handler whose failing writes never reach the caller.

    The standard handler prints a "Logging error" traceback to the same stream
    that just failed and drops the record. Here the failure is counted, the
    first and the latest lost line are kept, and the next write that works
    first says how many were lost and what the first one was."""

    def __init__(self, stream=None):
        super().__init__(stream)
        self._lost = 0
        self._first_lost = ""
        self._last_lost = ""

    def emit(self, record):
        try:
            line = self.format(record)
        except RecursionError:
            raise
        except Exception:
            self._note_lost(f"unformattable record from {record.name}")
            return
        try:
            if self._lost:
                summary = (
                    f"[log] {self._lost} log line(s) were lost while the disk was full. "
                    f"First lost: {self._first_lost} | Latest lost: {self._last_lost}"
                )
                self.stream.write(summary + self.terminator)
                self._lost = 0
            self.stream.write(line + self.terminator)
            self.flush()
        except RecursionError:
            raise
        except Exception:
            self._note_lost(line)

    def _note_lost(self, line: str) -> None:
        with _lock:
            _counters["log_write_failures"] += 1
        if not self._lost:
            self._first_lost = line[:500]
        self._last_lost = line[:500]
        self._lost += 1
