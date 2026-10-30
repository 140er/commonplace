"""Rank-first equivalence, access control and derived-index migration."""

import sqlite3

from hypothesis import given, settings, strategies as st

from commonplace.handoff.models import RecallHit, ThreadMessage
from commonplace.handoff.thread_store import ThreadStore


def old_search(store, stored_ids, query, limit=8):
    """Original algorithm, including ID batching and final ordering."""
    tokens = query.split()
    if not tokens or not stored_ids or limit <= 0:
        return []
    stored_ids = list(dict.fromkeys(stored_ids))
    match = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
    batch_size = max(1, min(500, store._sqlite_limit("SQLITE_LIMIT_VARIABLE_NUMBER", 999) - 2))
    rows = []
    for offset in range(0, len(stored_ids), batch_size):
        ids = stored_ids[offset : offset + batch_size]
        placeholders = ",".join("?" for _ in ids)
        rows.extend(
            store._conn.execute(
                "SELECT c.thread_id, c.idx, c.seq_start, c.seq_end, c.text, "
                "-bm25(chunks_fts) AS score FROM chunks_fts "
                "JOIN chunks AS c ON c.thread_id=chunks_fts.thread_id AND c.idx=chunks_fts.idx "
                f"WHERE chunks_fts MATCH ? AND c.thread_id IN ({placeholders}) "
                "ORDER BY bm25(chunks_fts), c.thread_id, c.idx LIMIT ?",
                (match, *ids, limit),
            ).fetchall()
        )
    rows.sort(key=lambda row: (-row["score"], row["thread_id"], row["idx"]))
    return [
        RecallHit(
            stored_thread_id=r["thread_id"],
            chunk_index=r["idx"],
            seq_start=r["seq_start"],
            seq_end=r["seq_end"],
            score=r["score"],
            text=r["text"],
        )
        for r in rows[:limit]
    ]


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
    query=st.lists(
        st.sampled_from(["alpha", "beta", "gamma", "absent", '"alpha"']), min_size=1, max_size=3
    ).map(" ".join),
)
def test_rank_first_matches_original(corpora, query):
    store = ThreadStore(":memory:")
    try:
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
        for appended in (False, True):
            if appended:
                store.append(
                    ids[0],
                    messages[0]
                    + [ThreadMessage(len(messages[0]), "user", "message", "alpha beta", raw="")],
                )
            for scope in ([], ids[:1], ids[-1:], ids, ids[::2] + ["missing", ids[0]]):
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
    store._conn.execute("UPDATE chunks_fts SET rowid=rowid+100")
    store._conn.execute("DELETE FROM chunks_fts WHERE rowid=102")
    store._conn.execute("PRAGMA user_version=0")
    original = [tuple(row) for row in store._conn.execute("SELECT * FROM chunks")]
    store.close()
    for _ in range(2):
        store = ThreadStore(path)
        try:
            assert store.search(ids, "alpha beta") == expected
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


def test_ties_use_thread_and_chunk_order_across_id_batches():
    store = ThreadStore(":memory:")
    try:
        ids = [
            store.store(
                "test",
                str(i),
                [ThreadMessage(j, "user", "message", "alpha " * 300, raw="") for j in range(3)],
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
