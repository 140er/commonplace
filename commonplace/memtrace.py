"""Opt-in gateway allocation diagnostics; no request values enter the log."""

from __future__ import annotations

import logging
import os
import threading
import tracemalloc

LOG = logging.getLogger(__name__)
INTERVAL_S = 600.0
TRACE_FRAMES = 5
_SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + os.sep


def process_counts() -> dict:
    """Cheap process counters. Missing platform support is an unknown value."""
    counts = {"thread_count": None, "open_fd_count": None}
    try:
        counts["thread_count"] = threading.active_count()
    except Exception:
        pass
    for field, path in (("thread_count", "/proc/self/task"), ("open_fd_count", "/proc/self/fd")):
        try:
            # Count entries, never read their names or targets into the payload.
            with os.scandir(path) as entries:
                found = sum(1 for _ in entries)
            # Listing /proc/self/fd holds its own descriptor open while it counts.
            counts[field] = max(found - 1, 0) if field == "open_fd_count" else found
        except (OSError, ValueError):
            pass
    return counts


class MemoryTrace:
    """Keep just the preceding snapshot, taken on a background thread."""

    def __init__(self):
        self.stop = threading.Event()
        self.previous = None
        self.owns_trace = False
        self.thread = threading.Thread(target=self.run, name="gateway-memtrace", daemon=True)

    def sample(self) -> None:
        snapshot = tracemalloc.take_snapshot()
        previous, self.previous = self.previous, snapshot
        if previous is None:
            return
        growth = (stat for stat in snapshot.compare_to(previous, "lineno") if stat.size_diff > 0)
        for _, stat in zip(range(10), growth):
            frame = stat.traceback[-1]
            filename = frame.filename
            site = (
                filename[len(_SOURCE_ROOT) :]
                if filename.startswith(_SOURCE_ROOT)
                else os.path.basename(filename)
            )
            LOG.warning(
                "gateway memtrace growth %s:%d size_bytes=%d count=%d",
                site,
                frame.lineno,
                stat.size_diff,
                stat.count_diff,
            )

    def run(self) -> None:
        try:
            if not tracemalloc.is_tracing():
                tracemalloc.start(TRACE_FRAMES)
                self.owns_trace = True
            while not self.stop.is_set():
                try:
                    self.sample()
                except Exception:
                    # Exception messages and tracebacks can contain request data.
                    LOG.warning("gateway memtrace sample unavailable")
                if self.stop.wait(INTERVAL_S):
                    break
        except Exception:
            LOG.warning("gateway memtrace unavailable")
        finally:
            self.previous = None
            if self.owns_trace:
                tracemalloc.stop()

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=5)


def start() -> MemoryTrace | None:
    """Off by default; an operator explicitly sets COMMONPLACE_MEMTRACE=1."""
    if os.environ.get("COMMONPLACE_MEMTRACE") != "1":
        return None
    try:
        trace = MemoryTrace()
        trace.thread.start()
        return trace
    except Exception:
        LOG.warning("gateway memtrace unavailable")
        return None
