"""A connected project orchestrator releases new parentless restricted-origin cards (#345)."""

import pytest

from commonplace.extensions import ToolError
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_board_authority import fresh as fresh
from tests.test_commonplace_boards import api, mcp


def connect_orchestrator(hub, fresh):
    status, body = api(
        hub,
        fresh.cookie,
        "POST",
        "/api/projects/work/orchestrator/connect",
        {"allow_shared": True},
    )
    assert status == 201, body
    principal = hub.db.authenticate_token(body["token"])
    board = next(b for b in fresh.owner.board_list() if b["slug"] == "work")
    assert board["coordinator"] == principal.actor
    return principal


def orchestration_on(hub, fresh):
    status, body = api(hub, fresh.cookie, "PUT", "/api/settings/orchestration", {"enabled": True})
    assert status == 200, body


def restricted_creator(fresh, **kwargs):
    return fresh.connect("restricted", trust="untrusted", write_wing="inbox", **kwargs)


def test_connected_orchestrator_releases_new_parentless_restricted_card(hub, fresh):
    orchestrator = connect_orchestrator(hub, fresh)
    creator = restricted_creator(fresh)
    card = creator.work.task_add(board="work", title="Restricted idea")
    shown = fresh.owner.task_show(card["number"])
    assert shown["status"] == "todo"
    assert shown["approved_by"] == orchestrator.actor
    decision = [e for e in shown["events"] if e["kind"] == "decide"][-1]
    assert decision["actor"] == orchestrator.actor
    assert decision["choice"] == "release origin"
    assert creator.work.task_take_next(task=card["number"])["status"] == "doing"


def test_unrelated_agent_seat_still_cannot_release(hub, fresh):
    connect_orchestrator(hub, fresh)
    # Another card that stays unreleased: a child proposal needs its parent first.
    parent = fresh.connect("parent")
    child = fresh.connect("child", parent=parent.id)
    proposal = child.work.task_add(board="work", title="Child follow-up")
    assert proposal["approved_by"] is None
    stranger = fresh.connect("stranger")
    with pytest.raises(ToolError):
        stranger.work.task_approve(proposal["number"], reason="Not my card")
    ok, reply = mcp(hub, stranger.token, "task_approve", task=proposal["number"], reason="Nope")
    assert not ok, reply
    assert fresh.owner.task_show(proposal["number"])["approved_by"] is None


def test_child_proposal_is_not_auto_released(hub, fresh):
    connect_orchestrator(hub, fresh)
    parent = fresh.connect("parent")
    child = fresh.connect("child", parent=parent.id)
    card = child.work.task_add(board="work", title="Child follow-up")
    shown = fresh.owner.task_show(card["number"])
    assert shown["status"] == "backlog" and shown["approved_by"] is None
    assert shown["proposal_status"] == "pending"


def test_other_coordinator_does_not_auto_release(hub, fresh):
    coordinator = fresh.connect("orchestrator", trust="standard")
    fresh.owner.board_set("work", coordinator.principal.actor)
    card = restricted_creator(fresh).work.task_add(board="work", title="Restricted idea")
    shown = fresh.owner.task_show(card["number"])
    assert shown["status"] == "backlog" and shown["approved_by"] is None


def test_creator_cannot_release_its_own_card(hub, fresh):
    creator = restricted_creator(fresh)
    card = creator.work.task_add(board="work", title="Restricted idea")
    shown = fresh.owner.task_show(card["number"])
    assert shown["status"] == "backlog" and shown["approved_by"] is None
    with pytest.raises(ToolError):
        creator.work.task_approve(card["number"], reason="Self release")


def test_card_says_who_releases_when_no_orchestrator_is_connected(hub, fresh):
    orchestration_on(hub, fresh)
    card = restricted_creator(fresh).work.task_add(board="work", title="Restricted idea")
    waiting = fresh.owner.task_show(card["number"])["waiting"]
    assert waiting["code"] == "not_released"
    assert waiting["text"] == (
        "Waiting for release: no connected Coworker can release it, so the workspace owner does"
    )


def test_card_says_when_the_coordinator_releases(hub, fresh):
    orchestration_on(hub, fresh)
    coordinator = fresh.connect("orchestrator", trust="standard")
    fresh.owner.board_set("work", coordinator.principal.actor)
    card = restricted_creator(fresh).work.task_add(board="work", title="Restricted idea")
    waiting = fresh.owner.task_show(card["number"])["waiting"]
    assert waiting["text"] == "Waiting for release: the board coordinator releases it"
