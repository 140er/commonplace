"""Home: the open-task list behind its table, and the three sections it renders."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_boards import api, env, service  # noqa: F401


def test_open_cards_are_scoped_and_carry_need(hub, env):  # noqa: F811
    token, agent, person, cookie, owner = env
    worker = service(hub, agent)
    asked = worker.ask('Ship <b>"now"</b>?', board="work")
    todo = owner.task_add(title="Later", board="work")
    owner.task_add(title="Someday", board="work", status="backlog")
    owner.seed_card(board="work", title="Finished", status="done")
    owner.board_add(slug="secret", wing="private.secret")
    secret = owner.seed_card(board="secret", title="SECRET CANARY", status="todo")

    status, payload = api(hub, cookie, "GET", "/api/cards")
    assert status == 200
    needs = {c["number"]: c["need"] for c in payload["cards"]}
    assert needs == {asked["number"]: "you", todo["number"]: "idle", secret["number"]: "idle"}
    assert payload["cards"][0]["title"] == 'Ship <b>"now"</b>?'
    assert payload["cards"][0]["url"]  # Every card keeps its short link.

    scoped = hub.token("home-reader", profile="full", scopes=["memory:read"], read_wings=["shared"])
    status, payload = api(hub, scoped, "GET", "/api/cards")
    assert status == 200
    assert {c["number"] for c in payload["cards"]} == {asked["number"], todo["number"]}
    assert "SECRET CANARY" not in json.dumps(payload)
    assert api(hub, None, "GET", "/api/cards")[0] == 401
    assert api(hub, cookie, "POST", "/api/cards", {})[0] == 404


def test_home_renders_three_sections_from_a_seeded_payload():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is needed for the Home render check")
    script = Path(__file__).with_name("commonplace_home_render.mjs")
    subprocess.run([node, str(script)], check=True, timeout=10)
