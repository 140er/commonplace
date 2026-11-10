"""Map-only citations must not expose even a member's own private thread."""

import json
import sqlite3
from pathlib import Path

import pytest

from commonplace import memory_tools
from commonplace.handoff.models import ThreadMessage
from mempalace.knowledge_graph import KnowledgeGraph
from mempalace.paths import KNOWLEDGE_GRAPH_FILENAME
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_memory import _save
from tests.test_memory_map_privacy import graph, owner_ctx, person, unproved


def private_thread(hub, uid):
    return hub.gateway.handoff.store_for(hub.workspace).store(
        hub.workspace,
        f"capture:{uid}:map-citation",
        [ThreadMessage(0, "user", "message", "Private exact words", "Private exact words")],
        actor=uid,
        wing="private_chats",
    )


@pytest.mark.parametrize("citation", ["fact", "fact_source"])
def test_member_own_private_thread_fact_equals_absent(hub, citation):
    _save(hub, hub.owner_token(), "Visible anchor", theme="shared")
    uid, headers = person(hub)
    thread = private_thread(hub, uid)
    before = graph(hub, headers)
    path = Path(hub.gateway.workspace_dir(hub.workspace)) / "palace" / KNOWLEDGE_GRAPH_FILENAME
    with KnowledgeGraph(str(path)) as kg:
        kg.add_entity("Private Alice", "person")
        kg.add_entity("Private Bob", "person")
        kg.add_triple(
            "Private Alice",
            "private chat citation",
            "Private Bob",
            wing="shared",
            source_thread=thread.id if citation == "fact" else None,
        )
    if citation == "fact_source":
        with sqlite3.connect(path) as conn:
            identifier = conn.execute("SELECT id FROM triples").fetchone()[0]
            conn.execute(
                "INSERT INTO fact_sources(triple_id, source_thread, source_seq_start, source_seq_end, added_at) VALUES (?, ?, 0, 0, '2026-11-01T00:00:00Z')",
                (identifier, thread.id),
            )
    assert graph(hub, headers) == before


def test_private_thread_source_is_null_in_map_only(hub, collection):
    ctx = owner_ctx(hub)
    thread = private_thread(hub, hub.admin)
    collection.add(
        ids=["thread-source"],
        documents=["Public exact words"],
        metadatas=[
            {
                "wing": "shared",
                "room": "general",
                "source_thread": thread.id,
                "source_seq_start": 0,
                "source_seq_end": 0,
            }
        ],
    )
    # A proved owner keeps the citation; without proof only the map drops it.
    assert memory_tools._map_memory(ctx, "thread-source")["source"]["thread"] == thread.id
    result = unproved(hub, ctx, memory_tools._map_memory, "thread-source")
    assert result["text"] == "Public exact words" and result["source"] is None
    assert thread.id not in json.dumps(result)


def test_private_thread_topic_is_null_in_map_only(hub, collection):
    ctx = owner_ctx(hub)
    thread = private_thread(hub, hub.admin)
    collection.add(
        ids=["thread-topic"],
        documents=["Public exact words"],
        metadatas=[
            {
                "wing": "shared",
                "room": thread.id,
                "source_thread": thread.id,
                "source_seq_start": 0,
                "source_seq_end": 0,
            }
        ],
    )
    assert memory_tools._map_memory(ctx, "thread-topic")["topic"] == thread.id
    result = unproved(hub, ctx, memory_tools._map_memory, "thread-topic")
    assert result["topic"] is None and result["source"] is None


ROOMS = {
    "app_threads": "/app/threads/{}",
    "app_deep": "/app/threads/{}/messages/3",
    "t3_link": "t3-thread://v1/{}",
    "mcp_prefix": "mcp:{}",
}


def select(hub, headers, identifier, suffix):
    return hub.raw(
        None, b"", method="GET", path=f"/api/memory/{identifier}{suffix}", headers=headers
    )


@pytest.mark.parametrize("form", ROOMS)
def test_member_map_selection_nulls_thread_reference_topic(hub, collection, form):
    _, headers = person(hub)
    thread = private_thread(hub, hub.admin)
    collection.add(
        ids=["probe"],
        documents=["Public exact words"],
        metadatas=[{"wing": "shared", "room": ROOMS[form].format(thread.id)}],
    )
    status, raw = select(hub, headers, "probe", "?view=map")
    assert status == 200, raw
    body = json.loads(raw)
    assert body["text"] == "Public exact words" and "topic" in body and body["topic"] is None
    assert thread.id not in raw


def test_member_map_selection_keeps_an_ordinary_topic(hub, collection):
    _, headers = person(hub)
    collection.add(
        ids=["probe"],
        documents=["Public exact words"],
        metadatas=[{"wing": "shared", "room": "general"}],
    )
    status, raw = select(hub, headers, "probe", "?view=map")
    assert status == 200 and json.loads(raw)["topic"] == "general"


def test_member_selection_never_returns_a_private_thread_source(hub, collection):
    uid, headers = person(hub)
    thread = private_thread(hub, uid)
    collection.add(
        ids=["probe"],
        documents=["Public exact words"],
        metadatas=[
            {
                "wing": "shared",
                "room": "general",
                "source_thread": thread.id,
                "source_seq_start": 0,
                "source_seq_end": 0,
            }
        ],
    )
    for suffix in ("", "?view=map"):
        status, raw = select(hub, headers, "probe", suffix)
        assert status == 200, raw
        assert json.loads(raw)["text"] == "Public exact words"
        assert json.loads(raw)["source"] is None and thread.id not in raw


def test_member_selection_of_private_thread_room_is_refused_on_every_route(hub, collection):
    uid, headers = person(hub)
    thread = private_thread(hub, uid)
    collection.add(
        ids=["probe"],
        documents=["Public exact words"],
        metadatas=[{"wing": "shared", "room": thread.id, "source_thread": thread.id}],
    )
    for suffix in ("", "?view=map"):
        status, raw = select(hub, headers, "probe", suffix)
        assert status == 404 and thread.id not in raw


def test_member_has_no_people_overlay_and_owner_orphans_stay_visible(hub):
    _save(hub, hub.owner_token(), "Visible anchor", theme="shared")
    _, headers = person(hub)
    path = Path(hub.gateway.workspace_dir(hub.workspace)) / "palace" / KNOWLEDGE_GRAPH_FILENAME
    with KnowledgeGraph(str(path)) as kg:
        kg.add_entity("Orphan person", "person")
    assert "Orphan person" not in json.dumps(graph(hub, headers))
    ctx = owner_ctx(hub)
    assert "Orphan person" in json.dumps(memory_tools.memory_map(ctx))
    assert "Orphan person" in json.dumps(unproved(hub, ctx))


@pytest.mark.parametrize("lookup", ["missing", "error"])
def test_unresolvable_selection_citations_are_null(hub, monkeypatch, lookup):
    from commonplace import memory_tools, sessions
    from commonplace.extensions import ToolContext

    uid, _ = person(hub)
    ctx = ToolContext(
        principal=sessions.person_principal(hub.db, uid, hub.workspace), gateway=hub.gateway
    )
    identifier = "thr_ffffffffffffffff"
    record = {
        "drawer_id": "public",
        "wing": "shared",
        "room": identifier,
        "content": "Exact words",
        "metadata": {"source_thread": identifier},
    }
    monkeypatch.setattr(memory_tools, "_memory", lambda *_: record)
    monkeypatch.setattr(memory_tools, "_boards", lambda *_: None)
    store = hub.gateway.handoff.store_for(hub.workspace)
    calls = []

    def get(thread):
        calls.append(thread)
        if lookup == "error":
            raise RuntimeError("store unavailable")
        return None

    monkeypatch.setattr(store, "get", get)
    result = memory_tools._map_memory(ctx, "public")
    assert result["source"] is None and result["topic"] is None
    assert calls == [identifier]


def test_unreadable_theme_matches_absent_for_wildcard_connection(hub):
    from tests.test_commonplace_memory import _http

    _save(hub, hub.owner_token(), "Private words", theme="private_notes")
    token = hub.token("wildcard", profile="standard", scopes=["memory:read"], read_wings=["*"])
    assert _http(hub, token, "/api/memory/map?theme=private_notes") == _http(
        hub, token, "/api/memory/map?theme=absent"
    )
