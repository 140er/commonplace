"""The existing priority REST contract protects UI writes and stale Undo."""

import pytest

from commonplace import sessions
from tests.test_commonplace_board_authority import fresh as fresh
from tests.test_commonplace_boards import api, hub_fixture as hub_fixture


@pytest.mark.parametrize("source", ["default", "coworker"])
def test_choosing_normal_claims_the_priority_for_the_owner(hub, fresh, source):
    card = fresh.owner.task_add("Review the invitation", board="work")
    if source == "coworker":
        coworker = fresh.connect("studio-coworker")
        fresh.owner.board_set("work", coordinator=coworker.principal.actor)
        card = coworker.work.task_update(
            card["number"],
            version=card["version"],
            priority="normal",
            priority_reason="No deadline yet",
        )
    path = f"/api/cards/{card['number']}"
    assert api(hub, fresh.cookie, "GET", path)[1]["priority_source"] == source
    status, saved = api(
        hub, fresh.cookie, "PATCH", path, {"version": card["version"], "priority": "normal"}
    )
    assert status == 200
    assert (saved["priority"], saved["priority_source"]) == ("normal", "person")
    assert saved["version"] == card["version"] + 1
    shown = api(hub, fresh.cookie, "GET", path)[1]
    assert shown["events"][-1]["text"] == "Priority set to normal"


def test_undo_restores_only_the_level_and_stale_undo_preserves_newer_state(hub, fresh):
    card = fresh.owner.task_add("Review the invitation", board="work")
    path = f"/api/cards/{card['number']}"
    status, saved = api(
        hub, fresh.cookie, "PATCH", path, {"version": card["version"], "priority": "high"}
    )
    assert status == 200
    status, undone = api(
        hub, fresh.cookie, "PATCH", path, {"version": saved["version"], "priority": "normal"}
    )
    assert status == 200
    assert (undone["priority"], undone["priority_source"]) == ("normal", "person")
    newer = fresh.owner.task_update(card["number"], version=undone["version"], priority="urgent")
    status, _ = api(
        hub, fresh.cookie, "PATCH", path, {"version": saved["version"], "priority": "normal"}
    )
    assert status == 409
    shown = api(hub, fresh.cookie, "GET", path)[1]
    assert (shown["priority"], shown["version"]) == ("urgent", newer["version"])


def test_member_can_read_provenance_but_cannot_change_priority(hub, fresh):
    coworker = fresh.connect("studio-coworker")
    fresh.owner.board_set("work", coordinator=coworker.principal.actor)
    card = fresh.owner.task_add("Review the invitation", board="work")
    saved = coworker.work.task_update(
        card["number"],
        version=card["version"],
        priority="high",
        priority_reason="Blocks the invitation",
    )
    member = hub.db.create_user("priority-member@example.invalid")
    hub.db.add_member(fresh.workspace, member, "member", actor=fresh.user)
    cookie = {
        "Cookie": f"{sessions.COOKIE}={sessions.start_session(hub.db, member, fresh.workspace)}"
    }
    path = f"/api/cards/{card['number']}"
    status, shown = api(hub, cookie, "GET", path)
    assert status == 200 and shown["can_prioritize"] is False
    assert shown["priority_source"] == "coworker"
    assert shown["events"][-1]["text"].endswith("Blocks the invitation")
    status, _ = api(hub, cookie, "PATCH", path, {"version": saved["version"], "priority": "urgent"})
    assert status == 403
    assert api(hub, fresh.cookie, "GET", path)[1]["priority"] == "high"
