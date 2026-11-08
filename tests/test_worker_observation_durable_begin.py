"""Begin delivery precedes work when available, without holding memory calls hostage."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest


@pytest.mark.skipif(os.name == "nt", reason="SIGKILL witness is Unix-only")
@pytest.mark.parametrize("phase", [None, "startup_preflight"])
def test_begin_survives_immediate_kill_with_background_writer_held(tmp_path, phase):
    # Hold the background writer until the producer explicitly waits for delivery.
    # This removes the base's lucky scheduling window. No fake sink or flush at exit.
    script = r"""
import json, sys, threading
from mempalace.worker_observation import Observer
permit, ready = threading.Event(), threading.Event()
original_drain = Observer._drain
def held_drain(self):
    ready.set()
    permit.wait()
    original_drain(self)
Observer._drain = held_drain
observer = Observer(sys.argv[1])
ready.wait()
original_wait = threading.Event.wait
def producer_wait(self, timeout=None):
    if threading.current_thread() is threading.main_thread():
        permit.set()
    return original_wait(self, timeout)
threading.Event.wait = producer_wait
phase = json.loads(sys.argv[2])
with observer.scope("mempalace_status" if phase is None else None, "http", phase) as token:
    print(json.dumps([observer.generation, token.call_id]), flush=True)
    original_wait(threading.Event())
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path), json.dumps(phase)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # A pipe readiness deadline avoids hanging if the child setup breaks.
        import selectors

        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            assert selector.select(10), "child did not enter observed work"
        marker = child.stdout.readline()
        child.send_signal(signal.SIGKILL)
        _, stderr = child.communicate(timeout=10)
        assert marker, stderr
        generation, call_id = json.loads(marker)
        assert child.returncode == -signal.SIGKILL
        path = tmp_path / "diagnostics" / "worker-memory.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        assert [r["event"] for r in rows] == [
            "generation_start",
            "phase_begin" if phase else "call_begin",
        ], "generation and exact begin must be on disk before work starts"
        assert all(r["worker_generation"] == generation for r in rows)
        assert rows[1]["call_id"] == call_id
        assert rows[1]["startup_phase"] == phase
        assert [r["record_seq"] for r in rows] == [1, 2]
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)


def test_default_http_startup_phases_on_disk(tmp_path, monkeypatch):
    from mempalace import mcp_server as server
    from mempalace.worker_observation import Observer

    monkeypatch.delenv("MEMPALACE_WORKER_MEMORY_OBSERVE", raising=False)
    monkeypatch.delenv("MEMPALACE_EAGER_WARMUP", raising=False)
    monkeypatch.setattr(server, "_worker_observer", None)
    monkeypatch.setattr(server, "_worker_observer_failed", False)
    monkeypatch.setattr(server._config, "_config_dir", tmp_path)
    monkeypatch.setattr(server, "_worker_observer_factory", Observer)
    monkeypatch.setattr(server, "_READ_ONLY", True)
    monkeypatch.setattr(server, "_args", SimpleNamespace(host="127.0.0.1", port=0))
    monkeypatch.setattr(server, "_start_idle_exit_watchdog", lambda: None)
    monkeypatch.setattr(server, "_start_write_stall_watchdog", lambda: None)
    monkeypatch.setattr(server, "_serve_http", lambda *args: None)

    monkeypatch.setattr(server, "_refresh_vector_disabled_flag", lambda: None)
    try:
        server._run_http_loop()
        observer = server._worker_observer
        if observer is not None:
            observer.close()
        path = tmp_path / "diagnostics" / "worker-memory.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        assert [(r["event"], r["startup_phase"]) for r in rows if r["startup_phase"]] == [
            ("phase_begin", "startup_preflight"),
            ("phase_end", "startup_preflight"),
        ], "default HTTP startup must activate observation before preflight"
    finally:
        if server._worker_observer is not None:
            server._worker_observer.close()


@pytest.mark.parametrize("phase", ["capacity_probe", "eager_warmup"])
def test_phase_activates_before_work(tmp_path, monkeypatch, phase):
    from mempalace import mcp_server as server
    from mempalace.worker_observation import Observer

    monkeypatch.delenv("MEMPALACE_WORKER_MEMORY_OBSERVE", raising=False)
    monkeypatch.setattr(server, "_worker_observer", None)
    monkeypatch.setattr(server, "_worker_observer_failed", False)
    monkeypatch.setattr(server._config, "_config_dir", tmp_path)
    monkeypatch.setattr(server, "_worker_observer_factory", Observer)
    monkeypatch.setattr(server, "_READ_ONLY", True)
    monkeypatch.setattr(server, "_args", SimpleNamespace(host="127.0.0.1", port=0))
    monkeypatch.setattr(server, "_start_idle_exit_watchdog", lambda: None)
    monkeypatch.setattr(server, "_start_write_stall_watchdog", lambda: None)
    monkeypatch.setattr(server, "_refresh_vector_disabled_flag", lambda: None)
    monkeypatch.delenv("MEMPALACE_EAGER_WARMUP", raising=False)

    def serve(*args):
        with server._worker_phase(phase):
            path = tmp_path / "diagnostics" / "worker-memory.jsonl"
            rows = (
                [json.loads(line) for line in path.read_text().splitlines()]
                if path.exists()
                else []
            )
            assert rows, "HTTP startup must activate the observer"
            assert rows[-1]["event"] == "phase_begin"
            assert rows[-1]["startup_phase"] == phase

    monkeypatch.setattr(server, "_serve_http", serve)
    try:
        server._run_http_loop()
    finally:
        if server._worker_observer is not None:
            server._worker_observer.close()


@pytest.mark.parametrize("failure", ["sink", "delivery_lock", "stalled_sink", "queue"])
def test_observation_failure_never_blocks_work(tmp_path, failure):
    from mempalace.worker_observation import Observer

    release, entered = threading.Event(), threading.Event()

    def sink(line):
        if failure == "sink":
            raise OSError("synthetic sink failure")
        if failure == "stalled_sink":
            entered.set()
            release.wait(5)

    observer = Observer(tmp_path, sink=sink)
    try:
        if failure == "stalled_sink":
            assert entered.wait(2)
        if failure == "delivery_lock":
            observer.delivery_lock.acquire()
        if failure == "queue":

            def full(line):
                import queue

                raise queue.Full

            observer.queue.put_nowait = full
        before = observer.lost
        start = time.monotonic()
        ran = False
        with observer.scope("mempalace_status"):
            ran = True
        elapsed = time.monotonic() - start
        assert ran
        if failure == "sink":
            observer.queue.join()
        assert observer.lost > before
        assert elapsed < 0.1, "observation must not wait for a stalled sink"
        assert not getattr(observer, "acknowledgments", {})
    finally:
        if failure == "delivery_lock":
            observer.delivery_lock.release()
        release.set()
        observer.close()
