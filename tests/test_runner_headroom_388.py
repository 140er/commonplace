"""Burst admission, authenticated resource reporting and configured runner capacity."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from commonplace.runner import agent, capacity, headroom, jobs
from tests.test_agent_limit_388 import start_setup  # noqa: F401


def test_cpu_probe_reports_idle_core_equivalents(monkeypatch):
    monkeypatch.setattr(headroom.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(headroom.os, "getloadavg", lambda: (2.5, 2, 1))
    assert headroom.cpu_free() == 5.5
    monkeypatch.setattr(headroom.os, "getloadavg", lambda: (20, 20, 20))
    assert headroom.cpu_free() == 0


def test_burst_cannot_spend_the_same_memory_twice(start_setup, tmp_path, monkeypatch):  # noqa: F811
    config, job, spawned = start_setup
    monkeypatch.setattr(agent, "_available_memory_mb", lambda: 1600)
    monkeypatch.setattr(headroom, "cpu_free", lambda: 8)

    class Hub:
        def __init__(self):
            self.posts = []

        def post(self, route, body):
            self.posts.append((route, body))

    for n in range(2):
        hub = Hub()
        agent._start(
            hub,
            config,
            {**job, "job_id": f"job_{n:016x}", "title_marker": f"commonplace-job:job_{n:016x}"},
            tmp_path / "journal",
            {},
            (),
        )
    assert len(spawned) == 1
    assert hub.posts == [
        ("result", {"lease_token": "lease", "outcome": "failed", "code": "low_memory"})
    ]


def test_cpu_headroom_refuses_before_starting(start_setup, tmp_path, monkeypatch):  # noqa: F811
    config, job, spawned = start_setup
    monkeypatch.setattr(agent, "_available_memory_mb", lambda: 10000)
    monkeypatch.setattr(headroom, "cpu_free", lambda: 0.5)

    class Hub:
        def post(self, route, body):
            assert route == "result" and body["code"] == "low_cpu"

    agent._start(Hub(), config, job, tmp_path / "journal", {}, ())
    assert spawned == []
    assert "low_cpu" in jobs.PRESPAWN_CODES


def test_reservations_serialize_independent_clients_and_expire(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "_available_memory_mb", lambda: 1024)
    monkeypatch.setattr(headroom, "cpu_free", lambda: 8)
    monkeypatch.setattr(headroom.time, "time", lambda: 1000)
    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(pool.map(lambda n: headroom.reserve({}, tmp_path, str(n)), range(4)))
    assert codes.count(None) == 1
    assert codes.count("low_memory") == 3
    monkeypatch.setattr(headroom.time, "time", lambda: 1121)
    assert headroom.reserve({}, tmp_path, "later") is None


def test_unknown_probes_keep_legacy_behavior(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "_available_memory_mb", lambda: None)
    monkeypatch.setattr(headroom, "cpu_free", lambda: None)
    assert headroom.reserve({}, tmp_path, "example") is None


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), "8"])
def test_invalid_cpu_reports_are_rejected(value):
    with pytest.raises(ValueError):
        headroom.validate({"memory_available_mb": 4096, "memory_floor_mb": 256, "cpu_free": value})


def test_capacity_respects_hourly_resources_and_report_expiry():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(jobs.SCHEMA + ";" + capacity.SCHEMA)
    machine = {"id": "example", "max_in_flight": 4, "max_starts_per_hour": 2}
    now = "2026-01-01T12:00:00Z"
    assert capacity.available(conn, machine, now) == 2
    data = {"memory_available_mb": 2048, "memory_floor_mb": 256, "cpu_free": 1.5}
    conn.execute("INSERT INTO runner_resources VALUES (?,?,?)", ("example", now, json.dumps(data)))
    assert capacity.available(conn, machine, now, max_agents=100) == 1
    assert capacity.available(conn, machine, now, max_agents=20) == 2
    assert capacity.available(conn, machine, now, max_agents=None) == 2
    assert capacity.available(conn, machine, jobs._after(now, 601), max_agents=100) == 2
