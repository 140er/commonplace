"""Small stale-while-revalidate snapshots for optional shell metadata only."""

import threading
from time import monotonic

from commonplace.bounded_map import BoundedMap
from commonplace.rules.checks import deadline

READ_BUDGET_S = 3.0
_lock = threading.Lock()


class Snapshots:
    def __init__(self, *, clock=monotonic):
        self.clock = clock
        self.values = BoundedMap(256)
        self.pending = set()
        self.retry = BoundedMap(256)
        self.slots = threading.BoundedSemaphore(2)
        self.lock = threading.Lock()

    def read(self, key, load, *, ttl=30.0):
        """Never wait for a scan; coalesce and cap even uncooperative readers."""
        with self.lock:
            now = self.clock()
            cached = self.values.get(key)
            state = "ready" if cached and now < cached[0] else "stale" if cached else "loading"
            if state == "ready" or key in self.pending:
                return (cached[1] if cached else None), state
            if now < self.retry.get(key, 0):
                return (cached[1] if cached else None), "stale" if cached else "unavailable"
            if not self.slots.acquire(blocking=False):
                return (cached[1] if cached else None), state
            self.pending.add(key)

        def refresh():
            try:
                with deadline(READ_BUDGET_S):
                    value = load()
                with self.lock:
                    self.values[key] = (self.clock() + ttl, value)
            except Exception:
                with self.lock:
                    self.retry[key] = self.clock() + ttl
            finally:
                with self.lock:
                    self.pending.discard(key)
                self.slots.release()

        try:
            threading.Thread(target=refresh, daemon=True, name="shell-metadata").start()
        except Exception:
            with self.lock:
                self.pending.discard(key)
                self.retry[key] = self.clock() + ttl
            self.slots.release()
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
