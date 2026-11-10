"""Confirmed run-stop over the real runner CLI/HTTP path and a synthetic host."""

import json
import subprocess
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest

from tests.runner_harness import launches, owners as owners, signup_hub as signup_hub
from tests.test_runner_agent import _invoke, _queue, _setup
from tests.test_runner_jobs import _cancel, _machine_call, _status


@pytest.fixture
def host():
    """External host double supervising an actual subprocess, with no provider calls."""
    state = SimpleNamespace(worker=None, receipts={})

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/disconnect":
                self.close_connection = True
                return
            if self.path == "/launch":
                script = (
                    "import signal,sys; "
                    + (
                        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                        if request["ignore"]
                        else ""
                    )
                    + "print('ready', flush=True); sys.stdin.read()"
                )
                state.worker = subprocess.Popen(
                    [sys.executable, "-c", script],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                assert state.worker.stdout.readline() == b"ready\n"
                content = dict(threadId="demo-thread", runId="demo-run", status="running")
            else:
                assert request["runId"] == "demo-run" and request["threadId"] == "demo-thread"
                content = state.receipts.get(request["stopRequestId"])
                if content is None:
                    content = dict(request, status="already_ended")
                    if state.worker.poll() is None:
                        accepted = (
                            datetime.now(timezone.utc)
                            .isoformat(timespec="microseconds")
                            .replace("+00:00", "Z")
                        )
                        state.worker.terminate()
                        try:
                            state.worker.wait(timeout=0.15)
                        except subprocess.TimeoutExpired:
                            content.update(status="running")
                        else:
                            content.update(
                                status="stopped",
                                acceptedAt=accepted,
                                endedAt=datetime.now(timezone.utc)
                                .isoformat(timespec="microseconds")
                                .replace("+00:00", "Z"),
                            )
                            state.receipts[request["stopRequestId"]] = content
            data = json.dumps({"isError": False, "structuredContent": content}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.port = server.server_port
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if state.worker is not None:
            if state.worker.poll() is None:
                state.worker.kill()
            state.worker.wait(timeout=2)
            state.worker.stdin.close()
            state.worker.stdout.close()


def _launched(hub, owner, tmp_path, host, mode="ok"):
    machine, path, env = _setup(hub, owner, tmp_path)
    fake_path = tmp_path / "fake-t3.json"
    fake = json.loads(fake_path.read_text())
    fake.update(host_port=host.port, mode=mode)
    fake_path.write_text(json.dumps(fake))
    number, job = _queue(hub, owner, machine)
    result = _invoke(path, env)
    assert result.returncode == 0
    assert host.worker is not None and host.worker.poll() is None
    return machine, path, env, number, job, result


def _stored(hub, owner, job):
    from commonplace.runner import jobs

    with jobs.open_store(hub.gateway, owner["workspace"]).connection() as conn:
        return dict(conn.execute("SELECT * FROM runner_jobs WHERE id=?", (job,)).fetchone())


@pytest.mark.parametrize(
    "field,value",
    [
        ("contractVersion", True),
        ("contractVersion", 2),
        ("threadId", "other"),
        ("runId", "other"),
        ("stopRequestId", "other"),
        ("status", "already_ended"),
    ],
)
def test_host_receipt_must_match_stop_request(monkeypatch, field, value):
    from commonplace.runner import agent

    content = dict(
        contractVersion=1,
        threadId="thread",
        runId="run",
        stopRequestId="stop",
        status="stopped",
        acceptedAt="2026-11-10T16:00:00.000001Z",
        endedAt="2026-11-10T16:00:00.000002Z",
    )
    content[field] = value
    monkeypatch.setattr(
        agent.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps({"structuredContent": content})
        ),
    )
    result = agent._stop_run(
        dict(t3_executable=["host"], t3_timeout_seconds=2),
        dict(thread_id="thread", run_id="run", stop_request_id="stop"),
        {},
    )
    assert result["outcome"] == "unconfirmed"


def test_host_timeout_is_unconfirmed(monkeypatch):
    from commonplace.runner import agent

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("host", 2)

    monkeypatch.setattr(agent.subprocess, "run", timeout)
    result = agent._stop_run(
        dict(t3_executable=["host"], t3_timeout_seconds=2),
        dict(thread_id="thread", run_id="run", stop_request_id="stop"),
        {},
    )
    assert result == dict(outcome="unconfirmed", code="stop_unconfirmed")


def test_launch_preserves_run_id(signup_hub, owners, tmp_path, host):
    hub, owner = signup_hub, owners["A"]
    _, _, _, _, job, _ = _launched(hub, owner, tmp_path, host)
    assert _stored(hub, owner, job).get("run_id") == "demo-run", "launch must retain its runId"


def test_confirmed_stop_replays_durable_result_without_stopping_twice(
    signup_hub, owners, tmp_path, host
):
    import io

    hub, owner = signup_hub, owners["A"]
    _, path, env, _, job, _ = _launched(hub, owner, tmp_path, host)
    assert _cancel(hub, owner, job)[0] == 200
    original = hub.gateway.find_route("POST", "/api/runner/stop-result")
    bodies = []

    def unavailable(handler):
        raw = handler.rfile.read(int(handler.headers["Content-Length"]))
        bodies.append(json.loads(raw))
        handler.rfile = io.BytesIO(raw)
        handler._send_json(503, {"error": "unavailable"})

    hub.gateway.add_route("POST", "/api/runner/stop-result", unavailable)
    assert _invoke(path, env).returncode == 5
    assert _stored(hub, owner, job)["state"] == "started"
    assert len(list((tmp_path / "journal" / "stops").glob("*.json"))) == 1
    hub.gateway.add_route("POST", "/api/runner/stop-result", original)
    assert _invoke(path, env).returncode == 0
    row = _stored(hub, owner, job)
    assert row["stop_status"] == "confirmed"
    calls = [entry for entry in launches(tmp_path) if entry["argv"][-2] == "t3_run_stop"]
    assert len(calls) == 1
    assert list((tmp_path / "journal" / "stops").glob("*.json")) == []
    # Reopening and duplicate delivery retain the original immutable evidence.
    from commonplace.runner import registry

    machine = next(
        m
        for m in registry.machines(hub.gateway.db, owner["workspace"])
        if m["id"] == row["machine_id"]
    )
    from commonplace.runner import jobs

    replay = jobs.stop_result(
        jobs.open_store(hub.gateway, owner["workspace"]), machine, **bodies[0]
    )
    assert replay == row


@pytest.mark.parametrize("mode", ["ok", "ignored", "disconnect", "unsupported", "false-confirm"])
def test_launched_worker_stop(signup_hub, owners, tmp_path, host, mode):
    hub, owner = signup_hub, owners["A"]
    _, path, env, task, job, _ = _launched(hub, owner, tmp_path, host, mode)
    status, body = _cancel(hub, owner, job)
    assert status == 200, ("a launched worker must accept a stop request", status, body)
    assert body["state"] == "started", "request alone must not imply stopped"
    assert _invoke(path, env).returncode == 0
    row = _stored(hub, owner, job)
    calls = [entry for entry in launches(tmp_path) if entry["argv"][-2] == "t3_run_stop"]
    assert len(calls) == 1 and calls[0]["launch"]["runId"] == "demo-run"
    running = host.worker.poll() is None
    if mode == "ok":
        assert not running, "host must have ended the launched worker"
        assert row["state"] == "cancelled" and row["stop_status"] == "confirmed"
        assert row["stop_ended_at"] > row["stop_accepted_at"]
        assert row["stop_confirmed_at"] and row["stop_request_id"]
    else:
        assert running
        assert row["state"] == "started" and row["stop_status"] != "confirmed"
        assert row["stop_confirmed_at"] is None
    assert _status(hub, owner, task)["stop_status"] == row["stop_status"]


def _stop_command(hub, owner, tmp_path, host):
    machine, _, _, _, job, _ = _launched(hub, owner, tmp_path, host)
    assert _cancel(hub, owner, job)[0] == 200
    status, command = _machine_call(hub, machine, "stop")
    assert status == 200, (status, command)
    return machine, job, command


@pytest.mark.parametrize("field", ["attempt", "run_id", "thread_id", "stop_request_id"])
def test_stop_result_fenced_to_attempt(signup_hub, owners, tmp_path, host, field):
    hub, owner = signup_hub, owners["A"]
    machine, job, command = _stop_command(hub, owner, tmp_path, host)
    evidence = dict(
        command,
        outcome="confirmed",
        accepted_at="2026-11-10T16:00:00.000001Z",
        ended_at="2026-11-10T16:00:00.000002Z",
    )
    evidence[field] = "99" if field == "attempt" else "stale"
    status, _ = _machine_call(hub, machine, "stop-result", evidence)
    assert status == 409, "a stale stop must not confirm another attempt"
    row = _stored(hub, owner, job)
    assert row["state"] == "started" and row["stop_confirmed_at"] is None


@pytest.mark.parametrize(
    "accepted,ended",
    [
        (None, None),
        ("bad", "bad"),
        ("2026-11-10T16:00:00Z", "2026-11-10T16:00:00Z"),
        ("2026-11-10T16:00:02Z", "2026-11-10T16:00:01Z"),
    ],
)
def test_stop_confirmation_requires_later_terminal_evidence(
    signup_hub, owners, tmp_path, host, accepted, ended
):
    hub, owner = signup_hub, owners["A"]
    machine, job, command = _stop_command(hub, owner, tmp_path, host)
    evidence = dict(command, outcome="confirmed")
    if accepted is not None:
        evidence.update(accepted_at=accepted, ended_at=ended)
    assert _machine_call(hub, machine, "stop-result", evidence)[0] == 400
    assert _stored(hub, owner, job)["state"] == "started"
