"""Process and container memory readings, and a worker's RSS high-water recycle.

The readings are read-only, cheap and never raise: an unreadable file is ``None``.
The recycle asks a worker to drain through its generation coordinator, the same way an
idle or shutting-down worker retires, so every accepted call still finishes and the
parent starts a replacement on the next call. Nothing here restarts the container.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# The anonymous-memory ceiling, in MiB, above which a worker asks to be replaced. Unset,
# empty or ``0`` turns the recycle off (the default): a worker's legitimate size follows
# its palace, so no fixed number is safe to ship before the operator has measured one.
RECYCLE_ENV = "MEMPALACE_WORKER_RSS_RECYCLE_MB"
# A value below this is a unit mistake (GiB typed as MiB), not a ceiling: it is ignored.
MIN_RECYCLE_MB = 512
# A worker is never recycled younger than this, nor before it has admitted two calls
# (the restart circuit counts a recycle after one admitted call as a failed start).
MIN_UPTIME_S = 600.0
MIN_ADMITTED = 2
SAMPLE_INTERVAL_S = 30.0
# Over the ceiling a worker waits for a moment with no call in flight, so no request waits
# on the drain; this far over it drains with calls in flight (they still finish).
HARD_FACTOR = 1.25

_FIELDS = {"VmRSS:": "rss_bytes", "RssAnon:": "anon_bytes", "RssFile:": "file_bytes"}
_CGROUP_ROOT = "/sys/fs/cgroup"


def read_process_memory(pid="self") -> Optional[dict]:
    """``{"rss_bytes", "anon_bytes", "file_bytes"}`` of a process from ``/proc``, or ``None``."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        values = {}
        with open(f"/proc/{pid}/status", encoding="ascii", errors="replace") as stream:
            for line in stream:
                parts = line.split()
                name = _FIELDS.get(parts[0]) if parts else None
                if name and len(parts) == 3 and parts[2] == "kB" and parts[1].isdigit():
                    values[name] = int(parts[1]) * 1024
    except (OSError, ValueError):
        return None
    return values if "rss_bytes" in values else None


def _flat_keyed(path: str) -> dict:
    values = {}
    with open(path, encoding="ascii", errors="replace") as stream:
        for line in stream:
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                values[parts[0]] = int(parts[1])
    return values


def _single_int(path: str) -> Optional[int]:
    try:
        with open(path, encoding="ascii") as stream:
            text = stream.read(64).strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def read_cgroup_memory(root: str = _CGROUP_ROOT) -> Optional[dict]:
    """The container's memory split from the cgroup v2 files, or ``None`` when absent.

    ``anon_bytes`` is what an OOM kill is about; ``file_bytes`` is page cache and mapped
    files, which the kernel reclaims first. ``working_set_bytes`` is ``current`` less the
    inactive file cache (what a container monitor alarms on). Unread fields are ``None``.
    """
    if not sys.platform.startswith("linux"):
        return None
    try:
        stat = _flat_keyed(os.path.join(root, "memory.stat"))
    except (OSError, ValueError):
        return None
    current = _single_int(os.path.join(root, "memory.current"))
    limit = _single_int(os.path.join(root, "memory.max"))  # "max" (no limit) reads as None
    try:
        oom_kill = _flat_keyed(os.path.join(root, "memory.events")).get("oom_kill")
    except (OSError, ValueError):
        oom_kill = None
    inactive = stat.get("inactive_file")
    return {
        "current_bytes": current,
        "limit_bytes": limit,
        "anon_bytes": stat.get("anon"),
        "file_bytes": stat.get("file"),
        "shmem_bytes": stat.get("shmem"),
        "inactive_file_bytes": inactive,
        "working_set_bytes": (
            max(0, current - inactive) if current is not None and inactive is not None else None
        ),
        "oom_kill": oom_kill,
    }


def trim_heap() -> None:
    """Return freed heap pages to the OS where glibc can; a no-op elsewhere. Never raises."""
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def recycle_limit_bytes(environ=None) -> int:
    """The recycle ceiling in bytes; ``0`` when it is off, unset or not a usable number."""
    raw = (os.environ if environ is None else environ).get(RECYCLE_ENV, "").strip()
    if not raw or raw == "0":
        return 0
    if not raw.isdigit() or int(raw) < MIN_RECYCLE_MB:
        logger.warning(
            "%s=%r is not a whole number of MiB of at least %d; the RSS recycle stays off",
            RECYCLE_ENV,
            raw,
            MIN_RECYCLE_MB,
        )
        return 0
    return int(raw) * 1024 * 1024


class RssWatchdog:
    """Asks a worker's coordinator to drain once its anonymous memory passes the ceiling."""

    def __init__(
        self,
        coordinator,
        limit_bytes: int,
        *,
        read: Callable[[], Optional[dict]] = read_process_memory,
        clock: Callable[[], float] = time.monotonic,
        min_uptime_s: float = MIN_UPTIME_S,
        interval_s: float = SAMPLE_INTERVAL_S,
    ):
        self.coordinator = coordinator
        self.limit_bytes = limit_bytes
        self.read = read
        self.clock = clock
        self.min_uptime_s = min_uptime_s
        self.interval_s = interval_s
        self.started = clock()
        self.stop = threading.Event()

    def check(self) -> bool:
        """Take one sample; True when it asked for the recycle."""
        if self.clock() - self.started < self.min_uptime_s:
            return False
        sample = self.read()
        if not sample:
            return False
        # File-backed pages are the kernel's to reclaim: only anonymous memory counts, and
        # a kernel that does not split them falls back to the whole RSS.
        size = sample.get("anon_bytes") or sample.get("rss_bytes") or 0
        if size < self.limit_bytes:
            return False
        with self.coordinator.lock:
            admitted, active = self.coordinator.admitted, self.coordinator.active
        if admitted < MIN_ADMITTED or (active and size < self.limit_bytes * HARD_FACTOR):
            return False
        logger.warning(
            "Worker memory %d MiB passed the recycle ceiling of %d MiB; draining for a replacement",
            size >> 20,
            self.limit_bytes >> 20,
        )
        self.coordinator.drain("rss", rss_source="proc")
        return True

    def run(self) -> None:
        while not self.stop.wait(self.interval_s):
            try:
                if self.check():
                    return
            except Exception:  # a watchdog that dies would only hide the growth
                logger.debug("RSS recycle sample failed", exc_info=True)


def start_rss_recycle(coordinator, environ=None) -> Optional[threading.Thread]:
    """Start the watchdog for a gateway-launched worker, if the ceiling is set."""
    limit = recycle_limit_bytes(environ)
    if limit <= 0 or coordinator is None:
        return None
    thread = threading.Thread(
        target=RssWatchdog(coordinator, limit).run, name="mcp-rss-recycle", daemon=True
    )
    thread.start()
    return thread
