"""Honest counts, bounded queries and privacy against real SQLite/log fixtures."""

import copy
import hashlib
import json
import sqlite3
from datetime import datetime

import pytest
from activity_fixtures import AT, CANARY, END, START, logs, source

from commonplace.compute import activity_math as timing, activity_schema as wire
from commonplace.compute.activity_collector import (
    Limits,
    SourceUnavailable,
    collect,
    collect_and_report,
    command_category,
    connect,
    identity,
)


@pytest.fixture
def snapshot(tmp_path):
    path = source(tmp_path / "source.sqlite")
    return collect(path, START, END, logs=[logs(tmp_path / "trace.ndjson")])


def test_clip_union_exclusive_concurrency_and_native_deduplication(snapshot):
    m = snapshot["metrics"]
    assert m["agent_hours"]["value"] * 3600 == pytest.approx(300)
    assert m["runs_started"]["value"] == 3
    assert m["runs_requested"]["value"] == 4
    assert m["peak_agents"]["value"] == 3
    assert m["tool_hours"]["value"] * 3600 == pytest.approx(60)
    main = next(t for t in snapshot["threads"] if t["id"] == identity("thread", "main"))
    assert main["phases"] == {"tool": 60, "wait": 40, "compaction": 10, "unknown": 90}
    assert main["seconds"] == sum(main["phases"].values()) == 200
    assert sum(b["calls"] for b in main["buckets"]) == 3
    assert sum(d["seconds"] for d in snapshot["daily"]) == 300
    assert sum(row["seconds"] for row in snapshot["models"]) == 300
    assert snapshot["coverage"]["native_agents_timed"] == 1


def test_usage_scope_cancelled_asks_pr_snapshots_and_missing_values(snapshot):
    m, c = snapshot["metrics"], snapshot["coverage"]
    assert m["tokens_input"]["value"] == 100
    assert m["tokens_cached"]["value"] == 80
    assert m["tokens_output"]["value"] == 20
    assert m["cache_creation"] == {"value": None, "reason": "usage_missing"}
    assert c["turns_seen"] == 2 and c["turns_complete"] == 1
    assert m["context_over_90"]["value"] == 1
    assert m["asks"]["value"] == 2
    assert m["asks_answered"]["value"] == m["asks_cancelled"]["value"] == 1
    assert m["ask_typical_seconds"]["value"] == 40
    assert m["prs_merged"]["value"] == 1 and m["prs_open"]["value"] == 0
    assert c["pr_links"] == 2 and c["pr_unique"] == c["pr_states"] == 1
    assert m["tasks_finished"]["reason"] == "task_mapping_missing"
    assert m["thinking_hours"]["reason"] == "thinking_not_measured"
    assert m["waiting_on_owner_hours"]["reason"] == "correlation_missing"
    assert m["lines_added"]["value"] == m["lines_deleted"]["value"] == 1
    test = next(s for s in snapshot["sinks"] if s["category"] == "test")
    assert test["count"] == test["timed"] == test["failed"] == 1
    assert test["p95"] == 60 and test["share"] is None


def test_source_and_every_untrusted_field_stay_private(snapshot, caplog, capsys):
    encoded = json.dumps(snapshot)
    assert CANARY not in encoded
    assert CANARY not in caplog.text + capsys.readouterr().out
    assert all(thread["id"].startswith("thread_") for thread in snapshot["threads"])
    assert snapshot["spans"][0]["category"] == "other"
    assert snapshot["coverage"]["logs_skipped"] == 1
    assert wire.loads(encoded.encode()) == snapshot


def test_all_wire_text_leaves_reject_arbitrary_strings_without_echo(snapshot):
    def leaves(value, path=()):
        if isinstance(value, dict):
            for key, item in value.items():
                yield from leaves(item, (*path, key))
        elif isinstance(value, list):
            for key, item in enumerate(value):
                yield from leaves(item, (*path, key))
        elif isinstance(value, str):
            yield path

    for path in leaves(snapshot):
        bad = copy.deepcopy(snapshot)
        parent = bad
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = CANARY
        with pytest.raises(wire.InvalidSnapshot) as caught:
            wire.loads(json.dumps(bad).encode())
        assert CANARY not in str(caught.value)
    bad = copy.deepcopy(snapshot)
    bad["threads"][0][CANARY] = CANARY
    with pytest.raises(wire.InvalidSnapshot, match="^invalid_activity_snapshot$"):
        wire.validate(bad)


@pytest.mark.parametrize("start,expected", [("2026-03-29", 23), ("2026-10-25", 25)])
def test_local_days_include_both_dst_boundaries(start, expected):
    at = datetime.fromisoformat(start).replace(tzinfo=timing.ZONE)
    end = at.replace(day=at.day + 1)
    parts = list(timing.civil_parts(at.timestamp(), end.timestamp()))
    assert parts == [(start, expected * 3600)]


def test_midnight_open_intervals_empty_and_nearest_rank():
    a = datetime(2026, 11, 18, 23, 30, tzinfo=timing.ZONE).timestamp()
    assert list(timing.civil_parts(a, a + 3600)) == [("2026-11-18", 1800), ("2026-11-19", 1800)]
    assert timing.clipped(AT, None, (START, END)) is None
    assert timing.clipped(AT, None, (AT + 10, AT + 20), open_at=AT + 20) == (AT + 10, AT + 20)
    assert timing.clipped(AT + 20, AT, (START, END)) is None
    assert timing.percentile([]) is None and timing.percentile([5]) == 5
    assert timing.percentile(list(range(1, 21))) == 19
    assert timing.peak([[(AT, AT + 10)], [(AT + 10, AT + 20)]]) == 1


def test_per_model_splits_real_switches_without_double_counting():
    segments = timing.phases([(AT, AT + 20)], {})
    runs = [
        {"interval": (AT, AT + 10), "model": "first", "ordinal": 1},
        {"interval": (AT + 10, AT + 20), "model": "second", "ordinal": 2},
    ]
    assert list(timing.model_parts(segments, runs, "other")) == [
        (AT, AT + 10, "unknown", "first"),
        (AT + 10, AT + 20, "unknown", "second"),
    ]


def test_readonly_query_plan_pagination_and_caps(tmp_path):
    path = source(tmp_path / "source.sqlite")
    with sqlite3.connect(path) as db:
        for n in range(300):
            db.execute(
                "INSERT INTO orchestration_v2_projection_turn_items VALUES (?,?,?,?,?,?,?)",
                (
                    f"tie{n:04}",
                    "r1",
                    "main",
                    5,
                    "dynamic_tool",
                    "completed",
                    json.dumps(
                        {
                            "startedAt": timing.iso(AT + 2),
                            "completedAt": timing.iso(AT + 3),
                            "toolName": CANARY,
                        }
                    ),
                ),
            )
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    db = connect(path)
    try:
        assert db.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            db.execute("CREATE TABLE forbidden(x)")
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT type FROM orchestration_v2_projection_turn_items WHERE run_id=? ORDER BY ordinal",
            ("r1",),
        ).fetchall()
        assert any("USING INDEX item_run_ordinal" in row[3] for row in plan)
    finally:
        db.close()
    complete = collect(path, START, END)
    assert complete["metrics"]["tool_calls"]["value"] == 302
    limited = collect(path, START, END, limits=Limits(items=10))
    assert limited["coverage"]["partial"]
    assert limited["coverage"]["items_seen"] == 10
    assert before == hashlib.sha256(path.read_bytes()).hexdigest()


def test_offline_or_malformed_source_is_not_empty_fleet(tmp_path):
    path = tmp_path / CANARY
    with pytest.raises(SourceUnavailable, match="^activity_source_unavailable$"):
        collect(path, START, END)
    assert not path.exists()


def test_missing_run_timing_is_unknown_and_active_native_count_does_not_need_timing(tmp_path):
    path = source(tmp_path / "source.sqlite")
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE orchestration_v2_projection_runs SET completed_at=NULL,"
            "payload_json=json_set(payload_json,'$.startedAt',NULL) WHERE status='completed'"
        )
        db.execute(
            "UPDATE orchestration_v2_projection_subagents SET status='running',"
            "started_at=NULL,completed_at=NULL,child_thread_id=NULL"
        )
    snapshot = collect(path, START, END)
    assert snapshot["coverage"]["runs_untimed"] == 3
    assert snapshot["coverage"]["runs_timed"] == 0
    assert snapshot["coverage"]["native_agents_timed"] == 0
    assert snapshot["coverage"]["active_native"] == 2
    assert snapshot["metrics"]["agent_hours"] == {"value": None, "reason": "no_timing"}


def test_transport_is_explicit_canary_free_and_arguments_are_not_labels(tmp_path):
    calls = []
    collect_and_report(
        source(tmp_path / "source.sqlite"),
        START,
        END,
        lambda path, body: calls.append((path, body)),
    )
    assert calls[0][0] == "/api/runner/activity" and CANARY not in json.dumps(calls)
    assert command_category("echo pytest " + CANARY) == "shell"
    assert command_category("python -m pytest " + CANARY) == "test"
    assert command_category("uv run python -m pytest " + CANARY) == "test"
    assert command_category("make " + CANARY) == "build"
