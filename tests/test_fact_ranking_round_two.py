"""Small-graph recall, query identity, optional indexing and seed diversity."""

import logging
import sqlite3

import pytest

from mempalace import fact_index, mcp_server
from mempalace.knowledge_graph import KnowledgeGraph
from mempalace.searcher.facts import search_facts
from tests._mcp_server_helpers import _patch_mcp_server

LONG_QUERY = (
    "Earlier in the handoff we discussed the infrastructure for the service; "
    "I need to recall what the Kestrel deploy port ended up being after the TLS migration"
)


@pytest.mark.parametrize("history", [False, True])
@pytest.mark.parametrize(
    "query", ["Kestrel deploy port number", "Kestrel deploy port after the TLS migration"]
)
def test_unknown_words_do_not_hide_small_graph_matches(history, query):
    with KnowledgeGraph.empty() as kg:
        kg.add_triple("Kestrel", "deploy_port", "8443", wing="shared")
        if history:
            kg.add_triple("Kestrel", "deploy_port", "9443", wing="shared", valid_to="2025-01-01")
        found = search_facts(kg, query)
        assert [fact["object"] for fact in found] == (["8443", "9443"] if history else ["8443"])
        assert all(fact["lexical_relevance"] == 1.0 for fact in found)


def test_all_unknown_words_and_an_empty_graph_return_no_facts():
    with KnowledgeGraph.empty() as kg:
        assert search_facts(kg, "unseen migration") == []
        kg.add_triple("Kestrel", "deploy_port", "8443", wing="shared")
        assert search_facts(kg, "unseen migration") == []


@pytest.mark.parametrize("query", ["Will", "where does Will work", "what does IT own", "IT"])
def test_capitalized_names_and_acronyms_survive_stop_words(query):
    with KnowledgeGraph.empty() as kg:
        person = kg.add_triple("Will", "works_at", "Acme", wing="shared")
        team = kg.add_triple("IT", "owns", "laptops", wing="shared")
        expected = team if "IT" in query else person
        assert [fact["id"] for fact in search_facts(kg, query)] == [expected]


@pytest.mark.parametrize(
    "query",
    ["who can", "Who can work", "Who can work? Who can work?", "What is the", "status of it"],
)
def test_question_glue_stays_noise_even_when_capitalized_names_exist(query):
    with KnowledgeGraph.empty() as kg:
        kg.add_triple("WHO", "owns", "the band", wing="shared")
        kg.add_triple("Can", "is_a", "person", wing="shared")
        kg.add_triple("IT", "owns", "laptops", wing="shared")
        assert search_facts(kg, query) == []


def test_long_query_local_search_and_kg_search_match(monkeypatch, config, kg, caplog):
    _patch_mcp_server(monkeypatch, config, kg)
    kg.add_triple("Kestrel", "deploy_port", "8443", wing="shared")
    monkeypatch.setattr(mcp_server, "_refresh_vector_disabled_flag", lambda: None)
    monkeypatch.setattr(mcp_server, "search_memories", lambda *a, **kw: {"results": []})
    assert 128 < len(LONG_QUERY) < 200
    caplog.clear()
    caplog.set_level(logging.WARNING)
    results = [
        mcp_server.tool_kg_query(LONG_QUERY, search=True),
        mcp_server.tool_search(LONG_QUERY),
    ]
    for result in results:
        assert "error" not in result and "facts_error" not in result
        assert [fact["object"] for fact in result["facts"]] == ["8443"]
    assert not caplog.records
    # Entity mode retains the entity-name limit.
    assert "error" in mcp_server.tool_kg_query(LONG_QUERY)


@pytest.mark.parametrize(
    "error", [sqlite3.OperationalError("no such module: fts5"), RuntimeError("build detail")]
)
def test_failed_index_build_keeps_kg_usable_and_retries(monkeypatch, tmp_path, config, error):
    path = str(tmp_path / "kg.sqlite3")
    create = fact_index._create
    attempts = []

    class WithoutFTS5:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, *args):
            if "CREATE VIRTUAL TABLE fact_search USING fts5" in sql:
                raise error
            return self.conn.execute(sql, *args)

    def unavailable(conn):
        attempts.append(True)
        create(WithoutFTS5(conn))

    with monkeypatch.context() as patch:
        patch.setattr(fact_index, "_create", unavailable)
        with KnowledgeGraph(db_path=path) as kg:
            assert not kg._fact_index_ready
            saved = kg.save_fact("Kestrel", "deploy_port", "8443", wing="shared")
            tid = saved["triple_id"]
            _patch_mcp_server(patch, config, kg)
            assert mcp_server.tool_kg_query("Kestrel")["facts"][0]["id"] == tid
            assert mcp_server.tool_kg_search("Kestrel") == {
                "facts": [],
                "facts_error": "unavailable",
            }
            assert (
                kg._conn()
                .execute("SELECT name FROM sqlite_master WHERE name LIKE 'fact_search%'")
                .fetchall()
                == []
            )
        with KnowledgeGraph(db_path=path) as kg:
            assert not kg._fact_index_ready
            assert kg.query_entity("Kestrel")[0]["id"] == tid
        assert len(attempts) == 2
    with KnowledgeGraph(db_path=path) as kg:
        assert kg._fact_index_ready
        assert search_facts(kg, "Kestrel")[0]["id"] == tid


@pytest.mark.parametrize("trigger", ["insert", "update", "delete", "rename"])
def test_missing_index_trigger_is_rebuilt_on_open(tmp_path, trigger):
    path = str(tmp_path / "kg.sqlite3")
    with KnowledgeGraph(db_path=path) as kg:
        kg.add_triple("Kestrel", "deploy_port", "8443", wing="shared")
        kg._conn().execute("DROP TRIGGER fact_search_" + trigger)
        later = kg.add_triple("Heron", "deploy_port", "9443", wing="shared")
    with KnowledgeGraph(db_path=path) as kg:
        names = {
            r[0] for r in kg._conn().execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
        assert {"fact_search_" + name for name in ("insert", "update", "delete", "rename")} <= names
        assert search_facts(kg, "Heron")[0]["id"] == later


def test_failed_upgrade_disables_old_index_hooks_so_kg_writes_continue(monkeypatch, tmp_path):
    path = str(tmp_path / "kg.sqlite3")
    with KnowledgeGraph(db_path=path) as kg:
        kg.add_triple("Kestrel", "deploy_port", "8443", wing="shared")
        kg._conn().execute("DROP TRIGGER fact_search_delete")
        kg._conn().commit()
    with monkeypatch.context() as patch:

        def broken(conn):
            raise sqlite3.OperationalError("no such module: fts5")

        patch.setattr(fact_index, "_create", broken)
        with KnowledgeGraph(db_path=path) as kg:
            assert not kg._fact_index_ready
            assert (
                not kg._conn()
                .execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'fact_search_%'"
                )
                .fetchall()
            )
            later = kg.add_triple("Heron", "deploy_port", "9443", wing="shared")
            assert kg.query_entity("Heron")[0]["id"] == later
    with KnowledgeGraph(db_path=path) as kg:
        assert search_facts(kg, "Heron")[0]["id"] == later


def test_archived_key_cannot_crowd_out_current_other_key():
    with KnowledgeGraph.empty() as kg:
        for i in range(60):
            kg.add_triple("Oldproj", "status", f"phase{i}", wing="shared", valid_to="2024-06-01")
        current = kg.add_triple(
            "Kestrel deployment project",
            "status",
            "green and shipping to production",
            wing="shared",
        )
        found = search_facts(kg, "status", limit=100)
        assert found[0]["id"] == current
        assert len(found) == 61  # History of the selected key remains visible.


def test_seed_cap_is_shared_by_distinct_keys_with_heavy_history():
    with KnowledgeGraph.empty() as kg:
        for i in range(60):
            kg.add_triple("Oldproj", "status", f"phase{i}", wing="shared", valid_to="2024-06-01")
        for i in range(60):
            kg.add_triple(
                f"Project {i}", "status", "green and shipping to production", wing="shared"
            )
        found = search_facts(kg, "status", limit=None)
        assert len({(f["subject"], f["predicate"]) for f in found}) == 50
        assert sum(f["current"] for f in found) == 49
        assert sum(f["subject"] == "Oldproj" for f in found) == 60
