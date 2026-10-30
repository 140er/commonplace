"""Synthetic rank-first comparison: uv run python -m benchmarks.thread_search."""

import argparse
import math
import random
import statistics
import tempfile
import time
from pathlib import Path

from commonplace.handoff.models import RecallHit, ThreadMessage
from commonplace.handoff.thread_store import ThreadStore


def old_search(store, stored_ids, query, limit=8):
    """Original algorithm, including ID batching and final ordering."""
    tokens = query.split()
    if not tokens or not stored_ids or limit <= 0:
        return []
    stored_ids = list(dict.fromkeys(stored_ids))
    match = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
    batch_size = max(
        1, min(500, store._sqlite_limit("SQLITE_LIMIT_VARIABLE_NUMBER", 999) - 2)
    )
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=50)
    args = parser.parse_args()
    rng = random.Random(36)
    with tempfile.TemporaryDirectory() as directory:
        store = ThreadStore(str(Path(directory) / "threads.sqlite3"))
        ids = []
        for thread in range(130):
            # Each message occupies one chunk, with realistic payload lengths.
            messages = [
                ThreadMessage(
                    i,
                    "user",
                    "message",
                    " ".join(
                        rng.choices(
                            ["needle", "alpha", "beta", "gamma", "delta"], k=400
                        )
                    ),
                    raw="",
                )
                for i in range(193)
            ]
            ids.append(
                store.store("synthetic", str(thread), messages, actor="benchmark").id
            )
        print(
            f"SQLite {__import__('sqlite3').sqlite_version}; chunks={store._conn.execute('SELECT count(*) FROM chunks').fetchone()[0]}; limit=8; runs={args.runs}"
        )
        for scope_name, scope in [("all-thread", ids), ("one-thread", ids[:1])]:
            timings = {"old": [], "new": []}
            for run in range(args.runs + 5):
                query = ["needle", "alpha beta", "gamma"][run % 3]
                assert old_search(store, scope, query) == store.search(scope, query)
                # Alternate execution order to reduce cache/order bias.
                for name in ["old", "new"] if run % 2 else ["new", "old"]:
                    function = (
                        old_search
                        if name == "old"
                        else lambda s, ids, q: s.search(ids, q)
                    )
                    start = time.perf_counter()
                    function(store, scope, query)
                    elapsed = (time.perf_counter() - start) * 1000
                    if run >= 5:
                        timings[name].append(elapsed)
            for name, samples in timings.items():
                print(
                    f"{scope_name} {name}: p50={statistics.median(samples):.2f} ms p95={sorted(samples)[math.ceil(0.95 * len(samples)) - 1]:.2f} ms"
                )
        store.close()


if __name__ == "__main__":
    main()
