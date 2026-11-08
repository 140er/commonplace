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
