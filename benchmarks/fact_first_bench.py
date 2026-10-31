#!/usr/bin/env python3
"""Fact-first vs plain drawer search on the fact-recall corpus (RFC 009).

Run from a checkout: uv run python benchmarks/fact_first_bench.py --threads 120
No models or network: drawers have explicit dummy vectors and use the product's
SQLite BM25 fallback. Each full thread is one verbatim drawer. A projection maps
retrieved source IDs back to scoped facts for the fact eval's answer/source
metrics; it never uses the expected answer or temporal/status ranking. Both
readers use the same structured subject/predicate key. The separate drawer gate
uses the existing search_benchmark runner on real product search results.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fact_recall_bench as recall_bench
import search_benchmark as drawer_bench
from mempalace.knowledge_graph import KnowledgeGraph
from mempalace.palace import get_collection
from mempalace.paths import knowledge_graph_path
from mempalace.searcher import search_memories


def compare(threads=120):
    corpus = recall_bench.build_corpus(threads)
    with tempfile.TemporaryDirectory() as tmp:
        palace = str(Path(tmp) / "palace")
        collection = get_collection(palace, create=True)

        def save(kg, stored_id, thread):
            collection.upsert(
                ids=[stored_id],
                documents=["\n".join(m.text for m in thread.messages)],
                embeddings=[[1.0, 0.0]],
                metadatas=[
                    {
                        "wing": thread.facts[0].wing if thread.facts else "shared",
                        "room": thread.day,
                        "source_file": thread.key,
                    }
                ],
            )
            return recall_bench.oracle_saver(kg, stored_id, thread)

        def plain(kg, question, k=5):
            # Search verbatim drawers first, independently of the KG. No dates,
            # lifecycle or gold values influence the drawer retrieval order.
            hits = []
            for wing in question.reader["wings"] or [None]:
                result = search_memories(
                    f"{question.subject} {question.predicate.replace('_', ' ')}",
                    palace,
                    wing=wing,
                    n_results=k,
                    vector_disabled=True,
                    include_facts=False,
                )
                assert not result.get("error"), result
                hits.extend(result["results"])
            hits.sort(key=lambda h: -h.get("bm25_score", 0))
            scoped = [
                f
                for f in kg.query_entity(
                    question.subject,
                    direction="outgoing",
                    wings=question.reader["wings"],
                    sensitivities=question.reader["sensitivities"],
                )
                if f["predicate"] == question.predicate
            ]
            sources = kg.sources([f["id"] for f in scoped])
            found = []
            for hit in hits[:k]:
                for fact in scoped:
                    if fact not in found and any(
                        s.get("source_thread") == hit["drawer_id"] for s in sources[fact["id"]]
                    ):
                        found.append(fact)
            return found[:k]

        plain_report = recall_bench.run(threads, saver=save, retriever=plain, corpus=corpus)
        fact_report = recall_bench.run(
            threads, retriever=recall_bench.fact_first_recall, corpus=corpus
        )
        # Use the actual stored source IDs as judgments, never the first hit.
        stored = collection.get()
        by_key = {m["source_file"]: i for i, m in zip(stored.ids, stored.metadatas)}
        with KnowledgeGraph(db_path=knowledge_graph_path(palace)) as kg:
            kg.save_fact(
                "Kestrel",
                "deploy_port",
                "8443",
                wing="project_kestrel",
                source_drawer_id=by_key["kestrel-setup"],
            )
        cases = [
            drawer_bench.BenchmarkCase(
                "port",
                "deploy port 8443",
                {by_key["kestrel-setup"]: 3},
                filters={"wing": "project_kestrel"},
            ),
            drawer_bench.BenchmarkCase(
                "contact",
                "Ada Lovelace backup contact",
                {by_key["t34"]: 3},
                filters={"wing": "project_kestrel"},
            ),
            drawer_bench.BenchmarkCase(
                "package",
                "Avery pnpm",
                {by_key["t25"]: 3},
                filters={"wing": "shared"},
            ),
        ]

        def algorithm(include_facts):
            def search(case, limit):
                result = search_memories(
                    case.query,
                    palace,
                    **case.filters,
                    n_results=limit,
                    vector_disabled=True,
                    include_facts=include_facts,
                )
                assert not result.get("error"), result
                return [
                    drawer_bench.SearchHit(h["drawer_id"], h.get("bm25_score", 0))
                    for h in result["results"]
                ]

            return SimpleNamespace(name="fact-first" if include_facts else "plain", search=search)

        drawers = drawer_bench.run_benchmark(
            cases, [algorithm(False), algorithm(True)], limit=5, ks=(1, 5), warmups=0, repeats=1
        )
        lower = {"stale_rate", "private_leaks", "false_facts"}
        compared = all(
            fact_report[key] <= plain_report[key]
            if key in lower
            else fact_report[key] >= plain_report[key]
            for key in recall_bench.BARS
        )
        drawer_equal = drawers["algorithms"]["plain"]["rankings"] == drawers["algorithms"][
            "fact-first"
        ]["rankings"] and (
            drawers["algorithms"]["plain"]["quality"]
            == drawers["algorithms"]["fact-first"]["quality"]
        )
        return {
            "threads": threads,
            "plain_drawers": {key: plain_report[key] for key in recall_bench.BARS},
            "fact_first": {key: fact_report[key] for key in recall_bench.BARS},
            "drawer_benchmark": {
                name: row["quality"] for name, row in drawers["algorithms"].items()
            },
            "fact_first_at_least_plain": compared,
            "drawer_recall_unchanged": drawer_equal,
            "passed": fact_report["passed"] and compared and drawer_equal,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threads", type=int, default=120)
    result = compare(parser.parse_args().threads)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
