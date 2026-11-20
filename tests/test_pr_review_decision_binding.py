"""Question-bound decisions: real board writes, local HTTP and delayed first application."""

import json
from datetime import datetime, timezone

import pytest

from commonplace.pr_review import intake, mapping, needs_me
from tests._console_helpers import console_hub_fixture  # noqa: F401
from tests.test_commonplace_boards import service
from tests.test_pr_review import (
    REPO,
    asked_card,
    batch,
    comment,
    rows,
    when,
    world,  # noqa: F401
)


def revise(h):
    now = datetime.now(timezone.utc)
    request = intake.validate(
        batch(comment(1, "Please also rename the helper.", updated_at=when(-1))), now
    )
    assert h.store.record(request, now)["edited"] == 1
    assert rows(h)["1"]["rev"] == 2


@pytest.mark.parametrize("label", ["Two more rounds", "I will do it myself", "Ignore"])
def test_unapplied_external_answer_cannot_decide_a_later_question(world, monkeypatch, label):  # noqa: F811
    h = world
    number = asked_card(h, monkeypatch)
    owner = service(h, h.person(h.admin))
    owner.decide(number, choice=label, then="return")
    assert h.store.pr(REPO, 7)["decided_event"] == 0
    revise(h)
    now = datetime.now(timezone.utc)
    h.store.hold(REPO, 7, "rounds_exhausted", "system", now)
    assert needs_me.raise_ask(
        h.gateway,
        h.workspace,
        h.store,
        h.store.pr(REPO, 7),
        mapping.card_facts(h.gateway, h.workspace, number),
        h.store.board("work"),
        now,
    )
    before = h.store.history(REPO, 7)
    assert needs_me.apply_answers(h.gateway, h.workspace, h.store, now) == 0
    assert {rows(h)[k]["status"] for k in ("1", "2")} == {"needs_you"}
    assert h.store.history(REPO, 7) == before
    assert not h.store.pr(REPO, 7)["loop_stopped"]
    owner.decide(number, choice="Ignore", then="return")
    assert needs_me.apply_answers(h.gateway, h.workspace, h.store, now) == 1
    assert {rows(h)[k]["reason"] for k in ("1", "2")} == {"dismissed_by_person"}
    assert needs_me.apply_answers(h.gateway, h.workspace, h.store, now) == 0


@pytest.mark.parametrize("label", ["Two more rounds", "I will do it myself", "Ignore"])
def test_a_revision_racing_answer_lookup_is_checked_inside_the_write(world, monkeypatch, label):  # noqa: F811
    h = world
    number = asked_card(h, monkeypatch)
    service(h, h.person(h.admin)).decide(number, choice=label, then="return")
    decide = h.store.decide

    def raced(*args, **kwargs):
        revise(h)
        return decide(*args, **kwargs)

    monkeypatch.setattr(h.store, "decide", raced)
    before = h.store.pr(REPO, 7)
    assert needs_me.apply_answers(h.gateway, h.workspace, h.store, datetime.now(timezone.utc)) == 0
    assert rows(h)["1"]["status"] == "new"
    assert rows(h)["2"]["status"] == "needs_you"
    after = h.store.pr(REPO, 7)
    assert (after["rounds_used"], after["loop_stopped"]) == (
        before["rounds_used"],
        before["loop_stopped"],
    )


@pytest.mark.parametrize("situation", ["unrelated_question", "card_refused"])
def test_comment_decision_reports_the_card_question_that_remains(world, monkeypatch, situation):  # noqa: F811
    h = world
    number = asked_card(h, monkeypatch)
    owner = service(h, h.person(h.admin))
    if situation == "unrelated_question":
        owner.decide(number, reply="Keep the comments waiting.", then="return")
        owner.ask(
            "Should we rename the project?",
            ["Ignore", "Rename"],
            task=number,
            person_reason="The project name needs its owner's decision.",
        )
    else:
        # A real BoardService refusal: its current offered options exclude this answer.
        # Do not stub decide or its exception handler.
        with owner.store.transaction() as conn:
            conn.execute(
                "UPDATE tasks SET ask_options=? WHERE id=?", (json.dumps(["Ignore"]), number)
            )
    before = mapping.card_facts(h.gateway, h.workspace, number)
    code, body, _ = h.request(
        h.owner, "POST", f"/api/pr-review/cards/{number}/decision", {"choice": "mine"}
    )
    assert code == 200 and body["card_answered"] is False, body
    assert body.get("card_answer_reason") == situation, body
    assert body.get("card_question_pending") is True, body
    after = mapping.card_facts(h.gateway, h.workspace, number)
    assert (after["status"], after["ask"]) == (before["status"], before["ask"])
    assert after["status"] == "review"
    assert {rows(h)[k]["reason"] for k in ("1", "2")} == {"handled_by_person"}
