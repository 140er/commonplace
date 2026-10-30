#!/usr/bin/env python3
"""Offline default-search recall and latency on a synthetic sqlite_exact palace.

Run the same command before and after a default change. A cached local MiniLM
model is required; downloads are refused. Timing includes query embedding and
the normal search pipeline, after one warm-up per query. Corpus construction is
excluded. Filler drawers reuse 100 template embeddings, with unique verbatim
record suffixes. Lexical needles deliberately have zero vectors.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mempalace.backends.embedding_wrapper import _embed_texts
from mempalace.palace import get_collection
from mempalace.searcher import search_memories

TOPICS = (
    "authentication middleware",
    "database optimization",
    "error handling patterns",
    "deployment configuration",
    "testing strategy",
    "backup retention",
    "message queue retries",
    "dependency upgrades",
    "access control",
    "transaction isolation",
)


def run(drawers=20_000, repeats=10):
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

    if not (Path(ONNXMiniLM_L6_V2.DOWNLOAD_PATH) / "onnx" / "model.onnx").is_file():
        raise RuntimeError("cached MiniLM ONNX model is required for this offline benchmark")
    os.environ["MEMPALACE_BACKEND_EXPLICIT"] = "sqlite_exact"
    os.environ["MEMPALACE_EMBEDDING_MODEL"] = "minilm"
    templates = [
        f"Engineering notes about {topic}. Revision {revision}: record the decision, "
        "implementation, verification and operational follow-up for this component."
        for topic in TOPICS
        for revision in range(10)
    ]
    semantic = [
        f"The {topic} decision is to use procedure kestrel{index:04d}."
        for index, topic in enumerate(TOPICS)
    ]
    lexical = [f"Artifact /srv/archive/build/ZXQ{index:06d}/manifest.lock" for index in range(10)]
    vectors = _embed_texts(templates + semantic)
    with tempfile.TemporaryDirectory(prefix="search-union-bench-") as palace:
        col = get_collection(palace, create=True, backend="sqlite_exact")
        docs = (
            semantic
            + lexical
            + [f"{templates[i % len(templates)]} Record {i:06d}." for i in range(drawers - 20)]
        )
        embeddings = vectors[len(templates) :] + [[0.0] * len(vectors[0])] * 10
        embeddings += [vectors[i % len(templates)] for i in range(drawers - 20)]
        for start in range(0, drawers, 500):
            end = min(start + 500, drawers)
            col.add(
                ids=[f"drawer-{i}" for i in range(start, end)],
                documents=docs[start:end],
                embeddings=embeddings[start:end],
                metadatas=[
                    {"wing": "engineering", "room": "notes", "source_file": f"record-{i}.txt"}
                    for i in range(start, end)
                ],
            )
        queries = list(TOPICS) + [f"kestrel{i:04d}" for i in range(10)]
        queries += [f"ZXQ{i:06d}" for i in range(10)]

        def search(query):
            result = search_memories(query, palace, n_results=5)
            if "error" in result:
                raise RuntimeError(result)
            return result

        for query in queries:
            search(query)
        latencies = []
        for _ in range(repeats):
            for query in queries:
                start = time.perf_counter()
                search(query)
                latencies.append((time.perf_counter() - start) * 1000)
        recall = {}
        for label, offset, needle_queries in (
            ("semantic", 0, [f"kestrel{i:04d}" for i in range(10)]),
            ("lexical_only", 10, [f"ZXQ{i:06d}" for i in range(10)]),
        ):
            recall[label] = sum(
                any(hit["drawer_id"] == f"drawer-{offset + i}" for hit in search(query)["results"])
                for i, query in enumerate(needle_queries)
            ) / len(needle_queries)
        return {
            "drawers": drawers,
            "samples": len(latencies),
            "latency_ms": {
                "p50": float(np.percentile(latencies, 50)),
                "p95": float(np.percentile(latencies, 95)),
            },
            "recall_at_5": recall,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drawers", type=int, default=20_000)
    parser.add_argument("--repeats", type=int, default=10)
    args = parser.parse_args()
    if args.drawers < 20 or args.repeats < 1:
        parser.error("requires at least 20 drawers and one repeat")
    print(json.dumps(run(args.drawers, args.repeats), indent=2))
