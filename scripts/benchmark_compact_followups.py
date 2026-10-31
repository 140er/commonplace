"""Synthetic compact latency benchmark; requires an explicit disk work directory.

Run the same script against each revision, with OPENBLAS_NUM_THREADS=1.
Each case creates one store, measures it, closes it and deletes it before
starting the next case. No embeddings or documents come from user data.
"""

from __future__ import annotations

import argparse
import gc
import json
import shutil
import statistics
import tempfile
import time
from pathlib import Path

import numpy as np

from mempalace.backends import compact


def benchmark(work_dir, *, compare_masks=False, analyze=False):
    root = Path(work_dir).resolve()
    temporary = Path(tempfile.gettempdir()).resolve()
    if root == temporary or temporary in root.parents:
        raise ValueError("choose an on-disk work directory outside the system temp directory")
    root.mkdir(parents=True, exist_ok=True)
    results = {}
    for size, layout in [(100_000, "balanced"), (200_000, "balanced"), (200_000, "skewed")]:
        directory = root / "store"
        if directory.exists():
            raise ValueError("benchmark store already exists; choose a fresh work directory")
        backend = compact.CompactBackend()
        try:
            col = backend.get_collection(str(directory), "bench", create=True)
            rng = np.random.default_rng(77)
            for start in range(0, size, 2000):
                count = min(2000, size - start)
                col.add(
                    ids=[str(i) for i in range(start, start + count)],
                    documents=["synthetic searchable record"] * count,
                    metadatas=[
                        {
                            "wing": "w0" if layout == "skewed" and i < size * 0.8 else f"w{i % 25}",
                            "room": f"r{(i // 25) % 30}",
                        }
                        for i in range(start, start + count)
                    ],
                    embeddings=rng.normal(size=(count, 384)).tolist(),
                )
            if analyze:
                col.run_maintenance("analyze")
            query = rng.normal(size=384).tolist()

            def run(where=None, col=col, query=query):
                begin = time.perf_counter()
                col.query(query_embeddings=[query], n_results=10, where=where, include=[])
                return (time.perf_counter() - begin) * 1000

            run()
            numbers = {}
            for name, where in {
                "unfiltered": None,
                "wing": {"wing": "w0"},
                "room": {"room": "r0"},
            }.items():
                run(where)
                numbers[name] = statistics.median(run(where) for _ in range(9))
            if size == 100_000:
                timings, write_timings = [], []
                for _ in range(9):
                    begin = time.perf_counter()
                    col.upsert(
                        ids=["new"],
                        documents=["synthetic new record"],
                        metadatas=[{"wing": "w0", "room": "r0"}],
                        embeddings=[query],
                    )
                    write_timings.append((time.perf_counter() - begin) * 1000)
                    timings.append(run())
                numbers["query_after_write"] = statistics.median(timings)
                numbers["write"] = statistics.median(write_timings)
            if compare_masks and size == 200_000 and layout == "balanced":
                comparisons = {}
                original = compact._BROAD_FILTER_FRACTION
                try:
                    for wings in [1, 3, 5, 10, 20]:
                        where = {"wing": {"$in": [f"w{i}" for i in range(wings)]}}
                        times = {}
                        for path, threshold in [("sql", 2), ("arrays", 0)]:
                            compact._BROAD_FILTER_FRACTION = threshold
                            run(where)
                            times[path] = statistics.median(run(where) for _ in range(9))
                        comparisons[f"{wings * 4}%"] = times
                finally:
                    compact._BROAD_FILTER_FRACTION = original
                numbers["mask_comparison"] = comparisons
            results[f"{size}-{layout}"] = numbers
            print(json.dumps({f"{size}-{layout}": numbers}), flush=True)
        finally:
            backend.close()
            shutil.rmtree(directory)
            gc.collect()
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--output", required=True, help="JSON measurements in milliseconds")
    parser.add_argument("--compare-masks", action="store_true")
    parser.add_argument(
        "--analyze", action="store_true", help="populate planner statistics before timing"
    )
    args = parser.parse_args()
    result = benchmark(args.work_dir, compare_masks=args.compare_masks, analyze=args.analyze)
    Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
