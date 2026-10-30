"""Gate for the fact recall eval (benchmarks/fact_recall_bench.py).

The eval answers questions from the fact index alone and checks each answer
against the verbatim chat it cites. These tests hold the index to its bar,
and prove each metric fails when the behavior it measures breaks.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

# tests/benchmarks shadows the top-level benchmarks/ directory, so load by path.
_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "fact_recall_bench.py"
_SPEC = importlib.util.spec_from_file_location("fact_recall_bench", _PATH)
bench = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = bench  # dataclasses look their module up by name
_SPEC.loader.exec_module(bench)


def test_the_fact_index_meets_the_bar():
    report = bench.run()
    misses = [row for row in report["per_question"] if not row["ok"]]
    assert misses == []
    assert report["recall_at_1"] == 1.0
    assert report["recall_at_5"] == 1.0
    assert report["stale_rate"] == 0.0
    assert report["source_hit_rate"] == 1.0
    assert report["old_thread_recall"] == 1.0
    assert report["private_leaks"] == 0
    assert report["merge_rate"] == 1.0


@pytest.mark.parametrize("threads", [36, 120])
def test_a_fact_only_in_the_oldest_thread_is_recalled(tmp_path, threads):
    """The deploy port is said once, in the oldest thread, behind every later one."""
    corpus = bench.build_corpus(threads)
    oldest = corpus.threads[0]
    assert oldest.day == min(t.day for t in corpus.threads)
    assert [t.key for t in corpus.threads if any("8443" in m.text for m in t.messages)] == [
        oldest.key
    ]
    report = bench.run(threads, workdir=str(tmp_path))
    rows = {row["id"]: row for row in report["per_question"]}
    for qid in ("old-thread-port", "old-thread-port-scoped"):
        assert rows[qid]["ok"] and rows[qid]["got"][0] == "8443", rows[qid]
    assert report["old_thread_recall"] == 1.0


def test_the_cited_range_is_the_exact_message(tmp_path):
    from commonplace.handoff.thread_store import ThreadStore
    from mempalace.knowledge_graph import KnowledgeGraph

    corpus = bench.build_corpus()
    store = ThreadStore(str(tmp_path / "threads.sqlite3"))
    kg = KnowledgeGraph(db_path=str(tmp_path / "kg.sqlite3"))
    try:
        for thread in corpus.threads:
            stored = store.store(bench.WORKSPACE, thread.key, thread.messages, actor="eval")
            bench.oracle_saver(kg, stored.id, thread)
        question = next(q for q in corpus.questions if q.id == "old-thread-port")
        (fact,) = bench.recall(kg, question)
        (source,) = kg.sources([fact["id"]])[fact["id"]]
        oldest = store.by_source(bench.WORKSPACE, corpus.threads[0].key)
        assert source["source_thread"] == oldest.id
        (message,) = store.messages(oldest.id, source["source_seq_start"], source["source_seq_end"])
        assert message.text == "For Kestrel the deploy port is 8443, don't forget it."
    finally:
        kg.close()
        store.close()


def test_the_corpus_is_deterministic():
    a, b = bench.build_corpus(seed=3), bench.build_corpus(seed=3)
    assert [[m.text for m in t.messages] for t in a.threads] == [
        [m.text for m in t.messages] for t in b.threads
    ]
    with pytest.raises(ValueError):
        bench.build_corpus(threads=10)


# ── the metrics bite ───────────────────────────────────────────────────────


def _naive_saver(kg, stored_id, thread):
    """Files every fact as a plain add: no merge into history, no supersede."""
    for fact in thread.facts:
        kg.add_triple(
            fact.subject,
            fact.predicate,
            fact.object,
            valid_from=fact.valid_from,
            source_thread=stored_id,
            source_seq_start=fact.seq,
            wing=fact.wing,
            sensitivity=fact.sensitivity,
        )
    return len(thread.facts)


def _wrong_range_saver(kg, stored_id, thread):
    for fact in thread.facts:
        kg.save_fact(
            fact.subject,
            fact.predicate,
            fact.object,
            valid_from=fact.valid_from,
            source_thread=stored_id,
            # Past the end of every thread: the range opens nothing.
            source_seq_start=fact.seq + 10,
            wing=fact.wing,
            sensitivity=fact.sensitivity,
            single_valued=fact.single_valued,
        )
    return len(thread.facts)


def test_stale_values_are_caught(tmp_path):
    report = bench.run(saver=_naive_saver, workdir=str(tmp_path))
    # npm and pnpm both stay open: the replaced value still reads as current.
    assert report["stale_rate"] > 0


def test_wrong_source_ranges_are_caught(tmp_path):
    report = bench.run(saver=_wrong_range_saver, workdir=str(tmp_path))
    assert report["source_hit_rate"] < 1.0
    assert report["old_thread_recall"] < 1.0


def test_leaks_are_caught(tmp_path, monkeypatch):
    # A reader with no wing or sensitivity filter sees the private facts.
    monkeypatch.setitem(bench.SCOPED, "wings", None)
    monkeypatch.setitem(bench.SCOPED, "sensitivities", None)
    report = bench.run(workdir=str(tmp_path))
    assert report["private_leaks"] == 2


def test_lost_merges_are_caught(tmp_path):
    def split_saver(kg, stored_id, thread):
        for fact in thread.facts:
            # A different wing per thread: restatements never meet.
            wing = fact.wing if fact.predicate != "default_branch" else f"w{stored_id[-4:]}"
            kg.save_fact(
                fact.subject,
                fact.predicate,
                fact.object,
                source_thread=stored_id,
                source_seq_start=fact.seq,
                wing=wing,
            )
        return len(thread.facts)

    report = bench.run(saver=split_saver, workdir=str(tmp_path))
    assert report["merge_rate"] == 0.0


def test_the_cli_prints_the_metrics(capsys):
    assert bench.main(["--threads", "36"]) == 0
    out = capsys.readouterr().out
    assert "recall_at_1" in out and "private_leaks      0" in out and "MISS" not in out
