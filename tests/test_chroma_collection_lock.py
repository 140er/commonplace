"""Tests for ChromaCollection's palace-write-lock integration.

Closes the gap left by ``mine_palace_lock`` only protecting the
``mempalace mine`` pipeline: MCP/direct writers that call
``ChromaCollection.add/upsert/update/delete`` must also serialize against
mine and against each other to avoid the multi-threaded HNSW corruption
documented in #974/#965.

Property tested:

* ``ChromaCollection(c, palace_path=p)`` wraps every write with
  ``mine_palace_lock(p)``.
* Writes raise ``MineAlreadyRunning`` when another holder owns the lock
  (instead of silently racing into the underlying chromadb call).
* Re-entrant composition with ``miner.mine()`` does not self-deadlock:
  ``with mine_palace_lock(p): col.upsert(...)`` runs to completion.
* ``ChromaCollection(c)`` (no palace_path) preserves legacy no-lock
  behaviour for tests/callers that build the adapter directly without
  going through ``ChromaBackend``.

POSIX-only: ``mine_palace_lock`` uses ``fcntl`` on Unix and ``msvcrt`` on
Windows; the contention semantics differ enough that the cross-process
tests are skipped on Windows runners.
"""

from __future__ import annotations

import multiprocessing
import os
from contextlib import contextmanager

import pytest

from mempalace.backends.chroma import ChromaCollection
from mempalace.palace import MineAlreadyRunning, mine_palace_lock


def _get_mp_context():
    """Same start-method picker as test_palace_locks.py — ``spawn`` everywhere.

    ``fork`` deadlocks under Python 3.13 when the parent is multi-threaded
    (pytest + chromadb + onnxruntime), and macOS forbids fork-without-exec via
    CoreFoundation. ``spawn`` is slower (re-imports) but safe.
    """
    return multiprocessing.get_context("spawn")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeChromaCollection:
    """Records calls; never blocks. Stand-in for chromadb.Collection."""

    def __init__(self):
        self.adds: list[dict] = []
        self.upserts: list[dict] = []
        self.updates: list[dict] = []
        self.deletes: list[dict] = []

    def add(self, **kwargs):
        self.adds.append(kwargs)

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)

    def update(self, **kwargs):
        self.updates.append(kwargs)

    def delete(self, **kwargs):
        self.deletes.append(kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_WAIT_SECONDS = 30


def _hold_lock(palace_path, ready, release):
    """Keep the lock until the parent finishes the operation under test."""
    with mine_palace_lock(palace_path):
        ready.set()
        if not release.wait(_WAIT_SECONDS):
            raise TimeoutError("parent did not release the lock holder")


def _join_children(*children):
    """Reap every child, including when synchronization or an assertion fails."""
    for child in children:
        child.join(timeout=_WAIT_SECONDS)
        if child.is_alive():
            child.terminate()
            child.join(timeout=_WAIT_SECONDS)
    failures = [f"{child.name}: exit {child.exitcode}" for child in children if child.exitcode != 0]
    if failures:
        pytest.fail("Children failed or timed out: " + "; ".join(failures))


@contextmanager
def _held_lock(palace_path):
    ctx = _get_mp_context()
    ready, release = ctx.Event(), ctx.Event()
    holder = ctx.Process(target=_hold_lock, args=(palace_path, ready, release))
    holder.start()
    try:
        assert ready.wait(_WAIT_SECONDS), "holder failed to acquire lock"
        yield
    finally:
        release.set()
        _join_children(holder)


# ---------------------------------------------------------------------------
# Tests — opt-in lock wiring
# ---------------------------------------------------------------------------


def test_palace_path_none_skips_lock(tmp_path, monkeypatch):
    """Legacy callers (``ChromaCollection(c)``) keep no-lock behaviour.

    A ``ChromaCollection`` built without ``palace_path`` must not touch the
    lock infrastructure at all. This guards against regressions where a
    test or third-party caller relies on the historical bare-write path.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    fake = _FakeChromaCollection()
    col = ChromaCollection(fake)  # no palace_path -> no lock

    # Hold the lock in a child process. Without palace_path, the parent
    # write must still succeed (the lock does not gate this caller).
    palace = str(tmp_path / "palace")
    with _held_lock(palace):
        col.upsert(documents=["doc"], ids=["id-1"])
        assert fake.upserts == [{"documents": ["doc"], "ids": ["id-1"]}]


def test_writer_blocks_during_mine(tmp_path, monkeypatch):
    """A held ``mine_palace_lock`` causes ``ChromaCollection`` writes to raise.

    This is the property that closes the MCP-bypass gap: when a mine is in
    flight, MCP/direct writes raise ``MineAlreadyRunning`` rather than
    silently entering chromadb's write path concurrent with mine.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    palace = str(tmp_path / "palace")
    with _held_lock(palace):
        fake = _FakeChromaCollection()
        col = ChromaCollection(fake, palace_path=palace)

        with pytest.raises(MineAlreadyRunning):
            col.upsert(documents=["doc"], ids=["id-1"])
        with pytest.raises(MineAlreadyRunning):
            col.add(documents=["doc"], ids=["id-2"])
        with pytest.raises(MineAlreadyRunning):
            col.update(ids=["id-3"], documents=["doc"])
        with pytest.raises(MineAlreadyRunning):
            col.delete(ids=["id-4"])

        # The fake must have received NO calls — the lock must gate
        # before reaching the underlying chromadb layer.
        assert fake.upserts == []
        assert fake.adds == []
        assert fake.updates == []
        assert fake.deletes == []


def test_reentrant_inside_mine_passes_through(tmp_path, monkeypatch):
    """``ChromaCollection.upsert`` inside ``mine_palace_lock`` does not deadlock.

    ``miner.mine()`` already holds ``mine_palace_lock(palace_path)`` for the
    full mine pipeline; ``_mine_body`` then calls
    ``collection.upsert(...)``. With the per-thread re-entrant guard in
    ``mine_palace_lock``, the inner acquire is a pass-through and the
    underlying chromadb call runs immediately.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    palace = str(tmp_path / "palace")
    fake = _FakeChromaCollection()
    col = ChromaCollection(fake, palace_path=palace)

    with mine_palace_lock(palace):
        # If the re-entrant guard were missing, this would self-deadlock on
        # the underlying flock. We rely on pytest-timeout (configured in
        # pyproject.toml) to enforce this in CI; the assertion just confirms
        # the call landed.
        col.upsert(documents=["d"], ids=["i"], metadatas=[{"k": "v"}])
        col.add(documents=["d2"], ids=["i2"])
        col.update(ids=["i"], documents=["d-updated"])
        col.delete(ids=["i2"])

    assert len(fake.upserts) == 1
    assert len(fake.adds) == 1
    assert len(fake.updates) == 1
    assert len(fake.deletes) == 1


class _HeldFakeChromaCollection(_FakeChromaCollection):
    """Signal that upsert owns the lock, then wait for the parent's release."""

    def __init__(self, acquired, release):
        super().__init__()
        self._acquired = acquired
        self._release = release

    def upsert(self, **kwargs):
        self._acquired.set()
        if not self._release.wait(_WAIT_SECONDS):
            raise TimeoutError("parent did not release the first writer")
        super().upsert(**kwargs)


def _writer_target(
    palace_path, tmp_path_str, pid, ready, start, acquired, release, retry, finished, result_q
):
    """Finish imports before readiness, then attempt writes on command."""
    os.environ["HOME"] = tmp_path_str
    # Fresh import inside child so HOME monkeypatch routes the lock dir.
    from mempalace.backends.chroma import ChromaCollection as _CC
    from mempalace.palace import MineAlreadyRunning as _MAR

    fake = _HeldFakeChromaCollection(acquired, release) if pid == 1 else _FakeChromaCollection()
    col = _CC(fake, palace_path=palace_path)
    ready.set()
    if not start.wait(_WAIT_SECONDS):
        raise TimeoutError(f"parent did not start writer {pid}")

    def attempt():
        try:
            col.upsert(documents=[f"d{pid}"], ids=[f"i{pid}"])
            result_q.put(("ok", pid))
        except _MAR:
            result_q.put(("busy", pid))

    attempt()
    if pid == 1:
        # Keep the owner alive so process exit cannot release a leaked lock.
        if not finished.wait(_WAIT_SECONDS):
            raise TimeoutError("parent did not finish the post-release check")
    else:
        if not retry.wait(_WAIT_SECONDS):
            raise TimeoutError("parent did not request the second writer's retry")
        attempt()


def test_concurrent_writers_serialize(tmp_path, monkeypatch):
    """Two processes calling ``ChromaCollection.upsert`` against the same
    palace must be serialized: at most one enters chromadb at a time, the
    other raises ``MineAlreadyRunning``.

    This is the property that prevents the parallel HNSW insert race that
    drives #974/#965 — under concurrent MCP write fan-out, exactly one
    writer reaches chromadb and the rest fail loudly instead of corrupting
    the index.

    Both children finish importing before the parent starts either write.
    The first writer holds its lock until the second reports its attempt.
    The second retries after release while the first process is still alive.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    palace = str(tmp_path / "palace")

    ctx = _get_mp_context()
    result_q = ctx.Queue()
    ready = [ctx.Event(), ctx.Event()]
    start = [ctx.Event(), ctx.Event()]
    acquired, release = ctx.Event(), ctx.Event()
    retry, finished = ctx.Event(), ctx.Event()
    children = [
        ctx.Process(
            target=_writer_target,
            args=(
                palace,
                str(tmp_path),
                i + 1,
                ready[i],
                start[i],
                acquired,
                release,
                retry,
                finished,
                result_q,
            ),
        )
        for i in range(2)
    ]
    for child in children:
        child.start()
    try:
        for i, event in enumerate(ready, 1):
            assert event.wait(_WAIT_SECONDS), f"writer {i} failed to finish importing"
        start[0].set()
        assert acquired.wait(_WAIT_SECONDS), "first writer failed to acquire the lock"
        start[1].set()
        attempt = result_q.get(timeout=_WAIT_SECONDS)
        assert attempt[1] == 2, f"expected the second writer's attempt, got {attempt}"
        release.set()
        outcomes = [attempt, result_q.get(timeout=_WAIT_SECONDS)]
        assert outcomes == [("busy", 2), ("ok", 1)]
        retry.set()
        assert result_q.get(timeout=_WAIT_SECONDS) == ("ok", 2)
    finally:
        release.set()
        retry.set()
        finished.set()
        for event in start:
            event.set()
        try:
            _join_children(*children)
        finally:
            result_q.close()
            result_q.join_thread()


def test_read_path_does_not_acquire_lock(tmp_path, monkeypatch):
    """``query`` / ``get`` / ``count`` must not be gated by the write lock.

    Read traffic is the dominant workload (semantic search, MCP get, etc.)
    and serializing it against mine would tank latency for no correctness
    benefit. This test pins that property: with another process holding
    the write lock, reads must still complete instantly.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    palace = str(tmp_path / "palace")
    with _held_lock(palace):
        # _FakeChromaCollection doesn't implement query/get/count; we only
        # need to confirm the wrapper does not call into mine_palace_lock
        # for reads, which we assert by observing the wrapped methods are
        # NOT in ChromaCollection's _write_lock path. A direct check via
        # source inspection is more honest than mocking the entire chroma
        # surface here.
        import inspect

        from mempalace.backends.chroma import ChromaCollection as _CC

        for write_attr in ("add", "upsert", "update", "delete"):
            src = inspect.getsource(getattr(_CC, write_attr))
            assert "_write_lock" in src, f"{write_attr} should acquire write lock"

        for read_attr in ("query", "get", "count"):
            method = getattr(_CC, read_attr, None)
            if method is None:
                continue
            src = inspect.getsource(method)
            assert "_write_lock" not in src, (
                f"{read_attr} must NOT acquire the write lock (read path)"
            )
