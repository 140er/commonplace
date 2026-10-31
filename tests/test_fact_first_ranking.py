"""Fact ranking, indexed reads and the additive drawer contract (RFC 009)."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from mempalace import provenance
from mempalace.fact_render import format_facts
from mempalace.knowledge_graph import KnowledgeGraph
from mempalace.searcher import search, search_memories
from mempalace.searcher.facts import search_facts

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


def save(kg, obj="pnpm", **kwargs):
    return kg.save_fact(
        "Avery",
        "uses_package_manager",
        obj,
        wing="shared",
        source_thread="thr_source",
        source_seq_start=2,
        source_seq_end=3,
        **kwargs,
    )["triple_id"]


def age(kg, triple_id, days):
    kg._conn().execute(
        "UPDATE fact_sources SET added_at=? WHERE triple_id=?",
        ((NOW - timedelta(days=days)).isoformat(), triple_id),
    )


@pytest.mark.parametrize(
    "approval,weight", [("approved", 1.0), ("proposal", 0.85), ("unknown", 0.75)]
)
@pytest.mark.parametrize(
    "days,decay", [(0, 1.0), (90, 2**-0.5), (180, 0.5), (720, 0.5), (-10, 1.0)]
)
def test_status_weights_and_decay(approval, weight, days, decay):
    with KnowledgeGraph.empty() as kg:
        tid = save(kg)
        kg._conn().execute("UPDATE triples SET approval_status=? WHERE id=?", (approval, tid))
        age(kg, tid, days)
        (fact,) = search_facts(kg, "Avery package manager", now=NOW)
        assert fact["status_weight"] == weight
        assert fact["decay"] == pytest.approx(1.0 if approval == "approved" else decay)
        assert fact["score"] == pytest.approx(weight * fact["decay"])


def test_latest_observation_then_valid_from_then_creation_controls_decay():
    with KnowledgeGraph.empty() as kg:
        tid = save(kg, valid_from="2026-05-03")
        age(kg, tid, 200)
        save(kg, source_url="https://example.com/chat")
        kg._conn().execute(
            "UPDATE fact_sources SET added_at=? WHERE id=(SELECT max(id) FROM fact_sources)",
            ((NOW - timedelta(days=90)).isoformat(),),
        )
        assert search_facts(kg, "pnpm", now=NOW)[0]["decay"] == pytest.approx(2**-0.5)
        kg._conn().execute("DELETE FROM fact_sources")
        assert search_facts(kg, "pnpm", now=NOW)[0]["decay"] == 0.5
        kg._conn().execute("UPDATE triples SET valid_from=NULL, extracted_at=?", (NOW.isoformat(),))
        assert search_facts(kg, "pnpm", now=NOW)[0]["decay"] == 1.0


def test_superseded_value_stays_visible_below_current_successor_with_both_matching():
    with KnowledgeGraph.empty() as kg:
        old = save(kg, "npm", valid_from="2025-01-01", single_valued=True)
        new = save(kg, "pnpm", valid_from="2025-02-01", single_valued=True)
        facts = search_facts(kg, "Avery package manager npm", now=NOW)
        assert [f["id"] for f in facts] == [new, old]
        assert facts[1]["status_weight"] == 0.25
        assert facts[1]["fact_status"] == "superseded"
        assert facts[1]["superseded_by"] == new
        assert "Superseded by: " + new in format_facts(facts)
        (then,) = search_facts(kg, "Avery package manager", as_of="2025-01-15", now=NOW)
        assert then["id"] == old
        (boundary,) = search_facts(kg, "Avery package manager", as_of="2025-02-01", now=NOW)
        assert boundary["id"] == new


def test_stale_sinks_for_its_key_while_unrelated_keys_keep_lexical_order():
    with KnowledgeGraph.empty() as kg:
        old = save(
            kg,
            "alpha bravo charlie delta echo foxtrot golf",
            valid_from="2025-01-01",
            single_valued=True,
        )
        new = save(kg, "pnpm", valid_from="2025-02-01", single_valued=True)
        # A pointer alone must sink and label the old fact, even if a legacy
        # row still has an active lifecycle and no end date.
        kg._conn().execute("UPDATE triples SET status='active', valid_to=NULL WHERE id=?", (old,))
        unrelated = kg.add_triple("Somebody", "mentions", "alpha bravo", wing="shared")
        found = search_facts(
            kg, "Avery Somebody alpha bravo charlie delta echo foxtrot golf", now=NOW
        )
        assert [f["id"] for f in found] == [unrelated, new, old]
        assert found[0]["score"] > found[1]["score"]
        assert found[2]["score"] > found[1]["score"]
        assert found[2]["fact_status"] == "superseded" and found[2]["superseded_by"] == new


@pytest.mark.parametrize(
    "end,current",
    [
        ("2026-10-29", False),
        ("2026-10-30", True),
        ("2026-10-31", True),
        ("2026-10-30T12:00:00Z", False),
    ],
)
def test_invalidated_and_future_end_dates(end, current):
    with KnowledgeGraph.empty() as kg:
        save(kg, valid_from="2025-01-01")
        kg.invalidate("Avery", "uses_package_manager", "pnpm", ended=end, wing="shared")
        (fact,) = search_facts(kg, "pnpm", now=NOW)
        assert fact["current"] is current
        assert fact["status_weight"] == (0.85 if current else 0.25)
        assert fact["fact_status"] == ("proposal" if current else "historical")


def test_whole_words_unicode_predicate_words_and_sanitization():
    with KnowledgeGraph.empty() as kg:
        save(kg)
        kg.save_fact("Élodie", "works_at", "Café", wing="shared")
        assert search_facts(kg, "npm") == []
        assert search_facts(kg, "Ave") == []
        assert search_facts(kg, "package manager")[0]["object"] == "pnpm"
        assert search_facts(kg, "ÉLODIE")[0]["subject"] == "Élodie"
        assert search_facts(kg, "cafe") == []
        assert search_facts(kg, "OR NEAR *") == []


def test_one_indexed_read_includes_all_provenance_and_drawer_sources():
    with KnowledgeGraph.empty() as kg:
        with provenance.verified(
            {
                "user": "owner",
                "connection": "builder",
                "person": True,
                "client": "web",
                "auth": "session",
                "trust": "trusted",
                "workspace": "home",
            }
        ):
            tid = save(kg, source_drawer_id="verbatim-drawer")
        calls = []
        kg._conn().set_trace_callback(calls.append)
        (fact,) = search_facts(kg, "pnpm", now=NOW)
        kg._conn().set_trace_callback(None)
        selects = [sql for sql in calls if sql.startswith(("SELECT", "WITH"))]
        assert len(selects) == 1
        plan = kg._conn().execute("EXPLAIN QUERY PLAN " + selects[0]).fetchall()
        assert any("VIRTUAL TABLE INDEX" in row[3] for row in plan)
        assert any("idx_fact_sources_triple" in row[3] for row in plan)
        assert fact["id"] == tid and fact["source_drawer_id"] == "verbatim-drawer"
        assert fact["source_link"] == "/app/threads/thr_source"
        assert fact["source_seq_start"] == 2 and fact["source_seq_end"] == 3
        assert fact["fact_status"] == fact["approval_status"] == "approved"
        assert fact["origin_kind"] == "user" and fact["approved_by"] == "owner"
        assert fact["sources"][0]["approval_link"] == "/app/threads/thr_source"


def test_private_and_other_wing_facts_never_enter_candidates():
    with KnowledgeGraph.empty() as kg:
        save(kg)
        kg.save_fact(
            "Avery", "uses_package_manager", "private-value", wing="shared", sensitivity="private"
        )
        kg.save_fact("Avery", "uses_package_manager", "other-wing-value", wing="vault")
        found = search_facts(kg, "Avery", wings=["shared"], sensitivities=["public", "internal"])
        assert [f["object"] for f in found] == ["pnpm"]
        assert search_facts(kg, "Avery", wings=[]) == []
        assert search_facts(kg, "Avery", sensitivities=[]) == []


def test_index_backfills_and_tracks_entity_rename_and_fact_deletion(tmp_path):
    path = str(tmp_path / "kg.sqlite3")
    with KnowledgeGraph(db_path=path) as kg:
        tid = save(kg)
        conn = kg._conn()
        for trigger in ("insert", "update", "delete", "rename"):
            conn.execute("DROP TRIGGER fact_search_" + trigger)
        conn.execute("DROP TABLE fact_search")
        conn.commit()
    with KnowledgeGraph(db_path=path) as kg:
        assert search_facts(kg, "pnpm")[0]["id"] == tid
        kg.add_entity("AVERY")
        assert search_facts(kg, "Avery")[0]["subject"] == "AVERY"
        kg._conn().execute("DELETE FROM triples WHERE id=?", (tid,))
        assert search_facts(kg, "pnpm") == []


def test_interrupted_index_install_rolls_back_without_changing_facts():
    from mempalace.fact_index import install

    with KnowledgeGraph.empty() as kg:
        tid = save(kg)
        conn = kg._conn()
        for trigger in ("insert", "update", "delete", "rename"):
            conn.execute("DROP TRIGGER fact_search_" + trigger)
        conn.execute("DROP TABLE fact_search")
        conn.set_authorizer(
            lambda action, *_: (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_CREATE_TRIGGER
                else sqlite3.SQLITE_OK
            )
        )
        with pytest.raises(sqlite3.DatabaseError):
            install(conn)
        conn.set_authorizer(None)
        assert (
            conn.execute("SELECT 1 FROM sqlite_master WHERE name='fact_search'").fetchone() is None
        )
        assert kg.query_entity("Avery")[0]["id"] == tid
        install(conn)
        assert search_facts(kg, "pnpm")[0]["id"] == tid


def test_drawer_results_and_all_existing_metadata_are_identical(palace_path, seeded_collection):
    from mempalace.paths import knowledge_graph_path

    with KnowledgeGraph(db_path=knowledge_graph_path(palace_path)) as kg:
        kg.save_fact("JWT", "uses", "authentication", source_drawer_id="d1", wing="notes")
    plain = search_memories("JWT authentication", palace_path, include_facts=False, n_results=2)
    combined = search_memories("JWT authentication", palace_path, n_results=2)
    assert combined["facts"] and combined["results"]
    assert {k: v for k, v in combined.items() if k != "facts"} == {
        k: v for k, v in plain.items() if k != "facts"
    }


@pytest.mark.parametrize("wings,found", [(["notes"], True), (["other"], False), ([], False)])
def test_fact_search_respects_the_wing_and_wings_intersection(
    palace_path, seeded_collection, wings, found
):
    from mempalace.paths import knowledge_graph_path

    with KnowledgeGraph(db_path=knowledge_graph_path(palace_path)) as kg:
        kg.save_fact("JWT", "uses", "authentication", source_drawer_id="d1", wing="notes")
    result = search_memories("JWT authentication", palace_path, wing="notes", wings=wings)
    assert bool(result["facts"]) == found


def test_cli_facts_appear_above_verbatim_drawers(palace_path, seeded_collection, capsys):
    from mempalace.paths import knowledge_graph_path

    with KnowledgeGraph(db_path=knowledge_graph_path(palace_path)) as kg:
        kg.save_fact("JWT", "uses", "authentication", source_drawer_id="d1", wing="notes")
    search("JWT authentication", palace_path)
    output = capsys.readouterr().out
    assert output.index("Facts") < output.index("[1]")
    assert "Source: d1" in output


def test_broken_fact_index_does_not_gate_working_drawers(palace_path, seeded_collection):
    from pathlib import Path

    from mempalace.paths import knowledge_graph_path

    plain = search_memories("JWT authentication", palace_path, include_facts=False)
    Path(knowledge_graph_path(palace_path)).write_bytes(b"corrupt graph")
    combined = search_memories("JWT authentication", palace_path)
    assert combined["facts"] == [] and combined["results"] == plain["results"]
