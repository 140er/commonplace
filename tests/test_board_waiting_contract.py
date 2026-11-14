"""Waiting explanations stay aligned with selection, counts and read authority."""

import json
import subprocess
from collections import Counter

import pytest

from commonplace.boards import tools
from commonplace.boards.service import BoardService
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests._phase_b_helpers import board, cap, claim, doing
from tests._split_helpers import blockers, take, world
from tests.runner_harness import http


def test_limit_and_blocker_sentences(hub):
    w = world(hub)
    dependency = w.card(title="Publish schema")
    dependent = w.card(title="Build reader")
    free = w.card(title="Other work")
    blockers(w, dependent, dependency)
    cap(w, 1)
    doing(w, w.card(title="In progress"), w.b.actor)
    cards = {c["number"]: c for c in w.boards.task_list(board="acme")}
    assert cards[free]["waiting"]["text"] == "Waiting for a free slot: 1 of 1 agents busy"
    assert cards[dependent]["waiting"]["text"] == f"Waiting for #{dependency}: Publish schema"
    for number in (free, dependent):
        status, refused = claim(w, number)
        assert status == 409
        assert refused["error"] == "no: " + cards[number]["waiting"]["text"]


def test_summary_matches_visible_card_reasons_and_omits_archived(hub):
    w = world(hub)
    cap(w, 1)
    doing(w, w.card(title="Working"), w.b.actor)
    dependency = w.card(title="Dependency")
    dependent = w.card(title="Dependent")
    blockers(w, dependent, dependency)
    approval = w.card(title="Approval")
    with w.boards.store.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status='review',ask='Proceed?',ask_to=? WHERE id=?",
            (w.owner.actor, approval),
        )
    archived = w.card(title="Archived")
    with w.boards.store.transaction() as conn:
        conn.execute("INSERT INTO card_archive(task_id,archived) VALUES (?,1)", (archived,))
    cards = w.boards.task_list(board="acme", all_statuses=True)
    reasons = Counter(c["waiting"]["code"] for c in cards if c.get("waiting"))
    result = board(w)
    assert {k: v for k, v in result["waiting"].items() if v} == dict(reasons)
    assert result["waiting_summary"] == (
        "1 agent busy (limit 1). 1 card waits for a free slot, "
        "1 card waits for another card, 1 card waits for a person."
    )
    assert result["waiting_task_summary"] == (
        "1 agent busy (limit 1). 1 task waits for a free agent, "
        "1 waits for another task, 1 waits for a person."
    )
    assert next(c for c in cards if c["number"] == approval)["waiting"]["code"] == "waits_on_person"
    status, refused = claim(w, approval)
    assert status == 409 and refused["code"] == "waits_on_person"
    assert result["waiting_summary"] in tools.run(w.ctx(w.owner), "board_list", {})
    assert "Waiting for a free slot: 1 of 1 agents busy" in tools.run(
        w.ctx(w.owner), "task_list", {"board": "acme"}
    )
    token = hub.owner_token("summary-reader")
    status, payload = http(hub.port, "GET", "/api/boards", token=token)
    assert status == 200
    assert (
        next(b for b in payload["boards"] if b["slug"] == "acme")["waiting_summary"]
        == result["waiting_summary"]
    )


def test_automatic_selection_and_display_use_the_same_reason_function(hub, monkeypatch):
    w = world(hub)
    first, dependency = w.card(title="Dependent"), w.card(title="Dependency")
    blockers(w, first, dependency)
    from commonplace.boards import why

    original = why.waiting
    seen = []

    def observed(row, **kwargs):
        reason = original(row, **kwargs)
        if row["id"] == first:
            seen.append(reason)
        return reason

    monkeypatch.setattr(why, "waiting", observed)
    shown = w.boards.task_show(first)["waiting"]
    seen.clear()
    status, picked = take(w)
    assert status == 200 and picked["number"] == dependency
    assert shown in seen, "selection bypassed the displayed reason function"
    status, refusal = take(w, first)
    assert status == 409 and refusal["error"] == shown["text"]


def test_waiting_reads_cannot_disclose_an_unreadable_blocker(hub):
    w = world(hub)
    w.boards.board_add(slug="vault", wing="vault")
    hidden = w.card(board="vault", title="Private dependency title")
    visible = w.card(title="Visible")
    # Simulate an old invalid edge: a read must not trust the stored relation.
    blockers(w, visible, hidden)
    service = BoardService(w.ctx(w.a))
    shown = service.task_show(visible)
    assert shown["blocked_by"] == []
    assert "Private dependency title" not in json.dumps(service.task_list(all_statuses=True))
    token = hub.token("reader", profile="full", scopes=["memory:read"], read_wings=["shared"])
    status, payload = http(hub.port, "GET", "/api/boards/acme/cards", token=token)
    assert status == 200 and "Private dependency title" not in json.dumps(payload)
    assert http(hub.port, "GET", f"/api/cards/{hidden}", token=token)[0] == 404
    status, rows = http(hub.port, "GET", "/api/boards/vault/cards", token=token)
    assert status == 404
    other = w.db.create_workspace("other", hub.admin)
    foreign = hub.token(
        "other-reader",
        workspace=other,
        profile="full",
        scopes=["memory:read"],
        read_wings=["shared"],
    )
    assert http(hub.port, "GET", f"/api/cards/{visible}", token=foreign)[0] == 404
    assert "Private dependency title" not in tools.run(w.ctx(w.a), "task_list", {})


def test_summary_uses_the_configured_limit(hub):
    w = world(hub)
    cap(w, 7)
    for n in range(7):
        doing(w, w.card(title=f"Working {n}"), w.pat(f"busy-{n}").actor)
    w.card(title="Waiting")
    assert (
        board(w).get("waiting_summary") == "7 agents busy (limit 7). 1 card waits for a free slot."
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_take_next_keeps_default_order_and_existing_holder(hub, enabled):
    w = world(hub, on=enabled)
    first, second = w.card(title="First"), w.card(title="Second")
    status, picked = take(w)
    assert status == 200 and picked["number"] == first
    assert claim(w, first)[1]["number"] == first
    assert take(w)[1]["number"] == second


def test_node_summary_is_the_server_line_in_board_and_agent_header():
    result = subprocess.run(
        ["node", "tests/board_waiting_summary.mjs"], capture_output=True, text=True, check=True
    )
    shown = json.loads(result.stdout)
    assert shown["board"] is True and shown["header"] is True
    assert shown["escaped"] is True and shown["absent"] is True
    assert shown["taskCopy"] is True
    assert shown["working"] == ["worker (agent) is working on it"]
    assert shown["workingTimed"] == ["worker (agent) is working on it · 2m ago"]
