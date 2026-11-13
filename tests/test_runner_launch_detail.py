"""Failed launch diagnostics travel with the job without exposing credentials."""

import json
import os

import pytest

from tests._commonplace_account_helpers import site_fixture as site_fixture
from tests.runner_harness import (
    _signup,
    cli_token,
    http,
    member,
    owners as owners,
    signup_hub as signup_hub,
)
from tests.test_runner_agent import _invoke, _queue, _setup, _state


@pytest.mark.parametrize("mode", ["is-error", "leaky", "diagnostic-flood"])
def test_launch_unknown_has_a_readable_redacted_cause(signup_hub, owners, tmp_path, caplog, mode):
    hub, owner = signup_hub, owners["A"]
    machine, path, env = _setup(hub, owner, tmp_path, mode=mode)
    number, job_id = _queue(hub, owner, machine)
    result = _invoke(path, env)
    assert result.returncode == 0
    row = _state(hub, owner, number, job_id)
    assert row["state"] == "uncertain" and row["outcome_code"] == "launch_unknown"
    detail = row.get("reason", {}).get("detail", "")
    expected = "refused:" if mode == "leaky" else "runtime_mode_escalation_denied"
    assert expected in detail, "launch_unknown must carry the bridge's readable cause"
    assert row["reason"]["code"] == "launch_unknown"
    assert len(detail) <= 4000
    assert "stdout:" in detail and "stderr:" in detail and "exit " in detail
    if mode == "diagnostic-flood":
        assert "stdout tail" in detail and "stderr tail" in detail
        assert "progress first" not in detail
    if mode == "is-error":
        assert '"code": "runtime_mode_escalation_denied"' in detail
    log = tmp_path / "journal" / f"{job_id}.launch.log"
    assert log.read_text() == detail
    assert os.stat(log).st_mode & 0o777 == 0o600
    from commonplace.runner import jobs

    store = jobs.open_store(hub.gateway, owner["workspace"])
    with store.connection() as conn:
        stored = conn.execute(
            "SELECT launch_output FROM runner_jobs WHERE id=?", (job_id,)
        ).fetchone()[0]
    assert stored == detail
    with hub.gateway.db._lock:
        audit = repr([tuple(r) for r in hub.gateway.db._conn.execute("SELECT * FROM audit_log")])
    surfaces = detail + stored + result.stdout + result.stderr + caplog.text + audit
    for secret in (env["RUNNER_T3_GRANT"], "sk-" + "S" * 24, "x" * 40):
        assert secret not in surfaces


def test_launch_detail_is_read_only_and_scoped_to_board_people(signup_hub, owners, tmp_path):
    hub, owner = signup_hub, owners["A"]
    machine, path, env = _setup(hub, owner, tmp_path, mode="is-error")
    number, job_id = _queue(hub, owner, machine)
    assert _invoke(path, env).returncode == 0
    endpoint = f"/api/card-starts/{job_id}/launch-output"
    reader = member(hub, owner, _signup(hub, "reader@example.test", 2))
    for person in (owner, reader):
        status, body = http(hub.port, "GET", endpoint, cookie=person["cookie"])
        assert status == 200, body
        assert body["reason"]["code"] == "launch_unknown"
        assert "runtime_mode_escalation_denied" in body["reason"]["detail"]
    _, token = cli_token(owner, "reader-agent", profile="standard")
    for auth in (
        {"token": token},
        {"token": machine["worker_token"]},
        {"token": machine["machine_token"]},
        {"cookie": owners["B"]["cookie"]},
    ):
        status, body = http(hub.port, "GET", endpoint, **auth)
        assert status == 404, body
        assert "runtime_mode_escalation_denied" not in json.dumps(body)
    assert http(hub.port, "POST", endpoint, cookie=owner["cookie"], body={})[0] == 404
    from commonplace.runner import jobs

    store = jobs.open_store(hub.gateway, owner["workspace"])
    with store.transaction() as conn:
        conn.execute("DELETE FROM runner_jobs WHERE id=?", (job_id,))
    assert http(hub.port, "GET", endpoint, cookie=owner["cookie"])[0] == 404


def test_success_does_not_store_job_output(signup_hub, owners, tmp_path):
    hub, owner = signup_hub, owners["A"]
    machine, path, env = _setup(hub, owner, tmp_path)
    number, job_id = _queue(hub, owner, machine)
    assert _invoke(path, env).returncode == 0
    row = _state(hub, owner, number, job_id)
    assert row["state"] == "started" and "reason" not in row
    from commonplace.runner import jobs

    store = jobs.open_store(hub.gateway, owner["workspace"])
    with store.connection() as conn:
        assert (
            conn.execute("SELECT launch_output FROM runner_jobs WHERE id=?", (job_id,)).fetchone()[
                0
            ]
            is None
        )
    endpoint = f"/api/card-starts/{job_id}/launch-output"
    assert http(hub.port, "GET", endpoint, cookie=owner["cookie"])[0] == 404


def test_failed_result_delivery_replays_only_redacted_diagnostics(signup_hub, owners, tmp_path):
    from tests.test_runner_agent import _result_failure

    hub, owner = signup_hub, owners["A"]
    machine, path, env = _setup(hub, owner, tmp_path, mode="diagnostic-flood")
    number, job_id = _queue(hub, owner, machine)
    original = hub.gateway.find_route("POST", "/api/runner/result")
    _result_failure(hub)
    assert _invoke(path, env).returncode == 5
    journal = tmp_path / "journal" / f"{job_id}.json"
    saved = json.loads(journal.read_text())
    assert "sk-" + "S" * 24 not in journal.read_text()
    hub.gateway.add_route("POST", "/api/runner/result", original)
    assert _invoke(path, env).returncode == 0
    assert not journal.exists()
    assert _state(hub, owner, number, job_id)["reason"]["detail"] == saved["launch_output"]


def test_machine_result_validates_and_redacts_output(signup_hub, owners, tmp_path):
    from tests.test_runner_jobs import _lease, _machine_call, _starting

    hub, owner = signup_hub, owners["A"]
    requester, token = cli_token(owner, "requester", profile="standard")
    machine, _, _ = _setup(hub, owner, tmp_path, requesters=[requester])
    from tests.test_runner_jobs import _start

    number, queued = _start(hub, owner, machine, token=token)
    leased = _lease(hub, machine)
    _starting(hub, machine, leased)
    body = {"lease_token": leased["lease_token"], "outcome": "uncertain", "code": "launch_unknown"}
    for invalid in ("x" * 4001, "", 12):
        assert _machine_call(hub, machine, "result", {**body, "launch_output": invalid})[0] == 400
    assert (
        _machine_call(
            hub, machine, "result", {**body, "code": "restart_unknown", "launch_output": "error"}
        )[0]
        == 400
    )
    sentinel = "sk-" + "S" * 24
    status, response = _machine_call(
        hub, machine, "result", {**body, "launch_output": "refused: " + sentinel}
    )
    assert status == 200 and sentinel not in json.dumps(response)
    owner_row = _state(hub, owner, number, queued["job_id"])
    assert "refused:" in owner_row["reason"]["detail"]
    assert sentinel not in json.dumps(owner_row)
    status, rows = http(hub.port, "GET", f"/api/card-starts?task={number}", token=token)
    assert status == 200 and len(rows) == 1
    assert "reason" not in rows[0] and "launch_output" not in rows[0]


def test_diagnostics_follow_workspace_export_and_deletion(site, monkeypatch):
    from commonplace.account import deletion
    from commonplace.runner import jobs
    from tests.test_commonplace_account_deletion import DAY, ask

    store = jobs.open_store(site.gateway, site.alice_home)
    detail = "exit 1\nstdout:\nrefused\nstderr:\nlaunch unavailable"
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO runner_jobs (id,task_number,board_slug,machine_id,project,topic,"
            "grant_version,runtime_mode,requested_by,state,outcome_code,launch_output,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "job_0123456789abcdef",
                1,
                "work",
                "machine",
                "demo",
                "worker",
                1,
                "approval-required",
                "user:" + site.alice,
                "uncertain",
                "launch_unknown",
                detail,
                jobs._now(),
                jobs._now(),
            ),
        )
    archive = site.export(site.alice, site.alice_home)
    name = f"workspaces/{site.alice_home}/stores/runner.sqlite3/runner_jobs.ndjson"
    assert archive.rows(name)[0]["launch_output"] == detail
    assert detail not in site.export(site.bob, site.bob_home).text()
    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION", "on")
    assert ask(site, site.alice, site.alice_home, "alice@example.test")[0] == 200
    site.advance(deletion.DEFAULT_GRACE_DAYS * DAY + 1)
    assert deletion.purge_due(site.gateway)[0]["state"] == "purged"
    from pathlib import Path

    assert not Path(store.path).exists()
