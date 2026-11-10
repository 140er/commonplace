"""A coordinator release survives real reservation moves; refusals stay plain."""

import pytest

from commonplace import sessions
from commonplace.extensions import ToolError
from commonplace.projects.types import SEAT_CLIENT
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_board_authority import fresh as fresh
from tests.test_commonplace_boards import api, service
from tests.test_commonplace_origin_holder_release import edit, policy, raw, snapshot

NOTE = "Hand this to the coordinator seat"
REASON = "Independent origin checked"


def released(hub, fresh):
    """The live order: submit, coordinator release, creator claim."""
    creator = fresh.connect("creator", trust="untrusted", write_wing="inbox")
    coordinator = fresh.connect("coordinator", trust="standard")
    fresh.owner.board_set("work", coordinator.principal.actor)
    number = creator.work.task_add(board="work", title="Restricted work")["number"]
    status, card = api(
        hub, coordinator.token, "POST", f"/api/cards/{number}/approve", {"reason": REASON}
    )
    assert status == 200, card
    assert creator.work.task_take_next(task=number)["status"] == "doing"
    return creator, coordinator, number


def appoint_elsewhere(hub, fresh, coordinator, change):
    if change == "replaced":
        fresh.owner.board_set("work", fresh.connect("replacement").principal.actor)
    elif change == "cleared":
        fresh.owner.board_set("work", None)
    else:
        hub.db._conn.execute(
            "UPDATE connections SET client=?, last_used_at='2000-01-01T00:00:00Z' WHERE id=?",
            (SEAT_CLIENT, coordinator.id),
        )


def refuse(work, number, call):
    before = snapshot(work, number)
    with pytest.raises(ToolError):
        call()
    assert snapshot(work, number) == before


@pytest.mark.parametrize("change", ["replaced", "cleared", "seat-idle"])
def test_release_survives_handoff_to_the_coordinator_seat(hub, fresh, change):
    creator, coordinator, number = released(hub, fresh)
    creator.work.task_handoff(number, coordinator.principal.actor, NOTE)
    appoint_elsewhere(hub, fresh, coordinator, change)
    assert coordinator.work.task_take_next(task=number)["status"] == "doing"
    assert coordinator.work.task_heartbeat(number)["status"] == "doing"
    assert coordinator.work.task_update(number, status_line="Still working")["status"] == "doing"
    back = coordinator.work.task_handoff(number, creator.principal.actor, "Back to the creator")
    assert back["status"] == "todo" and back["assignee"] == creator.principal.actor


def test_release_survives_handoff_to_another_trusted_seat(hub, fresh):
    creator, coordinator, number = released(hub, fresh)
    seat = fresh.connect("other-seat", trust="trusted")
    creator.work.task_handoff(number, seat.principal.actor, NOTE)
    appoint_elsewhere(hub, fresh, coordinator, "replaced")
    assert seat.work.task_take_next(task=number)["status"] == "doing"


def test_owner_releases_card_with_a_live_coordinator(hub, fresh):
    creator = fresh.connect("creator", trust="untrusted", write_wing="inbox")
    coordinator = fresh.connect("coordinator", trust="standard")
    fresh.owner.board_set("work", coordinator.principal.actor)
    number = creator.work.task_add(board="work", title="Waiting")["number"]
    assert fresh.owner.task_show(number)["can_approve"] is True
    card = fresh.owner.task_approve(number, reason=REASON)
    assert card["approved_by"] == f"user:{fresh.user}"
    assert card["status"] == "todo"
    assert fresh.owner.task_show(number).get("awaiting_release") is not True


def test_handoff_reaches_the_orchestrator_and_the_pool(hub, fresh):
    _, coordinator, number = released(hub, fresh)
    version = raw(fresh.owner, number)["version"]
    card = fresh.owner.task_handoff(number, "orchestrator", NOTE, version=version)
    assert card["assignee"] == coordinator.principal.actor
    version = raw(fresh.owner, number)["version"]
    card = fresh.owner.task_handoff(number, "pool", NOTE, version=version)
    assert card["assignee"] is None and card["status"] == "todo"


def test_handoff_without_a_coordinator_says_why(hub, fresh):
    _, _, number = released(hub, fresh)
    fresh.owner.board_set("work", None)
    with pytest.raises(ToolError, match="no board coordinator"):
        fresh.owner.task_handoff(number, "orchestrator", NOTE)


@pytest.mark.parametrize("actor", ["owner", "coordinator"])
def test_released_reservation_can_be_taken_over(hub, fresh, actor):
    creator, coordinator, number = released(hub, fresh)
    creator.work.task_handoff(number, fresh.connect("holder").principal.actor, NOTE)
    work = fresh.owner if actor == "owner" else coordinator.work
    card = work.task_take_next(task=number)
    assert card["status"] == "doing" and card["assignee"] == work.principal.actor


@pytest.mark.parametrize("actor", ["owner", "coordinator"])
def test_released_reservation_can_be_dropped(hub, fresh, actor):
    creator, coordinator, number = released(hub, fresh)
    creator.work.task_handoff(number, fresh.connect("holder").principal.actor, NOTE)
    work = fresh.owner if actor == "owner" else coordinator.work
    card = work.task_update(number, status="backlog", version=raw(fresh.owner, number)["version"])
    assert card["status"] == "backlog" and card["assignee"] is None


@pytest.mark.parametrize("relation", ["unrelated", "descendant"])
def test_reservation_takeover_stays_refused(hub, fresh, relation):
    creator, _, number = released(hub, fresh)
    creator.work.task_handoff(number, fresh.connect("holder").principal.actor, NOTE)
    parent = creator.id if relation == "descendant" else None
    actor = fresh.connect("intruder", parent=parent, trust="trusted")
    refuse(fresh.owner, number, lambda: actor.work.task_take_next(task=number))
    refuse(
        fresh.owner,
        number,
        lambda: actor.work.task_update(
            number, status="backlog", version=raw(fresh.owner, number)["version"]
        ),
    )


def test_owner_closes_a_released_unheld_card(hub, fresh):
    _, _, number = released(hub, fresh)
    fresh.owner.task_handoff(number, "anyone", NOTE, version=raw(fresh.owner, number)["version"])
    version = raw(fresh.owner, number)["version"]
    assert fresh.owner.task_update(number, status="done", version=version)["status"] == "done"


def test_member_cannot_close_a_released_unheld_card(hub, fresh):
    _, _, number = released(hub, fresh)
    fresh.owner.task_handoff(number, "anyone", NOTE, version=raw(fresh.owner, number)["version"])
    user = hub.db.create_user("member@example.test")
    hub.db.add_member(fresh.workspace, user, "member", actor=fresh.user)
    member = service(hub, sessions.person_principal(hub.db, user, fresh.workspace))
    refuse(
        fresh.owner,
        number,
        lambda: member.task_update(
            number, status="done", version=raw(fresh.owner, number)["version"]
        ),
    )


def member_session(hub, fresh):
    user = hub.db.create_user("member@example.test")
    hub.db.add_member(fresh.workspace, user, "member", actor=fresh.user)
    return service(hub, sessions.person_principal(hub.db, user, fresh.workspace))


def waiting_card(fresh):
    creator = fresh.connect("creator", trust="untrusted", write_wing="inbox")
    return creator, creator.work.task_add(board="work", title="Waiting")["number"]


def recorded_marker(hub, fresh, number, actor):
    name = actor.principal.actor[5:]
    edit(
        fresh.owner,
        number,
        status="todo",
        approved_by=actor.principal.actor,
        assignee=actor.principal.actor,
    )
    with fresh.owner.store.transaction() as conn:
        conn.execute(
            "INSERT INTO task_events(task_id,kind,choice,stamp_user,stamp_connection,stamp_trust,"
            "created_at) VALUES (?,?,?,?,?,?,?)",
            (
                number,
                "decide",
                "release origin",
                fresh.user,
                name,
                "trusted",
                "2026-11-10T00:00:00Z",
            ),
        )


def test_release_lapses_when_the_approver_loses_its_grants(hub, fresh):
    creator, coordinator, number = released(hub, fresh)
    creator.work.task_handoff(number, coordinator.principal.actor, NOTE)
    fresh.owner.board_set("work", fresh.connect("replacement").principal.actor)
    policy(hub, coordinator, trust="untrusted", write_wing="inbox")
    principal = hub.db.authenticate_connection(coordinator.id)
    refuse(fresh.owner, number, lambda: service(hub, principal).task_take_next(task=number))


def test_creator_recorded_release_is_not_authority(hub, fresh):
    creator, number = waiting_card(fresh)
    policy(hub, creator, trust="trusted", write_wing="shared")
    principal = hub.db.authenticate_connection(creator.id)
    recorded_marker(hub, fresh, number, creator)
    refuse(fresh.owner, number, lambda: service(hub, principal).task_take_next(task=number))


def test_owner_cannot_take_an_unreleased_reservation(hub, fresh):
    _, number = waiting_card(fresh)
    edit(fresh.owner, number, status="todo", assignee=fresh.connect("holder").principal.actor)
    refuse(fresh.owner, number, lambda: fresh.owner.task_take_next(task=number))


@pytest.mark.parametrize("coordinator", [True, False])
def test_owner_cannot_close_an_unreleased_card(hub, fresh, coordinator):
    if coordinator:
        fresh.owner.board_set(
            "work", fresh.connect("coordinator", trust="standard").principal.actor
        )
    _, number = waiting_card(fresh)
    edit(fresh.owner, number, status="todo", assignee=None)
    version = raw(fresh.owner, number)["version"]
    refuse(
        fresh.owner, number, lambda: fresh.owner.task_update(number, status="done", version=version)
    )


def test_member_session_cannot_release(hub, fresh):
    _, number = waiting_card(fresh)
    member = member_session(hub, fresh)
    refuse(fresh.owner, number, lambda: member.task_approve(number, reason=REASON))


def test_owner_move_does_not_release_with_a_live_coordinator(hub, fresh):
    fresh.owner.board_set("work", fresh.connect("coordinator", trust="standard").principal.actor)
    _, number = waiting_card(fresh)
    version = raw(fresh.owner, number)["version"]
    refuse(
        fresh.owner, number, lambda: fresh.owner.task_update(number, status="todo", version=version)
    )
