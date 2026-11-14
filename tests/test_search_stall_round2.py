"""A search answers within a small bound while memory is paused for a backup or starting.

A backup pauses the memory workers for as long as it copies their files (minutes on a large
palace). A search used to wait for the pause once per worker call, up to 20 s each, and a search
makes several calls: it ran into its client's 60 s limit and then answered a generic refusal.
The same holds while a worker is starting. The search-index job counted every such refusal as a
failure and backed off for up to a minute.
"""

import threading
import time

import pytest

from commonplace import gateway as gateway_module
from commonplace.handoff import search_index
from tests._commonplace_helpers import functional_worker_transport, hub_fixture  # noqa: F401
from tests.test_commonplace_memory import _http, _save
from tests.test_search_index_sweep import put, world  # noqa: F401

PATH = "/api/memory/search?q=Paused%20kettle%20canary"


def test_search_during_a_backup_pause_answers_retryable_503_within_the_request_budget(
    hub, monkeypatch
):
    owner = hub.owner_token()
    memory_id = _save(hub, owner, "Paused kettle canary", theme="shared")
    monkeypatch.setenv("COMMONPLACE_MEMORY_WAIT_S", "0.6")
    hub.supervisor.pause_wait_s = 3.0  # each worker call used to wait this long for resume
    hub.supervisor.pause()
    try:
        started = time.monotonic()
        status, body = _http(hub, owner, PATH)
        waited = time.monotonic() - started
    finally:
        hub.supervisor.resume()
    assert waited < 2.0, f"the search waited {waited:.1f}s on the pause"
    assert status == 503, (status, body)
    assert body["error"].startswith("no: memory is paused for a backup; try again in about ")
    assert body["retry_after_s"] >= 1 and "warming_up" not in body
    # The memory is intact: once the pause ends the same search returns it.
    status, ready = _http(hub, owner, PATH)
    assert status == 200 and [m["id"] for m in ready["results"]] == [memory_id]


def test_search_while_the_worker_starts_answers_warming_up_not_a_refusal(hub, monkeypatch):
    owner = hub.owner_token()
    memory_id = _save(hub, owner, "Paused kettle canary", theme="shared")
    monkeypatch.setenv("COMMONPLACE_MEMORY_WAIT_S", "0.6")
    hub.supervisor.stop_all()  # the next call starts a worker
    release = threading.Event()
    inner = hub.supervisor.launcher

    class SlowStart:
        def start(self, workspace_id, workspace_dir):
            assert release.wait(20), "the slow start was never released"
            return inner.start(workspace_id, workspace_dir)

    hub.supervisor.launcher = SlowStart()
    out = {}

    def search():
        out["status"], out["body"] = _http(hub, owner, PATH)

    thread = threading.Thread(target=search, daemon=True)
    thread.start()
    try:
        thread.join(3.0)
        assert not thread.is_alive(), "the search waited for the worker to start with no bound"
        assert out["status"] == 503, out
        assert out["body"]["warming_up"] is True and out["body"]["retry_after_s"] >= 1
        # A second search meets the same start and is told the same, at once.
        started = time.monotonic()
        status, body = _http(hub, owner, PATH)
        assert status == 503 and time.monotonic() - started < 3.0
    finally:
        release.set()
    # The start went on in the background: the next search finds the worker and answers.
    deadline = time.monotonic() + 20
    while True:
        status, ready = _http(hub, owner, PATH)
        if status == 200 or time.monotonic() > deadline:
            break
        time.sleep(0.2)
    assert status == 200 and [m["id"] for m in ready["results"]] == [memory_id]


def test_index_job_is_not_failed_or_backed_off_by_a_backup_pause(world):  # noqa: F811
    put(world, source="import:paused", count=3)
    refusals = []

    def paused(principal, name, args):
        refusals.append(name)
        raise gateway_module.WorkerPaused(30)

    world.service.call_tool = paused
    state = search_index.State()
    before = state.after_id
    assert search_index.sweep(world.service, world.ws, state) is True
    assert refusals, "the sweep never reached the worker"
    assert state.failures == 0, "a backup pause was counted as an index failure"
    assert state.after_id == before, "the thread was passed over instead of retried first"
    assert 3.0 <= search_index.next_pause(state) <= 30.0  # the worker's hint, not a backoff
    assert search_index.next_pause(state) == search_index.PAUSE_SECONDS * (2**0)  # hint is spent


@pytest.mark.parametrize("kind", ["WorkerBusy", "WorkerStarting"])
def test_index_job_treats_busy_and_starting_like_a_pause(world, kind):  # noqa: F811
    busy = getattr(gateway_module, kind)()
    put(world, source="import:busy", count=3)

    def refuse(principal, name, args):
        raise busy

    world.service.call_tool = refuse
    state = search_index.State()
    assert search_index.sweep(world.service, world.ws, state) is True
    assert state.failures == 0
