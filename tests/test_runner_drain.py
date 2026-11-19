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
    assert item["status"]["detail"] == (
        "No new card runs start here. Chat and Coworker sessions can still start here. "
        "1 run in progress will finish. Resuming it is not available in this version."
    )
    assert [b["code"] for b in item["health"]["blockers"]] == ["draining"]
    with fleet.store().transaction() as conn:
        conn.execute("UPDATE runner_jobs SET state='failed'")
    assert runner(api, made)["status"]["code"] == "drained"
    drained = runner(api, made)["status"]["detail"]
    assert (
        "No new card runs start here. Chat and Coworker sessions can still start here." in drained
    )
    assert "Resume to use it again" not in drained


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
    assert "Queued runs (1) stay in the queue" in preview["effect"][0]
    assert any(
        "Chat and Coworker sessions can still start here" in line for line in preview["will_not"]
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


def test_a_request_id_is_kept_with_the_drain_and_a_reused_one_is_refused(api, fleet):
    made = fleet.enroll("runner-ids")
    url = f"{BASE}/runner/{made['machine_id']}"
    status, first = api.request("POST", url + "/drain", {"expected_seq": 0, "request_id": "one"})
    assert (status, first["repeated"]) == (200, False)
    # A retry with the same id after a lost answer, even with a token that is now old, is the same request.
    status, again = api.request("POST", url + "/drain", {"expected_seq": 0, "request_id": "one"})
    assert (status, again["repeated"]) == (200, True)
    assert runner(api, made)["basis"] == {"seq": 1}
    # The same id for the other action is refused with the field named, and nothing changes.
    assert api.request("POST", url + "/resume", {"expected_seq": 1, "request_id": "one"}) == (
        409,
        {"error": "conflict", "field": "request_id", "reason": "request_reused"},
    )
    assert runner(api, made)["draining"] is True


def test_the_drain_flag_and_its_log_line_are_one_transaction(api, fleet):
    made = fleet.enroll("runner-log")
    act(api, made, "drain")
    with fleet.store().connection() as conn:
        rows = drain.log(conn, made["machine_id"], 10)
    assert [r["action"] for r in rows] == ["drain"]
    # A change that fails after the flag was written leaves neither the flag nor the line.
    with pytest.raises(RuntimeError):
        with fleet.store().transaction() as conn:
            drain.change(
                conn,
                made["machine_id"],
                draining=False,
                expected_seq=1,
                by="u",
                now="2026-01-01T00:00:00Z",
            )
            raise RuntimeError("fail before commit")
    with fleet.store().connection() as conn:
        assert drain.is_drained(conn, made["machine_id"]) is True
        assert [r["action"] for r in drain.log(conn, made["machine_id"], 10)] == ["drain"]
    # The activity list says so in words.
    texts = [e["text"] for e in runner(api, made)["activity"]]
    assert "Drain started: no new card runs start here" in texts


def test_a_drain_table_made_before_request_ids_existed_is_upgraded_in_place(api, fleet):
    made = fleet.enroll("runner-old-table")
    with fleet.store().transaction() as conn:
        conn.execute(
            "CREATE TABLE runner_drain (machine_id TEXT PRIMARY KEY, draining INTEGER NOT NULL, seq INTEGER NOT NULL, changed_by TEXT NOT NULL, changed_at TEXT NOT NULL)"
        )
    status, result = act(api, made, "drain", request_id="old")
    assert status == 200 and result["item"]["draining"] is True


# round 1 of the #615 review: drain says what it stops, and the hub-wide audit goes with the change


def test_a_failed_hub_audit_leaves_no_drain_nobody_recorded(api, fleet, monkeypatch):
    made = fleet.enroll("runner-audit")

    def refuse(**_):
        raise OSError("hub audit down")

    monkeypatch.setattr(api.env.db, "audit", refuse)
    status, result = act(api, made, "drain")
    assert (status, result) == (503, {"error": "authority_unavailable", "reason": "audit"})
    monkeypatch.undo()
    assert runner(api, made)["draining"] is False
    with fleet.store().connection() as conn:
        assert drain.log(conn, made["machine_id"], 10) == []
        assert drain.state(conn, made["machine_id"])["seq"] == 0


def test_the_hub_audit_line_is_written_before_the_drain_commits(api, fleet, monkeypatch):
    made = fleet.enroll("runner-order")
    seen = {}
    real = api.env.db.audit

    def spy(**fields):
        # At this moment the drain transaction is still open: its flag is not visible to another connection.
        with fleet.store().connection() as conn:
            seen["draining_when_audited"] = drain.is_drained(conn, made["machine_id"])
        return real(**fields)

    monkeypatch.setattr(api.env.db, "audit", spy)
    assert act(api, made, "drain")[0] == 200
    assert seen == {"draining_when_audited": False}
    assert runner(api, made)["draining"] is True


def test_the_poll_reads_runs_through_indexes_and_opens_the_runner_store_once(
    api, fleet, monkeypatch
):
    from commonplace.compute import overview

    made = fleet.enroll("runner-index")
    fleet.job(made, "started", task=1)
    stamp = "2026-01-01T00:00:00Z"
    with fleet.store().connection() as conn:
        names = {r[1] for r in conn.execute("PRAGMA index_list(runner_jobs)")}
        assert {"runner_jobs_state_updated", "runner_jobs_starting"} <= names
        # Every read of a poll searches an index (a SCAN reads the whole history, which is never pruned).
        for sql, params in (
            (overview.FACT_ACTIVE_SQL, ()),
            (overview.FACT_STARTED_RECENT_SQL, (stamp,)),
            (overview.FACT_STARTED_OLDER_SQL, (stamp, stamp)),
            (overview.FACT_STARTS_HOUR_SQL, (stamp,)),
        ):
            plan = " ".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params))
            assert "SEARCH" in plan and "SCAN" not in plan, plan
        for sql in (
            "SELECT machine_id,max(updated_at) FROM runner_jobs INDEXED BY runner_jobs_state_updated "
            "WHERE state='started' AND updated_at>? GROUP BY machine_id",
        ):
            plan = " ".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, (stamp,)))
            assert "SEARCH" in plan and "SCAN" not in plan, plan
    opened = []
    real = jobs.JobStore.__init__

    def counting(self, *a, **k):
        opened.append(1)
        real(self, *a, **k)

    monkeypatch.setattr(jobs.JobStore, "__init__", counting)
    assert api.request(path=BASE)[0] == 200
    assert len(opened) == 1
    opened.clear()
    assert runner(api, made)["id"] == made["machine_id"]
    # The detail reads the facts and the activity: one open each, not one per query.
    assert len(opened) <= 2


def test_drain_is_stated_to_stop_card_runs_only(api, fleet):
    made = fleet.enroll("runner-scope")
    fleet.seen(made, 5)
    act(api, made, "drain")
    detail = runner(api, made)
    scope = "No new card runs start here. Chat and Coworker sessions can still start here."
    assert detail["status"]["detail"].startswith(scope)
    # Chat and Coworker leases do not read the flag: pinned here so the sentence cannot drift from the code.
    import inspect

    from commonplace.runner import chat_relay, sessions

    for module in (chat_relay, sessions):
        assert "drain" not in inspect.getsource(module)


def test_a_runner_removed_between_the_check_and_the_drain_is_refused_inside_the_lock(
    api, fleet, monkeypatch
):
    from commonplace.compute import overview

    made = fleet.enroll("runner-race")
    real = overview._machine_row
    calls = []

    def racing(store, machine_id):
        calls.append(1)
        row = real(store, machine_id)
        # The route's concealment read and the pre-check see it not removed yet; the read inside the lock sees the removal.
        return row if len(calls) <= 2 else {**dict(row), "revoked_at": "2026-11-19T10:00:00Z"}

    seq = runner(api, made)["basis"]["seq"]  # read before the race starts
    monkeypatch.setattr(overview, "_machine_row", racing)
    assert api.request(
        "POST", f"{BASE}/runner/{made['machine_id']}/drain", {"expected_seq": seq}
    ) == (409, {"error": "conflict", "reason": "revoked"})
    assert len(calls) == 3
    monkeypatch.undo()
    assert runner(api, made)["draining"] is False
    with fleet.store().connection() as conn:
        assert drain.log(conn, made["machine_id"], 10) == []


def test_a_busy_runner_store_is_busy_not_an_audit_failure(api, fleet, monkeypatch):
    import sqlite3

    made = fleet.enroll("runner-busy-store")

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(drain, "change", locked)
    assert act(api, made, "drain") == (503, {"error": "authority_unavailable", "reason": "busy"})
    monkeypatch.undo()
    assert runner(api, made)["draining"] is False
