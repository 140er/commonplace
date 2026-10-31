"""The fact-first production ranker meets the recall and comparison gates."""

import importlib
from pathlib import Path


def test_fact_first_recall_and_matching_successor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "benchmarks"))
    bench = importlib.import_module("fact_recall_bench")
    report = bench.run(120, retriever=bench.fact_first_recall)
    assert report["passed"], report
    changed = next(row for row in report["per_question"] if row["id"] == "changed-value")
    assert changed["got"] == ["pnpm", "npm"]
    extracted = bench.run(40, saver=bench.extraction_saver, retriever=bench.fact_first_recall)
    assert extracted["passed"], extracted


def test_fact_first_meets_every_bar_and_drawer_benchmark_does_not_regress(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "benchmarks"))
    bench = importlib.import_module("fact_first_bench")
    report = bench.compare(36)
    assert report["passed"], report
    assert report["fact_first_at_least_plain"] and report["drawer_recall_unchanged"]
    assert report["drawer_benchmark"]["plain"]["pooled_recall@1"] == 1.0
    assert report["drawer_benchmark"]["fact-first"]["pooled_recall@5"] == 1.0
