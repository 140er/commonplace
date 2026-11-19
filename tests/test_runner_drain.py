"""Drain: a runner starts no new card runs while its started runs finish, enforced by the hub itself."""

import pytest

from commonplace.compute import routes
from commonplace.runner import capacity, drain, jobs, registry
from tests.compute_overview_helpers import Fleet
from tests.test_compute_api import API
from tests.test_compute_control import compute_env  # noqa: F401 -- shared real-store fixture

BASE = "/overview"


@pytest.fixture
def api(compute_env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(routes, "open_store", lambda gateway, workspace_id: compute_env.store)
    return API(compute_env)


@pytest.fixture
def fleet(api):
    return Fleet(api.env)


def runner(api, made):
    status, result = api.request(path=f"{BASE}/runner/{made['machine_id']}")
    assert status == 200, result
    return result["item"]


def act(api, made, action, seq=None, **extra):
    current = runner(api, made)
    seq = current["basis"]["seq"] if seq is None else seq
    return api.request(
        "POST", f"{BASE}/runner/{made['machine_id']}/{action}", {"expected_seq": seq, **extra}
    )


def machine_row(api, made):
    return registry.get_machine(api.env.db, api.env.workspace, made["machine_id"])


def test_drain_takes_effect_in_the_hub_and_queued_runs_wait(api, fleet):
    made = fleet.enroll("runner-lease")
    fleet.seen(made, 5)
    epoch = machine_row(api, made)["binding_epoch"]
    fleet.job(made, "queued", task=11, binding_epoch=epoch)
    store = fleet.store()
    machine = machine_row(api, made)
    status, result = act(api, made, "drain")
    assert status == 200 and result["item"]["draining"] is True
    # Nothing is handed to the drained machine and the queued run is not cancelled.
    assert jobs.lease(store, machine) is None
    with store.connection() as conn:
        assert (
            conn.execute("SELECT state FROM runner_jobs WHERE task_number=11").fetchone()[0]
            == "queued"
        )
        assert capacity.available(conn, machine, jobs._now()) == 0
    status, result = act(api, made, "resume")
    assert status == 200 and result["item"]["draining"] is False
    leased = jobs.lease(store, machine)
    assert leased is not None and leased["state"] == "leased"


def test_runs_already_started_are_left_alone_and_the_status_says_draining_until_none_remain(
    api, fleet
):
    made = fleet.enroll("runner-draining", max_in_flight=3)
    fleet.seen(made, 5)
    fleet.job(made, "started", task=1)
    act(api, made, "drain")
    item = runner(api, made)
    assert item["status"]["code"] == "draining" and item["status"]["tone"] == "wait"
    assert "1 run in progress will finish" in item["status"]["detail"]
    assert [b["code"] for b in item["health"]["blockers"]] == ["draining"]
    with fleet.store().transaction() as conn:
        conn.execute("UPDATE runner_jobs SET state='failed'")
    assert runner(api, made)["status"]["code"] == "drained"
    assert "Resume to use it again" in runner(api, made)["status"]["detail"]


def test_an_offline_drained_runner_still_says_offline_and_mentions_the_drain(api, fleet):
    made = fleet.enroll("runner-offline-drain")
    fleet.seen(made, 4000)
    act(api, made, "drain")
    item = runner(api, made)
    assert item["status"]["code"] == "offline"
    assert "set to drain" in item["status"]["detail"]


def test_preview_states_what_happens_to_running_work_before_the_confirm(api, fleet):
    made = fleet.enroll("runner-preview")
    fleet.seen(made, 5)
    fleet.job(made, "started", task=1)
    fleet.job(made, "queued", task=2)
    status, preview = api.request(path=f"{BASE}/runner/{made['machine_id']}/preview/drain")
    assert status == 200 and preview["allowed"] is True and preview["danger"] is True
    assert preview["running_work"] == "1 run in progress will finish normally; none is cancelled."
    assert "Queued runs (1) wait in the queue" in preview["effect"][0]
    assert any(
        "Chat and Coworker sessions are not affected" in line for line in preview["will_not"]
    )
    assert any("does not shut the machine down" in line for line in preview["will_not"])
    assert preview["basis"] == {"seq": 0}
    # Read-only: nothing changed.
    assert runner(api, made)["draining"] is False
    _, resume = api.request(path=f"{BASE}/runner/{made['machine_id']}/preview/resume")
    assert resume["allowed"] is False and resume["refusal"]["reason"] == "not_draining"


def test_a_repeat_is_not_an_error_and_is_audited_once(api, fleet):
    made = fleet.enroll("runner-repeat")
    status, first = act(api, made, "drain")
    assert (status, first["repeated"]) == (200, False)
    status, again = api.request(
        "POST", f"{BASE}/runner/{made['machine_id']}/drain", {"expected_seq": 0, "request_id": "x"}
    )
    assert (status, again["repeated"]) == (200, True)
    status, none = act(api, made, "resume")
    assert none["repeated"] is False
    status, none = api.request(
        "POST", f"{BASE}/runner/{made['machine_id']}/resume", {"expected_seq": 2}
    )
    assert none["repeated"] is True
    with api.env.db._lock:
        rows = api.env.db._conn.execute(
            "SELECT action FROM audit_log WHERE action LIKE 'runner.drain%' ORDER BY seq"
        ).fetchall()
    assert [r["action"] for r in rows] == ["runner.drain_started", "runner.drain_ended"]


def test_a_stale_token_is_refused_and_changes_nothing(api, fleet):
    made = fleet.enroll("runner-stale")
    status, result = act(api, made, "drain", seq=7)
    assert (status, result) == (409, {"error": "conflict", "reason": "stale_state"})
    assert runner(api, made)["draining"] is False


def test_a_revoked_runner_cannot_be_drained(api, fleet):
    made = fleet.enroll("runner-revoked")
    registry.revoke(api.env.db, api.env.principal, made["machine_id"])
    assert act(api, made, "drain") == (409, {"error": "conflict", "reason": "revoked"})
    _, preview = api.request(path=f"{BASE}/runner/{made['machine_id']}/preview/drain")
    assert preview["allowed"] is False and preview["refusal"]["reason"] == "revoked"


def test_bad_bodies_name_the_field(api, fleet):
    made = fleet.enroll("runner-body")
    url = f"{BASE}/runner/{made['machine_id']}/drain"
    assert api.request("POST", url, {})[1] == {
        "error": "invalid_request",
        "field": "expected_seq",
        "reason": "required",
    }
    assert api.request("POST", url, {"expected_seq": "0"})[1]["reason"] == "invalid"
    assert (
        api.request("POST", url, {"expected_seq": 0, "expected_version": 1})[1]["reason"]
        == "unknown_field"
    )
    assert (
        api.request("POST", url, {"expected_seq": 0, "request_id": "no spaces"})[1]["field"]
        == "request_id"
    )
    assert (
        api.request("POST", f"{BASE}/runner/{made['machine_id']}/start", {"expected_seq": 0})[1][
            "reason"
        ]
        == "route"
    )
    assert api.request("POST", f"{BASE}/runner/mach_0000000000000000/drain", {"expected_seq": 0})[
        1
    ] == {
        "error": "not_found",
        "reason": "record",
    }


def test_drain_state_is_plain_and_a_machine_never_drained_reads_as_active(api, fleet):
    made = fleet.enroll("runner-plain")
    with fleet.store().connection() as conn:
        assert drain.state(conn, made["machine_id"]) == {
            "draining": False,
            "seq": 0,
            "changed_by": None,
            "changed_at": None,
        }
        assert drain.is_drained(conn, made["machine_id"]) is False
    with pytest.raises(drain.Refused):
        with fleet.store().transaction() as conn:
            drain.change(
                conn,
                made["machine_id"],
                draining=True,
                expected_seq=3,
                by="u",
                now="2026-01-01T00:00:00Z",
            )
