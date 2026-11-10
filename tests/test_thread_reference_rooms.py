"""Thread-valued rooms reveal a thread ID only to readers of that thread."""

import json

import pytest

from commonplace import sessions
from commonplace.handoff import mirror_access
from commonplace.handoff.models import ThreadMessage
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_memory import _scoped

FORMS = {
    "bare": "{}",
    "app_path": "/app/threads/{}",
    "app_url": "https://example.test/app/threads/{}/messages/3",
    "t3_link": "t3-thread://v1/{}",
    "mcp_prefix": "mcp:{}",
}
DRAWER = "thread-room-probe"


def private_thread(hub, wing="private_chats"):
    return hub.gateway.handoff.store_for(hub.workspace).store(
        hub.workspace,
        f"capture:{hub.admin}:thread-room-{wing}",
        [ThreadMessage(0, "user", "message", "Thread exact words", "Thread exact words")],
        actor=hub.admin,
        wing=wing,
    )


def member(hub):
    uid = hub.db.create_user("thread-room-reader@example.test")
    hub.db.add_member(hub.workspace, uid, "member", actor=hub.admin)
    link = sessions.create_login_link(hub.db, "thread-room-reader@example.test", hub.workspace)
    return {"Cookie": f"{sessions.COOKIE}={sessions.redeem_login_link(hub.db, link)}"}


def owner(hub):
    link = sessions.create_login_link(hub.db, hub.db.user(hub.admin)["email"], hub.workspace)
    return {"Cookie": f"{sessions.COOKIE}={sessions.redeem_login_link(hub.db, link)}"}


def seed(hub, collection, form, wing="private_chats"):
    thread = private_thread(hub, wing)
    room = FORMS[form].format(thread.id)
    collection.add(
        ids=[DRAWER],
        documents=["Shared exact words about the plan"],
        metadatas=[{"wing": "shared", "room": room}],
    )
    return thread, room


def rest(hub, headers, path):
    status, raw = hub.raw(None, b"", method="GET", path=path, headers=headers)
    return status, raw


def mcp(hub, token, name, **args):
    response = hub.rpc(token, "tools/call", {"name": name, "arguments": args})
    return json.dumps(response, ensure_ascii=False)


ROUTES = {
    "rest_get": lambda hub, h, t: rest(hub, h, f"/api/memory/{DRAWER}")[1],
    "rest_search": lambda hub, h, t: rest(hub, h, "/api/memory/search?q=plan")[1],
    "mcp_get_drawer": lambda hub, h, t: mcp(hub, t, "mempalace_get_drawer", drawer_id=DRAWER),
    "mcp_list_drawers": lambda hub, h, t: mcp(hub, t, "mempalace_list_drawers", wing="shared"),
    "mcp_list_rooms": lambda hub, h, t: mcp(hub, t, "mempalace_list_rooms", wing="shared"),
    "mcp_taxonomy": lambda hub, h, t: mcp(hub, t, "mempalace_get_taxonomy"),
}


@pytest.mark.parametrize("route", sorted(ROUTES))
@pytest.mark.parametrize("form", sorted(FORMS))
def test_member_never_receives_unreadable_thread_id(hub, collection, form, route):
    headers, token = member(hub), _scoped(hub)
    thread, _ = seed(hub, collection, form)
    raw = ROUTES[route](hub, headers, token)
    assert thread.id not in raw, raw


# Room-name validation already refuses the slashed forms on write.
@pytest.mark.parametrize("form", ["bare", "mcp_prefix"])
def test_member_cannot_file_into_unreadable_thread_room(hub, form):
    thread = private_thread(hub)
    room = FORMS[form].format(thread.id)
    raw = mcp(hub, _scoped(hub), "mempalace_add_drawer", wing="shared", room=room, content="x")
    assert "Room not found" in raw, raw


@pytest.mark.parametrize("form", sorted(FORMS))
def test_owner_reads_thread_room_unchanged(hub, collection, form):
    _, room = seed(hub, collection, form)
    headers = owner(hub)
    status, raw = rest(hub, headers, f"/api/memory/{DRAWER}")
    assert status == 200, raw
    assert json.loads(raw)["topic"] == room
    status, raw = rest(hub, headers, "/api/memory/search?q=plan")
    assert status == 200, raw
    assert [hit["topic"] for hit in json.loads(raw)["results"]] == [room]


@pytest.mark.parametrize("form", sorted(FORMS))
def test_member_reads_room_naming_readable_thread(hub, collection, form):
    headers = member(hub)
    _, room = seed(hub, collection, form, wing="shared")
    status, raw = rest(hub, headers, f"/api/memory/{DRAWER}")
    assert status == 200, raw
    assert json.loads(raw)["topic"] == room


def test_thread_ids_names_every_reference_form():
    for form, pattern in FORMS.items():
        assert mirror_access.thread_ids(pattern.format("thr_ab12")) == ("thr_ab12",), form
    assert mirror_access.thread_ids("/app/threads/thr%5Fab12") == ("thr_ab12",)
    for ordinary in ("general", "thr_notes", "thr_deadline", "path_thr_ab12"):
        assert mirror_access.thread_ids(ordinary) == (), ordinary
    assert mirror_access.thread_ids(None) == ()


@pytest.mark.parametrize("room", ["general", "thr_deadline"])
def test_member_ordinary_room_unchanged(hub, collection, room):
    collection.add(
        ids=[DRAWER],
        documents=["Shared exact words about the plan"],
        metadatas=[{"wing": "shared", "room": room}],
    )
    status, raw = rest(hub, member(hub), f"/api/memory/{DRAWER}")
    assert status == 200, raw
    assert json.loads(raw)["topic"] == room
