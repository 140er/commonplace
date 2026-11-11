"""A worker starts with a bounded number of glibc malloc arenas (#275).

Each request thread of a threaded worker otherwise gets its own arena, and the
pages freed into it stay with the process, so RSS climbs with concurrency.
glibc reads ``MALLOC_ARENA_MAX`` from the environment the process starts with,
so these tests read the real worker's environment from ``/proc``.
"""

import sys
from pathlib import Path

import pytest

from commonplace.workers import SubprocessLauncher, WorkerSupervisor

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="reads /proc/<pid>/environ"
)


def _worker_environ(tmp_path) -> dict:
    sup = WorkerSupervisor(
        str(tmp_path / "data"), launcher=SubprocessLauncher(startup_timeout_s=120)
    )
    worker = sup.get("ws_arenas")
    try:
        raw = Path(f"/proc/{worker.pid}/environ").read_bytes().split(b"\0")
    finally:
        sup.stop_all()
    return dict(e.decode().split("=", 1) for e in raw if e)


def test_worker_caps_malloc_arenas_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("MALLOC_ARENA_MAX", raising=False)
    assert _worker_environ(tmp_path).get("MALLOC_ARENA_MAX") == "2"


def test_operator_malloc_arena_max_passes_through(tmp_path, monkeypatch):
    monkeypatch.setenv("MALLOC_ARENA_MAX", "4")
    assert _worker_environ(tmp_path).get("MALLOC_ARENA_MAX") == "4"
