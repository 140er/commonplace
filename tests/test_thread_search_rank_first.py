"""Rank-first equivalence, access control and derived-index migration."""

import sqlite3
import tempfile
from itertools import count
from pathlib import Path
from unittest.mock import patch

import pytest
from _thread_search_oracle import assert_alignment, old_search
from hypothesis import given, settings, strategies as st

from commonplace.handoff.models import ThreadMessage
from commonplace.handoff.thread_store import ThreadStore


@settings(max_examples=35, deadline=None)
@given(
    corpora=st.lists(
        st.lists(
            st.sampled_from(["alpha", "beta", "alpha beta", "gamma", "alpha alpha"]),
            min_size=1,
            max_size=5,
        ),
        min_size=1,
        max_size=7,
    ),
    data=st.data(),
    query=st.lists(
        st.sampled_from(["alpha", "beta", "gamma", "absent", '"alpha"']), min_size=1, max_size=3
    ).map(" ".join),
)
def test_rank_first_matches_original(corpora, query, data):
    counter = count()
    with patch(
        "commonplace.handoff.thread_store.secrets.token_hex",
        side_effect=lambda _: f"{next(counter):016x}",
    ):
        directory = tempfile.TemporaryDirectory()
        path = str(Path(directory.name) / "threads.sqlite3")
        store = ThreadStore(path)
        ids = []
        messages = []
        for i, texts in enumerate(corpora):
            # Pad messages to make multiple chunks; repeated texts force ties.
            items = [
                ThreadMessage(j, "user", "message", text + " filler" * 300, raw="")
                for j, text in enumerate(texts)
            ]
            messages.append(items)
            ids.append(store.store("test", str(i), items, actor="test").id)
    try:
        drawn_scope = data.draw(st.lists(st.sampled_from(ids + ["missing"]), max_size=12))
        for appended in (False, True):
            if appended:
                store.append(
                    ids[0],
                    messages[0]
                    + [ThreadMessage(len(messages[0]), "user", "message", "alpha beta", raw="")],
                )
            expected = old_search(store, ids, query, 1000)
            assert_alignment(store)
            store.close()
            store = ThreadStore(path)
            assert_alignment(store)
            assert store.search(ids, query, 1000) == expected
            for scope in ([], ids[:1], ids[-1:], ids, ids[::2] + ["missing", ids[0]], drawn_scope):
                for limit in (0, 1, 3, 1000):
                    assert store.search(scope, query, limit) == old_search(
                        store, scope, query, limit
                    )
            # Force several stored-id batches without changing the corpus scores.
            if hasattr(store._conn, "setlimit"):
                previous = store._conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 4)
                try:
                    for limit in (1, 1000):
                        assert store.search(ids, query, limit) == old_search(
                            store, ids, query, limit
                        )
                finally:
                    store._conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, previous)
    finally:
        store.close()
        directory.cleanup()


def test_legacy_rowids_migrate_and_reopen_idempotently(tmp_path):
    path = str(tmp_path / "threads.sqlite3")
    store = ThreadStore(path)
    ids = [
        store.store(
            "test", str(i), [ThreadMessage(0, "user", "message", text, raw="")], actor="test"
        ).id
        for i, text in enumerate(["alpha beta", "alpha", "beta"])
    ]
    expected = old_search(store, ids, "alpha beta")
    # Simulate a legacy file with arbitrary FTS rowids, plus an unindexed chunk.
    store._conn.execute("UPDATE chunks_fts SET rowid=rowid+100 WHERE rowid=1")
    store._conn.execute("DELETE FROM chunks_fts WHERE rowid=2")
    store._conn.execute("PRAGMA user_version=0")
    original = [tuple(row) for row in store._conn.execute("SELECT * FROM chunks")]
    store.close()
    for _ in range(2):
        store = ThreadStore(path)
        try:
            assert store.search(ids, "alpha beta") == expected
            assert store.search(ids, "alpha beta") == old_search(store, ids, "alpha beta")
            assert_alignment(store)
            assert [tuple(row) for row in store._conn.execute("SELECT * FROM chunks")] == original
            assert store._conn.execute("SELECT count(*) FROM chunks_fts").fetchone()[0] == 3
            assert (
                store._conn.execute(
                    "SELECT count(*) FROM chunks_fts f JOIN chunks c ON f.rowid=c.rowid "
                    "WHERE f.thread_id=c.thread_id AND f.idx=c.idx AND f.text=c.text"
                ).fetchone()[0]
                == 3
            )
        finally:
            store.close()


def test_scope_excludes_global_top_hits():
    store = ThreadStore(":memory:")
    try:
        denied = store.store(
            "other",
            "denied",
            [ThreadMessage(0, "user", "message", "alpha " * 50, raw="")],
            actor="test",
        )
        allowed = store.store(
            "test",
            "allowed",
            [ThreadMessage(0, "user", "message", "alpha " + "filler " * 500, raw="")],
            actor="test",
        )
        assert store.search([denied.id, allowed.id], "alpha", 1)[0].stored_thread_id == denied.id
        assert store.search([allowed.id], "alpha", 1) == old_search(store, [allowed.id], "alpha", 1)
        assert store.search([allowed.id], "alpha", 1)[0].stored_thread_id == allowed.id
    finally:
        store.close()


@pytest.mark.skipif(not hasattr(sqlite3.Connection, "setlimit"), reason="Python 3.11+ API")
def test_ties_use_thread_and_chunk_order_across_id_batches():
    store = ThreadStore(":memory:")
    try:
        ids = [
            store.store(
                "test",
                str(i),
                [ThreadMessage(j, "user", "message", "alpha " * 600, raw="") for j in range(3)],
                actor="test",
            ).id
            for i in range(8)
        ]
        ids.sort(reverse=True)
        expected = old_search(store, ids, "alpha", 100)
        assert len({hit.score for hit in expected}) == 1
        assert [(hit.stored_thread_id, hit.chunk_index) for hit in expected] == sorted(
            (hit.stored_thread_id, hit.chunk_index) for hit in expected
        )
        previous = store._conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 4)
        try:
            for limit in (1, 2, 7, 100):
                assert store.search(ids, "alpha", limit) == expected[:limit]
        finally:
            store._conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, previous)
    finally:
        store.close()


def test_aligned_legacy_migration_and_reopen_do_not_rewrite_index(tmp_path):
    path = str(tmp_path / "threads.sqlite3")
    store = ThreadStore(path)
    stored = store.store(
        "test", "original", [ThreadMessage(0, "user", "message", "alpha", raw="")], actor="test"
    )
    store._conn.execute("PRAGMA user_version=0")
    store.close()
    for _ in range(2):
        store = ThreadStore(path)
        try:
            assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 1
            assert store._conn.total_changes == 0
            assert_alignment(store)
            assert store.search([stored.id], "alpha") == old_search(store, [stored.id], "alpha")
        finally:
            store.close()


def test_old_writer_after_fallback_repairs_alignment_even_at_version_one(tmp_path):
    path = str(tmp_path / "threads.sqlite3")
    original = ThreadStore(path)
    original.store(
        "test", "first", [ThreadMessage(0, "user", "message", "alpha", raw="")], actor="test"
    )
    # Model an old FTS writer already open while another writer lacks FTS5.
    fallback = ThreadStore(path)
    fallback._fts_enabled = False
    denied = fallback.store(
        "other", "denied", [ThreadMessage(0, "user", "message", "topsecret", raw="")], actor="test"
    )
    fallback.close()
    original._fts_enabled = False
    allowed = original.store(
        "test", "allowed", [ThreadMessage(0, "user", "message", "public", raw="")], actor="test"
    )
    # The old store path assigns its own FTS rowid, filling the fallback gap.
    original._conn.execute(
        "INSERT INTO chunks_fts(thread_id, idx, text) "
        "SELECT thread_id, idx, text FROM chunks WHERE thread_id=?",
        (allowed.id,),
    )
    original.close()
    # The old initializer backfills by identity and ignores user_version.
    with sqlite3.connect(path) as legacy:
        legacy.execute(
            "INSERT INTO chunks_fts(thread_id, idx, text) "
            "SELECT thread_id, idx, text FROM chunks WHERE (thread_id, idx) NOT IN "
            "(SELECT thread_id, idx FROM chunks_fts)"
        )
        assert legacy.execute("PRAGMA user_version").fetchone()[0] == 1
        assert (
            legacy.execute(
                "SELECT c.thread_id FROM chunks_fts f JOIN chunks c ON c.rowid=f.rowid "
                "WHERE f.text LIKE '%topsecret%'"
            ).fetchone()[0]
            == allowed.id
        )
    reopened = ThreadStore(path)
    try:
        assert_alignment(reopened)
        for scope in ([allowed.id], [denied.id], [allowed.id, denied.id]):
            for query in ("topsecret", "public", "alpha public"):
                assert reopened.search(scope, query) == old_search(reopened, scope, query)
        assert reopened.search([allowed.id], "topsecret") == []
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "query,expected_plan", [("alpha", "scope AS MATERIALIZED"), ("rare", "CROSS JOIN")]
)
def test_hybrid_plans_match_original(query, expected_plan):
    store = ThreadStore(":memory:")
    try:
        ids = [
            store.store(
                "test",
                str(i),
                [
                    ThreadMessage(
                        0, "user", "message", "alpha rare" if i == 0 else "alpha filler", raw=""
                    )
                ],
                actor="test",
            ).id
            for i in range(10)
        ]
        statements = []
        store._conn.set_trace_callback(statements.append)
        hits = store.search(ids, query, 1)
        store._conn.set_trace_callback(None)
        assert hits == old_search(store, ids, query, 1)
        assert any(expected_plan in sql for sql in statements)
        assert_alignment(store)
    finally:
        store.close()
