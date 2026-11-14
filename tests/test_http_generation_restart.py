"""Exercise entrypoint activation before any replacement-only barriers."""

import importlib
import json
import threading
from types import SimpleNamespace

from mempalace import server_registry


class Process:
    def __init__(self):
        self.code = None
        self.ready = True
        self.completed = 0
        self.terminal = None
        self.pid = None
        self.reaped = False

    def poll(self):
        return self.code

    def release_exit(self, code):
        self.code = code

    def wait(self, timeout=None):
        assert self.code is not None
        self.reaped = True
        return self.code

    def drain(self):
        self.code = 0

    def close(self):
        pass


def test_hub_restarts_without_local_fallback(tmp_path, monkeypatch):
    from mempalace import mcp_proxy, mcp_server

    palace = str(tmp_path / "palace")
    monkeypatch.setenv("MEMPALACE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("MEMPALACE_HTTP_CHILD", raising=False)
    monkeypatch.setattr(mcp_server, "_config", SimpleNamespace(palace_path=palace))
    monkeypatch.setattr(mcp_server, "_args", SimpleNamespace(host="127.0.0.1", port=8123))
    monkeypatch.setattr(mcp_server, "_READ_ONLY", True)
    monkeypatch.setattr(mcp_server, "_refresh_vector_disabled_flag", lambda: None)
    monkeypatch.setattr(mcp_server, "_start_idle_exit_watchdog", lambda: None)
    monkeypatch.setattr(mcp_server, "_start_write_stall_watchdog", lambda: None)
    condition = threading.Condition()
    direct = []
    done = []
    processes = []
    errors = []
    entered = threading.Event()
    allow = threading.Event()
    restarting = threading.Event()
    holder = []
    marker_path = server_registry.server_state_dir(palace) / "monitor.json"

    def notify():
        with condition:
            condition.notify_all()

    def serve(*args):
        direct.append(True)
        notify()

    def factory(**kwargs):
        # Executed only when the existing runtime actually selects supervision.
        module = importlib.import_module("mempalace.mcp_http_supervisor")

        def launch():
            if processes:
                assert processes[0].reaped
                entered.set()
                assert allow.wait(5), "invalid harness: replacement not released"
            process = Process()
            processes.append(process)
            return process

        def publish(state, child=None):
            server_registry.write_monitor_marker(palace, state=state, child=child)
            notify()
            if state == "restarting":
                restarting.set()

        monitor = module.HTTPMonitor(launcher=launch, publish_marker=publish, palace_path=palace)
        holder.append(monitor)
        return monitor

    monkeypatch.setattr(mcp_server, "_serve_http", serve)
    monkeypatch.setattr(mcp_server, "_http_supervisor_factory", factory, raising=False)

    def run():
        try:
            mcp_server._run_http_loop()
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.append(True)
            notify()

    thread = threading.Thread(target=run)
    thread.start()
    try:
        with condition:
            assert condition.wait_for(lambda: marker_path.exists() or done, 5), "invalid harness"
        assert marker_path.exists() and not direct, "entrypoint must activate a live monitor"
        old_proxy = mcp_proxy._LocalServer()
        loads = []
        monkeypatch.setattr(mcp_proxy._LocalServer, "load", lambda self: loads.append(True))
        with condition:
            assert condition.wait_for(lambda: processes or errors, 5), "invalid harness"
        processes[0].release_exit(99)
        holder[0].wake.set()
        assert restarting.wait(5) and entered.wait(5), "invalid harness: no gap"
        assert json.loads(marker_path.read_text())["state"] == "restarting"
        request = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        for local in (old_proxy, mcp_proxy._LocalServer()):
            response = mcp_proxy._handle(request, palace, local)
            assert response["error"]["data"]["retryable"] is True
        assert loads == []
        allow.set()
        with condition:
            assert condition.wait_for(lambda: len(processes) == 2 or errors, 5), "invalid harness"
        assert not errors
    finally:
        allow.set()
        if holder:
            holder[0].stop()
        thread.join(5)
        assert not thread.is_alive(), "invalid harness: monitor not reaped"


def monitor_module():
    return importlib.import_module("mempalace.mcp_http_supervisor")


def test_monitor_startup_failure_circuit_green(tmp_path, monkeypatch):
    module = monitor_module()
    now = [0.0]
    calls = []
    markers = []

    def fail():
        calls.append(now[0])
        raise OSError("fixture start failure")

    monkeypatch.setattr(server_registry, "live_child_record", lambda _: None)
    monitor = module.HTTPMonitor(
        launcher=fail,
        palace_path=str(tmp_path),
        clock=lambda: now[0],
        publish_marker=lambda state, child: markers.append(state),
    )
    for tick in (0, 1, 2):
        now[0] = tick
        monitor.step()
    assert len(calls) == 3
    now[0] = 61.999
    monitor.step()
    assert len(calls) == 3
    now[0] = 62
    monitor.step()
    assert len(calls) == 4
    now[0] = 121.999
    monitor.step()
    assert len(calls) == 4
    now[0] = 122
    process = Process()
    monitor.launcher = lambda: calls.append(now[0]) or process
    monitor.step()
    assert len(calls) == 5
    process.completed = 16
    monitor.step()
    assert monitor.circuit.failures == 0
    assert "cooldown" in markers
    monitor.stop()
    monitor.step()


def test_monitor_death_drains_orphan_before_new_launch(tmp_path, monkeypatch):
    from mempalace.worker_lifecycle import Coordinator

    module = monitor_module()
    now = [0.0]
    calls, states, sequence = [], [], []
    alive = [True]
    process = Process()
    monkeypatch.setattr(
        server_registry,
        "live_child_record",
        lambda _: {"pid": 42, "start_identity": 7} if alive[0] else None,
    )
    monitor = module.HTTPMonitor(
        launcher=lambda: calls.append("launch") or process,
        palace_path=str(tmp_path),
        clock=lambda: now[0],
        publish_marker=lambda state, child: states.append(state),
    )

    def cleanup():
        sequence.append("cleanup")
        alive[0] = False

    old = Coordinator(cleanup=cleanup, exit=lambda: sequence.append("exit"))
    assert old.acquire() and old.admit()
    # This is the same callback the private liveness pipe invokes on EOF.
    old.drain("parent_lost")
    assert not old.acquire()
    monitor.step()
    assert states == ["orphan_draining"] and calls == []
    assert sequence == []
    sequence.append("reply flushed")
    old.release(storage=True, completed=True)
    assert sequence == ["reply flushed", "cleanup", "exit"]
    now[0] = 59.999
    monitor.step()
    assert calls == []
    now[0] = 60
    monitor.step()
    assert calls == ["launch"]
    monitor.stop()
    monitor.step()


def test_live_orphan_rechecked_only_at_fixed_cadence(tmp_path, monkeypatch):
    module = monitor_module()
    now = [0.0]
    checks, launches = [], []

    def orphan(_):
        checks.append(now[0])
        return {"pid": 42, "start_identity": 7}

    monkeypatch.setattr(server_registry, "live_child_record", orphan)
    monitor = module.HTTPMonitor(
        launcher=lambda: launches.append(True),
        palace_path=str(tmp_path),
        clock=lambda: now[0],
        publish_marker=lambda *args: None,
    )
    for tick in (0, 1, 59.999, 60, 60.001, 119.999, 120):
        now[0] = tick
        monitor.step()
    assert checks == [0, 60, 120]
    assert launches == []
