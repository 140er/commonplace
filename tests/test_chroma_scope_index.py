"""The optional scope index preserves Chroma writes and recovery semantics."""

import sqlite3
from unittest.mock import patch

import pytest

from mempalace.backends._inproc_sqlite import open_writer
from mempalace.backends.base import PalaceRef
from mempalace.backends.chroma import ChromaBackend
from mempalace.backends.chroma_sqlite import SCOPE_INDEX, _lexical_scope_sql, ensure_scope_index
from mempalace.backends.scoped_lexical import lexical_reader


def _index_exists(path):
    with lexical_reader(path / "chroma.sqlite3") as conn:
        return bool(
            conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (SCOPE_INDEX,)).fetchone()
        )


def _drop_index(path):
    with open_writer(path / "chroma.sqlite3") as conn:
        conn.execute(f"DROP INDEX {SCOPE_INDEX}")


def _seed(path):
    backend = ChromaBackend()
    col = backend.get_collection(str(path), "mempalace_drawers", create=True)
    col.add(
        ids=["a", "b"],
        documents=["quartz migration", "routine note"],
        metadatas=[{"wing": "public", "room": "notes", "source_file": f"{i}.md"} for i in range(2)],
    )
    return backend, col


def test_scope_index_is_partial_atomic_and_idempotent():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE embedding_metadata(id INTEGER,key TEXT,string_value TEXT)")
    trace = []
    conn.set_trace_callback(trace.append)
    assert ensure_scope_index(conn)
    assert not ensure_scope_index(conn)
    assert sum("CREATE INDEX" in sql for sql in trace) == 1
    begin = trace.index("BEGIN IMMEDIATE")
    assert "CREATE INDEX" in trace[begin + 1]
    assert trace[begin + 2] == "COMMIT"
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (SCOPE_INDEX,)).fetchone()[0]
    assert "(key,string_value,id)" in sql
    assert "WHERE key IN ('wing','room','source_file')" in sql
    conn.close()


def test_client_writes_queries_and_reopens_with_scope_index(tmp_path):
    backend, col = _seed(tmp_path)
    assert _index_exists(tmp_path)
    col.upsert(
        ids=["a", "c"],
        documents=["quartz ZXQ739251", "quartz deployment"],
        metadatas=[{"wing": "public", "room": "logs"}] * 2,
    )
    col.delete(ids=["b"])
    assert set(col.query(query_texts=["quartz"], n_results=2).ids[0]) == {"a", "c"}
    backend.close()
    backend = ChromaBackend()
    try:
        col = backend.get_collection(str(tmp_path), "mempalace_drawers")
        assert set(col.get().ids) == {"a", "c"}
        assert [h.id for h in col.union_lexical_search(query="ZXQ739251").hits] == ["a"]
    finally:
        backend.close()


def test_read_only_without_index_has_identical_hits_and_both_plans_are_indexed(tmp_path):
    backend, col = _seed(tmp_path)
    expected = col.union_lexical_search(query="quartz", where={"wing": "public"})
    with lexical_reader(tmp_path / "chroma.sqlite3") as conn:
        sql, params = _lexical_scope_sql("mempalace_drawers", [("wing", ("public",))], conn)
        plan = " ".join(str(row) for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params))
        assert "COVERING INDEX " + SCOPE_INDEX in plan
    backend.close()
    _drop_index(tmp_path)
    backend = ChromaBackend()
    try:
        col = backend.get_collection(
            palace=PalaceRef(id=str(tmp_path), local_path=str(tmp_path)),
            collection_name="mempalace_drawers",
            options={"read_only": True},
        )
        assert not _index_exists(tmp_path)
        actual = col.union_lexical_search(query="quartz", where={"wing": "public"})
        assert actual == expected
        with lexical_reader(tmp_path / "chroma.sqlite3") as conn:
            sql, params = _lexical_scope_sql("mempalace_drawers", [("wing", ("public",))], conn)
            plan = " ".join(str(row) for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params))
            assert SCOPE_INDEX not in plan
            assert "INDEX embedding_metadata_string_value" in plan
            fts_plan = " ".join(
                str(row)
                for row in conn.execute(
                    "EXPLAIN QUERY PLAN SELECT rowid FROM embedding_fulltext_search "
                    f"WHERE embedding_fulltext_search MATCH ? AND EXISTS(SELECT 1 FROM ({sql}) s "
                    "WHERE s.id=embedding_fulltext_search.rowid) ORDER BY rowid DESC LIMIT ?",
                    ('"quartz"', *params, 1001),
                )
            )
            assert "VIRTUAL TABLE INDEX" in fts_plan
    finally:
        backend.close()
    backend = ChromaBackend()
    try:
        backend.get_collection(str(tmp_path), "mempalace_drawers")
        assert _index_exists(tmp_path)
    finally:
        backend.close()


@pytest.mark.parametrize("operation", ["vacuum", "rebuild", "migrate"])
def test_repair_and_migration_recreate_scope_index(tmp_path, operation):
    from mempalace.migrate import migrate
    from mempalace.repair import _vacuum_and_rebuild_fts5, rebuild_from_sqlite

    palace = tmp_path / "palace"
    backend, _ = _seed(palace)
    backend.close()
    _drop_index(palace)
    assert not _index_exists(palace)
    if operation == "vacuum":
        _vacuum_and_rebuild_fts5(str(palace), strict=True)
    elif operation == "rebuild":
        dest = tmp_path / "recovered"
        assert rebuild_from_sqlite(str(palace), str(dest))["mempalace_drawers"] == 2
        palace = dest
    else:
        with patch("mempalace.migrate.collection_write_roundtrip_works", return_value=False):
            assert migrate(str(palace), confirm=True)
    assert _index_exists(palace)
    backend = ChromaBackend()
    try:
        col = backend.get_collection(str(palace), "mempalace_drawers")
        assert set(col.get().ids) == {"a", "b"}
        assert "a" in col.query(query_texts=["quartz"], n_results=2).ids[0]
    finally:
        backend.close()
