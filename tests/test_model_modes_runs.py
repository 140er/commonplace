"""Model modes on card starts (card #617, slice 1): the account a start records, the refusals that
name the next step, and a store from before modes that keeps every run as it was."""

import pytest

from commonplace.runner import jobs
from mempalace.sqlite_crypto import connect
from tests.runner_harness import (
    attest,
    card,
    enroll,
    http,
    owners as owners,
    signup_hub as signup_hub,
)

CONTRACT = {
    "job_id",
    "lease_token",
    "lease_until",
    "task_number",
    "board_slug",
    "project",
    "topic",
    "runtime_mode",
    "title_marker",
}
SETTING = "/api/settings/model-mode"
CONNECTIONS = "/api/me/model-connections"


def _machine(hub, owner, label):
    response = http(
        hub.port,
        "POST",
        "/api/memory",
        cookie=owner["cookie"],
        body={"text": "Synthetic workspace example.", "theme": "shared", "topic": "demo"},
    )
    assert response[0] == 201, response
    machine = enroll(hub, owner, label=label)
    attest(hub, owner, machine)
    return machine


def _start(hub, owner, machine):
    task = card(hub, owner)
    return http(
        hub.port,
        "POST",
        "/api/card-starts",
        cookie=owner["cookie"],
        body={
            "task": task["number"],
            "machine": machine["machine_id"],
            "project": "demo",
            "topic": "worker",
        },
    ), task


def _account(hub, owner, task):
    status, body = http(
        hub.port, "GET", f"/api/card-starts?task={task['number']}", cookie=owner["cookie"]
    )
    assert status == 200, (status, body)
    [job] = body
    return job["account"]


def _set_mode(hub, owner, **body):
    status, answer = http(hub.port, "PUT", SETTING, cookie=owner["cookie"], body=body)
    assert status == 200, (status, answer)


def _connect(hub, owner, label):
    status, answer = http(
        hub.port,
        "POST",
        CONNECTIONS,
        cookie=owner["cookie"],
        body={"provider": "claude", "label": "my subscription", "machine": label},
    )
    assert status == 201, (status, answer)
    return answer


def test_with_no_record_a_start_is_as_before_and_is_recorded_as_the_legacy_setup(
    signup_hub, owners
):
    hub, owner = signup_hub, owners["A"]
    machine = _machine(hub, owner, "org-1")
    (status, body), task = _start(hub, owner, machine)
    assert status == 201 and set(body) == CONTRACT  # the start reply is unchanged
    assert _account(hub, owner, task) == {
        "mode": "legacy",
        "mode_source": "legacy",
        "kind": "org",
        "label": None,
    }


def test_org_mode_runs_on_the_workspace_machine_and_says_so(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = _machine(hub, owner, "org-1")
    _set_mode(hub, owner, mode="org")
    (status, _), task = _start(hub, owner, machine)
    assert status == 201
    account = _account(hub, owner, task)
    assert (account["mode"], account["mode_source"], account["kind"], account["label"]) == (
        "org",
        "workspace",
        "org",
        "org-1",
    )


def test_personal_mode_without_a_connection_refuses_in_words_and_asks_once(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = _machine(hub, owner, "org-1")
    _set_mode(hub, owner, mode="personal")
    (status, body), task = _start(hub, owner, machine)
    assert (status, body["code"]) == (409, "connection_missing")
    assert "Connect it in Settings" in body["error"]
    assert (
        http(hub.port, "GET", f"/api/card-starts?task={task['number']}", cookie=owner["cookie"])[1]
        == []
    )
    needs = http(hub.port, "GET", "/api/needs-me", cookie=owner["cookie"])[1]
    assert [i["kind"] for i in needs["model_connections"]] == ["model_connection"]
    (_, _), _ = _start(hub, owner, machine)
    assert (
        len(http(hub.port, "GET", "/api/needs-me", cookie=owner["cookie"])[1]["model_connections"])
        == 1
    )
    # The workspace's other owner (here: the other workspace) never sees this person's item.
    other = owners["B"]
    assert (
        "model_connections" not in http(hub.port, "GET", "/api/needs-me", cookie=other["cookie"])[1]
    )


def test_personal_mode_runs_only_on_the_persons_own_machine(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    org = _machine(hub, owner, "org-1")
    own = _machine(hub, owner, "own-1")
    _set_mode(hub, owner, mode="personal")
    _connect(hub, owner, "own-1")
    (status, body), _ = _start(hub, owner, org)
    assert (status, body["code"]) == (409, "machine_not_your_account")
    (status, _), task = _start(hub, owner, own)
    assert status == 201
    account = _account(hub, owner, task)
    assert (account["mode"], account["kind"], account["label"]) == (
        "personal",
        "person",
        "my subscription",
    )
    # The need cleared itself the moment the connection existed.
    assert (
        "model_connections" not in http(hub.port, "GET", "/api/needs-me", cookie=owner["cookie"])[1]
    )


def test_org_mode_refuses_a_persons_own_machine(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    own = _machine(hub, owner, "own-1")
    _connect(hub, owner, "own-1")
    _set_mode(hub, owner, mode="org")
    (status, body), _ = _start(hub, owner, own)
    assert (status, body["code"]) == (409, "machine_not_org_account")


def test_the_recorded_account_never_carries_a_credential(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    own = _machine(hub, owner, "own-1")
    _connect(hub, owner, "own-1")
    _set_mode(hub, owner, mode="personal")
    (status, body), task = _start(hub, owner, own)
    assert status == 201
    conn = connect(str(hub.gateway.workspace_dir(owner["workspace"])) + "/runner.sqlite3")
    try:
        rows = conn.execute("SELECT * FROM run_ledger").fetchall()
        names = [d[0] for d in conn.execute("SELECT * FROM run_ledger").description]
    finally:
        conn.close()
    text = repr([dict(zip(names, row, strict=True)) for row in rows])
    assert "cmmn_" not in text


def test_a_store_from_before_modes_keeps_every_run_and_gains_empty_columns(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = _machine(hub, owner, "org-1")
    (status, _), task = _start(hub, owner, machine)
    assert status == 201
    path = str(hub.gateway.workspace_dir(owner["workspace"])) + "/runner.sqlite3"
    conn = connect(path, timeout=10, isolation_level=None)
    try:  # make the file look like one written before this change
        for table in ("runner_jobs", "run_ledger"):
            for name in ("mode", "mode_source", "account_kind", "account_label", "account_person"):
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {name}")
        before = [tuple(r) for r in conn.execute("SELECT job_id, status, model FROM run_ledger")]
    finally:
        conn.close()
    jobs.open_store(hub.gateway, owner["workspace"])
    conn = connect(path, timeout=10, isolation_level=None)
    try:
        for table in ("runner_jobs", "run_ledger"):
            columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            assert {
                "mode",
                "mode_source",
                "account_kind",
                "account_label",
                "account_person",
            } <= columns
        after = [tuple(r) for r in conn.execute("SELECT job_id, status, model FROM run_ledger")]
        assert after == before
        [row] = conn.execute("SELECT mode, account_kind FROM run_ledger").fetchall()
        assert tuple(row) == (None, None)
    finally:
        conn.close()
    # A run from before is shown without a mode, never as an invented one.
    status, body = http(
        hub.port, "GET", f"/api/card-starts?task={task['number']}", cookie=owner["cookie"]
    )
    assert status == 200 and body[0]["account"] is None


@pytest.mark.parametrize("mode", ["org", "personal", "hybrid"])
def test_the_setting_is_per_workspace(signup_hub, owners, mode):
    hub = signup_hub
    _set_mode(hub, owners["A"], mode=mode)
    status, body = http(hub.port, "GET", SETTING, cookie=owners["B"]["cookie"])
    assert (status, body) == (200, {"workspace": None, "projects": {}})
