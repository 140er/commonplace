"""Adding a card never starts a run unless the person explicitly chose an agent.

Usability test, 14 Nov 2026: the Add card form defaulted Who to "Any agent". On a board with a
Coworker config every card that lands unassigned in Todo starts a run, so a first card silently
queued one. The default must be the person adding the card; "Any agent" is an explicit choice
that says plainly it starts a run now. Browser-free: the form is rendered and submitted by node,
its payload is replayed through the board service and the Todo trigger sweep.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from commonplace.orch import triggers
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests._split_helpers import world
from tests.test_orch_runner import BOARD

STATIC = Path(__file__).resolve().parents[1] / "commonplace/web/static"
SCRIPT = Path(__file__).with_name("card_add_default.mjs")
T0 = "2030-01-01T00:00:00Z"


def form_payload(board_js, me, pick=None):
    args = ["node", str(SCRIPT), str(board_js), me] + ([pick] if pick is not None else [])
    result = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def default_sends_no_agent(payload, me):
    """The acceptance check the mutation test also runs against the old default."""
    assert payload["body"]["assignee"] == me, payload
    assert payload["body"]["assignee"] is not None


def add_from_form(w, payload):
    """What the REST route does with the form body, then one trigger sweep."""
    body = dict(payload["body"])
    return w.boards.task_add(board=BOARD, **body)["number"]


def test_form_defaults_to_me_and_says_nothing_starts():
    me = "user:maya"
    payload = form_payload(STATIC / "board.js", me)
    default_sends_no_agent(payload, me)
    selected = [o for o in payload["options"] if o["selected"]]
    assert [o["value"] for o in selected] == [me]
    assert "Any agent" not in selected[0]["label"]
    assert payload["hint"] == "Nothing starts when you add this card."
    # The agent choice exists, is never the default, and tells the person it starts a run now.
    agent = [o for o in payload["options"] if o["label"].startswith("Any agent")]
    assert len(agent) == 1 and "starts a run now" in agent[0]["label"] and not agent[0]["selected"]
    assert [o["value"] for o in payload["options"]].count(me) == 1


def test_default_card_on_a_configured_board_enqueues_no_run(hub):
    w = world(hub, runner=True)
    payload = form_payload(STATIC / "board.js", w.owner.actor)
    card = add_from_form(w, payload)
    triggers.run(w.js, now=T0)
    assert w.jobs_for(card) == [] and w.all_jobs() == []


def test_explicit_any_agent_still_starts_a_run(hub):
    w = world(hub, runner=True)
    me = w.owner.actor
    assert form_payload(STATIC / "board.js", me, "any")["body"]["assignee"] is None
    card = add_from_form(w, form_payload(STATIC / "board.js", me, "any"))
    triggers.run(w.js, now=T0)
    assert len(w.jobs_for(card)) == 1


def test_a_missing_who_falls_back_to_me_never_to_an_agent(tmp_path):
    payload = form_payload(STATIC / "board.js", "user:maya", "")
    assert payload["body"]["assignee"] == "user:maya"


def test_reverting_the_default_to_any_agent_fails_the_check(tmp_path):
    static = tmp_path / "static"
    shutil.copytree(STATIC, static)
    source = (static / "board.js").read_text()
    mutant = source.replace(" selected>Me (no run starts)", ">Me (no run starts)").replace(
        'value="${ANY_AGENT}">Any agent', 'value="${ANY_AGENT}" selected>Any agent'
    )
    assert mutant != source and mutant.count("selected>Any agent") == 1
    (static / "board.js").write_text(mutant)
    payload = form_payload(static / "board.js", "user:maya")
    assert payload["body"]["assignee"] is None
    with pytest.raises(AssertionError):
        default_sends_no_agent(payload, "user:maya")
