"""Session board-loss regressions, paired against the previously reviewed head."""

import json

import pytest

from commonplace.extensions import ToolError
from commonplace.runner import agent, sessions
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_orch_sessions import session_world as session_world, start


def retire(w, loss):
    with w.boards.store.transaction() as conn:
        if loss == "archived":
            conn.execute("UPDATE boards SET archived=1 WHERE slug='acme'")
        else:
            # Preserve valid task foreign keys while removing the configured slug.
            conn.execute("UPDATE boards SET slug='retired-acme' WHERE slug='acme'")


def row(w):
    return w.rows("SELECT * FROM runner_sessions WHERE board='acme'")[0]


@pytest.mark.parametrize("loss", ["archived", "missing"])
@pytest.mark.parametrize("delivery", ["live", "replay"])
def test_finished_turn_on_lost_board_clears_journal(
    session_world, tmp_path, monkeypatch, loss, delivery
):
    w = session_world
    start(w)
    journal = tmp_path / "journal" / "session-command.json"
    if delivery == "live":
        host = agent.subprocess.run

        def archive_on_completion(argv, **kwargs):
            if argv[-2] == "t3_thread_read":
                retire(w, loss)
            return host(argv, **kwargs)

        monkeypatch.setattr(agent.subprocess, "run", archive_on_completion)
    else:
        status, command = w.machine_call("sessions/lease")
        assert status == 200 and command["action"] == "inspect"
        token = {"lease_token": command["lease_token"]}
        assert w.machine_call("sessions/starting", token) == (200, {"proceed": True})
        journal.write_text(json.dumps({**token, "outcome": "finished"}))
        retire(w, loss)
    assert w.tick() == 0
    assert not journal.exists(), "a completed turn on an unavailable board must be acknowledged"
    state = row(w)
    assert state["state"] == "idle" and state["phase"] is None and state["lease_token"] is None
    calls = len(w.calls)
    assert w.tick() == 0 and len(w.calls) == calls and not journal.exists()


@pytest.mark.parametrize("loss", ["archived", "missing"])
@pytest.mark.parametrize("bound", [False, True])
def test_board_lost_before_starting_closes_command(session_world, loss, bound):
    w = session_world
    if bound:
        start(w)
    else:
        w.card()
    _, command = w.machine_call("sessions/lease")
    retire(w, loss)
    status, payload = w.machine_call("sessions/starting", {"lease_token": command["lease_token"]})
    assert (status, payload) == (200, {"proceed": False}), "board loss must retire a leased command"
    state = row(w)
    assert state["state"] == ("uncertain" if bound else "idle")
    assert state["phase"] is None and state["lease_token"] is None
    assert len(w.wakes()) == int(bound)


@pytest.mark.parametrize("loss", ["archived", "missing"])
def test_launch_ack_after_board_loss_retains_binding(session_world, loss):
    w = session_world
    w.card()
    _, command = w.machine_call("sessions/lease")
    token = {"lease_token": command["lease_token"]}
    assert w.machine_call("sessions/starting", token) == (200, {"proceed": True})
    retire(w, loss)
    status, payload = w.machine_call(
        "sessions/result", {**token, "outcome": "running", "thread_id": "session-thread"}
    )
    assert (status, payload.get("state")) == (200, "uncertain"), (
        "board loss cannot prove a launched thread ended"
    )
    state = row(w)
    assert state["thread_id"] == "session-thread" and state["code"] == "effect_unknown"
    assert state["phase"] is None and state["lease_token"] is None


@pytest.mark.parametrize("loss", ["archived", "missing"])
def test_status_of_lost_board_is_unavailable(session_world, loss):
    w = session_world
    start(w)
    retire(w, loss)
    try:
        state = sessions.status(w.gw, w.ws, "acme")
    except ToolError:
        state = {"state": "raised"}
    assert state["state"] == "unavailable", "a lost board has a safe status without card details"
    assert "thread_id" not in state and state["actionable"] == [] and state["blocked"] == []


@pytest.mark.parametrize("loss", ["archived", "missing"])
def test_board_loss_after_control_commit_is_acknowledged(session_world, monkeypatch, loss):
    w = session_world
    start(w)
    status = sessions.status

    def retire_before_status(gateway, ws, board):
        retire(w, loss)
        return status(gateway, ws, board)

    monkeypatch.setattr(sessions, "status", retire_before_status)
    code, payload = w.control("stop")
    assert code == 200 and payload.get("state") == "unavailable", (
        "a committed stop must remain acknowledged if its board disappears"
    )
    assert row(w)["paused"] == 1
