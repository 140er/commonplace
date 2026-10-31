"""Asks in the card's event log keep the choices offered, so the app can show them inline."""

import http.client
import json
import sqlite3
from pathlib import Path

import pytest

from commonplace import boards, sessions
from commonplace.boards.service import BoardService, VersionClash
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


def post(hub, path, body):
    session = sessions.redeem_login_link(
        hub.db, sessions.create_login_link(hub.db, "owner@example.com")
    )
    conn = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=10)
    try:
        conn.request(
            "POST",
            path,
            json.dumps(body).encode(),
            {"Content-Type": "application/json", "Cookie": f"{sessions.COOKIE}={session}"},
        )
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def reasked(owner, agent):
    """A answered elsewhere, then the agent asks B: returns the card as it was during A."""
    first = agent.ask("Ship it?", options=["yes", "no"], board="work")
    owner.decide(first["number"], choice="no", version=first["version"])
    agent.task_take_next(task=first["number"])
    agent.ask("Delete the branch?", options=["yes", "keep"], task=first["number"])
    return first


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


def test_a_stale_decide_never_answers_a_newer_ask(owner, agent):
    first = reasked(owner, agent)
    with pytest.raises(VersionClash) as clash:
        owner.decide(first["number"], choice="yes", version=first["version"])
    assert clash.value.current["ask"] == "Delete the branch?"
    current = owner.task_show(first["number"])
    assert current["status"] == "review" and current["ask"] == "Delete the branch?"


def test_rest_decide_requires_the_version_the_person_saw(hub, owner, agent):
    first = reasked(owner, agent)
    path = f"/api/cards/{first['number']}/decide"
    assert post(hub, path, {"choice": "yes"}) == (400, {"error": "version is required"})
    assert post(hub, path, {"choice": "yes", "version": None})[0] == 400
    status, body = post(hub, path, {"choice": "yes", "version": first["version"]})
    assert status == 409 and body["current"]["ask"] == "Delete the branch?"
    assert owner.task_show(first["number"])["status"] == "review"
    current = owner.task_show(first["number"])
    status, body = post(hub, path, {"choice": "keep", "version": current["version"]})
    assert status == 200 and body["status"] == "todo"
    decides = [e for e in owner.task_show(first["number"])["events"] if e["kind"] == "decide"]
    assert [d["choice"] for d in decides] == ["no", "keep"]


def test_mcp_decide_without_a_version_still_works(hub, owner, agent):
    created = agent.ask("Proceed?", options=["yes"], board="work")
    token = hub.owner_token("coordinator")
    response = hub.rpc(
        token,
        "tools/call",
        {"name": "decide", "arguments": {"task": created["number"], "choice": "yes"}},
    )
    assert not response["result"].get("isError"), response
    assert owner.task_show(created["number"])["status"] == "todo"


def test_app_sends_the_rendered_version_and_keeps_one_live_place():
    app = (Path(STATIC) / "app.js").read_text()
    assert 'data-version="${e(card.version)}"' in app
    assert "body: { ...payload, version }" in app
    # Home draws no choice buttons. A question in the needbox opens the decide queue.
    assert "homeView(" in app and "decision(card)" not in app
    assert 'data-action="decide"' not in (Path(STATIC) / "home.js").read_text()


def test_a_repeat_decide_with_the_old_version_returns_409(hub, owner, agent):
    created = agent.ask("Ship it?", options=["yes", "no"], board="work")
    path = f"/api/cards/{created['number']}/decide"
    status, body = post(
        hub, path, {"choice": "yes", "version": created["version"], "then": "return"}
    )
    assert status == 200 and body["status"] == "todo"
    status, body = post(
        hub, path, {"choice": "yes", "version": created["version"], "then": "return"}
    )
    assert status == 409 and body["error"] == "card changed"
    assert owner.task_show(created["number"])["status"] == "todo"
