"""Untimed active requests remain visible without inventing execution time."""

import json
import sqlite3

import pytest
from activity_core_fixtures import END, START, source

from commonplace.compute import activity_math as timing, activity_schema as wire
from commonplace.compute.activity_collector import Limits, collect, identity

PREFIX = "orchestration_v2_projection_"
ACTIVE = ("preparing", "starting", "running", "waiting")


@pytest.fixture
def empty_source(tmp_path):
    path = source(tmp_path / "source.sqlite")
    with sqlite3.connect(path) as db:
        for table in (
            "runs",
            "threads",
            "turn_items",
            "subagents",
            "runtime_requests",
            "run_attempts",
            "provider_turns",
            "context_transfers",
        ):
            db.execute(f"DELETE FROM {PREFIX}{table}")
    return path


def add_run(path, status, requested, *, started=None, absent=False, completed=None, number=1):
    payload = {"modelSelection": {"model": "gpt-6.1-sol"}}
    if not absent:
        payload["startedAt"] = started
    with sqlite3.connect(path) as db:
        db.execute(
            f"INSERT INTO {PREFIX}threads VALUES (?,?,?,?)",
            (f"thread-{number}", "project", timing.iso(END - 1), '{"createdBy":"agent"}'),
        )
        db.execute(
            f"INSERT INTO {PREFIX}runs VALUES (?,?,?,?,?,?,?)",
            (
                f"run-{number}",
                f"thread-{number}",
                1,
                status,
                requested,
                completed,
                json.dumps(payload),
            ),
        )


@pytest.mark.parametrize("status", ACTIVE)
@pytest.mark.parametrize("requested", [START - 3600, START - 1, START, END - 1])
@pytest.mark.parametrize("absent", [False, True], ids=["null-start", "absent-start"])
def test_untimed_active_request_survives_sql_and_retention(empty_source, status, requested, absent):
    add_run(empty_source, status, timing.iso(requested), absent=absent)
    snapshot = collect(empty_source, START, END)
    coverage, metrics = snapshot["coverage"], snapshot["metrics"]
    assert coverage["scans"]["runs"] == {"scanned": 1, "limit": 50000, "truncated": False}
    assert coverage["runs_seen"] == coverage["runs_untimed"] == coverage["active_threads"] == 1
    assert coverage["runs_timed"] == 0
    assert metrics["runs_requested"]["value"] == int(START <= requested < END)
    assert metrics["runs_started"]["value"] == 0
    assert metrics["threads"]["value"] == 1
    assert metrics["agent_hours"] == {"value": None, "reason": "no_timing"}
    thread = snapshot["threads"][0]
    assert thread["id"] == identity("thread", "thread-1")
    assert thread["status"] == status and thread["runs"] == 1
    assert thread["first"] == thread["last"] == max(START, requested)
    assert thread["seconds"] == 0 and thread["buckets"] == []
    assert all(day["runs"] == day["seconds"] == 0 for day in snapshot["daily"])
    assert wire.loads(json.dumps(snapshot).encode()) == snapshot


@pytest.mark.parametrize("status", ACTIVE)
@pytest.mark.parametrize("started", [START - 60, START + 60])
def test_valid_timed_active_run_keeps_clipped_execution(empty_source, status, started):
    add_run(empty_source, status, timing.iso(START - 120), started=timing.iso(started))
    snapshot = collect(empty_source, START, END)
    seconds = END - max(START, started)
    assert snapshot["coverage"]["runs_timed"] == 1
    assert snapshot["coverage"]["runs_untimed"] == 0
    assert snapshot["coverage"]["active_threads"] == 1
    assert snapshot["metrics"]["agent_hours"]["value"] * 3600 == pytest.approx(seconds)
    assert snapshot["metrics"]["runs_started"]["value"] == int(started >= START)


@pytest.mark.parametrize(
    "status,requested,retained,untimed,active",
    [
        ("queued", START - 60, 1, 0, 1),
        ("completed", START - 60, 0, 0, 0),
        ("completed", START, 1, 1, 0),
        ("running", END, 0, 0, 0),
        ("running", END + 60, 0, 0, 0),
        ("queued", END, 0, 0, 0),
        ("running", None, 0, 0, 0),
        ("running", "invalid", 0, 0, 0),
        ("running", False, 0, 0, 0),
    ],
)
def test_queued_completed_future_and_malformed_requests_keep_eligibility(
    empty_source, status, requested, retained, untimed, active
):
    request = timing.iso(requested) if type(requested) is int else requested
    add_run(empty_source, status, request)
    snapshot = collect(empty_source, START, END)
    assert snapshot["coverage"]["runs_seen"] == retained
    assert snapshot["coverage"]["runs_untimed"] == untimed
    assert snapshot["coverage"]["active_threads"] == active
    assert snapshot["metrics"]["runs_started"]["value"] == 0
    assert snapshot["metrics"]["agent_hours"] == (
        {"value": None, "reason": "no_timing"} if untimed else {"value": 0, "reason": None}
    )


@pytest.mark.parametrize("started", ["invalid", False, {}, []])
def test_malformed_start_with_in_window_request_stays_untimed(empty_source, started):
    add_run(empty_source, "running", timing.iso(START), started=started)
    snapshot = collect(empty_source, START, END)
    assert snapshot["coverage"]["runs_untimed"] == 1
    assert snapshot["coverage"]["runs_timed"] == 0
    assert snapshot["metrics"]["agent_hours"] == {"value": None, "reason": "no_timing"}


def test_active_request_is_filtered_before_unchanged_metadata_budget(empty_source):
    for number in range(1, 21):
        add_run(empty_source, "completed", timing.iso(START - 60), number=number)
    add_run(empty_source, "waiting", timing.iso(START - 120), number=21)
    add_run(empty_source, "starting", timing.iso(START - 240), number=22)
    snapshot = collect(empty_source, START, END, limits=Limits(metadata=1))
    assert snapshot["coverage"]["scans"]["runs"] == {
        "scanned": 1,
        "limit": 1,
        "truncated": True,
    }
    assert snapshot["coverage"]["partial"]
    assert snapshot["coverage"]["runs_seen"] == snapshot["coverage"]["active_threads"] == 1
    assert snapshot["coverage"]["runs_untimed"] == 1
    assert snapshot["metrics"]["agent_hours"] == {"value": None, "reason": "no_timing"}
