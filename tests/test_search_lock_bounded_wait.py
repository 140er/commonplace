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
        self.embed_release = threading.Event()  # set: embedding inference may end
        self.embedding = threading.Event()  # set while a request is inside inference
        self.queue_reader_in_u1 = False  # u1 holds the lock until a read is queued
        self.embed_in_write = False  # each write runs a stand-in embedding step
        monkeypatch.setattr(mcp, "_HTTP_REQUEST_LOCK", self.lock)
        monkeypatch.setattr(mcp._provenance, "current", lambda: object())
        monkeypatch.setattr(mcp, "_mcp_tool_preflight_refusal", lambda *a, **k: None)
        monkeypatch.setattr(mcp, "_write_drawer", self._write)
        monkeypatch.setattr(mcp, "handle_request", self._handle)
        # Names the head adds; absent on the base, which then ignores them.
        monkeypatch.setattr(mcp, "_HTTP_READ_LOCK_WAIT_S", BOUND, raising=False)
        monkeypatch.setattr(mcp, "_HTTP_MIRROR_LOCK_WAIT_S", BOUND, raising=False)

    def _inference(self):
        """A stand-in for embedding inference: the worker drops its lock around it."""
        from mempalace.embedding import embedding_section

        with embedding_section():
            self.embedding.set()
            assert self.embed_release.wait(15), "the inference was never released"

    def _write(self, **kw):
        self.write_entered.set()
        if self.slow_write.is_set():
            assert self.release.wait(15), "the stuck write was never released"
        if self.queue_reader_in_u1 and kw["content"] == "u1":
            end = time.monotonic() + 10
            while self.lock._waiting_readers < 1:
                assert time.monotonic() < end, "no read ever queued behind the write"
                time.sleep(0.001)
        if self.embed_in_write:
            self._inference()
        self.written.append(kw["content"])
        return {"success": True, "drawer_id": kw["drawer_id"]}

    def _handle(self, request):
        if request.get("id") == "slow":
            self.read_entered.set()
            assert self.release.wait(15), "the slow read was never released"
        if request.get("id") == "embed":
            self._inference()
        return {"jsonrpc": "2.0", "id": request.get("id"), "result": {"content": []}}

    def writer_waiting(self):
        """True once an index write is parked waiting for the palace to go quiet."""
        lock = self.lock
        return lock._waiting_writers + getattr(lock, "_waiting_polite", 0) >= 1

    def wait_until(self, predicate, what):
        end = time.monotonic() + 10
        while not predicate():
            assert time.monotonic() < end, what
            time.sleep(0.002)

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
    monkeypatch.setattr(palace.mcp, "_HTTP_MIRROR_LOCK_WAIT_S", 30.0, raising=False)
    slow, write, search = {}, {}, {}
    slow_reader = palace.spawn(_search_request("slow"), slow)
    try:
        assert palace.read_entered.wait(5), "the first search never reached the palace"
        writer = palace.spawn(_index_request([_unit(1, "u1")]), write)
        palace.wait_until(palace.writer_waiting, "the index write never started waiting")
        reader = palace.spawn(_search_request("fast"), search)
        reader.join(BOUND + 2.0)
        assert not reader.is_alive(), "a search queued behind the waiting index write"
        assert search["response"]["result"] == {"content": []}
        assert slow_reader.is_alive()  # it was answered while the slow search still ran
    finally:
        palace.release.set()
        slow_reader.join(10)
        writer.join(10)
    # The write only waited for the slow search to end, then wrote its unit whole.
    payload = json.loads(write["response"]["result"]["content"][0]["text"])
    assert payload["success"] is True and len(payload["drawer_ids"]) == 1
    assert palace.written == ["u1"]


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


def test_index_batch_gives_up_when_the_palace_never_goes_quiet_between_units(monkeypatch):
    """The lock taken back between units is bounded too, and the batch ends busy."""
    palace = _palace(monkeypatch)
    palace.queue_reader_in_u1 = True
    batch, slow = {}, {}
    writer = palace.spawn(_index_request([_unit(1, "u1"), _unit(2, "u2"), _unit(3, "u3")]), batch)
    try:
        assert palace.write_entered.wait(5), "the index write never took the lock"
        # A search queues while u1 holds the lock, runs in the hand-off and keeps
        # the palace busy: the write cannot take its lock back between u1 and u2.
        reader = palace.spawn(_search_request("slow"), slow)
        writer.join(BOUND + 2.0)
        assert not writer.is_alive(), "the index batch waited for the lock with no bound"
        error = _error(batch)
        assert error["code"] == -32001 and error["data"]["retryable"] is True
        # The batch stopped at the unit boundary and never wrote without the lock.
        assert palace.written == ["u1"]
    finally:
        palace.release.set()
        reader.join(10)
        writer.join(10)
    # The lock is intact afterwards: a read and an index write both run.
    out, again = {}, {}
    palace.spawn(_search_request("after"), out).join(5)
    assert out["response"]["result"] == {"content": []}
    palace.spawn(_index_request([_unit(9, "u9")]), again).join(5)
    assert palace.written == ["u1", "u9"]


def test_index_write_gives_up_when_it_cannot_take_the_lock_back_after_embedding(monkeypatch):
    palace = _palace(monkeypatch)
    palace.embed_in_write = True
    batch, slow = {}, {}
    writer = palace.spawn(_index_request([_unit(1, "u1"), _unit(2, "u2")]), batch)
    try:
        assert palace.embedding.wait(5), "the write never reached its embedding step"
        # The write dropped its lock for inference; a search takes it and stays.
        reader = palace.spawn(_search_request("slow"), slow)
        assert palace.read_entered.wait(5), "the search never reached the palace"
        palace.embed_release.set()  # inference ends; the lock cannot be taken back
        writer.join(BOUND + 2.0)
        assert not writer.is_alive(), "the index write waited after embedding with no bound"
        error = _error(batch)
        assert error["code"] == -32001 and error["data"]["retryable"] is True
        assert palace.written == []  # the unit was not written without the lock
    finally:
        palace.release.set()
        palace.embed_release.set()
        reader.join(10)
        writer.join(10)
    again = {}
    palace.embed_in_write = False
    palace.spawn(_index_request([_unit(1, "u1")]), again).join(5)
    assert palace.written == ["u1"]  # the lock is intact; a replay writes the unit


def test_search_gives_up_when_it_cannot_take_its_read_back_after_embedding(monkeypatch):
    palace = _palace(monkeypatch)
    search = {}
    reader = palace.spawn(_search_request("embed"), search)
    try:
        assert palace.embedding.wait(5), "the search never reached its embedding step"
        palace.lock.acquire_write()  # a write now holds the palace
        palace.embed_release.set()  # inference ends; the read lease cannot be taken back
        reader.join(BOUND + 2.0)
        assert not reader.is_alive(), "the search waited after embedding with no bound"
        error = _error(search)
        assert error["code"] == -32001 and error["data"]["retryable"] is True
    finally:
        palace.embed_release.set()
        if palace.lock.owns_write():
            palace.lock.release_write()
        reader.join(10)
    # The abandoned lease was not released twice: a write still gets the lock.
    assert palace.lock.acquire_write_polite(1.0)
    palace.lock.release_write()
    assert palace.lock._readers == 0


def test_gateway_keeps_the_workers_busy_answer_retryable(monkeypatch):
    """Internal callers and MCP clients see a retryable busy answer, not a generic failure."""
    import pytest

    from commonplace import gateway
    from mempalace import mcp_server as mcp

    busy = mcp._http_busy_response("x", "try again in a moment")
    client = gateway.WorkerClient(supervisor=None)
    monkeypatch.setattr(client, "post", lambda principal, request: busy)
    with pytest.raises(gateway.WorkerBusy) as raised:
        client.call_tool(object(), "mempalace_search", {"query": "kettle"})
    assert raised.value.retry_after_s == 2

    mediator = gateway.Mediator(db=None, workers=client)

    def busy_call(principal, request):
        raise gateway.WorkerBusy(2)

    monkeypatch.setattr(mediator, "_tools_call", busy_call)
    answer = mediator.handle(
        object(), {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {}}
    )
    assert answer["id"] == 7
    assert answer["error"]["code"] == -32001
    assert answer["error"]["message"].startswith("Memory is busy indexing")
    assert answer["error"]["data"] == {"retryable": True, "retry_after_s": 2}
    # Another unavailable worker keeps its own, non-retryable answer.
    monkeypatch.setattr(client, "post", lambda principal, request: {"error": {"code": -32603}})
    with pytest.raises(gateway.WorkerUnavailable) as other:
        client.call_tool(object(), "mempalace_search", {})
    assert not isinstance(other.value, gateway.WorkerBusy)
