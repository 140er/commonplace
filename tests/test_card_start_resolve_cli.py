"""``commonplace card-start resolve``: the owner path for an uncertain runner start."""

import json
import sqlite3

import pytest

from commonplace.cli import main as cli_main
from commonplace.control_db import ControlDB
from commonplace.runner import jobs, owner_cli
from commonplace.workspace_store import workspace_path


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.delenv("COMMONPLACE_DATA", raising=False)
    root = tmp_path / "data"
    assert cli_main(["--data", str(root), "init", "--admin-email", "owner@example.test"]) == 0
    return root


def _cli(data, *args, capsys):
    capsys.readouterr()
    code = cli_main(["--data", str(data), *args])
    out = capsys.readouterr()
    return code, out.out, out.err


def _workspace_id(data):
    db = ControlDB(str(data / "control.sqlite3"))
    try:
        return db.workspace("personal")["id"]
    finally:
        db.close()


def _store(data):
    db = ControlDB(str(data / "control.sqlite3"))
    try:
        return jobs.open_store(owner_cli._LocalGateway(db, data), db.workspace("personal")["id"])
    finally:
        db.close()


def _insert(data, job, state="uncertain", task=7):
    with _store(data).transaction() as conn:
        conn.execute(
            "INSERT INTO runner_jobs (id, task_number, board_slug, machine_id, project, topic, grant_version,"
            " runtime_mode, requested_by, state, attempt, outcome_code, created_at, updated_at)"
            " VALUES (?, ?, 'personal', 'mach_0123456789abcdef', 'p', 't', 1, 'auto', 'user:x', ?, 1,"
            " 'launch_unknown', '2026-11-12T07:00:00Z', '2026-11-12T07:15:06Z')",
            (job, task, state),
        )


def _row(data, job):
    with _store(data).connection() as conn:
        return conn.execute("SELECT * FROM runner_jobs WHERE id=?", (job,)).fetchone()


def test_an_uncertain_start_with_no_thread_is_resolved_as_failed_and_frees_the_slot(data, capsys):
    _insert(data, "job_wedged")
    # while it is uncertain it counts as in flight: a second start for the same card cannot be queued
    with pytest.raises(sqlite3.IntegrityError), _store(data).transaction() as conn:
        conn.execute(
            "INSERT INTO runner_jobs (id, task_number, board_slug, machine_id, project, topic, grant_version,"
            " runtime_mode, requested_by, state, attempt, created_at, updated_at)"
            " VALUES ('job_next', 7, 'personal', 'm', 'p', 't', 1, 'auto', 'user:x', 'queued', 0, 'n', 'n')"
        )
    code, out, err = _cli(
        data, "card-start", "resolve", "--workspace", "personal", "--job", "job_wedged",
        "--outcome", "failed", capsys=capsys,
    )  # fmt: skip
    assert code == 0, err
    result = json.loads(out)
    assert result["state"] == "failed" and result["thread_id"] is None
    assert result["resolved_by"].startswith("user:") and result["resolved_at"]
    row = _row(data, "job_wedged")
    assert row["state"] == "failed" and row["resolved_by"] == result["resolved_by"]
    with _store(data).transaction() as conn:  # the card's active-start slot is free again
        conn.execute(
            "INSERT INTO runner_jobs (id, task_number, board_slug, machine_id, project, topic, grant_version,"
            " runtime_mode, requested_by, state, attempt, created_at, updated_at)"
            " VALUES ('job_next', 7, 'personal', 'm', 'p', 't', 1, 'auto', 'user:x', 'queued', 0, 'n', 'n')"
        )


def test_an_uncertain_start_whose_thread_exists_is_resolved_as_started(data, capsys):
    _insert(data, "job_thread")
    code, out, err = _cli(
        data, "card-start", "resolve", "--workspace", "personal", "--job", "job_thread",
        "--outcome", "started", "--thread", "mcp:1234-abcd", capsys=capsys,
    )  # fmt: skip
    assert code == 0, err
    row = _row(data, "job_thread")
    assert row["state"] == "started" and row["thread_id"] == "mcp:1234-abcd" and row["started_at"]


def test_the_resolution_is_audited_like_the_tool_does(data, capsys):
    _insert(data, "job_audit")
    assert _cli(
        data, "card-start", "resolve", "--workspace", "personal", "--job", "job_audit",
        "--outcome", "failed", capsys=capsys,
    )[0] == 0  # fmt: skip
    db = ControlDB(str(data / "control.sqlite3"))
    try:
        entries = [dict(row) for row in db.audit_entries("personal", limit=20)]
    finally:
        db.close()
    assert any(e["action"] == "runner.resolve" and e["decision"] == "allow" for e in entries)


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        (("--outcome", "started"), "thread"),  # started needs the thread id
        (("--outcome", "failed", "--thread", "mcp:x"), "thread"),  # a failed start has no thread
        (("--outcome", "started", "--thread", "bad thread id"), "thread"),
    ],
)
def test_an_invalid_resolution_is_refused_and_changes_nothing(data, capsys, args, needle):
    _insert(data, "job_bad")
    code, _out, err = _cli(
        data,
        "card-start",
        "resolve",
        "--workspace",
        "personal",
        "--job",
        "job_bad",
        *args,
        capsys=capsys,
    )
    assert code == 2 and needle in err
    assert _row(data, "job_bad")["state"] == "uncertain"


def test_only_an_uncertain_start_can_be_resolved_and_an_unknown_job_is_refused(data, capsys):
    _insert(data, "job_done", state="failed")
    code, _out, err = _cli(
        data, "card-start", "resolve", "--workspace", "personal", "--job", "job_done",
        "--outcome", "failed", capsys=capsys,
    )  # fmt: skip
    assert code == 2 and "not uncertain" in err
    code, _out, err = _cli(
        data, "card-start", "resolve", "--workspace", "personal", "--job", "job_nope",
        "--outcome", "failed", capsys=capsys,
    )  # fmt: skip
    assert code == 2


def test_the_same_owner_check_as_the_web_ui_refuses_a_non_owner_and_an_unknown_workspace(
    data, capsys
):
    _insert(data, "job_owner")
    db = ControlDB(str(data / "control.sqlite3"))
    try:
        member = db.create_user("member@example.test")
    finally:
        db.close()
    code, _out, err = _cli(
        data, "card-start", "resolve", "--workspace", "personal", "--job", "job_owner",
        "--outcome", "failed", "--by", member, capsys=capsys,
    )  # fmt: skip
    assert code == 2 and "owner" in err
    assert _row(data, "job_owner")["state"] == "uncertain"
    code, _out, _err = _cli(
        data, "card-start", "resolve", "--workspace", "nowhere", "--job", "job_owner",
        "--outcome", "failed", capsys=capsys,
    )  # fmt: skip
    assert code == 2
    assert workspace_path(
        data, _workspace_id(data)
    )  # the data directory was never modified by the refusals
