"""Barrier-controlled cross-path admissions against independent SQLite connections."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from commonplace.projects import limits
from commonplace.runner import jobs, tools
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests._phase_b_helpers import World, call, cap, claim, seed


@pytest.fixture
def world(hub, monkeypatch):
    from commonplace.boards import service

    w = World(hub)
    # Each operation still opens an independent connection. Avoid schema migration
    # write locks hiding a missing admission lock in a contender's constructor.
    monkeypatch.setattr(service, "open_store", lambda *args, **kwargs: w.boards.store)
    return w


def race(world, monkeypatch, module, name, first, second):
    """Pause after the first path's in-lock operation, start its contender, resume."""
    paused, entered = Event(), Event()
    original = getattr(module, name)
    once = False
    lock_held = False

    def wrap(*args, **kwargs):
        nonlocal once, lock_held
        result = original(*args, **kwargs)
        if not once:
            once = True
            from mempalace.sqlite_crypto import connect

            probe = connect(world.js.path, timeout=0, isolation_level=None)
            try:
                try:
                    probe.execute("BEGIN IMMEDIATE")
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc):
                        raise
                    lock_held = True
            finally:
                probe.rollback()
                probe.close()
            paused.set()
            if not entered.wait(10):
                raise RuntimeError("contender did not enter")
        return result

    monkeypatch.setattr(module, name, wrap)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first)
        if not paused.wait(10):
            raise RuntimeError("first path did not enter")

        def contend():
            entered.set()
            return second()

        b = pool.submit(contend)
        results = a.result(timeout=15), b.result(timeout=15)
    assert lock_held, "admission lock must be held until the operation commits"
    return results


def assert_one(results):
    assert sum(status in (200, 201) for status, _ in results) == 1 and "project_limit" in str(
        results
    )  # RED


def test_r22_claim_then_start(world, monkeypatch):
    w = world
    seed(w)
    cap(w, 1)
    a, b = w.card(), w.card()
    assert_one(
        race(w, monkeypatch, limits, "check", lambda: claim(w, a), lambda: call(w.person_start, b))
    )


def test_r23_start_then_claim(world, monkeypatch):
    w = world
    seed(w)
    cap(w, 1)
    a, b = w.card(), w.card()
    assert_one(
        race(
            w, monkeypatch, jobs, "create_in", lambda: call(w.person_start, a), lambda: claim(w, b)
        )
    )


def test_r24_lease_then_claim(world, monkeypatch):
    w = world
    seed(w)
    a, b = w.card(), w.card()
    w.person_start(a)
    cap(w, 1)
    results = race(
        w,
        monkeypatch,
        jobs,
        "_update",
        lambda: call(jobs.lease, w.js, w.machine()),
        lambda: claim(w, b),
    )
    assert_one(results)


def test_r25_configured_start_then_claim(world, monkeypatch):
    from commonplace.orch import runner_bridge

    w = world
    seed(w)
    cap(w, 1)
    a, b = w.card(), w.card()
    w.configure(max_agents=1)
    assert_one(
        race(
            w,
            monkeypatch,
            runner_bridge,
            "_fenced",
            lambda: call(w.person_start, a),
            lambda: claim(w, b),
        )
    )


def test_r26_two_stores_start(world, monkeypatch):
    w = world
    seed(w)
    cap(w, 1)
    a, b = w.card(), w.card()
    other_store = jobs.open_store(w.gw, w.ws)
    original = jobs.open_store
    monkeypatch.setattr(
        jobs,
        "open_store",
        lambda gateway, ws: other_store if gateway is w.gw else original(gateway, ws),
    )
    assert_one(
        race(
            w,
            monkeypatch,
            jobs,
            "create_in",
            lambda: call(w.person_start, a),
            lambda: call(w.person_start, b),
        )
    )


def test_r26b_claim_before_enrollment(world, monkeypatch):
    w = world
    seed(w)
    cap(w, 1)
    # Remove the enrolled machine rows. A claim must still take the runner lock.
    with w.db._lock:
        w.db._conn.execute("DELETE FROM runner_machines")
    a, b = w.card(), w.card()

    def enroll_start():
        from tests._phase_b_helpers import machine_two

        m = machine_two(w)
        return call(tools.card_start, w.ctx(w.owner), b, m["id"], "demo", "worker")

    assert_one(race(w, monkeypatch, limits, "check", lambda: claim(w, a), enroll_start))


def test_r8c_lease_claim_race(world, monkeypatch):
    w = world
    seed(w)
    a, b = w.card(), w.card()
    w.person_start(a)
    cap(w, 1)
    assert_one(
        race(
            w,
            monkeypatch,
            jobs,
            "_update",
            lambda: call(jobs.lease, w.js, w.machine()),
            lambda: claim(w, b),
        )
    )
