"""Optional room hints: bounded reads, never part of opening a note composer."""

import threading
from concurrent.futures import Future
from time import monotonic

from mempalace.deadline import deadline

from .bounded_map import BoundedMap

ROOM_BUDGET_S = 0.75
ROOM_CACHE_TTL_S = 30.0
_cache_lock = threading.Lock()


class _RoomCache:
    def __init__(self):
        self.values = BoundedMap(256)
        self.pending = {}
        self.slots = threading.BoundedSemaphore(2)
        self.lock = threading.Lock()

    def rooms(self, ctx, wing):
        principal = ctx.principal
        # Room visibility can differ between connections in the same workspace.
        key = (
            principal.workspace_id,
            principal.user_id,
            principal.connection_id,
            principal.role,
            repr(principal.policy),
            wing,
        )
        with self.lock:
            cached = self.values.get(key)
            if cached is not None and monotonic() < cached[0]:
                return list(cached[1])
            # Coalesce repeated selection while a reader is stuck. A small pool
            # also bounds resources if a transport ignores the logical deadline.
            if key in self.pending or not self.slots.acquire(blocking=False):
                return ["general"]
            result = Future()
            self.pending[key] = result

        def read():
            try:
                with deadline(ROOM_BUDGET_S):
                    page = ctx.memory("mempalace_list_rooms", {"wing": wing})
                    rooms = page.get("rooms", {})
                    if not isinstance(rooms, dict):
                        raise ValueError("invalid room hints")
                    values = [
                        "general",
                        *sorted(r for r in rooms if isinstance(r, str) and r != "general"),
                    ]
                result.set_result(values)
            except Exception as exc:
                result.set_exception(exc)
            finally:
                with self.lock:
                    self.pending.pop(key, None)
                self.slots.release()

        try:
            threading.Thread(target=read, daemon=True, name="memory-room-hints").start()
        except Exception:
            with self.lock:
                self.pending.pop(key, None)
            self.slots.release()
            return ["general"]
        try:
            values = result.result(timeout=ROOM_BUDGET_S)
        except Exception:
            # Failure and late results are never cached. The form stays usable.
            return ["general"]
        self.values[key] = (monotonic() + ROOM_CACHE_TTL_S, tuple(values))
        return values


def room_choices(ctx, wing):
    """Called only after the route has checked this destination's write access."""
    with _cache_lock:
        cache = getattr(ctx.gateway, "_note_room_cache", None)
        if cache is None:
            cache = ctx.gateway._note_room_cache = _RoomCache()
    return cache.rooms(ctx, wing)
