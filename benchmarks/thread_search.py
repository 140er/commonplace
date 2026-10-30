"""Synthetic rank-first comparison: uv run python -m benchmarks.thread_search."""

import argparse
import math
import random
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from _thread_search_oracle import old_search
from commonplace.handoff.models import ThreadMessage
from commonplace.handoff.thread_store import ThreadStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=50)
    parser.add_argument("--threads", type=int, default=130)
    parser.add_argument("--chunks-per-thread", type=int, default=193)
    args = parser.parse_args()
    rng = random.Random(36)
    with tempfile.TemporaryDirectory() as directory:
        store = ThreadStore(str(Path(directory) / "threads.sqlite3"))
        ids = []
        for thread in range(args.threads):
            # Each message occupies one chunk, with realistic payload lengths.
            messages = [
                ThreadMessage(
                    i,
                    "user",
                    "message",
                    ("raretoken " if thread == 0 and i < 10 else "")
                    + " ".join(
                        rng.choices(
                            ["needle", "alpha", "beta", "gamma", "delta"], k=400
                        )
                    ),
                    raw="",
                )
                for i in range(args.chunks_per_thread)
            ]
            ids.append(
                store.store("synthetic", str(thread), messages, actor="benchmark").id
            )
        print(
            f"SQLite {sqlite3.sqlite_version}; chunks={store._conn.execute('SELECT count(*) FROM chunks').fetchone()[0]}; limit=8; runs={args.runs}"
        )
        store._conn.execute("PRAGMA user_version=0")
        store.close()
        started = time.perf_counter()
        store = ThreadStore(str(Path(directory) / "threads.sqlite3"))
        print(
            f"aligned legacy migration: {(time.perf_counter() - started) * 1000:.2f} ms; writes={store._conn.total_changes}"
        )
        for scope_name, scope in [
            ("all-thread", ids),
            ("one-thread", ids[:1]),
            ("chain", ids[:50]),
        ]:
            for workload, queries in [
                ("common", ["needle", "alpha beta", "gamma"]),
                ("rare", ["raretoken"]),
            ]:
                timings = {"old": [], "new": []}
                for run in range(args.runs + 5):
                    query = queries[run % len(queries)]
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
                        f"{scope_name} {workload} {name}: p50={statistics.median(samples):.2f} ms p95={sorted(samples)[math.ceil(0.95 * len(samples)) - 1]:.2f} ms"
                    )
        store.close()


if __name__ == "__main__":
    main()
