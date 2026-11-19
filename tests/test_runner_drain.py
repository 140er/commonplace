"""Drain: a runner starts no new card runs while its started runs finish, enforced by the hub itself."""

import pytest

from commonplace.compute import overview, routes
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
    fleet.job(made, "started", task=fleet.work(made))
    act(api, made, "drain")
    item = runner(api, made)
    assert item["status"]["code"] == "draining" and item["status"]["tone"] == "wait"
    assert item["status"]["detail"] == (
        "Draining. No new card runs start here. Chat and Coworker sessions can still start here. "
        "1 run in progress will finish. This screen has no button to resume it yet."
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
    fleet.job(made, "started", task=fleet.work(made))
    fleet.job(made, "queued", task=2)
    status, preview = api.request(path=f"{BASE}/runner/{made['machine_id']}/preview/drain")
    assert status == 200 and preview["allowed"] is True and preview["danger"] is True
    assert preview["running_work"] == "1 run in progress will finish normally; none is cancelled."
    assert "Queued runs (1) stay in the queue" in preview["effect"][0]
    # The confirmation says what drain stops in the one sentence every other screen uses, word for word.
    assert overview.DRAIN_SCOPE in preview["will_not"]
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
    assert (
        "Drain started. No new card runs start here. Chat and Coworker sessions can still start here."
        in texts
    )


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
    fleet.resources(made)  # creates the resource table the plan below reads
    act(api, made, "drain")  # creates the drain log
    stamp = "2026-01-01T00:00:00Z"
    with fleet.store().connection() as conn:
        names = {r[1] for r in conn.execute("PRAGMA index_list(runner_jobs)")}
        assert {"runner_jobs_state_updated", "runner_jobs_starting"} <= names
        # Every read of a poll searches an index (a SCAN reads the whole history, which is never pruned).
        for sql, params in (
            (overview.FACT_QUEUED_SQL, ()),
            (jobs.IN_FLIGHT_SQL, ("m",)),
            (overview.RECENT_RUNS_SQL, ("m", 10)),
            (overview.FACT_RESOURCES_SQL.format("?"), ("m",)),
            (overview.FACT_ENDED_SQL.format("?"), ("j",)),
            (
                "SELECT action,by,at FROM runner_drain_log WHERE machine_id=? ORDER BY id DESC LIMIT ?",
                ("m", 10),
            ),
            (overview.FACT_STARTED_OPEN_SQL, (stamp,)),
            (overview.FACT_SPEND_SQL, ("a", "b", "c", "d", "e", stamp, stamp)),
            (overview.FACT_SPEND_PLAIN_SQL, ("a", "b", "c", "d", "e", stamp, stamp)),
            (overview.FACT_STARTS_HOUR_SQL, (stamp,)),
        ):
            plan = " ".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params))
            assert "SEARCH" in plan and "SCAN" not in plan, plan
            assert "TEMP B-TREE FOR ORDER BY" not in plan, (
                plan
            )  # a machine's history is read in index order
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
    # The detail reads the facts (with the topology signals) and the activity: one open each, exactly.
    assert len(opened) == 2, len(opened)


def test_drain_is_stated_to_stop_card_runs_only(api, fleet):
    made = fleet.enroll("runner-scope")
    fleet.seen(made, 5)
    act(api, made, "drain")
    detail = runner(api, made)
    scope = "No new card runs start here. Chat and Coworker sessions can still start here."
    assert detail["status"]["detail"].startswith("Drained. " + scope)
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


def test_a_truly_busy_drained_runner_does_not_also_say_it_is_full(api, fleet):
    # Round 3 review: "Draining" beside "May be full: new runs wait for one to finish" contradicted itself.
    made = fleet.enroll("runner-busy-drained", max_in_flight=1)
    fleet.seen(made, 5)
    fleet.job(made, "started", task=fleet.work(made))
    before = {b["code"] for b in runner(api, made)["health"]["blockers"]}
    assert before == {"full"}
    act(api, made, "drain")
    item = runner(api, made)
    assert item["status"]["code"] == "draining"
    blockers = {b["code"]: b["text"] for b in item["health"]["blockers"]}
    assert set(blockers) == {"draining"} and blockers["draining"].startswith("Draining: ")


def test_drain_and_lease_take_their_locks_in_the_same_order(api, fleet, monkeypatch):
    """The lease takes the runner write transaction and then the control lock; a drain that took them the other way
    round could deadlock with it. A lease-like thread holds the runner transaction and then wants the control lock while
    a drain starts: both must finish."""
    import threading

    made = fleet.enroll("runner-order-2")
    store = fleet.store()
    db = api.env.db
    runner_held, drain_at_txn, results = threading.Event(), threading.Event(), {}
    real = overview.store_runner

    def lease_like():
        with store.transaction():
            runner_held.set()
            assert drain_at_txn.wait(10)
            with db._lock:  # the lease reads control data (a connection) inside its transaction
                results["lease"] = True

    def spy(store_arg):
        opened = real(store_arg)
        original = opened.transaction

        def transaction():
            drain_at_txn.set()  # the drain is about to open the runner transaction: it holds nothing yet
            return original()

        opened.transaction = transaction
        return opened

    monkeypatch.setattr(overview, "store_runner", spy)
    lease = threading.Thread(target=lease_like, daemon=True)
    lease.start()
    assert runner_held.wait(10)
    seq = runner(api, made)["basis"]["seq"]  # before the drain thread exists
    results["drain"] = []
    thread = threading.Thread(
        target=lambda: results["drain"].append(
            api.request("POST", f"{BASE}/runner/{made['machine_id']}/drain", {"expected_seq": seq})
        ),
        daemon=True,
    )
    thread.start()
    lease.join(15)
    thread.join(15)
    assert not lease.is_alive() and not thread.is_alive(), (
        "deadlock between the drain and the lease"
    )
    assert results["lease"] is True and results["drain"][0][0] == 200


def test_the_ledger_backfill_runs_once_not_at_every_open_of_the_store(api, fleet):
    from commonplace.runner import ledger

    made = fleet.enroll("runner-backfill")
    fleet.job(made, "started", task=11)
    store = fleet.store()
    # A store that predates the marker: the backfill finds the job that has no ledger row and writes it, once.
    with store.transaction() as conn:
        conn.execute("DROP TABLE run_ledger_backfill")
        conn.execute("DELETE FROM run_ledger")
    fleet.store()
    with fleet.store().connection() as conn:
        assert conn.execute("SELECT count(*) FROM run_ledger").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM run_ledger_backfill").fetchone()[0] == 1
        # A job row written behind the ledger's back is not looked for at every open any more.
        conn.execute("DELETE FROM run_ledger")
        plan = " ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + ledger._MISSING))
        assert "SCAN" in plan  # the anti-join is the expensive read the marker spares
    fleet.store()
    with fleet.store().connection() as conn:
        assert conn.execute("SELECT count(*) FROM run_ledger").fetchone()[0] == 0


@pytest.mark.parametrize(
    "name,slots,rows,full",
    [
        # (state of the started run, ended in the ledger, card still Doing, age in minutes)
        ("running", 1, [(False, True, 5)], True),
        ("finished but its card is still Doing", 1, [(True, True, 5)], True),
        ("card moved on, end reported", 1, [(True, False, 5)], False),
        ("card moved on, end never reported", 1, [(False, False, 5)], False),
        ("never ended, 8 days old, card still Doing", 1, [(False, True, 8 * 24 * 60)], True),
        (
            "two slots, one running and one finished but holding",
            2,
            [(False, True, 5), (True, True, 6)],
            True,
        ),
        ("two slots, only a finished one holding", 2, [(True, True, 5)], False),
        (
            "two slots, a finished one whose card moved on and a running one",
            2,
            [(True, False, 5), (False, True, 6)],
            False,
        ),
    ],
)
def test_the_lease_and_the_overview_agree_on_what_holds_a_slot(api, fleet, name, slots, rows, full):
    """One definition (``jobs.in_flight``) decides which started runs hold a slot; the lease refuses exactly when the
    overview says the machine is full, in every case of the matrix (a finished run whose card is still Doing included)."""
    made = fleet.enroll(f"matrix-{abs(hash(name)) % 10000}", max_in_flight=slots)
    fleet.seen(made, 5)
    for k, (ended, doing, minutes) in enumerate(rows):
        task = fleet.work(made) if doing else 900 + k
        job = fleet.job(made, "started", minutes_ago=minutes, task=task, project=f"p{k}")
        if ended:
            fleet.end(job, minutes_ago=max(0, minutes - 1))
    epoch = machine_row(api, made)["binding_epoch"]
    fleet.job(made, "queued", task=700, project="other", binding_epoch=epoch)
    shown = runner(api, made)
    says_full = any(b["code"] == "full" for b in shown["health"]["blockers"])
    leased = jobs.lease(fleet.store(), machine_row(api, made))
    assert says_full is full, (name, shown["health"]["blockers"])
    assert (leased is None) is full, (name, leased)


def test_two_first_opens_of_a_store_do_not_race_on_the_backfill_marker(api, fleet):
    """Both opens see no marker; the second must wait for the first and find it done, not fail on the marker row."""
    import threading

    from commonplace.runner import ledger

    made = fleet.enroll("runner-race-open")
    fleet.job(made, "started", task=21)
    with fleet.store().transaction() as conn:
        conn.execute("DROP TABLE run_ledger_backfill")
        conn.execute("DELETE FROM run_ledger")
    path = fleet.store().path
    from mempalace.sqlite_crypto import connect, row_factory

    errors, ready, go = [], threading.Barrier(2), threading.Event()

    def first_open():
        conn = connect(path, timeout=10, isolation_level=None)
        conn.row_factory = row_factory
        try:
            ready.wait(10)
            go.wait(10)
            ledger.ensure(conn)
        except BaseException as exc:  # noqa: BLE001 - the test reports whatever failed
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=first_open, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    go.set()
    for thread in threads:
        thread.join(20)
    assert errors == [] and not any(t.is_alive() for t in threads)
    with fleet.store().connection() as conn:
        assert conn.execute("SELECT count(*) FROM run_ledger").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM run_ledger_backfill").fetchone()[0] == 1


def test_one_unreadable_resource_report_does_not_fail_the_overview(api, fleet):
    good = fleet.enroll("runner-res-good")
    bad = fleet.enroll("runner-res-bad")
    fleet.seen(good, 5)
    fleet.seen(bad, 5)
    fleet.resources(good)
    fleet.resources(bad)
    with fleet.store().transaction() as conn:
        conn.execute(
            "UPDATE runner_resources SET resources='{not json' WHERE machine_id=?",
            (bad["machine_id"],),
        )
    status, result = api.request(path=BASE)
    assert status == 200
    items = {i["name"]: i for i in result["items"]}
    assert items["runner-res-good"]["usage"]["resources"]["known"] is True
    assert items["runner-res-bad"]["usage"]["resources"]["known"] is False


def test_a_finished_run_is_labelled_finished_not_started_in_the_activity_list(api, fleet):
    made = fleet.enroll("runner-labels")
    fleet.seen(made, 5)
    job = fleet.job(made, "started", minutes_ago=300, task=fleet.work(made))
    fleet.end(job, minutes_ago=290)
    fleet.job(made, "started", minutes_ago=10, task=fleet.work(made))
    texts = [e["text"] for e in runner(api, made)["activity"]]
    assert any(t.startswith("Finished: demo card") for t in texts), texts
    assert any(t.startswith("Started: demo card") for t in texts), texts


def test_every_drain_sentence_uses_the_one_drain_scope_and_zero_reads_none(api, fleet):
    made = fleet.enroll("runner-scope-2")
    fleet.seen(made, 4000)  # offline: the note says it is also set to drain
    act(api, made, "drain")
    item = runner(api, made)
    assert overview.DRAIN_SCOPE in item["status"]["detail"]
    assert (
        overview.DRAIN_SCOPE
        in {b["code"]: b["text"] for b in item["health"]["blockers"]}["draining"]
    )
    assert any(
        e["text"].startswith("Drain started. " + overview.DRAIN_SCOPE) for e in item["activity"]
    )
    status, preview = api.request(path=f"{BASE}/runner/{made['machine_id']}/preview/drain")
    assert preview["running_work"] == "None is in progress; nothing is cancelled."
