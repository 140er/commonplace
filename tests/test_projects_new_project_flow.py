"""A new project, start to finish through the routes behind the app (card 192).

The owner turns orchestration on, connects the automatic seat of a board made after
that, and sets the agent limit in project settings. The seat splits a new card with
its own token. Nothing is seeded in a database and no runner machine exists.
"""

import json
import re

from tests._projects_helpers import projects_hub_fixture  # noqa: F401

BOARD = "fresh"


def put(h, path, body):
    return h.request(h.owner, "PUT", path, body)[:2]


def tool(h, token, name, **arguments):
    status, result = h.mcp(token, name, **arguments)
    assert status == 200, result
    return result["ok"], result["text"]


def card(h, title):
    status, body, _ = h.request(h.owner, "POST", f"/api/boards/{BOARD}/cards", {"title": title})
    assert status == 201, body
    return body["number"]


def test_new_project_enables_orchestrator_splits_a_card_and_enforces_the_limit(projects_hub):
    h = projects_hub
    assert put(h, "/api/settings/orchestration", {"enabled": True}) == (200, {"enabled": True})
    assert h.create_board(h.owner, BOARD)[0] == 201

    # 1. The project has a seat without any setup, and settings can connect an agent to it.
    status, view = h.project(h.owner, BOARD)
    assert status == 200 and view["main"]["connected"] is False and view["can_configure"]
    assert view["orchestration"]["default_max_agents"] == 3 and view["limits"] == {
        "max_agents": None
    }
    assert "card.created" in view["setup"]["webhook_add"]["events"]
    status, connected = h.connect(h.owner, BOARD)
    assert status == 201, connected
    seat = connected["token"]
    assert h.coordinator(BOARD) == "conn:" + connected["connection"]["name"]

    # 2. A new card reaches the seat's inbox; the seat splits it into keyed children.
    root = card(h, "Build the importer")
    ok, text = tool(h, seat, "orchestrator_inbox", board=BOARD)
    assert ok and [c["number"] for c in json.loads(text)] == [root], text
    children = []
    for key in ("parse", "store"):
        ok, text = tool(
            h,
            seat,
            "task_add",
            title=f"Importer: {key}",
            board=BOARD,
            parent=root,
            labels=[f"split:{root}:{key}"],
        )
        assert ok, text
        children.append(int(re.match(r"#(\d+) added", text)[1]))
    ok, text = tool(h, seat, "card_split_done", task=root, state="split")
    assert ok and json.loads(text)["state"] == "split", text
    ok, text = tool(h, seat, "orchestrator_inbox", board=BOARD)
    assert ok and json.loads(text) == [], text

    # 3. The limit is a project setting and admission follows it.
    status, view = put(h, f"/api/projects/{BOARD}/limits", {"max_agents": 1})
    assert status == 200 and view["limits"] == {"max_agents": 1}
    workers = [
        h.hub.token(
            n,
            profile="full",
            scopes=["memory:read", "memory:write"],
            read_wings=[BOARD],
            write_wings=[BOARD],
            trust="trusted",
        )
        for n in ("w1", "w2")
    ]
    ok, text = tool(h, workers[0], "task_take_next", board=BOARD, task=children[0])
    assert ok, text
    ok, text = tool(h, workers[1], "task_take_next", board=BOARD, task=children[1])
    assert not ok and "Waiting for a free slot: 1 of 1" in text, text
    assert put(h, f"/api/projects/{BOARD}/limits", {"max_agents": 2})[0] == 200
    ok, text = tool(h, workers[1], "task_take_next", board=BOARD, task=children[1])
    assert ok, text
