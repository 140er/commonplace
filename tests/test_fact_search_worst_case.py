"""Scope, history and sanitization regressions for bounded fact lookup."""

import logging

import pytest

from mempalace import fact_index, mcp_server
from mempalace.knowledge_graph import KnowledgeGraph
from mempalace.searcher import facts as fact_search
from tests._mcp_server_helpers import _patch_mcp_server


@pytest.mark.parametrize("entry", ["facts", "kg_search", "kg_query", "search"])
def test_long_query_is_sanitized_once(monkeypatch, config, kg, caplog, entry):
    _patch_mcp_server(monkeypatch, config, kg)
    kg.add_triple("Kestrel", "deploy_port", "8443", wing="shared")
    monkeypatch.setattr(mcp_server, "_refresh_vector_disabled_flag", lambda: None)
    monkeypatch.setattr(mcp_server, "search_memories", lambda *a, **kw: {"results": []})
    query = "Kestrel " + "deployment " * 20 + "port?"
    assert 200 < len(query) <= 250
    calls = []
    sanitize = fact_search.sanitize_query

    def counted(raw):
        calls.append(raw)
        return sanitize(raw)

    monkeypatch.setattr(fact_search, "sanitize_query", counted)
    monkeypatch.setattr(mcp_server, "sanitize_query", counted)
    caplog.set_level(logging.WARNING, logger="mempalace.query_sanitizer")
    if entry == "facts":
        found = fact_search.search_facts(kg, query)
    elif entry == "kg_search":
        found = mcp_server.tool_kg_search(query)["facts"]
    elif entry == "kg_query":
        found = mcp_server.tool_kg_query(query, search=True)["facts"]
    else:
        found = mcp_server.tool_search(query)["facts"]
    assert [fact["object"] for fact in found] == ["8443"]
    assert calls == [query]
    warnings = [record for record in caplog.records if "Query sanitized:" in record.message]
    assert len(warnings) == 1


def test_common_seed_prefix_is_scoped_and_selected_history_is_not_capped():
    seed_cap = fact_search.FACT_CANDIDATE_LIMIT * fact_index.FALLBACK_SEED_MULTIPLIER
    with KnowledgeGraph.empty() as kg:
        for i in range(seed_cap + 1):
            kg.add_triple("Hidden entity project", "status", f"phase {i}", wing="vault")
        history = {
            kg.add_triple(
                "Visible entity project",
                "status",
                f"phase {i}",
                wing="shared",
                valid_to="2025-01-01",
            )
            for i in range(seed_cap + 1)
        }
        current = kg.add_triple("Visible entity project", "status", "current", wing="shared")
        hidden = kg.add_triple(
            "Visible entity project", "status", "private", wing="shared", sensitivity="private"
        )
        found = fact_search.search_facts(
            kg, "entity project", wings=["shared"], sensitivities=["internal"], limit=None
        )
        assert found[0]["id"] == current
        assert {fact["id"] for fact in found} == history | {current}
        assert hidden not in {fact["id"] for fact in found}
        assert all(not fact["current"] for fact in found[1:])


def test_common_seed_sql_work_does_not_grow_with_unselected_matches():
    with KnowledgeGraph.empty() as kg:
        conn = kg._conn()

        def append(start, end):
            conn.executemany(
                "INSERT INTO entities(id,name) VALUES(?,?)",
                ((f"e{i}", f"Entity project {i}") for i in range(start, end)),
            )
            conn.executemany(
                "INSERT INTO triples(id,subject,predicate,object,wing) VALUES(?,?,?,?,?)",
                ((f"t{i}", f"e{i}", "status", "e0", "shared") for i in range(start, end)),
            )

        def lookup():
            steps = 0

            def progress():
                nonlocal steps
                steps += 100
                return 0

            conn.set_progress_handler(progress, 100)
            try:
                rows = kg.search_fact_rows(
                    {"entity", "project"},
                    candidate_limit=fact_search.FACT_CANDIDATE_LIMIT,
                    common_term_fraction=fact_search.FACT_COMMON_TERM_FRACTION,
                )
            finally:
                conn.set_progress_handler(None, 0)
            return {row["id"] for row in rows}, steps

        append(0, 2000)
        initial, initial_steps = lookup()
        append(2000, 10000)
        expanded, expanded_steps = lookup()
        assert len(initial) == fact_search.FACT_CANDIDATE_LIMIT
        assert expanded == initial
        # Count VM work, not wall time: unrelated matches must not trigger
        # another complete SQL scan or change the bounded candidate prefix.
        assert expanded_steps <= initial_steps * 1.25
