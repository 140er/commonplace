#!/usr/bin/env python3
"""Offline query latency gates and literal/keyword recall on 60k/200k drawers.

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
QUESTION12 = "what did we decide about the deployment configuration and the error handling"
COMMON8 = "error deployment configuration team decided implementation verification component"
DATE_QUESTION = "what did we decide in March 2024 about the kestrel migration and the rollback window for the cluster"
LONG_DATE_QUESTION = (
    DATE_QUESTION
    + " deployment configuration authentication middleware followup verification component architecture latency release rollback integration design reliability maintenance"
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
    (
        "What did Sarah Okonkwo say about the budget review at our meeting on Tuesday afternoon?",
        "Okonkwo signed off on it.",
    ),
    (
        'Please find what we wrote down about the "marigold protocol" from the meeting with the vendor team last spring',
        "Marigold protocol: rotate keys every Friday.",
    ),
    (
        "Traceback at /srv/archive/project/src/module/file.py line 42 in handler: KeyError PR-38 E42 ZQ9",
        "Ticket ZQ9 tracks the flaky import.",
    ),
    (
        "March 2024 kestrel migration rollback window",
        "Kestrel migration: rollback window is Sunday night.",
    ),
)
NEEDLES += (
    ("foo_bar", "foo_bar is the exact function name."),
    ("src/main.py", "src/main.py contains the entry point."),
    ("build.alpha.7", "build.alpha.7 identifies this release."),
    ("server:8473", "server:8473 accepts the connection."),
    ('"copper lantern"', "The copper lantern is stored here."),
    ("café", "The café receipt is stored here."),
    ("项目部署配置", "项目部署配置 is the deployment note."),
    ("x" * 81, "x" * 81 + " is the long identifier."),
)
MID_WORDS = (
    "deployment",
    "configuration",
    "error",
    "handling",
    "authentication",
    "middleware",
    "database",
    "optimization",
)


def run(
    backend,
    repeats,
    recall_only=False,
    cold_probes=False,
    sizes=None,
    cases_filter=None,
    corpus="standard",
):

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
        "verification and operational followup for this component, and then we discussed what to do next with the team."
        for topic in TOPICS
        for revision in range(10)
    ]
    vectors = _embed_texts(templates)
    wings = ["engineering"] + [f"ops{i}" for i in range(6)] + ["small"]
    report = {
        "backend": backend,
        "corpus": corpus,
        "repeats": repeats,
        "cold_probes": cold_probes,
        "prompt_words": len(PROMPT.split()),
        "log_tokens": len(tokenize(LOG)),
        "rows": [],
        "recall": {},
        "date_question": {},
        "scope_index_overhead": {},
    }

    def document(i):
        if i < len(NEEDLES):
            return NEEDLES[i][1]
        if corpus == "standard":
            return f"{templates[i % 100]} Record {i:06d}."
        # Each everyday query word occurs in 1,400 drawers, not in all
        # templates or just a single needle. Disjoint term sets stress the
        # complete 11,200-row pre-rank pool before bounded text loading.
        word = MID_WORDS[i // 1400] if i < 1400 * len(MID_WORDS) else "archive"
        text = f"Routine archive entry {word}. Record {i:06d}."
        if corpus == "identifiers":
            text += (
                f" get_user_id_{i} lib/module_{i}.py trace.record.{i} node:{9000 + i} résumé 東京"
            )
        return text

    for size in (20_000,) if recall_only else (sizes or (60_000, 200_000)):
        with tempfile.TemporaryDirectory(prefix="query-budget-") as palace:
            col = get_collection(palace, create=True, backend=backend)
            for start in range(0, size, 500):
                stop = min(start + 500, size)
                col.add(
                    ids=[f"d{i}" for i in range(start, stop)],
                    documents=[document(i) for i in range(start, stop)],
                    embeddings=[
                        [0.0] * len(vectors[0]) if i < len(NEEDLES) else vectors[i % 100]
                        for i in range(start, stop)
                    ],
                    metadatas=[
                        {
                            "wing": "small" if i >= size - 64 else wings[i % 7],
                            "room": "notes",
                            "source_file": f"record-{i}.txt",
                            "chunk_index": 0,
                        }
                        for i in range(start, stop)
                    ],
                )
            from mempalace.palace import get_backend_for_palace

            store_backend = get_backend_for_palace(palace)
            if backend == "chroma" and corpus == "standard":
                from mempalace.backends._inproc_sqlite import open_writer
                from mempalace.backends.chroma_sqlite import SCOPE_INDEX, ensure_scope_index
                from mempalace.backends.scoped_lexical import lexical_reader

                # Establish the normal fast-reader guards before timing
                # in-process DDL beside Chroma's independent SQLite library.
                with lexical_reader(Path(palace) / "chroma.sqlite3"):
                    pass

                # Rewrite the same last 500 records: corpus content, ids and
                # embeddings stay fixed while the optional index is toggled.
                batch = list(range(size - 500, size))
                write = {
                    "ids": [f"d{i}" for i in batch],
                    "documents": [document(i) for i in batch],
                    "embeddings": [vectors[i % 100] for i in batch],
                    "metadatas": [
                        {
                            "wing": "small" if i >= size - 64 else wings[i % 7],
                            "room": "notes",
                            "source_file": f"record-{i}.txt",
                            "chunk_index": 0,
                        }
                        for i in batch
                    ],
                }
                samples = {"indexed": [], "unindexed": []}
                build_ms = []
                for repeat in range(5):
                    for indexed in (True, False) if repeat % 2 == 0 else (False, True):
                        with open_writer(Path(palace) / "chroma.sqlite3") as conn:
                            if indexed:
                                began = time.perf_counter()
                                created = ensure_scope_index(conn)
                                if created:
                                    build_ms.append((time.perf_counter() - began) * 1000)
                            else:
                                conn.execute(f"DROP INDEX IF EXISTS {SCOPE_INDEX}")
                        began = time.perf_counter()
                        col.upsert(**write)
                        samples["indexed" if indexed else "unindexed"].append(
                            (time.perf_counter() - began) * 1000
                        )
                with open_writer(Path(palace) / "chroma.sqlite3") as conn:
                    ensure_scope_index(conn)
                    index_bytes = conn.execute(
                        "SELECT sum(pgsize) FROM dbstat WHERE name=?", (SCOPE_INDEX,)
                    ).fetchone()[0]
                report["scope_index_overhead"][str(size)] = {
                    "index_bytes": index_bytes,
                    "build_p50_ms": float(np.percentile(build_ms, 50)),
                    "upsert_500_p50_ms": {
                        key: float(np.percentile(values, 50)) for key, values in samples.items()
                    },
                }

            def drawer_search(
                query, strategy, wing=None, limit=5, requested_wings=None, palace=palace
            ):
                return search_memories(
                    query,
                    palace,
                    n_results=limit,
                    wing=wing,
                    wings=requested_wings,
                    candidate_strategy=strategy,
                    include_facts=False,
                )

            shapes = [
                ("log123", LOG),
                ("prompt50", PROMPT),
                ("question12", QUESTION12),
                ("common8", COMMON8),
                ("mid8", " ".join(MID_WORDS)),
                ("long_literal", PROMPT + " ZQ9"),
                ("cjk", "项目部署配置故障排查"),
                ("capital50", "Remind" + PROMPT[len("Please") :]),
                (
                    "capital19",
                    "Remind me what the team decided about the deployment configuration and the error handling patterns for the authentication middleware last week",
                ),
                (
                    "capital_notes",
                    "Engineering notes: what did we decide about the deployment configuration and the error handling for this component with the team?",
                ),
            ]
            cases = [
                (f"{label}_{scope}", query, wing, gateway)
                for label, query in shapes
                for scope, wing, gateway in [
                    ("all", None, False),
                    ("wing", "engineering", False),
                    ("small", "small", False),
                    ("gateway8", None, True),
                ]
                if not cases_filter or f"{label}_{scope}" in cases_filter
            ]
            if recall_only:
                cases = []
            for label, query, wing, gateway in cases:

                def search(strategy, query=query, wing=wing, gateway=gateway):
                    if cold_probes:
                        store_backend.close_palace(palace)
                    if not gateway:
                        result = drawer_search(query, strategy, wing)
                    else:

                        def call_tool(principal, name, args):
                            if name == "mempalace_list_wings":
                                return {"wings": {w: {} for w in wings}}
                            return drawer_search(
                                args["query"],
                                strategy,
                                args.get("wing"),
                                args["limit"],
                                args.get("wings"),
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
            recall = {}
            for strategy in ("vector", "union", None):
                outcomes = {5: [], 10: []}
                for i, (query, _) in enumerate(NEEDLES):
                    for limit in outcomes:
                        result = search_memories(
                            query, palace, n_results=limit, candidate_strategy=strategy
                        )
                        outcomes[limit].append(
                            f"d{i}" in {h["drawer_id"] for h in result["results"]}
                        )
                recall[strategy or "default"] = {
                    f"at_{limit}": found for limit, found in outcomes.items()
                }
            report["recall"][str(size)] = recall
            from mempalace.lexical import _query_stop_words

            report["date_question"][str(size)] = {}
            for label, query in (("original", DATE_QUESTION), ("long", LONG_DATE_QUESTION)):
                result = drawer_search(query, None, limit=10)
                report["date_question"][str(size)][label] = {
                    "post_stopword_tokens": len(
                        [term for term in tokenize(query) if term not in _query_stop_words()]
                    ),
                    "needle_at_10": "d8" in {hit["drawer_id"] for hit in result["results"]},
                }
    report["recall_pass"] = all(
        all(found)
        for recall in report["recall"].values()
        for strategy in ("union", "default")
        for found in recall[strategy].values()
    )
    report["pass"] = report["recall_pass"] and all(row["pass"] for row in report["rows"])
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend", choices=["chroma", "sqlite_exact", "compact"], default="chroma"
    )
    parser.add_argument(
        "--corpus", choices=["standard", "moderate", "identifiers"], default="standard"
    )
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--recall-only", action="store_true")
    parser.add_argument(
        "--cold",
        "--cold-probes",
        dest="cold_probes",
        action="store_true",
        help="Close storage handles before every search; model remains warm. No probe cache exists.",
    )
    parser.add_argument("--sizes", nargs="+", type=int, default=[60000, 200000])
    parser.add_argument("--cases", nargs="+")
    args = parser.parse_args()
    report = run(
        args.backend,
        args.repeats,
        args.recall_only,
        args.cold_probes,
        args.sizes,
        args.cases,
        args.corpus,
    )
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["pass"] else 1)
