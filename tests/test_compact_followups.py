"""Crash safety, snapshot consistency and warm-cache regressions for compact."""

import sqlite3
import subprocess
import sys

import numpy as np
import pytest

from mempalace.backends import BackendError
from mempalace.backends._filters import matches_where
from mempalace.backends.compact import (
    _SCHEMA_VERSION,
    KEYWORD_ONLY,
    CompactBackend,
)


@pytest.fixture
def store(tmp_path):
    backend = CompactBackend()
    col = backend.get_collection(str(tmp_path), "records", create=True)
    yield backend, col, tmp_path
    backend.close()


def _query(col, where=None):
    return col.query(query_embeddings=[[1, 0]], where=where, n_results=100, include=[]).ids[0]


def _assert_cache_matches_disk(col):
    cache = col._handle._vector_cache[1]
    rows = col._handle.conn.execute(
        "SELECT rowid,embedding FROM vectors WHERE collection_id=1"
    ).fetchall()
    assert len(rows) == cache.size == np.count_nonzero(cache.rowid_index >= 0)
    for rowid, blob in rows:
        pos = cache.positions([rowid])[0]
        assert cache.rowids[pos] == rowid
        assert cache.matrix[pos].tobytes() == blob
        assert cache.norms[pos] == pytest.approx(np.linalg.norm(np.frombuffer(blob, np.int8)))


def test_incremental_cache_all_writes_and_rollback(store, monkeypatch):
    _, col, _ = store
    col.add(
        ids=["a", "b"],
        documents=["alpha"] * 2,
        embeddings=[[1, 0], [0, 1]],
        metadatas=[{"wing": "w", "room": "r"}] * 2,
    )
    _query(col)
    cache = col._handle._vector_cache[1]
    matrix = cache.matrix
    col.add(ids=["c"], documents=["alpha"], embeddings=[[1, 1]], metadatas=[{"wing": "x"}])
    col.upsert(ids=["a"], documents=["changed"], embeddings=[[-1, 0]], metadatas=[{"wing": "x"}])
    col.update(ids=["b"], embeddings=[[1, 0]], metadatas=[{"wing": "x", "room": "s"}])
    assert _query(col, {"wing": "x"}) == ["b", "c", "a"]
    assert col._handle._vector_cache[1] is cache
    assert cache.matrix is matrix  # no full-matrix copy for a normal turn
    _assert_cache_matches_disk(col)
    col.update(ids=["b"], metadatas=[{KEYWORD_ONLY: True}])
    col.upsert(ids=["c"], documents=["keyword"], metadatas=[{KEYWORD_ONLY: True}])
    col.delete(ids=["a"])
    assert _query(col) == []
    assert col._handle._vector_cache[1] is cache
    _assert_cache_matches_disk(col)
    col.update(ids=["b"], metadatas=[{KEYWORD_ONLY: False}], embeddings=[[1, 0]])
    assert _query(col) == ["b"]
    _assert_cache_matches_disk(col)
    before = cache.matrix.copy(), cache.rowids.copy(), cache.size
    original = col._record_pending_identity

    def fail(cur):
        original(cur)
        raise RuntimeError("injected rollback")

    monkeypatch.setattr(col, "_record_pending_identity", fail)
    with pytest.raises(RuntimeError, match="rollback"):
        col.upsert(ids=["b"], documents=["lost"], embeddings=[[-1, 0]])
    assert cache.size == before[2]
    assert np.array_equal(cache.matrix, before[0])
    assert np.array_equal(cache.rowids, before[1])
    assert col.get(ids=["b"]).documents == ["alpha"]
    assert _query(col) == ["b"]
    _assert_cache_matches_disk(col)


def test_incremental_cache_capacity_deletes_and_multiple_collections(store):
    backend, col, directory = store
    _query(col)  # dimensionless, empty cache
    col.add(ids=[str(i) for i in range(40)], documents=["alpha"] * 40, embeddings=[[1, 0]] * 40)
    _query(col)
    cache = col._handle._vector_cache[1]
    for i in range(40, 100):
        col.add(ids=[str(i)], documents=["alpha"], embeddings=[[1, 0]])
    assert _query(col) == [str(i) for i in range(100)]
    assert col._handle._vector_cache[1] is cache
    col.delete(ids=["1", "30", "99"])
    _assert_cache_matches_disk(col)
    other = backend.get_collection(str(directory), "other", create=True)
    # Creating a collection outside the compact cursor invalidates conservatively.
    _query(col)
    cache = col._handle._vector_cache[1]
    other.add(ids=["other"], documents=["beta"], embeddings=[[0, 1]])
    assert "other" not in _query(col)
    assert col._handle._vector_cache[1] is cache
    _assert_cache_matches_disk(col)


def test_external_process_write_reloads_cache(store):
    backend, col, directory = store
    col.add(ids=["a"], documents=["alpha"], embeddings=[[1, 0]], metadatas=[{"wing": "w"}])
    reader = backend.get_collection(str(directory), "records", options={"read_only": True})
    assert _query(reader, {"wing": "w"}) == ["a"]
    cache = col._handle._vector_cache[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from mempalace.backends.compact import CompactBackend
b = CompactBackend()
c = b.get_collection(sys.argv[1], 'records')
c.update(ids=['a'], metadatas=[{'wing':'x'}], embeddings=[[0,1]])
c.add(ids=['b'], documents=['beta'], embeddings=[[1,0]], metadatas=[{'wing':'x'}])
b.close()
""",
            str(directory),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert _query(reader, {"wing": "w"}) == []
    assert _query(reader, {"wing": "x"}) == ["b", "a"]
    assert col._handle._vector_cache[1] is not cache
    _assert_cache_matches_disk(reader)


@pytest.mark.parametrize(
    "marker",
    [
        "CREATE TABLE IF NOT EXISTS dictionaries",
        "CREATE TABLE IF NOT EXISTS vectors",
        "PRAGMA user_version=2",
    ],
)
def test_schema_creation_process_crash_is_atomic(tmp_path, marker):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sqlite3, sys
from mempalace.backends.compact import CompactBackend
conn=sqlite3.connect(sys.argv[1])
conn.set_trace_callback(lambda sql: os._exit(73) if sys.argv[2] in sql else None)
CompactBackend()._init_schema(conn)
""",
            str(tmp_path / "compact.sqlite3"),
            marker,
        ],
        timeout=30,
    )
    assert result.returncode == 73
    conn = sqlite3.connect(tmp_path / "compact.sqlite3")
    assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    assert conn.execute("PRAGMA application_id").fetchone()[0] == 0
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    conn.close()
    with_backend = CompactBackend()
    try:
        col = with_backend.get_collection(str(tmp_path), "records", create=True)
        assert col.count() == 0
        assert col._handle.conn.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION == 2
    finally:
        with_backend.close()


@pytest.mark.parametrize("populated", [False, True])
def test_partial_schema_refused_and_empty_repaired_through_rebuild(store, populated, monkeypatch):
    backend, col, directory = store
    if populated:
        col.add(ids=["a"], documents=["alpha"], embeddings=[[1, 0]])
    backend.close()
    conn = sqlite3.connect(directory / "compact.sqlite3")
    conn.execute("DROP TABLE dictionaries")
    conn.commit()
    before = list(conn.execute("SELECT * FROM documents"))
    conn.close()
    other = CompactBackend()
    try:
        for read_only in [False, True]:
            with pytest.raises(
                BackendError, match="incomplete compact schema.*repair rebuild-index"
            ):
                other.get_collection(str(directory), "records", options={"read_only": read_only})
        from mempalace import repair

        monkeypatch.setattr("mempalace.palace.get_backend_for_palace", lambda path: other)
        if populated:
            with pytest.raises(BackendError, match="containing documents"):
                repair.rebuild_index(
                    str(directory), collection_name="records", progress=lambda _: None
                )
        else:
            repair.rebuild_index(str(directory), collection_name="records", progress=lambda _: None)
            assert other.get_collection(str(directory), "records").count() == 0
        conn = sqlite3.connect(directory / "compact.sqlite3")
        assert list(conn.execute("SELECT * FROM documents")) == before
        conn.close()
    finally:
        other.close()


def test_version_one_upgrades_in_place_without_losing_drawers(store):
    backend, col, directory = store
    col.add(ids=["mcp-only"], documents=["original words"], embeddings=[[1, 0]])
    before = list(col._handle.conn.execute("SELECT * FROM documents"))
    backend.close()
    conn = sqlite3.connect(directory / "compact.sqlite3")
    conn.execute("DROP INDEX compact_room")
    conn.execute("PRAGMA user_version=1")
    conn.close()
    other = CompactBackend()
    try:
        with pytest.raises(BackendError, match="writable once to upgrade in place"):
            other.get_collection(str(directory), "records", options={"read_only": True})
        col = other.get_collection(str(directory), "records")
        assert list(col._handle.conn.execute("SELECT * FROM documents")) == before
        assert col._handle.conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert col.get().documents == ["original words"]
    finally:
        other.close()


@pytest.mark.parametrize("mutation", ["delete", "update", "add"])
def test_reindex_mutation_aborts_and_retains_vectors(store, mutation):
    _, col, directory = store
    col.add(ids=["a", "b"], documents=["alpha", "beta"], embeddings=[[1, 0], [0, 1]])
    called = False

    def embed(documents, metadatas):
        nonlocal called
        if not called:
            called = True
            if mutation == "delete":
                col.delete(ids=["a"])
            elif mutation == "update":
                col.update(ids=["a"], documents=["changed"])
            else:
                col.add(ids=["c"], documents=["gamma"], embeddings=[[1, 1]])
        return [[1, 0, 0]] * len(documents)

    with pytest.raises(BackendError, match="changed during staging"):
        col.reindex(embed, batch_size=1)
    assert not list(directory.glob("compact-reindex-*"))
    assert {r[0] for r in col._handle.conn.execute("SELECT DISTINCT dim FROM vectors")} == {2}
    assert not col._handle.conn.execute(
        "SELECT 1 FROM vectors v LEFT JOIN documents d USING(rowid) WHERE d.rowid IS NULL"
    ).fetchone()
    _query(col)


def test_reindex_cleanup_after_process_crash_and_active_stage(store):
    backend, col, directory = store
    col.add(ids=["a"], documents=["alpha"], embeddings=[[1, 0]])
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sys
from mempalace.backends.compact import CompactBackend
b=CompactBackend()
c=b.get_collection(sys.argv[1], 'records')
c.reindex(lambda docs, metas: os._exit(73))
""",
            str(directory),
        ],
        timeout=30,
    )
    assert result.returncode == 73
    assert list(directory.glob("compact-reindex-*"))
    backend.close_palace(str(directory))
    col = backend.get_collection(str(directory), "records")
    assert not list(directory.glob("compact-reindex-*"))
    stale = directory / "compact-reindex-stale"
    stale.mkdir()
    (stale / "vectors.sqlite3").write_bytes(b"interrupted")

    def embed(docs, metas):
        active = list(directory.glob("compact-reindex-*"))
        assert len(active) == 1
        other = CompactBackend()
        try:
            other.get_collection(str(directory), "records")
            assert active[0].exists()
        finally:
            other.close()
        return [[1, 0, 0]] * len(docs)

    col.reindex(embed)
    assert not list(directory.glob("compact-reindex-*"))
    assert col.query(query_embeddings=[[1, 0, 0]]).ids == [["a"]]
    assert {r[0] for r in col._handle.conn.execute("SELECT DISTINCT dim FROM vectors")} == {3}


def _plan_for_get(col, where):
    statements = []
    conn = col._handle.conn
    conn.set_trace_callback(statements.append)
    try:
        col.get(where=where, include=[])
    finally:
        conn.set_trace_callback(None)
    sql = next(s for s in statements if s.startswith("SELECT d.rowid,d.id FROM documents"))
    return sql, [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql)]


def test_bracket_values_and_bare_parent_lists_match_oracle_and_use_indexes(store):
    _, col, _ = store
    metas = [
        {"wing": '["w"]', "parent_drawer_id": '["p"]'},
        {"wing": ["w"], "parent_drawer_id": ["p"]},
        {"wing": '{"k":1}', "parent_entry_id": '{"k":1}'},
        {"wing": {"k": 1}, "parent_entry_id": {"k": 1}},
        {"wing": "w", "parent_entry_id": "p"},
        {},
    ]
    col.add(
        ids=list(map(str, range(len(metas)))),
        documents=["alpha"] * len(metas),
        metadatas=metas,
        embeddings=[[1, 0]] * len(metas),
    )
    for value in ['["w"]', '{"k":1}', " [literal", "{literal"]:
        for op in ["$eq", "$ne", "$in", "$nin"]:
            where = {"wing": {op: [value, None] if op in {"$in", "$nin"} else value}}
            assert col.get(where=where, include=[]).ids == [
                str(i) for i, m in enumerate(metas) if matches_where(m, where)
            ]
        _, plan = _plan_for_get(col, {"wing": value})
        assert any(
            index in p
            for p in plan
            for index in ("idx_documents_coll_wing_room_hall", "idx_documents_coll_wing_filed_at")
        ), plan
        assert not any(p.startswith(("SCAN d", "SCAN documents")) for p in plan), plan
    where = {"$or": [{"parent_drawer_id": '["p"]'}, {"parent_entry_id": '{"k":1}'}]}
    _, plan = _plan_for_get(col, where)
    for index in ["compact_parent_drawer", "compact_parent_entry"]:
        assert any(index in p and "SEARCH" in p for p in plan), plan
    # Bare lists are equality, never implicit membership in scalar parent ids.
    where = {"$or": [{"parent_drawer_id": ["p"]}, {"parent_entry_id": ["p"]}]}
    assert col.get(where=where, include=[]).ids == ["1"]
    sql, plan = _plan_for_get(col, where)
    assert "INDEXED BY compact_parent" not in sql
    assert not any("SCAN" in p and "compact_parent" in p for p in plan), plan


def test_broad_code_masks_and_selective_sql_have_filter_parity(store, monkeypatch):
    from mempalace.backends.compact import _VectorCache

    _, col, _ = store
    metas = [{"wing": "w" if i < 80 else "x", "room": "r" if i < 80 else "s"} for i in range(100)]
    metas += [{"wing": '["w"]', "room": None}, {"wing": ["w"]}, {"wing": True, "room": "r\0"}]
    col.add(
        ids=list(map(str, range(len(metas)))),
        documents=["alpha"] * len(metas),
        metadatas=metas,
        embeddings=[[1, 0]] * len(metas),
    )
    filters = [
        {"wing": "w"},
        {"room": "r"},
        {"wing": {"$in": ["w", "x"]}},
        {"$and": [{"wing": "w"}, {"room": "r"}]},
        {"$or": [{"wing": "w"}, {"room": "s"}]},
        {"wing": '["w"]'},
        {"wing": True},
        {"room": None},
        {"room": "r\0"},
        {"wing": {"$in": [None, "w"]}},
        {"$and": []},
        {"$or": []},
    ]
    original = _VectorCache.filter
    for sql_only in [False, True]:
        with monkeypatch.context() as patch:
            if sql_only:
                patch.setattr(_VectorCache, "filter", lambda *_: None)
            for where in filters:
                assert (
                    _query(col, where)
                    == [str(i) for i, m in enumerate(metas) if matches_where(m, where)][:100]
                )
    assert _VectorCache.filter is original
    statements = []
    col._handle.conn.set_trace_callback(statements.append)
    _query(col, {"wing": "w"})
    _query(col, {"room": "r"})
    col._handle.conn.set_trace_callback(None)
    assert not any(s.startswith("SELECT d.rowid FROM documents") for s in statements)
    _, plan = _plan_for_get(col, {"room": "s"})
    assert any("compact_room" in p and "SEARCH" in p for p in plan), plan


def test_lexical_snapshot_with_writer_process_between_selection_and_hydration(store, monkeypatch):
    _, col, directory = store
    col.add(ids=["a"], documents=["alpha"], metadatas=[{"wing": "w"}], embeddings=[[1, 0]])
    code = """
import sys
from mempalace.backends.compact import CompactBackend
b=CompactBackend()
c=b.get_collection(sys.argv[1], 'records')
print('ready', flush=True)
for command in sys.stdin:
    c.update(ids=['a'], metadatas=[{'wing':command.strip()}])
    print('done', flush=True)
b.close()
"""
    writer = subprocess.Popen(
        [sys.executable, "-u", "-c", code, str(directory)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    original = col._hydrate_rowids
    try:
        assert writer.stdout.readline().strip() == "ready"

        def set_wing(value):
            writer.stdin.write(value + "\n")
            writer.stdin.flush()
            assert writer.stdout.readline().strip() == "done"

        def hydrate(*args, **kwargs):
            set_wing("x")  # committed by another process after FTS selected w
            return original(*args, **kwargs)

        monkeypatch.setattr(col, "_hydrate_rowids", hydrate)
        for _ in range(30):
            set_wing("w")
            hits = col.lexical_search(query="alpha", where={"wing": "w"}).hits
            assert len(hits) == 1
            assert hits[0].metadata["wing"] == "w"
        assert not col._handle.conn.in_transaction
    finally:
        writer.stdin.close()
        writer.wait(timeout=30)
        if writer.poll() is None:
            writer.kill()
        assert writer.returncode == 0, writer.stderr.read()


def test_lexical_defensively_rechecks_where(store, monkeypatch):
    _, col, _ = store
    col.add(ids=["a"], documents=["alpha"], metadatas=[{"wing": "w"}], embeddings=[[1, 0]])
    original = col._hydrate_rowids

    def corrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        for item in result.values():
            item.metadatas[0]["wing"] = "x"
        return result

    monkeypatch.setattr(col, "_hydrate_rowids", corrupt)
    assert not col.lexical_search(query="alpha", where={"wing": "w"}).hits


def test_reindex_verifies_swapped_dimensions_and_rolls_back(store, monkeypatch):
    from mempalace.backends.compact import _quantize

    _, col, _ = store
    col.add(ids=["a", "b"], documents=["alpha", "beta"], embeddings=[[1, 0], [0, 1]])
    before = list(col._handle.conn.execute("SELECT * FROM vectors ORDER BY rowid"))
    original = col._put_vector

    def wrong_dimension(cur, rowid, cid, blob, scale, dim):
        # Simulate an incomplete swap that leaves one old-dimension vector.
        if rowid == 1:
            blob, scale, dim = _quantize([1, 0])
        original(cur, rowid, cid, blob, scale, dim)

    monkeypatch.setattr(col, "_put_vector", wrong_dimension)
    with pytest.raises(BackendError, match="vector verification failed"):
        col.reindex(lambda docs, metas: [[1, 0, 0]] * len(docs))
    assert list(col._handle.conn.execute("SELECT * FROM vectors ORDER BY rowid")) == before
    assert _query(col) == ["a", "b"]


def test_partial_batch_rollback_never_changes_warm_cache(store):
    _, col, _ = store
    col.add(ids=["a"], documents=["alpha"], embeddings=[[1, 0]])
    _query(col)
    cache = col._handle._vector_cache[1]
    before = cache.matrix.copy(), cache.size
    with pytest.raises(sqlite3.IntegrityError):
        col.add(ids=["b", "a"], documents=["beta", "collision"], embeddings=[[0, 1], [-1, 0]])
    assert cache.size == before[1]
    assert np.array_equal(cache.matrix, before[0])
    assert col.get(include=[]).ids == ["a"]
    assert _query(col) == ["a"]


def test_read_only_search_shares_patched_cache_across_backend_instances(store):
    _, writer, directory = store
    writer.add(
        ids=["a", "b"],
        documents=["alpha", "beta"],
        embeddings=[[1, 0], [0, 1]],
        metadatas=[{"wing": "w"}] * 2,
    )
    backend = CompactBackend()
    try:
        reader = backend.get_collection(str(directory), "records", options={"read_only": True})
        assert _query(reader) == ["a", "b"]
        cache = reader._handle._vector_cache[1]
        writer.update(ids=["a"], embeddings=[[-1, 0]], metadatas=[{"wing": "x"}])
        writer.add(ids=["c"], documents=["gamma"], embeddings=[[1, 0]], metadatas=[{"wing": "x"}])
        statements = []
        reader._handle.conn.set_trace_callback(statements.append)
        assert _query(reader, {"wing": "x"}) == ["c", "a"]
        reader._handle.conn.set_trace_callback(None)
        assert reader._handle._vector_cache[1] is cache is writer._handle._vector_cache[1]
        assert cache.rowid_index.dtype == np.int32
        assert not any(sql.startswith("SELECT v.rowid,v.embedding") for sql in statements)
        _assert_cache_matches_disk(reader)
    finally:
        backend.close()


def test_noop_writes_and_analyze_preserve_generation_and_reader_cache(store):
    backend, writer, directory = store
    writer.add(ids=["a"], documents=["alpha"], embeddings=[[1, 0]])
    reader = backend.get_collection(str(directory), "records", options={"read_only": True})
    _query(reader)
    cache = reader._handle._vector_cache[1]
    generation = writer._generation(writer._handle.conn.cursor())
    writer.update(ids=["missing"], metadatas=[{"wing": "none"}])
    writer.run_maintenance("analyze")
    assert writer._generation(writer._handle.conn.cursor()) == generation
    assert _query(reader) == ["a"]
    assert reader._handle._vector_cache[1] is cache


def test_postcommit_cache_failure_does_not_report_a_committed_write_as_failed(
    store, monkeypatch, caplog
):
    _, col, _ = store
    col.add(ids=["a"], documents=["alpha"], embeddings=[[1, 0]])
    _query(col)

    def fail(*args):
        raise RuntimeError("injected cache patch failure")

    monkeypatch.setattr(col, "_patch_cache", fail)
    col.add(ids=["b"], documents=["beta"], embeddings=[[0, 1]])
    assert "Discarding compact cache after a committed write" in caplog.text
    assert not col._handle._vector_cache
    assert col.get(ids=["b"]).documents == ["beta"]
    assert _query(col) == ["a", "b"]


def test_reindex_cleanup_is_best_effort_and_preserves_original_error(store, monkeypatch, caplog):
    from mempalace.backends import compact as module

    _, col, directory = store
    col.add(ids=["a"], documents=["alpha"], embeddings=[[1, 0]])
    stale = directory / "compact-reindex-stale"
    stale.mkdir()
    original = module.shutil.rmtree

    def denied(path, *args, **kwargs):
        if "compact-reindex-" in str(path):
            raise PermissionError("injected permission failure")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(module.shutil, "rmtree", denied)
        other = CompactBackend()
        try:
            assert other.get_collection(str(directory), "records").count() == 1
        finally:
            other.close()

        def embed(*args):
            raise ValueError("original embedding failure")

        with pytest.raises(ValueError, match="original embedding failure"):
            col.reindex(embed)
    assert "Could not remove an abandoned compact reindex stage" in caplog.text
    assert col.get().documents == ["alpha"]


def test_relative_palace_open_does_not_remove_live_reindex_stage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    backend = CompactBackend()
    try:
        col = backend.get_collection("relative", "records", create=True)
        col.add(ids=["a"], documents=["alpha"], embeddings=[[1, 0]])

        def embed(documents, metadatas):
            stages = list((tmp_path / "relative").glob("compact-reindex-*"))
            assert len(stages) == 1
            other = CompactBackend()
            try:
                other.get_collection("relative", "records")
                assert stages[0].exists()
            finally:
                other.close()
            return [[1, 0, 0]] * len(documents)

        col.reindex(embed)
        assert col.query(query_embeddings=[[1, 0, 0]]).ids == [["a"]]
    finally:
        backend.close()


def test_reindex_checks_each_batch_but_ignores_other_collection_writes(store):
    backend, col, directory = store
    col.add(ids=["a", "b", "c"], documents=["alpha"] * 3, embeddings=[[1, 0]] * 3)
    other = backend.get_collection(str(directory), "other", create=True)
    calls = []

    def unrelated(documents, metadatas):
        calls.append(1)
        other.upsert(ids=["x"], documents=["changed"], embeddings=[[1, 0]])
        return [[1, 0, 0]] * len(documents)

    col.reindex(unrelated, batch_size=1)
    assert len(calls) == 3
    assert col.query(query_embeddings=[[1, 0, 0]]).ids == [["a", "b", "c"]]
    assert other.query(query_embeddings=[[1, 0]]).ids == [["x"]]
    calls.clear()

    def changed(documents, metadatas):
        calls.append(1)
        col.update(ids=["c"], documents=["new words"])
        return [[1, 0, 0]] * len(documents)

    with pytest.raises(BackendError, match="changed during staging"):
        col.reindex(changed, batch_size=1)
    assert len(calls) == 1


@pytest.mark.parametrize("repair", [False, True])
def test_populated_schema_restores_derived_indexes_and_triggers(store, repair):
    backend, col, directory = store
    col.add(ids=["a"], documents=["original words"], embeddings=[[1, 0]], metadatas=[{"room": "r"}])
    before = [tuple(row) for row in col._handle.conn.execute("SELECT * FROM documents")]
    backend.close()
    conn = sqlite3.connect(directory / "compact.sqlite3")
    conn.executescript("DROP INDEX compact_room; DROP TRIGGER delete_vector;")
    conn.close()
    other = CompactBackend()
    try:
        if repair:
            assert other.repair_empty_schema(str(directory))
        col = other.get_collection(str(directory), "records")
        assert [tuple(row) for row in col._handle.conn.execute("SELECT * FROM documents")] == before
        assert col.get(where={"room": "r"}).documents == ["original words"]
        col.delete(ids=["a"])
        assert not col._handle.conn.execute("SELECT 1 FROM vectors").fetchone()
    finally:
        other.close()


def test_repair_missing_file_does_not_create_database(tmp_path):
    backend = CompactBackend()
    try:
        with pytest.raises(sqlite3.OperationalError):
            backend.repair_empty_schema(str(tmp_path))
        assert not (tmp_path / "compact.sqlite3").exists()
    finally:
        backend.close()


def test_format_stamp_is_durable_before_wal_switch(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sqlite3, sys
from mempalace.backends.compact import CompactBackend
conn=sqlite3.connect(sys.argv[1])
conn.set_trace_callback(lambda sql: os._exit(73) if sql == 'PRAGMA journal_mode=WAL' else None)
CompactBackend()._init_schema(conn)
""",
            str(tmp_path / "compact.sqlite3"),
        ],
        timeout=30,
    )
    assert result.returncode == 73
    assert CompactBackend.detect(str(tmp_path))
    backend = CompactBackend()
    try:
        assert backend.get_collection(str(tmp_path), "records", create=True).count() == 0
    finally:
        backend.close()


@pytest.mark.parametrize("operation", ["get", "lexical", "facets", "count"])
def test_immutable_read_race_does_not_leak_internal_exception(store, monkeypatch, operation):
    backend, col, directory = store
    col.add(ids=["a"], documents=["alpha"], metadatas=[{"wing": "w"}], embeddings=[[1, 0]])
    backend.close_palace(str(directory))
    readonly = CompactBackend()
    reader = readonly.get_collection(str(directory), "records", options={"read_only": True})
    assert reader._handle.immutable
    changed = False

    def commit_writer():
        nonlocal changed
        if changed:
            return
        changed = True
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import sys
from mempalace.backends.compact import CompactBackend
b=CompactBackend()
c=b.get_collection(sys.argv[1], 'records')
c.update(ids=['a'], documents=['beta'], metadatas=[{'wing':'x'}])
b.close()
""",
                str(directory),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr

    try:
        if operation in {"facets", "count"}:
            conn = reader._handle.conn
            factory = conn.row_factory

            def row_factory(cur, row):
                if any(field[0].upper() == "COUNT(*)" for field in cur.description):
                    commit_writer()
                return factory(cur, row)

            conn.row_factory = row_factory
            try:
                assert (
                    reader.facet_counts("wing") if operation == "facets" else reader.count()
                ) == ({"w": 1} if operation == "facets" else 1)
            finally:
                conn.row_factory = factory
        else:
            hook = "_result" if operation == "get" else "_hydrate_rowids"
            original = getattr(reader, hook)

            def after_selection(*args, **kwargs):
                commit_writer()
                return original(*args, **kwargs)

            monkeypatch.setattr(reader, hook, after_selection)
            if operation == "get":
                assert reader.get(where={"wing": "w"}).documents == ["alpha"]
            else:
                # The changed immutable view is rejected; the retry sees the
                # committed replacement, which no longer matches alpha/w.
                assert reader.lexical_search(query="alpha", where={"wing": "w"}).hits == []
        assert changed
        assert reader.get(ids=["a"]).documents == ["beta"]
    finally:
        readonly.close()
