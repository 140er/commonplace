"""The board card read does per-list work once and keeps every card's waiting reason."""

import json
import sqlite3

from commonplace import sessions
from commonplace.boards.service import BoardService, _plan_waiting
from commonplace.extensions import ToolContext
from commonplace.projects import orchestration
from tests._commonplace_helpers import hub_fixture  # noqa: F401


def owner_service(hub):
    principal = sessions.person_principal(hub.db, hub.admin, hub.workspace)
    orchestration.ensure_schema(hub.db)
    orchestration.set_enabled(hub.db, principal, True)
    return BoardService(ToolContext(principal=principal, gateway=hub.gateway))


def test_card_list_reads_the_coworker_setting_once(hub, monkeypatch):
    service = owner_service(hub)
    service.board_add(slug="project", wing="shared")
    for n in range(12):
        service.task_add(board="project", title=f"Card {n}", status="todo" if n % 3 else "backlog")
    reads = []
    original = orchestration.enabled
    monkeypatch.setattr(
        orchestration, "enabled", lambda *args: reads.append(args) or original(*args)
    )
    cards = service.task_list(board="project", all_statuses=True)
    assert len(cards) == 12
    # Once for the list's own projection; the pinned code asked once more for every card (13 reads here).
    assert len(reads) <= 2
    assert all("waiting" in card for card in cards)


def test_card_list_keeps_each_waiting_reason_and_snooze(hub):
    service = owner_service(hub)
    service.board_add(slug="project", wing="shared")
    plain = service.task_add(board="project", title="Plain", status="todo")
    planned = service.task_add(board="project", title="Planned", status="todo", labels=["plan"])
    backlog = service.task_add(board="project", title="Backlog", status="backlog")
    snoozed = service.task_add(board="project", title="Snoozed", status="todo")
    service.task_snooze(snoozed["number"], until="2026-12-31T00:00:00Z")
    by_number = {c["number"]: c for c in service.task_list(board="project", all_statuses=True)}
    for number in (plain, planned, backlog, snoozed):
        shown = service.task_show(number["number"])
        assert by_number[number["number"]]["waiting"] == shown["waiting"]
        assert by_number[number["number"]].get("snoozed") == shown.get("snoozed")
    assert by_number[snoozed["number"]]["waiting"]["code"] == "snoozed"
    assert by_number[planned["number"]]["waiting"]["code"] == "plan_review"


def old_plan_waiting(row):
    from commonplace.boards.display import plan_waiting

    data = dict(row)
    if isinstance(data.get("labels"), str):
        data["labels"] = json.loads(data["labels"] or "[]")
    return plan_waiting(data)


def test_plan_waiting_gives_the_same_answer_with_cheap_exits():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE tasks (status, labels, proposal_status, ask, assignee, ask_to, return_to)"
    )
    cases = []
    for status in ("backlog", "todo", "doing", "review", "done"):
        for labels in ("[]", '["plan"]', '["plan","x"]', '["other"]', None):
            for proposal in (None, "pending", "rejected", "approved"):
                for ask, assignee in ((None, None), ("question", None), (None, "user:a")):
                    cases.append((status, labels, proposal, ask, assignee, None, None))
    conn.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?,?)", cases)
    rows = conn.execute("SELECT * FROM tasks").fetchall()
    assert len(rows) == len(cases)
    assert any(old_plan_waiting(row) for row in rows)
    assert [_plan_waiting(row) for row in rows] == [old_plan_waiting(row) for row in rows]
