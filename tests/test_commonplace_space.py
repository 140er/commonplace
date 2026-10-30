"""Space chat, coordinator wakes and linked reservations through real REST/MCP."""

# ruff: noqa: F811

import json
import queue
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from commonplace import events, webhooks
from commonplace.boards.store import BoardStore
from commonplace.extensions import ToolError
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_boards import api, env, mcp, service  # noqa: F401
from tests.test_commonplace_webhooks import receiver  # noqa: F401


def test_verbatim_stamps_append_only_and_round_trip(hub, env):
    token, agent, person, cookie, owner = env
    text = f"  Split  this\r\n<tool_call> user:{person.user_id}\n✋  "
    status, posted = api(hub, cookie, "POST", "/api/boards/work/chat", {"text": text})
    assert status == 201 and posted["text"] == text
    assert posted["actor"] == person.actor and posted["stamp_user"] == person.user_id
    assert posted["stamp_connection"] is None and posted["stamp_trust"] == "trusted"
    assert "tool_syntax" in posted["flags"]
    ok, output = mcp(hub, token, "space_read", board="work")
    assert ok and json.loads(output)["messages"] == [posted]
    reply = f"Reply verbatim user:{person.user_id}  \n"
    ok, output = mcp(hub, token, "space_post", board="work", text=reply)
    assert ok
    response = json.loads(output)
    assert response["text"] == reply and response["actor"] == agent.actor
    assert response["stamp_connection"] == agent.connection
    status, stream = api(hub, cookie, "GET", f"/api/boards/work/chat?after={posted['id']}")
    assert status == 200 and stream["messages"] == [response]
    assert stream["cursor"] == response["id"]
    with owner.store.connection() as conn:
        row = conn.execute("SELECT * FROM space_messages WHERE id=?", (posted["id"],)).fetchone()
        assert row["text"] == text and row["stamp_user"] == person.user_id
        for sql in ("UPDATE space_messages SET text='changed'", "DELETE FROM space_messages"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute(sql)
    assert (
        api(hub, token, "POST", "/api/boards/work/chat", {"text": "x", "actor": "user:fake"})[0]
        == 400
    )
    kinds = [e["kind"] for e in events.after(hub.db, hub.workspace)]
    assert kinds[-2:] == ["space_message", "space_replied"]
    assert all(text not in json.dumps(e) for e in events.after(hub.db, hub.workspace))


def test_scopes_tool_allowlists_and_untrusted_chat(hub, env):
    _token, _agent, _person, cookie, owner = env
    owner.space_post("work", "PRIVATE CHAT")
    hidden = hub.token(
        "hidden",
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["elsewhere"],
        write_wings=["elsewhere"],
        trust="trusted",
    )
    for method, tool, args in (("GET", "space_read", {}), ("POST", "space_post", {"text": "x"})):
        status, result = api(
            hub, hidden, method, "/api/boards/work/chat", args if method == "POST" else None
        )
        assert status == 404 and "PRIVATE CHAT" not in json.dumps(result)
        ok, output = mcp(hub, hidden, tool, board="work", **args)
        assert not ok and "PRIVATE CHAT" not in output
    for name, scopes, tools in (
        ("no-scope", [], ["space_read", "space_post"]),
        ("no-tools", ["memory:read", "memory:write"], []),
    ):
        value = hub.token(
            name,
            tools=tools,
            scopes=scopes,
            read_wings=["shared"],
            write_wings=["shared"],
            trust="trusted",
        )
        assert api(hub, value, "GET", "/api/boards/work/chat")[0] == 403
        assert api(hub, value, "POST", "/api/boards/work/chat", {"text": "x"})[0] == 403
    inbox = hub.token(
        "inbox",
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared"],
        write_wing="inbox",
        trust="untrusted",
    )
    assert api(hub, inbox, "GET", "/api/boards/work/chat")[0] == 200
    assert api(hub, inbox, "POST", "/api/boards/work/chat", {"text": "x"})[0] == 403
    assert api(hub, None, "GET", "/api/boards/work/chat")[0] == 401
    assert api(hub, cookie, "GET", "/api/boards/work/chat?after=invalid")[0] == 400
    assert api(hub, cookie, "GET", "/api/boards/work/chat?after=-1")[0] == 403


def test_coordinator_setting_owner_only_and_validated(hub, env):
    token, agent, person, cookie, owner = env
    assert api(hub, cookie, "PATCH", "/api/boards/work", {"coordinator": agent.connection}) == (
        200,
        {"board": "work", "coordinator": agent.actor},
    )
    assert owner.board_list()[0]["coordinator"] == agent.actor
    nonowner = service(hub, replace(person, role="member"))
    with pytest.raises(ToolError, match="only a workspace owner"):
        nonowner.board_set("work", None)
    for ref in ("missing", "owner", "anyone"):
        assert api(hub, cookie, "PATCH", "/api/boards/work", {"coordinator": ref})[0] == 403
    # Same owner role is insufficient when the token cannot read/write the board.
    hub.token(
        "hidden",
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["elsewhere"],
        write_wings=["elsewhere"],
        trust="trusted",
    )
    assert api(hub, cookie, "PATCH", "/api/boards/work", {"coordinator": "hidden"})[0] == 403
    ok, output = mcp(hub, token, "board_set", board="work", coordinator=None)
    assert ok and json.loads(output)["coordinator"] is None


def test_linked_tasks_reuse_reservations_and_keep_board_boundaries(hub, env):
    token, agent, _person, cookie, owner = env
    message = owner.space_post("work", "Make three tasks")
    other = hub.owner_token("codex:vm")
    ok, output = mcp(
        hub,
        token,
        "task_add",
        board="work",
        title="One",
        message_id=message["id"],
        assignee="codex:vm",
    )
    assert ok, output
    [task] = owner.task_list("work")
    assert task["message_id"] == message["id"] and task["assignee"] == "conn:codex:vm"
    assert not mcp(hub, token, "task_take_next", task=task["number"])[0]
    assert mcp(hub, other, "task_take_next", task=task["number"])[0]
    assert api(hub, cookie, "GET", f"/api/cards/{task['number']}")[1]["message_id"] == message["id"]
    owner.board_add(slug="other", wing="shared")
    for board, mid in (("other", message["id"]), ("work", 999), ("work", True), ("work", 2**80)):
        with pytest.raises(ToolError, match="message not found"):
            owner.task_add(title="Wrong link", board=board, message_id=mid)
    assert owner.task_list("other") == []


def test_user_request_wakes_only_coordinator_without_sending_text(hub, env, receiver):
    token, agent, _person, cookie, owner = env
    url, received, _ = receiver
    hook = webhooks.add(hub.db, agent, url, kinds=["*"], board="work", waiting_on=agent.actor)
    owner.board_set("work", agent.connection)
    observer = hub.gateway.authenticate(f"Bearer {hub.owner_token('observer')}")
    webhooks.add(hub.db, observer, url, kinds=["space_message"], board="work")
    try:
        status, message = api(
            hub, cookie, "POST", "/api/boards/work/chat", {"text": "SECRET REQUEST"}
        )
        assert status == 201
        _headers, raw = received.get(timeout=3)
        wake = json.loads(raw)
        assert wake["kind"] == "space_message" and wake["data"]["message_id"] == message["id"]
        assert wake["data"]["coordinator"] == agent.actor
        assert wake["url"].endswith("/app/#/space/work") and b"SECRET REQUEST" not in raw
        assert mcp(hub, token, "space_post", board="work", text="I split it.")[0]
        with pytest.raises(queue.Empty):
            received.get(timeout=0.2)
        assert len(webhooks.deliveries(hub.db, agent, hook["id"])) == 1
    finally:
        webhooks.stop(hub.gateway)


def test_chat_pagination_and_additive_migration(hub, env):
    _token, _agent, _person, _cookie, owner = env
    before = owner.task_add(title="Existing card", board="work")
    # Model a pre-feature database, retaining existing cards and history.
    with owner.store.connection() as conn:
        conn.execute("ALTER TABLE tasks DROP COLUMN message_id")
        conn.execute("ALTER TABLE boards DROP COLUMN coordinator")
        conn.execute("DROP TABLE space_messages")
    BoardStore(Path(owner.store.path).parent)
    assert owner.task_show(before["number"])["title"] == "Existing card"
    for i in range(102):
        owner.space_post("work", f" {i}\n")
    first = owner.space_read("work")
    second = owner.space_read("work", after=first["cursor"])
    assert len(first["messages"]) == 100 and len(second["messages"]) == 2
    assert second["messages"][-1]["text"] == " 101\n"
    assert owner.space_read("work", after=second["cursor"])["messages"] == []
    assert owner.store.changes > 102
