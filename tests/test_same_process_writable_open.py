"""Deterministic concurrent opens and overlapping process-wide writer leases."""

import subprocess
import sys
import threading

import pytest

import mempalace.palace as palace_module
from mempalace.backends.compact import CompactBackend
from mempalace.backends.sqlite_exact import SQLiteExactBackend

_WAIT_SECONDS = 30


def _wait(event):
    if not event.wait(_WAIT_SECONDS):
        raise TimeoutError("writer coordination timed out")


def _join(threads):
    for thread in threads:
        thread.join(_WAIT_SECONDS)
    assert not any(thread.is_alive() for thread in threads), "writer thread did not finish"


@pytest.fixture(params=[SQLiteExactBackend, CompactBackend], ids=["sqlite_exact", "compact"])
def writers(request, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    path = str(tmp_path / "palace")
    seed = request.param()
    seed.get_collection(path, "drawers", create=True)
    seed.close()
    backends = [request.param(), request.param()]
    yield path, backends
    for backend in backends:
        backend.close()


def _probe_writer(path, backend):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from mempalace.backends import get_backend
from mempalace.palace import MineAlreadyRunning
backend = get_backend(sys.argv[2])
try:
    backend.get_collection(sys.argv[1], "drawers", create=False)
except MineAlreadyRunning:
    print("busy")
else:
    print("ok")
finally:
    backend.close()
""",
            path,
            backend.name,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=_WAIT_SECONDS,
    )
    return result.stdout.strip()


def test_concurrent_writable_open_waits_for_lease_registration(writers, monkeypatch):
    path, backends = writers
    acquired, resume, contender_waiting = (threading.Event() for _ in range(3))
    real_guard = palace_module._palace_lock_guard
    real_record = palace_module._write_lock_holder
    errors, opened = [], []

    class ObservedGuard:
        def __enter__(self):
            if not real_guard.acquire(blocking=False):
                contender_waiting.set()
                real_guard.acquire()

        def __exit__(self, *exc):
            real_guard.release()

    def pause_registration(lock_file):
        if threading.current_thread() is threads[0]:
            acquired.set()
            _wait(resume)
        real_record(lock_file)

    def open_collection(backend):
        try:
            opened.append(backend.get_collection(path, "drawers", create=False))
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(palace_module, "_palace_lock_guard", ObservedGuard())
    monkeypatch.setattr(palace_module, "_write_lock_holder", pause_registration)
    threads = [threading.Thread(target=open_collection, args=(backend,)) for backend in backends]
    try:
        threads[0].start()
        _wait(acquired)
        threads[1].start()
        _wait(contender_waiting)
    finally:
        resume.set()
        _join([thread for thread in threads if thread.ident is not None])
    assert errors == []
    assert len(opened) == 2
    assert all(collection.count() == 0 for collection in opened)


def test_closing_original_opener_keeps_other_threads_writer_lease(writers, monkeypatch):
    path, backends = writers
    registered, finish_open, other_registered, finish_other_open = (
        threading.Event() for _ in range(4)
    )
    real_init = backends[0]._init_schema
    other_init = backends[1]._init_schema
    errors = []

    def pause_open(conn):
        real_init(conn)
        registered.set()
        _wait(finish_open)

    def original_opener():
        try:
            backends[0].get_collection(path, "drawers", create=False)
        except BaseException as exc:
            errors.append(exc)

    def pause_other_open(conn):
        other_init(conn)
        other_registered.set()
        _wait(finish_other_open)

    def overlapping_writer():
        try:
            col = backends[1].get_collection(path, "drawers", create=False)
            with col._cursor(write=True) as cur:
                cur.execute("INSERT INTO meta(key,value) VALUES ('overlap_test','committed')")
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(backends[0], "_init_schema", pause_open)
    monkeypatch.setattr(backends[1], "_init_schema", pause_other_open)
    threads = [
        threading.Thread(target=original_opener),
        threading.Thread(target=overlapping_writer),
    ]
    try:
        threads[0].start()
        _wait(registered)
        threads[1].start()
        _wait(other_registered)
        finish_open.set()
        _join(threads[:1])
        assert errors == []
        backends[0].close()
        assert _probe_writer(path, backends[1]) == "busy"
        finish_other_open.set()
        _join(threads[1:])
        assert errors == []
        assert (
            backends[1]
            ._clients[path]
            .conn.execute("SELECT value FROM meta WHERE key='overlap_test'")
            .fetchone()[0]
            == "committed"
        )
        backends[1].close()
        assert _probe_writer(path, backends[1]) == "ok"
    finally:
        finish_open.set()
        finish_other_open.set()
        _join([thread for thread in threads if thread.ident is not None])


def test_shared_handle_serializes_writers(writers):
    path, backends = writers
    first = backends[0].get_collection(path, "drawers", create=False)
    second = backends[0].get_collection(path, "drawers", create=False)
    writing, attempted, release = (threading.Event() for _ in range(3))
    errors, order = [], []
    real_lock = first._handle.lock

    class ObservedHandleLock:
        def __enter__(self):
            if not real_lock.acquire(blocking=False):
                attempted.set()
                real_lock.acquire()

        def __exit__(self, *exc):
            real_lock.release()

    def write(col, position):
        try:
            with col._cursor(write=True) as cur:
                order.append(position)
                cur.execute("INSERT INTO meta(key,value) VALUES (?, 'written')", (f"t{position}",))
                if position == 1:
                    writing.set()
                    _wait(release)
        except BaseException as exc:
            errors.append(exc)

    first._handle.lock = ObservedHandleLock()
    threads = [
        threading.Thread(target=write, args=(col, i)) for i, col in enumerate([first, second], 1)
    ]
    try:
        threads[0].start()
        _wait(writing)
        threads[1].start()
        _wait(attempted)
        assert order == [1]
    finally:
        release.set()
        _join([thread for thread in threads if thread.ident is not None])
    assert errors == []
    assert order == [1, 2]
