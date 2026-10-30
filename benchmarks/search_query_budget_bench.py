#!/usr/bin/env python3
"""Offline long-query latency gates and literal/keyword recall on 20k/60k drawers.

Set MEMPALACE_BENCH_CODE_ROOT to a source snapshot to compare an earlier
revision using the same corpus and harness. Gateway rows use the production
fan-out mediator with in-process worker calls, excluding HTTP transport.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(
    0, os.environ.get("MEMPALACE_BENCH_CODE_ROOT", str(Path(__file__).resolve().parents[1]))
)
from commonplace.gateway import Mediator
from mempalace.backends.embedding_wrapper import _embed_texts
from mempalace.lexical import tokenize
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
PROMPT = (
    "Please review the deployment configuration and authentication middleware then explain the error "
    "handling patterns database optimization testing strategy backup retention message queue retries "
    "dependency upgrades access control transaction isolation implementation verification and operational "
    "followup before we change the application release process and decide which components need further work today together"
)
PROMPT = " ".join(PROMPT.split()[:50])
LOG_PREFIX = "ERROR ZXQ739251 /srv/archive/kestrel/manifest.lock ECONNRESET port 8473 "
LOG = LOG_PREFIX + " ".join(
    (
        "deployment configuration authentication middleware error handling patterns database "
        "optimization testing strategy backup retention queue retries"
    ).split()[i % 15]
    for i in range(123 - len(tokenize(LOG_PREFIX)))
)
NEEDLES = (
    (
        "ZXQ739251",
        "Build ZXQ739251 failed reading /srv/archive/kestrel/manifest.lock on port 8473.",
    ),
    (
        "/srv/archive/kestrel/manifest.lock",
        "Build ZXQ739251 failed reading /srv/archive/kestrel/manifest.lock on port 8473.",
    ),
    ("marigold", "The marigold note is stored here."),
    ("quartz", "The quartz note is stored here."),
    ("lantern", "The lantern note is stored here."),
)


def run(backend, repeats, recall_only=False):
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

    cache = Path(ONNXMiniLM_L6_V2.DOWNLOAD_PATH) / "onnx"
    if not all(
        (cache / name).is_file()
        for name in (
            "model.onnx",
            "tokenizer.json",
            "config.json",
            "special_tokens_map.json",
            "tokenizer_config.json",
            "vocab.txt",
        )
    ):
        raise RuntimeError("complete cached MiniLM model required; this benchmark is offline")
    os.environ["MEMPALACE_BACKEND_EXPLICIT"] = backend
    os.environ["MEMPALACE_EMBEDDING_MODEL"] = "minilm"
    templates = [
        f"Engineering notes about {topic}. Revision {revision}: record the decision, implementation, "
        "verification and operational followup for this component."
        for topic in TOPICS
        for revision in range(10)
    ]
    vectors = _embed_texts(templates)
    wings = ["engineering"] + [f"ops{i}" for i in range(7)]
    report = {
        "backend": backend,
        "repeats": repeats,
        "prompt_words": len(PROMPT.split()),
        "log_tokens": len(tokenize(LOG)),
        "rows": [],
        "recall": {},
    }
    for size in (20_000,) if recall_only else (20_000, 60_000):
        with tempfile.TemporaryDirectory(prefix="query-budget-") as palace:
            col = get_collection(palace, create=True, backend=backend)
            for start in range(0, size, 500):
                stop = min(start + 500, size)
                col.add(
                    ids=[f"d{i}" for i in range(start, stop)],
                    documents=[
                        NEEDLES[i][1]
                        if i < len(NEEDLES)
                        else f"{templates[i % 100]} Record {i:06d}."
                        for i in range(start, stop)
                    ],
                    embeddings=[
                        [0.0] * len(vectors[0]) if i < len(NEEDLES) else vectors[i % 100]
                        for i in range(start, stop)
                    ],
                    metadatas=[
                        {
                            "wing": wings[i % 8],
                            "room": "notes",
                            "source_file": f"record-{i}.txt",
                            "chunk_index": 0,
                        }
                        for i in range(start, stop)
                    ],
                )
            cases = (
                [("log123_all", LOG, None, False), ("prompt50_gateway8", PROMPT, None, True)]
                if size == 20_000
                else [
                    ("prompt50_all", PROMPT, None, False),
                    ("prompt50_wing", PROMPT, "engineering", False),
                ]
            )
            if recall_only:
                cases = []
            for label, query, wing, gateway in cases:

                def search(strategy, query=query, wing=wing, gateway=gateway):
                    if not gateway:
                        result = search_memories(
                            query, palace, n_results=5, wing=wing, candidate_strategy=strategy
                        )
                    else:

                        def call_tool(principal, name, args):
                            if name == "mempalace_list_wings":
                                return {"wings": {w: {} for w in wings}}
                            return search_memories(
                                args["query"],
                                palace,
                                n_results=args["limit"],
                                wing=args["wing"],
                                candidate_strategy=strategy,
                            )

                        mediator = object.__new__(Mediator)
                        mediator.workers = SimpleNamespace(call_tool=call_tool)
                        principal = SimpleNamespace(
                            policy=SimpleNamespace(can_read_wing=lambda w: w in wings)
                        )
                        result = mediator._fan_out_search(
                            principal, "mempalace_search", {"query": query, "limit": 5}
                        )
                    if "error" in result:
                        raise RuntimeError(result)
                    return result

                print(f"{backend}: {size} drawers, {label}", file=sys.stderr, flush=True)
                samples = {s: [] for s in ("vector", "union")}
                for strategy in samples:
                    search(strategy)
                for repeat in range(repeats):
                    for strategy in ("vector", "union") if repeat % 2 == 0 else ("union", "vector"):
                        began = time.perf_counter()
                        search(strategy)
                        samples[strategy].append((time.perf_counter() - began) * 1000)
                row = {
                    "case": label,
                    "drawers": size,
                    "latency_ms": {
                        s: {
                            "p50": float(np.percentile(v, 50)),
                            "p95": float(np.percentile(v, 95)),
                            "max": max(v),
                        }
                        for s, v in samples.items()
                    },
                }
                row["union_p95_limit_ms"] = max(
                    row["latency_ms"]["vector"]["p95"] * 1.25,
                    row["latency_ms"]["vector"]["p95"] + 50,
                )
                row["pass"] = row["latency_ms"]["union"]["p95"] <= row["union_p95_limit_ms"]
                report["rows"].append(row)
            if size == 20_000:
                for strategy in ("vector", "union"):
                    outcomes = []
                    for i, (query, _) in enumerate(NEEDLES):
                        result = search_memories(
                            query, palace, n_results=5, candidate_strategy=strategy
                        )
                        outcomes.append(f"d{i}" in {h["drawer_id"] for h in result["results"]})
                    report["recall"][strategy] = outcomes
    report["pass"] = all(row["pass"] for row in report["rows"])
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["chroma", "sqlite_exact"], default="chroma")
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--recall-only", action="store_true")
    args = parser.parse_args()
    report = run(args.backend, args.repeats, args.recall_only)
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["pass"] else 1)
