"""A card waiting for release stays in the owner's Today, with its creator and trust on the card (#663)."""

import subprocess
from pathlib import Path

import pytest

from commonplace import sessions
from commonplace.extensions import ToolError
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_board_authority import fresh as fresh
from tests.test_commonplace_boards import service


def waiting_creator(fresh):
    return fresh.connect("restricted", trust="untrusted", write_wing="inbox")


def release_numbers(groups):
    return [c["number"] for c in groups["approvals"] if c.get("approval_kind") == "origin"]


def test_waiting_card_is_in_today_without_a_coworker(fresh):
    number = waiting_creator(fresh).work.task_add(board="work", title="Waiting")["number"]
    assert release_numbers(fresh.owner.needs_me()) == [number]


def test_waiting_card_is_in_today_with_a_connected_coworker(fresh):
    coordinator = fresh.connect("orchestrator", trust="standard")
    fresh.owner.board_set("work", coordinator.principal.actor)
    number = waiting_creator(fresh).work.task_add(board="work", title="Waiting")["number"]
    groups = fresh.owner.needs_me()
    assert release_numbers(groups) == [number]
    item = next(c for c in groups["approvals"] if c["number"] == number)
    assert item["actions"] == ["approve"] and item["can_approve"] is True
    assert item["need"] == "release"


def test_member_does_not_see_the_waiting_card(hub, fresh):
    number = waiting_creator(fresh).work.task_add(board="work", title="Waiting")["number"]
    user = hub.db.create_user("member@example.test")
    hub.db.add_member(fresh.workspace, user, "member", actor=fresh.user)
    member = service(hub, sessions.person_principal(hub.db, user, fresh.workspace))
    try:
        groups = member.needs_me()
    except ToolError:
        return
    assert number not in release_numbers(groups)
    with pytest.raises(ToolError):
        member.task_approve(number, reason="Not the owner")


def test_release_from_today_uses_the_existing_release_path(fresh):
    coordinator = fresh.connect("orchestrator", trust="standard")
    fresh.owner.board_set("work", coordinator.principal.actor)
    number = waiting_creator(fresh).work.task_add(board="work", title="Waiting")["number"]
    released = fresh.owner.task_approve(number, reason="Checked by the owner")
    assert released["approved_by"] == f"user:{fresh.user}"
    assert release_numbers(fresh.owner.needs_me()) == []


@pytest.mark.parametrize("trust", ["untrusted", "trusted"])
def test_card_read_names_the_creator_and_trust(fresh, trust):
    creator = fresh.connect("restricted", trust=trust, write_wing="inbox")
    number = creator.work.task_add(board="work", title="Waiting")["number"]
    shown = fresh.owner.task_show(number)
    assert shown["awaiting_release"] is True
    assert shown["creator"] == {"name": "restricted", "trust": trust, "inbox_only": True}


def test_release_block_in_the_card_and_in_today():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("release_block.mjs"))],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
