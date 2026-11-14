"""HTTP startup observation is asynchronous, ordered and isolated from failure."""

import json
import threading
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def http_child_generation(monkeypatch):
    """Startup ordering is a property of the HTTP child, not of its restart owner.

    Without the child marker ``_run_http_loop`` would hand off to the real
    supervisor, which launches a server process and blocks for as long as it
    lives. The stub makes that mistake fail immediately instead of hanging.
    """
    from mempalace import mcp_server as server

    def no_supervisor(**kwargs):
        raise AssertionError("startup tests must run the child path, not spawn a supervisor")

    monkeypatch.setenv("MEMPALACE_HTTP_CHILD", "1")
    monkeypatch.setattr(server, "_http_coordinator", None, raising=False)
    monkeypatch.setattr(server, "_http_supervisor_factory", no_supervisor, raising=False)


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
def test_http_phase_coverage(tmp_path, monkeypatch, phase):
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
    monkeypatch.setattr(server, "_refresh_vector_disabled_flag", lambda: None)

    def serve(*args):
        with server._worker_phase(phase):
            pass

    monkeypatch.setattr(server, "_serve_http", serve)
    try:
        server._run_http_loop()
        if server._worker_observer is not None:
            server._worker_observer.close()
        path = tmp_path / "diagnostics" / "worker-memory.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        assert [(r["event"], r["startup_phase"]) for r in rows if r["startup_phase"] == phase] == [
            ("phase_begin", phase),
            ("phase_end", phase),
        ]
    finally:
        if server._worker_observer is not None:
            server._worker_observer.close()


@pytest.mark.parametrize("failure", ["constructor", "sink", "delivery_lock", "queue"])
def test_observation_failure_does_not_stop_http_startup(tmp_path, monkeypatch, failure):
    from mempalace import mcp_server as server
    from mempalace.worker_observation import Observer

    instances, served = [], []

    def factory(directory):
        if failure == "constructor":
            raise OSError("synthetic constructor failure")
        observer = Observer(directory)
        observer.queue.join()
        instances.append(observer)
        if failure == "sink":

            def broken(line):
                raise OSError("synthetic sink failure")

            observer.sink = broken
        elif failure == "delivery_lock":
            observer.delivery_lock.acquire()
        elif failure == "queue":

            def full(line):
                import queue

                raise queue.Full

            observer.queue.put_nowait = full
        return observer

    monkeypatch.delenv("MEMPALACE_WORKER_MEMORY_OBSERVE", raising=False)
    monkeypatch.delenv("MEMPALACE_EAGER_WARMUP", raising=False)
    monkeypatch.setattr(server, "_worker_observer", None)
    monkeypatch.setattr(server, "_worker_observer_failed", False)
    monkeypatch.setattr(server, "_worker_observation_lost", 0)
    monkeypatch.setattr(server, "_worker_observer_factory", factory)
    monkeypatch.setattr(server._config, "_config_dir", tmp_path)
    monkeypatch.setattr(server, "_READ_ONLY", True)
    monkeypatch.setattr(server, "_args", SimpleNamespace(host="127.0.0.1", port=0))
    monkeypatch.setattr(server, "_start_idle_exit_watchdog", lambda: None)
    monkeypatch.setattr(server, "_start_write_stall_watchdog", lambda: None)
    monkeypatch.setattr(server, "_refresh_vector_disabled_flag", lambda: None)
    monkeypatch.setattr(server, "_serve_http", lambda *args: served.append(True))
    try:
        server._run_http_loop()
        for observer in instances:
            observer.queue.join()
        assert served == [True]
        assert server._worker_observation_lost + sum(o.lost for o in instances) > 0
    finally:
        for observer in instances:
            if failure == "delivery_lock":
                observer.delivery_lock.release()
            observer.close()


def test_concurrent_record_sequence_order(tmp_path):
    from mempalace.worker_observation import Observer

    observer = Observer(tmp_path)
    barrier = threading.Barrier(5)

    def produce():
        barrier.wait(timeout=5)
        for _ in range(8):
            with observer.scope("mempalace_status", "http"):
                pass

    threads = [threading.Thread(target=produce) for _ in range(4)]
    try:
        for thread in threads:
            thread.start()
        barrier.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()
        observer.close()
        rows = [
            json.loads(line)
            for line in (tmp_path / "diagnostics" / "worker-memory.jsonl").read_text().splitlines()
        ]
        seqs = [r["record_seq"] for r in rows]
        assert len(seqs) > 1
        assert seqs == sorted(set(seqs))
        assert rows[0]["event"] == "generation_start"
    finally:
        for thread in threads:
            thread.join(timeout=5)
        observer.close()
