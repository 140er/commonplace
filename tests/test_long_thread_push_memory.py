"""Extending a long saved thread costs memory in proportion to the new text, not the thread."""

import hashlib
import json
import tracemalloc

import pytest

from commonplace import capture, continuity as core, continuity_schema as schema
from commonplace.control_db import ControlDB
from commonplace.handoff import thread_store
from commonplace.handoff.models import ThreadMessage
from commonplace.handoff.thread_store import ThreadStore, ThreadStoreV2, canonical_sha256

LINE_BYTES = 2000


def legacy_sha256(messages):
    """The digest as it was first defined: one JSON array of every message, built whole."""
    canonical = [(m.seq, m.role, m.kind, m.name, m.call_id, m.text, m.raw) for m in messages]
    encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8", "surrogatepass"
    )
    return hashlib.sha256(encoded).hexdigest()


def lines(first, count):
    rows = []
    for i in range(first, first + count):
        role = "user" if i % 2 == 0 else "assistant"
        text = f"m{i} " + f"lorem ipsum {i} " * (LINE_BYTES // 16)
        content = text if role == "user" else [{"type": "text", "text": text}]
        record = {
            "type": role,
            "uuid": f"u{i}",
            "sessionId": "S",
            "timestamp": "2026-11-15T10:00:00Z",
            "message": {"role": role, "content": content},
        }
        rows.append(json.dumps(record))
    return ("\n".join(rows) + "\n").encode()


@pytest.fixture(params=["v1", "v2"])
def env(request, tmp_path, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_PLANS", "off")
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    user = db.create_user("synthetic@example.test")
    ws = db.create_workspace("synthetic", user, personal=True)
    schema.ensure_schema(db)
    store = (ThreadStore if request.param == "v1" else ThreadStoreV2)(":memory:")
    yield db, store, user, ws, request.param
    store.close()
    db.close()


def push(env, raw, before):
    db, store, user, ws, _ = env
    with db._lock, db._tx() as conn:
        return core.stream_push(
            conn,
            store,
            ws=ws,
            user_id=user,
            connection_id="",
            kind="capture",
            stream_id=f"capture:{user}:claude-code:a",
            source="claude-code",
            native_source="claude-code",
            data=raw,
            offset=len(before),
            prefix=hashlib.sha256(before).hexdigest(),
            actor="synthetic",
            maximum=None,
            gate=lambda: None,
        )


def test_extending_a_long_thread_stays_within_a_few_times_its_size(env):
    first = lines(0, 3500)  # about 8 MiB of transcript, held once as archive and once parsed
    push(env, first, b"")
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        before = tracemalloc.get_traced_memory()[0]
        result = push(env, lines(3500, 20), first)
        peak = tracemalloc.get_traced_memory()[1] - before
    finally:
        tracemalloc.stop()
    assert result["status"] == "appended" and result["added_messages"] == 20
    # Measured on the old code: about 15 times the thread (v1) and 12 times (v2).
    # Now about 3 times (v1) and 6 times (v2): the archive, the sum with the new
    # text, and one message being hashed.
    assert peak < 8 * len(first), f"{peak / len(first):.1f}x the thread"


@pytest.mark.parametrize("piece", [1, 7, thread_store._HASH_PIECE])
def test_the_streamed_digest_equals_the_whole_thread_digest(monkeypatch, piece):
    monkeypatch.setattr(thread_store, "_HASH_PIECE", piece)
    odd = 'quote " back \\ line\n tab\t nul \x00 é ✓ 😀 lone \ud800 end'
    for count in (0, 1, 3):
        messages = [
            ThreadMessage(i, "user", "message", odd * (i + 1), odd[::-1], name='n,"x', call_id="c")
            for i in range(count)
        ]
        assert canonical_sha256(messages) == legacy_sha256(messages)
        assert canonical_sha256(iter(messages)) == legacy_sha256(messages)


def test_append_rows_matches_appending_the_whole_transcript(env):
    _, store, _, _, kind = env
    other = ThreadStore(":memory:") if kind == "v1" else ThreadStoreV2(":memory:")
    try:
        rows = [ThreadMessage(i, "user", "message", f"t{i}", f"r{i}") for i in range(7)]
        first = store.store("ws", "one", rows[:3], actor="a")
        other.store("ws", "one", rows[:3], actor="a")
        extended = store.append_rows(first.id, rows[3:], max_storage_bytes=None)
        whole = other.append(other.by_source("ws", "one").id, rows)
        assert extended.message_count == whole.message_count == 7
        assert extended.sha256 == whole.sha256 == legacy_sha256(rows)
        assert extended.chunk_count == whole.chunk_count
        assert [m.raw for m in store.iter_messages(first.id, page=2)] == [m.raw for m in rows]
        # The saved thread answers the same later whole-transcript append.
        assert store.append(first.id, rows).message_count == 7
        with pytest.raises(ValueError):
            store.append_rows(first.id, [ThreadMessage(9, "user", "message", "t", "r")])
    finally:
        other.close()


def test_a_large_capture_hands_freed_pages_back_after_it_returns(monkeypatch):
    trimmed = []
    monkeypatch.setattr("mempalace.worker_memory.trim_heap", lambda: trimmed.append(True))

    class Handler:
        def __init__(self, length, fail=""):
            self.headers = {"Content-Length": length, "fail": fail}

    def route(handler):
        if handler.headers.get("fail"):
            raise RuntimeError("route failed")

    wrapped = capture._trim_after_large(route)
    wrapped(Handler(str(capture.TRIM_AFTER_BYTES - 1)))
    wrapped(Handler("not a number"))
    assert trimmed == []
    wrapped(Handler(str(capture.TRIM_AFTER_BYTES)))
    assert trimmed == [True]
    wrapped(Handler(str(capture.TRIM_AFTER_BYTES)))
    assert trimmed == [True, True]
    with pytest.raises(RuntimeError):
        wrapped(Handler(str(capture.TRIM_AFTER_BYTES), fail="yes"))
    assert trimmed == [True, True, True]
