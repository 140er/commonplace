"""Deterministic regression probes through the pre-fix public seams."""

import http.client
import importlib
import json
import os
import signal
import socket
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from commonplace.gateway import WorkerClient, WorkerPaused
from commonplace.workers import DrainTimeout, Worker, WorkerSupervisor
from mempalace import server_registry


def capture(call):
    try:
        return call()
    except SystemExit as exc:
        return ("exit", exc.code)
    except Exception as exc:
        return exc


def monitor_module():
    return importlib.import_module("mempalace.mcp_http_supervisor")


class Process:
    pid = None
    start_identity = None
    completed = None
    terminal = None

    def __init__(self, *, ready=True):
        self.ready = ready
        self.code = None
        self.reaped = False

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        self.reaped = True
        return self.code

    def drain(self):
        self.code = 0


@pytest.mark.parametrize("command", ["search", "mine"])
@pytest.mark.parametrize("state", ["restarting", "cooldown"])
def test_cli_gap_exits_before_local_path(command, state, monkeypatch, capsys):
    from mempalace import cli

    hub = SimpleNamespace(
        info={"hub_unavailable": True, "state": state},
        capabilities=[],
        read_only=False,
        healthy=lambda: False,
    )
    monkeypatch.setattr(cli.HubClient, "discover", lambda *args, **kwargs: hub)
    direct = []

    def invoke():
        function = getattr(cli, "_forward_" + command + "_to_hub")
        if not function(SimpleNamespace(), "fixture"):
            direct.append(command)
        return "returned"

    outcome = capture(invoke)
    assert outcome == ("exit", 1), repr(outcome)
    assert direct == []
    assert "retry" in capsys.readouterr().err.lower()


def test_missing_set_blocking_keeps_fact_channel(monkeypatch):
    module = importlib.import_module("mempalace.worker_lifecycle")
    read_fd, write_fd = os.pipe()
    monkeypatch.delenv(module.LIVENESS_ENV, raising=False)
    monkeypatch.setenv(module.FACTS_ENV, str(write_fd))
    monkeypatch.delattr(os, "set_blocking", raising=False)
    try:
        coordinator = capture(
            lambda: module.child_coordinator(cleanup=lambda: None, exit=lambda: None)
        )
        assert isinstance(coordinator, module.Coordinator), repr(coordinator)
        coordinator.ready()
        assert json.loads(os.read(read_fd, 512))["kind"] == "ready"
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.parametrize("status", [1, 2])
def test_first_unready_child_propagates_startup_status(tmp_path, monkeypatch, status):
    module = monitor_module()
    monkeypatch.setattr(server_registry, "live_child_record", lambda _: None)
    process = Process(ready=False)
    launches = []
    monitor = module.HTTPMonitor(
        palace_path=str(tmp_path),
        launcher=lambda: launches.append(True) or process,
        publish_marker=lambda *args: None,
    )
    monitor.step()
    process.code = status
    outcome = capture(monitor.step)
    assert outcome == ("exit", status), repr(outcome)
    assert process.reaped and len(launches) == 1


def test_busy_port_exits_monitor_with_child_status(tmp_path):
    module = monitor_module()
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "LANG") if name in os.environ}
    env.update(
        HOME=str(tmp_path),
        USERPROFILE=str(tmp_path),
        MEMPALACE_CONFIG_DIR=str(tmp_path / "config"),
        MEMPALACE_WORKER_MEMORY_OBSERVE="0",
    )
    children = []
    launches = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        command = [
            sys.executable,
            "-m",
            "mempalace.mcp_server",
            "--transport",
            "http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--palace",
            str(tmp_path / "palace"),
            "--read-only",
        ]

        def launch():
            launches.append(True)
            if len(launches) != 1:
                raise RuntimeError("fixture forbids a second startup launch")
            child = module.Child(command, env)
            children.append(child)
            return child

        monitor = module.HTTPMonitor(
            palace_path=str(tmp_path / "palace"), launcher=launch, publish_marker=lambda *args: None
        )
        try:
            monitor.step()
            assert children[0].process.wait(timeout=10) == 1, "invalid fixture: bind must fail"
            outcome = capture(monitor.step)
            assert outcome == ("exit", 1), repr(outcome)
            assert len(launches) == 1
        finally:
            for child in children:
                child.drain()
                child.wait(timeout=5)


def test_second_monitor_reports_existing_lease_refusal(tmp_path, monkeypatch, caplog):
    from filelock import FileLock

    module = monitor_module()
    monkeypatch.setattr(server_registry, "server_state_dir", lambda _: tmp_path)
    monitor = module.HTTPMonitor(
        palace_path="fixture",
        launcher=lambda: pytest.fail("existing monitor must not launch another child"),
    )
    with FileLock(str(tmp_path / "monitor.lock")):
        outcome = capture(monitor.run)
    assert outcome == ("exit", 2), repr(outcome)
    assert "already" in caplog.text.lower() and "monitor" in caplog.text.lower()


@pytest.mark.parametrize("recorded_identity", [None, 100])
def test_live_pid_with_unknown_identity_blocks_launch(tmp_path, monkeypatch, recorded_identity):
    module = monitor_module()
    lifecycle = importlib.import_module("mempalace.worker_lifecycle")
    monkeypatch.setattr(lifecycle, "process_identity", lambda _: None)
    monkeypatch.setattr(server_registry, "server_state_dir", lambda _: tmp_path)
    (tmp_path / "serverinfo.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "start_identity": recorded_identity,
                "host": "127.0.0.1",
                "port": 1,
            }
        )
    )
    launches, states = [], []
    monitor = module.HTTPMonitor(
        palace_path="fixture",
        launcher=lambda: launches.append(True) or Process(),
        publish_marker=lambda state, child: states.append(state),
    )
    monitor.step()
    assert launches == [], "a live PID with unreadable identity remains an orphan"
    assert states == ["orphan_draining"]


def test_paused_pin_preserves_gateway_retry_hint(tmp_path, monkeypatch):
    supervisor = WorkerSupervisor(str(tmp_path))
    worker = Worker("ws_fixture", "127.0.0.1", 1, "fixture-token", lambda: None)

    def get(_):
        supervisor._paused = True  # pause after get, before the actual pin
        return worker

    monkeypatch.setattr(supervisor, "get", get)
    principal = SimpleNamespace(workspace_id="ws_fixture")
    outcome = capture(lambda: WorkerClient(supervisor).post(principal, {"method": "ping"}))
    assert isinstance(outcome, WorkerPaused), repr(outcome)
    assert outcome.retry_after_s > 0
    assert worker.pins == 0 and supervisor.in_flight == 0


def test_reap_timeout_reinserts_only_retiring_worker(tmp_path):
    class Launcher:
        def start(self, *args):
            pytest.fail("a live draining generation cannot be launched over")

    supervisor = WorkerSupervisor(str(tmp_path), launcher=Launcher())
    worker = Worker(
        "ws_fixture", "127.0.0.1", 1, "fixture-token", lambda: None, alive=lambda: False
    )

    def reap(_):
        raise subprocess.TimeoutExpired("fixture", 0)

    worker.reap = reap
    supervisor._workers[worker.workspace_id] = worker
    outcome = capture(lambda: supervisor._get(worker.workspace_id, timeout_s=0))
    assert isinstance(outcome, DrainTimeout), repr(outcome)
    assert supervisor._workers[worker.workspace_id] is worker
    assert worker.retiring is True


def test_signal_handler_only_sets_stop_flag(tmp_path, monkeypatch):
    module = monitor_module()
    process = Process()
    handlers, operations = {}, []
    inside_signal = [False]
    original_drain = process.drain

    def drain():
        operations.append(("drain", inside_signal[0]))
        original_drain()

    process.drain = drain

    class Wake:
        def wait(self, _):
            inside_signal[0] = True
            try:
                handlers[signal.SIGTERM](signal.SIGTERM, None)
            finally:
                inside_signal[0] = False

        def clear(self):
            pass

        def set(self):
            operations.append(("wake", inside_signal[0]))

    def install(sig, handler):
        previous = handlers.get(sig)
        handlers[sig] = handler
        return previous

    monkeypatch.setattr(signal, "signal", install)
    monkeypatch.setattr(server_registry, "server_state_dir", lambda _: tmp_path)
    monkeypatch.setattr(server_registry, "live_child_record", lambda _: None)
    monitor = module.HTTPMonitor(
        palace_path="fixture", launcher=lambda: process, publish_marker=lambda *args: None
    )
    monitor.wake = Wake()
    monitor.run()
    assert all(not in_handler for _, in_handler in operations), operations
    assert process.reaped


def test_marker_failure_keeps_child_and_retries(tmp_path, monkeypatch):
    module = monitor_module()
    monkeypatch.setattr(server_registry, "live_child_record", lambda _: None)
    attempts = []

    def publish(state, child):
        attempts.append(state)
        if state == "draining" and attempts.count("draining") == 1:
            raise OSError("fixture publication failure")

    process = Process()
    monitor = module.HTTPMonitor(
        palace_path=str(tmp_path), launcher=lambda: process, publish_marker=publish
    )
    monitor.step()
    process.terminal = {"reason": "idle"}
    outcome = capture(monitor.step)
    assert outcome is None, repr(outcome)
    assert monitor.child is process and process.poll() is None
    monitor.step()
    assert attempts.count("draining") == 2


def test_http_error_reply_does_not_count_successful_completion(monkeypatch):
    from mempalace import mcp_server

    monkeypatch.setattr(mcp_server, "_http_coordinator", None)
    monkeypatch.setenv("MEMPALACE_MCP_HTTP_TOKEN", "fixture-token")
    monkeypatch.delenv("MEMPALACE_MCP_TRUSTED_IDENTITY", raising=False)
    monkeypatch.setattr(
        mcp_server,
        "_http_dispatch",
        lambda request: {
            "jsonrpc": "2.0",
            "id": request["id"],
            "error": {"code": -32602, "message": "fixture"},
        },
    )
    httpd = mcp_server._build_http_server("127.0.0.1", 0)
    completed = threading.Event()
    release = httpd.coordinator.release

    def finished(**kwargs):
        release(**kwargs)
        completed.set()

    httpd.coordinator.release = finished
    thread = threading.Thread(target=httpd.serve_forever)
    thread.start()
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
    try:
        conn.request(
            "POST",
            "/mcp",
            json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "fixture"}}
            ),
            {"Content-Type": "application/json", "Authorization": "Bearer fixture-token"},
        )
        response = conn.getresponse()
        assert response.status == 200 and "error" in json.loads(response.read())
        assert completed.wait(5), "invalid harness: response release was not reached"
        assert httpd.coordinator.admitted == 1
        assert httpd.coordinator.completed == 0
    finally:
        conn.close()
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)


def test_http_idle_exit_reports_idle_to_coordinator(monkeypatch):
    from mempalace import mcp_server

    targets, reasons, exits = [], [], []

    class Thread:
        def __init__(self, *, target, **kwargs):
            targets.append(target)

        def start(self):
            pass

    class EndTick(Exception):
        pass

    sleeps = []

    def sleep(_):
        sleeps.append(True)
        if len(sleeps) > 1:
            raise EndTick()

    monkeypatch.setattr(mcp_server.threading, "Thread", Thread)
    monkeypatch.setattr(mcp_server.time, "sleep", sleep)
    monkeypatch.setattr(mcp_server.time, "monotonic", lambda: 100)
    monkeypatch.setattr(mcp_server, "_last_request_time", 0)
    monkeypatch.setattr(mcp_server, "_mcp_idle_timeout_secs", lambda: 1)
    monkeypatch.setattr(mcp_server, "_http_coordinator", SimpleNamespace(drain=reasons.append))
    monkeypatch.setattr(mcp_server, "_exit_running_registered_cleanup", lambda: exits.append(True))
    mcp_server._start_idle_exit_watchdog()
    try:
        targets[0]()
    except EndTick:
        pass
    assert reasons == ["idle"]
    assert exits == []


@pytest.mark.parametrize("command", ["search", "mine"])
def test_cli_nonforwardable_command_still_refuses_gap(command, tmp_path, monkeypatch):
    from mempalace import cli
    from tests.test_hub_forward import _mine_args, _search_args

    hub = SimpleNamespace(info={"hub_unavailable": True})
    monkeypatch.setattr(cli.HubClient, "discover", lambda *args, **kwargs: hub)
    direct = []
    if command == "search":
        from mempalace import searcher

        args = _search_args(palace=str(tmp_path / "palace"), include_media=True)
        monkeypatch.setattr(searcher, "search", lambda **kwargs: direct.append("search"))
    else:
        from mempalace import convo_miner

        args = _mine_args(tmp_path, palace=str(tmp_path / "palace"), no_gitignore=True)
        monkeypatch.setattr(
            cli,
            "_resolve_cli_write_routing_or_exit",
            lambda *args: SimpleNamespace(use_daemon=False),
        )
        monkeypatch.setattr(cli, "_exit_on_misspelled_embedding_model", lambda: None)
        monkeypatch.setattr(convo_miner, "mine_convos", lambda **kwargs: direct.append("mine"))
    outcome = capture(lambda: getattr(cli, "cmd_" + command)(args))
    assert outcome == ("exit", 1), repr(outcome)
    assert direct == []
