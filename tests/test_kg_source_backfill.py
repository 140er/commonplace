"""Source recovery completes once without blocking later fact-search reads."""

import sqlite3
import subprocess
import sys

import pytest

from mempalace.knowledge_graph import KnowledgeGraph, open_graph
from mempalace.searcher.facts import search_facts
from tests.test_kg_fact_source import _legacy_graph


@pytest.mark.parametrize("completed", [False, True])
def test_read_open_and_fact_search_do_not_wait_for_a_writer(tmp_path, monkeypatch, completed):
    path = str(tmp_path / "graph.db")
    with KnowledgeGraph(path) as graph:
        triple = graph.add_triple("subject", "uses", "tool", source_thread="thread:source")
        if not completed:
            graph._conn().execute("PRAGMA user_version = 0")
    original = sqlite3.connect
    writer = original(path)
    writer.execute("BEGIN IMMEDIATE")

    def connect(*args, **kwargs):
        kwargs["timeout"] = 0
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    try:
        # A write attempt fails immediately rather than hiding a busy wait.
        with open_graph(path, read=True) as graph:
            assert not graph._conn().in_transaction
            assert graph._conn().execute("PRAGMA user_version").fetchone()[0] == int(completed)
            assert triple in {row["id"] for row in search_facts(graph, "tool")}
    finally:
        writer.rollback()
        writer.close()


def test_interruption_before_completion_marker_rolls_back_the_backfill(tmp_path):
    path = str(tmp_path / "legacy.db")
    _legacy_graph(path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE triples SET source_file='source.txt'")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sqlite3, sys
from mempalace.knowledge_graph import KnowledgeGraph
original = sqlite3.connect
class Interrupted(sqlite3.Connection):
    def execute(self, sql, *args, **kwargs):
        if sql.startswith('PRAGMA user_version ='):
            assert self.in_transaction
            assert super().execute('SELECT count(*) FROM fact_sources').fetchone()[0] == 1
            os._exit(37)
        return super().execute(sql, *args, **kwargs)
def connect(*args, **kwargs):
    kwargs['factory'] = Interrupted
    return original(*args, **kwargs)
sqlite3.connect = connect
KnowledgeGraph(sys.argv[1])
""",
            path,
        ],
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 37, result.stderr.decode()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM fact_sources").fetchone()[0] == 0
    with KnowledgeGraph(path) as graph:
        assert graph._conn().execute("PRAGMA user_version").fetchone()[0] == 1
        (source,) = graph.sources(["t_old"])["t_old"]
        assert source["source_file"] == "source.txt"
    with KnowledgeGraph(path) as graph:
        assert graph.sources(["t_old"])["t_old"] == [source]


def test_completed_migration_open_cost_does_not_grow_with_graph_size(tmp_path, monkeypatch):
    original = sqlite3.connect

    class Counted(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.steps = 0
            self.set_progress_handler(self.count, 100)

        def count(self):
            self.steps += 1
            return 0

    def connect(*args, **kwargs):
        kwargs["factory"] = Counted
        return original(*args, **kwargs)

    steps = []
    for count in (1, 50_000):
        path = str(tmp_path / f"graph-{count}.db")
        with KnowledgeGraph(path) as graph:
            graph._conn().executemany(
                "INSERT INTO triples(id, subject, predicate, object, source_file) VALUES (?, ?, ?, ?, ?)",
                (
                    (f"fact-{number}", "subject", "uses", "tool", "source.txt")
                    for number in range(count)
                ),
            )
            graph._conn().commit()
        with monkeypatch.context() as patch:
            patch.setattr(sqlite3, "connect", connect)
            with open_graph(path, read=True) as graph:
                steps.append(graph._conn().steps)
                assert graph._conn().execute("SELECT count(*) FROM fact_sources").fetchone()[0] == 0
    # Count VM work instead of using a machine-dependent wall-clock threshold.
    assert steps[1] <= steps[0] + 20


@pytest.mark.parametrize("unmarked", [False, True])
def test_reopening_between_replicated_facts_and_sources_keeps_peer_provenance(tmp_path, unmarked):
    source_path, target_path = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    with KnowledgeGraph(source_path) as source:
        triple = source.add_triple(
            "subject",
            "uses",
            "tool",
            source_thread="thread:peer",
            source_seq_start=2,
            source_seq_end=4,
        )
        entities, facts, sources = (
            source.dump_rows(table) for table in ("entities", "triples", "fact_sources")
        )
        expected = source.sources([triple])[triple]
        assert expected[0]["origin_kind"] == "thread"
        assert expected[0]["origin_id"] == "thread:peer"
    with KnowledgeGraph(target_path) as target:
        for row in entities:
            target.apply_row("entities", row)
        if unmarked:
            target._conn().execute("PRAGMA user_version = 0")
        for row in facts:
            target.apply_row("triples", row)
    with KnowledgeGraph(target_path) as target:
        assert target._conn().execute("PRAGMA user_version").fetchone()[0] == 1
        assert target.sources([triple])[triple] == []
        for row in sources:
            target.apply_row("fact_sources", row)
        assert target.sources([triple])[triple] == expected
