"""Only authenticated, generation-matched no-dispatch proof permits replay."""

import contextlib
import http.client
import json
from types import SimpleNamespace

import pytest

from commonplace.gateway import WorkerClient, WorkerUnavailable
from commonplace.workers import Worker

GENERATION = "11111111-1111-4111-8111-111111111111"
REPLACEMENT = "22222222-2222-4222-8222-222222222222"
REQUEST = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "write"}}


class Supervisor:
    def __init__(self):
        self.events = []
        self.workers = []
        for generation in (GENERATION, REPLACEMENT):
            worker = Worker("ws_fixture", "127.0.0.1", 1, "fixture-token", lambda: None)
            worker.generation = generation
            self.workers.append(worker)
        self.current = 0

    def get(self, _workspace):
        worker = self.workers[self.current]
        return self.replace(worker) if getattr(worker, "retiring", False) else worker

    @contextlib.contextmanager
    def pin(self, worker):
        self.events.append("pin")
        try:
            yield worker
        finally:
            self.events.append("release")

    def retire(self, worker):
        self.events.append("retire")

    def replace(self, worker, timeout_s=30):
        assert self.events[-2:] == ["release", "retire"]
        self.events.append("reap")
        self.current = 1
        return self.workers[1]


def run_post(
    monkeypatch,
    status=503,
    headers=None,
    payload=None,
    failure=None,
    body_failure=None,
    replacement_failure=None,
):
    supervisor = Supervisor()
    if replacement_failure is not None:

        def replace(*args, **kwargs):
            raise replacement_failure

        supervisor.replace = replace
    attempts = []
    if headers is None:
        headers = {
            "X-Mempalace-Generation": GENERATION,
            "X-Mempalace-Dispatch": "not-started",
            "X-Mempalace-Reason": "recycling",
        }
    if payload is None:
        payload = {
            "jsonrpc": "2.0",
            "id": 7,
            "error": {
                "code": -32071,
                "data": {
                    "dispatch_started": False,
                    "generation": GENERATION,
                    "reason": "recycling",
                },
            },
        }

    class Response:
        def __init__(self, code, data, fields):
            self.status = code
            self.data = json.dumps(data).encode()
            self.headers = fields

        def getheader(self, name, default=None):
            return self.headers.get(name, default)

        def isclosed(self):
            return not self.data

        def read1(self, _size):
            if body_failure:
                raise body_failure
            data, self.data = self.data, b""
            return data

        def close(self):
            pass

    class Connection:
        sock = None

        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            attempts.append(supervisor.current)
            if failure:
                raise failure

        def getresponse(self):
            if len(attempts) == 1:
                return Response(status, payload, headers)
            return Response(200, {"result": "replacement"}, {})

        def close(self):
            pass

    monkeypatch.setattr("commonplace.gateway.http.client.HTTPConnection", Connection)
    monkeypatch.setattr("commonplace.rules.checks.remaining", lambda value: value)
    principal = SimpleNamespace(
        workspace_id="ws_fixture",
        identity=lambda: {
            "user": "u",
            "connection": "c",
            "client": "fixture",
            "auth": "pat",
            "trust": "standard",
            "workspace": "ws_fixture",
        },
    )
    supervisor.call_again = lambda: WorkerClient(supervisor).post(principal, REQUEST)
    try:
        outcome = supervisor.call_again()
    except Exception as exc:
        outcome = exc
    return outcome, attempts, supervisor


def test_proven_recycling_refusal_retries_once(monkeypatch):
    outcome, attempts, supervisor = run_post(monkeypatch)
    assert attempts, "the pinned base must reach the real HTTP request path"
    assert outcome == {"result": "replacement"}, repr(outcome)
    assert attempts == [0, 1]
    assert supervisor.events[:4] == ["pin", "release", "retire", "reap"]


@pytest.mark.parametrize(
    "kind",
    ["missing", "mismatch", "transport", "body_loss", "post_dispatch", "busy", "false_integer"],
)
def test_unproven_failure_never_replays(monkeypatch, kind):
    headers = {
        "X-Mempalace-Generation": GENERATION,
        "X-Mempalace-Dispatch": "not-started",
        "X-Mempalace-Reason": "recycling",
    }
    options = {}
    if kind == "missing":
        headers = {}
    elif kind == "mismatch":
        headers["X-Mempalace-Generation"] = REPLACEMENT
    elif kind == "transport":
        options["failure"] = OSError("fixture loss")
    elif kind == "body_loss":
        options["body_failure"] = http.client.IncompleteRead(b"fixture")
    elif kind == "false_integer":
        options["payload"] = {
            "jsonrpc": "2.0",
            "id": 7,
            "error": {
                "code": -32071,
                "data": {"dispatch_started": 0, "generation": GENERATION, "reason": "recycling"},
            },
        }
    elif kind == "post_dispatch":
        headers["X-Mempalace-Dispatch"] = "started"
    elif kind == "busy":
        headers["X-Mempalace-Reason"] = "busy"
        options["payload"] = {
            "jsonrpc": "2.0",
            "id": 7,
            "error": {
                "code": -32071,
                "data": {"dispatch_started": False, "generation": GENERATION, "reason": "busy"},
            },
        }
    outcome, attempts, supervisor = run_post(monkeypatch, headers=headers, **options)
    assert isinstance(outcome, WorkerUnavailable)
    assert attempts == [0]
    assert "reap" not in supervisor.events


def test_draining_success_is_final(monkeypatch):
    outcome, attempts, supervisor = run_post(
        monkeypatch,
        status=200,
        headers={
            "X-Mempalace-Generation": GENERATION,
            "X-Mempalace-State": "DRAINING",
            "X-Mempalace-Admitted": "1",
        },
        payload={"result": "final"},
    )
    assert outcome == {"result": "final"}
    assert attempts == [0]
    assert supervisor.events[-1] == "retire"
    assert supervisor.call_again() == {"result": "replacement"}
    assert attempts == [0, 1]


def test_replacement_pause_preserves_retry_hint(monkeypatch):
    from commonplace.gateway import WorkerPaused
    from commonplace.workers import WorkersPaused

    outcome, attempts, _ = run_post(monkeypatch, replacement_failure=WorkersPaused(67))
    assert isinstance(outcome, WorkerPaused)
    assert outcome.retry_after_s == 67
    assert attempts == [0]
