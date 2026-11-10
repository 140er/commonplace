"""Persistent sessions: paired base/head proofs through the real runner loop and HTTP."""

import json
from types import SimpleNamespace

import pytest

from commonplace.orch import triggers
from commonplace.runner import agent
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests._phase_b_helpers import cap, doing
from tests._projects_helpers import ProjectsHub
from tests._split_helpers import blockers
from tests.test_orch_runner import World, config_text


@pytest.fixture
def session_world(hub, tmp_path, monkeypatch):
    w = World(hub, topics=("worker", "orchestrate"))
    p = ProjectsHub(hub)
    w.project_http = p
    w.board_id = w.board_row()["id"]
    w.write(config_text(w.machine_id).split("[[triggers]]")[0])
    # Arm the legacy cursor: base failure must not be an arming/setup miss.
    triggers.arm_cursor(w.gw, w.ws, "acme")
    w.calls = []
    w.host_status = "completed"
    w.host_error = False

    def host(argv, **kwargs):
        name, args = argv[-2], json.loads(argv[-1])
        if name in ("t3_thread_launch", "t3_thread_send"):
            assert isinstance(args["message"], str) and args["message"]
        w.calls.append((name, args))
        data = {"threadId": "session-thread", "runId": "session-run", "status": "running"}
        if name == "t3_thread_read":
            data = {"thread": {"threadId": "session-thread", "status": w.host_status}}
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"isError": w.host_error, "structuredContent": data}),
        )

    monkeypatch.setattr(agent.subprocess, "run", host)
    monkeypatch.setattr(agent, "_environment", lambda c: (w.machine_token, "fake-grant", {}))
    config = {
        "hub_url": f"http://127.0.0.1:{hub.port}",
        "poll_seconds": 1,
        "journal_dir": str(tmp_path / "journal"),
        "projects": {"demo": {"project_id": "host-project"}},
        "topics": ["worker", "orchestrate"],
        "binding_acks": ["demo:worker", "demo:orchestrate"],
        "t3_executable": ["/bin/true"],
        "t3_timeout_seconds": 5,
    }
    w.tick = lambda: agent._run(config, True)
    w.session = lambda: p.project(p.owner, "acme")[1].get("session", {})
    w.control = lambda action: p.request(
        p.owner, "POST", f"/api/projects/acme/orchestrator/{action}", {}
    )[:2]
    w.wakes = lambda: [c for c in w.calls if c[0] in ("t3_thread_launch", "t3_thread_send")]
    return w


def start(w):
    w.card(status="backlog")
    assert w.tick() == 0
    assert len(w.wakes()) == 1, "an accepted runner must launch a board session without card.todo"
    assert w.session()["state"] == "running"


def test_session_keeps_working_while_actionable(session_world):
    w = session_world
    start(w)
    cap(w, 1)
    doing(w, w.card(status="backlog"), w.a.actor)
    assert w.tick() == 0  # The host reports that its turn ended; the board still has work.
    assert w.session()["state"] == "running", "a full project is not a blocked board"
    assert w.tick() == 0
    assert [name for name, _ in w.wakes()] == ["t3_thread_launch", "t3_thread_send"]
    assert w.wakes()[-1][1]["threadId"] == "session-thread"
    assert not w.all_jobs(), "sessions must not be per-card start jobs"


def test_session_finishes_only_done_or_explicitly_blocked(session_world):
    w = session_world
    start(w)
    with w.boards.store.transaction() as conn:
        conn.execute("UPDATE tasks SET status='done' WHERE board_id=?", (w.board_id,))
    dependency = w.card(status="todo")
    with w.boards.store.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status='review',ask=?,ask_to=? WHERE id=?",
            ("Which permission may we use?", w.owner.actor, dependency),
        )
    dependent = w.card(status="todo")
    blockers(w, dependent, dependency)
    assert w.tick() == 0
    state = w.session()
    assert state["state"] == "idle"
    assert {b["number"] for b in state["blocked"]} == {dependency, dependent}
    assert all(b["reason"] for b in state["blocked"])
    assert len(w.wakes()) == 1


def test_session_new_card_wakes_idle_without_todo_trigger(session_world):
    w = session_world
    start(w)
    with w.boards.store.transaction() as conn:
        conn.execute("UPDATE tasks SET status='done' WHERE board_id=?", (w.board_id,))
    assert w.tick() == 0
    assert w.session()["state"] == "idle"
    w.card(status="backlog")  # Not a todo transition.
    assert w.tick() == 0
    assert w.session()["state"] == "running"
    assert len(w.wakes()) == 2 and w.wakes()[-1][0] == "t3_thread_send"


def test_session_clear_board_has_no_wakes(session_world):
    w = session_world
    for _ in range(3):
        assert w.tick() == 0
    assert w.session().get("state") == "idle", "a hosted clear board must be idle"
    assert w.calls == []


def test_session_owner_stop_persists_until_resume(session_world):
    w = session_world
    start(w)
    assert w.control("stop")[0] == 200
    w.card()
    for _ in range(3):
        assert w.tick() == 0
    assert w.session()["state"] == "stopped" and len(w.wakes()) == 1
    assert any(name == "t3_thread_interrupt" for name, _ in w.calls)
    assert w.control("resume")[0] == 200
    assert w.tick() == 0
    assert w.session()["state"] == "running" and len(w.wakes()) == 2


def test_session_no_accepted_runner_has_no_host(session_world):
    w = session_world
    w.unconfigure()
    w.card()
    assert w.tick() == 0
    assert w.session().get("state") == "no_host", "no accepted runner means no host attached"
    assert w.session()["reason"] == "no host attached"
    assert not w.calls
