"""Cloud workspace lifecycle: owner requests, the state machine and the executor seam (fake executor)."""

import pytest

from commonplace.compute import routes, workspace_lifecycle as lifecycle, workspaces
from commonplace.compute.control import StoreError
from tests.compute_overview_helpers import FakeExecutor, workspace_body
from tests.test_compute_api import API
from tests.test_compute_control import compute_env  # noqa: F401 -- shared real-store fixture

OVERVIEW = "/overview"


@pytest.fixture
def api(compute_env, monkeypatch):  # noqa: F811
    # Serve every request from the fixture's store so the test clock (``env.clock``) governs the API too.
    monkeypatch.setattr(routes, "open_store", lambda gateway, workspace_id: compute_env.store)
    return API(compute_env)


def make(api, **changes):
    """A workspace on a machine, ready to be asked to start."""
    if "node_id" not in changes:
        _, node = api.enrolled()
        changes["node_id"] = node["id"]
    status, result = api.request("POST", "/workspaces", workspace_body(**changes))
    assert status == 201, result
    return result["workspace"]


def item(api, workspace, *, path=None):
    status, result = api.request(path=path or f"{OVERVIEW}/workspace/{workspace['id']}")
    assert status == 200, result
    return result["item"]


def ask(api, workspace, action, token=None, **extra):
    current = item(api, workspace)
    token = token or current["basis"]
    body = {"expected_seq": token["seq"], "expected_version": token["version"], **extra}
    return api.request("POST", f"{OVERVIEW}/workspace/{workspace['id']}/{action}", body)


def conflict(result, reason):
    assert result == (409, {"error": "conflict", "reason": reason}), result


def test_a_new_workspace_is_not_started_and_start_is_allowed_with_a_machine(api):
    ws = make(api)
    current = item(api, ws)
    assert current["status"]["code"] == "not_started"
    assert current["basis"] == {"seq": 0, "version": 1}
    allowed = {a["action"]: a["allowed"] for a in current["actions"]}
    assert allowed == {"start": True, "stop": False, "restart": False, "withdraw": False}


def test_start_request_is_recorded_as_requested_and_never_as_starting(api):
    ws = make(api)
    status, result = ask(api, ws, "start", request_id="r-1")
    assert status == 202 and result["repeated"] is False
    assert result["request"]["action"] == "start" and result["request"]["state"] == "requested"
    current = result["item"]
    assert current["status"]["code"] == "start_requested"
    assert "nothing has started" in current["status"]["detail"]
    assert current["status"]["label"] == "Start requested"
    # No executor exists in this version: the text says so and the request can be withdrawn.
    assert "not available in this version" in current["status"]["detail"]
    assert current["basis"]["seq"] == 1
    assert ws["status"] != "running"


def test_the_same_request_id_is_a_repeat_not_a_second_request(api):
    ws = make(api)
    first = ask(api, ws, "start", request_id="same")
    body = {"expected_seq": 0, "expected_version": 1, "request_id": "same"}
    status, again = api.request("POST", f"{OVERVIEW}/workspace/{ws['id']}/start", body)
    assert (status, again["repeated"]) == (200, True)
    assert again["request"] == first[1]["request"]
    audit = api.env.store._journal
    with audit() as conn:
        rows = conn.execute("SELECT action FROM audit WHERE object_id=?", (ws["id"],)).fetchall()
    assert [r["action"] for r in rows].count("workspace.start_requested") == 1


def test_a_request_id_reused_for_another_action_is_refused(api):
    ws = make(api)
    ask(api, ws, "start", request_id="one")
    body = {"expected_seq": 1, "expected_version": 1, "request_id": "one"}
    status, result = api.request("POST", f"{OVERVIEW}/workspace/{ws['id']}/withdraw", body)
    assert (status, result) == (
        409,
        {"error": "conflict", "field": "request_id", "reason": "request_reused"},
    )


def test_asking_again_for_the_action_already_waiting_is_a_repeat(api):
    ws = make(api)
    ask(api, ws, "start")
    status, result = api.request(
        "POST",
        f"{OVERVIEW}/workspace/{ws['id']}/start",
        {"expected_seq": 0, "expected_version": 1, "request_id": "other-tab"},
    )
    assert (status, result["repeated"]) == (200, True)


def test_another_action_while_a_request_waits_is_refused_with_the_reason(api):
    ws = make(api)
    ask(api, ws, "start")
    conflict(ask(api, ws, "stop"), "request_pending")
    conflict(ask(api, ws, "restart"), "request_pending")


def test_a_stale_token_is_a_conflict_and_changes_nothing(api):
    ws = make(api)
    stale = {"seq": 0, "version": 1}
    ask(api, ws, "start")
    conflict(ask(api, ws, "withdraw", token=stale), "stale_state")
    assert item(api, ws)["status"]["code"] == "start_requested"
    # A settings edit after the confirmation was read moves the version: the confirm is stale too.
    api.request("POST", f"/workspaces/{ws['id']}", {"expected_revision": 1, "name": "Renamed"})
    conflict(ask(api, ws, "withdraw", token={"seq": 1, "version": 1}), "stale_state")


def test_withdraw_clears_the_request_and_is_audited(api):
    ws = make(api)
    ask(api, ws, "start")
    status, result = ask(api, ws, "withdraw")
    assert status == 200 and result["request"] is None
    assert result["item"]["status"]["code"] == "not_started"
    conflict(ask(api, ws, "withdraw"), "nothing_to_withdraw")
    with api.env.store._journal() as conn:
        actions = [
            r["action"]
            for r in conn.execute("SELECT action FROM audit WHERE object_id=?", (ws["id"],))
        ]
    assert "workspace.request_withdrawn" in actions


def test_actions_are_refused_without_a_machine_with_a_reason(api):
    ws = make(api, node_id=None)
    conflict(ask(api, ws, "start"), "no_machine")
    assert item(api, ws)["status"]["code"] == "no_machine"


@pytest.mark.parametrize("action", ["stop", "restart"])
def test_stop_and_restart_need_a_started_workspace(api, action):
    ws = make(api)
    conflict(ask(api, ws, action), "not_started")


def test_full_path_start_run_stop_restart_with_a_fake_executor(api):
    env = api.env
    executor = FakeExecutor(env)
    ws = make(api)
    ask(api, ws, "start")
    claim = executor.claim()
    assert (
        claim["workspace_id"] == ws["id"] and claim["action"] == "start" and claim["revision"] == 1
    )
    assert item(api, ws)["status"]["code"] == "starting"
    executor.run(claim, "running")
    running = item(api, ws)
    assert running["status"]["code"] == "running" and running["request"] is None
    assert running["status"]["detail"] == "It runs with the saved settings."
    # The older record agrees: ready, with the applied revision.
    assert api.request(path=f"/workspaces/{ws['id']}")[1]["workspace"]["status"] == "ready"
    allowed = {a["action"]: a["allowed"] for a in running["actions"]}
    assert allowed == {"start": False, "stop": True, "restart": True, "withdraw": False}
    conflict(ask(api, ws, "start"), "already_running")

    ask(api, ws, "stop")
    claim = executor.claim()
    assert claim["action"] == "stop"
    assert item(api, ws)["status"]["code"] == "stopping"
    executor.run(claim, "stopped")
    stopped = item(api, ws)
    assert stopped["status"]["code"] == "stopped" and stopped["request"] is None
    conflict(ask(api, ws, "stop"), "already_stopped")
    conflict(ask(api, ws, "restart"), "not_running")

    ask(api, ws, "start")
    claim = executor.claim()
    executor.run(claim, "running")
    ask(api, ws, "restart")
    claim = executor.claim()
    assert item(api, ws)["status"]["code"] == "stopping"
    executor.run(claim, "stopped")
    # Mid-restart it is not "stopped for good": the request is still open.
    assert item(api, ws)["status"]["code"] == "starting"
    assert item(api, ws)["status"]["label"] == "Restarting"
    executor.run(claim, "starting", "running")
    assert item(api, ws)["status"]["code"] == "running"


def test_a_failed_start_is_failed_with_its_reason_and_can_be_retried_or_stopped(api):
    executor = FakeExecutor(api.env)
    ws = make(api)
    ask(api, ws, "start")
    claim = executor.claim()
    executor.report(claim, "failed", reason="image_not_found")
    failed = item(api, ws)
    assert failed["status"]["code"] == "failed"
    assert "No such image was found" in failed["status"]["detail"]
    allowed = {a["action"]: a["allowed"] for a in failed["actions"]}
    assert allowed["start"] and allowed["stop"] and allowed["restart"]
    # A start that failed counts as started: the machine is locked and the record cannot be discarded.
    status, result = api.request(
        "POST", f"/workspaces/{ws['id']}/discard", {"expected_revision": 1}
    )
    assert (status, result["reason"]) == (409, "already_started")
    ask(api, ws, "start")
    assert item(api, ws)["status"]["code"] == "start_requested"


def test_a_starting_workspace_cannot_be_discarded_or_moved(api):
    executor = FakeExecutor(api.env)
    ws = make(api)
    ask(api, ws, "start")
    executor.claim()
    status, result = api.request(
        "POST", f"/workspaces/{ws['id']}/discard", {"expected_revision": 1}
    )
    assert (status, result["reason"]) == (409, "already_started")
    assert api.request(path=f"/workspaces/{ws['id']}")[1]["workspace"]["started"] is True


def test_a_request_that_was_never_claimed_goes_away_with_a_discarded_record(api):
    ws = make(api)
    ask(api, ws, "start")
    status, _ = api.request("POST", f"/workspaces/{ws['id']}/discard", {"expected_revision": 1})
    assert status == 200
    with api.env.store._journal() as conn:
        assert conn.execute("SELECT count(*) FROM cloud_workspace_lifecycle").fetchone()[0] == 0


def test_a_picked_up_request_cannot_be_withdrawn_while_the_machine_reports(api):
    executor = FakeExecutor(api.env)
    ws = make(api)
    ask(api, ws, "start")
    executor.claim()
    conflict(ask(api, ws, "withdraw"), "already_picked_up")
    # When the machine stops reporting the owner can clear it; the status says it is unknown.
    api.env.clock.now += lifecycle.REPORT_STALE_S + 5
    unknown = item(api, ws)
    assert unknown["status"]["code"] == "unknown"
    assert "nothing newer has arrived" in unknown["status"]["detail"]
    status, result = ask(api, ws, "withdraw")
    assert status == 200 and result["request"] is None


def test_executor_reports_are_checked_against_the_state_machine(api):
    env = api.env
    executor = FakeExecutor(env)
    ws = make(api)
    ask(api, ws, "start")
    claim = executor.claim()
    for phase, kwargs in (
        ("stopped", {}),  # starting cannot jump to stopped
        ("running", {}),  # running needs the applied revision
        ("running", {"applied_revision": 99}),  # ... and one that exists
        ("failed", {}),  # failed needs a reason
        ("failed", {"reason": "made_up"}),
        ("starting", {"reason": "start_failed"}),
    ):
        with pytest.raises(StoreError) as caught:
            executor.report(claim, phase, **kwargs)
        assert caught.value.reason == "invalid_report", (phase, kwargs)
    assert item(api, ws)["status"]["code"] == "starting"


def test_a_report_for_a_withdrawn_or_finished_claim_is_refused_and_changes_nothing(api):
    executor = FakeExecutor(api.env)
    ws = make(api)
    ask(api, ws, "start")
    claim = executor.claim()
    executor.run(claim, "running")
    with pytest.raises(StoreError) as caught:
        executor.report(claim, "stopped")
    assert caught.value.reason == "stale_claim"
    assert item(api, ws)["status"]["code"] == "running"


def test_a_heartbeat_refreshes_the_report_time_without_moving_the_phase(api):
    executor = FakeExecutor(api.env)
    ws = make(api)
    ask(api, ws, "start")
    claim = executor.claim()
    seq = item(api, ws)["basis"]["seq"]
    api.env.clock.now += 120
    executor.report(claim, "starting")
    again = item(api, ws)
    assert again["status"]["code"] == "starting" and again["basis"]["seq"] == seq
    assert again["last_seen"]["age_s"] == 0


def test_running_is_never_shown_on_a_stale_report(api):
    executor = FakeExecutor(api.env)
    ws = make(api)
    ask(api, ws, "start")
    executor.run(executor.claim(), "running")
    api.env.clock.now += lifecycle.REPORT_STALE_S - 1
    assert item(api, ws)["status"]["code"] == "running"
    api.env.clock.now += 2
    stale = item(api, ws)
    assert stale["status"]["code"] == "unknown"
    assert "last report said Running" in stale["status"]["detail"]
    assert "nothing newer has arrived" in stale["status"]["detail"]
    assert stale["health"]["state"] == "attention"


def test_a_workspace_the_old_seam_marked_ready_has_no_report_and_is_unknown(api):
    ws = make(api)
    with api.env.store._journal() as conn:
        api.env.store._set_workspace_state(conn, ws["id"], "ready", applied_revision=1)
    current = item(api, ws)
    assert current["status"]["code"] == "unknown"
    assert "Nothing has reported" in current["status"]["detail"]


def test_restart_needed_is_a_flag_beside_running_not_a_second_status(api):
    executor = FakeExecutor(api.env)
    ws = make(api)
    ask(api, ws, "start")
    executor.run(executor.claim(), "running", revision=1)
    api.request("POST", f"/workspaces/{ws['id']}", {"expected_revision": 1, "cpu_cores": 4})
    current = item(api, ws)
    assert current["status"]["code"] == "running" and current["restart_needed"] is True
    assert "restart it to apply them" in current["status"]["detail"]


def test_bad_tokens_and_ids_are_refused_with_the_field_named(api):
    ws = make(api)
    url = f"{OVERVIEW}/workspace/{ws['id']}/start"
    assert api.request(
        "POST", url, {"expected_seq": 0, "expected_version": 1, "request_id": "bad id!"}
    ) == (
        400,
        {"error": "invalid_request", "field": "request_id", "reason": "invalid"},
    )
    assert (
        api.request("POST", url, {"expected_seq": -1, "expected_version": 1})[1]["field"]
        == "expected_seq"
    )
    assert api.request("POST", url, {"expected_seq": "0", "expected_version": 1})[1] == {
        "error": "invalid_request",
        "field": "expected_seq",
        "reason": "invalid",
    }
    assert api.request("POST", url, {"expected_version": 1})[1] == {
        "error": "invalid_request",
        "field": "expected_seq",
        "reason": "required",
    }
    assert (
        api.request("POST", url, {"expected_seq": 0, "expected_version": 1, "state": "running"})[1][
            "reason"
        ]
        == "unknown_field"
    )


def test_the_lifecycle_seam_has_no_rest_path_and_phases_are_server_owned(api):
    ws = make(api)
    for path in (
        f"{OVERVIEW}/workspace/{ws['id']}/running",
        f"{OVERVIEW}/workspace/{ws['id']}/claim",
        f"{OVERVIEW}/workspace/{ws['id']}/report",
    ):
        status, result = api.request(
            "POST", path, {"expected_seq": 0, "expected_version": 1, "phase": "running"}
        )
        assert (status, result["reason"]) == (404, "route")
    assert item(api, ws)["status"]["code"] == "not_started"
    assert workspaces.status_of  # the older vocabulary is untouched


def test_effect_preview_says_what_a_stop_does_to_running_work_before_the_confirm(api):
    executor = FakeExecutor(api.env)
    ws = make(api)
    ask(api, ws, "start")
    executor.run(executor.claim(), "running")
    status, stop = api.request(path=f"{OVERVIEW}/workspace/{ws['id']}/preview/stop")
    assert status == 200 and stop["allowed"] is True and stop["danger"] is True
    assert "cancelled, not finished" in stop["running_work"]
    assert (
        "30 seconds" in stop["running_work"]
        and "Files saved in its storage are kept" in stop["running_work"]
    )
    assert stop["basis"] == item(api, ws)["basis"]
    assert stop["executor"] == {"available": True}
    _, restart = api.request(path=f"{OVERVIEW}/workspace/{ws['id']}/preview/restart")
    assert "cancelled, not finished" in restart["running_work"]
    # A preview is read-only: nothing was requested.
    assert item(api, ws)["request"] is None


def test_preview_without_an_executor_says_nothing_will_start(api):
    ws = make(api)
    _, start = api.request(path=f"{OVERVIEW}/workspace/{ws['id']}/preview/start")
    text = " ".join(start["effect"])
    assert "not available in this version" in text and "Nothing is started" in text
    assert start["executor"] == {"available": False}
    assert start["running_work"].startswith("Nothing is running in it now")


def test_preview_of_a_refused_action_gives_the_reason_and_no_effect_claim(api):
    ws = make(api)
    _, stop = api.request(path=f"{OVERVIEW}/workspace/{ws['id']}/preview/stop")
    assert stop["allowed"] is False
    assert stop["refusal"] == {
        "reason": "not_started",
        "text": "It has not been started yet. Start it first.",
    }


def test_settings_edited_while_a_start_is_under_way_leave_it_running_with_older_settings(api):
    executor = FakeExecutor(api.env)
    ws = make(api)
    ask(api, ws, "start")
    claim = executor.claim()
    assert claim["revision"] == 1
    api.request("POST", f"/workspaces/{ws['id']}", {"expected_revision": 1, "cpu_cores": 4})
    executor.run(claim, "running", revision=claim["revision"])
    current = item(api, ws)
    assert current["status"]["code"] == "running" and current["restart_needed"] is True
    assert "restart it to apply them" in current["status"]["detail"]
    assert api.request(path=f"/workspaces/{ws['id']}")[1]["workspace"]["status"] == "restart_needed"


def test_withdrawing_a_request_a_machine_picked_up_does_not_say_nothing_was_sent(api):
    executor = FakeExecutor(api.env)
    ws = make(api)
    ask(api, ws, "start")
    _, before = api.request(path=f"{OVERVIEW}/workspace/{ws['id']}/preview/withdraw")
    assert "Nothing had been sent to a machine" in before["effect"][0]
    executor.claim()
    api.env.clock.now += lifecycle.REPORT_STALE_S + 5
    _, after = api.request(path=f"{OVERVIEW}/workspace/{ws['id']}/preview/withdraw")
    assert after["allowed"] is True
    text = " ".join(after["effect"])
    assert "Nothing had been sent" not in text
    assert "may still be starting or running" in text


def test_an_unclaimed_request_expires_and_no_executor_can_take_it_later(api):
    env = api.env
    ws = make(api)
    ask(api, ws, "start")
    assert item(api, ws)["status"]["code"] == "start_requested"
    env.clock.now += lifecycle.REQUEST_TTL_S + 60
    # Shown as expired at once (a read writes nothing) and said so, not carried out.
    expired = item(api, ws)
    assert expired["status"]["code"] == "not_started" and expired["request"] is None
    assert "earlier start request expired after 24 hours" in expired["health"]["issues"][0]
    with env.store._journal() as conn:
        assert lifecycle.claim(conn, "late-executor", env.store._now()) is None
        actions = [
            r["action"]
            for r in conn.execute("SELECT action FROM audit WHERE object_id=?", (ws["id"],))
        ]
    assert "workspace.request_expired" in actions and "workspace.request_claimed" not in actions
    # The owner can ask again at once; the new request is a fresh one.
    status, result = ask(api, ws, "start")
    assert status == 202 and item(api, ws)["status"]["code"] == "start_requested"


def test_a_request_inside_its_time_to_live_is_still_claimed(api):
    executor = FakeExecutor(api.env)
    ws = make(api)
    ask(api, ws, "start")
    api.env.clock.now += lifecycle.REQUEST_TTL_S - 60
    assert executor.claim()["workspace_id"] == ws["id"]


def test_asking_for_something_else_after_expiry_is_not_blocked_by_the_dead_request(api):
    ws = make(api)
    ask(api, ws, "start")
    api.env.clock.now += lifecycle.REQUEST_TTL_S + 1
    current = item(api, ws)
    allowed = {a["action"]: a["allowed"] for a in current["actions"]}
    assert allowed["start"] is True and allowed["withdraw"] is False
    conflict(ask(api, ws, "withdraw"), "nothing_to_withdraw")


def test_the_lifecycle_table_is_created_once_inside_its_own_transaction(api):
    with api.env.store._journal() as conn:
        lifecycle.schema(conn)  # a second call finds the table and changes nothing
        assert (
            conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE name='cloud_workspace_lifecycle'"
            ).fetchone()[0]
            == 1
        )
    import sqlite3

    plain = sqlite3.connect(":memory:", isolation_level=None)
    plain.execute("CREATE TABLE cloud_workspaces (id TEXT PRIMARY KEY)")
    lifecycle.schema(plain)
    assert not plain.in_transaction
    lifecycle.schema(plain)
    assert plain.execute("SELECT count(*) FROM cloud_workspace_lifecycle").fetchone()[0] == 0
