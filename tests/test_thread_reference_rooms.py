"""Rooms and topics naming a thread reveal it only to readers of that thread."""

import json
import secrets

import pytest

from commonplace import sessions
from commonplace.handoff import mirror_access, provenance_refs
from commonplace.handoff.models import ThreadMessage
from tests._commonplace_helpers import hub_fixture  # noqa: F401

FULLWIDTH_THR = "ｔｈｒ"
FORMS = {
    "bare": lambda t: t.id,
    "app_path": lambda t: f"/app/threads/{t.id}",
    "app_url": lambda t: f"https://example.test/app/threads/{t.id}/messages/3",
    "t3_link": lambda t: f"t3-thread://v1/{t.id}",
    "mcp_prefix": lambda t: f"mcp:{t.id}",
    "percent": lambda t: "/app/threads/" + t.id.replace("_", "%5F"),
    "underscore_suffix": lambda t: f"{t.id}_notes",
    "underscore_prefix": lambda t: f"notes_{t.id}",
    "letter_suffix": lambda t: f"{t.id}g",
    "letter_prefix": lambda t: f"x{t.id}",
    "fullwidth": lambda t: FULLWIDTH_THR + t.id[3:],
    "mcp_source": lambda t: t.source_thread_id,
    # Undecodable values are hidden from every reader: no thread can be proved.
    "undecodable": lambda t: f"/app/threads/%FF{t.id}",
}
# Room-name validation refuses slashes, colons and percent signs on write.
WRITABLE = [
    "bare",
    "underscore_suffix",
    "underscore_prefix",
    "letter_suffix",
    "letter_prefix",
    "fullwidth",
]
DRAWER = "thread-room-probe"
TEXT = "Shared exact words about the plan"


def secret(thread, form):
    """The identity that must not reach a non-reader, in any spelling."""
    return thread.source_thread_id[4:] if form == "mcp_source" else thread.id[4:]


def store_thread(hub, form, *, actor, wing):
    source = (
        f"mcp:probe-{secrets.token_hex(6)}"
        if form == "mcp_source"
        else f"capture:{actor}:room-{secrets.token_hex(6)}"
    )
    return hub.gateway.handoff.store_for(hub.workspace).store(
        hub.workspace,
        source,
        [ThreadMessage(0, "user", "message", "Thread exact words", "Thread exact words")],
        actor=actor,
        wing=wing,
    )


def member(hub):
    uid = hub.db.create_user("thread-room-member@example.test")
    hub.db.add_member(hub.workspace, uid, "member", actor=hub.admin)
    link = sessions.create_login_link(hub.db, "thread-room-member@example.test", hub.workspace)
    return {"Cookie": f"{sessions.COOKIE}={sessions.redeem_login_link(hub.db, link)}"}


def owner(hub):
    link = sessions.create_login_link(hub.db, hub.db.user(hub.admin)["email"], hub.workspace)
    return {"Cookie": f"{sessions.COOKIE}={sessions.redeem_login_link(hub.db, link)}"}


@pytest.fixture(autouse=True)
def palace_files(config, monkeypatch, tmp_path):
    """Tunnels and hallways resolve their files from the test palace."""
    from mempalace import hallways, palace_graph

    monkeypatch.setattr(palace_graph, "MempalaceConfig", lambda: config)
    monkeypatch.setattr(hallways, "_get_hallway_file", lambda *a, **k: str(tmp_path / "h.json"))
    monkeypatch.setattr(hallways, "_legacy_hallway_file", lambda: str(tmp_path / "legacy.json"))


SCOPES = ["memory:read", "memory:write", "memory:delete", "private:read", "private:write"]


def agent(hub, name, scopes):
    """A see-all owner-role agent; thread reads depend only on its scopes and filer."""
    uid = hub.db.create_user(f"{name}@example.test")
    hub.db.add_member(hub.workspace, uid, "owner", actor=hub.admin)
    token = hub.token(
        name,
        user=uid,
        profile="full",
        scopes=scopes,
        read_wings=["*"],
        write_wings=["*"],
        trust="trusted",
    )
    principal = hub.db.authenticate_token(token)
    assert principal.policy.sees_all_wings
    return uid, token, principal


@pytest.fixture
def blind(hub):
    """Sees every wing but cannot read any thread."""
    return agent(hub, "thread-blind", SCOPES)


@pytest.fixture
def reader(hub):
    """Sees every wing and reads its own threads."""
    return agent(hub, "thread-reader", [*SCOPES, "threads:read"])


def hidden_thread(hub, blind, form):
    thread = store_thread(hub, form, actor=hub.admin, wing="private_chats")
    assert not hub.gateway.handoff.recall.can_read(blind[2], thread.id)
    return thread


def readable_thread(hub, reader, form):
    thread = store_thread(hub, form, actor=reader[0], wing="shared")
    assert hub.gateway.handoff.recall.can_read(reader[2], thread.id)
    return thread


def seed(collection, config, room):
    """One room value in drawers, a diary topic, a tunnel and a hallway."""
    from mempalace import hallways, palace_graph

    collection.add(
        ids=[DRAWER, "probe-other", "probe-general", "probe-other-general", "probe-diary"],
        documents=[TEXT, "Other plan words", "General words", "Other general words", TEXT],
        metadatas=[
            {"wing": "shared", "room": room},
            {"wing": "other", "room": room},
            {"wing": "shared", "room": "general"},
            {"wing": "other", "room": "general"},
            {
                "wing": "shared",
                "room": "diary",
                "hall": "hall_diary",
                "topic": room,
                "type": "diary_entry",
                "agent": "probe",
                "filed_at": "2026-11-10T00:00:00Z",
                "date": "2026-11-10",
            },
        ],
    )
    palace_graph._save_tunnels(
        [
            {
                "id": "probe-tunnel",
                "source": {"wing": "shared", "room": room},
                "target": {"wing": "other", "room": "general"},
                "label": "probe",
            }
        ],
        config,
    )
    hallways._save_hallways(
        [
            {
                "id": "probe-hallway",
                "wing": "shared",
                "rooms": [room],
                "label": "probe",
                "co_occurrence_count": 2,
            }
        ],
        config,
    )


def rest(hub, headers, path):
    return hub.raw(None, b"", method="GET", path=path, headers=headers)


def mcp(hub, token, name, **args):
    response = hub.rpc(token, "tools/call", {"name": name, "arguments": args})
    return json.dumps(response, ensure_ascii=False)


READS = {
    "get_drawer": ("mempalace_get_drawer", {"drawer_id": DRAWER}),
    "get_drawers": ("mempalace_get_drawers", {"drawer_ids": [DRAWER]}),
    "search": ("mempalace_search", {"query": "plan"}),
    "list_drawers": ("mempalace_list_drawers", {"wing": "shared"}),
    "list_rooms": ("mempalace_list_rooms", {"wing": "shared"}),
    "taxonomy": ("mempalace_get_taxonomy", {}),
    "status": ("mempalace_status", {}),
    "list_tunnels": ("mempalace_list_tunnels", {}),
    "follow_tunnels": ("mempalace_follow_tunnels", {"wing": "other", "room": "general"}),
    "traverse": ("mempalace_traverse", {"start_room": "general"}),
    "find_tunnels": ("mempalace_find_tunnels", {}),
    "graph_stats": ("mempalace_graph_stats", {}),
    "list_hallways": ("mempalace_list_hallways", {}),
    "check_duplicate": ("mempalace_check_duplicate", {"content": TEXT, "threshold": 0.1}),
    "diary_read": ("mempalace_diary_read", {"agent_name": "probe", "wing": "shared"}),
}


@pytest.mark.parametrize("route", sorted(READS))
@pytest.mark.parametrize("form", sorted(FORMS))
def test_thread_blind_reader_never_receives_thread_identity(
    hub, collection, config, blind, form, route
):
    thread = hidden_thread(hub, blind, form)
    seed(collection, config, FORMS[form](thread))
    name, args = READS[route]
    raw = mcp(hub, blind[1], name, **args)
    assert secret(thread, form) not in raw, raw


@pytest.mark.parametrize("route", sorted(READS))
@pytest.mark.parametrize(
    "form", ["bare", "app_path", "underscore_suffix", "fullwidth", "mcp_source"]
)
def test_thread_reader_keeps_room_on_every_route(hub, collection, config, reader, form, route):
    thread = readable_thread(hub, reader, form)
    seed(collection, config, FORMS[form](thread))
    name, args = READS[route]
    raw = mcp(hub, reader[1], name, **args)
    assert secret(thread, form) in raw, raw


@pytest.mark.parametrize("route", ["/api/memory/" + DRAWER, "/api/memory/search?q=plan"])
@pytest.mark.parametrize("form", sorted(FORMS))
def test_member_rest_never_receives_thread_identity(hub, collection, config, blind, form, route):
    headers = member(hub)
    thread = hidden_thread(hub, blind, form)
    seed(collection, config, FORMS[form](thread))
    _, raw = rest(hub, headers, route)
    assert secret(thread, form) not in raw, raw


WRITES = {
    "add_drawer": lambda r: ("mempalace_add_drawer", {"wing": "shared", "room": r, "content": "x"}),
    "update_drawer": lambda r: (
        "mempalace_update_drawer",
        {"drawer_id": "probe-general", "room": r},
    ),
    "checkpoint_item": lambda r: (
        "mempalace_checkpoint",
        {"items": [{"wing": "shared", "room": r, "content": "Checkpoint words"}]},
    ),
    "checkpoint_diary": lambda r: (
        "mempalace_checkpoint",
        {"items": [], "diary": {"agent_name": "probe", "entry": "x", "topic": r}},
    ),
    "create_tunnel": lambda r: (
        "mempalace_create_tunnel",
        {"source_wing": "shared", "source_room": r, "target_wing": "other", "target_room": "x"},
    ),
    "diary_write": lambda r: (
        "mempalace_diary_write",
        {"agent_name": "probe", "entry": "x", "topic": r, "wing": "shared"},
    ),
}


@pytest.mark.parametrize("route", sorted(WRITES))
@pytest.mark.parametrize("form", WRITABLE)
def test_thread_blind_writer_cannot_file_into_thread_room(
    hub, collection, config, blind, form, route
):
    thread = hidden_thread(hub, blind, form)
    seed(collection, config, "general")
    name, args = WRITES[route](FORMS[form](thread))
    raw = mcp(hub, blind[1], name, **args)
    assert "Room not found" in raw, raw


@pytest.mark.parametrize("form", sorted(FORMS))
def test_thread_blind_writer_cannot_delete_thread_room_drawer(hub, collection, config, blind, form):
    thread = hidden_thread(hub, blind, form)
    seed(collection, config, FORMS[form](thread))
    raw = mcp(hub, blind[1], "mempalace_delete_drawer", drawer_id=DRAWER)
    assert collection.get(ids=[DRAWER])["ids"] == [DRAWER], raw


@pytest.mark.parametrize("form", sorted(set(FORMS) - {"undecodable"}))
def test_owner_session_reads_thread_room_unchanged(hub, collection, config, form):
    thread = store_thread(hub, form, actor=hub.admin, wing="private_chats")
    room = FORMS[form](thread)
    seed(collection, config, room)
    headers = owner(hub)
    status, raw = rest(hub, headers, f"/api/memory/{DRAWER}")
    assert status == 200, raw
    assert json.loads(raw)["topic"] == room
    status, raw = rest(hub, headers, "/api/memory/search?q=plan")
    assert status == 200, raw
    assert room in [hit["topic"] for hit in json.loads(raw)["results"]]


@pytest.mark.parametrize("room", ["general", "thr_notes", "thr_deadline", "mcp-notes"])
def test_ordinary_rooms_unchanged(hub, collection, config, blind, room):
    seed(collection, config, room)
    status, raw = rest(hub, member(hub), f"/api/memory/{DRAWER}")
    assert status == 200, raw
    assert json.loads(raw)["topic"] == room
    got = mcp(hub, blind[1], "mempalace_get_drawer", drawer_id=DRAWER)
    assert json.loads(json.loads(got)["result"]["content"][0]["text"])["room"] == room


def test_room_references_names_every_form():
    class Thread:
        id = "thr_" + "ab12" * 4
        source_thread_id = "mcp:probe-1"

    for form, make in FORMS.items():
        if form == "undecodable":
            continue
        expected = Thread.source_thread_id if form == "mcp_source" else Thread.id
        assert expected in provenance_refs.room_references(make(Thread)), form
    for ordinary in ("general", "thr_notes", "thr_deadline", "thr_abcdefg", None):
        assert provenance_refs.room_references(ordinary) == (), ordinary
    # Values that cannot be decoded name a thread nobody can prove readable.
    for undecodable in (
        "%FF" + Thread.id,
        "thr%252525255Fab12",
        "thr​_ab12",
    ):
        assert provenance_refs.room_references(undecodable) is None, undecodable
    assert mirror_access.thread_ids is provenance_refs.room_references
