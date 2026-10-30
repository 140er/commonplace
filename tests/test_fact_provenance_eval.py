"""The invented 5% gate must retain its deciding source and proposal status."""

import importlib.util
from pathlib import Path

import pytest


def _bench():
    path = Path(__file__).resolve().parents[1] / "benchmarks" / "fact_provenance_bench.py"
    spec = importlib.util.spec_from_file_location("fact_provenance_bench", path)
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    return bench


def test_provenance_recall_eval():
    report = _bench().run()
    assert report["passed"], report
    assert report["provenance_accuracy"] == 1.0


@pytest.mark.parametrize("fault", ["approval", "source"])
def test_provenance_eval_fails_when_recall_launders_authority_or_loses_source(monkeypatch, fault):
    bench = _bench()
    original = bench.KnowledgeGraph.query_entity

    def corrupted(kg, *args, **kwargs):
        rows = original(kg, *args, **kwargs)
        for row in rows:
            if row["object"] == "5%":
                if fault == "approval":
                    row["fact_status"] = "approved"
                    row["approved_by"] = "eval-user"
                else:
                    row["origin_link"] = "t3-thread://v1/mcp:unrelated"
        return rows

    monkeypatch.setattr(bench.KnowledgeGraph, "query_entity", corrupted)
    report = bench.run()
    assert not report["passed"] and report["provenance_accuracy"] < 1.0
    check = "invented_rule_is_proposal" if fault == "approval" else "proposal_has_exact_source"
    assert not report["checks"][check]


def test_provenance_eval_fails_when_a_later_proposal_defeats_person_approval(monkeypatch):
    bench = _bench()
    monkeypatch.setattr("mempalace.fact_provenance.may_replace_approved", lambda *_args: False)
    report = bench.run()
    assert not report["passed"] and report["provenance_accuracy"] < 1.0
    assert not report["checks"]["earlier_user_decision_retires_later_proposal"]
