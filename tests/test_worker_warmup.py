"""A gateway-launched worker warms its palace up in the background and says so.

The first search used to open the client and collection, load the embedder and
the vector segment, and read the lexical index on the request path, and the
capacity verdict it recorded was invalid again by the second search. The worker
now pays that once, off the request path, and a search that arrives meanwhile is
told. The capacity-probe reuse that makes later searches cheap is covered in
``test_hnsw_capacity``.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from types import SimpleNamespace

import pytest

from commonplace.workers import SubprocessLauncher, WorkerSupervisor
from mempalace import mcp_server, provenance


@pytest.fixture
def worker_state(monkeypatch):
    monkeypatch.setattr(mcp_server, "_worker_warmup_state", "off")
    return lambda: mcp_server._worker_warmup_state


@pytest.fixture
def steps(monkeypatch):
    """Record the order of the palace work the warm-up does."""
    calls: list = []
    monkeypatch.setattr(mcp_server, "_backend_db_exists", lambda: True)
    monkeypatch.setattr(mcp_server, "_vector_disabled", False)

    def refresh():
        calls.append("probe")

    def search(query, **kwargs):
        calls.append(("search", query, kwargs))
        return {"results": []}

    monkeypatch.setattr(mcp_server, "_refresh_vector_disabled_flag", refresh)
    monkeypatch.setattr(mcp_server, "search_memories", search)
    return calls


def test_warmup_runs_the_search_path_between_two_capacity_probes(steps, worker_state):
    mcp_server._warm_worker_for_search()

    assert [step if isinstance(step, str) else step[0] for step in steps] == [
        "probe",
        "search",
        "probe",
    ]
    _, query, kwargs = steps[1]
    assert query == mcp_server._WARMUP_PROBE_TEXT
    assert kwargs["n_results"] == 1 and kwargs["include_facts"] is False
    assert worker_state() == "done"


def test_warmup_never_opens_a_diverged_index_through_chroma(steps, worker_state, monkeypatch):
    monkeypatch.setattr(mcp_server, "_vector_disabled", True)

    mcp_server._warm_worker_for_search()

    assert steps[1][2]["vector_disabled"] is True
    assert worker_state() == "done"


def test_warmup_leaves_a_missing_palace_alone(steps, worker_state, monkeypatch):
    monkeypatch.setattr(mcp_server, "_backend_db_exists", lambda: False)

    mcp_server._warm_worker_for_search()

    assert steps == []
    assert worker_state() == "done"


def test_warmup_failure_is_reported_and_never_raised(steps, worker_state, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(mcp_server, "search_memories", boom)

    mcp_server._warm_worker_for_search()

    assert worker_state() == "failed"


def test_warmup_starts_in_the_background_and_is_running_at_once(worker_state, monkeypatch):
    release, entered = threading.Event(), threading.Event()

    def slow():
        entered.set()
        release.wait(10)

    monkeypatch.setattr(mcp_server, "_warm_worker_for_search", slow)

    started = time.monotonic()
    mcp_server._start_worker_warmup()
    try:
        assert time.monotonic() - started < 2, "start must not wait for the warm-up"
        assert worker_state() == "running"
        assert entered.wait(5)
    finally:
        release.set()


@pytest.mark.parametrize(("state", "flagged"), [("running", True), ("done", False), ("off", False)])
def test_search_result_is_flagged_only_while_warming(state, flagged, monkeypatch):
    monkeypatch.setattr(mcp_server, "_worker_warmup_state", state)
    monkeypatch.setattr(mcp_server, "_refresh_vector_disabled_flag", lambda: None)
    monkeypatch.setattr(mcp_server, "_vector_disabled", False)
    monkeypatch.setattr(mcp_server, "search_memories", lambda *a, **k: {"results": [{"x": 1}]})

    result = mcp_server.tool_search("kettle", include_facts=False)

    assert result["results"] == [{"x": 1}]
    assert result.get("warming_up") is (True if flagged else None)


def test_status_payload_reports_the_warmup_state(monkeypatch):
    monkeypatch.setattr(mcp_server, "_worker_warmup_state", "running")
    httpd = SimpleNamespace(
        stats_lock=threading.Lock(),
        recent_clients={},
        request_count=0,
        status_counts={},
        bind_host="127.0.0.1",
        server_address=("127.0.0.1", 1),
        started_at="2026-11-14T00:00:00Z",
        started_monotonic=time.monotonic(),
        scheme="http",
    )

    assert mcp_server._http_status_payload(httpd)["warmup"] == {"state": "running"}


class _SkewedClock:
    """``time`` with a monotonic clock a test can move; everything else is real."""

    def __init__(self):
        self.now = 1_000.0

    def monotonic(self):
        return self.now

    def __getattr__(self, name):
        return getattr(time, name)


def test_searches_spaced_apart_through_the_tool_do_not_repeat_the_capacity_probe(
    monkeypatch, config, kg, seeded_collection
):
    """The card's symptom: every search more than 10 s after the last paid the probe again.

    The first search's own collection open writes to the database, so the verdict
    it recorded is invalid once; after that an untouched palace stays probed no
    matter how long the gaps are.
    """
    from _mcp_server_helpers import _patch_mcp_server

    from mempalace.backends import chroma as chroma_mod

    _patch_mcp_server(monkeypatch, config, kg)
    clock = _SkewedClock()
    monkeypatch.setattr(chroma_mod, "time", clock)
    runs: list = []
    real = chroma_mod._hnsw_capacity_status_uncached

    def counting(*args, **kwargs):
        runs.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(chroma_mod, "_hnsw_capacity_status_uncached", counting)

    def search():
        found = mcp_server.tool_search("authentication tokens", include_facts=False)
        assert found["results"], found

    search()
    search()
    settled = len(runs)
    for gap in (11.0, 120.0, 3_600.0):
        clock.now += gap
        search()
    assert len(runs) == settled, "a search spaced apart re-ran the capacity probe"


def _statusz(worker):
    identity = {
        "user": "u",
        "connection": "c",
        "client": "t",
        "auth": "token",
        "trust": "trusted",
        "workspace": "ws_warm",
    }
    request = urllib.request.Request(
        f"http://{worker.host}:{worker.port}/statusz",
        headers={
            "Authorization": f"Bearer {worker.token}",
            "Host": worker.host_header,
            provenance.IDENTITY_HEADER: provenance.encode_identity(identity),
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def _wait_for_warmup(worker, timeout_s=120):
    deadline = time.monotonic() + timeout_s
    state = None
    while time.monotonic() < deadline:
        state = _statusz(worker)["warmup"]["state"]
        # "off" is final: a launched worker is "running" before it binds.
        if state != "running":
            return state
        time.sleep(0.25)
    return state


def test_a_real_gateway_worker_warms_up_its_palace(tmp_path):
    """The launcher turns the warm-up on, and it finishes on a real palace."""
    import chromadb

    supervisor = WorkerSupervisor(
        str(tmp_path / "data"), launcher=SubprocessLauncher(startup_timeout_s=120)
    )
    palace = supervisor.workspace_dir("ws_warm") / "palace"
    palace.mkdir(parents=True)
    client = chromadb.PersistentClient(path=str(palace))
    drawers = client.get_or_create_collection(
        "mempalace_drawers", metadata={"hnsw:space": "cosine"}
    )
    drawers.add(
        ids=["a", "b"],
        documents=["Kettle canary one", "Kettle canary two"],
        metadatas=[{"wing": "shared", "room": "r"}, {"wing": "shared", "room": "r"}],
    )
    client.close()

    worker = supervisor.get("ws_warm")
    try:
        assert _wait_for_warmup(worker) == "done"
    finally:
        supervisor.stop_all()
