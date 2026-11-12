"""Agent limit up to 100 and the runner's per-machine memory guard (card 388)."""

import sqlite3

import pytest

from commonplace.orch import schema
from commonplace.orch.schema import ConfigError
from commonplace.projects import limits
from commonplace.runner import agent, jobs
from tests._projects_helpers import projects_hub_fixture  # noqa: F401
from tests.runner_harness import (
    card,
    http,
    owners as owners,
    runner_config,
    signup_hub as signup_hub,
)
from tests.test_orch_acceptance import (
    BOARD,
    _configure,
    _jobs,
    _lease,
    _machine,
    _person_start,
)
from tests.test_orch_schema import data

# ---------------------------------------------------------------- the bound


def test_schema_and_limits_share_one_bound():
    assert schema.MAX_AGENTS_RANGE == (1, getattr(limits, "MAX_AGENTS", None)) == (1, 100)


def test_config_accepts_100_and_refuses_101():
    doc = data()
    doc["limits"]["max_agents"] = 100
    assert schema.validate(doc).max_agents == 100
    doc["limits"]["max_agents"] = 101
    with pytest.raises(ConfigError) as info:
        schema.validate(doc)
    assert info.value.code == "invalid_value"
    assert "between 1 and 100" in str(info.value)


def test_project_limit_roundtrips_100_and_refuses_101(projects_hub):
    h = projects_hub
    assert h.create_board(h.owner, "alpha")[0] == 201
    put = lambda value: h.request(  # noqa: E731
        h.owner, "PUT", "/api/projects/alpha/limits", {"max_agents": value}
    )[0]
    assert put(100) == 200
    assert h.project(h.owner, "alpha")[1]["limits"] == {"max_agents": 100}
    assert put(101) == 400
    assert h.project(h.owner, "alpha")[1]["limits"] == {"max_agents": 100}


def test_table_created_with_the_old_bound_is_widened_keeping_rows():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE project_limits (board_id INTEGER PRIMARY KEY, "
        "max_agents INTEGER CHECK(max_agents BETWEEN 1 AND 20))"
    )
    conn.execute("INSERT INTO project_limits VALUES (1, 7)")
    limits.put(conn, 2, {"max_agents": 100})
    assert limits.get(conn, 1) == {"max_agents": 7}
    assert limits.get(conn, 2) == {"max_agents": 100}
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO project_limits VALUES (3, 101)")
    # A second put leaves the already-widened table alone.
    limits.put(conn, 1, {"max_agents": 20})
    assert limits.get(conn, 1) == {"max_agents": 20}


def test_effective_limit_stays_capped_by_runner_capacity(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = _machine(hub, owner)
    first, second = card(hub, owner, board=BOARD), card(hub, owner, board=BOARD)
    _configure(hub, owner, machine, max_agents=100)
    assert _person_start(hub, owner, machine, first)[0] == 201
    assert _person_start(hub, owner, machine, second)[0] == 201
    assert _lease(hub, machine)[0] == 200
    assert _lease(hub, machine)[0] == 204  # machine max_in_flight=1, not the limit of 100


# ---------------------------------------------------------------- memory guard


class FakeHub:
    def __init__(self):
        self.posts = []

    def post(self, route, body, **_):
        self.posts.append((route, body))


@pytest.fixture
def start_setup(tmp_path, monkeypatch):
    exe = tmp_path / "t3"
    exe.write_text("")
    config = {
        "projects": {"demo": {"project_id": "demo"}},
        "topics": ["worker"],
        "binding_acks": ["demo:worker"],
        "t3_executable": [str(exe)],
        "min_free_memory_mb": 1024,
    }
    job = {
        "task_number": 7,
        "job_id": "job_" + "0" * 16,
        "title_marker": "commonplace-job:job_" + "0" * 16,
        "lease_token": "lease",
        "project": "demo",
        "topic": "worker",
        "runtime_mode": "approval-required",
    }
    (tmp_path / "journal").mkdir()
    spawned = []
    monkeypatch.setattr(agent, "_spawn", lambda *a: spawned.append(a) or {"outcome": "uncertain"})
    return config, job, spawned


def _start(config, job, tmp_path):
    hub = FakeHub()
    agent._start(hub, config, job, tmp_path / "journal", {}, ())
    return hub


def test_start_is_refused_below_the_memory_floor(start_setup, tmp_path, monkeypatch):
    config, job, spawned = start_setup
    monkeypatch.setattr(agent, "_available_memory_mb", lambda: 1023, raising=False)
    hub = _start(config, job, tmp_path)
    assert hub.posts == [
        ("result", {"lease_token": "lease", "outcome": "failed", "code": "low_memory"})
    ]
    assert spawned == []
    assert "low_memory" in jobs.PRESPAWN_CODES  # the hub accepts and shows the reason


def test_start_proceeds_when_memory_meets_the_floor(start_setup, tmp_path, monkeypatch):
    config, job, spawned = start_setup
    monkeypatch.setattr(agent, "_available_memory_mb", lambda: 1024, raising=False)
    hub = _start(config, job, tmp_path)
    assert [route for route, _ in hub.posts] == ["starting", "result"]
    assert len(spawned) == 1


def test_unreadable_probe_never_blocks(start_setup, monkeypatch):
    config, job, _ = start_setup
    monkeypatch.setattr(agent, "_available_memory_mb", lambda: None, raising=False)
    assert agent._job_code(config, job) is None


def test_zero_floor_disables_the_guard(start_setup, monkeypatch):
    config, job, _ = start_setup
    config["min_free_memory_mb"] = 0
    monkeypatch.setattr(agent, "_available_memory_mb", lambda: 0, raising=False)
    assert agent._job_code(config, job) is None


MEMINFO = "MemTotal:       16384000 kB\nMemFree:          100000 kB\nMemAvailable:    2048000 kB\nBuffers: 1 kB\n"


def test_meminfo_parser_converts_kb_to_mb(tmp_path):
    path = tmp_path / "meminfo"
    path.write_text(MEMINFO)
    assert agent._available_memory_mb(str(path)) == 2000  # 2048000 kB // 1024


def test_meminfo_parser_is_unknown_without_a_usable_line(tmp_path):
    path = tmp_path / "meminfo"
    path.write_text("MemTotal: 1 kB\nMemAvailable: lots kB\n")
    assert agent._available_memory_mb(str(path)) is None
    path.write_text("MemTotal: 1 kB\n")
    assert agent._available_memory_mb(str(path)) is None
    assert agent._available_memory_mb(str(tmp_path / "missing")) is None


def test_low_memory_reaches_card_start_status(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = _machine(hub, owner)
    task = card(hub, owner, board=BOARD)
    assert _person_start(hub, owner, machine, task)[0] == 201
    code, lease = _lease(hub, machine)
    assert code == 200
    # Exactly what the runner client posts when its guard refuses a leased start.
    code, body = http(
        hub.port,
        "POST",
        "/api/runner/result",
        token=machine["machine_token"],
        body={"lease_token": lease["lease_token"], "outcome": "failed", "code": "low_memory"},
    )
    assert code == 200, (code, body)
    (job,) = _jobs(hub, owner, task)
    assert (job["state"], job["outcome_code"]) == ("failed", "low_memory")


def _loaded(tmp_path, **overrides):
    endpoint = type("Endpoint", (), {"port": 1})()
    path = runner_config(tmp_path, endpoint, "synthetic-machine-token", **overrides)
    return agent._load(path)


def test_existing_config_without_the_key_gets_the_default_floor(tmp_path):
    config = _loaded(tmp_path)
    assert config.get("min_free_memory_mb") == 256
    assert _loaded(tmp_path, min_free_memory_mb=2048)["min_free_memory_mb"] == 2048


@pytest.mark.parametrize("bad", [-1, True, "256", 1 << 21])
def test_floor_is_validated(tmp_path, bad):
    with pytest.raises(agent._ConfigError) as info:
        _loaded(tmp_path, min_free_memory_mb=bad)
    assert "min_free_memory_mb" in str(info.value)
