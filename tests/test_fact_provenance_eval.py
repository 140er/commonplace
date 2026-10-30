"""The invented 5% gate must retain its deciding source and proposal status."""

import importlib.util
from pathlib import Path


def test_provenance_recall_eval():
    path = Path(__file__).resolve().parents[1] / "benchmarks" / "fact_provenance_bench.py"
    spec = importlib.util.spec_from_file_location("fact_provenance_bench", path)
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    report = bench.run()
    assert report["passed"], report
    assert report["provenance_accuracy"] == 1.0
