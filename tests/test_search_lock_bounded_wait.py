"""A search never waits on a background index write for more than a small bound.

The worker's request lock is shared by palace reads and exclusive for writes. The
search-index backfill writes through it (``internal/write_mirror_units``). These
tests hold that lock the way a slow index write does and run a search against it.
"""

import json
import threading
import time

BOUND = 0.4  # the shortened read/write lock wait the tests configure, in seconds


def _unit(seq, text):
    return {
        "seq_start": seq,
        "seq_end": seq,
        "part": 0,
        "message_class": "conversation",
        "text": text,
        "index_spans": None,
    }


def _search_request(request_id="s"):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": "mempalace_search", "arguments": {"query": "kettle"}},
    }


def _index_request(units):
    return {
        "jsonrpc": "2.0",
        "id": "w",
        "method": "internal/write_mirror_units",
        "params": {"thread_id": "t-synth", "units": units},
    }


class _Palace:
    """The worker's dispatch with a stand-in write and read behind the real lock."""

    def __init__(self, monkeypatch):
        from mempalace import mcp_server as mcp

        self.mcp = mcp
        self.lock = mcp._RWLock()
        self.written = []
        self.slow_write = threading.Event()  # set: the write is stuck holding the lock
        self.write_entered = threading.Event()
        self.slow_read = threading.Event()  # set: reads named "slow" are stuck in the palace
        self.read_entered = threading.Event()
        self.release = threading.Event()
        monkeypatch.setattr(mcp, "_HTTP_REQUEST_LOCK", self.lock)
        monkeypatch.setattr(mcp._provenance, "current", lambda: object())
        monkeypatch.setattr(mcp, "_mcp_tool_preflight_refusal", lambda *a, **k: None)
        monkeypatch.setattr(mcp, "_write_drawer", self._write)
        monkeypatch.setattr(mcp, "handle_request", self._handle)
        # Names the head adds; absent on the base, which then ignores them.
        monkeypatch.setattr(mcp, "_HTTP_READ_LOCK_WAIT_S", BOUND, raising=False)
        monkeypatch.setattr(mcp, "_HTTP_MIRROR_LOCK_WAIT_S", BOUND, raising=False)

    def _write(self, **kw):
        self.write_entered.set()
        if self.slow_write.is_set():
            assert self.release.wait(15), "the stuck write was never released"
        self.written.append(kw["content"])
        return {"success": True, "drawer_id": kw["drawer_id"]}

    def _handle(self, request):
        if request.get("id") == "slow":
            self.read_entered.set()
            assert self.release.wait(15), "the slow read was never released"
        return {"jsonrpc": "2.0", "id": request.get("id"), "result": {"content": []}}

    def spawn(self, request, out):
        def run():
            started = time.monotonic()
            try:
                out["response"] = self.mcp._http_dispatch(request)
            except BaseException as exc:  # surfaced on the test thread
                out["error"] = exc
            out["elapsed"] = time.monotonic() - started

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread


def _palace(monkeypatch):
    return _Palace(monkeypatch)


def _error(out):
    response = out["response"]
    assert "error" in response, response
    return response["error"]


def test_search_answers_within_a_bound_while_an_index_write_holds_the_lock(monkeypatch):
    palace = _palace(monkeypatch)
    palace.slow_write.set()
    write, search = {}, {}
    writer = palace.spawn(_index_request([_unit(1, "u1"), _unit(2, "u2")]), write)
    try:
        assert palace.write_entered.wait(5), "the index write never took the lock"
        reader = palace.spawn(_search_request(), search)
        reader.join(BOUND + 2.0)
        assert not reader.is_alive(), "the search is still waiting on the index write"
        assert search["elapsed"] < BOUND + 2.0
        error = _error(search)
        assert error["code"] == -32001
        assert error["data"]["retryable"] is True
        assert "indexing" in error["message"].lower()
    finally:
        palace.release.set()
        writer.join(10)
    assert not writer.is_alive()
    # Nothing was dropped: the stuck write finished and its batch is whole.
    assert palace.written == ["u1", "u2"]


def test_search_is_not_queued_behind_a_waiting_index_write(monkeypatch):
    palace = _palace(monkeypatch)
    slow, write, search = {}, {}, {}
    slow_reader = palace.spawn(_search_request("slow"), slow)
    try:
        assert palace.read_entered.wait(5), "the first search never reached the palace"
        writer = palace.spawn(_index_request([_unit(1, "u1")]), write)
        time.sleep(0.3)  # the index write is now waiting for the slow search to end
        reader = palace.spawn(_search_request("fast"), search)
        reader.join(BOUND + 2.0)
        assert not reader.is_alive(), "a search queued behind the waiting index write"
        assert search["response"]["result"] == {"content": []}
        assert slow_reader.is_alive()  # it was answered while the slow search still ran
    finally:
        palace.release.set()
        slow_reader.join(10)
        writer.join(10)
    assert palace.written == ["u1"] or "error" in write["response"]


def test_index_write_gives_way_to_readers_and_answers_busy_not_forever(monkeypatch):
    palace = _palace(monkeypatch)
    slow, write = {}, {}
    slow_reader = palace.spawn(_search_request("slow"), slow)
    try:
        assert palace.read_entered.wait(5), "the search never reached the palace"
        writer = palace.spawn(_index_request([_unit(1, "u1")]), write)
        writer.join(BOUND + 2.0)
        assert not writer.is_alive(), "the index write waited on a reader with no bound"
        error = _error(write)
        assert error["data"]["retryable"] is True
        assert palace.written == []  # nothing was written; a retry replays it whole
    finally:
        palace.release.set()
        slow_reader.join(10)


def test_index_write_runs_when_no_search_is_running(monkeypatch):
    palace = _palace(monkeypatch)
    write = {}
    palace.spawn(_index_request([_unit(1, "u1"), _unit(2, "u2")]), write).join(10)
    payload = json.loads(write["response"]["result"]["content"][0]["text"])
    assert payload["success"] is True and len(payload["drawer_ids"]) == 2
    assert palace.written == ["u1", "u2"]
    # The lock is free again for the next search.
    out = {}
    palace.spawn(_search_request(), out).join(5)
    assert out["response"]["result"] == {"content": []}
