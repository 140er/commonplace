"""Small stale-while-revalidate snapshots for optional shell metadata only."""

import threading
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from time import monotonic

from commonplace.bounded_map import BoundedMap
from commonplace.rules.checks import deadline

READ_BUDGET_S = 3.0
_lock = threading.Lock()


class Snapshots:
    def __init__(self, *, clock=monotonic):
        self.clock = clock
        self.values = BoundedMap(256)
        self.pending = {}
        self.retry = BoundedMap(256)
        # Loaders own their transport timeouts. An uncooperative loader holds a
        # slot until it exits; exhaustion degrades display hints to stale/loading.
        self.slots = threading.BoundedSemaphore(2)
        self.lock = threading.Lock()

    def read(self, key, load, *, ttl=30.0, wait_s=0.0, retry_s=None):
        """Return stale data immediately; optionally join a bounded cold read."""
        start = False
        with self.lock:
            now = self.clock()
            cached = self.values.get(key)
            state = "ready" if cached and now < cached[0] else "stale" if cached else "loading"
            if state == "ready":
                return (cached[1] if cached else None), state
            result = self.pending.get(key)
            if result is None:
                if now < self.retry.get(key, 0):
                    return (cached[1] if cached else None), "stale" if cached else "unavailable"
                if not self.slots.acquire(blocking=False):
                    return (cached[1] if cached else None), state
                result = Future()
                self.pending[key] = result
                start = True

        def refresh():
            error = None
            try:
                with deadline(READ_BUDGET_S):
                    value = load()
                with self.lock:
                    self.values[key] = (self.clock() + ttl, value)
            except Exception as exc:
                # Publish only after removing the in-flight entry, so a new
                # request can retry an expired read rather than join its error.
                error = exc
                with self.lock:
                    self.retry[key] = self.clock() + (ttl if retry_s is None else retry_s)
            finally:
                with self.lock:
                    self.pending.pop(key, None)
                self.slots.release()
            if error is None:
                result.set_result(value)
            else:
                result.set_exception(error)

        if start:
            try:
                threading.Thread(target=refresh, daemon=True, name="shell-metadata").start()
            except Exception as exc:
                with self.lock:
                    self.pending.pop(key, None)
                    self.retry[key] = self.clock() + (ttl if retry_s is None else retry_s)
                self.slots.release()
                result.set_exception(exc)
        if cached is None and wait_s > 0:
            try:
                return result.result(timeout=wait_s), "ready"
            except (FutureTimeoutError, TimeoutError):
                return None, "loading"
            except Exception:
                return None, "unavailable"
        return (cached[1] if cached else None), state


def snapshots(gateway):
    with _lock:
        cache = getattr(gateway, "_shell_snapshots", None)
        if cache is None:
            cache = gateway._shell_snapshots = Snapshots()
    return cache


def viewer_key(principal):
    return (
        principal.workspace_id,
        principal.user_id,
        principal.connection_id,
        principal.role,
        repr(principal.policy),
    )
