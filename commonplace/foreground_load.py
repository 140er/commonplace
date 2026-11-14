"""Keep user requests ahead of background index writes.

The gateway wraps every worker call from a person (search, reads) in
``request()``. A background index write (``background_call``) waits, bounded,
until no such request is running and none ended a moment ago, and rests for a
share of its own run time afterwards, so the worker is never saturated by the
backfill. Nothing here blocks a person: ``request()`` never waits.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import threading
import time

# Longest a background write waits for people to go quiet before it goes ahead
# anyway, so a busy workspace still makes slow progress.
QUIET_WAIT_MAX = 5.0
# Quiet time after the last request ends, so a burst of searches is not
# interleaved with writes.
QUIET_SETTLE = 0.25
REST_CAP = 5.0
_SLICE = 0.05


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return min(max(value, low), high)


# Rest after each background write, as a multiple of how long the write took:
# 2 keeps the index at roughly a third of the worker's time. 0 turns it off.
REST_RATIO = _env_float("COMMONPLACE_SEARCH_INDEX_REST_RATIO", 2.0, 0.0, 20.0)

# Set while the index job itself talks to the worker; those calls are not people.
_background = contextvars.ContextVar("foreground_load_background", default=False)

_cond = threading.Condition()
_inflight = 0
_last_end = 0.0


@contextlib.contextmanager
def request():
    """Mark one person's worker call as running; never waits."""
    global _inflight, _last_end
    if _background.get():
        yield
        return
    with _cond:
        _inflight += 1
    try:
        yield
    finally:
        with _cond:
            _inflight -= 1
            _last_end = time.monotonic()
            _cond.notify_all()


@contextlib.contextmanager
def background():
    """Calls made inside are the backfill's own and do not count as people."""
    token = _background.set(True)
    try:
        yield
    finally:
        _background.reset(token)


def busy() -> bool:
    with _cond:
        return _inflight > 0 or time.monotonic() - _last_end < QUIET_SETTLE


def wait_quiet(limit: float | None = None) -> bool:
    """Wait until no request runs and none just ended; False when the wait ran out."""
    deadline = time.monotonic() + (QUIET_WAIT_MAX if limit is None else limit)
    with _cond:
        while True:
            now = time.monotonic()
            if _inflight == 0 and now - _last_end >= QUIET_SETTLE:
                return True
            if now >= deadline:
                return False
            wake = deadline - now
            if _inflight == 0:
                wake = min(wake, QUIET_SETTLE - (now - _last_end))
            _cond.wait(max(wake, 0.001))


def rest(seconds: float, closed=None) -> None:
    """Sleep in short slices so a closing service is not held up."""
    end = time.monotonic() + max(0.0, seconds)
    while True:
        left = end - time.monotonic()
        if left <= 0 or (closed is not None and closed()):
            return
        time.sleep(min(left, _SLICE))


def background_call(fn, *, closed=None):
    """Run one background write after people go quiet, then rest in proportion."""
    wait_quiet()
    started = time.monotonic()
    try:
        return fn()
    finally:
        rest(min(REST_CAP, REST_RATIO * (time.monotonic() - started)), closed)
