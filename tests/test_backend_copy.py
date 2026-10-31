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
    assert "current_hash=" in caplog.text
    assert "previous_hash=" in caplog.text
    assert "private content" not in caplog.text
    assert "sensitive query" not in caplog.text
