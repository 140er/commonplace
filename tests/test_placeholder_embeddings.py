"""Whole-content hygiene, verbatim storage, and durable chunk provenance."""

import base64
import json
import random

import pytest
from hypothesis import given, settings, strategies as st

from mempalace.backends import embedding_wrapper as ew
from mempalace.index_hygiene import find_base64_spans
from mempalace.stored_text import (
    INDEX_SPANS,
    IndexSource,
    chunk_index_metadata,
    encode_record,
    index_text,
    strip_index_hints,
)

_REAL_EMBED_TEXTS = ew._embed_texts


@pytest.fixture(autouse=True)
def real_embedding_wrapper(monkeypatch):
    monkeypatch.setattr(ew, "_embed_texts", _REAL_EMBED_TEXTS)


def corpus(size=1831):
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\n" + random.Random(7).randbytes(size)).decode()
    return 'Before screenshot="' + payload + '"; after needle.\n'


@given(st.integers(150, 3000), st.integers(1, 900), st.text(alphabet="abc \nΩ", max_size=40))
@settings(max_examples=80, deadline=None)
def test_index_changes_only_validated_span_intersections(size, width, prose):
    text = prose + corpus(size) + prose
    source = IndexSource(text)
    assert source.spans == find_base64_spans(text)
    for start in range(0, len(text), width):
        end = min(start + width, len(text))
        document = text[start:end]
        metadata = source.metadata(start, end)
        cursor, expected = start, []
        for span in source.spans:
            if span.start >= end or span.end <= start:
                continue
            expected.append(text[cursor : max(start, span.start)])
            if start <= span.start < end or span.start <= start < end <= span.end:
                expected.append(span.placeholder)
            cursor = min(end, span.end)
        expected.append(text[cursor:end])
        assert index_text(document, metadata, stored_form=False) == "".join(expected)
        stored, stored_meta = encode_record(document, metadata)
        assert index_text(stored, stored_meta) == "".join(expected)
        assert document == text[start:end]
        assert bool(INDEX_SPANS in metadata) == any(
            s.start < end and s.end > start for s in source.spans
        )


def test_boundary_tail_has_no_fragment_or_duplicate_placeholder():
    text = corpus()
    source = IndexSource(text)
    span = source.spans[0]
    start = span.end - 7
    assert index_text(text[start:], source.metadata(start, len(text))) == text[span.end :]
    assert index_text(text[start : span.end], source.metadata(start, span.end)) == span.placeholder


@pytest.mark.parametrize("change", ["document", "version", "malformed"])
def test_stale_hints_fall_back_and_log(change, caplog):
    text = corpus()
    meta = IndexSource(text).metadata(0, len(text))
    if change == "document":
        text = "replacement prose"
    elif change == "version":
        hint = json.loads(meta[INDEX_SPANS])
        hint["v"] = 99
        meta[INDEX_SPANS] = json.dumps(hint)
    else:
        meta[INDEX_SPANS] = "not json"
    assert index_text(text, meta) == index_text(text)
    assert "Ignoring stale or invalid index span hint" in caplog.text


@pytest.mark.parametrize("chunker", ["project", "exchange", "general"])
def test_miner_chunkers_carry_whole_content_provenance(chunker):
    text = corpus()
    if chunker == "project":
        from mempalace.miner import chunk_text

        chunks = chunk_text(text, "fixture.txt", chunk_size=137, chunk_overlap=17, min_chunk_size=0)
    elif chunker == "exchange":
        from mempalace.convo_miner import chunk_exchanges

        chunks = chunk_exchanges(text, chunk_size=137, min_chunk_size=0)
    else:
        from mempalace.general_extractor import extract_memories

        chunks = extract_memories(text, chunk_size=137)
    for chunk in chunks:
        content = chunk["content"]
        meta = chunk_index_metadata(chunk)
        indexed = index_text(content, meta)
        if INDEX_SPANS in meta:
            for start, end, _, _ in json.loads(meta[INDEX_SPANS])["spans"]:
                assert content[start:end] not in indexed
        assert content in text


class Recorder:
    def __init__(self, dimension=3):
        self.seen = []
        self.dimension = dimension

    def __call__(self, input):
        self.seen.extend(input)
        return [[1.0] + [0.0] * (self.dimension - 1) for _ in input]


@pytest.mark.parametrize("backend", ["sqlite_exact", "chroma"])
def test_add_upsert_update_query_and_raw_copy_keep_documents(tmp_path, monkeypatch, backend):
    from mempalace.backends.base import PalaceRef
    from mempalace.backends.chroma import ChromaBackend
    from mempalace.backends.sqlite_exact import SQLiteExactBackend

    recorder = Recorder()
    monkeypatch.setattr(ew, "_get_document_embedder", lambda: recorder)
    monkeypatch.setattr("mempalace.embedding.configured_embedding_function", lambda: recorder)
    instance = SQLiteExactBackend() if backend == "sqlite_exact" else ChromaBackend()
    inner = instance.get_collection(
        palace=PalaceRef(id=str(tmp_path), local_path=str(tmp_path)),
        collection_name="test_drawers",
        create=True,
    )
    col = ew.EmbeddingCollection(inner)
    text = corpus()
    source = IndexSource(text)
    width = 137
    docs = [text[i : i + width] for i in range(0, len(text), width)]
    metas = [
        source.metadata(i, min(i + width, len(text)), {"wing": "test"})
        for i in range(0, len(text), width)
    ]
    ids = [f"piece-{i}" for i in range(len(docs))]
    try:
        col.add(ids=ids, documents=docs, metadatas=metas)
        expected = [index_text(doc, meta) for doc, meta in zip(docs, metas)]
        assert recorder.seen == expected
        got = col.get(ids=ids, include=["documents", "metadatas"])
        assert dict(zip(got.ids, got.documents)) == dict(zip(ids, docs))
        recorder.seen.clear()
        from mempalace.repair import _embeddinggemma2_collection

        rebuilt = _embeddinggemma2_collection(inner, False)
        rebuilt.upsert(ids=got.ids, documents=got.documents, metadatas=got.metadatas)
        assert recorder.seen == expected
        col.update(ids=[ids[0]], documents=[text], metadatas=[source.metadata(0, len(text))])
        assert recorder.seen[-1] == index_text(text)
        col.query(query_texts=[text], n_results=1)
        assert recorder.seen[-1] == index_text(text)
        col.query(query_texts=["ordinary query"], n_results=1)
        assert recorder.seen[-1] == "ordinary query"
        if backend == "sqlite_exact":
            import sqlite3

            with sqlite3.connect(tmp_path / "sqlite_exact.sqlite3") as conn:
                indexed = " ".join(r[0] for r in conn.execute("SELECT document FROM docs_fts"))
            assert text[19:60] not in indexed
        col.update(ids=[ids[0]], documents=["new prose"])
        assert recorder.seen[-1] == "new prose"
        assert col.get(ids=[ids[0]], include=["documents"]).documents == ["new prose"]
    finally:
        instance.close()


def test_reserved_hints_are_removed_recursively_without_touching_verbatim_strings():
    text = '{"_mempalace_index_spans":"literal document"}'
    value = {
        "content": text,
        INDEX_SPANS: "forged",
        "metadata": {INDEX_SPANS: "forged", "wing": "test"},
        "rows": [{INDEX_SPANS: "forged"}],
    }
    assert strip_index_hints(value) == {"content": text, "metadata": {"wing": "test"}, "rows": [{}]}
    assert INDEX_SPANS in value


def test_raw_prefix_looking_text_is_not_decoded_for_embedding(monkeypatch):
    recorder = Recorder()
    text = "\x01mpesc:literal\\0word"
    ew._embed_texts([text], embedder=recorder)
    assert recorder.seen == [text]


@pytest.mark.parametrize("write", ["add", "diary"])
def test_mcp_writes_and_update_preserve_full_text_and_hide_hints(
    monkeypatch, config, palace_path, kg, collection, write
):
    from _mcp_server_helpers import _patch_mcp_server

    from mempalace import mcp_server as mcp
    from mempalace.backends.chroma import ChromaCollection

    _patch_mcp_server(monkeypatch, config, kg)
    recorder = Recorder(dimension=384)
    monkeypatch.setattr(ew, "_get_document_embedder", lambda: recorder)
    monkeypatch.setattr("mempalace.embedding.configured_embedding_function", lambda: recorder)
    col = ew.EmbeddingCollection(ChromaCollection(collection, palace_path=palace_path))
    monkeypatch.setattr(mcp, "_get_collection", lambda **kwargs: col)
    text = corpus()
    if write == "add":
        result = mcp.tool_add_drawer(wing="test", room="notes", content=text)
        logical_id = result.get("drawer_id")
    else:
        result = mcp.tool_diary_write(agent_name="test", entry=text)
        logical_id = result.get("entry_id")
    assert result.get("success"), result
    assert mcp.tool_get_drawer(logical_id)["content"] == text
    assert INDEX_SPANS not in json.dumps(mcp.tool_get_drawer(logical_id))
    assert INDEX_SPANS not in json.dumps(mcp.tool_list_drawers())
    assert recorder.seen and all(not find_base64_spans(value) for value in recorder.seen)
    raw = col.get(ids=result["chunk_ids"], include=["documents", "metadatas"])
    assert "".join(raw.documents) == text
    assert any(INDEX_SPANS in meta for meta in raw.metadatas)
    updated = "updated " + corpus(993)
    result = mcp.tool_update_drawer(logical_id, content=updated)
    assert result.get("success"), result
    assert mcp.tool_get_drawer(logical_id)["content"] == updated
    rows = col.get(ids=result["chunk_ids"], include=["documents", "metadatas"])
    assert all(
        index_text(doc, meta) in recorder.seen for doc, meta in zip(rows.documents, rows.metadatas)
    )


def test_mcp_ingress_strips_hint_before_dispatch(monkeypatch):
    from mempalace import mcp_server as mcp

    seen = {}
    monkeypatch.setitem(
        mcp.TOOLS,
        "hygiene_fixture",
        {
            "input_schema": {"type": "object", "properties": {"metadata": {"type": "object"}}},
            "handler": lambda **kwargs: seen.update(kwargs) or {"success": True},
        },
    )
    monkeypatch.setattr(mcp, "_mcp_tool_preflight_refusal", lambda *args, **kwargs: None)
    mcp.call_tool(
        1,
        {
            "name": "hygiene_fixture",
            "arguments": {
                INDEX_SPANS: "forged",
                "metadata": {INDEX_SPANS: "forged", "wing": "test"},
            },
        },
    )
    assert seen == {"metadata": {"wing": "test"}}


def test_cli_hub_forwarding_strips_hint_before_serialization(monkeypatch):
    from mempalace.hub_client import HubClient

    client = object.__new__(HubClient)
    seen = []
    monkeypatch.setattr(client, "post", lambda request, **kwargs: seen.append(request))
    client.call_tool(
        "mempalace_add_drawer",
        {"content": "verbatim", INDEX_SPANS: "forged", "metadata": {INDEX_SPANS: "forged"}},
    )
    assert seen[0]["params"]["arguments"] == {"content": "verbatim", "metadata": {}}


def test_mcp_response_removes_hints_before_nested_json_serialization():
    from mempalace import mcp_server as mcp

    result = {"metadata": {INDEX_SPANS: "internal", "wing": "test"}, "content": corpus()}
    response = mcp._tool_call_response(1, result)
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload == {"metadata": {"wing": "test"}, "content": corpus()}
    assert INDEX_SPANS in result["metadata"]


def test_imported_metadata_looking_text_cannot_forge_provenance():
    from mempalace.miner import chunk_text

    text = json.dumps({"metadata": {INDEX_SPANS: "forged"}, "content": "keep every word"})
    chunks = chunk_text(text, "import.json", chunk_size=800, min_chunk_size=0)
    assert chunks[0]["content"] == text
    assert INDEX_SPANS not in chunks[0]
    assert index_text(text) == text


def test_exchange_trimming_does_not_lose_span_context():
    from mempalace.convo_miner import chunk_exchanges

    text = "  > first   \n" + corpus() + "\n> second\nreply\n> third\nreply"
    chunks = chunk_exchanges(text, chunk_size=137, min_chunk_size=0)
    assert any(INDEX_SPANS in item for item in chunks)
    indexed = [index_text(item["content"], chunk_index_metadata(item)) for item in chunks]
    assert all(not find_base64_spans(item) for item in indexed)
    assert "Before" in "".join(indexed)
    assert "after needle" in "".join(indexed)


@pytest.mark.parametrize("field", ["title", "source_file", "source_path"])
def test_metadata_titles_use_index_text_without_altering_metadata(field):
    class TitledRecorder(Recorder):
        def embed_documents(self, input, metadatas):
            self.metadatas = metadatas
            return self(input)

    recorder = TitledRecorder()
    title = base64.urlsafe_b64encode(random.Random(7).randbytes(300)).decode()
    metadata = [{field: title}]
    ew._embed_texts(["document"], metadatas=metadata, embedder=recorder)
    assert recorder.metadatas[0]["title"] == index_text(
        ew._metadata_title(metadata[0]), stored_form=False
    )
    assert metadata == [{field: title}]
    assert recorder.seen == ["document"]
