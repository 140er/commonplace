"""Existing Compact update behavior remains intact as nullable types become explicit."""

from pathlib import Path
from typing import Callable, Optional
from unittest.mock import Mock

import pytest

from mempalace.backends import embedding_wrapper
from mempalace.backends.base import PalaceRef
from mempalace.backends.compact import KEYWORD_ONLY, CompactBackend
from mempalace.backends.embedding_wrapper import EmbeddingCollection


@pytest.mark.parametrize(
    "helper", [embedding_wrapper._is_native_media, embedding_wrapper._metadata_title]
)
def test_existing_helper_none_refusal(
    helper: Callable[[Optional[dict]], object],
) -> None:
    with pytest.raises(AttributeError) as refusal:
        helper(None)
    assert type(refusal.value) is AttributeError
    assert str(refusal.value) == "'NoneType' object has no attribute 'get'"
    assert refusal.value.name == "get"
    assert refusal.value.obj is None


@pytest.mark.parametrize("mode", ["explicit", "absent", "keyword"])
def test_real_compact_update_keeps_embedding_and_forwarding_contract(
    mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = CompactBackend()
    try:
        inner = backend.get_collection(
            palace=PalaceRef(id=str(tmp_path), local_path=str(tmp_path)),
            collection_name="contract",
            create=True,
        )
        original_metadata: list[dict[str, str | bool]] = [
            {"retained": "exact plain metadata"},
            {"retained": "exact keyword metadata", KEYWORD_ONLY: True},
        ]
        inner.add(
            ids=["plain", "keyword"],
            documents=["exact plain words", "exact keyword words"],
            metadatas=original_metadata,
            embeddings=[[1.0, 0.0], None],
        )
        original = inner.get(ids=["plain", "keyword"], include=["metadatas"])
        assert original.metadatas == original_metadata
        assert all(isinstance(metadata, dict) for metadata in original.metadatas)

        ids = ["keyword" if mode == "keyword" else "plain"]
        documents = ["updated exact words"]
        metadatas = [{"patched": "exact patch"}]
        embeddings = [[0.0, 1.0]] if mode == "explicit" else None
        embed = Mock(return_value=[[0.0, 1.0]])
        forward = Mock(wraps=inner.update)
        monkeypatch.setattr(embedding_wrapper, "_embed_texts", embed)
        monkeypatch.setattr(inner, "update", forward)
        EmbeddingCollection(inner).update(
            ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings
        )

        forward.assert_called_once()
        forwarded = forward.call_args.kwargs
        assert forwarded["ids"] is ids
        assert forwarded["documents"] is documents
        assert forwarded["metadatas"] is metadatas
        if mode == "explicit":
            assert forwarded["embeddings"] is embeddings
        elif mode == "keyword":
            assert forwarded["embeddings"] == [None]
        else:
            assert forwarded["embeddings"] == [[0.0, 1.0]]
        if mode == "absent":
            embed.assert_called_once_with(
                documents,
                metadatas=[{**original_metadata[0], **metadatas[0]}],
            )
        else:
            embed.assert_not_called()

        saved = inner.get(ids=["plain", "keyword"], include=["documents", "metadatas"])
        assert saved.ids == ["plain", "keyword"]
        position = 1 if mode == "keyword" else 0
        expected_documents = ["exact plain words", "exact keyword words"]
        expected_documents[position] = documents[0]
        expected_metadata = [dict(metadata) for metadata in original_metadata]
        expected_metadata[position].update(metadatas[0])
        assert saved.documents == expected_documents
        assert saved.metadatas == expected_metadata
        assert metadatas == [{"patched": "exact patch"}]
    finally:
        backend.close()
