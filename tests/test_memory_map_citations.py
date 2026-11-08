"""Map-only citations must not expose even a member's own private thread."""

import json
import sqlite3
from pathlib import Path

import pytest

from commonplace.handoff.models import ThreadMessage
from mempalace.knowledge_graph import KnowledgeGraph
from mempalace.paths import KNOWLEDGE_GRAPH_FILENAME
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_memory import _save
from tests.test_memory_map_privacy import graph, person


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


def test_member_own_private_thread_selection_keeps_null_source(hub):
    _save(hub, hub.owner_token(), "Visible anchor", theme="shared")
    uid, headers = person(hub)
    thread = private_thread(hub, uid)
    status, raw = hub.raw(
        None,
        {
            "text": "Public exact words",
            "theme": "shared",
            "source": {"thread": thread.id, "seq_start": 0, "seq_end": 0},
        },
        method="POST",
        path="/api/memory",
        headers=headers,
    )
    assert status == 201, raw
    identifier = json.loads(raw)["id"]
    path = f"/api/memory/{identifier}"
    status, raw = hub.raw(None, b"", method="GET", path=path, headers=headers)
    assert status == 200 and json.loads(raw)["source"]["thread"] == thread.id
    status, raw = hub.raw(None, b"", method="GET", path=path + "?view=map", headers=headers)
    assert status == 200 and json.loads(raw)["text"] == "Public exact words"
    assert "source" in json.loads(raw) and json.loads(raw)["source"] is None
    assert thread.id not in raw


def test_private_thread_topic_is_null_in_map_only(hub, collection):
    _save(hub, hub.owner_token(), "Visible anchor", theme="shared")
    uid, headers = person(hub)
    thread = private_thread(hub, uid)
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
    path = "/api/memory/thread-topic"
    status, raw = hub.raw(None, b"", method="GET", path=path, headers=headers)
    assert status == 200 and json.loads(raw)["topic"] == thread.id
    status, raw = hub.raw(None, b"", method="GET", path=path + "?view=map", headers=headers)
    assert status == 200
    assert json.loads(raw)["topic"] is None and json.loads(raw)["source"] is None


def test_member_orphan_people_remain_visible(hub):
    _save(hub, hub.owner_token(), "Visible anchor", theme="shared")
    _, headers = person(hub)
    path = Path(hub.gateway.workspace_dir(hub.workspace)) / "palace" / KNOWLEDGE_GRAPH_FILENAME
    with KnowledgeGraph(str(path)) as kg:
        kg.add_entity("Orphan person", "person")
    assert "Orphan person" in json.dumps(graph(hub, headers))


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
