"""Search stays interactive while the search-index backfill runs.

The backfill writes through the gateway's worker client. A synthetic worker
serializes every call on one slot, as a saturated worker does, so any index
write that runs during a search shows up as an overlap and as latency.
"""

import hashlib
import json
import threading
import time
from types import SimpleNamespace

import pytest

from commonplace import continuity_schema, foreground_load
from commonplace.control_db import ControlDB
from commonplace.gateway import WorkerClient, WorkerUnavailable
from commonplace.handoff import search_index
from commonplace.handoff.models import ThreadMessage
from commonplace.handoff.service import HandoffService
from commonplace.handoff.thread_store import ThreadStoreV2
from tests._mirror_drain import drain_service

WRITE_S = 0.06
SEARCH_S = 0.3
TICK = 0.005


class SyntheticWorker:
    """One shared CPU: calls that run together each progress at 1/n speed."""

    def __init__(self):
        self.cond = threading.Condition()
        self.active = 0
        self.writes = []  # (start, end) of every index write
        self.calls = 0
        self.fail_first = False
        self.first_write = threading.Event()

    def work(self, seconds):
        with self.cond:
            self.active += 1
        done, last = 0.0, time.monotonic()
        try:
            while done < seconds:
                time.sleep(TICK)
                now = time.monotonic()
                with self.cond:
                    sharing = self.active
                done += (now - last) / sharing
                last = now
        finally:
            with self.cond:
                self.active -= 1

    def __call__(self, worker, principal, request):
        method = request["method"]
        if method == "internal/write_mirror_units":
            self.calls += 1
            if self.fail_first and self.calls == 1:
                raise WorkerUnavailable()
            units = request["params"]["units"]
            start = time.monotonic()
            self.work(WRITE_S)
            self.writes.append((start, time.monotonic()))
            self.first_write.set()
            ids = [
                hashlib.sha256(json.dumps([request["params"]["thread_id"], u]).encode()).hexdigest()
                for u in units
            ]
            body = {"success": True, "drawer_ids": ids}
        elif request["params"]["name"] == "mempalace_list_drawers":
            body = {"drawers": [], "total": 0}
        else:
            self.work(SEARCH_S)
            body = {"results": []}
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"content": [{"type": "text", "text": json.dumps(body)}]},
        }


@pytest.fixture
def world(tmp_path, monkeypatch):
    db = ControlDB(str(tmp_path / "control.db"))
    continuity_schema.ensure_schema(db)
    user = db.create_user("owner@example.test")
    ws = db.create_workspace("synthetic", user)
    store = ThreadStoreV2(str(tmp_path / "threads.v2.sqlite3"))
    synthetic = SyntheticWorker()
    supervisor = SimpleNamespace(get=lambda _ws: SimpleNamespace(retiring=False))
    client = WorkerClient(supervisor)
    monkeypatch.setattr(client, "_post", synthetic)
    service = HandoffService(
        control_db=db,
        handoff_db=SimpleNamespace(set_document_store=lambda _: None),
        settings=None,
        store_for=lambda _: store,
        events=None,
        call_tool=client.call_tool,
    )
    monkeypatch.setenv("COMMONPLACE_PLANS", "off")
    monkeypatch.delenv("COMMONPLACE_SEARCH_INDEX", raising=False)
    monkeypatch.setattr(search_index, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(search_index, "MAX_THREADS", 3)
    monkeypatch.setattr(foreground_load, "REST_RATIO", 2.0, raising=False)
    principal = SimpleNamespace(workspace_id=ws)
    yield SimpleNamespace(
        db=db,
        ws=ws,
        store=store,
        worker=synthetic,
        client=client,
        service=service,
        principal=principal,
        searches=[],
    )
    service.close()
    store.close()
    db.close()


def put(world, count):
    for i in range(count):
        world.store.store(
            world.ws,
            f"import:{i}",
            [ThreadMessage(0, "user", "message", f"synthetic words {i}", "{}")],
            actor="synthetic",
        )


def search(world):
    start = time.monotonic()
    world.client.post(
        world.principal,
        {
            "jsonrpc": "2.0",
            "id": "s",
            "method": "tools/call",
            "params": {"name": "mempalace_search"},
        },
    )
    end = time.monotonic()
    world.searches.append((start, end))
    return end - start


def test_index_writes_do_not_start_while_a_search_runs(world):
    put(world, 14)
    world.service.index_later(world.ws)
    assert world.worker.first_write.wait(10)
    latencies = []
    for _ in range(4):
        latencies.append(search(world))
        time.sleep(0.05)
    drain_service(world.service)
    print(
        f"search latency under index load: max={max(latencies):.3f}s all={[round(x, 3) for x in latencies]}"
    )
    started_inside = [w for w in world.worker.writes for s, e in world.searches if s < w[0] < e]
    # An index write that began mid-search would queue the search behind it.
    assert len(started_inside) <= 1, started_inside
    # A search waits for at most the one write already running.
    assert max(latencies) < SEARCH_S + WRITE_S + 0.05, latencies
    # The backfill still finishes: nothing is dropped, only paced.
    assert len(world.worker.writes) == 14
    assert world.service.index_status(world.ws)["threads_pending"] == 0


def test_index_rests_between_writes_in_proportion_to_their_cost(world):
    put(world, 6)
    world.service.index_later(world.ws)
    drain_service(world.service)
    writes = sorted(world.worker.writes)
    assert len(writes) == 6
    gaps = [b[0] - a[1] for a, b in zip(writes, writes[1:])]
    # Idle worker, no search: still about two write-times of rest after each write.
    assert min(gaps) >= 0.8 * 2.0 * WRITE_S, gaps


def test_a_failed_write_stops_the_sweep_and_backs_off(world, monkeypatch):
    put(world, 3)
    world.worker.fail_first = True
    state = search_index.State()
    assert search_index.sweep(world.service, world.ws, state) is True
    # The next thread is not hammered after the worker refused.
    assert world.worker.calls == 1
    assert state.failures == 1
    monkeypatch.setattr(search_index, "PAUSE_SECONDS", 1.0)
    assert search_index.next_pause(state) == 2.0
    # A later clean sweep resets the backoff and indexes everything.
    for _ in range(2):  # the pinned range is replayed on the pass after it failed
        while search_index.sweep(world.service, world.ws, state):
            pass
    assert state.failures == 0
    assert world.service.index_status(world.ws)["threads_pending"] == 0


def test_defaults_keep_one_sweep_small():
    assert search_index.MAX_THREADS <= 4
    assert search_index.MAX_UNITS <= 256
    assert search_index.PAUSE_SECONDS >= 2
    assert foreground_load.REST_RATIO >= 1


def test_env_settings_are_clamped_and_fall_back(monkeypatch):
    monkeypatch.setenv("X_N", "7")
    assert search_index._env_number("X_N", 3, 1, 50) == 7
    monkeypatch.setenv("X_N", "500")
    assert search_index._env_number("X_N", 3, 1, 50) == 50
    monkeypatch.setenv("X_N", "nope")
    assert search_index._env_number("X_N", 3, 1, 50) == 3
    monkeypatch.setenv("X_N", "0.5")
    assert search_index._env_number("X_N", 3.0, 0.0, 9.0, float) == 0.5


# ---- worker side: priority and transient read errors -----------------------


def _install(monkeypatch, mcp, handle):
    monkeypatch.setattr(mcp, "_HTTP_REQUEST_LOCK", mcp._RWLock())
    monkeypatch.setattr(mcp._provenance, "current", lambda: object())
    monkeypatch.setattr(mcp, "_mcp_tool_preflight_refusal", lambda *a, **k: None)
    monkeypatch.setattr(
        mcp, "_write_drawer", lambda **kw: {"success": True, "drawer_id": kw["drawer_id"]}
    )
    monkeypatch.setattr(mcp, "handle_request", handle)
    monkeypatch.setattr(mcp, "_HTTP_READ_RETRY_DELAYS", (0.001, 0.001, 0.001), raising=False)


def _run_in_thread(fn):
    out = {}

    def target():
        out["value"] = fn()

    thread = threading.Thread(target=target)
    thread.start()
    thread.join(10)
    return out["value"]


def _search_request():
    return {
        "jsonrpc": "2.0",
        "id": "r",
        "method": "tools/call",
        "params": {"name": "mempalace_search", "arguments": {"query": "x"}},
    }


def test_search_read_survives_a_transient_busy_error(monkeypatch):
    from mempalace import mcp_server as mcp

    attempts = []

    def handle(request):
        attempts.append(1)
        if len(attempts) < 3:
            return {
                "jsonrpc": "2.0",
                "id": "r",
                "error": {
                    "code": -32000,
                    "message": "Internal tool error",
                    "data": {"error_class": "OperationalError"},
                },
            }
        return {"jsonrpc": "2.0", "id": "r", "result": {"content": []}}

    _install(monkeypatch, mcp, handle)
    response = _run_in_thread(lambda: mcp._http_dispatch(_search_request()))
    assert "error" not in response, response
    assert len(attempts) == 3


def test_search_read_gives_up_after_a_bounded_number_of_retries(monkeypatch):
    from mempalace import mcp_server as mcp

    attempts = []

    def handle(request):
        attempts.append(1)
        return {
            "jsonrpc": "2.0",
            "id": "r",
            "error": {"code": -32000, "message": "x", "data": {"error_class": "OperationalError"}},
        }

    _install(monkeypatch, mcp, handle)
    response = _run_in_thread(lambda: mcp._http_dispatch(_search_request()))
    assert "error" in response
    assert len(attempts) == 4  # the first call plus three bounded retries


def test_other_read_errors_are_not_retried(monkeypatch):
    from mempalace import mcp_server as mcp

    attempts = []

    def handle(request):
        attempts.append(1)
        return {
            "jsonrpc": "2.0",
            "id": "r",
            "error": {"code": -32000, "message": "x", "data": {"error_class": "ValueError"}},
        }

    _install(monkeypatch, mcp, handle)
    _run_in_thread(lambda: mcp._http_dispatch(_search_request()))
    assert len(attempts) == 1


def test_mirror_write_runs_at_lower_priority_in_its_request_thread(monkeypatch):
    import os

    from mempalace import mcp_server as mcp

    calls = []
    monkeypatch.setattr(os, "setpriority", lambda which, who, value: calls.append((which, value)))
    monkeypatch.delenv("MEMPALACE_MIRROR_NICE", raising=False)
    _install(monkeypatch, mcp, lambda request: {})
    unit = {
        "seq_start": 1,
        "seq_end": 1,
        "part": 0,
        "message_class": "conversation",
        "text": "u1",
        "index_spans": None,
    }
    request = {
        "jsonrpc": "2.0",
        "id": "w",
        "method": "internal/write_mirror_units",
        "params": {"thread_id": "t-synth", "units": [unit]},
    }
    _run_in_thread(lambda: mcp._http_dispatch(request))
    assert calls == [(os.PRIO_PROCESS, 10)]
    calls.clear()
    monkeypatch.setenv("MEMPALACE_MIRROR_NICE", "0")
    _run_in_thread(lambda: mcp._http_dispatch(request))
    assert calls == []
