"""Space chat, coordinator wakes and linked reservations through real REST/MCP."""

# ruff: noqa: F811

import json
import queue
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from commonplace import events, urls, webhooks
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
    status, result = api(
        hub, cookie, "PATCH", "/api/boards/work", {"coordinator": agent.connection}
    )
    assert status == 200 and result["coordinator"] == agent.actor
    assert "webhook_add" in result["warning"] and "events=space_message" in result["warning"]
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
    hook = webhooks.add(hub.db, agent, url, kinds=["space_message"], board="work")
    assert "warning" not in owner.board_set("work", agent.connection)
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
        assert wake["url"] == urls.share_link(hub.db, hub.workspace, "/app/#/space/work")
        assert b"SECRET REQUEST" not in raw
        ok, output = mcp(hub, token, "space_read", board="work", after=message["id"] - 1)
        assert ok and json.loads(output)["messages"][0]["text"] == "SECRET REQUEST"
        assert mcp(
            hub, token, "task_add", title="Split request", board="work", message_id=message["id"]
        )[0]
        assert mcp(hub, token, "space_post", board="work", text="I split it.")[0]
        with pytest.raises(queue.Empty):
            received.get(timeout=0.2)
        assert len(webhooks.deliveries(hub.db, agent, hook["id"])) == 1
        # A wildcard subscription *does* wake on the coordinator's own task_add.
        wildcard = webhooks.add(hub.db, agent, url, kinds=["*"], board="work")
        assert mcp(hub, token, "task_add", title="Another task", board="work")[0]
        assert json.loads(received.get(timeout=3)[1])["kind"] == "card.created"
        assert len(webhooks.deliveries(hub.db, agent, wildcard["id"])) == 1
        row = hub.db._conn.execute("SELECT * FROM webhooks WHERE id=?", (hook["id"],)).fetchone()
        event = next(e for e in events.after(hub.db, hub.workspace) if e["kind"] == "space_message")
        assert webhooks._can_receive(hub.db, hub.gateway, row, event)
        owner.board_set("work", None)
        assert not webhooks._can_receive(hub.db, hub.gateway, row, event)
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
    tail = owner.space_read("work", last=3)
    assert [m["text"] for m in tail["messages"]] == [" 99\n", " 100\n", " 101\n"]
    assert tail["oldest"] == tail["messages"][0]["id"] and tail["has_more"]
    previous = owner.space_read("work", before=tail["oldest"], last=3)
    assert [m["text"] for m in previous["messages"]] == [" 96\n", " 97\n", " 98\n"]


def test_tail_rest_mcp_round_trip_boundaries_and_validation(hub, env):
    token, _agent, _person, cookie, owner = env
    assert owner.space_read("work", last=10) == {
        "board": "work",
        "coordinator": None,
        "messages": [],
        "cursor": 0,
        "oldest": 0,
        "has_more": False,
    }
    owner.board_add(slug="other", wing="shared")
    messages = []
    for text in (" first\n", "second", "third", "last "):
        messages.append(owner.space_post("work", text))
        owner.space_post("other", "Not this board")
    for args, query in (
        ({"last": 2}, "last=2"),
        ({"last": 2, "before": messages[2]["id"]}, f"last=2&before={messages[2]['id']}"),
        ({"before": messages[0]["id"]}, f"before={messages[0]['id']}"),
    ):
        expected = owner.space_read("work", **args)
        assert api(hub, cookie, "GET", f"/api/boards/work/chat?{query}") == (200, expected)
        ok, output = mcp(hub, token, "space_read", board="work", **args)
        assert ok and json.loads(output) == expected
        assert all(m["text"] != "Not this board" for m in expected["messages"])
    for args in (
        {"last": 0},
        {"last": 101},
        {"last": True},
        {"before": -1},
        {"before": 2**80},
        {"after": 0, "last": 1},
        {"after": 0, "before": 1},
    ):
        with pytest.raises(ToolError):
            owner.space_read("work", **args)
    assert api(hub, cookie, "GET", "/api/boards/work/chat?last=oops")[0] == 400
    assert api(hub, cookie, "GET", "/api/boards/work/chat?after=0&last=1")[0] == 403


def test_coordinator_warns_only_without_a_live_matching_subscription(hub, env):
    _token, agent, _person, _cookie, owner = env
    url = "http://127.0.0.1:9/wake"
    for kinds, board in ((["card.*"], "work"), (["space_message"], "other")):
        hook = webhooks.add(hub.db, agent, url, kinds=kinds, board=board)
        assert "warning" in owner.board_set("work", agent.connection)
        webhooks.remove(hub.db, agent, hook["id"])
    hook = webhooks.add(hub.db, agent, url, kinds=["space_message"], board="work")
    assert "warning" not in owner.board_set("work", agent.connection)
    webhooks.remove(hub.db, agent, hook["id"])
    assert "warning" in owner.board_set("work", agent.connection)
    # An active registration whose connection was revoked cannot receive a wake.
    hook = webhooks.add(hub.db, agent, url, kinds=["space_message"], board="work")
    with hub.db._lock, hub.db._tx():
        hub.db._conn.execute(
            "UPDATE connections SET revoked_at='now' WHERE id=?", (agent.connection_id,)
        )
    assert not webhooks.has_space_subscription(
        hub.db, hub.gateway, hub.workspace, agent.actor, "work"
    )
    with pytest.raises(ToolError, match="active agent"):
        owner.board_set("work", agent.connection)
    with hub.db._lock, hub.db._tx():
        hub.db._conn.execute(
            "UPDATE connections SET revoked_at=NULL WHERE id=?", (agent.connection_id,)
        )
    webhooks.remove(hub.db, agent, hook["id"])
    webhooks.add(hub.db, agent, url, kinds=["*"], board="")
    assert "warning" not in owner.board_set("work", agent.connection)
    assert owner.board_set("work", None) == {"board": "work", "coordinator": None}


@pytest.mark.parametrize("profile", ["slim", "standard", "full"])
def test_space_tools_are_work_profile_only(hub, env, profile):
    _token, _agent, _person, _cookie, owner = env
    token = hub.token(
        f"space-{profile}",
        profile=profile,
        trust="trusted",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared"],
        write_wings=["shared"],
    )
    names = hub.tool_names(token)
    tools = {"space_read", "space_post", "board_set"}
    assert (tools <= names) if profile != "slim" else not tools & names
    for tool, args in (
        ("space_read", {"last": 1}),
        ("space_post", {"text": "reply"}),
        ("board_set", {"coordinator": None}),
    ):
        assert mcp(hub, token, tool, board="work", **args)[0] == (profile != "slim")
    if profile == "slim":
        assert api(hub, token, "GET", "/api/boards/work/chat?last=1")[0] == 403
        assert api(hub, token, "POST", "/api/boards/work/chat", {"text": "x"})[0] == 403
        assert api(hub, token, "PATCH", "/api/boards/work", {"coordinator": None})[0] == 403
        with pytest.raises(ToolError, match="active agent"):
            owner.board_set("work", f"space-{profile}")
