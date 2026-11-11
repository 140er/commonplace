"""Idle nudge: one wake to the orchestrator when open work sees no done card for N minutes.

Every test drives a fake clock through ``idle_nudge.sweep`` and reads the nudge from the
public event feed, so a base without the feature fails on behaviour (no event), not on an
import. Timestamps that the board stamps with the real clock are rewritten to fake time.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from commonplace import events, webhooks
from tests._console_helpers import optional
from tests._projects_helpers import projects_hub_fixture  # noqa: F401

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def iso(at):
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def minutes(n):
    return T0 + timedelta(minutes=n)


class Board:
    def __init__(self, h):
        self.h = h
        h.set_coordinator("a1")
        self.mark = h.seq()

    def card(self, title, status="todo"):
        code, body, _ = self.h.request(
            self.h.owner, "POST", "/api/boards/work/cards", {"title": title}
        )
        assert code == 201, body
        if status == "doing":
            self.sql(
                "UPDATE tasks SET status='doing',assignee='conn:a1',claim_until=? WHERE id=?",
                iso(minutes(30)),
                body["number"],
            )
        else:
            self.sql("UPDATE tasks SET status=? WHERE id=?", status, body["number"])
        return body["number"]

    def sql(self, statement, *args):
        from commonplace.boards.service import open_store

        store = open_store(self.h.gateway.workspace_dir(self.h.workspace))
        with store.transaction() as conn:
            conn.execute(statement, args)

    def done(self, number, at):
        self.sql(
            "UPDATE tasks SET status='done',done_at=?,assignee=NULL,claim_until=NULL WHERE id=?",
            iso(at),
            number,
        )

    def sweep(self, at):
        module = optional("commonplace.projects.idle_nudge")
        if module is not None:
            module.sweep(self.h.gateway, at)
        found = [
            e
            for e in events.after(self.h.db, self.h.workspace, self.mark, 1000)
            if e["kind"] == "board.idle_nudge"
        ]
        self.mark = self.h.seq()
        return found

    def setting(self, body):
        return self.h.request(self.h.owner, "PUT", "/api/projects/work/idle_nudge", body)[:2]


@pytest.fixture(name="board")
def board_fixture(projects_hub):
    return Board(projects_hub)


def test_one_nudge_after_n_minutes_with_stuck_counts(board):
    board.card("Doing one", "doing")
    board.card("Todo one")
    assert board.sweep(minutes(0)) == []
    assert board.sweep(minutes(19)) == []
    sent = board.sweep(minutes(20))
    assert len(sent) == 1
    data = sent[0]["data"]
    assert sent[0]["subject"] == "work"
    assert data["board"] == "work"
    assert data["coordinator"] == "conn:a1"
    assert (data["minutes"], data["stalled_minutes"]) == (20, 20)
    assert (data["doing"], data["todo"], data["checks"], data["expired"]) == (1, 1, 0, 0)


def test_no_duplicate_within_n_then_rearms_after_another_n(board):
    board.card("Doing one", "doing")
    assert board.sweep(minutes(0)) == []
    assert len(board.sweep(minutes(20))) == 1
    assert board.sweep(minutes(21)) == []
    assert board.sweep(minutes(39)) == []
    assert len(board.sweep(minutes(40))) == 1
    assert board.sweep(minutes(41)) == []


def test_done_card_resets_the_timer(board):
    board.card("Open work")
    finished = board.card("Finished")
    assert board.sweep(minutes(0)) == []
    board.done(finished, minutes(15))
    assert board.sweep(minutes(20)) == []
    assert board.sweep(minutes(34)) == []
    sent = board.sweep(minutes(35))
    assert len(sent) == 1
    assert sent[0]["data"]["stalled_minutes"] == 20


def test_done_after_a_nudge_rearms_from_that_done(board):
    board.card("Open work")
    finished = board.card("Finished")
    board.sweep(minutes(0))
    assert len(board.sweep(minutes(20))) == 1
    board.done(finished, minutes(25))
    assert board.sweep(minutes(44)) == []
    assert len(board.sweep(minutes(45))) == 1


def test_empty_board_never_nudges(board):
    for at in (0, 20, 200):
        assert board.sweep(minutes(at)) == []


def test_fully_blocked_board_never_nudges_and_restarts_clock_on_new_work(board):
    waiting = board.card("Waits on a dependency")
    dependency = board.card("Not started", "backlog")
    board.sql(
        "INSERT INTO task_blockers(task_id,blocker_id,created_by,created_at) VALUES (?,?,?,?)",
        waiting,
        dependency,
        "user:x",
        iso(T0),
    )
    person = board.card("Waits on a person")
    board.sql("UPDATE tasks SET assignee='user:someone' WHERE id=?", person)
    for at in (0, 20, 200):
        assert board.sweep(minutes(at)) == []
    board.card("Fresh work")
    assert board.sweep(minutes(201)) == []
    assert board.sweep(minutes(220)) == []
    assert len(board.sweep(minutes(221))) == 1


def test_setting_changes_n_and_zero_turns_it_off(board):
    board.card("Doing one", "doing")
    status, body = board.setting({"minutes": 5})
    assert status == 200
    assert body["idle_nudge"]["effective"] == 5
    assert board.sweep(minutes(0)) == []
    assert board.sweep(minutes(4)) == []
    assert len(board.sweep(minutes(5))) == 1
    assert board.setting({"minutes": 0})[1]["idle_nudge"]["enabled"] is False
    assert board.sweep(minutes(500)) == []
    reset = board.setting({"minutes": None})[1]["idle_nudge"]
    assert (reset["minutes"], reset["effective"], reset["enabled"]) == (None, 20, True)


def test_setting_is_owner_only_and_validated(board):
    h = board.h
    for bad in ({"minutes": -1}, {"minutes": 1441}, {"minutes": "5"}, {"minutes": True}, {}):
        assert board.setting(bad)[0] == 400
    assert h.request(h.member, "PUT", "/api/projects/work/idle_nudge", {"minutes": 5})[0] == 403
    assert h.project(h.owner, "work")[1]["idle_nudge"]["effective"] == 20


def test_project_view_shows_the_nudge_until_a_card_is_done(board):
    open_card = board.card("Doing one", "doing")
    finished = board.card("Finished")
    board.sweep(minutes(0))
    assert board.h.project(board.h.owner, "work")[1]["idle_nudge"]["last"] is None
    board.sweep(minutes(20))
    last = board.h.project(board.h.owner, "work")[1]["idle_nudge"]["last"]
    assert (last["doing"], last["minutes"]) == (1, 20)
    board.done(finished, datetime.now(timezone.utc) + timedelta(days=1))
    assert board.h.project(board.h.owner, "work")[1]["idle_nudge"]["last"] is None
    assert open_card


def test_nudge_goes_to_the_lease_holder_before_the_seat(board):
    h = board.h
    from commonplace.orch import durable, store as orch_store

    store = orch_store.open_store(h.gateway, h.workspace)
    with store.connection() as conn:
        for statement in durable.SCHEMA_STATEMENTS:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO orch_leases(board,role,epoch,holder,holder_actor,token_hash,claimed_at,"
            "expires_at) VALUES ('work','main',1,'lead','conn:lead','x',?,?)",
            (iso(T0), iso(minutes(600))),
        )
    board.card("Doing one", "doing")
    board.sweep(minutes(0))
    assert board.sweep(minutes(20))[0]["data"]["coordinator"] == "conn:lead"


def test_no_recipient_means_no_nudge(board):
    board.h.request(board.h.owner, "PATCH", "/api/boards/work", {"coordinator": None})
    board.card("Doing one", "doing")
    assert board.sweep(minutes(0)) == []
    assert board.sweep(minutes(40)) == []


def test_expired_claims_and_pending_checks_are_counted(board):
    stuck = board.card("Expired", "doing")
    checks = board.card("Checks", "doing")
    board.sql("UPDATE tasks SET checks='pending' WHERE id=?", checks)
    board.setting({"minutes": 40})
    board.sweep(minutes(0))
    data = board.sweep(minutes(40))[0]["data"]
    assert (data["doing"], data["checks"], data["expired"]) == (2, 1, 2)
    assert stuck


def test_wake_reaches_only_the_coordinator_with_board_stuck_counts(board):
    h = board.h
    assert h.mcp(h.a1, "webhook_add", url="http://127.0.0.1:9/hook", events="board.idle_nudge")[1][
        "ok"
    ]
    other = h.hub.token(
        "bystander",
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["work"],
        write_wings=["work"],
        trust="trusted",
    )
    assert h.mcp(other, "webhook_add", url="http://127.0.0.1:9/hook", events="board.idle_nudge")[1][
        "ok"
    ]
    board.card("Doing one", "doing")
    board.card("Todo one")
    board.sweep(minutes(0))
    nudge = board.sweep(minutes(20))[0]
    with h.db._lock:
        rows = [
            dict(r)
            for r in h.db._conn.execute(
                "SELECT workspace_id,owner_actor,kinds,board,waiting_on FROM webhooks"
            )
        ]
    matched = [r["owner_actor"] for r in rows if webhooks._matches(r, nudge)]
    assert matched == ["conn:a1"]
    payload = json.loads(webhooks._payload(h.db, nudge))
    assert payload["kind"] == "board.idle_nudge"
    assert payload["agent"] == "commonplace"
    assert "work:" in payload["message"]
    assert "20 min since the last done card" in payload["message"]
    assert "1 doing, 0 checks pending, 0 expired claims, 1 todo" in payload["message"]
    assert "No card finished in the last 20 minutes." in payload["message"]
    assert payload["url"].startswith("http")
