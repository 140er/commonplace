"""Bounded, selective fact lookup and failure isolation for RFC 009."""

import inspect

import pytest

from mempalace import mcp_server, searcher
from mempalace.fact_render import format_facts
from mempalace.knowledge_graph import KnowledgeGraph
from mempalace.searcher import facts as fact_search
from tests._mcp_server_helpers import _patch_mcp_server


@pytest.mark.parametrize(
    "query", ["what is a the of at it", "is_a", "who what where", "status of it"]
)
def test_filler_and_predicate_glue_do_not_retrieve_unrelated_facts(query):
    with KnowledgeGraph.empty() as kg:
        kg.add_triple("Alice", "member_of", "the band", wing="shared")
        kg.add_triple("Max", "is_a", "dog", wing="shared")
        assert fact_search.search_facts(kg, query) == []


def test_filler_only_query_never_queries_the_graph(monkeypatch):
    with KnowledgeGraph.empty() as kg:
        monkeypatch.setattr(
            kg, "search_fact_rows", lambda *a, **kw: pytest.fail("filler queried KG")
        )
        assert fact_search.search_facts(kg, "what is the of at") == []


def test_rare_terms_outrank_common_matches():
    with KnowledgeGraph.empty() as kg:
        for i in range(120):
            kg.add_triple(f"Item {i}", "belongs_to", "project", wing="shared")
        target = kg.add_triple("Target", "color", "cyan", wing="shared")
        found = fact_search.search_facts(kg, "project cyan")
        assert found[0]["id"] == target
        assert found[0]["lexical_relevance"] > 0.9
        assert all(f["lexical_relevance"] < 0.1 for f in found[1:])


def test_common_terms_alone_still_retrieve_matches():
    with KnowledgeGraph.empty() as kg:
        for i in range(60):
            kg.add_triple(f"Item {i}", "belongs_to", "project", wing="shared")
        assert len(fact_search.search_facts(kg, "project")) == 5


def test_library_memory_search_skips_facts_it_does_not_use(monkeypatch):
    from commonplace import memory_tools

    calls = []

    def memory(ctx, name, params):
        calls.append((name, params))
        return {"results": []}

    monkeypatch.setattr(memory_tools, "_memory", memory)
    assert memory_tools._search(None, {"query": "needle"}) == []
    assert calls == [("mempalace_search", {"query": "needle", "limit": 5, "include_facts": False})]


def test_observation_sources_have_stable_order():
    with KnowledgeGraph.empty() as kg:
        for thread, seq in (("thr_b", 2), ("thr_a", 9), ("thr_a", 1)):
            kg.save_fact(
                "Project",
                "status",
                "green",
                wing="shared",
                source_thread=thread,
                source_seq_start=seq,
                source_seq_end=seq,
            )
        kg._conn().execute(
            "UPDATE fact_sources SET added_at=CASE source_thread WHEN 'thr_b' THEN '2026-02-01' ELSE '2026-01-01' END"
        )
        (fact,) = fact_search.search_facts(kg, "green")
        assert [(s["source_thread"], s["source_seq_start"]) for s in fact["sources"]] == [
            ("thr_a", 1),
            ("thr_a", 9),
            ("thr_b", 2),
        ]


def test_sql_candidate_cap_precedes_source_loading_and_retains_key_history():
    with KnowledgeGraph.empty() as kg:
        old = kg.save_fact(
            "Target",
            "status",
            "ancientvalue",
            wing="shared",
            single_valued=True,
            valid_from="2025-01-01",
        )["triple_id"]
        current = kg.save_fact(
            "Target", "status", "modern", wing="shared", single_valued=True, valid_from="2025-02-01"
        )["triple_id"]
        for i in range(120):
            kg.add_triple(f"Other {i}", "mentions", "Target ancientvalue", wing="shared")
        hidden = kg.add_triple("Target", "status", "hidden", wing="vault")
        query = "Target ancientvalue"
        seeds = {
            r[0]
            for r in kg._conn().execute(
                "SELECT triple_id FROM fact_search WHERE fact_search MATCH ? ORDER BY rank LIMIT ?",
                ('"target" OR "ancientvalue"', fact_search.FACT_CANDIDATE_LIMIT),
            )
        }
        assert old in seeds and current not in seeds
        rows = kg.search_fact_rows(
            {"target", "ancientvalue"},
            candidate_limit=fact_search.FACT_CANDIDATE_LIMIT,
            common_term_fraction=fact_search.FACT_COMMON_TERM_FRACTION,
            wings=["shared"],
        )
        assert (
            len({(r["subject"], r["predicate"]) for r in rows}) == fact_search.FACT_CANDIDATE_LIMIT
        )
        assert len(rows) == fact_search.FACT_CANDIDATE_LIMIT + 1
        assert {r["id"] for r in rows} >= {old, current}
        assert hidden not in {r["id"] for r in rows}
        ranked = fact_search.search_facts(kg, query, wings=["shared"], limit=None)
        ids = [f["id"] for f in ranked]
        assert ids.index(current) < ids.index(old)


def test_scope_is_applied_before_the_candidate_cap():
    with KnowledgeGraph.empty() as kg:
        for i in range(100):
            kg.add_triple(f"Hidden {i}", "status", "cyan", wing="vault")
        public = kg.add_triple("Visible", "status", "cyan", wing="shared")
        assert [f["id"] for f in fact_search.search_facts(kg, "cyan", wings=["shared"])] == [public]


def test_vacuum_preserves_fact_ids_and_index_matches():
    with KnowledgeGraph.empty() as kg:
        removed = kg.add_triple("Remove", "has", "first", wing="shared")
        target = kg.add_triple("Keep", "uses", "pnpm", wing="shared")
        another = kg.add_triple("Other", "uses", "uv", wing="shared")
        conn = kg._conn()
        conn.execute("DELETE FROM triples WHERE id=?", (removed,))
        conn.commit()
        before = list(conn.execute("SELECT docid, triple_id FROM fact_search_keys ORDER BY docid"))
        # Some SQLite builds preserve rowids during VACUUM. Force a renumber
        # too, so this also fails for an index coupled to triples.rowid.
        conn.execute("UPDATE triples SET rowid=rowid+1000")
        conn.commit()
        conn.execute("VACUUM")
        after = list(conn.execute("SELECT docid, triple_id FROM fact_search_keys ORDER BY docid"))
        assert before == after
        assert fact_search.search_facts(kg, "pnpm")[0]["id"] == target
        assert fact_search.search_facts(kg, "uv")[0]["id"] == another


def test_rowid_index_upgrade_rebuilds_only_derived_data():
    from mempalace.fact_index import install

    with KnowledgeGraph.empty() as kg:
        tid = kg.add_triple("Keep", "uses", "pnpm", wing="shared", source_drawer_id="drawer")
        before = dict(kg._conn().execute("SELECT * FROM triples WHERE id=?", (tid,)).fetchone())
        conn = kg._conn()
        for name in ("insert", "update", "delete", "rename"):
            conn.execute("DROP TRIGGER fact_search_" + name)
        for name in ("fact_search_vocab", "fact_search", "fact_search_keys"):
            conn.execute("DROP TABLE " + name)
        conn.execute("CREATE VIRTUAL TABLE fact_search USING fts5(subject,predicate,object)")
        conn.execute(
            "INSERT INTO fact_search(rowid,subject,predicate,object) VALUES(999,'wrong','uses','pnpm')"
        )
        install(conn)
        assert dict(conn.execute("SELECT * FROM triples WHERE id=?", (tid,)).fetchone()) == before
        (fact,) = fact_search.search_facts(kg, "pnpm")
        assert fact["id"] == tid and fact["source_drawer_id"] == "drawer"


@pytest.mark.parametrize(
    "exception",
    [ValueError("private detail"), TypeError("private detail"), RuntimeError("private detail")],
)
def test_any_fact_exception_preserves_library_and_cli_drawers(
    monkeypatch, palace_path, seeded_collection, capsys, exception
):
    plain = searcher.search_memories("JWT authentication", palace_path, include_facts=False)

    def broken(*args, **kwargs):
        raise exception

    monkeypatch.setattr(fact_search, "search_facts", broken)
    combined = searcher.search_memories("JWT authentication", palace_path)
    assert combined["results"] == plain["results"]
    assert combined["facts"] == [] and combined["facts_error"] == "unavailable"
    assert "private detail" not in str(combined)
    searcher.search("JWT authentication", palace_path)
    output = capsys.readouterr().out
    assert "[1]" in output and "facts_error: unavailable" in output
    assert "private detail" not in output


def test_mcp_fact_exception_keeps_drawers_and_returns_generic_marker(monkeypatch, config, kg):
    _patch_mcp_server(monkeypatch, config, kg)
    monkeypatch.setattr(mcp_server, "_refresh_vector_disabled_flag", lambda: None)
    monkeypatch.setattr(
        mcp_server, "search_memories", lambda *a, **kw: {"results": [{"text": "exact drawer"}]}
    )

    def broken(*args, **kwargs):
        raise ValueError("private detail")

    monkeypatch.setattr(fact_search, "search_facts", broken)
    result = mcp_server.tool_search("needle")
    assert result == {
        "results": [{"text": "exact drawer"}],
        "facts": [],
        "facts_error": "unavailable",
    }


def test_kg_search_bad_as_of_is_a_normal_tool_error(monkeypatch, config, kg):
    _patch_mcp_server(monkeypatch, config, kg)
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "mempalace_kg_query",
            "arguments": {"entity": "Kestrel", "search": True, "as_of": "not-a-date"},
        },
    }
    response = mcp_server.handle_request(request)
    assert "error" not in response  # No JSON-RPC -32000.
    import json

    result = json.loads(response["result"]["content"][0]["text"])
    assert set(result) == {"error"}


def test_public_defaults_follow_the_drawer_function(monkeypatch, palace_path):
    public = inspect.signature(searcher.search_memories)
    inner = inspect.signature(searcher._search_drawer_memories)
    assert (
        public.parameters["candidate_strategy"].default
        == inner.parameters["candidate_strategy"].default
    )
    seen = []

    def future_inner(query, palace_path, candidate_strategy="union", **kwargs):
        seen.append(candidate_strategy)
        return {"results": []}

    monkeypatch.setattr(searcher, "_search_drawer_memories", future_inner)
    searcher.search_memories("needle", palace_path, include_facts=False)
    assert seen == ["union"]


def test_missing_provenance_keys_and_source_order_are_safe():
    sources = [
        {"added_at": "2026-02-01", "source_thread": "thr_b", "source_seq_start": 2},
        {"added_at": "2026-01-01", "source_thread": "thr_a", "source_seq_start": 9},
        {"added_at": "2026-01-01", "source_thread": "thr_a", "source_seq_start": 1},
    ]
    fact = {"subject": "Project", "predicate": "status", "object": "green", "sources": sources}
    output = format_facts([fact])
    assert "origin=unknown:unknown" in output
    assert output.index("[1:") < output.index("[9:") < output.index("thr_b")
    assert format_facts([None]) == "\n  facts_error: unavailable\n"
