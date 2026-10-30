"""Facts live in wings and carry a sensitivity; reads can be limited to both."""

import sqlite3

import pytest

from mempalace.knowledge_graph import KnowledgeGraph, import_graph_rows, strictest, visibility


def _row(kg, triple_id):
    return kg._conn().execute("SELECT * FROM triples WHERE id = ?", (triple_id,)).fetchone()


def _objects(facts):
    return sorted(f["object"] for f in facts)


def test_a_fact_records_its_wing_and_sensitivity(kg):
    tid = kg.add_triple("api", "uses", "sqlite", wing="project_api", sensitivity="public")
    row = _row(kg, tid)
    assert (row["wing"], row["sensitivity"]) == ("project_api", "public")
    (fact,) = kg.query_entity("api")
    assert (fact["wing"], fact["sensitivity"]) == ("project_api", "public")


def test_defaults_are_no_wing_and_internal(kg):
    row = _row(kg, kg.add_triple("api", "uses", "sqlite"))
    assert row["wing"] is None and row["sensitivity"] == "internal"


def test_the_same_words_in_two_wings_are_two_facts(kg):
    shared = kg.add_triple("api", "uses", "sqlite", wing="shared")
    private = kg.add_triple("api", "uses", "sqlite", wing="private")
    assert shared != private
    assert kg.add_triple("api", "uses", "sqlite", wing="shared") == shared


def test_reads_filter_by_wing_and_sensitivity(kg):
    kg.add_triple("api", "uses", "sqlite", wing="shared")
    kg.add_triple("api", "uses", "postgres", wing="private")
    kg.add_triple("api", "uses", "redis")  # filed before facts had wings
    kg.add_triple("api", "uses", "vault", wing="shared", sensitivity="secret")

    assert _objects(kg.query_entity("api")) == ["postgres", "redis", "sqlite", "vault"]
    assert _objects(kg.query_entity("api", wings=["shared"])) == ["sqlite", "vault"]
    assert _objects(
        kg.query_entity("api", wings=["shared"], sensitivities=["public", "internal"])
    ) == ["sqlite"]
    assert kg.query_entity("api", wings=[]) == []
    assert _objects(kg.query_relationship("uses", wings=["private"])) == ["postgres"]
    assert _objects(kg.timeline("api", wings=["shared", "private"])) == [
        "postgres",
        "sqlite",
        "vault",
    ]
    assert kg.timeline_total("api", wings=["shared"], sensitivities=["internal"]) == 1
    assert kg.timeline_total(wings=[]) == 0


def test_candidates_come_only_from_visible_facts(kg):
    kg.add_triple("Alice Smith", "works_at", "Acme", wing="private")
    kg.add_triple("Alice Jones", "works_at", "Initech", wing="shared")
    assert {c["name"] for c in kg.find_entity_candidates("Alice")} == {
        "Alice Smith",
        "Alice Jones",
    }
    assert [c["name"] for c in kg.find_entity_candidates("Alice", wings=["shared"])] == [
        "Alice Jones"
    ]
    assert kg.find_entity_candidates("Alice", wings=[]) == []
    # A lone fuzzy match must not answer from a hidden wing either.
    assert kg.query_entity("Smith", wings=["shared"], direction="both") == []


def test_supersede_stays_in_its_wing_and_keeps_sensitivity(kg):
    shared = kg.add_triple("api", "uses", "sqlite", wing="shared")
    private = kg.add_triple("api", "uses", "sqlite", wing="vault", sensitivity="private")
    new = kg.supersede("api", "uses", "sqlite", "postgres", wing="vault")
    assert _row(kg, private)["superseded_by"] == new
    assert _row(kg, shared)["status"] == "active" and _row(kg, shared)["valid_to"] is None
    assert _row(kg, new)["wing"] == "vault"
    # The successor is never less sensitive than what it replaces.
    assert _row(kg, new)["sensitivity"] == "private"


def test_supersede_onto_an_open_successor_raises_its_sensitivity(kg):
    kg.add_triple("api", "uses", "sqlite", wing="w", sensitivity="private")
    successor = kg.add_triple("api", "uses", "postgres", wing="w", sensitivity="public")
    assert kg.supersede("api", "uses", "sqlite", "postgres", wing="w") == successor
    assert _row(kg, successor)["sensitivity"] == "private"


def test_invalidate_only_ends_the_fact_in_its_wing(kg):
    shared = kg.add_triple("api", "uses", "sqlite", wing="shared")
    other = kg.add_triple("api", "uses", "sqlite", wing="other")
    kg.invalidate("api", "uses", "sqlite", ended="2026-01-01", wing="shared")
    assert _row(kg, shared)["valid_to"] == "2026-01-01"
    assert _row(kg, other)["valid_to"] is None


def test_rewrite_keeps_wing_and_sensitivity(kg):
    old = kg.add_triple("api", "Uses", "sqlite", wing="w", sensitivity="private")
    new = kg.rewrite(old, "uses", "sqlite")
    assert (_row(kg, new)["wing"], _row(kg, new)["sensitivity"]) == ("w", "private")


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"sensitivity": "top"}, "sensitivity must be one of"),
        ({"wing": 3}, "wing must be a string"),
        ({"wing": "a\nb"}, "control characters"),
    ],
)
def test_bad_wing_or_sensitivity_is_refused(kg, kwargs, message):
    with pytest.raises(ValueError, match=message):
        kg.add_triple("a", "b", "c", **kwargs)
    assert kg.stats()["triples"] == 0


def test_visibility_and_strictest():
    assert visibility() == (None, None)
    assert visibility(["b", "a", "a"], []) == (["a", "b"], [])
    with pytest.raises(ValueError):
        visibility("shared")
    with pytest.raises(ValueError):
        visibility(None, ["top"])
    assert strictest(None, "public") == "internal"
    assert strictest("private", "secret", "internal") == "secret"


def test_stats_list_fact_wings(kg):
    kg.add_triple("a", "b", "c", wing="shared")
    kg.add_triple("a", "b", "d")
    assert kg.stats()["wings"] == ["shared"]


def test_a_legacy_graph_migrates_with_no_wing(tmp_path):
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
        row = _row(kg, "t_old")
        assert row["wing"] is None and row["sensitivity"] == "internal"
        indexes = {r["name"] for r in kg._conn().execute("PRAGMA index_list(triples)")}
        assert "idx_triples_wing" in indexes
        # A legacy fact matches no wing filter.
        assert kg.query_entity("Max", wings=["shared"]) == []


def test_replication_and_import_carry_wing_and_sensitivity(tmp_path):
    src = str(tmp_path / "src.sqlite3")
    with KnowledgeGraph(db_path=src) as kg:
        tid = kg.add_triple("a", "b", "c", wing="w", sensitivity="private")
        entities, triples = kg.dump_rows("entities"), kg.dump_rows("triples")
    with KnowledgeGraph(db_path=str(tmp_path / "replica.sqlite3")) as replica:
        for row in entities:
            replica.apply_row("entities", row)
        replica.apply_row("triples", triples[0])
        assert (_row(replica, tid)["wing"], _row(replica, tid)["sensitivity"]) == ("w", "private")
        replica.apply_row("triples", {**triples[0], "id": "t2", "sensitivity": None})
        assert _row(replica, "t2")["sensitivity"] == "internal"
    dst = str(tmp_path / "dst.sqlite3")
    with KnowledgeGraph(db_path=dst) as kg:
        # The same words in another wing are not the same fact.
        kg.add_triple("a", "b", "c", wing="other")
    counts = import_graph_rows(src, dst, apply=True)
    assert counts["triples"]["added"] == 1
    with KnowledgeGraph(db_path=dst) as kg:
        assert _row(kg, tid)["wing"] == "w"
