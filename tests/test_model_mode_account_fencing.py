"""Account identity fences for persistent sessions and admitted card starts."""

import pytest

from commonplace.projects import model_modes
from commonplace.runner import jobs, sessions
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_orch_runner import PROVIDER, World, config_text
from tests.test_orch_sessions import session_world as session_world, start

ACCOUNT_FIELDS = (
    "mode",
    "mode_source",
    "account_kind",
    "account_label",
    "account_person",
)
PIN = "account_connection_id"


def _mode(w, mode):
    model_modes.put(w.db, w.owner, {"mode": mode})


def _connect(w):
    return model_modes.add_connection(
        w.db,
        w.owner,
        {"provider": PROVIDER, "label": "My plan", "machine": w.machine_id},
    )


def _replace(w, connection):
    model_modes.revoke_connection(w.db, w.owner, connection["id"])
    replacement = _connect(w)
    assert replacement["id"] != connection["id"]
    assert replacement["label"] == connection["label"]
    assert replacement["machine"] == connection["machine"]
    return replacement


def _session(w):
    [row] = w.rows("SELECT * FROM runner_sessions")
    return row


def _without_columns(w, table, names):
    """Emulate a populated store written before the account pins existed."""
    with w.js.connection() as conn:
        columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name in names:
            if name in columns:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {name}")


@pytest.mark.parametrize("change", ["org_to_personal", "personal_to_org", "replacement"])
def test_existing_session_rejects_another_currently_valid_account(session_world, change):
    w = session_world
    _mode(w, "org" if change == "org_to_personal" else "personal")
    original_connection = None if change == "org_to_personal" else _connect(w)
    start(w)
    original = _session(w)
    if change == "org_to_personal":
        _connect(w)
        _mode(w, "personal")
    elif change == "personal_to_org":
        model_modes.revoke_connection(w.db, w.owner, original_connection["id"])
        _mode(w, "org")
    else:
        _replace(w, original_connection)
    decision = model_modes.decide(
        w.db, w.ws, "acme", person=w.owner.user_id, provider=PROVIDER, machine=w.machine()
    )
    assert decision.refusal is None, "the changed account must itself be valid"
    assert w.tick() == 0
    assert len(w.calls) == 1, "an existing session must not inspect or continue on another account"
    assert w.session()["reason"] == "account_changed"
    assert _session(w) == original, "a refused lease must preserve the original session record"
    assert w.control("resume")[0] == 403


@pytest.mark.parametrize("mode", ["org", "personal", "legacy"])
def test_unchanged_session_keeps_its_original_account(session_world, mode):
    w = session_world
    if mode != "legacy":
        _mode(w, mode)
    connection = _connect(w) if mode == "personal" else None
    start(w)
    original = _session(w)
    assert w.tick() == 0  # inspect the original thread
    assert w.tick() == 0  # continue it while work remains
    assert len(w.wakes()) == 2
    assert w.wakes()[-1][1]["threadId"] == "session-thread"
    row = _session(w)
    assert {key: row[key] for key in ACCOUNT_FIELDS} == {
        key: original[key] for key in ACCOUNT_FIELDS
    }
    assert row[PIN] == (connection["id"] if connection else None)
    assert not {PIN, "account_person", "account_label"} & w.session().keys()
    assert w.machine_token not in repr(row)


@pytest.mark.parametrize("change", ["replacement", "revocation"])
def test_session_rechecks_its_account_after_lease_before_host_effect(session_world, change):
    w = session_world
    _mode(w, "personal")
    connection = _connect(w)
    w.card()
    status, command = w.machine_call("sessions/lease")
    assert status == 200
    if change == "replacement":
        _replace(w, connection)
    else:
        model_modes.revoke_connection(w.db, w.owner, connection["id"])
    assert w.machine_call("sessions/starting", {"lease_token": command["lease_token"]})[0] == 409
    assert not w.calls
    assert _session(w)["phase"] == "leased"


def test_legacy_session_migration_preserves_thread_and_refuses_unknown_account(session_world):
    w = session_world
    start(w)
    original = _session(w)
    _without_columns(w, "runner_sessions", (*ACCOUNT_FIELDS, PIN))
    sessions._open(w.gw, w.ws)
    migrated = _session(w)
    assert {k: migrated[k] for k in original if k not in (*ACCOUNT_FIELDS, PIN)} == {
        k: v for k, v in original.items() if k not in (*ACCOUNT_FIELDS, PIN)
    }
    assert w.tick() == 0
    assert len(w.calls) == 1, "migration must never invent authority for the existing thread"
    assert w.session()["reason"] == "account_changed"
    assert all(migrated[key] is None for key in (*ACCOUNT_FIELDS, PIN))
    assert w.control("stop")[0] == 200
    assert _session(w)["paused"] == 1


@pytest.fixture
def account_world(hub):
    return World(hub)


def _queued(w, *, personal=True, configured=False):
    _mode(w, "personal" if personal else "org")
    connection = _connect(w) if personal else None
    if configured:
        w.write(config_text(w.machine_id).split("[[triggers]]")[0])
    task = w.card(board="acme" if configured else "plain", status="todo")
    status, command = w.person_start(task)
    assert status == 201, command
    [job] = w.jobs_for(task)
    assert job["state"] == "queued"
    return connection, job


def _assert_refused(w, original, *, attempt=0):
    [job] = w.jobs_for(original["task_number"])
    assert (job["state"], job["outcome_code"]) == ("cancelled", "account_changed")
    assert job["attempt"] == attempt
    assert job["starting_at"] is None and job["started_at"] is None
    assert {key: job[key] for key in ACCOUNT_FIELDS} == {
        key: original[key] for key in ACCOUNT_FIELDS
    }
    [run] = w.rows("SELECT * FROM run_ledger WHERE job_id=?", (job["id"],))
    assert run["status"] == "cancelled"
    assert run["started_at"] is None and run["cost_micros"] is None
    assert {key: run[key] for key in ACCOUNT_FIELDS} == {
        key: original[key] for key in ACCOUNT_FIELDS
    }
    assert w.rows("SELECT * FROM cost_events") == []
    assert len(w.rows("SELECT * FROM run_ledger")) == 1
    return job


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("change", ["revocation", "replacement"])
def test_admitted_personal_job_refuses_changed_connection_before_lease(
    account_world, configured, change
):
    w = account_world
    connection, original = _queued(w, configured=configured)
    if change == "replacement":
        _replace(w, connection)
    else:
        model_modes.revoke_connection(w.db, w.owner, connection["id"])
    assert w.machine()["enabled"] and not w.machine()["revoked_at"]
    assert w.machine_call("lease")[0] == 204, "an enabled runner is not proof of the pinned account"
    job = _assert_refused(w, original)
    assert job["lease_token"] is None and job["lease_until"] is None


@pytest.mark.parametrize("change", ["revocation", "replacement"])
def test_personal_job_rechecks_connection_after_lease_before_start(account_world, change):
    w = account_world
    connection, original = _queued(w)
    command = w.lease()
    if change == "replacement":
        _replace(w, connection)
    else:
        model_modes.revoke_connection(w.db, w.owner, connection["id"])
    assert w.machine_call("starting", {"lease_token": command["lease_token"]})[0] == 409
    _assert_refused(w, original, attempt=1)


@pytest.mark.parametrize("personal", [False, True])
@pytest.mark.parametrize("configured", [False, True])
def test_unchanged_job_leases_and_starts_with_original_account(account_world, personal, configured):
    w = account_world
    connection, original = _queued(w, personal=personal, configured=configured)
    # New settings govern new admissions; this job keeps its original account.
    _mode(w, "org" if personal else "personal")
    command = w.lease()
    assert not {PIN, "account_person", "account_label"} & command.keys()
    assert w.machine_call("starting", {"lease_token": command["lease_token"]})[0] == 200
    [job] = w.jobs_for(original["task_number"])
    assert job["state"] == "starting" and job["attempt"] == 1
    assert job[PIN] == (connection["id"] if connection else None)
    assert {key: job[key] for key in ACCOUNT_FIELDS} == {
        key: original[key] for key in ACCOUNT_FIELDS
    }
    assert w.machine_token not in repr(job)


@pytest.mark.parametrize("personal", [False, True])
def test_job_pin_migration_preserves_rows_and_refuses_unpinned_personal_job(
    account_world, personal
):
    w = account_world
    _, original = _queued(w, personal=personal)
    _without_columns(w, "runner_jobs", (PIN,))
    jobs.open_store(w.gw, w.ws)
    [migrated] = w.jobs_for(original["task_number"])
    assert {k: migrated[k] for k in original if k != PIN} == {
        k: v for k, v in original.items() if k != PIN
    }
    status, _ = w.machine_call("lease")
    assert status == (204 if personal else 200)
    assert migrated[PIN] is None, "migration must never fabricate a connection identity"
    if personal:
        _assert_refused(w, original)


def test_pre_mode_job_still_leases_on_unchanged_shared_machine(account_world):
    w = account_world
    _, original = _queued(w, personal=False)
    _without_columns(w, "runner_jobs", (*ACCOUNT_FIELDS, PIN))
    jobs.open_store(w.gw, w.ws)
    assert w.machine_call("lease")[0] == 200
    [job] = w.jobs_for(original["task_number"])
    assert all(job[key] is None for key in (*ACCOUNT_FIELDS, PIN))
