#!/usr/bin/env python3
"""Measure added fact-search latency on a seeded local graph, without models.

The corpus mirrors the review probe: one source per fact, nine predicates,
and entity names sharing common words. Queries cover broad, selective, and
all-common terms. Timings exclude seeding and the one-time index build, and
report the optional fact lookup's added wall time per search.
"""

import argparse
import json
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mempalace.knowledge_graph import KnowledgeGraph
from mempalace.searcher.facts import search_facts

QUERIES = ("what is the status of the project", "Entity 42", "entity project")
PREDICATES = (
    "is_a",
    "works_at",
    "lives_in",
    "part_of",
    "member_of",
    "uses",
    "owns",
    "decided_on",
    "status_of",
)


def measure(size, repeats=3):
    rng = random.Random(1)
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "kg.sqlite3")
        with KnowledgeGraph(db_path=path) as kg:
            conn = kg._conn()
            for name in ("insert", "update", "delete", "rename"):
                conn.execute("DROP TRIGGER IF EXISTS fact_search_" + name)
            conn.execute("DROP TABLE fact_search")
            conn.execute("DROP TABLE IF EXISTS fact_search_vocab")
            conn.execute("DROP TABLE IF EXISTS fact_search_keys")
            with conn:
                conn.executemany(
                    "INSERT INTO entities(id,name) VALUES(?,?)",
                    ((f"e{i}", f"Entity {i} of the project") for i in range(size // 5)),
                )
                conn.executemany(
                    "INSERT INTO triples(id,subject,predicate,object,wing) VALUES(?,?,?,?,?)",
                    (
                        (
                            f"t{i}",
                            f"e{rng.randrange(size // 5)}",
                            rng.choice(PREDICATES),
                            f"e{rng.randrange(size // 5)}",
                            "shared",
                        )
                        for i in range(size)
                    ),
                )
                conn.executemany(
                    "INSERT INTO fact_sources(triple_id,source_thread,added_at) VALUES(?,?,?)",
                    ((f"t{i}", "thr_source", "2026-01-01T00:00:00+00:00") for i in range(size)),
                )
        with KnowledgeGraph(db_path=path) as kg:
            rows = []
            for query in QUERIES:
                search_facts(kg, query)  # warm caches, outside the samples
                samples = []
                for _ in range(repeats):
                    start = time.perf_counter()
                    facts = search_facts(kg, query)
                    samples.append((time.perf_counter() - start) * 1000)
                rows.append(
                    {
                        "query": query,
                        "facts": len(facts),
                        "median_ms": statistics.median(samples),
                        "max_ms": max(samples),
                        "samples_ms": samples,
                    }
                )
            return {"facts": size, "repeats": repeats, "lookups": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[5000, 20000, 50000])
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps([measure(size, args.repeats) for size in args.sizes], indent=2), flush=True)
