"""A search never sends the worker work its caller cannot wait for, and asks a busy worker once.

Three live findings: every initialize sent a memory search to the worker that the startup
context (80 ms) abandoned, the worker ran it to the end and the abandoned searches piled up;
a worker that answered busy was asked again by each of the request's later calls, so a busy
answer took 13 to 15 s instead of the request budget; and nothing said why the index job's
writes were refused.
"""

import logging
from types import SimpleNamespace

import pytest

from commonplace import gateway as gateway_module
from commonplace.rules import checks
from commonplace.workers import memory_wait
from mempalace import mcp_server

PRINCIPAL = SimpleNamespace(workspace_id="ws_x", identity=lambda: {})


class _Supervisor:
    def __init__(self):
        self.touched = 0

    def call(self, wait_s=None):
        self.touched += 1
        raise RuntimeError("the worker was asked")


def _search_request():
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "mempalace_search", "arguments": {"query": "kettle"}},
    }


def test_a_call_on_a_deadline_too_short_to_finish_is_not_sent_to_the_worker():
    supervisor = _Supervisor()
    client = gateway_module.WorkerClient(supervisor)
    with checks.deadline(0.08):  # the startup context's whole budget
        with pytest.raises(TimeoutError):
            client.post(PRINCIPAL, _search_request())
    assert supervisor.touched == 0, "a search the caller had given up on reached the worker"


def test_a_call_with_time_left_is_still_sent():
    supervisor = _Supervisor()
    client = gateway_module.WorkerClient(supervisor)
    with checks.deadline(5.0):
        with pytest.raises(RuntimeError):
            client.post(PRINCIPAL, _search_request())
    assert supervisor.touched == 1


def test_a_busy_answer_is_asked_once_per_request(monkeypatch):
    client = gateway_module.WorkerClient(_Supervisor())
    busy = mcp_server._http_busy_response(1, "try again in a moment")
    asked = []

    def ask(principal, request, pause_wait_s=None):
        asked.append(request["id"])
        return busy

    monkeypatch.setattr(client, "_post_to_worker", ask)
    with memory_wait(5.0):
        assert client.post(PRINCIPAL, _search_request()) == busy
        with pytest.raises(gateway_module.WorkerBusy):
            client.post(PRINCIPAL, _search_request())
        with pytest.raises(gateway_module.WorkerBusy):
            client.post(PRINCIPAL, _search_request())
    assert asked == [1], "the worker was asked again after it said busy"
    # A new request starts clean.
    with memory_wait(5.0):
        assert client.post(PRINCIPAL, _search_request()) == busy
    assert asked == [1, 1]


def test_a_refused_index_write_says_why(monkeypatch, caplog):
    client = gateway_module.WorkerClient(_Supervisor())
    refusal = {
        "jsonrpc": "2.0",
        "id": "x",
        "error": {"code": -32002, "message": "HNSW index diverged from SQLite; repair it"},
    }
    monkeypatch.setattr(client, "post", lambda principal, request: refusal)
    with caplog.at_level(logging.WARNING, logger="commonplace.gateway"):
        with pytest.raises(gateway_module.WorkerUnavailable):
            client.call_tool(PRINCIPAL, "write_mirror_units", {"thread_id": "t", "units": []})
    assert "worker refused an index write: code=-32002 message=HNSW index diverged" in caplog.text
