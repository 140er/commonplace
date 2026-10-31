"""Every collection, vector and palace sidecar survives an explicit copy."""

import json
import sqlite3

import pytest

from mempalace.backend_copy import (
    CopyMismatch,
    copy_palace,
    open_collection,
    verify_palaces,
)
from mempalace.backends import ChromaBackend, CompactBackend


def seed(path):
    backend = ChromaBackend()
    try:
        for name in ("mempalace_drawers", "other_collection", "empty_collection"):
            col = open_collection(backend, path, name, create=True)
            if name != "empty_collection":
                col.add(
                    ids=["a", "b"],
                    documents=["verbatim\0text", "long " * 300],
                    metadatas=[{"wing": "test", "last_modified": "2026-01-01"}] * 2,
                    embeddings=[[0.13, 1.0, -0.73], [0.0, 0.0, 0.0]],
                )
    finally:
        backend.close()
    for name in ("knowledge_graph.sqlite3", "logstream.sqlite3"):
        with sqlite3.connect(path / name) as conn:
            conn.execute("CREATE TABLE fixture(value TEXT)")
            conn.execute("INSERT INTO fixture VALUES ('verbatim fact')")
    (path / "replica.json").write_text(json.dumps({"replica_id": "test-replica"}))
    (path / "custom-sidecar").write_bytes(b"sidecar\0unchanged")


def test_bidirectional_copy_and_post_switch_write(tmp_path):
    old, compact, restored = [tmp_path / name for name in ("old", "compact", "restored")]
    seed(old)
    copy_palace(old, compact, "chroma", "compact")
    assert not (compact / "chroma.sqlite3").exists()
    backend = CompactBackend()
    try:
        open_collection(backend, compact, "mempalace_drawers", create=True).add(
            ids=["new"],
            documents=["after switch"],
            metadatas=[{"last_modified": "2026-01-02"}],
            embeddings=[[0.3, 0.7, -0.9]],
        )
    finally:
        backend.close()
    copy_palace(compact, restored, "compact", "chroma")
    assert not (restored / "compact.sqlite3").exists()
    verify_palaces(compact, restored, "compact", "chroma")
    for name in ("knowledge_graph.sqlite3", "logstream.sqlite3"):
        with sqlite3.connect(restored / name) as conn:
            assert conn.execute("SELECT * FROM fixture").fetchall() == [("verbatim fact",)]
    assert (restored / "replica.json").read_bytes() == (old / "replica.json").read_bytes()
    assert (restored / "custom-sidecar").read_bytes() == b"sidecar\0unchanged"


def test_size_gate_before_destination_creation(tmp_path):
    seed(tmp_path / "old")
    with pytest.raises(ValueError, match="size limit"):
        copy_palace(tmp_path / "old", tmp_path / "compact", "chroma", "compact", max_records=1)
    assert not (tmp_path / "compact").exists()


def test_verification_detects_changed_metadata(tmp_path):
    seed(tmp_path / "old")
    copy_palace(tmp_path / "old", tmp_path / "compact", "chroma", "compact")
    backend = CompactBackend()
    try:
        open_collection(backend, tmp_path / "compact", "mempalace_drawers", create=True).update(
            ids=["a"], metadatas=[{"wing": "wrong"}]
        )
    finally:
        backend.close()
    with pytest.raises(CopyMismatch):
        verify_palaces(tmp_path / "old", tmp_path / "compact", "chroma", "compact")


def test_sampled_reads_log_hashes_and_leave_live_result_unchanged(caplog):
    import logging
    from types import SimpleNamespace

    from mempalace.dual_read import SampledReads

    current = SimpleNamespace(get=lambda **kwargs: {"id": "record", "text": "private content"})
    previous = SimpleNamespace(get=lambda **kwargs: {"id": "record", "text": "old private content"})
    reader = SampledReads(current, previous, reads=["get"], sample_every=1)
    with caplog.at_level(logging.INFO, logger="mempalace.dual_read"):
        assert reader.get(query="sensitive query") == current.get()
    assert "match=False" in caplog.text
    assert "private content" not in caplog.text
    assert "sensitive query" not in caplog.text


def test_vector_verification_rejects_errors_beyond_rounding():
    from mempalace.backend_copy import verify_records
    from mempalace.backends import GetResult

    original = GetResult(ids=["a"], documents=["text"], metadatas=[{}], embeddings=[[1.0, 0.5]])
    rounded = GetResult(ids=["a"], documents=["text"], metadatas=[{}], embeddings=[[1.0, 64 / 127]])
    verify_records(original, rounded, quantized=True)
    rounded = GetResult(ids=["a"], documents=["text"], metadatas=[{}], embeddings=[[1.0, 0.6]])
    with pytest.raises(CopyMismatch, match="quantisation"):
        verify_records(original, rounded, quantized=True)


def test_sampled_comparison_error_never_logs_exception_payload(caplog):
    import logging
    from types import SimpleNamespace

    from mempalace.dual_read import SampledReads

    def unavailable():
        raise ValueError("sensitive exception payload")

    reader = SampledReads(
        SimpleNamespace(get=lambda: "value"),
        SimpleNamespace(get=unavailable),
        reads=["get"],
        sample_every=1,
    )
    with caplog.at_level(logging.INFO, logger="mempalace.dual_read"):
        assert reader.get() == "value"
    assert "comparison_unavailable" in caplog.text
    assert "sensitive exception payload" not in caplog.text


def test_copy_never_loads_operator_embedder(tmp_path, monkeypatch):
    seed(tmp_path / "old")

    def unavailable(**kwargs):
        raise AssertionError("The operator embedder must not be resolved during a copy")

    monkeypatch.setattr(ChromaBackend, "_resolve_embedding_function", staticmethod(unavailable))
    copy_palace(tmp_path / "old", tmp_path / "compact", "chroma", "compact")
    copy_palace(tmp_path / "compact", tmp_path / "restored", "compact", "chroma")


def test_rollback_preserves_empty_metadata_and_unknown_identity_dimension(tmp_path):
    from mempalace.backends.base import EmbedderIdentity

    source = tmp_path / "compact"
    backend = CompactBackend()
    try:
        col = open_collection(backend, source, "mempalace_drawers", create=True)
        col.add(
            ids=["empty", "legacy"],
            documents=["first", "second"],
            metadatas=[{}, {"wing": "test"}],
            embeddings=[[1.0, 2.0], [2.0, 1.0]],
        )
        col.set_embedder_identity(EmbedderIdentity("fixture-model", 0))
    finally:
        backend.close()
    copy_palace(source, tmp_path / "chroma", "compact", "chroma")
    verify_palaces(source, tmp_path / "chroma", "compact", "chroma")
    copy_palace(tmp_path / "chroma", tmp_path / "compact-again", "chroma", "compact")


def test_legacy_unknown_embedder_dimension_survives_both_directions(tmp_path):
    from mempalace.backends.base import EmbedderIdentity

    source = tmp_path / "old"
    seed(source)
    backend = ChromaBackend()
    try:
        open_collection(backend, source, "mempalace_drawers").set_embedder_identity(
            EmbedderIdentity("fixture-model", 0)
        )
    finally:
        backend.close()
    identity = (source / "mempalace_embedder.json").read_bytes()
    copy_palace(source, tmp_path / "compact", "chroma", "compact")
    copy_palace(tmp_path / "compact", tmp_path / "restored", "compact", "chroma")
    assert (tmp_path / "restored" / "mempalace_embedder.json").read_bytes() == identity


def test_sqlite_copy_verification_compares_text_after_nul(tmp_path):
    import shutil

    from mempalace.backend_copy import verify_sidecars

    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    with sqlite3.connect(source / "records.sqlite3") as conn:
        conn.execute("CREATE TABLE records(value TEXT)")
        conn.execute("INSERT INTO records VALUES (?)", ("prefix\0original",))
    conn.close()
    shutil.copyfile(source / "records.sqlite3", target / "records.sqlite3")
    with sqlite3.connect(target / "records.sqlite3") as conn:
        conn.execute("UPDATE records SET value=?", ("prefix\0changed",))
    conn.close()
    with pytest.raises(CopyMismatch, match="values mismatch"):
        verify_sidecars(source, target)
