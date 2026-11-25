"""Runtime controls for truthful backend declarations and wrapper compatibility."""

import inspect
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator, Sequence
from unittest.mock import Mock

import chromadb
import pytest
from chromadb.api.client import Client
from chromadb.config import Settings
from pydantic import ValidationError

import mempalace.palace as palace
from mempalace.backends import chroma, chroma_sqlite, embedding_wrapper
from mempalace.backends.base import BaseCollection, PalaceRef, QueryResult
from mempalace.backends.embedding_wrapper import EmbeddingCollection
from mempalace.backends.sqlite_exact import SQLiteExactBackend, SQLiteExactCollection


@pytest.fixture
def stored_collection(tmp_path: Path) -> Iterator[chroma.ChromaCollection]:
    client = chromadb.PersistentClient(
        path=str(tmp_path), settings=Settings(anonymized_telemetry=False)
    )
    assert isinstance(client, Client)
    raw = client.create_collection("contract_drawers", embedding_function=None)
    vectors: list[Sequence[float] | Sequence[int]] = [[1.0, 0.0], [0.0, 1.0]]
    raw.add(
        ids=["bare", "dated"],
        documents=["exact bare words", "exact dated words"],
        embeddings=vectors,
    )
    raw.update(ids=["dated"], metadatas=[{"filed_at": "2026-11-21T00:00:00Z"}])
    try:
        yield chroma.ChromaCollection(raw, palace_path=str(tmp_path))
    finally:
        client.close()


def test_real_optional_metadata_keeps_rows_and_values(
    stored_collection: chroma.ChromaCollection, tmp_path: Path
) -> None:
    with sqlite3.connect(tmp_path / "chroma.sqlite3") as connection:
        original = list(
            chroma_sqlite._sqlite_iter_metadata(connection, "contract_drawers", None, None)
        )
    assert original == [None, {"filed_at": "2026-11-21T00:00:00Z"}]
    rows = stored_collection.get_all_metadata()
    assert isinstance(rows, list)
    assert rows == original
    recent = stored_collection.get_recent(limit=10)
    assert recent.ids == ["dated", "bare"]
    assert recent.documents == ["exact dated words", "exact bare words"]
    assert isinstance(recent.metadatas, list)
    assert recent.metadatas == [original[1], None]
    assert stored_collection.get_recent(limit=10, include=["documents"]).metadatas == []


def test_metadata_forwarder_preserves_snapshot_identity(
    stored_collection: chroma.ChromaCollection, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = stored_collection.get_all_metadata()
    monkeypatch.setattr(stored_collection, "get_all_metadata", lambda **kwargs: snapshot)
    wrapped = EmbeddingCollection(stored_collection)
    assert wrapped.get_all_metadata() is snapshot
    assert snapshot[0] is None


@pytest.mark.parametrize("space", ["cosine", "l2", "ip"])
def test_valid_native_vector_schema(space: str) -> None:
    schema = chroma._caller_vector_schema({"hnsw_space": space})
    floats = schema.keys["#embedding"].float_list
    assert floats is not None and floats.vector_index is not None
    config = floats.vector_index.config
    assert config is not None and config.hnsw is not None
    assert config.space == space
    assert config.embedding_function is None
    assert config.hnsw.num_threads == 1
    assert config.hnsw.batch_size == chroma._HNSW_WRITE_DEFAULTS["hnsw:batch_size"]
    assert config.hnsw.sync_threshold == chroma._HNSW_WRITE_DEFAULTS["hnsw:sync_threshold"]


@pytest.mark.parametrize("space", ["unsupported", "42"])
def test_invalid_native_vector_schema_keeps_validation(space: str) -> None:
    values = {
        "embedding_function": None,
        "hnsw": chromadb.HnswIndexConfig(
            num_threads=1,
            batch_size=chroma._HNSW_WRITE_DEFAULTS["hnsw:batch_size"],
            sync_threshold=chroma._HNSW_WRITE_DEFAULTS["hnsw:sync_threshold"],
        ),
        "space": space,
    }
    with pytest.raises(ValidationError) as native:
        chromadb.VectorIndexConfig.model_validate(values)
    with pytest.raises(ValidationError) as actual:
        chroma._caller_vector_schema({"hnsw_space": space})
    assert actual.value.errors(include_url=False) == native.value.errors(include_url=False)
    assert str(actual.value) == str(native.value)


def test_absent_schema_and_dynamic_flags_keep_defaults() -> None:
    for schema in [None, {}, {"keys": None}]:
        assert chroma._schema_hnsw_config(schema) is None
    direct = chroma.ChromaCollection(object())
    for name in ["_require_embeddings", "_migration_vectors"]:
        assert not hasattr(direct, name)
        assert getattr(direct, name, False) is False


def test_backend_assigns_existing_caller_vector_flags(tmp_path: Path) -> None:
    backend = chroma.ChromaBackend()
    try:
        collection = backend.get_collection(
            palace=PalaceRef(id=str(tmp_path), local_path=str(tmp_path)),
            collection_name="flags",
            create=True,
            options={"caller_vectors": True},
        )
        assert collection._require_embeddings is True
        assert collection._migration_vectors is False
    finally:
        backend.close()


def test_existing_capability_getter_tracks_inner_without_writes(
    stored_collection: chroma.ChromaCollection,
) -> None:
    inner = stored_collection
    wrapped = EmbeddingCollection(inner)
    assert wrapped.upsert_merges_metadata is True
    inner.upsert_merges_metadata = False
    assert wrapped.upsert_merges_metadata is False
    assert inner.distance_metric == wrapped.distance_metric


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("value", [False, True])
def test_new_capability_setter_forwards_explicit_writes(nested: bool, value: bool) -> None:
    inner = chroma.ChromaCollection(object())
    first = EmbeddingCollection(inner)
    wrapped = EmbeddingCollection(first) if nested else first
    wrapped.upsert_merges_metadata = value
    assert wrapped.upsert_merges_metadata is value
    assert first.upsert_merges_metadata is value
    assert inner.upsert_merges_metadata is value


def test_actual_upserts_keep_metadata_merge_semantics(
    stored_collection: chroma.ChromaCollection,
) -> None:
    wrapped = EmbeddingCollection(stored_collection)
    wrapped.upsert(
        ids=["dated"],
        documents=["replacement words"],
        metadatas=[{"room": "new"}],
        embeddings=[[1.0, 0.0]],
    )
    result = stored_collection.get(ids=["dated"], include=["documents", "metadatas"])
    assert result.documents == ["replacement words"]
    assert result.metadatas == [{"filed_at": "2026-11-21T00:00:00Z", "room": "new"}]


@pytest.mark.parametrize("texts", ["whole query", ["whole query"]])
def test_existing_text_queries_embed_once(
    stored_collection: chroma.ChromaCollection,
    monkeypatch: pytest.MonkeyPatch,
    texts: str | list[str],
) -> None:
    embed = Mock(return_value=[[1.0, 0.0]])
    monkeypatch.setattr(embedding_wrapper, "_embed_texts", embed)
    result = EmbeddingCollection(stored_collection).query(query_texts=texts, n_results=1)
    assert result.ids == [["bare"]]
    embed.assert_called_once_with(["whole query"], query=True)


@pytest.mark.parametrize("texts", ["whole query", ["whole query"]])
def test_actual_chroma_combined_input_refusal_does_no_work(
    stored_collection: chroma.ChromaCollection,
    monkeypatch: pytest.MonkeyPatch,
    texts: str | list[str],
) -> None:
    embed = Mock(side_effect=AssertionError("invalid input must not embed"))
    storage = Mock(side_effect=AssertionError("invalid input must not query storage"))
    monkeypatch.setattr(embedding_wrapper, "_embed_texts", embed)
    monkeypatch.setattr(stored_collection._collection, "query", storage)
    with pytest.raises(ValueError) as original:
        stored_collection.query(query_texts=texts, query_embeddings=[[1.0, 0.0]])
    with pytest.raises(ValueError) as wrapped:
        EmbeddingCollection(stored_collection).query(
            query_texts=texts, query_embeddings=[[1.0, 0.0]]
        )
    assert str(wrapped.value) == str(original.value)
    embed.assert_not_called()
    storage.assert_not_called()


def test_actual_sqlite_combined_input_refusal_and_vector_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = SQLiteExactBackend()
    collection = backend.get_collection(
        palace=PalaceRef(id=str(tmp_path), local_path=str(tmp_path)),
        collection_name="contract",
        create=True,
    )
    assert isinstance(collection, SQLiteExactCollection)
    embed = Mock(side_effect=AssertionError("explicit vectors must not embed"))
    monkeypatch.setattr(embedding_wrapper, "_embed_texts", embed)
    try:
        collection.add(ids=["one"], documents=["exact words"], embeddings=[[1.0, 0.0]])
        with pytest.raises(ValueError) as original:
            collection.query(query_texts="whole query", query_embeddings=[[1.0, 0.0]])
        with pytest.raises(ValueError) as wrapped:
            EmbeddingCollection(collection).query(
                query_texts="whole query", query_embeddings=[[1.0, 0.0]]
            )
        assert str(wrapped.value) == str(original.value)
        result = EmbeddingCollection(collection).query(query_embeddings=[[1.0, 0.0]], n_results=1)
        assert result.ids == [["one"]]
        embed.assert_not_called()
    finally:
        backend.close()


class _UpdateSnapshot(chroma.ChromaCollection):
    def __init__(self, embeddings: list[list[float] | None] | None) -> None:
        super().__init__(object())
        self.snapshot = SimpleNamespace(
            ids=["first", "second"],
            documents=["old first", "old second"],
            metadatas=[{}, {}],
            embeddings=embeddings,
        )
        self.saved: list[dict] = []

    def get(self, **kwargs):
        return self.snapshot

    def upsert(self, **kwargs) -> None:
        self.saved.append(kwargs)


@pytest.mark.parametrize("embeddings", [None, [None, [0.0, 1.0]], [[1.0, 0.0], [0.0, 1.0]]])
def test_base_update_keeps_whole_batch_embedding_policy(
    embeddings: list[list[float] | None] | None,
) -> None:
    collection = _UpdateSnapshot(embeddings)
    BaseCollection.update(
        collection, ids=["second", "first"], documents=["new second", "new first"]
    )
    assert collection.saved == [
        {
            "ids": ["second", "first"],
            "documents": ["new second", "new first"],
            "metadatas": [{}, {}],
            "embeddings": (
                None
                if embeddings is None or any(vector is None for vector in embeddings)
                else [embeddings[1], embeddings[0]]
            ),
        }
    ]
    assert QueryResult.empty(2, embeddings_requested=True).embeddings == [[], []]


def test_mine_lock_export_keeps_exec_identity_and_owned_lock(tmp_path: Path) -> None:
    original = inspect.unwrap(palace.mine_palace_lock)
    assert original.__globals__ is vars(palace)
    assert Path(original.__code__.co_filename).name == "palace_lock.py"
    assert "mempalace.palace.palace_lock" not in sys.modules
    with palace.mine_palace_lock(str(tmp_path / "data"), lock_dir=str(tmp_path / "locks")) as held:
        assert held is None
        with palace.mine_palace_lock(str(tmp_path / "data"), lock_dir=str(tmp_path / "locks")):
            assert "mempalace.palace.palace_lock" not in sys.modules
