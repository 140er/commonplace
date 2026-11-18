"""Coalescing, stale values, deadlines and bounded optional metadata work."""

import threading

from commonplace.web.startup import Snapshots


def test_stale_value_is_immediate_and_refresh_is_coalesced():
    clock = [0.0]
    cache = Snapshots(clock=lambda: clock[0])
    cache.values["count"] = (1.0, 7)
    clock[0] = 2
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def load():
        calls.append(1)
        entered.set()
        try:
            assert release.wait(2)
            return 8
        finally:
            finished.set()

    try:
        assert cache.read("count", load) == (7, "stale")
        assert entered.wait(1)
        assert cache.read("count", load) == (7, "stale")
        assert len(calls) == 1
    finally:
        release.set()
        assert finished.wait(1)
    # Acquiring both permits joins the refresh's cleanup without polling.
    assert cache.slots.acquire(timeout=1)
    assert cache.slots.acquire(timeout=1)
    assert cache.read("count", load) == (8, "ready")


def test_optional_work_has_a_deadline_and_failure_backoff():
    from commonplace.rules.checks import remaining

    cache = Snapshots()
    entered = threading.Event()

    def fail():
        assert 0 < remaining(100) <= 3
        entered.set()
        raise TimeoutError("Synthetic slow metadata")

    assert cache.read("count", fail) == (None, "loading")
    assert entered.wait(1)
    assert cache.slots.acquire(timeout=1)
    assert cache.slots.acquire(timeout=1)
    assert cache.read("count", lambda: 99) == (None, "unavailable")


def test_uncooperative_dependencies_cannot_accumulate_threads():
    cache = Snapshots()
    cache.values[0] = (cache.clock() - 1, 7)
    release = threading.Event()
    entered = [threading.Event(), threading.Event()]

    def load(i):
        entered[i].set()
        assert release.wait(2)
        return i

    try:
        for i in range(2):
            assert cache.read(i, lambda i=i: load(i)) == (
                (7, "stale") if i == 0 else (None, "loading")
            )
            assert entered[i].wait(1)
        assert cache.read(2, lambda: 2) == (None, "loading")
        assert cache.read(0, lambda: 99) == (7, "stale")
        assert len(cache.pending) == 2
    finally:
        release.set()
        assert cache.slots.acquire(timeout=1)
        assert cache.slots.acquire(timeout=1)
