"""#547: the Add card form's Who lists the board's agents, and the text says whether choosing one starts a run.

The list comes from the server for the signed-in person (the owner sees every agent that can work on the board, a
member only their own) and carries what handing a card to each agent does. The form is rendered by node from that very
answer, its body is posted to the real route and the Todo trigger sweep runs, so what the form says is compared with
what the server then does. Browser-free: node drives the real board renderer (tests/board_add_who_agents.mjs).
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from commonplace.orch import triggers
from tests._phase_b_helpers import World, cap, seed
from tests._projects_helpers import projects_hub_fixture  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).with_name("board_add_who_agents.mjs")
CASES = [
    "options",
    "none",
    "wording",
    "escape",
    "submit",
    "change",
    "failure_keeps_choice",
    "board_view",
]

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")


def node(*args):
    result = subprocess.run(
        ["node", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "STATIC_DIR": str(ROOT / "commonplace/web/static")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize("case", CASES)
def test_form_options_and_wording(case):
    assert f"{case}: PASS" in node(case)


def configured(projects_hub):
    w = World(projects_hub.hub)
    seed(w)
    cap(w, 1)
    w.configure(max_agents=1)
    worker = w.db.authenticate_connection(w.machine()["worker_connection_id"])
    return w, worker


def assignees(h, auth, board="acme"):
    status, body, _ = h.request(auth, "GET", f"/api/boards/{board}/assignees")
    return status, body


def form_for(me, agents, pick=None):
    args = ["render", me, json.dumps(agents)] + ([pick] if pick is not None else [])
    return json.loads(node(*args))


def test_owner_lists_the_agents_and_what_assigning_each_does(projects_hub):
    h = projects_hub
    w, worker = configured(h)
    status, body = assignees(h, h.owner)
    assert status == 200 and body["board"] == "acme"
    by_ref = {agent["ref"]: agent for agent in body["agents"]}
    # The board's Coworker worker is the only one a person's hand-over starts; every other agent is only given the card.
    assert by_ref[worker.actor]["starts_run"] is True
    assert by_ref[w.a.actor]["starts_run"] is False and by_ref[w.a.actor]["name"] == "orch-a"
    assert [ref for ref, agent in by_ref.items() if agent["starts_run"]] == [worker.actor]
    assert all(set(agent) == {"ref", "name", "starts_run"} for agent in body["agents"])


@pytest.mark.parametrize("choice", ["me", "bot", "coworker"])
def test_form_text_says_what_the_server_does(projects_hub, choice):
    h = projects_hub
    w, worker = configured(h)
    me = w.owner.actor
    agents = assignees(h, h.owner)[1]["agents"]
    pick = {"me": None, "bot": w.a.actor, "coworker": worker.actor}[choice]
    form = form_for(me, agents, pick)
    status, card, _ = h.request(h.owner, "POST", "/api/boards/acme/cards", form["body"])
    assert status == 201, card
    triggers.run(w.js)
    jobs = w.jobs_for(card["number"])
    # A card handed to a bot starts nothing by itself: only the Coworker's worker, which the text says out loud.
    assert len(jobs) == (1 if choice == "coworker" else 0)
    if choice == "coworker":
        assert form["hint"].startswith("Adding this card starts") and "now" in form["hint"]
        assert card["assignee"] == worker.actor and card["status_line"].startswith("Start queued")
    elif choice == "bot":
        assert form["hint"] == "Ready for orch-a. Nothing starts when you add this card."
        assert card["assignee"] == w.a.actor and "Start blocked" in card["status_line"]
    else:
        assert form["hint"] == "Nothing starts when you add this card."
        assert card["assignee"] == me and w.all_jobs() == []


def test_a_member_sees_only_their_own_bots_and_starts_nothing(projects_hub):
    h = projects_hub
    w, worker = configured(h)
    token = h.hub.token(
        "member-bot",
        user=h.member_user,
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared"],
        write_wings=["shared"],
        trust="trusted",
    )
    own = h.hub.gateway.authenticate(f"Bearer {token}")
    status, body = assignees(h, h.member)
    assert status == 200
    assert body["agents"] == [{"ref": own.actor, "name": "member-bot", "starts_run": False}]
    assert worker.actor not in json.dumps(body) and w.a.actor not in json.dumps(body)
    form = form_for(f"user:{h.member_user}", body["agents"], own.actor)
    status, card, _ = h.request(h.member, "POST", "/api/boards/acme/cards", form["body"])
    assert status == 201 and card["assignee"] == own.actor, card
    triggers.run(w.js)
    assert w.all_jobs() == []


def test_a_board_without_agents_keeps_the_field_as_it_is(projects_hub):
    h = projects_hub
    assert h.create_board(h.owner, "solo")[0] == 201
    status, body = assignees(h, h.owner, "solo")
    assert (status, body) == (200, {"board": "solo", "agents": []})
    assert "optgroup" not in node("none") and node("none").strip() == "none: PASS"


def test_the_list_is_for_people_on_a_board_they_can_see(projects_hub):
    h = projects_hub
    configured(h)
    assert assignees(h, h.owner, "missing")[0] == 404
    assert assignees(h, h.a1)[0] == 403
    assert assignees(h, None)[0] in (401, 403)
