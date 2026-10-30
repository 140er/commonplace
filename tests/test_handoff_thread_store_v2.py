"""Parser-oracle losslessness, v1 conformance, and verified migration."""

import base64
import hashlib
import json
import os
import sqlite3
from dataclasses import replace
from unittest.mock import patch

import pytest
from hypothesis import given, settings, strategies as st

from commonplace.handoff.thread_store import (
    ThreadConflict,
    ThreadStore,
    ThreadStoreV2,
    canonical_sha256,
    chunk_messages,
)
from commonplace.handoff.thread_store_migrate import MigrationMismatch, migrate_v1
from commonplace.handoff.thread_store_v2_schema import APPLICATION_ID, FILENAME, USER_VERSION
from commonplace.handoff.transcripts import parse


def transcript(fmt, text):
    blocks = [
        {"type": "text", "text": text},
        {"type": "tool_use", "id": "call-1", "name": "read", "input": {"path": "sample.txt"}},
        {"type": "thinking", "thinking": "reasoning"},
    ]
    result = {"type": "tool_result", "tool_use_id": "call-1", "content": "result " + text}
    if fmt == "messages":
        return json.dumps(
            [
                {"role": "assistant", "content": blocks, "timestamp": "2026-01-01"},
                {"role": "user", "content": [result, {"type": "text", "text": "tail"}]},
            ],
            ensure_ascii=False,
        )
    if fmt == "claude-code":
        records = [
            {"type": "assistant", "message": {"role": "assistant", "content": blocks}},
            {
                "type": "user",
                "message": {"role": "user", "content": [result, {"type": "text", "text": "tail"}]},
            },
        ]
    else:
        records = [
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "assistant", "content": blocks},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": "result " + text,
                },
            },
        ]
    return "\r\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\r\n"


@pytest.fixture
def store(tmp_path):
    value = ThreadStoreV2(str(tmp_path / FILENAME))
    yield value
    value.close()


@pytest.mark.parametrize("fmt", ["claude-code", "codex", "messages"])
@pytest.mark.parametrize(
    "text",
    [
        "  café 🧠 e\u0301\n\x00trailing  ",
        "lone \ud800 and \udcff",
        "needle " * 2500,
        base64.b64encode(bytes(range(256)) * 4).decode() + " " + "A" * 257 + "===",
    ],
)
def test_parser_is_the_oracle(store, fmt, text):
    expected = parse(transcript(fmt, text), fmt)
    thread = store.store("workspace", "source", expected, actor="actor")
    assert store.messages(thread.id) == expected
    assert store.chunks(thread.id) == chunk_messages(expected)
    assert thread.sha256 == canonical_sha256(expected)
    assert store.store("workspace", "source", expected, actor="other") == thread
    assert store.messages(thread.id, 1, 2) == expected[1:3]
    assert store.messages(thread.id, -1) == []
    assert store.messages(thread.id, 2, 1) == []
    assert store.messages("missing") == []
    assert store.chunks("missing") == []
    assert store._conn.execute("SELECT count(*) FROM line").fetchone()[0] < len(expected)


def test_reopen_and_identity(tmp_path):
    path = tmp_path / FILENAME
    items = parse(transcript("messages", "needle\x00surrogate\ud800"))
    value = ThreadStoreV2(str(path))
    thread = value.store("workspace", "source", items, actor="actor")
    value.close()
    value = ThreadStoreV2(str(path))
    try:
        assert value.messages(thread.id) == items
        assert value._conn.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert value._conn.execute("PRAGMA user_version").fetchone()[0] == USER_VERSION
        assert value.index_version == 0
        if os.name != "nt":
            assert path.stat().st_mode & 0o777 == 0o600
    finally:
        value.close()
    with pytest.raises(ValueError, match="filename"):
        ThreadStoreV2(str(tmp_path / "threads.sqlite3"))
    wrong = tmp_path / "wrong" / FILENAME
    wrong.parent.mkdir()
    conn = sqlite3.connect(wrong)
    conn.execute("CREATE TABLE unrelated (id)")
    conn.close()
    with pytest.raises(ValueError, match="supported"):
        ThreadStoreV2(str(wrong))


def test_canonical_digest_compatibility():
    items = parse(transcript("messages", "café 🧠\x00"))
    canonical = [(m.seq, m.role, m.kind, m.name, m.call_id, m.text, m.raw) for m in items]
    assert (
        canonical_sha256(items)
        == hashlib.sha256(
            json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )
    assert canonical_sha256([replace(items[0], text="\ud800")])


def simple(texts):
    return parse(json.dumps([{"role": "user", "content": text} for text in texts]))


def test_append_boundaries_hash_check_and_runs(store, monkeypatch):
    items = simple(["a" * 30000, "b" * 30000, "c" * 30000])
    thread = store.store("workspace", "source", items[:2], actor="actor")
    original = store.chunks(thread.id)
    with monkeypatch.context() as context:
        context.setattr(
            store, "_frame", lambda *args: pytest.fail("prefix validation decompressed data")
        )
        assert store.append(thread.id, items[:2]) == thread
        with pytest.raises(ThreadConflict):
            store.append(thread.id, [replace(items[0], text="changed"), *items[1:]])
    before = tuple(store._conn.execute("SELECT * FROM block WHERE idx=0").fetchone())
    store._conn.execute(
        "CREATE TEMP TRIGGER unchanged_tail BEFORE UPDATE ON block WHEN old.idx=0 BEGIN SELECT RAISE(ABORT, 'unchanged block rewritten'); END"
    )
    updated = store.append(thread.id, items)
    store._conn.execute("DROP TRIGGER unchanged_tail")
    assert store.messages(thread.id) == items
    assert store.chunks(thread.id)[: len(original)] == original
    assert tuple(store._conn.execute("SELECT * FROM block WHERE idx=0").fetchone()) == before
    assert store.append(thread.id, items[:1]) == updated
    assert store.append(thread.id, []) == updated
    with pytest.raises(ThreadConflict):
        store.append(thread.id, [replace(items[0], text="changed")])
    with pytest.raises(ValueError):
        store.append("missing", items)
    with pytest.raises(ValueError):
        store.append(thread.id, [replace(items[0], seq=1)])
    with pytest.raises(ValueError):
        store.store("workspace", "empty", [], actor="actor")
    with pytest.raises(ThreadConflict):
        store.store("workspace", "source", simple(["different"]), actor="actor")


def test_per_turn_appends_extend_last_block_without_rewriting_earlier_blocks(store):
    items = simple([f"turn {i} needle" for i in range(100)])
    thread = store.store("workspace", "source", items[:1], actor="actor")
    for end in range(2, len(items) + 1):
        store.append(thread.id, items[:end])
    assert store._conn.execute("SELECT count(*) FROM block").fetchone()[0] == 1
    assert len(store.chunks(thread.id)) == 100
    assert store.messages(thread.id) == items
    # Appending another parser message from the same raw line extends the run.
    multiple = parse(transcript("messages", "equal raw"))
    second = store.store("workspace", "multi", multiple[:1], actor="actor")
    store.append(second.id, multiple[:2])
    assert (
        store._conn.execute("SELECT count(*) FROM line WHERE thread_id=?", (second.id,)).fetchone()[
            0
        ]
        == 1
    )
    assert store.messages(second.id) == multiple[:2]


def test_blob_dedup_noncanonical_and_kind_separation(store):
    text_blob = b"A" * 257  # shaped like base64 but cannot be decoded
    encoded = base64.b64encode(text_blob).decode()
    text = encoded + " " + text_blob.decode() + " " + encoded
    items = simple([text, text])
    thread = store.store("workspace", "source", items, actor="actor")
    assert store.messages(thread.id) == items
    rows = store._conn.execute("SELECT * FROM blob").fetchall()
    assert {(row["kind"], row["data"]) for row in rows} == {
        ("text", text_blob),
        ("decoded", text_blob),
    }
    assert len({row["key"] for row in rows}) == 2
    for row in rows:
        assert row["key"] == hashlib.sha256(row["kind"].encode() + row["data"]).digest()


def test_every_message_field_and_marker_looking_text(store):
    item = simple(["\0\ud800"])[0]
    item = replace(
        item, name="\ud800", call_id="\udfff\0", at="\ud800", raw="\x01mpesc: marker \0\udcff"
    )
    thread = store.store("workspace", "source", [item], actor="actor")
    assert store.messages(thread.id) == [item]
    store.put_doc("doc", "quoted \ud800\0")
    assert store.doc("doc") == "quoted \ud800\0"
    assert store.doc("missing") == ""
    store.advance_fact_cursor(thread.id, 1, 1)
    assert store.fact_cursor(thread.id, 1) == 1
    assert store.fact_cursor(thread.id, 2) == 0
    with pytest.raises(ValueError):
        store.fact_cursor(thread.id, 0)


def test_nul_literal_search_and_contentless_join(store, monkeypatch):
    first = store.store("workspace", "first", simple(["foo\0bar needle"]), actor="actor")
    second = store.store("workspace", "second", simple(["foo\0baz needle"]), actor="actor")
    assert [h.stored_thread_id for h in store.search([first.id, second.id], "FOO\0BAR")] == [
        first.id
    ]
    assert store.search([second.id], "foo\0bar") == []
    rows = store._conn.execute("SELECT rowid, text FROM chunk_fts").fetchall()
    assert all(row["text"] is None for row in rows)
    assert {row["rowid"] for row in rows} == {
        row[0] for row in store._conn.execute("SELECT rowid FROM chunk")
    }
    original = store._frame

    def text_only(block, frame):
        assert frame == "text"
        return original(block, frame)

    monkeypatch.setattr(store, "_frame", text_only)
    assert len(store.search([first.id, second.id], "needle")) == 2
    for query in ['"', "AND", "*", "NEAR(", "🧠", "", "user:foo OR text:secret", "x" * 60000]:
        store.search([first.id], query)
    before = store.chunks(first.id)
    store.reindex()
    assert store.chunks(first.id) == before
    with pytest.raises(ValueError, match="Unsupported"):
        store.reindex(99)


@settings(max_examples=35, deadline=None)
@given(
    st.lists(
        st.text(alphabet="abcdef xyzé🧠\n\t", min_size=0, max_size=150), min_size=1, max_size=12
    ),
    st.sampled_from(["a", "b", "xyz", "é", "absent", "a b"]),
)
def test_v1_v2_search_property(texts, query):
    old, new = ThreadStore(":memory:"), ThreadStoreV2(":memory:")
    try:
        ids = []
        for index, text in enumerate(texts):
            items = simple([text])
            with patch("secrets.token_hex", return_value=f"{index:016x}"):
                first = old.store("workspace", str(index), items, actor="actor")
                second = new.store("workspace", str(index), items, actor="actor")
            assert first.id == second.id
            ids.append(first.id)
            assert old.chunks(first.id) == new.chunks(second.id)
        assert old.search(ids, query) == new.search(ids, query)
    finally:
        old.close()
        new.close()


def make_v1(path):
    old = ThreadStore(str(path))
    items = parse(transcript("claude-code", "needle " * 3000))
    root = old.store("workspace", "root", items[:1], actor="actor")
    old.append(root.id, items[:3])
    old.append(root.id, items)
    child = old.store(
        "workspace", "child", simple(["child\0needle"]), actor="other", parent_stored_id=root.id
    )
    old.put_doc("handoff", "verbatim\0quoted")
    old.advance_fact_cursor(root.id, 2, 2)
    return old, root, child


def test_row_copy_migration_preserves_all_rows_and_boundaries(tmp_path):
    path = tmp_path / "threads.sqlite3"
    old, root, child = make_v1(path)
    before = path.read_bytes()
    assert migrate_v1(str(path)) == 2
    new = ThreadStoreV2(str(tmp_path / FILENAME))
    try:
        for thread in (root, child):
            assert new.get(thread.id) == old.get(thread.id)
            assert new.messages(thread.id) == old.messages(thread.id)
            assert new.chunks(thread.id) == old.chunks(thread.id)
            assert new.parent_of(thread.id) == old.parent_of(thread.id)
        assert new.doc("handoff") == old.doc("handoff")
        assert new.fact_cursor(root.id, 2) == 2
        assert new.search([root.id, child.id], "needle") == old.search(
            [root.id, child.id], "needle"
        )
        assert path.read_bytes() == before
        with pytest.raises(ValueError, match="empty"):
            migrate_v1(str(path))
    finally:
        old.close()
        new.close()


@pytest.mark.parametrize("damage", ["text", "sha256", "at", "seq", "chunk_count"])
def test_migration_mismatch_aborts_workspace(tmp_path, damage):
    path = tmp_path / "threads.sqlite3"
    old, root, _ = make_v1(path)
    if damage == "text":
        old._conn.execute(
            "UPDATE chunks SET text='corrupt' WHERE thread_id=? AND idx=0", (root.id,)
        )
    elif damage in {"sha256", "chunk_count"}:
        old._conn.execute(
            f"UPDATE threads SET {damage}=? WHERE id=?",
            ("bad" if damage == "sha256" else 99, root.id),
        )
    elif damage == "at":
        # 'at' is intentionally outside the v1 digest; it must still copy.
        old._conn.execute("UPDATE messages SET at='changed' WHERE thread_id=?", (root.id,))
        assert migrate_v1(str(path)) == 2
        new = ThreadStoreV2(str(tmp_path / FILENAME))
        assert new.messages(root.id) == old.messages(root.id)
        new.close()
        old.close()
        return
    else:
        old._conn.execute("UPDATE messages SET seq=99 WHERE thread_id=? AND seq=0", (root.id,))
    old.close()
    with pytest.raises(MigrationMismatch):
        migrate_v1(str(path))
    new = ThreadStoreV2(str(tmp_path / FILENAME))
    try:
        for table in [
            "threads",
            "block",
            "line",
            "message",
            "chunk",
            "blob",
            "fact_extraction",
            "handoff_docs",
        ]:
            assert new._conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    finally:
        new.close()


@pytest.mark.parametrize("operation", ["store", "append"])
def test_block_readback_failure_is_atomic(store, monkeypatch, operation):
    items = simple(["first", "second"])
    root = store.store("workspace", "root", items[:1], actor="actor")
    before = {
        table: [tuple(row) for row in store._conn.execute(f"SELECT * FROM {table}")]
        for table in [
            "threads",
            "block",
            "line",
            "message",
            "blob",
            "chunk",
            "chunk_fts",
            "index_meta",
        ]
    }
    with monkeypatch.context() as context:
        original = store._frame
        calls = []

        def corrupt(block, frame):
            calls.append(frame)
            values = original(block, frame)
            # An append reads old frames before it writes; fail on verification.
            return values + ["corrupt"] if operation == "store" or len(calls) > 2 else values

        context.setattr(store, "_frame", corrupt)
        with pytest.raises(ValueError):
            if operation == "store":
                store.store("workspace", "new", items, actor="actor")
            else:
                store.append(root.id, items)
    assert not store._conn.in_transaction
    for table, rows in before.items():
        assert [tuple(row) for row in store._conn.execute(f"SELECT * FROM {table}")] == rows
    assert store.messages(root.id) == items[:1]


def test_dictionary_training_own_frames_and_immutability(store):
    assert store.train_dictionaries() == []
    # Varied synthetic prose, not a base64-shaped run, supplies workspace samples.
    texts = [" ".join(f"token-{i}-{j} word-{j % 17}" for j in range(200)) for i in range(100)]
    thread = store.store("workspace", "source", simple(texts), actor="actor")
    ids = store.train_dictionaries()
    assert len(ids) == 2
    store.store("workspace", "next", simple(["next dictionary-backed turn"]), actor="actor")
    for dictionary_id in ids:
        assert store._conn.execute(
            "SELECT 1 FROM block WHERE raw_dict=? OR text_dict=?", (dictionary_id, dictionary_id)
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store._conn.execute("UPDATE dict SET data=x'00' WHERE id=?", (dictionary_id,))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store._conn.execute("DELETE FROM dict WHERE id=?", (dictionary_id,))
    assert store.messages(thread.id) == simple(texts)
    with pytest.raises(sqlite3.IntegrityError, match="last"):
        store._conn.execute("UPDATE block SET zraw=x'00' WHERE thread_id=? AND idx=0", (thread.id,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store._conn.execute("DELETE FROM block WHERE thread_id=?", (thread.id,))


def test_fts_unavailable_literal_fallback_and_later_reindex(tmp_path, monkeypatch):
    original = sqlite3.connect

    class NoFts(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith("CREATE VIRTUAL TABLE"):
                raise sqlite3.OperationalError("no such module: fts5")
            return super().execute(sql, parameters)

    path = tmp_path / FILENAME
    with monkeypatch.context() as context:
        context.setattr(sqlite3, "connect", lambda *a, **kw: original(*a, factory=NoFts, **kw))
        value = ThreadStoreV2(str(path))
        try:
            assert not value._fts_enabled
            thread = value.store(
                "workspace", "source", simple(["needle % _ \\ foo\0bar"]), actor="actor"
            )
            assert value.search([thread.id], "NEEDLE")
            assert value.search([thread.id], "foo\0bar")
            assert value.search([thread.id], "%")
            assert value.search([thread.id], "x" * 60000) == []
        finally:
            value.close()
    value = ThreadStoreV2(str(path))
    try:
        assert value.search([thread.id], "needle")
        assert value._conn.execute("SELECT count(*) FROM chunk_fts").fetchone()[0] == 1
        value.reindex()
        assert value.search([thread.id], "needle")
    finally:
        value.close()


def test_line_runs_do_not_dedup_nonconsecutive_raw(store):
    items = simple(["first", "second", "third"])
    items[2] = replace(items[2], raw=items[0].raw)
    thread = store.store("workspace", "source", items, actor="actor")
    assert store._conn.execute("SELECT count(*) FROM line").fetchone()[0] == 3
    assert store.messages(thread.id) == items


def test_separate_connections_concurrent_append(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / FILENAME
    first, second = ThreadStoreV2(str(path)), ThreadStoreV2(str(path))
    try:
        items = simple([f"turn {i}" for i in range(10)])
        thread = first.store("workspace", "source", items[:1], actor="actor")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(value.append, thread.id, items) for value in (first, second)]
            assert futures[0].result(timeout=10) == futures[1].result(timeout=10)
        assert first.messages(thread.id) == items
        assert len(first.chunks(thread.id)) == 2
    finally:
        first.close()
        second.close()


def test_mixed_workspace_files_preserve_api_but_never_share_dictionaries(store):
    texts = [" ".join(f"token-{i}-{j} word-{j % 17}" for j in range(200)) for i in range(100)]
    first = store.store("workspace-a", "source", simple(texts), actor="actor")
    assert len(store.train_dictionaries()) == 2
    own = store.store("workspace-a", "dictionary-backed", simple(["own workspace"]), actor="actor")
    original = tuple(
        store._conn.execute(
            "SELECT raw_dict, text_dict FROM block WHERE thread_id=?", (own.id,)
        ).fetchone()
    )
    assert all(value is not None for value in original)
    second = store.store("workspace-b", "source", simple(["different workspace"]), actor="actor")
    assert store.by_source("workspace-a", "source").id == first.id
    assert store.by_source("workspace-b", "source").id == second.id
    with pytest.raises(ValueError, match="single-workspace"):
        store.train_dictionaries()
    new_frames = store._conn.execute(
        "SELECT raw_dict, text_dict FROM block WHERE thread_id=?", (second.id,)
    ).fetchone()
    assert tuple(new_frames) == (None, None)
    assert store.messages(own.id) == simple(["own workspace"])
    assert (
        tuple(
            store._conn.execute(
                "SELECT raw_dict, text_dict FROM block WHERE thread_id=?", (own.id,)
            ).fetchone()
        )
        == original
    )
    own_extended = simple(["own workspace", "extended after mixing"])
    store.append(own.id, own_extended)
    assert store.messages(own.id) == own_extended
    assert tuple(
        store._conn.execute(
            "SELECT raw_dict, text_dict FROM block WHERE thread_id=?", (own.id,)
        ).fetchone()
    ) == (None, None)
    # The dictionary stays immutable even after its final reference is replaced.
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store._conn.execute("UPDATE dict SET data=x'00' WHERE id=?", (original[0],))


def test_large_message_decodes_each_block_once_per_operation(store, monkeypatch):
    from collections import Counter

    unit = "needle large payload \0 "
    items = simple([(unit * (4 * 1024 * 1024 // len(unit) + 1))[: 4 * 1024 * 1024]])
    original = store._frame
    calls = Counter()

    def counted(block, frame):
        calls[block["first_seq"], frame] += 1
        return original(block, frame)

    monkeypatch.setattr(store, "_frame", counted)
    thread = store.store("workspace", "source", items, actor="actor")
    assert max(calls.values()) == 1
    expected = chunk_messages(items)
    for operation in (
        lambda: store.chunks(thread.id),
        lambda: store.search([thread.id], "needle"),
        lambda: store.search([thread.id], "payload \0"),
        store.reindex,
    ):
        calls.clear()
        result = operation()
        assert max(calls.values()) == 1
        assert not any(frame == "raw" for _, frame in calls)
        if result and hasattr(result[0], "index"):
            assert result == expected


def test_text_reads_do_not_fetch_raw_columns(store):
    items = simple(["needle " * 1000])
    thread = store.store("workspace", "source", items, actor="actor")
    statements = []

    def authorize(action, table, column, database, source):
        if action == sqlite3.SQLITE_READ and table == "block" and column in {"zraw", "raw_dict"}:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    store._conn.set_trace_callback(statements.append)
    store._conn.set_authorizer(authorize)
    try:
        assert store._messages(thread.id, 0, None, raw=False)[0].text == items[0].text
        assert store.chunks(thread.id) == chunk_messages(items)
        assert store.search([thread.id], "needle")
    finally:
        store._conn.set_authorizer(None)
        store._conn.set_trace_callback(None)
    block_reads = [sql.lower() for sql in statements if "from block" in sql.lower()]
    assert block_reads and all("zraw" not in sql and "select *" not in sql for sql in block_reads)
    columns = [row[1] for row in store._conn.execute("PRAGMA table_info(block)")]
    assert columns.index("ztext") < columns.index("zraw")


def test_frame_checksum_rejects_corruption_before_append(store):
    import zstandard as zstd

    items = simple(["needle payload " * 100])
    thread = store.store("workspace", "source", items, actor="actor")
    frame = store._conn.execute(
        "SELECT ztext FROM block WHERE thread_id=?", (thread.id,)
    ).fetchone()[0]
    assert zstd.get_frame_parameters(frame).has_checksum
    corrupt = frame[:-1] + bytes([frame[-1] ^ 1])
    store._conn.execute("UPDATE block SET ztext=? WHERE thread_id=?", (corrupt, thread.id))
    for operation in (
        lambda: store.messages(thread.id),
        lambda: store.append(thread.id, simple([items[0].text, "new message"])),
    ):
        with pytest.raises(zstd.ZstdError):
            operation()
    assert store.get(thread.id) == thread
    assert (
        store._conn.execute("SELECT ztext FROM block WHERE thread_id=?", (thread.id,)).fetchone()[0]
        == corrupt
    )


@pytest.mark.parametrize("mix_workspaces", [False, True])
def test_dictionary_training_does_not_block_writers_and_rechecks_workspace(
    store, tmp_path, monkeypatch, mix_workspaces
):
    import zstandard as zstd

    texts = [" ".join(f"token-{i}-{j} word-{j % 17}" for j in range(200)) for i in range(250)]
    store.store("workspace", "source", simple(texts), actor="actor")
    other = ThreadStoreV2(str(tmp_path / FILENAME))
    other._conn.execute("PRAGMA busy_timeout=50")
    original = zstd.train_dictionary
    inserted = []

    def train(size, samples):
        assert not store._conn.in_transaction
        assert sum(map(len, samples)) <= 100 * size
        if not inserted:
            inserted.append(
                other.store(
                    "other" if mix_workspaces else "workspace",
                    "concurrent",
                    simple(["concurrent write"]),
                    actor="actor",
                )
            )
        return original(size, samples)

    monkeypatch.setattr(zstd, "train_dictionary", train)
    try:
        if mix_workspaces:
            with pytest.raises(ValueError, match="single-workspace"):
                store.train_dictionaries()
            assert store._conn.execute("SELECT count(*) FROM dict").fetchone()[0] == 0
        else:
            assert len(store.train_dictionaries()) == 2
        assert other.messages(inserted[0].id) == simple(["concurrent write"])
    finally:
        other.close()


def test_training_sample_excludes_blob_keys():
    from commonplace.handoff import thread_frames

    blobs = {}
    text = "prefix " + base64.b64encode(bytes(range(256)) * 2).decode() + " suffix"
    frame = thread_frames.encode_frame(
        [text], lambda key, kind, data: blobs.setdefault(key, (kind, data))
    )
    sample = thread_frames.training_sample(thread_frames.compress(frame, None), None, 10000)
    assert blobs and all(key not in sample for key in blobs)
    assert b"prefix " in sample and b" suffix" in sample
    assert len(thread_frames.training_sample(thread_frames.compress(frame, None), None, 5)) == 5


def test_replace_cannot_bypass_dictionary_and_sealed_block_immutability(store):
    thread = store.store("workspace", "source", simple(["first " * 20000, "second"]), actor="actor")
    store._conn.execute("INSERT INTO dict(frame, data) VALUES ('raw', x'0102')")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store._conn.execute(
            "INSERT OR REPLACE INTO dict(id, frame, data) VALUES (1, 'raw', x'0304')"
        )
    row = store._conn.execute(
        "SELECT * FROM block WHERE thread_id=? AND idx=0", (thread.id,)
    ).fetchone()
    with pytest.raises(sqlite3.IntegrityError, match="last"):
        store._conn.execute(
            "INSERT OR REPLACE INTO block VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(row)
        )


@st.composite
def random_thread(draw):
    from commonplace.handoff.models import ThreadMessage

    base64_runs = [
        base64.b64encode(bytes(range(256)) * 2).decode(),
        "A" * 257 + "===",
        "a" * 256 + "=" + "b" * 256,
        base64.urlsafe_b64encode(b"\xfb\xff" * 160).decode(),
    ]
    alphabet = "ab zé🧠\0\n" + ("\ud800\udcff" if draw(st.booleans()) else "")
    short = st.text(alphabet=alphabet, max_size=70)
    content = st.one_of(short, st.sampled_from(base64_runs + ["long needle \0 " * 6000]))
    items = []
    for seq in range(draw(st.integers(1, 9))):
        raw = (
            items[-1].raw
            if items and draw(st.booleans())
            else draw(st.one_of(short, st.sampled_from(base64_runs + ["source line " * 6000])))
        )
        items.append(
            ThreadMessage(
                seq,
                draw(st.sampled_from(["user", "assistant", "tool"])),
                draw(st.sampled_from(["message", "tool_result", "other"])),
                draw(content),
                raw,
                draw(short),
                draw(short),
                draw(short),
            )
        )
    cuts = sorted(
        set(draw(st.lists(st.integers(1, len(items)), min_size=1, max_size=5)) + [len(items)])
    )
    stale = draw(st.integers(0, len(items)))
    start = draw(st.integers(-1, len(items) + 1))
    end = draw(st.one_of(st.none(), st.integers(-1, len(items) + 1)))
    return items, cuts, stale, start, end


@settings(max_examples=35, deadline=None)
@given(random_thread())
def test_random_messages_append_ranges_and_v1_parity(case):
    items, cuts, stale, start, end = case
    new = ThreadStoreV2(":memory:")
    eligible = not any(
        0xD800 <= ord(char) <= 0xDFFF
        for item in items
        for field in (item.role, item.kind, item.text, item.raw, item.name, item.call_id, item.at)
        for char in field
    )
    old = ThreadStore(":memory:") if eligible else None
    try:
        expected_chunks = []
        previous = 0
        thread = None
        old_thread = None
        for cut in cuts:
            if thread is None:
                thread = new.store("workspace", "source", items[:cut], actor="actor")
                if old:
                    old_thread = old.store("workspace", "source", items[:cut], actor="actor")
            else:
                thread = new.append(thread.id, items[:cut])
                if old:
                    old_thread = old.append(old_thread.id, items[:cut])
            base = len(expected_chunks)
            expected_chunks.extend(
                replace(chunk, index=base + chunk.index)
                for chunk in chunk_messages(items[previous:cut])
            )
            previous = cut
            assert new.messages(thread.id) == items[:cut]
            assert new.chunks(thread.id) == expected_chunks
            assert thread.sha256 == canonical_sha256(items[:cut])
            if old:
                assert old.messages(old_thread.id) == new.messages(thread.id)
                assert old.chunks(old_thread.id) == expected_chunks
                assert old_thread.sha256 == thread.sha256
        assert new.append(thread.id, items[:stale]) == thread
        expected_range = (
            []
            if start < 0 or end is not None and (end < 0 or end < start)
            else items[start : None if end is None else end + 1]
        )
        assert new.messages(thread.id, start, end) == expected_range
    finally:
        new.close()
        if old:
            old.close()


def test_migration_reads_back_thread_metadata_rows(tmp_path):
    source = tmp_path / "threads.sqlite3"
    old, _, _ = make_v1(source)
    old.close()
    new = ThreadStoreV2(str(tmp_path / FILENAME))
    new._conn.execute(
        "CREATE TRIGGER corrupt_thread AFTER INSERT ON threads BEGIN UPDATE threads SET created_by='corrupt' WHERE id=new.id; END"
    )
    new.close()
    with pytest.raises(MigrationMismatch, match="thread rows"):
        migrate_v1(str(source))
    new = ThreadStoreV2(str(tmp_path / FILENAME))
    try:
        assert new._conn.execute("SELECT count(*) FROM threads").fetchone()[0] == 0
    finally:
        new.close()
