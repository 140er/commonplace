"""Window availability and bounded model attribution on retained synthetic data."""

import json
import sqlite3

import pytest
from activity_fixtures import AT, END, START, source

from commonplace.compute import activity_math as timing, activity_schema as wire
from commonplace.compute.activity_collector import collect


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
