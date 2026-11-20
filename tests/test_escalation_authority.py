"""Final-model account authority, refusal atomicity and unchanged lease fences."""

import pytest

from commonplace.orch import runner_bridge
from commonplace.projects import escalation, model_modes
from commonplace.runner import registry
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_orch_runner import PROVIDER, World, config_text, http, refused
from tests.test_projects_escalation import set_rule, set_state

FINAL_PROVIDER = "example-provider-two"
FINAL_MODEL = {"provider": FINAL_PROVIDER, "model": "example-strong"}


def setup(hub, *, first=True, final=True):
    w = World(hub)
    model_modes.put(w.db, w.owner, {"mode": "personal"})
    if first:
        model_modes.add_connection(
            w.db,
            w.owner,
            {"provider": PROVIDER, "label": "First plan", "machine": w.machine_id},
        )
    other = registry.enroll(
        w.db,
        w.owner,
        {
            "label": "runner-two",
            "projects": ["demo"],
            "topics": ["worker"],
            "wings": ["shared"],
            "requesters": [w.a.connection_id, w.b.connection_id],
        },
    )
    registry.attest(
        w.db,
        w.owner,
        other["machine_id"],
        {
            "topics": ["worker"],
            "provider_instance_id": "worker-two",
            "worker_connection_id": other["worker_connection_id"],
        },
    )
    connection = (
        model_modes.add_connection(
            w.db,
            w.owner,
            {"provider": FINAL_PROVIDER, "label": "Final plan", "machine": other["machine_id"]},
        )
        if final
        else None
    )
    w.configure()
    set_rule(w, model=FINAL_MODEL)
    return w, other, connection


def lease(w, machine):
    return http(w.hub.port, "POST", "/api/runner/lease", token=machine["machine_token"], body={})


def needs(w):
    with w.db._lock:
        return [dict(r) for r in w.db._conn.execute("SELECT * FROM model_connection_needs")]


def assert_pin(w, task, machine, connection):
    [job] = w.jobs_for(task)
    [run] = w.rows("SELECT * FROM run_ledger WHERE job_id=?", (job["id"],))
    assert job["machine_id"] == machine["machine_id"]
    assert (job["model_provider"], job["model"]) == (
        FINAL_PROVIDER,
        FINAL_MODEL["model"],
    )
    assert job["account_connection_id"] == connection["id"]
    for row in (job, run):
        assert row["account_person"] == w.owner.user_id
        assert row["account_kind"] == "person" and row["account_label"] == "Final plan"
    return job


def test_only_the_final_provider_needs_a_connection_and_escalation_runs_once(hub, monkeypatch):
    w, machine, connection = setup(hub, first=False)
    task = w.card(labels=["security"])
    pick = escalation.pick
    calls = []

    def counted(*args, **kwargs):
        calls.append(args[2])
        return pick(*args, **kwargs)

    monkeypatch.setattr(escalation, "pick", counted)
    assert w.person_start(task)[0] == 201
    assert len(calls) == 1
    assert needs(w) == [], "the unused initial provider must never create a missing-account item"
    assert_pin(w, task, machine, connection)
    status, command = lease(w, machine)
    assert status == 200
    assert command["modelSelection"] == {
        "instanceId": FINAL_PROVIDER,
        "model": FINAL_MODEL["model"],
    }
    assert not {"account_person", "account_label", "account_connection_id"} & command.keys()


@pytest.mark.parametrize(
    "change,code",
    [
        ("missing", "connection_missing"),
        ("revoked", "connection_missing"),
        ("disabled", "connection_missing"),
        ("unbound", "machine_unavailable"),
        ("project", "project_not_allowed"),
        ("topic", "topic_not_allowed"),
        ("wing", "outside_worker_wing"),
        ("runtime", "mode_not_allowed"),
    ],
)
def test_refused_final_authority_never_creates_a_job_or_ledger_row(hub, change, code):
    w, machine, connection = setup(hub, final=change != "missing")
    machine_id = machine["machine_id"]
    if change == "revoked":
        model_modes.revoke_connection(w.db, w.owner, connection["id"])
    elif change == "disabled":
        registry.patch(w.db, w.owner, machine_id, {"enabled": False})
    elif change == "unbound":
        registry.patch(w.db, w.owner, machine_id, {"topics": ["worker", "other"]})
    elif change == "runtime":
        registry.patch(w.db, w.owner, w.machine_id, {"max_runtime_mode": "full-access"})
        w.write(
            config_text(w.machine_id).replace(
                'runtime_mode = "approval-required"', 'runtime_mode = "auto"'
            )
        )
    elif change in {"project", "topic", "wing"}:
        grant = {
            "project": {"projects": ["other"]},
            "topic": {"topics": ["other"]},
            "wing": {"wings": ["inbox"]},
        }[change]
        row = registry.patch(w.db, w.owner, machine_id, grant)
        if change in {"topic", "wing"}:
            registry.attest(
                w.db,
                w.owner,
                machine_id,
                {
                    "topics": ["other"] if change == "topic" else ["worker"],
                    "provider_instance_id": "worker-two",
                    "worker_connection_id": row["worker_connection_id"],
                },
            )
    task = w.card(labels=["security"])
    before = {
        table: w.rows(f"SELECT * FROM {table}")
        for table in ("runner_jobs", "run_ledger", "cost_events")
    }
    refusal = refused(code, w.person_start, task)
    assert refusal.status == 409
    assert {table: w.rows(f"SELECT * FROM {table}") for table in before} == before
    assert w.all_jobs() == []
    if code == "connection_missing":
        [item] = needs(w)
        assert item["provider"] == FINAL_PROVIDER and item["user_id"] == w.owner.user_id
        assert FINAL_PROVIDER in str(refusal)


def test_final_machine_requester_fence_applies_before_orchestrator_admission(hub):
    w, machine, _ = setup(hub)
    registry.patch(w.db, w.owner, machine["machine_id"], {"requesters": [w.b.connection_id]})
    task = w.card(labels=["security"])
    claim = w.claim(w.a)
    refused("not_allowed", w.orch_start, w.a, claim, task, "final-requester")
    assert w.all_jobs() == [] and w.steps() == []
    assert w.rows("SELECT * FROM run_ledger") == []


@pytest.mark.parametrize("path", ["person", "orch"])
def test_failed_round_escalation_uses_final_authority_and_retries_replay(hub, path):
    w, machine, connection = setup(hub)
    task = w.card()
    set_rule(w, model=FINAL_MODEL, after_failures=1, labels=[])
    status, first = w.person_start(task, request_key="first")
    assert status == 201
    set_state(w, first["job_id"], "failed")
    if path == "person":
        assert w.person_start(task, request_key="final")[0] == 201

        def replay():
            return w.person_start(task, request_key="final")
    else:
        claim = w.claim(w.a)
        assert w.orch_start(w.a, claim, task, "final")["status"] == 201

        def replay():
            return w.orch_start(w.a, claim, task, "final")

    final = w.jobs_for(task)[-1]
    assert final["model_reason"] == "failed_rounds:1"
    assert final["account_connection_id"] == connection["id"]
    assert final["machine_id"] == machine["machine_id"]
    before = w.all_jobs()
    set_rule(w, model=FINAL_MODEL, after_failures=5, labels=[])
    answer = replay()
    assert (answer[0] if path == "person" else answer["status"]) == 200
    assert w.all_jobs() == before


@pytest.mark.parametrize("change", ["revocation", "replacement"])
def test_queued_escalated_job_preserves_its_exact_connection_fence(hub, change):
    w, machine, connection = setup(hub)
    task = w.card(labels=["security"])
    assert w.person_start(task)[0] == 201
    original = assert_pin(w, task, machine, connection)
    model_modes.revoke_connection(w.db, w.owner, connection["id"])
    if change == "replacement":
        replacement = model_modes.add_connection(
            w.db,
            w.owner,
            {"provider": FINAL_PROVIDER, "label": "Final plan", "machine": machine["machine_id"]},
        )
        assert replacement["id"] != connection["id"]
    assert lease(w, machine)[0] == 204
    [job] = w.jobs_for(task)
    assert (job["state"], job["outcome_code"]) == ("cancelled", "account_changed")
    assert job["account_connection_id"] == original["account_connection_id"]
    assert job["starting_at"] is None and job["started_at"] is None
    assert w.rows("SELECT * FROM cost_events") == []


def test_account_resolution_uses_the_control_snapshot_without_reads_in_the_start_lock(
    hub, monkeypatch
):
    w, machine, connection = setup(hub)
    task = w.card(labels=["security"])
    prepare = runner_bridge.prepare

    def snapshotted(*args, **kwargs):
        plan = prepare(*args, **kwargs)
        monkeypatch.setattr(
            model_modes, "snapshot_account", lambda *a, **k: pytest.fail("late control read")
        )
        monkeypatch.setattr(
            registry, "get_machine", lambda *a, **k: pytest.fail("late machine read")
        )
        return plan

    monkeypatch.setattr(runner_bridge, "prepare", snapshotted)
    assert w.person_start(task)[0] == 201
    assert_pin(w, task, machine, connection)


def test_synthetic_http_smoke_admits_final_account_and_refuses_its_absence(hub, caplog):
    w, machine, connection = setup(hub)
    assert http(w.hub.port, "GET", "/healthz")[0] == 200
    task = w.card(labels=["security"])
    status, reply, _ = w.api.request(w.api.owner, "POST", "/api/card-starts", {"task": task})
    assert status == 201, reply
    assert_pin(w, task, machine, connection)
    assert lease(w, machine)[0] == 200
    model_modes.revoke_connection(w.db, w.owner, connection["id"])
    refused_task = w.card(labels=["security"])
    before = w.rows("SELECT * FROM run_ledger")
    status, reply, _ = w.api.request(
        w.api.owner, "POST", "/api/card-starts", {"task": refused_task}
    )
    assert (status, reply.get("code")) == (409, "connection_missing")
    assert FINAL_PROVIDER in reply["error"]
    assert w.jobs_for(refused_task) == []
    assert w.rows("SELECT * FROM run_ledger") == before
    assert not any(record.exc_info for record in caplog.records)
