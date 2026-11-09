"""A lost cross-store acknowledgement must not create duplicate board cards."""

import pytest

from commonplace import sessions
from commonplace.extensions import ToolError
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_boards import service


@pytest.fixture
def owner(hub):
    principal = sessions.person_principal(hub.db, hub.admin, hub.workspace)
    result = service(hub, principal)
    result.board_add(slug="coworker-work", wing="shared")
    return result


def intent(**changes):
    return {
        "board": "coworker-work",
        "title": "Inspect the example",
        "body": "Exact words.",
    } | changes


def test_lost_acknowledgement_replays_same_card_after_reopen(hub, owner):
    first = owner.ensure_chat_task("conversation:1:job:1", intent())
    reopened = service(hub, owner.principal)
    again = reopened.ensure_chat_task("conversation:1:job:1", intent())
    assert first["number"] == again["number"]
    assert len(owner.task_list(board="coworker-work")) == 1
    assert first["body"] == "Exact words."


def test_changed_payload_conflicts_instead_of_reusing_authorization(owner):
    owner.ensure_chat_task("conversation:1:job:1", intent())
    with pytest.raises(ToolError, match="conflicts"):
        owner.ensure_chat_task("conversation:1:job:1", intent(body="Different request"))
    assert len(owner.task_list(board="coworker-work")) == 1


def test_repeated_words_with_distinct_origin_are_distinct_requests(owner):
    first = owner.ensure_chat_task("conversation:1:job:1", intent())
    second = owner.ensure_chat_task("conversation:1:job:2", intent())
    assert first["number"] != second["number"]


def test_binding_failure_rolls_back_the_card_and_its_events(owner, monkeypatch):
    original = owner._new_card

    def fail_after_card(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("interrupted before origin binding")

    monkeypatch.setattr(owner, "_new_card", fail_after_card)
    with pytest.raises(RuntimeError, match="interrupted"):
        owner.ensure_chat_task("conversation:1:job:1", intent())
    assert owner.task_list(board="coworker-work") == []
    monkeypatch.setattr(owner, "_new_card", original)
    assert owner.ensure_chat_task("conversation:1:job:1", intent())["number"] > 0


def test_agent_cannot_impersonate_person_for_chat_dispatch(hub, owner):
    token = hub.owner_token("coworker-agent")
    agent = service(hub, hub.gateway.authenticate(f"Bearer {token}"))
    with pytest.raises(ToolError, match="requires a person"):
        agent.ensure_chat_task("conversation:1:job:1", intent())
    assert owner.task_list(board="coworker-work") == []
