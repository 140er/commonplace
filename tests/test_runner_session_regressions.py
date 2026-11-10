"""Gate regressions: the same behavior checks run on the reviewed head and the fix."""

import hashlib

import pytest

from commonplace.runner import agent, sessions
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests._phase_b_helpers import machine_two
from tests.test_orch_runner import config_text
from tests.test_orch_sessions import session_world as session_world, start


def test_stop_then_resume_preserves_unknown_launch(session_world):
    w = session_world
    w.host_error = True
    w.card()
    assert w.tick() == 0 and w.session()["state"] == "uncertain"
    assert w.control("stop")[0] == 200
    resume_status, _ = w.control("resume")
    w.host_error = False
    assert w.tick() == 0
    assert resume_status == 403 and w.session()["state"] == "uncertain", (
        "stop must not make an unknown host effect safe to resume"
    )
    assert len(w.wakes()) == 1


@pytest.mark.parametrize("broken", ["foreign_archived", "own_archived", "own_missing"])
def test_unavailable_configured_board_does_not_starve_session(session_world, broken, monkeypatch):
    w = session_world
    machine = machine_two(w)["id"] if broken == "foreign_archived" else w.machine_id
    name = "aaa-unavailable"
    w.boards.board_add(slug=name, wing="shared")
    text = config_text(machine, board=name).split("[[triggers]]")[0]
    w.st.write(
        w.owner, name, text, connections=[], sha256=hashlib.sha256(text.encode()).hexdigest()
    )
    with w.boards.store.transaction() as conn:
        if broken == "own_missing":
            conn.execute("DELETE FROM boards WHERE slug=?", (name,))
        else:
            conn.execute("UPDATE boards SET archived=1 WHERE slug=?", (name,))
    reads = []
    board_state = sessions.board_state

    def read_board(gateway, ws, board):
        reads.append(board)
        return board_state(gateway, ws, board)

    monkeypatch.setattr(sessions, "board_state", read_board)
    w.card()
    exit_code = w.tick()
    assert len(w.wakes()) == 1 and exit_code == 0, (
        "a broken configured board must not prevent the healthy board from launching"
    )
    if broken == "foreign_archived":
        assert name not in reads, "filter another machine's board before reading its cards"


@pytest.mark.parametrize("failure", ["invalid_json", "invalid_shape", "session_transport"])
def test_session_journal_or_error_keeps_card_leasing(session_world, tmp_path, monkeypatch, failure):
    w = session_world
    card = w.card(board="plain", status="todo")
    assert w.person_start(card)[0] == 201
    journal = tmp_path / "journal"
    journal.mkdir(exist_ok=True)
    if failure == "session_transport":
        post = agent._Hub.post

        def transport(self, route, body, **kwargs):
            if route == "sessions/lease":
                raise agent._HubError("transient", 503)
            return post(self, route, body, **kwargs)

        monkeypatch.setattr(agent._Hub, "post", transport)
    else:
        (journal / "session-command.json").write_text("{" if failure == "invalid_json" else "[]")
    # Convert the observable runner crash into data, rather than a setup/error RED.
    try:
        exit_code = w.tick()
    except (ValueError, TypeError, KeyError) as exc:
        exit_code = type(exc).__name__
    card_launches = [
        args
        for name, args in w.calls
        if name == "t3_thread_launch" and args["title"].startswith("Card #")
    ]
    assert len(card_launches) == 1 and exit_code == 0, (
        "session failures must not prevent a queued card from launching in the same pass"
    )
    if failure != "session_transport":
        assert not (journal / "session-command.json").exists()
        assert list(journal.glob("session-command.json.quarantine-*"))


def test_invalid_session_command_reports_local_refusal(session_world, monkeypatch):
    w = session_world
    check = agent._local_grant_code

    def worker_topics_only(config, command):
        # The machine grants orchestrate, but the host's local ceiling does not.
        if "action" in command:
            config = {**config, "topics": ["worker"]}
        return check(config, command)

    monkeypatch.setattr(agent, "_local_grant_code", worker_topics_only)
    w.card()
    assert w.tick() == 0
    state = w.session()
    assert state["reason"] == "topic_not_allowed" and state["state"] == "unavailable", (
        "a local refusal must be visible instead of a silent expiring lease"
    )
    assert not w.calls


def test_nonowner_project_view_omits_session_card_details(session_world):
    w = session_world
    start(w)
    card = w.card()
    with w.boards.store.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status='review',ask=?,ask_to=? WHERE id=?",
            ("Owner-only question", w.owner.actor, card),
        )
    owner = w.session()
    assert owner["thread_id"] and owner["blocked"]
    p = w.project_http
    status, view = p.project(p.member, "acme")
    assert status == 200
    assert not {"thread_id", "blocked", "actionable"} & view["session"].keys(), (
        "non-owner project views must omit thread bindings and card details"
    )
