"""Whole-content hygiene, verbatim storage, and durable chunk provenance."""

import base64
import json
import random
import sys
import unicodedata
from collections import UserDict
from types import MappingProxyType
from unittest.mock import patch

import pytest
from hypothesis import example, given, settings, strategies as st

from mempalace.backends import embedding_wrapper as ew
from mempalace.index_hygiene import find_base64_spans
from mempalace.stored_text import (
    INDEX_SPANS,
    RESERVED_METADATA,
    IndexSource,
    chunk_index_metadata,
    encode_record,
    index_text,
    user_tool_arguments,
)

_REAL_EMBED_TEXTS = ew._embed_texts


@pytest.fixture(autouse=True)
def real_embedding_wrapper(monkeypatch):
    monkeypatch.setattr(ew, "_embed_texts", _REAL_EMBED_TEXTS)
    monkeypatch.setattr("mempalace.stored_text._hint_warning_times", {})


def corpus(size=1831):
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\n" + random.Random(7).randbytes(size)).decode()
    return 'Before screenshot="' + payload + '"; after needle.\n'


@given(
    size=st.one_of(st.sampled_from([150, 151, 152, 153]), st.integers(150, 3000)),
    width=st.one_of(st.integers(1, 12), st.integers(13, 900)),
    prose=st.text(alphabet="abc \nΩ\t=", max_size=40),
    edges=st.sampled_from(
        [
            ("'", "'"),
            (" ", "\t"),
            ("\n", "\r\n"),
            ("=", ";"),
            ("key=", ";"),
            ("Ω", "é"),
            ("cafe\u0301", ""),
            ("\u0301", "\u20dd"),
            ("\u200c", "\u200d"),
            ("٢", "Ⅳ"),
            ("…", "🧭"),
        ]
    ),
)
@settings(max_examples=80, deadline=None)
@example(size=150, width=1, prose="", edges=("=", "\t"))
@example(size=151, width=2, prose="\n", edges=("key=", ";"))
@example(size=152, width=3, prose="", edges=("Ω", "é"))
@example(size=153, width=12, prose="", edges=("…", "🧭"))
@example(size=150, width=7, prose="", edges=("٢", "Ⅳ"))
def test_index_changes_only_validated_span_intersections(size, width, prose, edges):
    with patch("mempalace.stored_text._warn_index_hint") as warn:
        run = base64.b64encode(random.Random(size).randbytes(size)).decode()
        text = prose + edges[0] + run + edges[1] + prose
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
                # Padding-only intersections carry no searchable payload and stay literal.
                if not text[max(start, span.start) : min(end, span.end)].strip("="):
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
                left < end and right > start for left, right in source.candidates
            )
    warn.assert_not_called()


def test_boundary_tail_has_no_fragment_or_duplicate_placeholder():
    text = corpus()
    source = IndexSource(text)
    span = source.spans[0]
    start = span.end - 7
    assert index_text(text[start:], source.metadata(start, len(text))) == text[span.end :]
    assert index_text(text[start : span.end], source.metadata(start, span.end)) == span.placeholder


@pytest.mark.parametrize("size", [151, 152])
@pytest.mark.parametrize("tail", ["", "; after needle"])
def test_producer_leaves_padding_only_intersections_literal(size, tail, caplog):
    run = base64.b64encode(random.Random(size).randbytes(size)).decode()
    text = run + tail
    start = len(run.rstrip("="))
    document = text[start:]
    metadata = IndexSource(text).metadata(start, len(text))
    assert json.loads(metadata[INDEX_SPANS])["spans"] == []
    assert index_text(document, metadata) == document
    assert "Ignoring stale or invalid index span hint" not in caplog.text


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


@pytest.mark.parametrize("backend", ["sqlite_exact", "chroma", "compact"])
def test_add_upsert_update_query_and_raw_copy_keep_documents(tmp_path, monkeypatch, backend):
    from mempalace.backends.base import PalaceRef
    from mempalace.backends.chroma import ChromaBackend
    from mempalace.backends.compact import CompactBackend
    from mempalace.backends.sqlite_exact import SQLiteExactBackend

    recorder = Recorder()
    monkeypatch.setattr(ew, "_get_document_embedder", lambda: recorder)
    monkeypatch.setattr("mempalace.embedding.configured_embedding_function", lambda: recorder)
    instance = {
        "sqlite_exact": SQLiteExactBackend,
        "chroma": ChromaBackend,
        "compact": CompactBackend,
    }[backend]()
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
        from mempalace.repair import _text_embedding_collection

        rebuilt = _text_embedding_collection(inner, False)
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
    assert user_tool_arguments(value) == {
        "content": text,
        "metadata": {"wing": "test"},
        "rows": [{INDEX_SPANS: "forged"}],
    }
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


def test_mcp_ingress_strips_all_reserved_metadata_before_dispatch(monkeypatch):
    from mempalace import mcp_server as mcp

    seen = {}
    monkeypatch.setitem(
        mcp.TOOLS,
        "mempalace_add_drawer",
        {
            "input_schema": {"type": "object", "properties": {"metadata": {"type": "object"}}},
            "handler": lambda **kwargs: seen.update(kwargs) or {"success": True},
        },
    )
    monkeypatch.setattr(mcp, "_mcp_tool_preflight_refusal", lambda *args, **kwargs: None)
    mcp.call_tool(
        1,
        {
            "name": "mempalace_add_drawer",
            "arguments": {
                **dict.fromkeys(RESERVED_METADATA, "forged"),
                "metadata": {**dict.fromkeys(RESERVED_METADATA, "forged"), "wing": "test"},
            },
        },
    )
    assert seen == {"metadata": {"wing": "test"}}


def test_cli_hub_forwarding_strips_all_reserved_metadata_before_serialization(monkeypatch):
    from mempalace.hub_client import HubClient

    client = object.__new__(HubClient)
    seen = []
    monkeypatch.setattr(client, "post", lambda request, **kwargs: seen.append(request))
    client.call_tool(
        "mempalace_add_drawer",
        {
            "content": "verbatim",
            **dict.fromkeys(RESERVED_METADATA, "forged"),
            "metadata": dict.fromkeys(RESERVED_METADATA, "forged"),
        },
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


def test_trimmed_exchange_maps_occurrences_without_erasing_an_invalid_run():
    from mempalace.convo_miner import chunk_exchanges

    payload = base64.b64encode(random.Random(7).randbytes(300)).decode()
    invalid = "X" + payload + "X"
    text = (
        "  > header   \ninvalid='"
        + invalid
        + "'\nvalid='"
        + payload
        + "'\n> second\nreply\n> third\nreply"
    )
    assert len(find_base64_spans(text)) == 1
    chunks = chunk_exchanges(text, chunk_size=10000, min_chunk_size=0)
    indexed = "".join(index_text(item["content"], chunk_index_metadata(item)) for item in chunks)
    assert "invalid='" + invalid + "'" in indexed
    assert "valid='[base64 binary 300 B]'" in indexed


def test_hint_projection_failure_never_blocks_verbatim_chunks(caplog):
    from copy import deepcopy

    from mempalace.stored_text import annotate_chunks

    text = corpus()
    chunks = [{"content": text[:137]}, {"content": "chunker-specific transformed text"}]
    before = deepcopy(chunks)
    assert annotate_chunks(text, chunks) is chunks
    assert chunks == before
    assert "storing chunks without hints" in caplog.text


def test_invalid_hint_warnings_are_rate_limited(caplog):
    for _ in range(30):
        assert index_text("plain document", {INDEX_SPANS: "bad json"}) == "plain document"
    assert caplog.text.count("Ignoring stale or invalid index span hint") == 1


def forged_hint(text, spans):
    import hashlib

    return {
        INDEX_SPANS: json.dumps(
            {
                "v": 1,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "spans": spans,
            }
        )
    }


def test_hint_with_spaces_cannot_erase_prose(caplog):
    text = "the secret needle word"
    hint = forged_hint(text, [[4, 18, "[base64 binary 300 B]", True]])
    assert index_text(text, hint) == text
    assert "Ignoring stale or invalid index span hint" in caplog.text


def test_deep_hint_json_cannot_crash_decoder(caplog):
    hint = "[" * 2000 + "0" + "]" * 2000
    assert index_text("plain document", {INDEX_SPANS: hint}) == "plain document"
    assert "Ignoring stale or invalid index span hint" in caplog.text


@pytest.mark.parametrize(
    "start,end,emit,accepted",
    [
        (5, 10, False, False),
        (14, 29, False, False),
        (31, 39, True, True),
        (0, 4, False, True),
    ],
)
def test_each_word_hint_pins_the_trusted_edge_contract(start, end, emit, accepted, caplog):
    # Synthetic prose: edge fragments rely on trusted whole-source provenance.
    text = "Meet Alice at Kubernetes-east; password"
    placeholder = "[base64 binary 150 B]"
    hint = forged_hint(text, [[start, end, placeholder, emit]])
    expected = text
    if accepted:
        expected = text[:start] + (placeholder if emit else "") + text[end:]
    assert index_text(text, hint) == expected
    assert ("Ignoring stale or invalid index span hint" in caplog.text) == (not accepted)


@pytest.mark.parametrize(
    "placeholder",
    [
        "[base64 binary 150 B]",
        "[base64 image/png 300 B]",
        "[base64 application/pdf 151 B]",
        f"[base64 binary {sys.maxsize} B]",
    ],
)
def test_a_single_token_chunk_can_be_a_trusted_edge_fragment(placeholder, caplog):
    hint = forged_hint("password", [[0, 8, placeholder, True]])
    assert index_text("password", hint) == placeholder
    assert "Ignoring stale or invalid index span hint" not in caplog.text


@pytest.mark.parametrize(
    "placeholder",
    [
        "[base64 binary 0 B]",
        "[base64 binary 149 B]",
        "[base64 binary -1 B]",
        "[base64 binary 0150 B]",
        f"[base64 binary {sys.maxsize + 1} B]",
        "[base64 text/plain 150 B]",
        "[base64 image/unknown 150 B]",
        "[base64 binary 150 bytes]",
    ],
)
def test_edge_hints_reject_impossible_placeholders(placeholder, caplog):
    text = "prefix password"
    hint = forged_hint(text, [[7, len(text), placeholder, True]])
    assert index_text(text, hint) == text
    assert "Ignoring stale or invalid index span hint" in caplog.text


@pytest.mark.parametrize(
    "fragment",
    [
        "two words",
        "naïve",
        "abc=def",
        "abc===",
        "abc+_",
        "abc==",
        "A" * 201,
    ],
)
def test_edge_hints_require_a_possible_fragment_of_the_declared_run(fragment, caplog):
    text = "prefix " + fragment
    hint = forged_hint(text, [[7, len(text), "[base64 binary 150 B]", True]])
    assert index_text(text, hint) == text
    assert "Ignoring stale or invalid index span hint" in caplog.text


@pytest.mark.parametrize("padding", ["=", "=="])
@pytest.mark.parametrize("emit", [False, True])
def test_forged_padding_only_spans_are_rejected(padding, emit, caplog):
    text = "x " + padding if emit else padding + " after"
    start = 2 if emit else 0
    hint = forged_hint(text, [[start, start + len(padding), "[base64 binary 151 B]", emit]])
    assert index_text(text, hint) == text
    assert "Ignoring stale or invalid index span hint" in caplog.text


@pytest.mark.parametrize(
    "text,start,end,emit",
    [
        ("naïve", 0, 2, False),
        ("naïve", 3, 5, True),
        ("wordⅣ", 0, 4, False),
        ("٢word", 1, 5, True),
        ("nai\u0308ve", 0, 3, False),
        ("nai\u0308ve", 4, 6, True),
        ("ab\u093ecd", 0, 2, False),
        ("ab\u093ecd", 3, 5, True),
        ("ab\u20ddcd", 0, 2, False),
        ("ab\u20ddcd", 3, 5, True),
        ("ab\u200ccd", 0, 2, False),
        ("ab\u200ccd", 3, 5, True),
        ("ab\u200dcd", 0, 2, False),
        ("ab\u200dcd", 3, 5, True),
    ],
)
def test_edge_hints_cannot_split_unicode_words(text, start, end, emit, caplog):
    hint = forged_hint(text, [[start, end, "[base64 binary 150 B]", emit]])
    assert index_text(text, hint) == text
    assert "Ignoring stale or invalid index span hint" in caplog.text


@pytest.mark.parametrize("form", ["NFC", "NFD"])
def test_forged_edge_hints_preserve_composed_and_decomposed_words(form):
    edge = unicodedata.normalize(form, "ï")
    text = "ab" + edge + "cd"
    for start, end, emit in [(0, 2, False), (2 + len(edge), len(text), True)]:
        hint = forged_hint(text, [[start, end, "[base64 binary 150 B]", emit]])
        assert index_text(text, hint) == text


def test_all_unicode_marks_connectors_and_formats_reject_forged_edge_hints():
    for codepoint in range(sys.maxunicode + 1):
        char = chr(codepoint)
        category = unicodedata.category(char)
        if category.startswith("M") or category in ("Pc", "Cf"):
            text = "ab" + char + "cd"
            for start, end, emit in [(0, 2, False), (3, 5, True)]:
                hint = forged_hint(text, [[start, end, "[base64 binary 150 B]", emit]])
                assert index_text(text, hint) == text, f"U+{codepoint:04X}, emit={emit}"


@pytest.mark.parametrize(
    "rule",
    [
        "continuation",
        "continuation-to-end",
        "start-complete",
        "short",
        "left-boundary",
        "right-boundary",
        "padding-boundary",
        "interior-clipping",
        "entropy",
        "alphabet",
        "length",
        "pad-bits",
        "placeholder",
    ],
)
def test_invalid_hint_spans_fall_back_to_chunk_detection(rule, caplog):
    run = base64.b64encode(random.Random(7).randbytes(151)).decode()
    placeholder = "[base64 binary 151 B]"
    before, after, emit = "before '", "' after", True
    if rule == "continuation":
        emit = False
    elif rule == "continuation-to-end":
        before, after, emit = "", "", False
    elif rule == "start-complete":
        before, run = "", run[:17]
    elif rule == "short":
        run = base64.b64encode(random.Random(7).randbytes(147)).decode()
        placeholder = "[base64 binary 147 B]"
    elif rule == "left-boundary":
        before += "X"
        after = ""  # Even an edge fragment must have maximal visible boundaries.
    elif rule == "right-boundary":
        before, after = "", "X" + after
    elif rule == "padding-boundary":
        before, after = "", "=" + after
    elif rule == "interior-clipping":
        run = run[:17]
    elif rule == "entropy":
        run = "A" * 204
    elif rule == "alphabet":
        run = run[:-4] + "+_-A"
    elif rule == "length":
        run = run[:-1]
    elif rule == "pad-bits":
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        run = run[:-3] + alphabet[alphabet.index(run[-3]) + 1] + "=="
    elif rule == "placeholder":
        placeholder = "[base64 binary 0 B]"
    text = before + run + after
    hint = forged_hint(text, [[len(before), len(before) + len(run), placeholder, emit]])
    assert index_text(text, hint) == index_text(text)
    assert "Ignoring stale or invalid index span hint" in caplog.text


def test_complete_run_at_chunk_start_keeps_valid_producer_hint(caplog):
    run = base64.b64encode(random.Random(7).randbytes(151)).decode()
    text = run + "; after needle"
    metadata = IndexSource(text).metadata(0, len(text))
    assert index_text(text, metadata) == "[base64 binary 151 B]; after needle"
    assert "Ignoring stale or invalid index span hint" not in caplog.text


def test_invalid_span_discards_entire_hint(caplog):
    text = corpus() + "short word."
    [span] = find_base64_spans(text)
    hint = forged_hint(
        text,
        [
            [span.start, span.end, span.placeholder, True],
            [len(text) - 5, len(text) - 1, "[base64 binary 0 B]", True],
        ],
    )
    assert index_text(text, hint) == index_text(text)
    assert "Ignoring stale or invalid index span hint" in caplog.text


@pytest.mark.parametrize("kind", ["unpadded", "glued", "mixed", "padding"])
def test_rejected_whole_runs_are_not_reclassified_on_chunk_fragments(kind):
    from mempalace.miner import chunk_text

    payload = base64.urlsafe_b64encode(random.Random(9).randbytes(1000)).decode()
    rejected = {
        "unpadded": payload.rstrip("="),
        "glued": payload.rstrip("=") + "wordx",
        "mixed": payload.rstrip("=") + "+/_-",
        "padding": payload + "=",
    }[kind]
    assert not find_base64_spans(rejected)
    chunks = chunk_text(rejected, "fixture.txt", chunk_size=800, chunk_overlap=0, min_chunk_size=0)
    assert any(find_base64_spans(chunk["content"]) for chunk in chunks)
    for chunk in chunks:
        meta = chunk_index_metadata(chunk)
        assert json.loads(meta[INDEX_SPANS])["spans"] == []
        assert index_text(chunk["content"], meta) == chunk["content"]


@pytest.mark.parametrize("mapping", [dict, UserDict, MappingProxyType])
def test_adapter_and_exchange_metadata_cannot_forge_hints(mapping):
    from types import SimpleNamespace

    from mempalace.convo_miner import file_conversation_exchange
    from mempalace.sources.base import DrawerRecord
    from mempalace.sources.context import PalaceContext

    writes = []
    collection = SimpleNamespace(upsert=lambda **kwargs: writes.append(kwargs))
    context = PalaceContext(
        drawer_collection=collection,
        knowledge_graph=None,
        palace_path="fixture",
        adapter_name="fixture",
        adapter_version="1",
    )
    original = {**dict.fromkeys(RESERVED_METADATA, "forged"), "tag": "keep"}
    metadata = mapping(original)
    context.upsert_drawer(
        DrawerRecord(
            content="verbatim",
            source_file="fixture.txt",
            chunk_index=0,
            metadata=metadata,
        )
    )
    file_conversation_exchange(
        collection,
        wing="test",
        room="notes",
        text="verbatim exchange",
        source_file="fixture.txt",
        agent="fixture",
        extra_metadata={**dict.fromkeys(RESERVED_METADATA, "forged"), "tag": "keep"},
    )
    assert len(writes) == 2
    assert dict(metadata) == original
    assert writes[0]["metadatas"][0]["tag"] == "keep"
    assert all(not RESERVED_METADATA.intersection(write["metadatas"][0]) for write in writes)
    assert writes[1]["metadatas"][0]["tag"] == "keep"


def test_hint_change_reembeds_pre_policy_vectors_once(monkeypatch):
    from types import SimpleNamespace

    from mempalace import palace
    from mempalace.backends.base import GetResult

    text = corpus()
    old = {"source_file": "fixture.txt"}
    new = IndexSource(text).metadata(0, len(text), old)
    col = SimpleNamespace(
        get=lambda **kwargs: GetResult(ids=["id"], documents=[text], metadatas=[old]),
        upsert_merges_metadata=False,
    )
    vector_reads = []
    monkeypatch.setattr(palace, "_stored_vectors", lambda *args: vector_reads.append(args) or {})
    rows, reuse, vectors = palace._split_unchanged(col, [("id", text, new)])
    assert rows == [("id", text, new)] and reuse == [] and vectors == []
    assert vector_reads[0][1] == []
    old.update(new)
    monkeypatch.setattr(palace, "_stored_vectors", lambda *args: {"id": [1.0, 0.0, 0.0]})
    monkeypatch.setattr("mempalace.embedding.probe_dimension", lambda: 3)
    rows, reuse, vectors = palace._split_unchanged(col, [("id", text, new)])
    assert rows == [] and reuse == [("id", text, new)] and vectors == [[1.0, 0.0, 0.0]]


@pytest.mark.parametrize("backend", ["chroma", "sqlite_exact"])
@pytest.mark.parametrize("width", [800, 10000])
def test_drawer_updates_tombstone_old_hints(monkeypatch, config, palace_path, kg, backend, width):
    from _mcp_server_helpers import _patch_mcp_server

    from mempalace import mcp_server as mcp
    from mempalace.backends import PalaceRef, get_backend

    _patch_mcp_server(monkeypatch, config, kg)
    monkeypatch.setattr(type(config), "chunk_size", property(lambda self: width))
    inner = get_backend(backend).get_collection(
        palace=PalaceRef(id=palace_path, local_path=palace_path),
        collection_name="mempalace_drawers",
        create=True,
    )
    col = ew.EmbeddingCollection(inner)
    recorder = Recorder(dimension=384)
    monkeypatch.setattr(ew, "_get_document_embedder", lambda: recorder)
    monkeypatch.setattr("mempalace.embedding.configured_embedding_function", lambda: recorder)
    monkeypatch.setattr(mcp, "_get_collection", lambda **kwargs: col)
    # First chunk is plain, so cleanup cannot rely on its prior metadata having a hint.
    text = "plain prefix. " * 70 + corpus()
    added = mcp.tool_add_drawer(wing="test", room="notes", content=text)
    assert added.get("success"), added
    plain = "plain replacement. " * 100
    updated = mcp.tool_update_drawer(added["drawer_id"], content=plain)
    assert updated.get("success"), updated
    rows = col.get(include=["documents", "metadatas"])
    assert all(meta.get(INDEX_SPANS) is None for meta in rows.metadatas)
    assert "".join(rows.documents) == plain


def test_event_and_artifact_metadata_remain_verbatim_at_client_boundaries():
    from mempalace import mcp_server as mcp
    from mempalace.stored_text import FLAG, KEYWORD_ONLY, dumps_client

    metadata = {
        INDEX_SPANS: "user JSON",
        FLAG: "literal",
        KEYWORD_ONLY: "user value",
        "nested": {INDEX_SPANS: "also literal"},
    }
    for name in ("mempalace_event_append", "mempalace_artifact_put", "mempalace_patch_submit"):
        assert name in mcp.TOOLS
        arguments = {"metadata": metadata, "content": "verbatim"}
        assert user_tool_arguments(arguments, tool_name=name) == arguments
        result = mcp._tool_call_response(1, arguments, tool_name=name)
        assert json.loads(result["result"]["content"][0]["text"]) == arguments
        assert json.loads(dumps_client(arguments)) == arguments
    cleaned = user_tool_arguments({"metadatas": (metadata,)}, tool_name="mempalace_add_drawer")
    assert cleaned == {"metadatas": ({"nested": {INDEX_SPANS: "also literal"}},)}


@pytest.mark.parametrize("chunker", ["project", "exchange", "general"])
@given(
    size=st.one_of(st.sampled_from([150, 151, 152, 153]), st.integers(200, 1800)),
    width=st.one_of(st.integers(1, 12), st.integers(37, 850)),
    kind=st.sampled_from(["valid", "unpadded", "glued", "mixed", "padding", "identifier"]),
    prefix=st.sampled_from(
        ["Ω 🧭", "lone\udcff byte", "NUL\0value", "line\u2028separator", "CRLF\r\ntext"]
    ),
    edges=st.sampled_from(
        [
            ("'", "'"),
            (" ", "\t"),
            ("\n", "\r\n"),
            ("=", ";"),
            ("key=", ";"),
            ("Ω", "é"),
            ("cafe\u0301", ""),
            ("\u0301", "\u20dd"),
            ("\u200c", "\u200d"),
            ("٢", "Ⅳ"),
            ("…", "🧭"),
        ]
    ),
)
@settings(max_examples=200, deadline=None)
@example(size=150, width=1, kind="valid", prefix="Ω", edges=("=", "\t"))
@example(size=151, width=2, kind="valid", prefix="NUL\0value", edges=("key=", ";"))
@example(size=152, width=3, kind="valid", prefix="Ω", edges=("Ω", "é"))
@example(size=153, width=12, kind="unpadded", prefix="Ω", edges=("…", "🧭"))
@example(size=150, width=7, kind="valid", prefix="Ω", edges=("٢", "Ⅳ"))
def test_real_chunkers_match_whole_content_oracle(chunker, size, width, kind, prefix, edges):
    with patch("mempalace.stored_text._warn_index_hint") as warn:
        import re
        from bisect import bisect_left

        payload = base64.urlsafe_b64encode(random.Random(size).randbytes(size)).decode()
        run = {
            "valid": payload,
            "unpadded": payload.rstrip("="),
            "glued": "word" + payload.rstrip("=") + "tailx",
            "mixed": payload.rstrip("=") + "+/_-",
            "padding": payload + "===",
            "identifier": "abcdef0123456789" * 30,
        }[kind]
        text = (
            f"  > {prefix} first   \r\nBefore {edges[0]}{run}{edges[1]} after needle."
            "\r\n> second\nreply\n> third\nreply"
        )
        spans = find_base64_spans(text)
        if chunker == "project":
            from mempalace.miner import chunk_text

            chunks = chunk_text(
                text, "fixture.txt", chunk_size=width, chunk_overlap=width // 5, min_chunk_size=0
            )
            view, ranges, cursor = text, [], 0
            for chunk in chunks:
                start = text.find(chunk["content"], cursor)
                assert start >= 0
                ranges.append((start, start + len(chunk["content"])))
                cursor = start + 1
            projected = [(s.start, s.end, s.placeholder) for s in spans]
        else:
            if chunker == "exchange":
                from mempalace.convo_miner import chunk_exchanges

                chunks = chunk_exchanges(text, chunk_size=width, min_chunk_size=0)
            else:
                from mempalace.general_extractor import extract_memories

                chunks = extract_memories(text, chunk_size=width)
            view = "".join(c["content"] for c in chunks)
            # Independent oracle: map non-whitespace character ordinals using
            # small fixture arrays, then project only original validated spans.
            source_positions = [i for i, c in enumerate(text) if not c.isspace()]
            view_positions = [i for i, c in enumerate(view) if not c.isspace()]
            assert "".join(text[i] for i in source_positions) == "".join(
                view[i] for i in view_positions
            )
            projected = []
            for span in spans:
                first = bisect_left(source_positions, span.start)
                last = bisect_left(source_positions, span.end) - 1
                projected.append(
                    (view_positions[first], view_positions[last] + 1, span.placeholder)
                )
            ranges, start = [], 0
            for chunk in chunks:
                ranges.append((start, start + len(chunk["content"])))
                start += len(chunk["content"])
        for chunk, (start, end) in zip(chunks, ranges):
            expected, cursor = [], start
            for left, right, placeholder in projected:
                if left >= end or right <= start:
                    continue
                # Padding-only intersections carry no searchable payload and stay literal.
                if not view[max(start, left) : min(end, right)].strip("="):
                    continue
                expected.append(view[cursor : max(start, left)])
                if start <= left < end or left <= start < end <= right:
                    expected.append(placeholder)
                cursor = min(end, right)
            expected.append(view[cursor:end])
            expected_text = re.sub(r"[\x00\ud800-\udfff]", " ", "".join(expected))
            assert (
                index_text(chunk["content"], chunk_index_metadata(chunk), stored_form=False)
                == expected_text
            )
    warn.assert_not_called()


def test_hybrid_keyword_scores_use_hinted_index_view_and_preserve_text(monkeypatch):
    from mempalace import searcher

    text = corpus()
    source = IndexSource(text)
    # A locally undecodable fragment exercises hint propagation into reranking.
    start = source.spans[0].start + 17
    doc = text[start : start + 137]
    meta = source.metadata(start, start + len(doc))
    captured = []
    monkeypatch.setattr(
        searcher,
        "bm25_scores",
        lambda query, docs, **kwargs: captured.extend(docs) or [1.0] * len(docs),
    )
    rows = [{"text": doc, "distance": 0.1, "_index_metadata": meta}]
    ranked = searcher._hybrid_rank(rows, "needle")
    assert captured == [index_text(doc, meta)]
    assert ranked[0]["text"] == doc


def test_no_fts_keyword_scores_use_index_view(tmp_path, monkeypatch):
    from mempalace.backends import sqlite_exact

    backend = sqlite_exact.SQLiteExactBackend()
    col = backend.create_collection(str(tmp_path), "test_drawers")
    text = corpus()
    meta = IndexSource(text).metadata(0, len(text))
    col.add(ids=["id"], documents=[text], metadatas=[meta], embeddings=[[1.0, 0.0, 0.0]])
    captured = []
    monkeypatch.setattr(col, "_lexical_search_fts", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sqlite_exact, "bm25_scores", lambda query, docs: captured.extend(docs) or [1.0] * len(docs)
    )
    try:
        assert col.lexical_search(query="needle").hits[0].document == text
        assert captured == [index_text(text, meta)]
    finally:
        backend.close()
