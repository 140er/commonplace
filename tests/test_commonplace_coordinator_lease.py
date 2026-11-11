"""Only a live coordinator lease of exactly this board makes a seat the board coordinator."""

import pytest

from commonplace.boards.service import BoardService
from commonplace.extensions import ToolContext, ToolError
from commonplace.orch import durable
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_origin_holder_release import edit, raw, snapshot
from tests.test_orch_durable import BOARD, ROLE, T0, Env, at

REASON = "Independent origin checked"
PAST = "2020-01-01T00:00:00Z"


@pytest.fixture
def env(hub):
    return Env(hub)


def work_for(env, principal):
    return BoardService(ToolContext(principal=principal, gateway=env.hub.gateway))


def restricted_card(env, board=BOARD):
    token = env.hub.token(
        "creator",
        profile="standard",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared"],
        write_wing="inbox",
        trust="untrusted",
    )
    creator = env.hub.gateway.authenticate(f"Bearer {token}")
    return work_for(env, creator).task_add(board=board, title="Restricted work")["number"]


def refuse(env, work, number, call, match="only the direct parent|not available to take"):
    before = snapshot(env.owner_board, number)
    with pytest.raises(ToolError, match=match):
        call()
    assert snapshot(env.owner_board, number) == before


def approve(work, number):
    return work.task_approve(number, approve=True, reason=REASON)


def test_the_live_coordinator_lease_holder_releases_the_card(hub, env):
    number = restricted_card(env)
    seat = work_for(env, env.a)
    refuse(env, seat, number, lambda: approve(seat, number))
    env.resume(env.a)
    card = approve(seat, number)
    assert card["status"] == "todo"
    assert raw(env.owner_board, number)["approved_by"] == env.a.actor


def test_a_replaced_epoch_is_refused_and_the_new_holder_is_accepted(hub, env):
    number = restricted_card(env)
    first, second = work_for(env, env.a), work_for(env, env.b)
    assert env.resume(env.a)["epoch"] == 1
    assert env.resume(env.b, force=False, now=at(durable.TTL_SECONDS + 1, T0))["epoch"] == 2
    refuse(env, first, number, lambda: approve(first, number))
    assert approve(second, number)["status"] == "todo"


def test_an_expired_lease_is_refused(hub, env):
    number = restricted_card(env)
    seat = work_for(env, env.a)
    env.resume(env.a, now=PAST)
    refuse(env, seat, number, lambda: approve(seat, number))


def test_a_released_lease_is_refused(hub, env):
    number = restricted_card(env)
    seat = work_for(env, env.a)
    claim = env.resume(env.a)
    durable.release(env.ctx(env.a), BOARD, ROLE, claim["epoch"], claim["token"], now=T0)
    refuse(env, seat, number, lambda: approve(seat, number))


def test_a_lease_for_another_board_is_refused(hub, env):
    number = restricted_card(env, board="plain")
    seat = work_for(env, env.a)
    env.resume(env.a)
    refuse(env, seat, number, lambda: approve(seat, number))


def test_a_seat_without_the_lease_is_refused_while_another_holds_it(hub, env):
    number = restricted_card(env)
    other = work_for(env, env.b)
    env.resume(env.a)
    refuse(env, other, number, lambda: approve(other, number))


def test_a_lease_holder_that_is_the_cards_creator_cannot_take_over_its_reservation(hub, env):
    number = restricted_card(env)
    env.owner_board.task_approve(number, reason=REASON)
    env.resume(env.a)
    edit(env.owner_board, number, created_by=env.a.actor, assignee=env.b.actor)
    seat = work_for(env, env.a)
    refuse(env, seat, number, lambda: seat.task_take_next(task=number), match=None)
