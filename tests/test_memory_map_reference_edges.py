"""Map-only derived references and unavailable citation lookups stay private."""

import sqlite3
from pathlib import Path

import pytest

from commonplace import memory_tools
from mempalace.knowledge_graph import KnowledgeGraph
from mempalace.paths import KNOWLEDGE_GRAPH_FILENAME
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_memory import _save
from tests.test_memory_map_citations import private_thread
from tests.test_memory_map_privacy import graph, person


def graph_path(hub):
    return Path(hub.gateway.workspace_dir(hub.workspace)) / "palace" / KNOWLEDGE_GRAPH_FILENAME


def cited_fact(hub, **kwargs):
    with KnowledgeGraph(str(graph_path(hub))) as kg:
        kg.add_entity("Citation Alice", "person")
        kg.add_entity("Citation Bob", "person")
        kg.add_triple("Citation Alice", "derived citation", "Citation Bob", wing="shared", **kwargs)


@pytest.mark.parametrize("citation", ["metadata", "room", "missing", "runtime"])
def test_member_drawer_citation_fails_closed(hub, collection, monkeypatch, citation):
    _save(hub, hub.owner_token(), "Visible anchor", theme="shared")
    uid, headers = person(hub)
    thread = private_thread(hub, uid)
    if citation in ("metadata", "room"):
        metadata = {"wing": "shared", "room": "general"}
        if citation == "room":
            metadata["room"] = thread.id
        else:
            metadata.update(source_thread=thread.id, source_seq_start=0, source_seq_end=0)
        collection.add(ids=["cited-drawer"], documents=["Public exact words"], metadatas=[metadata])
    before = graph(hub, headers)
    cited_fact(hub, source_drawer_id="cited-drawer")
    original = memory_tools._memory

    def memory(ctx, name, args):
        if (
            citation == "runtime"
            and name == "mempalace_get_drawer"
            and args.get("drawer_id") == "cited-drawer"
        ):
            raise RuntimeError("citation lookup unavailable")
        return original(ctx, name, args)

    monkeypatch.setattr(memory_tools, "_memory", memory)
    assert graph(hub, headers) == before


@pytest.mark.parametrize("field", ["origin_id", "origin_link", "approval_link"])
@pytest.mark.parametrize("kind", ["private", "ordinary"])
def test_member_derived_reference_only_filters_threads(hub, field, kind):
    _save(hub, hub.owner_token(), "Visible anchor", theme="shared")
    uid, headers = person(hub)
    thread = private_thread(hub, uid)
    before = graph(hub, headers)
    cited_fact(hub)
    value = thread.id if field == "origin_id" else f"/app/threads/{thread.id}"
    if kind == "ordinary":
        value = "user:writer" if field == "origin_id" else "https://example.test/notes"
    with sqlite3.connect(graph_path(hub)) as conn:
        conn.execute(f"UPDATE triples SET {field} = ?", (value,))
    after = graph(hub, headers)
    if kind == "private":
        assert after == before
    else:
        assert any(link["label"] == "derived_citation" for link in after["links"])


@pytest.mark.parametrize("form", ["app_slash", "app_deep", "url_deep", "t3_slash", "bare_slash"])
@pytest.mark.parametrize("private", [False, True])
def test_member_thread_reference_path_segment(hub, form, private):
    from commonplace.handoff.models import ThreadMessage

    _save(hub, hub.owner_token(), "Visible anchor", theme="shared")
    uid, headers = person(hub)
    thread = hub.gateway.handoff.store_for(hub.workspace).store(
        hub.workspace,
        f"capture:{uid}:reference-path",
        [ThreadMessage(0, "user", "message", "Exact words", "Exact words")],
        actor=uid,
        wing="private_chats" if private else "shared",
    )
    before = graph(hub, headers)
    cited_fact(hub)
    value = {
        "app_slash": f"/app/threads/{thread.id}/",
        "app_deep": f"/app/threads/{thread.id}/messages/3",
        "url_deep": f"https://example.test/app/threads/{thread.id}/messages/3",
        "t3_slash": f"t3-thread://v1/{thread.id}/",
        "bare_slash": f"{thread.id}/",
    }[form]
    with sqlite3.connect(graph_path(hub)) as conn:
        conn.execute("UPDATE triples SET origin_link = ?, approval_link = ?", (value, value))
    after = graph(hub, headers)
    if private:
        assert after == before
    else:
        assert any(link["label"] == "derived_citation" for link in after["links"])


def test_member_ordinary_drawer_room_keeps_fact(hub, collection):
    _save(hub, hub.owner_token(), "Visible anchor", theme="shared")
    _, headers = person(hub)
    collection.add(
        ids=["ordinary-room"],
        documents=["Public exact words"],
        metadatas=[{"wing": "shared", "room": "general"}],
    )
    cited_fact(hub, source_drawer_id="ordinary-room")
    assert any(link["label"] == "derived_citation" for link in graph(hub, headers)["links"])


def test_reference_decision_is_request_local_and_memoized(monkeypatch):
    from commonplace.handoff import provenance_refs

    original = provenance_refs.refs
    calls = []

    def refs(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(provenance_refs, "refs", refs)
    value = "https://example.test/ordinary"
    cache = {}
    assert memory_tools._map_private_reference(None, value, cache) is False
    assert memory_tools._map_private_reference(None, value, cache) is False
    assert calls == [value]
    assert memory_tools._map_private_reference(None, value, {}) is False
    assert calls == [value, value]
