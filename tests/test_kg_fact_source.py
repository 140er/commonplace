"""Facts point back at the chat messages they came from, and carry a status."""

import sqlite3

import pytest

from mempalace.knowledge_graph import KnowledgeGraph, source_range


def _row(kg, triple_id):
    return kg._conn().execute("SELECT * FROM triples WHERE id = ?", (triple_id,)).fetchone()


def test_add_triple_stores_the_message_range(kg):
    tid = kg.add_triple(
        "Avery",
        "uses",
        "pnpm",
        source_thread="thread-41",
        source_seq_start=41,
        source_seq_end=44,
    )
    row = _row(kg, tid)
    assert (row["source_thread"], row["source_seq_start"], row["source_seq_end"]) == (
        "thread-41",
        41,
        44,
    )
    assert row["status"] == "active"
    assert row["superseded_by"] is None

    (fact,) = kg.query_entity("Avery")
    assert fact["id"] == tid
    assert fact["source_thread"] == "thread-41"
    assert (fact["source_seq_start"], fact["source_seq_end"]) == (41, 44)
    assert fact["status"] == "active"


def test_a_fact_without_a_source_still_works(kg):
    tid = kg.add_triple("Max", "loves", "chess")
    row = _row(kg, tid)
    assert row["source_thread"] is None
    assert row["source_seq_start"] is None
    assert row["status"] == "active"


def test_one_message_number_means_that_message(kg):
    tid = kg.add_triple("Max", "loves", "chess", source_thread="t", source_seq_start=7)
    row = _row(kg, tid)
    assert (row["source_seq_start"], row["source_seq_end"]) == (7, 7)


@pytest.mark.parametrize(
    "args, message",
    [
        ((None, 3, 4), "needs source_thread"),
        (("t", 5, 4), "before source_seq_start"),
        (("t", -1, 2), "must not be negative"),
        (("t", 1.5, 2), "whole message number"),
        (("t", True, 2), "whole message number"),
        (("t", "x", 2), "whole message number"),
        (("t\n1", 1, 2), "control characters"),
        (("t" * 300, 1, 2), "too long"),
        ((7, 1, 2), "must be a string"),
    ],
)
def test_bad_ranges_are_refused(kg, args, message):
    with pytest.raises(ValueError, match=message):
        source_range(*args)
    with pytest.raises(ValueError, match=message):
        kg.add_triple(
            "A", "b", "C", source_thread=args[0], source_seq_start=args[1], source_seq_end=args[2]
        )
    assert kg.stats()["triples"] == 0


def test_range_accepts_decimal_strings():
    assert source_range(" t ", "3", "5") == ("t", 3, 5)
    assert source_range("t") == ("t", None, None)
    assert source_range() == (None, None, None)


def test_supersede_marks_the_old_fact_and_links_the_new(kg):
    old = kg.add_triple("Avery", "uses", "npm", source_thread="old", source_seq_start=3)
    new = kg.supersede(
        "Avery",
        "uses",
        "npm",
        "pnpm",
        at="2026-10-01T00:00:00Z",
        source_thread="new",
        source_seq_start=10,
        source_seq_end=12,
    )
    old_row, new_row = _row(kg, old), _row(kg, new)
    assert old_row["status"] == "superseded"
    assert old_row["superseded_by"] == new
    assert old_row["valid_to"] == "2026-10-01T00:00:00Z"
    assert new_row["status"] == "active"
    assert new_row["superseded_by"] is None
    assert (new_row["source_thread"], new_row["source_seq_start"], new_row["source_seq_end"]) == (
        "new",
        10,
        12,
    )
    by_id = {f["id"]: f for f in kg.query_entity("Avery")}
    assert by_id[old]["superseded_by"] == new
    assert by_id[old]["source_thread"] == "old"


def test_supersede_onto_an_already_open_successor_links_to_it(kg):
    old = kg.add_triple("Avery", "uses", "npm")
    existing = kg.add_triple("Avery", "uses", "pnpm")
    assert kg.supersede("Avery", "uses", "npm", "pnpm") == existing
    assert _row(kg, old)["superseded_by"] == existing
    assert _row(kg, existing)["status"] == "active"


def test_supersede_with_no_open_old_fact_marks_nothing(kg):
    new = kg.supersede("Avery", "uses", "npm", "pnpm")
    assert _row(kg, new)["status"] == "active"
    statuses = kg._conn().execute("SELECT status FROM triples").fetchall()
    assert [r["status"] for r in statuses] == ["active"]


def test_supersede_with_a_bad_range_writes_nothing(kg):
    old = kg.add_triple("Avery", "uses", "npm")
    with pytest.raises(ValueError):
        kg.supersede("Avery", "uses", "npm", "pnpm", source_seq_start=3)
    row = _row(kg, old)
    assert row["valid_to"] is None
    assert row["status"] == "active"


def test_invalidate_ends_a_fact_but_leaves_its_status(kg):
    tid = kg.add_triple("Max", "has", "injury")
    kg.invalidate("Max", "has", "injury", ended="2026-01-02")
    row = _row(kg, tid)
    assert row["valid_to"] == "2026-01-02"
    assert row["status"] == "active"


def test_rewrite_keeps_the_range_and_links_old_to_new(kg):
    old = kg.add_triple(
        "Max", "Likes", "chess", source_thread="t", source_seq_start=1, source_seq_end=2
    )
    new = kg.rewrite(old, "likes", "chess", at="2026-01-01T00:00:00Z")
    new_row = _row(kg, new)
    assert (new_row["source_thread"], new_row["source_seq_start"], new_row["source_seq_end"]) == (
        "t",
        1,
        2,
    )
    assert _row(kg, old)["status"] == "superseded"
    assert _row(kg, old)["superseded_by"] == new


def test_timeline_and_relationship_reads_carry_the_new_fields(kg):
    tid = kg.add_triple("Max", "loves", "chess", source_thread="t", source_seq_start=4)
    (entry,) = kg.timeline("Max")
    assert entry["id"] == tid and entry["source_seq_end"] == 4
    (rel,) = kg.query_relationship("loves")
    assert rel["status"] == "active" and rel["source_thread"] == "t"


def _legacy_graph(path):
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


def test_a_legacy_graph_migrates_in_place(tmp_path):
    path = str(tmp_path / "legacy.sqlite3")
    _legacy_graph(path)
    with KnowledgeGraph(db_path=path) as kg:
        row = _row(kg, "t_old")
        assert row["status"] == "active"
        assert row["source_thread"] is None
        indexes = {r["name"] for r in kg._conn().execute("PRAGMA index_list(triples)")}
        assert {"idx_triples_status", "idx_triples_source_thread"} <= indexes
        new = kg.supersede("Max", "loves", "chess", "go", source_thread="t", source_seq_start=2)
        assert _row(kg, "t_old")["superseded_by"] == new
    # A second open is a no-op migration.
    with KnowledgeGraph(db_path=path) as kg:
        assert _row(kg, "t_old")["status"] == "superseded"


def test_source_backfill_retries_after_interrupted_first_open(tmp_path, monkeypatch):
    from mempalace import fact_index

    path = str(tmp_path / "interrupted.sqlite3")
    _legacy_graph(path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE triples SET source_file = 'source.txt' WHERE id = 't_old'")

    def crash_before_backfill(conn):
        assert conn.execute("SELECT count(*) FROM fact_sources").fetchone()[0] == 0
        conn.commit()
        conn.close()
        raise RuntimeError("interrupted upgrade")

    with monkeypatch.context() as patch:
        patch.setattr(fact_index, "try_install", crash_before_backfill)
        with pytest.raises(RuntimeError, match="interrupted upgrade"):
            KnowledgeGraph(db_path=path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM fact_sources").fetchone()[0] == 0
    with KnowledgeGraph(db_path=path) as kg:
        sources = kg.sources(["t_old"])["t_old"]
        assert len(sources) == 1 and sources[0]["source_file"] == "source.txt"
        assert _row(kg, "t_old")["source_file"] == "source.txt"
    with KnowledgeGraph(db_path=path) as kg:
        assert kg.sources(["t_old"])["t_old"] == sources


def test_replication_carries_status_and_range(tmp_path):
    with KnowledgeGraph(db_path=str(tmp_path / "a.sqlite3")) as source:
        old = source.add_triple("Max", "loves", "chess", source_thread="t", source_seq_start=1)
        new = source.supersede("Max", "loves", "chess", "go")
        entities = source.dump_rows("entities")
        triples = source.dump_rows("triples")
    with KnowledgeGraph(db_path=str(tmp_path / "b.sqlite3")) as replica:
        for row in entities:
            replica.apply_row("entities", row)
        for row in triples:
            report = replica.apply_row("triples", row)
            if row["id"] == old:
                assert report["unresolved_links"] == [{"id": old, "superseded_by": new}]
                assert _row(replica, old)["superseded_by"] is None
        # A later pass retries links after their successor rows have arrived.
        replica.apply_row("triples", triples[0])
        assert _row(replica, old)["superseded_by"] == new
        assert _row(replica, old)["source_thread"] == "t"
        # A row from an older replica has no status: it lands as a live fact.
        replica.apply_row("triples", {**triples[1], "id": "t_legacy", "status": None})
        assert _row(replica, "t_legacy")["status"] == "active"


def test_import_copies_status_and_range(tmp_path):
    from mempalace.knowledge_graph import import_graph_rows

    src, dst = str(tmp_path / "src.sqlite3"), str(tmp_path / "dst.sqlite3")
    with KnowledgeGraph(db_path=src) as kg:
        old = kg.add_triple("Max", "loves", "chess", source_thread="t", source_seq_start=5)
        new = kg.supersede("Max", "loves", "chess", "go")
    import_graph_rows(src, dst, apply=True)
    with KnowledgeGraph(db_path=dst) as kg:
        assert _row(kg, old)["superseded_by"] == new
        assert _row(kg, old)["source_seq_start"] == 5


def test_an_older_peer_cannot_erase_a_supersede_link(tmp_path):
    with KnowledgeGraph(db_path=str(tmp_path / "r.sqlite3")) as kg:
        old = kg.add_triple("Max", "loves", "chess", source_thread="t", source_seq_start=1)
        new = kg.supersede("Max", "loves", "chess", "go")
        legacy_row = {
            key: value
            for key, value in kg.dump_rows("triples")[0].items()
            if key
            not in (
                "source_thread",
                "source_seq_start",
                "source_seq_end",
                "status",
                "superseded_by",
            )
        }
        assert legacy_row["id"] == old
        kg.apply_row("triples", legacy_row)
        row = _row(kg, old)
        assert row["status"] == "superseded" and row["superseded_by"] == new
        assert row["source_thread"] == "t"


def test_supersede_onto_the_same_value_links_the_new_row(kg):
    old = kg.add_triple("Max", "loves", "chess")
    new = kg.supersede("Max", "loves", "chess", "chess", at="2026-01-01T00:00:00Z")
    assert new != old
    assert _row(kg, old)["superseded_by"] == new
    assert _row(kg, new)["status"] == "active"


def test_whole_floats_count_as_message_numbers():
    assert source_range("t", 3.0, 4.0) == ("t", 3, 4)
