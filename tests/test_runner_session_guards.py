"""Session fencing and recovery checks; behavioral base/head proofs live separately."""

import pytest

from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_orch_sessions import session_world as session_world, start


@pytest.mark.parametrize("guard", ["topic", "requester", "restore", "binding", "model"])
def test_session_authority_is_rechecked_before_host_effect(session_world, guard):
    w = session_world
    start(w)
    if guard == "topic":
        from commonplace.runner import registry

        registry.patch(w.db, w.owner, w.machine_id, {"topics": ["worker"]})
    elif guard == "requester":
        with w.db._lock:
            w.db._conn.execute(
                "UPDATE memberships SET role='member' WHERE workspace_id=? AND user_id=?",
                (w.ws, w.owner.user_id),
            )
    elif guard == "restore":
        w.flag_restore()
    elif guard == "binding":
        from commonplace.runner import registry

        registry.patch(w.db, w.owner, w.machine_id, {"enabled": False})
    else:
        w.set_models(None, role="main")
    w.tick()
    assert len(w.calls) == 1, "lost authority must fence even an inspection/continuation"


def test_session_owner_control_rejects_agent_and_member(session_world):
    w = session_world
    p = w.project_http
    assert p.request(p.member, "POST", "/api/projects/acme/orchestrator/stop", {})[0] == 403
    assert (
        p.request(
            {"Authorization": "Bearer " + p.a1}, "POST", "/api/projects/acme/orchestrator/stop", {}
        )[0]
        == 403
    )


def test_session_stop_racing_launch_keeps_thread_binding(session_world):
    w = session_world
    w.card()
    status, command = w.machine_call("sessions/lease")
    assert status == 200
    token = {"lease_token": command["lease_token"]}
    assert w.machine_call("sessions/starting", token) == (200, {"proceed": True})
    assert w.control("stop")[0] == 200
    assert (
        w.machine_call(
            "sessions/result", {**token, "outcome": "running", "thread_id": "session-thread"}
        )[0]
        == 200
    )
    assert w.tick() == 0
    assert w.session()["state"] == "stopped"
    assert w.session()["thread_id"] == "session-thread"
    assert w.wakes() == []
    assert any(name == "t3_thread_interrupt" for name, _ in w.calls)


def test_session_unknown_host_effect_is_not_replayed(session_world):
    w = session_world
    w.host_error = True
    w.card()
    assert w.tick() == 0
    assert w.session()["state"] == "uncertain"
    assert w.tick() == 0
    assert len(w.wakes()) == 1
    assert w.control("resume")[0] == 403


def test_session_starting_rechecks_authority_and_board(session_world):
    w = session_world
    a = w.card()
    _, command = w.machine_call("sessions/lease")
    token = {"lease_token": command["lease_token"]}
    with w.boards.store.transaction() as conn:
        conn.execute("UPDATE tasks SET status='done' WHERE id=?", (a,))
    assert w.machine_call("sessions/starting", token) == (200, {"proceed": False})
    assert not w.calls
    w.card()
    _, command = w.machine_call("sessions/lease")
    token = {"lease_token": command["lease_token"]}
    w.unconfigure()
    assert w.machine_call("sessions/starting", token)[0] == 409
    assert not w.calls


def test_session_duplicate_leases_do_not_duplicate_effects(session_world):
    w = session_world
    w.card()
    status, first = w.machine_call("sessions/lease")
    assert status == 200
    assert w.machine_call("sessions/lease")[0] == 204
    token = {"lease_token": first["lease_token"]}
    assert w.machine_call("sessions/starting", token)[0] == 200
    assert w.machine_call("sessions/starting", token)[0] == 409


def test_session_expired_starting_is_uncertain(session_world):
    w = session_world
    w.card()
    _, command = w.machine_call("sessions/lease")
    assert w.machine_call("sessions/starting", {"lease_token": command["lease_token"]})[0] == 200
    with w.js.transaction() as conn:
        conn.execute("UPDATE runner_sessions SET lease_until='2000-01-01T00:00:00Z'")
    assert w.tick() == 0
    assert w.session()["state"] == "uncertain" and not w.calls


def test_session_running_board_does_not_starve_another(session_world):
    import hashlib

    from commonplace.projects import models
    from tests.test_orch_runner import MODEL, PROVIDER, config_text

    w = session_world
    start(w)
    text = config_text(w.machine_id, board="plain").split("[[triggers]]")[0]
    w.st.write(
        w.owner, "plain", text, connections=[], sha256=hashlib.sha256(text.encode()).hexdigest()
    )
    models.put(w.db, w.owner, "plain", {"main": {"provider": PROVIDER, "model": MODEL}})
    w.card(board="plain")
    w.host_status = "running"
    assert w.tick() == 0
    assert len(w.wakes()) == 2
    assert "plain" in w.wakes()[-1][1]["title"]
