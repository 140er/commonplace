"""A search never waits for the capacity probe because its last verdict is ten seconds old.

The probe is O(palace): an embeddings COUNT and an unpickle of the whole segment metadata
(about 3.7 s on the live palace). It re-ran for every search spaced more than the reuse
ceiling apart, so every search paid it. A search now takes the last verdict at once while
its files are unchanged, the probe runs again behind it, and an unchanged pickle is not
unpickled again.
"""

import os
import threading
import time
from types import SimpleNamespace

import pytest

from mempalace import mcp_server as mcp
from mempalace.backends import chroma as chroma_mod
from tests.test_hnsw_capacity import COLLECTION, _seed_chroma_db, _write_pickle

SLOW = 1.0  # seconds the stand-in probe takes


def _fine_stamps(palace):
    """Timestamps that prove fine resolution, as an ext4 volume's do."""
    ns = (int(time.time()) - 5) * 1_000_000_000 + 123_456_789
    for root, _dirs, names in os.walk(palace):
        for name in names:
            if name == "chroma.sqlite3" or name.endswith(".pickle"):
                os.utime(os.path.join(root, name), ns=(ns, ns))


@pytest.fixture
def palace(tmp_path, monkeypatch):
    """A balanced palace (no standing gap), a slow counting probe and a movable clock."""
    _seed_chroma_db(str(tmp_path), sqlite_count=20_000, segment_id="seg-swr")
    _write_pickle(str(tmp_path), "seg-swr", hnsw_count=20_000)
    _fine_stamps(str(tmp_path))
    runs = []
    real = chroma_mod._hnsw_capacity_status_uncached

    def slow_probe(palace_path, collection_name="mempalace_drawers"):
        runs.append(time.perf_counter())
        time.sleep(SLOW)
        return real(palace_path, collection_name)

    monkeypatch.setattr(chroma_mod, "_hnsw_capacity_status_uncached", slow_probe)
    clock = [1_000.0]
    monkeypatch.setattr(chroma_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        mcp, "_config", SimpleNamespace(palace_path=str(tmp_path), collection_name=COLLECTION)
    )
    monkeypatch.setattr(mcp, "_is_chroma_backend", lambda: True)
    return SimpleNamespace(path=str(tmp_path), runs=runs, clock=clock)


def _search_dispatch():
    """One worker search through the real dispatch; the search itself is a stand-in."""
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "mempalace_search", "arguments": {"query": "kettle"}},
    }
    started = time.perf_counter()
    mcp._http_dispatch(request)
    return time.perf_counter() - started


@pytest.fixture(autouse=True)
def stand_in_search(monkeypatch):
    def handle(request):
        mcp._refresh_vector_disabled_flag()
        return {"jsonrpc": "2.0", "id": request.get("id"), "result": {"content": []}}

    monkeypatch.setattr(mcp, "handle_request", handle)
    monkeypatch.setattr(mcp._provenance, "current", lambda: object())


def test_search_after_the_ceiling_answers_from_the_last_verdict_and_probes_behind_it(palace):
    first = _search_dispatch()
    assert first >= SLOW * 0.9, "the first search has no verdict and must probe"
    assert len(palace.runs) == 1

    palace.clock[0] += chroma_mod._CAPACITY_FINE_STAMP_MAX_AGE_SECONDS + 5  # past the ceiling
    second = _search_dispatch()
    assert second < 0.4, f"the search waited {second:.2f}s for a probe of unchanged files"
    assert chroma_mod.wait_for_capacity_refresh(10), "the background probe never finished"
    assert len(palace.runs) == 2, "the verdict was not refreshed behind the search"

    # The refreshed verdict is the one the next search uses, again without waiting.
    palace.clock[0] += 5
    assert _search_dispatch() < 0.4
    assert len(palace.runs) == 2


def test_a_changed_file_still_forces_the_probe_in_the_call(palace):
    _search_dispatch()
    palace.clock[0] += 400
    _write_pickle(palace.path, "seg-swr", hnsw_count=2_000)  # the flush moved the pickle
    started = time.perf_counter()
    status = chroma_mod.hnsw_capacity_status(palace.path, COLLECTION)
    with chroma_mod.serve_stale_capacity_verdicts():
        status = chroma_mod.hnsw_capacity_status(palace.path, COLLECTION)
    assert status["diverged"] is True, "a stale verdict hid a changed index"
    assert len(palace.runs) >= 2
    assert time.perf_counter() - started >= SLOW * 0.9


def test_one_background_probe_at_a_time(palace):
    _search_dispatch()
    palace.clock[0] += 400
    threads = [threading.Thread(target=_search_dispatch) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert chroma_mod.wait_for_capacity_refresh(10)
    assert len(palace.runs) == 2, "searches started more than one background probe"


def test_a_verdict_that_is_not_ok_is_never_served_stale(palace):
    _write_pickle(palace.path, "seg-swr", hnsw_count=2_000)
    _fine_stamps(palace.path)
    first = _search_dispatch()
    assert first >= SLOW * 0.9
    palace.clock[0] += 400
    # "diverged" stays a synchronous answer: the search waits for the fresh verdict.
    assert _search_dispatch() >= SLOW * 0.9
    assert len(palace.runs) == 2


def test_the_clock_dependent_verdict_is_not_served_near_the_moment_it_turns(tmp_path, monkeypatch):
    """A standing gap with no explicit sync threshold turns diverged when the pickle ages."""
    _seed_chroma_db(str(tmp_path), sqlite_count=20_000, segment_id="seg-gap")
    _write_pickle(str(tmp_path), "seg-gap", hnsw_count=19_900)  # 100 rows behind
    _fine_stamps(str(tmp_path))
    clock, wall = [1_000.0], [time.time()]
    monkeypatch.setattr(chroma_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(chroma_mod.time, "time", lambda: wall[0])
    assert chroma_mod.hnsw_capacity_status(str(tmp_path), COLLECTION)["status"] == "ok"
    grace = chroma_mod._HNSW_PERSISTENT_DIVERGENCE_GRACE_SECONDS
    clock[0] += chroma_mod._CAPACITY_FINE_STAMP_MAX_AGE_SECONDS + 1
    wall[0] += grace + 1
    with chroma_mod.serve_stale_capacity_verdicts():
        # Past the reuse limit and past the moment the verdict turns: probe, do not serve.
        assert chroma_mod.hnsw_capacity_status(str(tmp_path), COLLECTION)["status"] == "diverged"


def test_the_pickle_is_not_unpickled_again_while_it_is_unchanged(tmp_path, monkeypatch):
    _seed_chroma_db(str(tmp_path), sqlite_count=20_000, segment_id="seg-pick")
    _write_pickle(str(tmp_path), "seg-pick", hnsw_count=20_000)
    _fine_stamps(str(tmp_path))
    reads = []
    real = chroma_mod._hnsw_element_count
    monkeypatch.setattr(chroma_mod, "_hnsw_element_count", lambda *a: reads.append(1) or real(*a))
    for _ in range(3):
        chroma_mod._hnsw_capacity_status_uncached(str(tmp_path), COLLECTION)
    assert len(reads) == 1, "an unchanged pickle was unpickled again by each probe"
    _write_pickle(str(tmp_path), "seg-pick", hnsw_count=20_500)  # a flush rewrites it
    assert (
        chroma_mod._hnsw_capacity_status_uncached(str(tmp_path), COLLECTION)["hnsw_count"] == 20_500
    )
    assert len(reads) == 2


def test_a_palace_with_an_explicit_sync_threshold_keeps_its_verdict_for_the_whole_ceiling(
    tmp_path, monkeypatch
):
    """The live palace: a standing flush lag and an old pickle expired its verdict every 10 s.

    The rule for a verdict the clock can change is only for a collection with no explicit
    sync threshold; with one, nothing in the verdict depends on time.
    """
    _seed_chroma_db(str(tmp_path), sqlite_count=20_000, segment_id="seg-exp", sync_threshold=1000)
    _write_pickle(str(tmp_path), "seg-exp", hnsw_count=19_900)  # 100 rows behind, within 2000
    ns = (int(time.time()) - 3_600) * 1_000_000_000 + 123_456_789  # an old pickle
    for root, _dirs, names in os.walk(str(tmp_path)):
        for name in names:
            if name == "chroma.sqlite3" or name.endswith(".pickle"):
                os.utime(os.path.join(root, name), ns=(ns, ns))
    runs = []
    real = chroma_mod._hnsw_capacity_status_uncached
    monkeypatch.setattr(
        chroma_mod,
        "_hnsw_capacity_status_uncached",
        lambda *a, **k: runs.append(1) or real(*a, **k),
    )
    clock = [1_000.0]
    monkeypatch.setattr(chroma_mod.time, "monotonic", lambda: clock[0])
    assert chroma_mod.hnsw_capacity_status(str(tmp_path), COLLECTION)["status"] == "ok"
    for gap in (11.0, 25.0, 100.0):  # searches spaced further apart than 10 s
        clock[0] += gap
        assert chroma_mod.hnsw_capacity_status(str(tmp_path), COLLECTION)["status"] == "ok"
    assert len(runs) == 1, "a verdict that cannot change with time was probed again after 10 s"


def test_the_rule_that_sets_the_reuse_ceiling_is_logged_once_with_its_evidence(tmp_path, caplog):
    import logging

    _seed_chroma_db(str(tmp_path), sqlite_count=20_000, segment_id="seg-log", sync_threshold=1000)
    _write_pickle(str(tmp_path), "seg-log", hnsw_count=19_900)
    _fine_stamps(str(tmp_path))
    with caplog.at_level(logging.INFO, logger=chroma_mod.logger.name):
        chroma_mod.hnsw_capacity_status(str(tmp_path), COLLECTION)
        chroma_mod.hnsw_capacity_status(str(tmp_path), COLLECTION)
    lines = [r.getMessage() for r in caplog.records if "capacity verdict reuse" in r.getMessage()]
    assert len(lines) == 1, lines
    assert "fine timestamps=True" in lines[0] and "ceiling 300s" in lines[0]
    assert "explicit sync threshold=True" in lines[0] and "clock-dependent=False" in lines[0]
