"""Compact's format, filters and keyword-only lifecycle contract."""

import json
import os
import sqlite3
import sys

import numpy as np
import pytest

from mempalace.backends import (
    BackendClosedError,
    BackendError,
    BackendMismatchError,
    DimensionMismatchError,
    EmbedderIdentityMismatchError,
    PalaceRef,
    UnsupportedFilterError,
)
from mempalace.backends.compact import (
    _APPLICATION_ID,
    KEYWORD_ONLY,
    CompactBackend,
)
from mempalace.backends.embedding_wrapper import EmbeddingCollection


@pytest.fixture
def compact(tmp_path):
    backend = CompactBackend()
    ref = PalaceRef(id=str(tmp_path), local_path=str(tmp_path))
    col = backend.get_collection(palace=ref, collection_name="mempalace_drawers", create=True)
    yield backend, ref, col
    backend.close()


def test_format_roundtrip_and_partial_updates(compact):
    backend, ref, col = compact
    texts = ["plain " * 200, "NUL\0 and lone \udc80 and \\uDC80", "\x01mpesc:literal\\0"]
    metas = [{"wing": "w\0", "source_file": "\udcff", "tag": ["\udc90"]}] * 3
    col.add(ids=["a", "b", "c"], documents=texts, metadatas=metas, embeddings=[[0.1, 1, -0.6]] * 3)
    col.update(ids=["a", "b", "c"], metadatas=[{"room": "moved"}] * 3)
    got = col.get(ids=["a", "b", "c"], include=["documents", "metadatas", "embeddings"])
    assert got.documents == texts
    assert got.metadatas == [{**meta, "room": "moved"} for meta in metas]
    assert np.allclose(got.embeddings, [[0.1, 1, -0.6]] * 3, atol=1 / 127)
    conn = col._handle.conn
    assert conn.execute("PRAGMA application_id").fetchone()[0] == _APPLICATION_ID
    row = conn.execute("SELECT document,embedding,scale FROM documents LIMIT 1").fetchone()
    assert isinstance(row[0], bytes)
    assert len(row[1]) == 3 and len(row[2]) == 4
    assert conn.execute("SELECT document FROM docs_fts LIMIT 1").fetchone()[0] is None
    backend.close_palace(ref)
    reopened = backend.get_collection(palace=ref, collection_name="mempalace_drawers", create=False)
    assert reopened.get(ids=["b"]).documents == [texts[1]]
    with pytest.raises(BackendClosedError):
        col.count()


def test_dictionary_bootstrap_is_local_immutable_and_per_row(compact):
    _, _, col = compact
    docs = [
        (f"record {i} token {i % 37} " + "document sentence archive searchable words " * 24)
        for i in range(200)
    ]
    col.add(ids=[str(i) for i in range(200)], documents=docs, embeddings=[[1, 2]] * 200)
    conn = col._handle.conn
    dictionary = conn.execute("SELECT id,data FROM dictionaries").fetchone()
    assert dictionary is not None
    assert conn.execute("SELECT DISTINCT dictionary_id FROM documents").fetchone()[0] is None
    col.add(ids=["later"], documents=[docs[0]], embeddings=[[1, 2]])
    assert (
        conn.execute("SELECT dictionary_id FROM documents WHERE id='later'").fetchone()[0]
        == dictionary[0]
    )
    assert col.get(ids=["later", "0"]).documents == [docs[0], docs[0]]
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE dictionaries SET data=x'00'")
    conn.rollback()
    other = col._backend.get_collection(col._handle.palace_path, "other", create=True)
    other.add(ids=["x"], documents=["unrelated"], embeddings=[[1, 2]])
    assert conn.execute("SELECT dictionary_id FROM documents WHERE id='x'").fetchone()[0] is None
    assert conn.execute("SELECT data FROM dictionaries").fetchone()[0] == dictionary[1]


def test_keyword_only_write_update_query_and_copies(compact, monkeypatch, tmp_path):
    backend, ref, inner = compact
    calls = []

    def embed(texts, **kwargs):
        calls.extend(texts)
        return [[1.0, 0.25] for _ in texts]

    monkeypatch.setattr("mempalace.backends.embedding_wrapper._embed_texts", embed)
    col = EmbeddingCollection(inner)
    col.add(
        ids=["k", "v"],
        documents=["keyword initial", "vector initial"],
        metadatas=[{KEYWORD_ONLY: True}, {}],
    )
    assert calls == ["vector initial"]
    calls.clear()
    col.update(ids=["k", "v", "missing"], documents=["keyword changed", "vector changed", "ghost"])
    assert calls == ["vector changed"]
    col.update(ids=["k"], metadatas=[{"title": "changed"}])
    assert calls == ["vector changed"]
    assert col.query(query_embeddings=[[1, 0.25]]).ids == [["v"]]
    rows = col.get(ids=["k", "v"], include=["documents", "metadatas", "embeddings"])
    assert rows.embeddings[0] is None
    assert rows.metadatas[0][KEYWORD_ONLY] is True
    assert {hit.id for hit in col.lexical_search(query="changed").hits} == {"k", "v"}
    # The same document/metadata copy used by replication/rebuild must retain
    # the marker, including when it receives dequantised nullable embeddings.
    other = EmbeddingCollection(
        backend.get_collection(str(tmp_path / "replica"), "mempalace_drawers", create=True)
    )
    calls.clear()
    other.upsert(ids=rows.ids, documents=rows.documents, metadatas=rows.metadatas)
    assert calls == ["vector changed"]
    assert other.get(ids=["k"], include=["embeddings"]).embeddings == [None]
    assert "supports_embeddings_passthrough" not in backend.capabilities
    # Switching to keyword-only deletes the old vector, even if provided.
    col.update(ids=["v"], metadatas=[{KEYWORD_ONLY: True}], embeddings=[[1, 1]])
    assert col.query(query_embeddings=[[1, 1]]).ids == [[]]
    # Explicit promotion embeds the old document exactly once.
    calls.clear()
    col.update(ids=["k"], metadatas=[{KEYWORD_ONLY: False}])
    assert calls == ["keyword changed"]
    assert col.query(query_embeddings=[[1, 1]]).ids == [["k"]]
    # Migration of compact's current schema performs no embedding.
    from mempalace.migrate import migrate

    calls.clear()
    assert migrate(ref.local_path, confirm=True)
    assert calls == []


def test_repair_preserves_keyword_only_and_failure_is_atomic(compact, monkeypatch):
    _, ref, inner = compact
    from mempalace import repair

    inner.add(
        ids=["k", "v"],
        documents=["keyword row", "vector row"],
        metadatas=[{KEYWORD_ONLY: True}, {}],
        embeddings=[None, [1, 2]],
    )
    calls = []

    def embed(texts, **kwargs):
        calls.extend(texts)
        return [[1, 0] for _ in texts]

    monkeypatch.setattr("mempalace.backends.embedding_wrapper._embed_texts", embed)
    repair.rebuild_index(ref.local_path, progress=lambda _: None)
    assert calls == ["vector row"]
    assert inner.get(ids=["k"], include=["embeddings"]).embeddings == [None]
    before = inner.get(include=["documents", "metadatas", "embeddings"])

    def fail(*args, **kwargs):
        raise RuntimeError("provider failed")

    monkeypatch.setattr("mempalace.backends.embedding_wrapper._embed_texts", fail)
    with pytest.raises(RuntimeError, match="provider failed"):
        repair.rebuild_index(ref.local_path, progress=lambda _: None)
    assert inner.get(include=["documents", "metadatas", "embeddings"]) == before


def test_filters_hydrate_only_returned_rows_and_fts_mutation(compact, monkeypatch):
    _, _, col = compact
    docs = [f"searchable alpha document {i}" for i in range(200)]
    metas = [
        {
            "wing": "w" if i % 2 else "x",
            "room": "r",
            "parent_drawer_id": f"p{i // 4}",
            "parent_entry_id": f"p{i // 4}",
        }
        for i in range(200)
    ]
    col.add(
        ids=[str(i) for i in range(200)],
        documents=docs,
        metadatas=metas,
        embeddings=[[1, i + 1] for i in range(200)],
    )
    decompress = col._decompress
    seen = []

    def track(*args):
        seen.append(1)
        return decompress(*args)

    monkeypatch.setattr(col, "_decompress", track)
    where = {"$or": [{"parent_drawer_id": "p7"}, {"parent_entry_id": "p7"}]}
    assert col.get(where=where, limit=2).ids == ["28", "29"]
    assert len(seen) == 2
    seen.clear()
    assert len(col.lexical_search(query="searchable", where=where, n_results=2).hits) == 2
    assert len(seen) == 2
    seen.clear()
    assert len(col.query(query_embeddings=[[1, 30]], where=where, n_results=2).ids[0]) == 2
    assert len(seen) == 2
    assert col._handle._vector_cache[1][2].dtype == np.int8
    seen.clear()
    assert col.get(where={"wing": "w"}, limit=2, include=["metadatas"]).ids == ["1", "3"]
    assert not seen
    statements = []
    col._handle.conn.set_trace_callback(statements.append)
    col.get(where=where, limit=2, include=[])
    col._handle.conn.set_trace_callback(None)
    selected_sql = next(sql for sql in statements if "FROM documents d NOT INDEXED" in sql)
    plan = [row[3] for row in col._handle.conn.execute("EXPLAIN QUERY PLAN " + selected_sql)]
    assert any("SEARCH d USING INTEGER PRIMARY KEY (rowid=?)" in part for part in plan)
    assert any("compact_parent_drawer" in part for part in plan)
    assert any("compact_parent_entry" in part for part in plan)
    assert not any("SEARCH d USING COVERING INDEX" in part for part in plan)
    col.upsert(ids=["28"], documents=["replacement uniqueterm"], embeddings=[[1, 1]])
    assert [h.id for h in col.lexical_search(query="uniqueterm").hits] == ["28"]
    assert "28" not in {h.id for h in col.lexical_search(query="searchable", n_results=200).hits}
    col.delete(ids=["28"])
    assert not col.lexical_search(query="uniqueterm").hits
    assert len(col.get(where_document={"$contains": "document 29"}).ids) == 1
    with pytest.raises(UnsupportedFilterError):
        col.get(where={"x": {"$unknown": 1}})


def test_selection_identity_and_minimum_sqlite(compact, tmp_path, monkeypatch):
    backend, ref, col = compact
    from mempalace.backends.registry import resolve_backend_for_palace
    from mempalace.palace import resolve_backend_name

    assert CompactBackend.detect(ref.local_path)
    assert resolve_backend_name(ref.local_path, explicit="compact") == "compact"
    assert resolve_backend_for_palace(config_value="compact", env_value="sqlite_exact") == "compact"
    with pytest.raises(BackendMismatchError):
        resolve_backend_name(ref.local_path, explicit="sqlite_exact")
    col.add(ids=["a"], documents=["text"], embeddings=[[1, 2]])
    with pytest.raises(DimensionMismatchError):
        col.add(ids=["b"], documents=["text"], embeddings=[[1]])
    with pytest.raises(DimensionMismatchError):
        col.query(query_embeddings=[[1]])

    class Embedder:
        model_name = "model-a"
        dimension = 2

    backend.get_collection(palace=ref, collection_name="mempalace_drawers", embedder=Embedder())
    Embedder.model_name = "model-b"
    with pytest.raises(EmbedderIdentityMismatchError):
        backend.get_collection(palace=ref, collection_name="mempalace_drawers", embedder=Embedder())
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 42, 0))
    with pytest.raises(BackendError, match="3.43"):
        backend.get_collection(palace=ref, collection_name="mempalace_drawers")


def test_readonly_cache_observes_commits(compact):
    backend, ref, col = compact
    col.add(ids=["a"], documents=["first"], embeddings=[[1, 0]])
    backend.close_palace(ref)
    reader = backend.get_collection(
        palace=ref, collection_name="mempalace_drawers", options={"read_only": True}
    )
    assert reader.query(query_embeddings=[[1, 0]]).ids == [["a"]]
    writer_backend = CompactBackend()
    try:
        writer = writer_backend.get_collection(palace=ref, collection_name="mempalace_drawers")
        writer.add(ids=["b"], documents=["second"], embeddings=[[0, 1]])
        assert reader.query(query_embeddings=[[0, 1]], n_results=1).ids == [["b"]]
    finally:
        writer_backend.close()


def test_workspace_config_and_cli_selection(tmp_path, monkeypatch):
    from mempalace import cli
    from mempalace.config import MempalaceConfig
    from mempalace.palace import resolve_backend_name

    config_path = tmp_path / "config.json"
    palace_path = tmp_path / "palace"
    config_path.write_text(json.dumps({"palace_path": str(palace_path), "backend": "compact"}))
    monkeypatch.setenv("MEMPALACE_BACKEND", "sqlite_exact")
    monkeypatch.setenv("MEMPALACE_BACKEND_EXPLICIT", "")
    config = MempalaceConfig(config_dir=tmp_path)
    monkeypatch.setattr("mempalace.config.MempalaceConfig", lambda: config)
    assert config.backend == "compact"
    assert resolve_backend_name(str(palace_path)) == "compact"
    assert resolve_backend_name(str(palace_path), explicit="sqlite_exact") == "sqlite_exact"
    captured = {}
    monkeypatch.setattr(sys, "argv", ["mempalace", "--backend", "compact", "status"])
    monkeypatch.setattr(cli, "cmd_status", lambda args: captured.update(args.__dict__))
    cli.main()
    assert captured["global_backend"] == "compact"
    assert os.environ["MEMPALACE_BACKEND_EXPLICIT"] == "compact"


def test_unrelated_and_future_databases_are_rejected(tmp_path):
    path = tmp_path / "compact.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE unrelated(x)")
    conn.commit()
    conn.close()
    backend = CompactBackend()
    try:
        with pytest.raises(BackendError, match="application_id"):
            backend.get_collection(str(tmp_path), "records", create=True)
    finally:
        backend.close()
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA application_id={_APPLICATION_ID}")
    conn.execute("PRAGMA user_version=99")
    conn.close()
    backend = CompactBackend()
    try:
        with pytest.raises(BackendError, match="schema version"):
            backend.get_collection(str(tmp_path), "records", options={"read_only": True})
    finally:
        backend.close()


def test_dimension_failure_and_duplicate_batch_roll_back(compact):
    _, _, col = compact
    col.add(ids=["a"], documents=["original"], embeddings=[[1, 2]])
    with pytest.raises(DimensionMismatchError):
        col.upsert(ids=["a", "b"], documents=["changed", "new"], embeddings=[[2, 1], [1, 2, 3]])
    assert col.get().documents == ["original"]
    with pytest.raises(sqlite3.IntegrityError):
        col.add(ids=["b", "a"], documents=["new", "duplicate"], embeddings=[[1, 2], [1, 2]])
    assert col.get().ids == ["a"]
    assert not col.lexical_search(query="new").hits


def test_literal_metadata_keys_and_scalar_filter_semantics(compact):
    _, _, col = compact
    col.add(
        ids=["a", "b", "c"],
        documents=["one", "two", "three"],
        metadatas=[{'quoted"key': "yes", "日本語": "value", "value": 1}, {"value": "1"}, {}],
        embeddings=[[1, 0]] * 3,
    )
    assert col.get(where={'quoted"key': "yes"}).ids == ["a"]
    assert col.get(where={"日本語": "value"}).ids == ["a"]
    assert col.get(where={"value": 1}).ids == ["a"]
    assert col.get(where={"value": {"$ne": 1}}).ids == ["b", "c"]
    assert col.get(where={"value": {"$in": [None, 1]}}).ids == ["a", "c"]
    assert col.get(where={"value": {"$nin": [None, 1]}}).ids == ["b"]


def test_recent_hydrates_only_newest_rows(compact, monkeypatch):
    _, _, col = compact
    col.add(
        ids=["a", "b", "c"],
        documents=["old", "new", "keyword"],
        metadatas=[
            {"filed_at": "2026-01-01T00:00:00Z"},
            {"filed_at": "2026-01-02T00:00:00+00:00"},
            {"filed_at": "2026-01-03T00:00:00Z", KEYWORD_ONLY: True},
        ],
        embeddings=[[1, 0], [0, 1], None],
    )
    decompress = col._decompress
    seen = []

    def track(*args):
        seen.append(1)
        return decompress(*args)

    monkeypatch.setattr(col, "_decompress", track)
    got = col.get_recent(limit=2, include=["documents", "metadatas", "embeddings"])
    assert got.ids == ["c", "b"]
    assert got.documents == ["keyword", "new"]
    assert got.embeddings == [None, [0, 1]]
    assert len(seen) == 2


@pytest.mark.parametrize("command", [["repair"], ["repair", "rebuild-index"], ["migrate"]])
def test_compact_cli_maintenance_keeps_keyword_only(compact, monkeypatch, command):
    from mempalace import cli

    _, ref, col = compact
    col.add(
        ids=["k", "v"],
        documents=["keyword", "vector"],
        metadatas=[{KEYWORD_ONLY: True}, {}],
        embeddings=[None, [1, 0]],
    )
    seen = []

    def embed(texts, **kwargs):
        seen.extend(texts)
        return [[0, 1] for _ in texts]

    monkeypatch.setattr("mempalace.backends.embedding_wrapper._embed_texts", embed)
    monkeypatch.setenv("MEMPALACE_BACKEND", "")
    monkeypatch.setenv("MEMPALACE_BACKEND_EXPLICIT", "")
    monkeypatch.setattr(
        sys, "argv", ["mempalace", "--palace", ref.local_path, "--backend", "compact", *command]
    )
    cli.main()
    assert seen == ([] if command == ["migrate"] else ["vector"])
    assert col.get(ids=["k"], include=["embeddings"]).embeddings == [None]


@pytest.mark.parametrize(
    "options",
    [
        ["--mode", "from-sqlite"],
        ["--mode", "max-seq-id"],
        ["--archive-existing"],
        ["rebuild-index", "--archive-existing"],
    ],
)
def test_compact_cli_refuses_chroma_recovery_modes(compact, monkeypatch, capsys, options):
    from mempalace import cli

    _, ref, _ = compact
    monkeypatch.setenv("MEMPALACE_BACKEND", "")
    monkeypatch.setenv("MEMPALACE_BACKEND_EXPLICIT", "")
    monkeypatch.setattr(
        sys,
        "argv",
        ["mempalace", "--palace", ref.local_path, "--backend", "compact", "repair", *options],
    )
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "require Chroma" in capsys.readouterr().err


def test_compact_cli_repair_dry_run_never_embeds(compact, monkeypatch):
    from mempalace import cli

    _, ref, col = compact
    col.add(ids=["k"], documents=["keyword"], metadatas=[{KEYWORD_ONLY: True}])

    def unexpected(*args, **kwargs):
        pytest.fail("dry run tried to embed")

    monkeypatch.setattr("mempalace.backends.embedding_wrapper._embed_texts", unexpected)
    monkeypatch.setenv("MEMPALACE_BACKEND", "")
    monkeypatch.setenv("MEMPALACE_BACKEND_EXPLICIT", "")
    monkeypatch.setattr(
        sys,
        "argv",
        ["mempalace", "--palace", ref.local_path, "--backend", "compact", "repair", "--dry-run"],
    )
    cli.main()
    assert col.get().documents == ["keyword"]


def test_current_schema_migrate_is_read_only(compact, monkeypatch):
    from mempalace.migrate import migrate

    _, ref, col = compact
    col.add(ids=["k"], documents=["keyword"], metadatas=[{KEYWORD_ONLY: True}])

    def unexpected(*args, **kwargs):
        pytest.fail("current compact schema check tried to initialize storage")

    monkeypatch.setattr(CompactBackend, "_init_schema", unexpected)
    assert migrate(ref.local_path, dry_run=True)


def test_filtered_lexical_mask_plan_and_randomized_ranking_parity(compact):
    import random

    from mempalace.backends._filters import matches_where

    _, _, col = compact
    rng = random.Random(42)
    words = ["alpha", "beta", "gamma", "delta"]
    documents = [" ".join(rng.choices(words, k=rng.randint(5, 30))) for _ in range(200)]
    metadata = [
        {
            "wing": f"w{rng.randrange(4)}",
            "room": f"r{rng.randrange(5)}",
            "parent_drawer_id": f"p{i // 4}",
            "value": i,
        }
        for i in range(200)
    ]
    col.add(
        ids=[str(i) for i in range(200)],
        documents=documents,
        metadatas=metadata,
        embeddings=[[1, 0]] * 200,
    )
    conn = col._handle.conn
    statements = []
    conn.set_trace_callback(statements.append)
    col.lexical_search(query="alpha beta", where={"wing": "w1", "room": "r2"}, n_results=7)
    conn.set_trace_callback(None)
    mask_sql = next(sql for sql in statements if sql.startswith("WITH allowed AS MATERIALIZED"))
    assert "LIMIT 7" in mask_sql
    plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + mask_sql)]
    assert sum("SCAN docs_fts VIRTUAL TABLE" in part for part in plan) == 1
    assert any("MATERIALIZE allowed" in part for part in plan)
    assert any("SEARCH allowed USING AUTOMATIC COVERING INDEX (rowid=?)" in part for part in plan)
    assert not any(part == "SCAN allowed" for part in plan)
    # Oracle: the original single FTS scan/join, filtered in Python, then
    # limited. This checks result identity and BM25 ordering independently
    # of the new SQL mask construction across randomized predicates.
    baseline = conn.execute(
        "SELECT d.id,bm25(docs_fts) AS score FROM docs_fts JOIN documents d "
        "ON d.rowid=docs_fts.rowid WHERE docs_fts MATCH ? AND d.collection_id=1 "
        "ORDER BY score,d.rowid",
        ('"alpha" OR "beta"',),
    ).fetchall()
    for _ in range(30):
        wing, room = f"w{rng.randrange(4)}", f"r{rng.randrange(5)}"
        where = rng.choice(
            [
                {"wing": wing},
                {"$and": [{"wing": wing}, {"room": room}]},
                {"$or": [{"wing": wing}, {"room": room}]},
                {"$and": [{"wing": {"$ne": wing}}, {"value": {"$gte": rng.randrange(200)}}]},
                {
                    "$or": [
                        {"parent_drawer_id": f"p{rng.randrange(50)}"},
                        {"parent_entry_id": "missing"},
                    ]
                },
            ]
        )
        expected = [row for row in baseline if matches_where(metadata[int(row[0])], where)][:7]
        actual = col.lexical_search(query="alpha beta", where=where, n_results=7).hits
        assert [hit.id for hit in actual] == [row[0] for row in expected]
        assert [hit.score for hit in actual] == pytest.approx([-row[1] for row in expected])
