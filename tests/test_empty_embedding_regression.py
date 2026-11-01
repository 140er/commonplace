"""Nonempty source pieces must remain embeddable without repeating keyword hints."""

import base64
import random
from types import SimpleNamespace

import pytest

from mempalace.backends import embedding_wrapper as ew
from mempalace.stored_text import INDEX_SPANS, KEYWORD_ONLY, IndexSource, index_text
from tests.test_placeholder_embeddings import Recorder

_REAL_EMBED_TEXTS = ew._embed_texts
_EMPTY_INPUT_ERROR = "strict embedder rejected exact empty input"


class StrictRecorder(Recorder):
    def __call__(self, input):
        vectors = super().__call__(input)
        if "" in input:
            raise ValueError(_EMPTY_INPUT_ERROR)
        return vectors


@pytest.fixture
def strict_recorder(monkeypatch, record_property):
    recorder = StrictRecorder()
    monkeypatch.setattr(ew, "_embed_texts", _REAL_EMBED_TEXTS)
    monkeypatch.setattr(ew, "_get_document_embedder", lambda: recorder)
    monkeypatch.setattr("mempalace.embedding.configured_embedding_function", lambda: recorder)
    yield recorder
    record_property("embedding_inputs", len(recorder.seen))
    record_property("empty_inputs", recorder.seen.count(""))


@pytest.fixture(params=["compact", "sqlite_exact", "chroma"])
def strict_writer(request, tmp_path, monkeypatch, strict_recorder, record_property):
    from mempalace import mcp_server as server
    from mempalace.backends.chroma import ChromaBackend
    from mempalace.backends.compact import CompactBackend
    from mempalace.backends.sqlite_exact import SQLiteExactBackend

    backend = {
        "compact": CompactBackend,
        "sqlite_exact": SQLiteExactBackend,
        "chroma": ChromaBackend,
    }[request.param]()
    col = ew.EmbeddingCollection(backend.create_collection(str(tmp_path), "drawers"))
    monkeypatch.setattr(
        server,
        "_config",
        SimpleNamespace(chunk_size=800, palace_path=str(tmp_path), collection_name="drawers"),
    )
    monkeypatch.setattr(server, "_is_chroma_backend", lambda: False)
    monkeypatch.setattr(server, "_palace_known_wings", lambda: frozenset({"private_threads"}))
    monkeypatch.setattr(server, "_get_collection", lambda **kwargs: col)
    monkeypatch.setattr(server, "_wal_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_wal_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_invalidate_overview_caches", lambda: None)
    try:
        yield server, col, strict_recorder, request.param
    finally:
        record_property("stored_records", col.count())
        backend.close()


def test_embed_texts_keeps_covered_800_character_pieces_nonempty(strict_recorder):
    run = base64.b64encode(random.Random(7).randbytes(1800)).decode()
    text = 'Before screenshot="' + run + '"; after needle.\n'
    source = IndexSource(text)
    docs = [text[start : start + 800] for start in range(0, len(text), 800)]
    metas = [
        source.metadata(start, start + len(doc))
        for start, doc in zip(range(0, len(text), 800), docs)
    ]
    assert [index_text(doc, meta) for doc, meta in zip(docs, metas)] == [
        'Before screenshot="[base64 binary 1800 B]',
        "",
        "",
        '"; after needle.\n',
    ]
    vectors = ew._embed_texts(docs, metadatas=metas)
    assert vectors == [[1.0, 0.0, 0.0]] * 4
    assert strict_recorder.seen == [
        'Before screenshot="[base64 binary 1800 B]',
        "[base64 binary 1800 B]",
        "[base64 binary 1800 B]",
        '"; after needle.\n',
    ]
    assert "" not in strict_recorder.seen


def test_public_chunked_write_keeps_embedding_inputs_nonempty(strict_writer, record_property):
    server, col, recorder, _ = strict_writer
    run = base64.b64encode(random.Random(7).randbytes(1800)).decode()
    assert len(run) == 2400
    content = 'Before screenshot="' + run + '"; after needle.\n'
    result = server.tool_add_drawer(wing="chat", room="notes", content=content)
    assert result.get("success"), result
    rows = col.get(ids=result["chunk_ids"], include=["documents", "metadatas"])
    assert "".join(rows.documents) == content
    assert [len(doc) for doc in rows.documents] == [800, 800, 800, 36]
    assert recorder.seen == [
        'Before screenshot="[base64 binary 1800 B]',
        "[base64 binary 1800 B]",
        "[base64 binary 1800 B]",
        '"; after needle.\n',
    ]
    assert "" not in recorder.seen
    views = [index_text(doc, meta) for doc, meta in zip(rows.documents, rows.metadatas)]
    assert "".join(views) == 'Before screenshot="[base64 binary 1800 B]"; after needle.\n'
    record_property(
        "keyword_placeholders", sum(view.count("[base64 binary 1800 B]") for view in views)
    )


@pytest.mark.parametrize("payload_bytes", [1950, 4500])
@pytest.mark.parametrize(
    "role,kind,message_class",
    [
        ("user", "message", "conversation"),
        ("assistant", "tool_call", "tool_call"),
        ("tool", "tool_result", "tool_output"),
    ],
)
def test_mirror_progress_with_strict_embedder(
    strict_writer, tmp_path, record_property, payload_bytes, role, kind, message_class
):
    from commonplace.handoff.models import ThreadMessage
    from commonplace.handoff.palace_mirror import class_units, mirror_pending
    from commonplace.handoff.thread_store_v2 import ThreadStoreV2

    server, col, recorder, backend = strict_writer
    payload = base64.b64encode(random.Random(39).randbytes(payload_bytes)).decode()
    text = "preamble\n" + payload + "\nexact suffix"
    message = ThreadMessage(0, role, kind, text, "raw")
    units = class_units([message])
    assert len(units) == (1 if payload_bytes == 1950 else 2)
    assert all(unit.message_class == message_class for unit in units)
    if payload_bytes == 4500:
        assert index_text(units[1].text, {INDEX_SPANS: units[1].index_spans}) == "\nexact suffix"

    def call(principal, name, args):
        result = (
            server.write_mirror_units(**args)
            if name == "write_mirror_units"
            else server.TOOLS[name]["handler"](**args)
        )
        assert result.get("success", True), result
        return result

    store = ThreadStoreV2(str(tmp_path / "threads.v2.sqlite3"))
    stored = store.store("workspace", "source", [message], actor="actor")
    try:
        ids = mirror_pending(call, None, stored, store)
        assert store.mirror_progress(stored.id) == (0, 0)
        assert len(ids) == len(units)
        documents, views, metadatas = [], [], []
        for drawer_id, unit in zip(ids, units):
            rows = col.get(
                where={"parent_drawer_id": drawer_id}, include=["documents", "metadatas"]
            )
            pieces = sorted(
                zip(rows.documents, rows.metadatas), key=lambda row: row[1]["chunk_index"]
            )
            assert "".join(doc for doc, _ in pieces) == unit.text
            documents.extend(doc for doc, _ in pieces)
            metadatas.extend(meta for _, meta in pieces)
            views.extend(index_text(doc, meta) for doc, meta in pieces)
        assert "".join(documents) == f"[0] {role} {kind}: {text}"
        placeholder = f"[base64 binary {payload_bytes} B]"
        assert "".join(views) == f"[0] {role} {kind}: preamble\n{placeholder}\nexact suffix"
        assert sum(view.count(placeholder) for view in views) == 1
        record_property("keyword_placeholders", 1)
        if backend == "compact" and message_class == "tool_output":
            assert recorder.seen == []
            assert all(meta[KEYWORD_ONLY] is True for meta in metadatas)
        else:
            assert len(recorder.seen) == len(documents)
            assert placeholder in recorder.seen
            assert "" not in recorder.seen
            assert all(KEYWORD_ONLY not in meta for meta in metadatas)
        assert mirror_pending(call, None, stored, store) == []
    finally:
        record_property("mirror_progress", store.mirror_progress(stored.id))
        store.close()
