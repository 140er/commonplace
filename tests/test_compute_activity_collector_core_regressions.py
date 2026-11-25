"""Collection boundaries and current authority, using disposable synthetic data."""

import collections
import json
import sqlite3

import pytest
from activity_core_fixtures import AT, END, START, source

from commonplace.compute import activity_math as timing, activity_schema as wire
from commonplace.compute.activity_collector import Collector, Limits, collect

PREFIX = "orchestration_v2_projection_"


def test_current_usage_and_context_survive_historical_turn_prefix(tmp_path):
    path = source(tmp_path / "source.sqlite")
    with sqlite3.connect(path) as db:
        db.execute(f"UPDATE {PREFIX}provider_turns SET ordinal=ordinal+10")
        for i in range(3):
            db.execute(
                f"INSERT INTO {PREFIX}provider_turns VALUES (?,?,?,?,?,?,?)",
                (
                    f"old-{i}",
                    "main",
                    i,
                    timing.iso(START - 100),
                    timing.iso(START - 90),
                    "ar1",
                    "{}",
                ),
            )
    snapshot = collect(path, START, END, limits=Limits(turns=2))
    assert snapshot["metrics"]["tokens_input"]["value"] == 100
    assert snapshot["metrics"]["tokens_cached"]["value"] == 80
    assert snapshot["metrics"]["tokens_output"]["value"] == 20
    assert snapshot["metrics"]["turns_with_context"]["value"] == 2
    assert snapshot["coverage"]["turns_seen"] == 2
    assert not snapshot["coverage"]["partial"]


def test_auxiliary_scans_and_retained_samples_obey_the_budget(tmp_path, monkeypatch):
    path = source(tmp_path / "source.sqlite")
    with sqlite3.connect(path) as db:
        for i in range(40):
            db.execute(
                f"INSERT INTO {PREFIX}runtime_requests VALUES (?,?,?,?,?,?)",
                (
                    "main",
                    "user_input",
                    "resolved",
                    timing.iso(AT + 5),
                    timing.iso(AT + 6),
                    '{"answers":{}}',
                ),
            )
            db.execute(
                f"INSERT INTO {PREFIX}run_attempts VALUES (?,?,?,?)",
                (
                    f"retry-{i}",
                    "r1",
                    "completed",
                    json.dumps(
                        {
                            "reason": "retry",
                            "startedAt": timing.iso(AT + 5),
                            "completedAt": timing.iso(AT + 6),
                        }
                    ),
                ),
            )
            db.execute(
                f"INSERT INTO {PREFIX}context_transfers VALUES (?,?,?,?,?,?,?)",
                (
                    "main",
                    "child",
                    "r3",
                    "provider_handoff",
                    timing.iso(AT + 10),
                    "resolved_native",
                    '{"sourcePoint":{"runId":"r1"}}',
                ),
            )
    counts: collections.Counter[str] = collections.Counter()
    original_rows, original_finish = Collector.rows, Collector.finish
    retained = {}

    def rows(self, sql, args=()):
        table = next(
            (
                name
                for name in ("runtime_requests", "run_attempts", "context_transfers")
                if PREFIX + name in sql and "provider_turns" not in sql
            ),
            None,
        )
        for row in original_rows(self, sql, args):
            if table:
                counts[table] += 1
            yield row

    def finish(self):
        retained["wait_intervals"] = sum(len(agent["wait"]) for agent in self.agents.values())
        retained["wait_samples"] = len(self.sinks.get("wait", {}).get("samples", []))
        retained["retry_samples"] = len(self.sinks.get("recorded_retry", {}).get("samples", []))
        return original_finish(self)

    monkeypatch.setattr(Collector, "rows", rows)
    monkeypatch.setattr(Collector, "finish", finish)
    limits = Limits(metadata=4, runs=10, items=10, turns=10, log_lines=10)
    snapshot = collect(path, START, END, limits=limits)
    assert max(counts.values()) <= limits.metadata
    assert max(retained.values()) <= limits.metadata
    assert snapshot["coverage"]["partial"]
    for name in ("asks", "retries", "transfers"):
        assert snapshot["coverage"]["scans"][name] == {"scanned": 4, "limit": 4, "truncated": True}


def test_pr_link_overflow_cannot_be_a_complete_merged_zero(tmp_path):
    path = source(tmp_path / "source.sqlite")
    links = [
        {
            "host": "github.example.test",
            "repository": "sample/project",
            "number": i + 1,
            "snapshot": {"state": "open" if i < 512 else "merged", "syncedAt": timing.iso(AT + 50)},
        }
        for i in range(513)
    ]
    with sqlite3.connect(path) as db:
        db.execute(
            f"UPDATE {PREFIX}threads SET payload_json=json_set(payload_json,'$.pullRequests',json(?)) WHERE thread_id='main'",
            (json.dumps(links),),
        )
        db.execute(
            f"UPDATE {PREFIX}threads SET payload_json=json_set(payload_json,'$.pullRequests',json('[]')) WHERE thread_id='child'"
        )
    snapshot = collect(path, START, END)
    assert snapshot["coverage"]["partial"]
    assert snapshot["metrics"]["prs_merged"] == {"value": None, "reason": "partial_scan"}
    assert snapshot["coverage"]["scans"]["pr_links"]["truncated"]


def test_auxiliary_budgets_ignore_old_foreign_and_ineligible_rows(tmp_path, monkeypatch):
    path = source(tmp_path / "source.sqlite")
    with sqlite3.connect(path) as db:
        for i in range(40):
            for tid, at, kind in (
                ("main", START - 100, "user_input"),
                ("foreign", AT, "user_input"),
                ("main", AT, "permission"),
            ):
                db.execute(
                    f"INSERT INTO {PREFIX}runtime_requests VALUES (?,?,?,?,?,?)",
                    (tid, kind, "resolved", timing.iso(at), timing.iso(at + 1), '{"answers":{}}'),
                )
            for label, rid, at, reason in (
                ("old", "r1", START - 100, "retry"),
                ("foreign", "foreign", AT, "retry"),
                ("initial", "r1", AT, "initial"),
            ):
                db.execute(
                    f"INSERT INTO {PREFIX}run_attempts VALUES (?,?,?,?)",
                    (
                        f"{label}-{i}",
                        rid,
                        "completed",
                        json.dumps(
                            {
                                "reason": reason,
                                "startedAt": timing.iso(at),
                                "completedAt": timing.iso(at + 1),
                            }
                        ),
                    ),
                )
            for tid, at, status in (
                ("main", START - 100, "resolved_native"),
                ("foreign", AT, "resolved_native"),
                ("main", AT, "failed"),
            ):
                db.execute(
                    f"INSERT INTO {PREFIX}context_transfers VALUES (?,?,?,?,?,?,?)",
                    (
                        tid,
                        "foreign",
                        "r3",
                        "provider_handoff",
                        timing.iso(at),
                        status,
                        '{"sourcePoint":{"runId":"r1"}}',
                    ),
                )
        db.execute(
            f"INSERT INTO {PREFIX}runtime_requests VALUES (?,?,?,?,?,?)",
            (
                "main",
                "user_input",
                "resolved",
                timing.iso(AT + 5),
                timing.iso(AT + 6),
                '{"answers":{}}',
            ),
        )
        db.execute(
            f"INSERT INTO {PREFIX}run_attempts VALUES (?,?,?,?)",
            (
                "current",
                "r1",
                "completed",
                json.dumps(
                    {
                        "reason": "retry",
                        "startedAt": timing.iso(AT + 5),
                        "completedAt": timing.iso(AT + 6),
                    }
                ),
            ),
        )
        db.execute(
            f"INSERT INTO {PREFIX}context_transfers VALUES (?,?,?,?,?,?,?)",
            (
                "main",
                "child",
                "r3",
                "provider_handoff",
                timing.iso(AT + 10),
                "resolved_native",
                '{"sourcePoint":{"runId":"r1"}}',
            ),
        )
    counts: collections.Counter[str] = collections.Counter()
    original = Collector.rows

    def rows(self, sql, args=()):
        table = next(
            (
                name
                for name in ("runtime_requests", "run_attempts", "context_transfers")
                if PREFIX + name in sql and "provider_turns" not in sql
            ),
            None,
        )
        for row in original(self, sql, args):
            if table:
                counts[table] += 1
            yield row

    monkeypatch.setattr(Collector, "rows", rows)
    snapshot = collect(
        path, START, END, limits=Limits(metadata=4, runs=10, items=10, turns=10, log_lines=10)
    )
    assert counts == {"runtime_requests": 3, "run_attempts": 1, "context_transfers": 1}
    assert snapshot["metrics"]["asks"]["value"] == 3
    assert snapshot["metrics"]["retries"]["value"] == 1
    assert snapshot["metrics"]["handoffs"]["value"] == 1
    assert not snapshot["coverage"]["partial"]


def test_metadata_budget_follows_window_and_still_counts_future_starts_requested_now(tmp_path):
    path = source(tmp_path / "source.sqlite")
    with sqlite3.connect(path) as db:
        for i in range(50):
            db.execute(
                f"INSERT INTO {PREFIX}runs VALUES (?,?,?,?,?,?,?)",
                (
                    f"old-{i}",
                    "main",
                    i + 10,
                    "completed",
                    timing.iso(START - 300),
                    timing.iso(START - 200),
                    json.dumps({"startedAt": timing.iso(START - 250)}),
                ),
            )
        db.execute(
            f"INSERT INTO {PREFIX}runs VALUES (?,?,?,?,?,?,?)",
            (
                "later",
                "main",
                100,
                "completed",
                timing.iso(AT),
                timing.iso(END + 120),
                json.dumps({"startedAt": timing.iso(END + 100)}),
            ),
        )
    snapshot = collect(
        path, START, END, limits=Limits(metadata=5, runs=10, items=10, turns=10, log_lines=10)
    )
    assert snapshot["metrics"]["runs_requested"]["value"] == 5
    assert snapshot["metrics"]["runs_started"]["value"] == 3
    assert snapshot["coverage"]["runs_seen"] == 4
    assert snapshot["coverage"]["scans"]["runs"]["scanned"] == 5
    assert not snapshot["coverage"]["partial"]


def test_missing_linked_pr_state_is_not_a_known_zero(tmp_path):
    path = source(tmp_path / "source.sqlite")
    with sqlite3.connect(path) as db:
        db.execute(
            f"UPDATE {PREFIX}threads SET payload_json=json_insert(payload_json,'$.pullRequests[#]',json(?)) WHERE thread_id='main'",
            (
                json.dumps(
                    {"host": "github.example.test", "repository": "sample/project", "number": 18}
                ),
            ),
        )
    snapshot = collect(path, START, END)
    assert snapshot["coverage"]["pr_states"] == 1
    assert snapshot["coverage"]["pr_unique"] == 2
    for name in ("prs_open", "prs_closed", "prs_merged"):
        assert snapshot["metrics"][name] == {"value": None, "reason": "pr_snapshot_missing"}


@pytest.mark.parametrize("started", [None, "invalid", False])
def test_untimed_retained_edit_makes_window_line_totals_unavailable(tmp_path, started):
    path = source(tmp_path / "source.sqlite")
    with sqlite3.connect(path) as db:
        if started is None:
            db.execute(
                "UPDATE orchestration_v2_projection_turn_items "
                "SET payload_json=json_remove(payload_json,'$.startedAt') WHERE type='file_change'"
            )
        else:
            db.execute(
                "UPDATE orchestration_v2_projection_turn_items "
                "SET payload_json=json_set(payload_json,'$.startedAt',?) WHERE type='file_change'",
                (started,),
            )
    snapshot = collect(path, START, END)
    for key in ("lines_added", "lines_deleted"):
        assert snapshot["metrics"][key] == {"value": None, "reason": "edit_timing_missing"}
    assert not snapshot["coverage"]["scans"]["items"]["truncated"]
    assert wire.loads(json.dumps(snapshot).encode()) == snapshot


def test_model_attribution_preserves_overlaps_ties_phase_gaps_and_fallback():
    runs = [
        {"interval": (AT + 5, AT + 30), "ordinal": 3, "model": "first"},
        {"interval": (AT + 10, AT + 20), "ordinal": 3, "model": "tied"},
        {"interval": (AT + 15, AT + 25), "ordinal": 4, "model": "latest"},
        {"interval": (AT, AT + 35), "ordinal": 1, "model": "older"},
        {"interval": None, "ordinal": 99, "model": "untimed"},
    ]
    segments = [(AT, AT + 12, "tool"), (AT + 15, AT + 45, "wait")]
    assert list(timing.model_parts(segments, runs, "fallback")) == [
        (AT, AT + 5, "tool", "older"),
        (AT + 5, AT + 10, "tool", "first"),
        (AT + 10, AT + 12, "tool", "first"),
        (AT + 15, AT + 20, "wait", "latest"),
        (AT + 20, AT + 25, "wait", "latest"),
        (AT + 25, AT + 30, "wait", "first"),
        (AT + 30, AT + 35, "wait", "older"),
        (AT + 35, AT + 45, "wait", "fallback"),
    ]


def test_model_attribution_processes_all_50000_adjacent_runs_with_bounded_visits():
    count, visits, budget = 50000, 0, 8 * 50000

    class ObservedRun(dict):
        def __getitem__(self, key):
            nonlocal visits
            visits += 1
            assert visits <= budget, "Model attribution exceeded the retained-run visit budget"
            return super().__getitem__(key)

    runs = [
        ObservedRun(interval=(AT + n, AT + n + 1), ordinal=n, model=f"model-{n % 2}")
        for n in range(count)
    ]
    parts = list(timing.model_parts([(AT - 1, AT + count + 1, "unknown")], runs, "fallback"))
    assert len(parts) == count + 2
    assert parts[0] == (AT - 1, AT, "unknown", "fallback")
    assert parts[-1] == (AT + count, AT + count + 1, "unknown", "fallback")
    assert parts[1:-1] == [(AT + n, AT + n + 1, "unknown", f"model-{n % 2}") for n in range(count)]
    assert visits <= budget
