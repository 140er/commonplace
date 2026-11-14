"""Expanded-limit guards must preserve normal-limit session behavior."""

import pytest

from commonplace.runner import headroom
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_orch_runner import session_config_text
from tests.test_orch_sessions import session_world as session_world, start


@pytest.mark.parametrize("limit", [3, 20])
def test_normal_limit_sessions_launch_despite_low_cpu(session_world, monkeypatch, limit):
    w = session_world
    w.write(session_config_text(w.machine_id, max_agents=limit))
    monkeypatch.setattr(headroom, "cpu_free", lambda: 0)
    start(w)


def test_unknown_measurements_do_not_require_reservation_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(
        headroom,
        "snapshot",
        lambda _: {
            "memory_available_mb": None,
            "cpu_free": None,
            "memory_floor_mb": 256,
        },
    )
    assert headroom.reserve({}, tmp_path / "absent-journal", "example") is None


def test_expanded_limit_session_refuses_known_cpu_shortage(session_world, monkeypatch):
    w = session_world
    w.write(session_config_text(w.machine_id, max_agents=30))
    monkeypatch.setattr(headroom, "cpu_free", lambda: 0)
    w.card()
    assert w.tick() == 0
    assert not w.wakes()
    assert w.session()["reason"] == "low_cpu"


def test_expanded_limit_session_launches_with_unknown_probes(session_world, tmp_path, monkeypatch):
    from commonplace.runner import agent

    w = session_world
    w.write(session_config_text(w.machine_id, max_agents=30))
    monkeypatch.setattr(agent, "_available_memory_mb", lambda: None)
    monkeypatch.setattr(headroom, "cpu_free", lambda: None)
    start(w)
    assert not (tmp_path / "journal" / "headroom.sqlite3").exists()


def test_optional_report_failure_keeps_normal_session_available(session_world, monkeypatch):
    from commonplace.runner import agent

    post = agent._Hub.post

    def unavailable(self, route, body, **kwargs):
        if route == "resources":
            raise agent._HubError("transient", 503)
        return post(self, route, body, **kwargs)

    monkeypatch.setattr(agent._Hub, "post", unavailable)
    start(session_world)
