"""save_fact folds a new fact into the graph: merge, supersede, history or add."""

import pytest

from mempalace.knowledge_graph import KnowledgeGraph


def _row(kg, triple_id):
    return kg._conn().execute("SELECT * FROM triples WHERE id = ?", (triple_id,)).fetchone()


def _count(kg):
    return kg._conn().execute("SELECT COUNT(*) FROM triples").fetchone()[0]


def test_a_new_fact_is_added_with_its_source(kg):
    out = kg.save_fact("api", "uses", "sqlite", wing="w", source_thread="t1", source_seq_start=4)
    assert out["action"] == "added"
    assert kg.sources([out["triple_id"]])[out["triple_id"]] == [
        {
            "source_thread": "t1",
            "source_seq_start": 4,
            "source_seq_end": 4,
            "added_at": kg.sources([out["triple_id"]])[out["triple_id"]][0]["added_at"],
        }
    ]


def test_the_same_fact_merges_and_keeps_every_source(kg):
    first = kg.save_fact("api", "uses", "sqlite", wing="w", source_thread="t1", source_seq_start=4)
    again = kg.save_fact(
        "api",
        "uses",
        "sqlite",
        wing="w",
        source_thread="t2",
        source_seq_start=9,
        source_seq_end=11,
        sensitivity="private",
        confidence=0.5,
    )
    assert again == {"action": "merged", "triple_id": first["triple_id"], "source_added": True}
    assert _count(kg) == 1
    row = _row(kg, first["triple_id"])
    assert row["seen_count"] == 2
    # Sensitivity only rises; confidence keeps the higher value.
    assert row["sensitivity"] == "private" and row["confidence"] == 1.0
    threads = [s["source_thread"] for s in kg.sources([first["triple_id"]])[first["triple_id"]]]
    assert threads == ["t1", "t2"]
    # Seeing the same range again adds no source.
    repeat = kg.save_fact(
        "api", "uses", "sqlite", wing="w", source_thread="t2", source_seq_start=9, source_seq_end=11
    )
    assert repeat["source_added"] is False
    assert _row(kg, first["triple_id"])["seen_count"] == 3


def test_add_triple_retry_records_the_new_source(kg):
    tid = kg.add_triple("api", "uses", "sqlite", source_thread="t1", source_seq_start=1)
    assert kg.add_triple("api", "uses", "sqlite", source_thread="t2", source_seq_start=5) == tid
    assert [s["source_thread"] for s in kg.sources([tid])[tid]] == ["t1", "t2"]


def test_a_single_valued_predicate_is_replaced(kg):
    old = kg.save_fact("Jeffrey", "works_at", "Acme", wing="w", valid_from="2024-01-01")
    new = kg.save_fact(
        "Jeffrey", "works_at", "Initech", wing="w", valid_from="2026-02-01", single_valued=True
    )
    assert new["action"] == "superseded"
    assert new["superseded"] == [old["triple_id"]] and new["previous"] == ["Acme"]
    old_row = _row(kg, old["triple_id"])
    assert old_row["status"] == "superseded" and old_row["superseded_by"] == new["triple_id"]
    assert old_row["valid_to"] == "2026-02-01T00:00:00Z"
    assert _row(kg, new["triple_id"])["valid_from"] == "2026-02-01T00:00:00Z"
    (current,) = kg.query_entity("Jeffrey", as_of="2026-03-01")
    assert current["object"] == "Initech"


def test_a_multi_valued_predicate_keeps_both(kg):
    kg.save_fact("Max", "loves", "chess", wing="w")
    out = kg.save_fact("Max", "loves", "swimming", wing="w")
    assert out["action"] == "added"
    assert sorted(f["object"] for f in kg.query_entity("Max") if f["current"]) == [
        "chess",
        "swimming",
    ]


def test_older_news_is_filed_as_history(kg):
    current = kg.save_fact("Jeffrey", "works_at", "Initech", wing="w", valid_from="2026-02-01")
    old = kg.save_fact(
        "Jeffrey", "works_at", "Acme", wing="w", valid_from="2024-01-01", single_valued=True
    )
    assert old["action"] == "historical" and old["current"] == current["triple_id"]
    row = _row(kg, old["triple_id"])
    assert (row["valid_from"], row["valid_to"]) == ("2024-01-01", "2026-02-01")
    assert _row(kg, current["triple_id"])["valid_to"] is None
    (then,) = kg.query_entity("Jeffrey", as_of="2025-01-01")
    assert then["object"] == "Acme"


def test_replacement_stays_in_its_wing_and_visibility(kg):
    other = kg.save_fact("Jeffrey", "works_at", "Acme", wing="other")
    hidden = kg.save_fact("Jeffrey", "works_at", "Secret Co", wing="w", sensitivity="private")
    out = kg.save_fact(
        "Jeffrey",
        "works_at",
        "Initech",
        wing="w",
        single_valued=True,
        sensitivities=["public", "internal"],
    )
    assert out["action"] == "added"
    assert _row(kg, other["triple_id"])["valid_to"] is None
    assert _row(kg, hidden["triple_id"])["valid_to"] is None
    # A caller that can't see the private copy never merges into it either.
    again = kg.save_fact(
        "Jeffrey", "works_at", "Secret Co", wing="w", sensitivities=["public", "internal"]
    )
    assert again["action"] == "added" and again["triple_id"] != hidden["triple_id"]


def test_the_successor_is_as_sensitive_as_what_it_replaces(kg):
    kg.save_fact("Jeffrey", "lives_in", "Utrecht", wing="w", sensitivity="private")
    out = kg.save_fact("Jeffrey", "lives_in", "Delft", wing="w", single_valued=True)
    assert _row(kg, out["triple_id"])["sensitivity"] == "private"


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"confidence": 2}, "confidence"),
        ({"confidence": True}, "confidence"),
        ({"source_seq_start": 3}, "source_thread"),
        ({"sensitivity": "top"}, "sensitivity"),
        ({"valid_from": "yesterday"}, "valid_from"),
    ],
)
def test_bad_input_writes_nothing(kg, kwargs, message):
    with pytest.raises(ValueError, match=message):
        kg.save_fact("a", "b", "c", **kwargs)
    assert _count(kg) == 0


def test_a_legacy_graph_gains_the_sources_table(tmp_path):
    import sqlite3

    path = str(tmp_path / "legacy.sqlite3")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE entities (id TEXT PRIMARY KEY, name TEXT NOT NULL,
            type TEXT DEFAULT 'unknown', properties TEXT DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE triples (id TEXT PRIMARY KEY, subject TEXT NOT NULL,
            predicate TEXT NOT NULL, object TEXT NOT NULL, valid_from TEXT,
            valid_to TEXT, confidence REAL DEFAULT 1.0, source_closet TEXT,
            source_file TEXT, extracted_at TEXT DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO entities (id, name) VALUES ('max', 'Max'), ('chess', 'chess');
        INSERT INTO triples (id, subject, predicate, object)
            VALUES ('t_old', 'max', 'loves', 'chess');
        """
    )
    conn.commit()
    conn.close()
    with KnowledgeGraph(db_path=path) as kg:
        assert _row(kg, "t_old")["seen_count"] == 1
        out = kg.save_fact("Max", "loves", "chess", source_thread="t", source_seq_start=1)
        assert out == {"action": "merged", "triple_id": "t_old", "source_added": True}
        assert kg.sources(["t_old"])["t_old"][0]["source_thread"] == "t"
