"""Gateway shutdown cannot leave extraction workers restarting the supervisor."""

import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from commonplace.gateway import Gateway
from commonplace.workers import WorkerSupervisor


def test_gateway_closes_background_handoff_work_before_permanently_closing_workers(monkeypatch):
    calls = []

    class Server:
        server_address = ("127.0.0.1", 1)

        def serve_forever(self):
            raise RuntimeError("test shutdown")

        def server_close(self):
            calls.append("server")

    class Reaper:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(threading, "Thread", Reaper)
    monkeypatch.setattr(signal, "signal", lambda *_args: None)
    gateway = Gateway.__new__(Gateway)
    gateway.build_server = lambda *_args: Server()
    gateway.handoff = SimpleNamespace(close=lambda: calls.append("handoff"))
    gateway.supervisor = SimpleNamespace(
        stop_idle=lambda: None, close=lambda: calls.append("workers")
    )
    with pytest.raises(RuntimeError, match="test shutdown"):
        gateway.serve("127.0.0.1", 0)
    assert calls == ["server", "handoff", "workers"]


def test_worker_started_during_shutdown_is_stopped_and_cannot_be_restarted(tmp_path):
    entered, release = threading.Event(), threading.Event()
    stopped = []
    worker = SimpleNamespace(stop=lambda: stopped.append(True), alive=lambda: True)

    class Launcher:
        def start(self, *_args):
            entered.set()
            assert release.wait(timeout=5)
            return worker

    supervisor = WorkerSupervisor(str(tmp_path), launcher=Launcher())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(supervisor.get, "ws_a1")
        try:
            assert entered.wait(timeout=5)
            supervisor.close()
            release.set()
            with pytest.raises(RuntimeError, match="closed"):
                future.result(timeout=5)
        finally:
            release.set()
            supervisor.close()
    assert stopped == [True] and supervisor._workers == {}
    with pytest.raises(RuntimeError, match="closed"):
        supervisor.get("ws_a1")
