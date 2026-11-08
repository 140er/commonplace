"""User-visible startup exit status and post-ready recovery integration."""

import os
import socket
import subprocess
import sys


def child_env(tmp_path):
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "LANG") if key in os.environ}
    env.update(
        HOME=str(tmp_path),
        USERPROFILE=str(tmp_path),
        MEMPALACE_CONFIG_DIR=str(tmp_path / "config"),
        MEMPALACE_WORKER_MEMORY_OBSERVE="0",
        MEMPALACE_MCP_WRITER_WAIT_SECONDS="0",
    )
    return env


def run_startup(command, env):
    proc = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        _, stderr = proc.communicate(timeout=15)
        return proc.returncode, stderr.decode(errors="replace")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        proc.stderr.close()


def command(palace, port):
    return [
        sys.executable,
        "-m",
        "mempalace.mcp_server",
        "--transport",
        "http",
        "--palace",
        str(palace),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]


def test_user_entrypoint_busy_port_returns_one(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        code, stderr = run_startup(
            command(tmp_path / "palace", listener.getsockname()[1]) + ["--read-only"],
            child_env(tmp_path),
        )
    assert code == 1
    assert "status 1" in stderr and "Failed to start" in stderr


def test_user_entrypoint_writer_lease_refusal_returns_two(tmp_path):
    from mempalace.palace import mine_palace_lock

    palace = tmp_path / "palace"
    with mine_palace_lock(str(palace), lock_dir=str(tmp_path / ".mempalace" / "locks")):
        code, stderr = run_startup(command(palace, 0), child_env(tmp_path))
    assert code == 2
    assert "status 2" in stderr and "startup refused" in stderr


def test_user_entrypoint_second_monitor_returns_two(tmp_path):
    from filelock import FileLock

    from mempalace.paths import palace_key

    palace = tmp_path / "palace"
    state = tmp_path / ".mempalace" / "server" / palace_key(str(palace))
    state.mkdir(parents=True)
    with FileLock(str(state / "monitor.lock")):
        code, stderr = run_startup(command(palace, 0), child_env(tmp_path))
    assert code == 2
    assert "already running" in stderr and "Traceback" not in stderr


def test_post_ready_circuit_and_transition_logs(tmp_path, monkeypatch, caplog):
    import logging

    from mempalace import server_registry
    from mempalace.mcp_http_supervisor import HTTPMonitor
    from tests.test_http_restart_review import Process

    monkeypatch.setattr(server_registry, "live_child_record", lambda _: None)
    caplog.set_level(logging.INFO)
    now = [0.0]
    children = []

    def launch():
        child = Process(ready=not children)
        children.append(child)
        return child

    monitor = HTTPMonitor(
        palace_path=str(tmp_path),
        launcher=launch,
        clock=lambda: now[0],
        publish_marker=lambda *args: None,
    )
    monitor.step()
    for tick in (1, 2, 3):
        now[0] = tick
        children[-1].code = 1
        monitor.step()
    assert len(children) == 3
    assert "latched" in caplog.text and "cooldown" in caplog.text
    now[0] = 62.999
    monitor.step()
    assert len(children) == 3
    now[0] = 63
    monitor.step()
    assert len(children) == 4
    assert "recovery probe" in caplog.text
    children[-1].ready = True
    monitor.step()
    assert monitor.state == "running"
