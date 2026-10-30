"""Asks in the card's event log keep the choices offered, so the app can show them inline."""

import sqlite3
from pathlib import Path

import pytest

from commonplace import boards, sessions
from commonplace.boards.service import BoardService
from commonplace.boards.store import BoardStore
from commonplace.extensions import ToolContext
from commonplace.web import STATIC
from tests._commonplace_helpers import hub_fixture  # noqa: F401


@pytest.fixture
def owner(hub):
    person = sessions.person_principal(hub.db, hub.admin, hub.workspace)
    service = BoardService(ToolContext(principal=person, gateway=hub.gateway))
    service.board_add(slug="work", wing="shared")
    return service


@pytest.fixture
def agent(hub):
    principal = hub.gateway.authenticate(f"Bearer {hub.owner_token('agent')}")
    return BoardService(ToolContext(principal=principal, gateway=hub.gateway))


def asks(service, number):
    return [e for e in service.task_show(number)["events"] if "options" in e]


def test_ask_options_are_stored_verbatim_and_paired_with_the_decide(owner, agent):
    created = agent.ask("Ship  it?\nOr wait", options=["Ship  now", "wait ✋"], board="work")
    owner.decide(created["number"], choice="wait ✋", reply="Not on a Friday.", then="todo")
    taken = agent.task_take_next(task=created["number"])
    agent.task_handoff(taken["number"], to="owner", note="Which port?", options=["80", "8080"])
    events = owner.task_show(created["number"])["events"]
    asked = [e for e in events if "options" in e]
    assert [(e["kind"], e["text"], e["options"]) for e in asked] == [
        ("ask", "Ship  it?\nOr wait", ["Ship  now", "wait ✋"]),
        ("handoff", "Which port?", ["80", "8080"]),
    ]
    decide = next(e for e in events if e["kind"] == "decide")
    assert events.index(asked[0]) < events.index(decide) < events.index(asked[1])
    assert decide["choice"] == "wait ✋" and decide["text"] == "Not on a Friday."
    assert all(e["target"] == f"user:{owner.principal.user_id}" for e in asked)


def test_free_text_asks_carry_an_empty_option_list(owner, agent):
    created = agent.ask("Anything to add?", board="work")
    assert asks(owner, created["number"])[0]["options"] == []


def test_review_moves_record_the_question_and_choices(owner, agent):
    owner.board_add(slug="gated", wing="shared")
    with owner.store.transaction() as conn:
        conn.execute("UPDATE boards SET done_needs_review=1 WHERE slug='gated'")
    card = agent.task_add(title="Gated", board="gated")
    agent.task_take_next(task=card["number"])
    agent.task_update(card["number"], status="done")
    [ask] = asks(owner, card["number"])
    assert (ask["kind"], ask["text"], ask["options"]) == ("move", "Done?", ["ship", "redo"])
    assert ask["target"] == f"user:{owner.principal.user_id}"
    seeded = owner.seed_card(board="work", title="Seeded", body="Please look", status="review")
    [ask] = asks(owner, seeded["number"])
    assert (ask["kind"], ask["text"], ask["options"]) == ("move", "Please look", [])


def test_other_events_have_no_options(owner, agent):
    card = agent.task_add(title="Plain", board="work")
    owner.task_comment(card["number"], "a comment")
    agent.task_take_next(task=card["number"])
    agent.task_handoff(card["number"], to="anyone", note="Back to the pool")
    assert all("options" not in e for e in owner.task_show(card["number"])["events"])


def test_existing_event_logs_gain_the_options_column(tmp_path):
    path = Path(tmp_path) / "boards.sqlite3"
    schema = Path(boards.__file__).with_name("schema.sql").read_text()
    conn = sqlite3.connect(path)
    conn.executescript(schema.replace("    options    TEXT,", ""))
    assert "options" not in {r[1] for r in conn.execute("PRAGMA table_info(task_events)")}
    conn.close()
    BoardStore(tmp_path)
    conn = sqlite3.connect(path)
    assert "options" in {r[1] for r in conn.execute("PRAGMA table_info(task_events)")}
    conn.close()


def test_app_renders_asks_inline_with_one_live_place():
    app = (Path(STATIC) / "app.js").read_text()
    assert "function askCard(card, ask" in app and "conversation(card)" in app
    # The panel header no longer repeats the open ask: the conversation holds it.
    assert '<p class="stamp">${e(actor(card.return_to))}</p>' not in app
    assert 'data-ask="${card.number}"' in app
