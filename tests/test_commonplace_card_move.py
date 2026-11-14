"""A person moves a card between the Deck's columns: the route, its refusals and the menu's options.

Moving to Review is a plain move (not a question to anyone), a stale version says what happened,
and every destination the menu offers is one the server accepts (and the reverse)."""

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests._commonplace_helpers import REVIEW_CONTEXT, REVIEW_OPTIONS, hub_fixture  # noqa: F401
from tests.test_commonplace_board_authority import fresh as fresh
from tests.test_commonplace_boards import api

OPTIONS_SCRIPT = Path(__file__).with_name("card_move_options.mjs")
COLUMN_STATUS = {"doing": "doing", "review": "review", "done": "done"}


def patch(hub, fresh, number, **body):
    body.setdefault("version", fresh.owner.task_show(number)["version"])
    return api(hub, fresh.cookie, "PATCH", f"/api/cards/{number}", body)


def card_in(hub, fresh, status):
    """A new owner card, brought to ``status`` by the moves a person makes."""
    number = fresh.owner.task_add(board="work", title=f"Card in {status}")["number"]
    for step in {
        "todo": [],
        "backlog": ["backlog"],
        "doing": ["doing"],
        "review": ["doing", "review"],
        "done": ["doing", "done"],
    }[status]:
        code, reply = patch(hub, fresh, number, status=step)
        assert code == 200, reply
    return number


def test_a_person_moves_a_card_to_review_without_asking_anything(hub, fresh):
    number = card_in(hub, fresh, "doing")
    code, reply = patch(hub, fresh, number, status="review")
    assert code == 200, reply
    moved = fresh.owner.task_show(number)
    assert moved["status"] == "review"
    # Nobody is asked: it is not a decision waiting on the owner.
    assert (
        moved["review_kind"] == "moved" and moved["ask_to"] is None and moved["ask_options"] == []
    )
    assert moved["need"] != "you"


@pytest.mark.parametrize("target", ["todo", "doing", "done"])
def test_a_person_can_move_on_from_a_review_they_moved(hub, fresh, target):
    number = card_in(hub, fresh, "review")
    code, reply = patch(hub, fresh, number, status=target)
    assert code == 200, reply
    assert fresh.owner.task_show(number)["status"] == target
    assert fresh.owner.task_show(number)["ask"] is None


def test_a_question_an_agent_asked_is_answered_not_moved_past(hub, fresh):
    asker = fresh.connect("asker")
    waiting = asker.work.ask(
        "Ready to close? Which approach should we take?",
        board="work",
        context=REVIEW_CONTEXT,
        options=REVIEW_OPTIONS,
    )
    code, reply = patch(hub, fresh, waiting["number"], status="doing")
    assert code == 403, reply
    assert "decide" in reply["error"]
    assert fresh.owner.task_show(waiting["number"])["status"] == "review"


def test_asking_with_a_question_still_opens_the_decision(hub, fresh):
    number = card_in(hub, fresh, "doing")
    code, reply = patch(
        hub,
        fresh,
        number,
        status="review",
        question="Which approach should we take for the rollout?",
        context=REVIEW_CONTEXT,
        options=REVIEW_OPTIONS,
    )
    assert code == 200, reply
    asked = fresh.owner.task_show(number)
    assert asked["review_kind"] == "decision" and asked["ask_options"] == REVIEW_OPTIONS


def test_a_second_tap_with_the_old_version_names_the_current_card(hub, fresh):
    number = card_in(hub, fresh, "todo")
    seen = fresh.owner.task_show(number)["version"]
    assert patch(hub, fresh, number, status="doing", version=seen)[0] == 200
    code, reply = patch(hub, fresh, number, status="doing", version=seen)
    # The refusal is a version clash and carries the card as it is now: the client can tell the move
    # already happened, instead of reporting a change nobody made.
    assert code == 409 and reply["error"] == "card changed", reply
    assert reply["current"]["status"] == "doing" and reply["current"]["version"] > seen


def test_a_refused_move_that_is_not_a_version_clash_says_why(hub, fresh):
    number = card_in(hub, fresh, "todo")
    until = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh.owner.task_snooze(number, until=until)
    code, reply = patch(hub, fresh, number, status="doing")
    assert code == 409, reply
    # Its own code and reason (no "current" card): a client must not call this a stale card.
    assert (
        reply["code"] == "snoozed"
        and "snoozed" in reply["error"].lower()
        and "current" not in reply
    )


def menu_options():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, str(OPTIONS_SCRIPT)], capture_output=True, text=True, timeout=20, check=True
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("status", ["backlog", "todo", "doing", "review", "done"])
def test_the_menu_offers_exactly_the_moves_the_server_accepts(hub, fresh, status):
    options = menu_options()[status]
    assert [o["name"] for o in options] == ["Active", "In review", "Shipped"]
    for option in options:
        number = card_in(hub, fresh, status)
        if option.get("current"):
            continue
        # The destination a click sends: the option's own status, or the column's when it is off.
        target = option.get("status") or COLUMN_STATUS[option["column"]]
        code, reply = patch(hub, fresh, number, status=target)
        if option.get("disabled"):
            assert code == 403, (status, option["name"], reply)
        else:
            assert code == 200, (status, option["name"], reply)
            assert fresh.owner.task_show(number)["status"] == target


def test_a_question_waiting_for_the_owner_disables_every_move(hub, fresh):
    assert all(o["disabled"] for o in menu_options()["asked"])
