"""Gate for the fact recall eval (benchmarks/fact_recall_bench.py).

The eval answers questions from the fact index alone and checks each answer
against the verbatim chat it cites. These tests hold the index to its bar,
and prove each metric fails when the behavior it measures breaks.
"""

import importlib.util
import json
import sys
from dataclasses import replace
from datetime import date, datetime
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
    assert report["extraction_recall"] == 1.0
    assert report["temporal_precision"] == 1.0
    assert report["denied_ok"] == 1.0
    assert report["max_source_messages"] == 2
    assert report["passed"]


@pytest.mark.parametrize("threads", [36, 120, 400])
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


def test_the_corpus_has_real_dates_and_newer_competing_facts():
    corpus = bench.build_corpus(400)
    for thread in corpus.threads:
        assert date.fromisoformat(thread.day)
        assert all(datetime.fromisoformat(m.at.replace("Z", "+00:00")) for m in thread.messages)
        assert all(f.valid_from for f in thread.facts)
    competing = [
        (thread, fact)
        for thread in corpus.threads
        for fact in thread.facts
        if fact.subject == "Kestrel" and "deploy_port" in fact.predicate
    ]
    assert len(competing) == 3
    assert all(f.valid_from > corpus.threads[0].day for _, f in competing[1:])


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


@pytest.mark.parametrize("ignored", [("wings",), ("sensitivities",), ("wings", "sensitivities")])
def test_leaks_are_caught(tmp_path, ignored):
    def unfiltered(kg, question):
        reader = {**question.reader, **dict.fromkeys(ignored)}
        return bench.recall(kg, replace(question, reader=reader))

    report = bench.run(workdir=str(tmp_path), retriever=unfiltered)
    assert report["private_leaks"] > 0
    assert not report["passed"]


def test_lost_merges_are_caught(tmp_path):
    def split_saver(kg, stored_id, thread):
        for fact in thread.facts:
            # A different wing per thread: restatements never meet.
            wing = fact.wing if fact.predicate != "default_branch" else thread.key
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
    assert report["merge_rate"] < 1.0
    assert report["extraction_recall"] < 1.0


def test_the_cli_prints_the_metrics(capsys):
    assert bench.main(["--threads", "36"]) == 0
    out = capsys.readouterr().out
    assert "recall_at_1" in out and "private_leaks" in out and "MISS" not in out


def test_as_of_catches_ignored_end_dates(tmp_path, monkeypatch):
    import mempalace.knowledge_graph as graph

    def ignores_end(as_of):
        start = graph._sql_temporal_start_expr("t.valid_from")
        return f" AND (t.valid_from IS NULL OR {start} <= ?)", [graph._temporal_start_key(as_of)]

    monkeypatch.setattr(graph, "_temporal_filter_sql", ignores_end)
    report = bench.run(workdir=str(tmp_path))
    assert report["recall_at_1"] == 1.0  # Correct top answer alone hid this bug.
    assert report["temporal_precision"] < 1.0
    assert report["stale_rate"] > 0.0
    assert not report["passed"]


def test_superseded_labels_do_not_hide_unclosed_facts(tmp_path):
    def unclosed(kg, stored_id, thread):
        bench.oracle_saver(kg, stored_id, thread)
        with kg._conn() as conn:
            conn.execute("UPDATE triples SET valid_to=NULL WHERE status='superseded'")

    report = bench.run(saver=unclosed, workdir=str(tmp_path))
    assert report["stale_rate"] > 0.0
    assert not report["passed"]


@pytest.mark.parametrize(
    "corruption", ["whole-thread", "echo", "missing-thread", "partial", "merged"]
)
def test_inexact_or_missing_sources_are_caught(tmp_path, corruption):
    def corrupt_saver(kg, stored_id, thread):
        bench.oracle_saver(kg, stored_id, thread)
        with kg._conn() as conn:
            if corruption == "whole-thread":
                conn.execute("UPDATE fact_sources SET source_seq_start=0, source_seq_end=1000000")
            elif corruption == "echo" and thread.key == "kestrel-setup":
                conn.execute("UPDATE fact_sources SET source_seq_start=3, source_seq_end=3")
            elif corruption == "missing-thread":
                conn.execute(
                    "UPDATE fact_sources SET source_thread=? WHERE source_thread=?",
                    ("missing:" + stored_id, stored_id),
                )
            elif corruption == "partial" and thread.key == "t34":
                conn.execute(
                    "UPDATE fact_sources SET source_seq_start=source_seq_end WHERE source_thread=?",
                    (stored_id,),
                )
            elif corruption == "merged" and thread.key in ("t12", "t33"):
                conn.execute(
                    "UPDATE fact_sources SET source_seq_start=99, source_seq_end=99 "
                    "WHERE source_thread=?",
                    (stored_id,),
                )

    report = bench.run(saver=corrupt_saver, workdir=str(tmp_path))
    assert report["source_hit_rate"] < 1.0
    assert report["extraction_recall"] < 1.0
    if corruption == "merged":
        assert report["merge_rate"] < 1.0
    assert not report["passed"]


@pytest.mark.parametrize(
    "value, text, hit",
    [
        ("npm", "pnpm", False),
        ("main", "remain", False),
        ("npm", "Use npm.", True),
        ("Ada Lovelace", "Ada Lovelace is here.", True),
    ],
)
def test_verbatim_value_matching_requires_whole_words(value, text, hit):
    assert bench._contains_value(text, value) is hit


def test_a_missing_non_answered_fact_is_caught_by_filing_coverage(tmp_path):
    def skips_near_miss(kg, stored_id, thread):
        if thread.key != "t26":
            bench.oracle_saver(kg, stored_id, thread)

    report = bench.run(saver=skips_near_miss, workdir=str(tmp_path))
    assert report["recall_at_1"] == 1.0
    assert report["extraction_recall"] < 1.0
    assert not report["passed"]


def test_identical_values_in_different_wings_do_not_merge(tmp_path):
    def collapses_wings(kg, stored_id, thread):
        if thread.key == "t32":
            thread = replace(
                thread, facts=[replace(f, wing="project_kestrel") for f in thread.facts]
            )
        bench.oracle_saver(kg, stored_id, thread)

    report = bench.run(saver=collapses_wings, workdir=str(tmp_path))
    assert report["merge_rate"] < 1.0
    assert report["extraction_recall"] < 1.0
    assert not report["passed"]


def test_denied_questions_count_wrong_non_hidden_values(tmp_path):
    def wrong_denial(kg, question):
        if question.answer is None:
            other = bench.Question("other", "Heron", "deploy_port", "9000", bench.OWNER)
            return bench.recall(kg, other)
        return bench.recall(kg, question)

    report = bench.run(workdir=str(tmp_path), retriever=wrong_denial)
    assert report["private_leaks"] == 0
    assert report["denied_ok"] == 0.0
    assert not report["passed"]


@pytest.mark.parametrize("json_output", [False, True])
def test_the_cli_exits_nonzero_when_the_bar_fails(monkeypatch, capsys, json_output):
    monkeypatch.setitem(bench.SAVERS, "broken", _wrong_range_saver)
    args = ["--threads", "36", "--saver", "broken"]
    assert bench.main(args + (["--json"] if json_output else [])) == 1
    out = capsys.readouterr().out
    if json_output:
        assert not json.loads(out)["passed"]
    else:
        assert "FAIL source_hit_rate" in out


def test_the_cli_rejects_too_few_threads():
    with pytest.raises(SystemExit) as exc:
        bench.main(["--threads", "35"])
    assert exc.value.code == 2


@pytest.mark.parametrize("threads", [36, 120])
def test_real_extraction_meets_its_grounded_private_fixture(threads):
    report = bench.run(threads=threads, saver=bench.extraction_saver)
    assert report["passed"], report
    assert all(report[key] == bar for key, bar in bench.BARS.items())
    assert report["old_thread_recall"] == 1.0


def test_extraction_fixture_is_valid_independently_of_the_extractor():
    corpus = bench.build_extraction_corpus()
    report = bench.run(corpus=corpus)
    assert report["passed"], report
    missing = bench.run(corpus=corpus, saver=lambda *_args: None)
    assert missing["extraction_recall"] == missing["old_thread_recall"] == 0.0
    assert not missing["passed"]


def test_extractor_receives_only_messages_and_trusted_metadata(monkeypatch):
    from commonplace.handoff import facts
    from commonplace.handoff.models import ThreadMessage

    original = facts.extract
    calls = []

    def messages_only(messages, **metadata):
        assert all(isinstance(message, ThreadMessage) for message in messages)
        assert set(metadata) == {"user", "fallback_at"}
        calls.append(len(messages))
        return original(messages, **metadata)

    monkeypatch.setattr(facts, "extract", messages_only)
    assert bench.run(saver=bench.extraction_saver)["passed"]
    assert len(calls) == 40


def test_the_cli_runs_actual_extraction(capsys):
    assert bench.main(["--saver", "extract", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["extraction_recall"] == 1.0
