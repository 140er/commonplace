#!/usr/bin/env python3
"""Offline vector/union recall and latency on a synthetic 20k-drawer palace.

A complete cached local MiniLM model is required. Timing includes query
embedding and production search, after one warm-up per query/strategy/scope.
Construction is excluded. Filler drawers reuse 100 template embeddings with
unique record suffixes. Lexical needles deliberately have zero vectors.
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
COMMON_QUERIES = (
    "what did we decide about the deployment configuration and the error handling",
    "engineering notes record implementation verification",
    "what is the decision and follow up for this component",
)


def _latency(samples):
    return {
        "p50": float(np.percentile(samples, 50)),
        "p95": float(np.percentile(samples, 95, method="inverted_cdf")),
    }


def run(drawers=20_000, repeats=5, backend="sqlite_exact", wing="engineering", room="notes"):
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

    cache = Path(ONNXMiniLM_L6_V2.DOWNLOAD_PATH) / "onnx"
    required = (
        "model.onnx",
        "tokenizer.json",
        "config.json",
        "special_tokens_map.json",
        "tokenizer_config.json",
        "vocab.txt",
    )
    if not all((cache / name).is_file() for name in required):
        raise RuntimeError(
            "complete cached MiniLM ONNX model is required for this offline benchmark"
        )
    os.environ["MEMPALACE_BACKEND_EXPLICIT"] = backend
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
        col = get_collection(palace, create=True, backend=backend)
        docs = (
            semantic
            + lexical
            + [f"{templates[i % len(templates)]} Record {i:06d}." for i in range(drawers - 20)]
        )
        embeddings = vectors[len(templates) :] + [[0.0] * len(vectors[0])] * 10
        embeddings += [vectors[i % len(templates)] for i in range(drawers - 20)]
        metadata = [
            {
                "wing": "engineering" if i % 2 == 0 else "ops",
                "room": "notes" if i % 4 < 2 else "logs",
                "source_file": f"record-{i}.txt",
                "chunk_index": 0,
            }
            for i in range(drawers)
        ]
        for start in range(0, drawers, 500):
            end = min(start + 500, drawers)
            col.add(
                ids=[f"drawer-{i}" for i in range(start, end)],
                documents=docs[start:end],
                embeddings=embeddings[start:end],
                metadatas=metadata[start:end],
            )
        groups = {
            "topics": list(TOPICS),
            "common_words": list(COMMON_QUERIES),
            "semantic_needles": [f"kestrel{i:04d}" for i in range(10)],
            "lexical_needles": [f"ZXQ{i:06d}" for i in range(10)],
        }
        scopes = {"all": {}, "wing": {"wing": wing}, "wing_room": {"wing": wing, "room": room}}
        report = {"backend": backend, "drawers": drawers, "repeats": repeats, "scopes": {}}

        def search(query, strategy, filters):
            result = search_memories(
                query, palace, n_results=5, candidate_strategy=strategy, **filters
            )
            if "error" in result:
                raise RuntimeError(result)
            return result

        for scope, filters in scopes.items():
            print(f"{backend}: {drawers} drawers, measuring {scope}", file=sys.stderr, flush=True)
            timings = {
                strategy: {group: [] for group in groups} for strategy in ("vector", "union")
            }
            for strategy in timings:
                for queries in groups.values():
                    for query in queries:
                        search(query, strategy, filters)
            # Alternate order to reduce drift between the two strategies.
            for repeat in range(repeats):
                order = ("vector", "union") if repeat % 2 == 0 else ("union", "vector")
                for group, queries in groups.items():
                    for query in queries:
                        for strategy in order:
                            start = time.perf_counter()
                            search(query, strategy, filters)
                            timings[strategy][group].append((time.perf_counter() - start) * 1000)
            result = {"filters": filters, "strategies": {}}
            for strategy, grouped in timings.items():
                samples = [sample for values in grouped.values() for sample in values]
                recall = {}
                for label, offset in (("semantic_needles", 0), ("lexical_needles", 10)):
                    relevant = [
                        (i, q)
                        for i, q in enumerate(groups[label])
                        if all(metadata[offset + i][k] == v for k, v in filters.items())
                    ]
                    found = sum(
                        any(
                            hit["drawer_id"] == f"drawer-{offset + i}"
                            for hit in search(q, strategy, filters)["results"]
                        )
                        for i, q in relevant
                    )
                    recall[label] = {"found": found, "total": len(relevant)}
                result["strategies"][strategy] = {
                    "samples": len(samples),
                    "latency_ms": _latency(samples),
                    "by_query_group_ms": {
                        group: _latency(values) for group, values in grouped.items()
                    },
                    "recall_at_5": recall,
                }
            report["scopes"][scope] = result
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drawers", type=int, default=20_000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--backend", choices=["chroma", "sqlite_exact"], default="sqlite_exact")
    parser.add_argument("--wing", default="engineering")
    parser.add_argument("--room", default="notes")
    args = parser.parse_args()
    if args.drawers < 20 or args.repeats < 1:
        parser.error("requires at least 20 drawers and one repeat")
    print(json.dumps(run(args.drawers, args.repeats, args.backend, args.wing, args.room), indent=2))
