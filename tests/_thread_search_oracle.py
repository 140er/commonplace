"""Original thread-search SQL retained as an independent test/benchmark oracle."""

from commonplace.handoff.models import RecallHit


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


def assert_alignment(store):
    """Every FTS row must identify and contain exactly its source chunk."""
    chunks = [
        tuple(row)
        for row in store._conn.execute(
            "SELECT rowid, thread_id, idx, text FROM chunks ORDER BY rowid"
        )
    ]
    indexed = [
        tuple(row)
        for row in store._conn.execute(
            "SELECT rowid, thread_id, idx, text FROM chunks_fts ORDER BY rowid"
        )
    ]
    assert indexed == chunks
