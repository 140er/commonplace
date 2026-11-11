"""Owner-granted extra write wings for a runner worker (start, writes, authority, defaults)."""

import json

import pytest

from tests.runner_harness import (
    attest,
    card,
    cli_token,
    http,
    member,
    owners as owners,
    signup_hub as signup_hub,
)
from tests.test_runner_start import _accepted, _enroll, _machine_row, _ok, _start, _tool

WINGS = ["platform", "compute", "notes"]


def _patch(hub, machine, body, **auth):
    return http(hub.port, "PATCH", "/api/runners/" + machine["machine_id"], body=body, **auth)


def _granted(hub, owner, write_wings=("compute",)):
    """A machine that reads three wings and writes the first plus ``write_wings``.

    Starts below come from the signed-in owner, who can see every board."""
    machine = _enroll(hub, owner, wings=WINGS, max_in_flight=4)
    status, body = _patch(hub, machine, {"write_wings": list(write_wings)}, cookie=owner["cookie"])
    assert status == 200, (status, body)
    assert body["write_set"] == ["platform", *write_wings], body
    attest(hub, owner, machine)
    return machine


def _policy(hub, owner, machine):
    principal = hub.db.authenticate_connection(
        _machine_row(hub, owner, machine)["worker_connection_id"]
    )
    return principal.policy


def test_worker_starts_and_writes_cards_in_every_granted_write_wing(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = _granted(hub, owner)
    for board in ("compute", "platform"):
        task = card(hub, owner, board=board)
        _accepted(_start(hub, machine, task, owner=owner))
        worker = machine["worker_token"]
        _ok(_tool(hub, worker, "task_take_next", task=task["number"]))
        _ok(_tool(hub, worker, "task_comment", task=task["number"], text="Synthetic progress."))
    policy = _policy(hub, owner, machine)
    assert policy.write_wing == "platform" and policy.write_wings == ("compute",)
    assert policy.can_write_wing("compute") and not policy.can_write_wing("notes")


def test_wing_without_write_grant_is_still_outside_the_worker_wing(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = _granted(hub, owner)
    code, body = _start(hub, machine, card(hub, owner, board="notes"), owner=owner)
    assert code == 403 and body["error"].endswith("outside_worker_wing"), (code, body)
    assert not _policy(hub, owner, machine).can_write_wing("notes")
    # A card already reserved for the worker passes the board-write rule (own card), so
    # only the start check's write set keeps a read-only wing out.
    from commonplace.boards.store import BoardStore

    worker = hub.db.authenticate_connection(
        _machine_row(hub, owner, machine)["worker_connection_id"]
    )
    task = card(hub, owner, board="notes")
    with BoardStore(hub.gateway.workspace_dir(owner["workspace"])).transaction() as conn:
        conn.execute("UPDATE tasks SET assignee=? WHERE id=?", (worker.actor, task["number"]))
    code, body = _start(hub, machine, task, owner=owner)
    assert code == 403 and body["error"].endswith("outside_worker_wing"), (code, body)


@pytest.mark.parametrize("caller", ["member", "trusted", "worker", "foreign-owner"])
def test_only_the_workspace_owner_changes_the_write_set(signup_hub, owners, caller):
    hub, owner = signup_hub, owners["A"]
    machine = _granted(hub, owner)
    if caller == "member":
        auth = {"cookie": member(hub, owner, owners["B"])["cookie"]}
    elif caller == "foreign-owner":
        auth = {"cookie": owners["B"]["cookie"]}
    elif caller == "worker":
        auth = {"token": machine["worker_token"]}
    else:
        _, token = cli_token(owner, "admin-agent", profile="standard", trust="trusted")
        auth = {"token": token}
    for body in ({"write_wings": []}, {"write_wings": ["compute", "notes"]}):
        status, result = _patch(hub, machine, body, **auth)
        assert status == (404 if caller == "foreign-owner" else 403), (status, result)
    row = _machine_row(hub, owner, machine)
    assert row["write_set"] == ["platform", "compute"] and row["binding_state"] == (
        "owner_attested"
    )
    assert _policy(hub, owner, machine).write_wings == ("compute",)


@pytest.mark.parametrize(
    "wing", ["private.notes", "private", "compute*", "missing", "Private-Notes"]
)
def test_private_pattern_and_unknown_wings_cannot_be_added(signup_hub, owners, wing):
    hub, owner = signup_hub, owners["A"]
    machine = _granted(hub, owner)
    status, body = _patch(hub, machine, {"write_wings": ["compute", wing]}, cookie=owner["cookie"])
    assert (status, body) == (
        400,
        {"error": "choose existing nonprivate workspace wings without patterns"},
    )
    status, body = _patch(
        hub, machine, {"wings": [*WINGS, wing], "write_wings": [wing]}, cookie=owner["cookie"]
    )
    assert status == 400, (status, body)
    assert _machine_row(hub, owner, machine)["write_set"] == ["platform", "compute"]
    assert _policy(hub, owner, machine).write_wings == ("compute",)


def test_write_wings_must_be_granted_and_narrowing_wings_drops_them(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    _enroll(hub, owner, label="spare", wings=["shared", "extra"])
    machine = _granted(hub, owner)
    status, body = _patch(hub, machine, {"write_wings": ["extra"]}, cookie=owner["cookie"])
    assert (status, body) == (400, {"error": "write_wings must be granted wings"})
    status, body = _patch(hub, machine, {"wings": ["platform", "notes"]}, cookie=owner["cookie"])
    assert status == 200 and body["write_set"] == ["platform"], (status, body)
    assert body["write_wings"] == [] and body["binding_state"] == "unbound"
    assert _policy(hub, owner, machine).write_wings == ()


def test_write_set_change_is_audited_and_resets_the_binding(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = _granted(hub, owner)
    with hub.db._lock:
        rows = hub.db._conn.execute(
            "SELECT action, reason, target_ids FROM audit_log WHERE workspace_id=? "
            "AND action='runner.patch' ORDER BY seq",
            (owner["workspace"],),
        ).fetchall()
    assert [(r[0], machine["machine_id"] in r[2]) for r in rows] == [("runner.patch", True)]
    status, body = _patch(hub, machine, {"write_wings": []}, cookie=owner["cookie"])
    assert status == 200 and body["binding_state"] == "unbound", (status, body)
    with hub.db._lock:
        reasons = [
            r[0]
            for r in hub.db._conn.execute(
                "SELECT reason FROM audit_log WHERE workspace_id=? AND action='runner.patch' "
                "ORDER BY seq",
                (owner["workspace"],),
            )
        ]
    assert reasons == ["binding_reset", "binding_reset"]
    status, body = _patch(hub, machine, {"write_wings": []}, cookie=owner["cookie"])
    assert status == 200 and body["binding_state"] == "unbound", (status, body)


def test_existing_grants_keep_the_first_wing_as_the_only_write_wing(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = _enroll(hub, owner, wings=WINGS, max_in_flight=4)
    attest(hub, owner, machine)
    row = _machine_row(hub, owner, machine)
    assert row.get("write_wings") is None and row.get("write_set") == ["platform"], row
    policy = _policy(hub, owner, machine)
    assert (policy.write_wing, policy.write_wings) == ("platform", ())
    _accepted(_start(hub, machine, card(hub, owner, board="platform"), owner=owner))
    for board in ("compute", "notes"):
        code, body = _start(hub, machine, card(hub, owner, board=board), owner=owner)
        assert code == 403 and body["error"].endswith("outside_worker_wing"), (code, body)
    # An unrelated grant change leaves both the write set and the stored column alone.
    status, body = _patch(hub, machine, {"max_in_flight": 2}, cookie=owner["cookie"])
    assert status == 200 and body["write_wings"] is None, (status, body)
    assert body["write_set"] == ["platform"] and body["binding_state"] == "owner_attested"


def test_older_registry_tables_gain_a_nullable_write_wings_column(tmp_path):
    from commonplace.control_db import ControlDB
    from commonplace.runner import registry

    db = ControlDB(tmp_path / "control.sqlite3")
    with db._lock:
        db._conn.execute(
            "CREATE TABLE runner_machines (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, "
            "label TEXT NOT NULL, machine_connection_id TEXT NOT NULL UNIQUE, "
            "worker_connection_id TEXT NOT NULL UNIQUE, projects TEXT NOT NULL, "
            "topics TEXT NOT NULL, wings TEXT NOT NULL, revoked_at TEXT)"
        )
        db._conn.execute(
            "INSERT INTO runner_machines VALUES ('mach_0123456789abcdef','ws','old','m','w',"
            "'[]','[]',?,NULL)",
            (json.dumps(["platform", "compute"]),),
        )
    registry.ensure(db)
    registry.ensure(db)
    row = registry.get_machine(db, "ws", "mach_0123456789abcdef")
    assert "write_wings" in row.keys() and row["write_wings"] is None
    assert registry.machine_write_set(row) == ["platform"]
