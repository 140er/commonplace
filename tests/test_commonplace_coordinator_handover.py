"""The board coordinator releases restricted cards handed to it; others stay refused."""

import pytest

from commonplace.extensions import ToolError
from commonplace.projects.types import SEAT_CLIENT
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_board_authority import fresh as fresh
from tests.test_commonplace_boards import api
from tests.test_commonplace_origin_holder_release import edit, raw, snapshot

REASON = "Independent origin checked"
NOTE = "Handing this to the coordinator seat"
REFUSALS = (
    "restricted-origin work needs parent or workspace owner release"
    "|only the direct parent|not available to take"
)


def held_unreleased(fresh, *, seat_client=None, hub=None):
    """A restricted card nobody released, reserved for the seat that coordinates the board."""
    creator = fresh.connect("creator", trust="untrusted", write_wing="inbox")
    seat = fresh.connect("seat", trust="trusted")
    fresh.owner.board_set("work", seat.principal.actor)
    if seat_client:
        hub.db._conn.execute(
            "UPDATE connections SET client=?, last_used_at='2099-01-01T00:00:00Z' WHERE id=?",
            (seat_client, seat.id),
        )
    number = creator.work.task_add(board="work", title="Restricted work")["number"]
    edit(fresh.owner, number, assignee=seat.principal.actor, status="todo")
    return creator, seat, number


def refuse(work, number, call, match=REFUSALS):
    before = snapshot(work, number)
    with pytest.raises(ToolError, match=match):
        call()
    assert snapshot(work, number) == before


@pytest.mark.parametrize("client", [None, SEAT_CLIENT])
def test_coordinator_releases_a_restricted_card_it_holds(hub, fresh, client):
    _, seat, number = held_unreleased(fresh, seat_client=client, hub=hub)
    refuse(fresh.owner, number, lambda: seat.work.task_update(number, status="doing"))
    card = seat.work.task_approve(number, approve=True, reason=REASON)
    assert card["status"] == "todo" and card["assignee"] == seat.principal.actor
    assert raw(fresh.owner, number)["approved_by"] == seat.principal.actor
    assert seat.work.task_update(number, status="doing")["status"] == "doing"
    assert seat.work.task_heartbeat(number)["status"] == "doing"


@pytest.mark.parametrize("change", ["none", "replaced", "cleared"])
@pytest.mark.parametrize("releaser", ["owner", "coordinator"])
def test_handoff_to_the_coordinator_keeps_an_existing_release(hub, fresh, releaser, change):
    creator = fresh.connect("creator", trust="untrusted", write_wing="inbox")
    seat = fresh.connect("seat", trust="trusted")
    if releaser == "coordinator":
        fresh.owner.board_set("work", seat.principal.actor)
    number = creator.work.task_add(board="work", title="Restricted work")["number"]
    if releaser == "owner":
        fresh.owner.task_approve(number, reason=REASON)
        fresh.owner.board_set("work", seat.principal.actor)
    else:
        status, card = api(
            hub, seat.token, "POST", f"/api/cards/{number}/approve", {"reason": REASON}
        )
        assert status == 200, card
    assert creator.work.task_take_next(task=number)["status"] == "doing"
    creator.work.task_handoff(number, seat.principal.actor, NOTE)
    if change == "replaced":
        fresh.owner.board_set("work", fresh.connect("replacement").principal.actor)
    elif change == "cleared":
        fresh.owner.board_set("work", None)
    assert raw(fresh.owner, number)["assignee"] == seat.principal.actor
    assert seat.work.task_take_next(task=number)["status"] == "doing"
    assert seat.work.task_update(number, status_line="Still working")["status"] == "doing"
    back = seat.work.task_handoff(number, creator.principal.actor, "Back to the creator")
    assert back["status"] == "todo" and back["assignee"] == creator.principal.actor


@pytest.mark.parametrize("appointed", ["other-seat", "nobody", "replaced"])
def test_a_seat_that_is_not_the_board_coordinator_is_refused(hub, fresh, appointed):
    creator, seat, number = held_unreleased(fresh)
    if appointed == "other-seat":
        fresh.owner.board_set("work", fresh.connect("other-seat", trust="trusted").principal.actor)
    elif appointed == "nobody":
        fresh.owner.board_set("work", None)
    else:
        fresh.owner.board_set("work", fresh.connect("replacement").principal.actor)
    refuse(fresh.owner, number, lambda: seat.work.task_approve(number, approve=True, reason=REASON))
    refuse(fresh.owner, number, lambda: seat.work.task_update(number, status="doing"))
    refuse(fresh.owner, number, lambda: seat.work.task_take_next(task=number))
    refuse(
        fresh.owner,
        number,
        lambda: creator.work.task_approve(number, approve=True, reason=REASON),
    )
