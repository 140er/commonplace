"""Compute overview REST: every machine, runner and cloud workspace with honest status, health and usage."""

import json

import pytest

from commonplace import sessions
from commonplace.compute import overview, routes
from commonplace.runner import registry
from tests.compute_overview_helpers import (
    ADDRESS,
    FakeExecutor,
    Fleet,
    after,
    revoke_machine,
    workspace_body,
)
from tests.test_compute_api import API
from tests.test_compute_control import compute_env  # noqa: F401 -- shared real-store fixture
from tests.test_compute_workspaces import pat_headers

BASE = "/overview"


@pytest.fixture
def api(compute_env, monkeypatch):  # noqa: F811
    # Serve every request from the fixture's store so the test clock (``env.clock``) governs the API too.
    monkeypatch.setattr(routes, "open_store", lambda gateway, workspace_id: compute_env.store)
    return API(compute_env)


@pytest.fixture
def fleet(api):
    return Fleet(api.env)


def read(api, **kw):
    status, result = api.request(path=BASE, **kw)
    assert status == 200, result
    return result


def by_name(result):
    return {item["name"]: item for item in result["items"]}


def member(api):
    cookie = sessions.start_session(api.env.db, api.env.member, api.env.workspace)
    return lambda *args, **kw: api.request(*args, cookie=cookie, **kw)


def workspace(api, **changes):
    status, result = api.request("POST", "/workspaces", workspace_body(**changes))
    assert status == 201, result
    return result["workspace"]


# empty and shape


def test_empty_overview_is_honest_about_what_exists_and_what_cannot_happen_yet(api):
    result = read(api)
    assert result["items"] == [] and result["counts"]["total"] == 0
    assert result["can_manage"] is True and result["hidden"] is None
    assert result["refresh_seconds"] == overview.REFRESH_SECONDS == 30
    assert result["thresholds"] == {
        "green_within_s": 180,
        "red_after_s": 900,
        "path_fresh_s": 900,
        "path_unknown_after_s": 3600,
        "report_stale_s": 600,
    }
    assert result["executor"]["available"] is False
    assert "not available in this version" in result["executor"]["text"]
    assert result["truncated"] == {}


def test_the_clock_is_the_servers_and_as_of_is_served(api):
    assert read(api)["as_of"].endswith("Z")


# runners


def test_runner_status_follows_the_last_signal_and_never_says_online_without_one(api, fleet):
    online = fleet.enroll("runner-online")
    slow = fleet.enroll("runner-slow")
    offline = fleet.enroll("runner-offline")
    never = fleet.enroll("runner-never")
    fleet.seen(online, 40)
    fleet.seen(slow, 400)
    fleet.seen(offline, 1500)
    items = by_name(read(api))
    assert items["runner-online"]["status"]["code"] == "online"
    assert items["runner-online"]["status"]["tone"] == "ok"
    assert items["runner-online"]["last_seen"]["label"] == "Last seen 40 seconds ago"
    assert items["runner-slow"]["status"]["code"] == "slow"
    assert items["runner-slow"]["status"]["label"] == "Slow to report"
    assert items["runner-offline"]["status"]["code"] == "offline"
    assert items["runner-offline"]["status"]["tone"] == "bad"
    assert "heard from it for a long time" in items["runner-offline"]["status"]["detail"]
    assert items["runner-never"]["status"]["code"] == "never_connected"
    assert items["runner-never"]["last_seen"] is None
    assert items["runner-never"]["health"]["state"] == "unknown"
    # Offline and never-connected explain themselves and block new work.
    assert any(b["code"] == "offline" for b in items["runner-offline"]["health"]["blockers"])
    assert any(b["code"] == "never_connected" for b in items["runner-never"]["health"]["blockers"])
    assert items["runner-online"]["health"]["blockers"] == []
    assert fleet is not None and never is not None


def test_a_runner_that_goes_quiet_turns_offline_as_the_clock_moves(api, fleet):
    made = fleet.enroll("runner-clock")
    fleet.seen(made, 10)
    assert by_name(read(api))["runner-clock"]["status"]["code"] == "online"
    after(api.env, 200)
    assert by_name(read(api))["runner-clock"]["status"]["code"] == "slow"
    after(api.env, 800)
    stale = by_name(read(api))["runner-clock"]
    assert stale["status"]["code"] == "offline"
    assert stale["last_seen"]["age_s"] >= 1000


def test_disabled_and_revoked_runners_say_so_and_never_look_online(api, fleet):
    off = fleet.enroll("runner-off")
    gone = fleet.enroll("runner-gone")
    fleet.seen(off, 5)
    fleet.seen(gone, 5)
    registry.patch(api.env.db, api.env.principal, off["machine_id"], {"enabled": False})
    revoke_machine(api.env, gone)
    items = by_name(read(api))
    assert items["runner-off"]["status"]["code"] == "disabled"
    assert any(b["code"] == "disabled" for b in items["runner-off"]["health"]["blockers"])
    assert items["runner-gone"]["status"]["code"] == "revoked"
    assert items["runner-gone"]["status"]["tone"] == "bad"
    assert [b["code"] for b in items["runner-gone"]["health"]["blockers"]] == ["revoked"]
    assert items["runner-gone"]["actions"][0]["allowed"] is False


def test_an_unapproved_runner_lists_what_blocks_it(api, fleet):
    made = fleet.enroll("runner-unapproved", attest=False)
    fleet.seen(made, 5)
    blockers = by_name(read(api))["runner-unapproved"]["health"]["blockers"]
    assert [b["code"] for b in blockers] == ["not_approved"]


def test_busy_runner_shows_runs_in_progress_the_age_and_a_full_blocker(api, fleet):
    made = fleet.enroll("runner-busy", max_in_flight=1)
    fleet.seen(made, 20)
    fleet.job(made, "started", minutes_ago=3, task=fleet.work(made))
    fleet.job(made, "queued", task=2)
    busy = by_name(read(api))["runner-busy"]
    assert busy["status"]["code"] == "busy"
    assert busy["status"]["label"] == "Online · Busy"
    assert busy["status"]["detail"] == "1 of 1 runs at once is in progress."
    assert busy["last_seen"]["label"] == "Last seen 20 seconds ago"
    assert busy["usage"]["runs"]["running"] == 1 and busy["usage"]["runs"]["queued"] == 1
    assert [b["code"] for b in busy["health"]["blockers"]] == ["full"]


def test_a_started_run_whose_end_was_never_reported_is_its_own_bucket_not_in_progress(api, fleet):
    made = fleet.enroll("runner-old")
    fleet.seen(made, 20)
    fleet.job(made, "started", minutes_ago=60 * 8, task=1)  # no card is being worked on for it
    runs = by_name(read(api))["runner-old"]["usage"]["runs"]
    assert runs["running"] == 0 and runs["end_unreported"] == 1
    assert "plus 1 run that started and never reported an end" in runs["text"]


def test_a_finished_run_whose_card_is_still_doing_holds_a_slot_and_says_so(api, fleet):
    # The hub's rule, unchanged: a started run holds a slot while its card is Doing, whether or not the machine reported
    # its end (the job row stays "started"; only the ledger knows). The screen says the same and says why.
    made = fleet.enroll("runner-finished", max_in_flight=1)
    fleet.seen(made, 20)
    job = fleet.job(made, "started", minutes_ago=60, task=fleet.work(made))
    fleet.end(job, minutes_ago=50)
    item = by_name(read(api))["runner-finished"]
    assert item["status"]["code"] == "busy"
    assert item["status"]["detail"] == (
        "None is in progress, but the machine is busy: 1 finished run still holds a slot until its card moves on."
    )
    runs = item["usage"]["runs"]
    assert (runs["running"], runs["finished_holding"], runs["end_unreported"]) == (0, 1, 0)
    full = [b for b in item["health"]["blockers"] if b["code"] == "full"]
    assert full and "1 finished run still holds a slot until its card moves on" in full[0]["text"]
    # Once the card moves on the slot is free, and the finished run is not counted anywhere.
    other = fleet.enroll("runner-freed", max_in_flight=1)
    fleet.seen(other, 20)
    fleet.end(
        fleet.job(other, "started", minutes_ago=60, task=7), minutes_ago=50
    )  # no card is being worked on
    freed = by_name(read(api))["runner-freed"]
    assert freed["status"]["code"] == "online" and freed["status"]["detail"] == "Waiting for work."
    assert freed["usage"]["runs"]["running"] == 0 and freed["usage"]["runs"]["end_unreported"] == 0
    assert freed["health"]["blockers"] == []
    # And a run not yet reported ended with its card still Doing is in progress.
    working = fleet.enroll("runner-working", max_in_flight=1)
    fleet.seen(working, 20)
    fleet.job(working, "started", minutes_ago=60, task=fleet.work(working))
    busy = by_name(read(api))["runner-working"]
    assert busy["status"]["code"] == "busy" and busy["usage"]["runs"]["running"] == 1


def test_a_card_that_moved_on_without_an_end_report_is_not_in_progress(api, fleet):
    made = fleet.enroll("runner-moved-on")
    fleet.seen(made, 20)
    fleet.job(
        made, "started", minutes_ago=30, task=7
    )  # a card nobody is working on: its end was never reported
    item = by_name(read(api))["runner-moved-on"]
    assert item["status"]["code"] == "online"
    assert item["usage"]["runs"]["running"] == 0 and item["usage"]["runs"]["end_unreported"] == 1


def test_a_drained_runner_with_an_old_open_run_never_claims_none_is_in_progress(api, fleet):
    made = fleet.enroll("runner-old-drain")
    fleet.seen(made, 20)
    fleet.job(made, "started", minutes_ago=60 * 8, task=1)
    status, _ = api.request(
        "POST", f"{BASE}/runner/{made['machine_id']}/drain", {"expected_seq": 0}
    )
    assert status == 200
    item = by_name(read(api))["runner-old-drain"]
    assert item["status"]["code"] == "drained"
    assert "None is known to be in progress" in item["status"]["detail"]
    assert "started and never reported an end" in item["status"]["detail"]


def test_an_item_beyond_the_cap_is_still_reachable_by_id(api, fleet, monkeypatch):
    monkeypatch.setattr(overview, "MAX_ITEMS", 1)
    runners = {fleet.enroll(name)["machine_id"]: name for name in ("runner-c", "runner-d")}
    _, node = api.enrolled()
    workspaces_made = {workspace(api, name=n, node_id=node["id"])["id"]: n for n in ("Aaa", "Bbb")}
    listed = {(i["kind"], i["id"]) for i in read(api)["items"]}
    assert len([k for k in listed if k[0] == "runner"]) == 1
    assert len([k for k in listed if k[0] == "workspace"]) == 1
    # Every id the capped list left out is still found directly, with its own data.
    hidden_runners = [i for i in runners if ("runner", i) not in listed]
    hidden_workspaces = [i for i in workspaces_made if ("workspace", i) not in listed]
    assert len(hidden_runners) == 1 and len(hidden_workspaces) == 1
    status, result = api.request(path=f"{BASE}/runner/{hidden_runners[0]}")
    assert status == 200 and result["item"]["name"] == runners[hidden_runners[0]]
    status, result = api.request(path=f"{BASE}/workspace/{hidden_workspaces[0]}")
    assert status == 200 and result["item"]["name"] == workspaces_made[hidden_workspaces[0]]
    assert result["item"]["status"]["code"] == "not_started"
    status, result = api.request(path=f"{BASE}/workspace/{hidden_workspaces[0]}/preview/start")
    assert status == 200 and result["allowed"] is True
    status, result = api.request(path=f"{BASE}/runner/{hidden_runners[0]}/preview/drain")
    assert status == 200 and result["allowed"] is True


def test_has_started_reads_a_row_built_without_the_lifecycle_join(api):
    from commonplace.compute import workspaces as ws

    row = {"applied_revision": None, "state": "pending"}
    assert ws.has_started(row) is False
    assert ws.has_started({**row, "lifecycle_phase": "starting"}) is True


def test_uncertain_runs_are_an_issue_that_needs_the_owner(api, fleet):
    made = fleet.enroll("runner-uncertain")
    fleet.seen(made, 20)
    fleet.job(made, "uncertain", task=3)
    item = by_name(read(api))["runner-uncertain"]
    assert item["health"]["state"] == "attention"
    assert "uncertain" in item["health"]["issues"][0] and "owner" in item["health"]["issues"][0]


def test_unknown_usage_is_unknown_not_zero(api, fleet):
    made = fleet.enroll("runner-unknown")
    fleet.seen(made, 10)
    usage = by_name(read(api))["runner-unknown"]["usage"]
    assert usage["resources"] == {
        "known": False,
        "reason": "never_reported",
        "text": "Not reported: this machine has not sent a resource report.",
    }
    assert usage["spend"]["known"] is True and usage["spend"]["totals"] == []
    assert usage["spend"]["text"] == "No runs in the last 30 days."
    assert "0.00" not in json.dumps(usage)


def test_resource_reports_carry_their_age_and_stale_ones_are_unknown(api, fleet):
    made = fleet.enroll("runner-resources")
    fleet.seen(made, 10)
    fleet.resources(made, seconds_ago=120, memory_mb=6144, cpu_free=3.5)
    fresh = by_name(read(api))["runner-resources"]["usage"]["resources"]
    assert fresh["known"] is True and fresh["age_s"] == 120 and fresh["stale"] is False
    assert fresh["text"] == "6.0 GiB memory free, 3.5 CPU cores free, last reported 2 minutes ago"
    after(api.env, 1000)
    stale = by_name(read(api))["runner-resources"]["usage"]["resources"]
    assert stale["known"] is False and stale["reason"] == "stale" and stale["stale"] is True
    assert "stale, treat as unknown" in stale["text"]


def test_spend_is_summed_per_currency_and_unknown_costs_are_counted_not_hidden(api, fleet):
    made = fleet.enroll("runner-spend")
    fleet.seen(made, 10)
    fleet.spend(made, micros=1_500_000, currency="USD", job="a")
    fleet.spend(made, micros=2_000_000, currency="USD", job="b")
    fleet.spend(made, micros=900_000, currency="EUR", job="c")
    spend = by_name(read(api))["runner-spend"]["usage"]["spend"]
    assert {t["currency"]: t["text"] for t in spend["totals"]} == {
        "EUR": "0.90 EUR",
        "USD": "3.50 USD",
    }
    assert spend["known"] is True and spend["unknown_runs"] == 0
    fleet.spend(made, unknown=True, job="d")
    spend = by_name(read(api))["runner-spend"]["usage"]["spend"]
    assert spend["known"] is False and spend["unknown_runs"] == 1
    assert spend["text"].endswith("plus 1 run with unknown cost")


def test_spend_outside_the_window_is_not_counted(api, fleet):
    made = fleet.enroll("runner-window")
    fleet.seen(made, 10)
    fleet.spend(made, job="ancient", minutes_ago=60 * 24 * 40)
    assert by_name(read(api))["runner-window"]["usage"]["spend"]["runs"] == 0


# Target machines


def test_target_machines_are_never_called_online(api):
    _, node = api.enrolled()
    machine = by_name(read(api))[node["name"]]
    assert machine["kind"] == "machine"
    assert machine["status"]["code"] == "not_verified"
    assert "nothing new can start on it" in machine["status"]["detail"]
    assert machine["last_seen"] is None
    assert machine["usage"]["measured"]["known"] is False
    assert machine["usage"]["measured"]["reason"] == "not_collected"
    assert machine["actions"] == []
    assert ADDRESS not in json.dumps(machine)  # no address even for the owner


def test_a_verified_machine_says_it_is_an_enrollment_check_and_expires(api):
    _, node = api.enrolled()
    env = api.env
    with env.store._journal() as conn:
        meta = json.loads(
            conn.execute("SELECT metadata FROM nodes WHERE id=?", (node["id"],)).fetchone()[0]
        )
        meta["state"] = "verified"
        conn.execute("UPDATE nodes SET metadata=? WHERE id=?", (json.dumps(meta), node["id"]))
        conn.execute(
            "INSERT INTO enrollment_verifications(node_id,gateway_id,enrollment,receipt_digest,verified_at,expires_at) "
            "VALUES (?,?,?,?,?,?)",
            (node["id"], "g", "e", "d" * 64, env.clock.now - 60, env.clock.now + 600),
        )
    machine = by_name(read(api))[node["name"]]
    assert machine["status"]["code"] == "verified"
    assert "not a live check" in machine["status"]["detail"]
    after(env, 1200)
    assert by_name(read(api))[node["name"]]["status"]["code"] == "verification_expired"


def test_machine_lists_the_workspaces_placed_on_it(api):
    _, node = api.enrolled()
    workspace(api, name="One", node_id=node["id"])
    workspace(api, name="Two", node_id=node["id"])
    machine = by_name(read(api))[node["name"]]
    assert machine["placement"]["workspaces"] == 2
    assert "2 cloud workspaces placed here" in machine["usage"]["summary"]


# workspaces


def test_workspaces_show_limits_and_say_use_is_not_measured(api):
    _, node = api.enrolled()
    ws = workspace(api, node_id=node["id"], cpu_cores=4, memory_mib=8192, storage_gib=50)
    item = by_name(read(api))[ws["name"]]
    assert item["usage"]["limits"]["text"] == "4 CPU cores, 8 GiB memory, 50 GiB storage (limits)"
    assert item["usage"]["measured"] == {
        "known": False,
        "reason": "not_measured",
        "text": "Not measured yet: Commonplace does not collect CPU, memory or disk use from cloud workspaces.",
    }
    assert item["placement"]["machine_name"] == node["name"]
    assert item["last_seen"] is None and item["status"]["code"] == "not_started"


def test_a_failed_workspace_explains_the_reason_and_what_to_do(api):
    _, node = api.enrolled()
    ws = workspace(api, node_id=node["id"])
    executor = FakeExecutor(api.env)
    api.request(
        "POST",
        f"{BASE}/workspace/{ws['id']}/start",
        {"expected_seq": 0, "expected_version": 1},
    )
    claim = executor.claim()
    executor.report(claim, "failed", reason="out_of_memory")
    item = by_name(read(api))[ws["name"]]
    assert item["status"]["code"] == "failed" and item["status"]["tone"] == "bad"
    assert (
        item["status"]["detail"]
        == "It ran out of memory. Raise the memory limit in Change settings."
    )
    assert item["health"]["state"] == "attention"


def test_counts_sort_and_summarise_every_kind(api, fleet):
    _, node = api.enrolled()
    workspace(api, node_id=node["id"])
    made = fleet.enroll("runner-count")
    fleet.seen(made, 10)
    fleet.enroll("runner-never-count")
    result = read(api)
    assert result["counts"]["by_kind"] == {"machine": 1, "runner": 2, "workspace": 1}
    assert result["counts"]["by_status"]["online"] == 1
    assert [i["kind"] for i in result["items"]] == ["runner", "runner", "machine", "workspace"]


def test_more_than_the_cap_is_counted_not_hidden(api, monkeypatch):
    monkeypatch.setattr(overview, "MAX_ITEMS", 2)
    for index in range(3):
        workspace(api, name=f"W{index}")
    result = read(api)
    assert len([i for i in result["items"] if i["kind"] == "workspace"]) == 2
    assert result["truncated"] == {"workspace": 3}


# detail


def test_detail_has_activity_facts_and_says_logs_are_not_collected(api):
    ws = workspace(api)
    api.request("POST", f"/workspaces/{ws['id']}", {"expected_revision": 1, "name": "Renamed"})
    status, result = api.request(path=f"{BASE}/workspace/{ws['id']}")
    assert status == 200
    texts = [entry["text"] for entry in result["item"]["activity"]]
    assert texts == ["Settings changed by the owner", "Created by the owner"]
    facts = {fact["label"]: fact["value"] for fact in result["item"]["facts"]}
    assert facts["Logs"].startswith("Not collected")
    assert facts["Image"] == "ghcr.io/acme/tools:1.2"


def test_runner_detail_lists_recent_runs_and_limits(api, fleet):
    made = fleet.enroll("runner-detail")
    fleet.seen(made, 10)
    fleet.job(made, "started", task=7)
    status, result = api.request(path=f"{BASE}/runner/{made['machine_id']}")
    assert status == 200
    assert result["item"]["activity"][0]["text"].startswith("Started: demo card 7")
    facts = {f["label"]: f["value"] for f in result["item"]["facts"]}
    assert facts["Allowed to start runs"] == "Yes" and facts["Logs"].startswith("Not collected")


def test_unknown_ids_kinds_and_paths_are_refused_with_a_reason(api):
    assert api.request(path=f"{BASE}/workspace/nothing") == (
        404,
        {"error": "not_found", "reason": "record"},
    )
    assert api.request(path=f"{BASE}/runner/mach_0000000000000000")[1]["reason"] == "record"
    assert api.request(path=f"{BASE}/thing/x")[1] == {"error": "not_found", "reason": "route"}
    assert api.request(path=f"{BASE}/workspace/bad id")[1]["reason"] in ("identifier", "route")
    assert api.request(path=f"{BASE}?kind=runner") == (
        400,
        {"error": "invalid_request", "reason": "query"},
    )
    assert api.request("DELETE", BASE)[0] in (404, 405, 403)


# members and tokens


def test_a_member_sees_cloud_workspaces_only_with_no_machine_names_or_addresses(api, fleet):
    _, node = api.enrolled()
    ws = workspace(api, node_id=node["id"], name="Shared box")
    made = fleet.enroll("secret-runner-label")
    fleet.seen(made, 5)
    fleet.spend(made)
    ask = member(api)
    status, result = ask(path=BASE)
    assert status == 200
    assert result["can_manage"] is False
    assert [i["name"] for i in result["items"]] == ["Shared box"]
    assert result["hidden"] == {
        "kinds": ["machine", "runner"],
        "text": "Machines and runners are visible to the workspace owner.",
    }
    item = result["items"][0]
    assert item["actions"] == [] and item["basis"] is None
    assert item["placement"] == {"assigned": True, "machine_name": None, "machine_id": None}
    text = json.dumps(result)
    for secret in (
        node["name"],
        node["id"],
        ADDRESS,
        "secret-runner-label",
        made["machine_id"],
        "USD",
        "build-runner",
    ):
        assert secret not in text, secret
    # The member can open the workspace detail, with redacted facts, and nothing else.
    status, detail = ask(path=f"{BASE}/workspace/{ws['id']}")
    assert status == 200 and detail["can_manage"] is False
    assert {f["label"]: f["value"] for f in detail["item"]["facts"]}[
        "Machine"
    ] == "A machine is assigned"
    assert node["name"] not in json.dumps(detail)
    for kind, ident in (("runner", made["machine_id"]), ("machine", node["id"])):
        assert ask(path=f"{BASE}/{kind}/{ident}") == (
            403,
            {"error": "owner_required", "reason": "member"},
        )
    assert ask(path=f"{BASE}/workspace/{ws['id']}/preview/start") == (
        403,
        {"error": "owner_required", "reason": "member"},
    )


def test_a_member_cannot_act_and_nothing_changes(api, fleet):
    _, node = api.enrolled()
    ws = workspace(api, node_id=node["id"])
    made = fleet.enroll("runner-guarded")
    ask = member(api)
    for path, body in (
        (f"{BASE}/workspace/{ws['id']}/start", {"expected_seq": 0, "expected_version": 1}),
        (f"{BASE}/workspace/{ws['id']}/withdraw", {"expected_seq": 0, "expected_version": 1}),
        (f"{BASE}/runner/{made['machine_id']}/drain", {"expected_seq": 0}),
        (f"{BASE}/workspace/{ws['id']}/start", {"nonsense": True}),
    ):
        assert ask("POST", path, body) == (403, {"error": "owner_required", "reason": "member"}), (
            path
        )
    assert by_name(read(api))[ws["name"]]["status"]["code"] == "not_started"
    assert by_name(read(api))["runner-guarded"]["draining"] is False


def test_a_personal_access_token_never_reaches_the_overview_even_with_the_owner_cookie(api, fleet):
    ws = workspace(api)
    made = fleet.enroll("runner-pat")
    headers = pat_headers(api)
    for method, path, payload in (
        ("GET", BASE, None),
        ("GET", f"{BASE}/workspace/{ws['id']}", None),
        ("GET", f"{BASE}/workspace/{ws['id']}/preview/start", None),
        ("POST", f"{BASE}/workspace/{ws['id']}/start", {"expected_seq": 0, "expected_version": 1}),
        ("POST", f"{BASE}/runner/{made['machine_id']}/drain", {"expected_seq": 0}),
    ):
        assert api.request(method, path, payload, headers=headers) == (
            403,
            {"error": "owner_required", "reason": "member"},
        ), (method, path)
        status, result = api.request(method, path, payload, headers=headers, cookie=False)
        assert status in (401, 403) and "items" not in result
    assert by_name(read(api))[ws["name"]]["request"] is None


def test_signed_out_and_foreign_callers_get_reasons_and_no_data(api):
    ws = workspace(api)
    assert api.request(path=BASE, cookie=False) == (401, {"error": "sign_in", "reason": "session"})
    foreign = sessions.start_session(api.env.db, api.env.foreign, api.env.other_workspace)
    # Another workspace's person reads their own (empty) overview and cannot reach this one's ids.
    status, result = api.request(path=BASE, cookie=foreign)
    assert status in (200, 403, 404) and "Build box" not in json.dumps(result)
    status, result = api.request(path=f"{BASE}/workspace/{ws['id']}", cookie=foreign)
    assert status in (403, 404) and ws["name"] not in json.dumps(result)


def test_no_secret_shaped_field_is_ever_served(api, fleet):
    _, node = api.enrolled()
    workspace(api, node_id=node["id"])
    made = fleet.enroll("runner-leak-check")
    fleet.seen(made, 5)
    text = json.dumps(read(api)).lower()
    for needle in (
        "token",
        "password",
        "secret",
        "private_key",
        "host_key",
        "credential",
        "lease_token",
    ):
        assert needle not in text, needle
    assert made["machine_token"] not in text and made["worker_token"] not in text


def test_a_workspace_on_an_unverified_machine_lists_that_as_what_stops_it(api):
    _, node = api.enrolled()
    ws = workspace(api, node_id=node["id"])
    blockers = {b["code"] for b in by_name(read(api))[ws["name"]]["health"]["blockers"]}
    assert "machine_not_verified" in blockers
    with api.env.store._journal() as conn:
        meta = json.loads(
            conn.execute("SELECT metadata FROM nodes WHERE id=?", (node["id"],)).fetchone()[0]
        )
        meta["state"] = "verified"
        conn.execute("UPDATE nodes SET metadata=? WHERE id=?", (json.dumps(meta), node["id"]))
    blockers = {b["code"] for b in by_name(read(api))[ws["name"]]["health"]["blockers"]}
    assert "machine_not_verified" not in blockers


def test_the_action_route_checks_the_owner_before_it_looks_at_a_record(api):
    ws = workspace(api)
    ask = member(api)
    for path in (f"{BASE}/workspace/{ws['id']}/start", f"{BASE}/workspace/nothing/start"):
        assert ask("POST", path, {"expected_seq": 0, "expected_version": 1}) == (
            403,
            {"error": "owner_required", "reason": "member"},
        )


# round 1 of the #615 review: copy that matches what the screen can do, honest machine blockers, one bad row


def _all_text(value):
    """Every string in a served answer, to check a phrase appears nowhere."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for inner in value.values():
            yield from _all_text(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _all_text(inner)


def test_an_unverified_or_expired_machine_names_what_it_blocks(api):
    _, node = api.enrolled()
    machine = by_name(read(api))[node["name"]]
    assert [b["code"] for b in machine["health"]["blockers"]] == ["not_verified"]
    assert machine["health"]["blockers"][0]["text"] == (
        "Not verified: cloud workspaces placed here cannot start until it is verified."
    )
    # No control verifies a machine in this version: there is no step, and the sentence says what is true.
    assert machine["status"]["next"] is None
    assert machine["status"]["detail"].endswith(
        "Verification is not available in this version of the app."
    )
    assert "Advanced" not in machine["status"]["detail"]
    assert machine["health"]["state"] == "idle", (
        "nobody can act on it, so it is not an attention state"
    )
    env = api.env
    with env.store._journal() as conn:
        meta = json.loads(
            conn.execute("SELECT metadata FROM nodes WHERE id=?", (node["id"],)).fetchone()[0]
        )
        meta["state"] = "verified"
        conn.execute("UPDATE nodes SET metadata=? WHERE id=?", (json.dumps(meta), node["id"]))
        conn.execute(
            "INSERT INTO enrollment_verifications(node_id,gateway_id,enrollment,receipt_digest,verified_at,expires_at) "
            "VALUES (?,?,?,?,?,?)",
            (node["id"], "g", "e", "d" * 64, env.clock.now - 60, env.clock.now + 600),
        )
    assert by_name(read(api))[node["name"]]["health"]["blockers"] == []
    after(env, 1200)
    expired = by_name(read(api))[node["name"]]
    assert [b["code"] for b in expired["health"]["blockers"]] == ["verification_expired"]
    assert "verified again" in expired["health"]["blockers"][0]["text"]


def test_one_unreadable_machine_row_does_not_fail_the_whole_overview(api, fleet):
    _, node = api.enrolled()
    fleet.enroll("runner-still-listed")
    with api.env.store._journal() as conn:
        conn.execute("UPDATE nodes SET metadata=? WHERE id=?", ("{not json", node["id"]))
    result = read(api)
    items = by_name(result)
    assert items["Machine"]["status"]["code"] == "not_verified"
    assert "runner-still-listed" in items


def test_no_sentence_promises_a_control_the_screen_does_not_have(api, fleet):
    _, node = api.enrolled()
    ws = workspace(api, node_id=node["id"], name="Queued box")
    made = fleet.enroll("runner-copy", max_in_flight=1)
    fleet.seen(made, 5)
    fleet.job(made, "uncertain", task=3)
    api.request(
        "POST", f"{BASE}/workspace/{ws['id']}/start", {"expected_seq": 0, "expected_version": 1}
    )
    api.request("POST", f"{BASE}/runner/{made['machine_id']}/drain", {"expected_seq": 0})
    result = read(api)
    queued = by_name(result)["Queued box"]
    assert queued["status"]["code"] == "start_requested"
    assert queued["status"]["detail"].endswith(
        "It expires after 24 hours if no machine picks it up; there is nothing to do meanwhile."
    )
    texts = list(_all_text(result))
    texts += list(_all_text(api.request(path=f"{BASE}/runner/{made['machine_id']}")[1]))
    for forbidden in (
        "You can withdraw",
        "Resume to use it again",
        "until you resume",
        "wait for the owner to resolve",
        "Ask again",
        "no new runs start",
        "No new runs start",
    ):
        assert not [t for t in texts if forbidden in t], forbidden
    runner_item = by_name(result)["runner-copy"]
    assert runner_item["status"]["detail"].startswith(overview.DRAIN_SCOPE)
    assert overview.DRAIN_SCOPE == (
        "No new card runs start here. Chat and Coworker sessions can still start here."
    )
    assert overview.NO_RESUME in runner_item["status"]["detail"]


def test_an_offline_runner_does_not_say_zero_runs_and_has_one_next_step(api, fleet):
    made = fleet.enroll("runner-quiet")
    fleet.seen(made, 4000)
    item = by_name(read(api))["runner-quiet"]
    assert item["status"]["code"] == "offline"
    assert item["usage"]["runs"]["text"].startswith(
        "0 runs recorded in progress; more may be running while it is offline"
    )
    assert "0 runs in progress" not in item["usage"]["summary"]
    assert item["status"]["next"].startswith("Check that the machine is on")
    slow = fleet.enroll("runner-slowish")
    fleet.seen(slow, 400)
    assert by_name(read(api))["runner-slowish"]["status"]["next"].startswith("Wait a little")


def test_a_removed_runner_is_not_shown_as_seen_now(api, fleet):
    made = fleet.enroll("runner-gone")
    fleet.seen(made, 21)
    revoke_machine(api.env, made)
    item = by_name(read(api))["runner-gone"]
    assert item["status"]["code"] == "revoked"
    assert item["last_seen"]["label"] == "Last contact 21 seconds ago, before it was removed"
    assert (item["last_seen"]["prefix"], item["last_seen"]["suffix"]) == (
        "Last contact",
        ", before it was removed",
    )


def test_a_stale_resource_report_is_called_stale_in_the_row_and_the_offline_limit_is_the_configured_one(
    api, fleet, monkeypatch
):
    made = fleet.enroll("runner-stale-res")
    fleet.seen(made, 4000)
    fleet.resources(made, seconds_ago=1200)
    item = by_name(read(api))["runner-stale-res"]
    assert "Resources last reported 20 minutes ago (stale)" in item["usage"]["summary"]
    monkeypatch.setenv("COMMONPLACE_TOPOLOGY_RED_S", "7200")
    fleet.seen(made, 9000)
    blockers = by_name(read(api))["runner-stale-res"]["health"]["blockers"]
    text = [b["text"] for b in blockers if b["code"] == "offline"]
    assert text and "for over 2 hours" in text[0]


# round 1 of the #615 review, UI vocabulary: the overview and the workspaces list describe one record one way


def test_a_workspace_without_a_machine_is_saved_not_started_and_never_needs_attention(api):
    ws = workspace(api, name="Machine-less", node_id=None)
    result = read(api)
    item = by_name(result)["Machine-less"]
    assert (item["status"]["code"], item["status"]["label"], item["status"]["tone"]) == (
        "not_started",
        "Saved, not started",
        "quiet",
    )
    assert item["health"]["state"] == "idle" and result["counts"]["attention"] == 0
    # No machine exists at all: no instruction that cannot be followed.
    assert item["status"]["detail"] == overview.NO_MACHINES_SENTENCE
    assert [b["text"] for b in item["health"]["blockers"] if b["code"] == "no_machine"] == [
        "No machine is set up yet; starting will need one, which is not available yet."
    ]
    assert "No machine chosen" not in json.dumps(result)
    # A machine exists, none is chosen: the instruction is real now.
    api.enrolled()
    item = by_name(read(api))["Machine-less"]
    assert item["status"]["detail"].endswith("Choose a machine for it with Change settings.")
    assert any(
        b["text"] == "Machine: not chosen yet. Choose one with Change settings."
        for b in item["health"]["blockers"]
    )
    # A member is told who can choose, with no machine named.
    status, shown = member(api)(path=BASE)
    detail = shown["items"][0]["status"]["detail"]
    assert detail.endswith("The workspace owner can choose a machine.") and ws["id"]


def test_a_started_nothing_workspace_with_a_machine_uses_the_same_label(api):
    _, node = api.enrolled()
    workspace(api, name="With machine", node_id=node["id"])
    status = by_name(read(api))["With machine"]["status"]
    assert (status["code"], status["label"]) == ("not_started", "Saved, not started")
    assert status["detail"] == overview.NOT_STARTED_NOTE


def test_an_unreadable_image_is_an_attention_state_and_a_named_one(api):
    ws = workspace(api, name="Odd image")
    with api.env.store._journal() as conn:
        conn.execute(
            "UPDATE cloud_workspaces SET image_reference=? WHERE id=?",
            ("::not an image::", ws["id"]),
        )
    result = read(api)
    item = by_name(result)["Odd image"]
    assert item["status"]["code"] == "image_unreadable" and item["health"]["state"] == "attention"
    assert result["counts"]["attention"] == 1


def test_the_overview_uses_the_words_of_the_workspaces_list():
    from pathlib import Path

    script = (
        Path(overview.__file__).parents[1] / "web" / "static" / "compute-workspaces.js"
    ).read_text()
    for text in (overview.NOT_STARTED_NOTE, overview.NO_MACHINES_SENTENCE, overview.SAVED_LABEL):
        assert text in script, text


# round 2 of the #615 review


def test_the_expired_verification_step_does_not_point_at_a_control_either(api):
    _, node = api.enrolled()
    env = api.env
    with env.store._journal() as conn:
        meta = json.loads(
            conn.execute("SELECT metadata FROM nodes WHERE id=?", (node["id"],)).fetchone()[0]
        )
        meta["state"] = "verified"
        conn.execute("UPDATE nodes SET metadata=? WHERE id=?", (json.dumps(meta), node["id"]))
        conn.execute(
            "INSERT INTO enrollment_verifications(node_id,gateway_id,enrollment,receipt_digest,verified_at,expires_at) "
            "VALUES (?,?,?,?,?,?)",
            (node["id"], "g", "e", "d" * 64, env.clock.now - 60, env.clock.now + 600),
        )
    after(env, 1200)
    status = by_name(read(api))[node["name"]]["status"]
    assert status["code"] == "verification_expired"
    assert status["next"] is None
    assert (
        "Advanced" not in status["detail"] and "not available in this version" in status["detail"]
    )


def test_a_member_is_never_told_to_use_a_control_only_the_owner_has(api):
    _, node = api.enrolled()
    ws = workspace(api, node_id=node["id"], name="Failing box")
    executor = FakeExecutor(api.env)
    api.request(
        "POST", f"{BASE}/workspace/{ws['id']}/start", {"expected_seq": 0, "expected_version": 1}
    )
    claim = executor.claim()
    executor.report(claim, "failed", reason="out_of_memory")
    owner = by_name(read(api))["Failing box"]["status"]["detail"]
    assert "Raise the memory limit in Change settings" in owner
    status, shown = member(api)(path=BASE)
    detail = shown["items"][0]["status"]["detail"]
    assert detail == "It ran out of memory. Ask the workspace owner to raise the memory limit."
    assert "Change settings" not in json.dumps(shown)


def test_a_workspace_the_clock_made_unknown_tells_a_member_to_ask_the_owner(api):
    _, node = api.enrolled()
    ws = workspace(api, node_id=node["id"], name="Quiet box")
    executor = FakeExecutor(api.env)
    api.request(
        "POST", f"{BASE}/workspace/{ws['id']}/start", {"expected_seq": 0, "expected_version": 1}
    )
    executor.run(executor.claim(), "running")
    after(api.env, 1000)
    owner = by_name(read(api))["Quiet box"]["status"]
    assert owner["code"] == "unknown"
    assert owner["next"] == "Nothing to do yet; this clears when the machine reports again."
    _, shown = member(api)(path=BASE)
    assert (
        shown["items"][0]["status"]["next"]
        == "Nothing to do yet; this clears when the machine reports again."
    )


def test_blockers_name_themselves_and_a_drained_runner_is_called_drained(api, fleet):
    offline = fleet.enroll("runner-off")
    fleet.seen(offline, 4000)
    never = fleet.enroll("runner-never")
    drained = fleet.enroll("runner-drained")
    fleet.seen(drained, 10)
    api.request("POST", f"{BASE}/runner/{drained['machine_id']}/drain", {"expected_seq": 0})
    items = by_name(read(api))
    text = {
        name: {b["code"]: b["text"] for b in item["health"]["blockers"]}
        for name, item in items.items()
    }
    assert text["runner-off"]["offline"].startswith("Offline: nothing heard for over 15 minutes")
    assert text["runner-never"]["never_connected"].startswith("Never connected:")
    assert items["runner-drained"]["status"]["label"] == "Drained"
    assert text["runner-drained"]["draining"].startswith(f"Drained: {overview.DRAIN_SCOPE}")
    assert never is not None


def test_a_removed_runner_is_informational_not_something_that_needs_attention(api, fleet):
    made = fleet.enroll("runner-removed")
    fleet.seen(made, 30)
    revoke_machine(api.env, made)
    result = read(api)
    item = by_name(result)["runner-removed"]
    assert item["status"]["code"] == "revoked" and item["health"]["state"] == "idle"
    assert result["counts"]["attention"] == 0
    assert item["health"]["blockers"][0]["text"].startswith("Removed:")


def test_an_expired_request_is_not_carried_out_and_says_there_is_nothing_to_do(api):
    _, node = api.enrolled()
    ws = workspace(api, node_id=node["id"], name="Lapsed box")
    api.request(
        "POST", f"{BASE}/workspace/{ws['id']}/start", {"expected_seq": 0, "expected_version": 1}
    )
    after(api.env, 25 * 3600)
    health = by_name(read(api))["Lapsed box"]["health"]
    assert health["issue_label"] == "Not carried out"
    assert health["issues"][0].endswith(
        "Starting is not available in this version, so there is nothing to do yet."
    )


def test_the_page_says_once_that_starting_is_not_available_and_mentions_no_requests(api):
    result = read(api)
    assert result["executor"]["text"] == (
        "Starting and stopping cloud workspaces on a machine is not available in this version."
    )
    _, node = api.enrolled()
    workspace(api, node_id=node["id"], name="Boxed")
    blockers = by_name(read(api))["Boxed"]["health"]["blockers"]
    assert [b["text"] for b in blockers if b["code"] == "not_available"] == [
        "No machine can carry out a start yet."
    ]


def test_one_unreadable_machine_row_does_not_fail_a_list_that_has_a_workspace_on_it(api):
    _, node = api.enrolled()
    ws = workspace(api, node_id=node["id"], name="On the bad row")
    with api.env.store._journal() as conn:
        conn.execute("UPDATE nodes SET metadata=? WHERE id=?", ("{not json", node["id"]))
    result = read(api)
    row = by_name(result)["On the bad row"]
    assert row["placement"]["machine_name"] is None and row["placement"]["assigned"] is True
    status, listed = api.request(path="/workspaces")
    assert status == 200 and [w["id"] for w in listed["workspaces"]] == [ws["id"]]
    status, detail = api.request(path=f"{BASE}/workspace/{ws['id']}")
    assert status == 200 and detail["item"]["name"] == "On the bad row"


def test_a_locked_runner_registry_is_not_an_empty_runner_list(api, fleet, monkeypatch):
    import sqlite3

    fleet.enroll("runner-registry")
    real = registry.machines

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(registry, "machines", locked)
    assert api.request(path=BASE) == (
        503,
        {"error": "authority_unavailable", "reason": "busy"},
    )

    # A hub where the runner was never installed has no runner machines: that is the one empty answer.
    def absent(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: runner_machines")

    monkeypatch.setattr(registry, "machines", absent)
    assert read(api)["items"] == []
    monkeypatch.setattr(registry, "machines", real)


def test_the_audit_and_the_poll_read_by_index(api):
    with api.env.store._journal() as conn:
        plan = " ".join(
            row[3]
            for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT action,user_id,created_at FROM audit WHERE object_id=? "
                "ORDER BY id DESC LIMIT 10",
                ("x",),
            )
        )
    assert "SEARCH" in plan and "audit_object" in plan and "SCAN" not in plan, plan


def test_a_locked_compute_database_is_busy_not_a_server_error(api):
    import sqlite3

    holder = sqlite3.connect(str(api.env.path), isolation_level=None, timeout=0)
    holder.execute("BEGIN IMMEDIATE")
    try:
        status, result = api.request(path=BASE)
        assert (status, result) == (503, {"error": "authority_unavailable", "reason": "busy"})
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert read(api)["items"] == []


def test_a_machine_with_more_workspaces_than_names_says_and_n_more(api):
    _, node = api.enrolled()
    for n in range(7):
        workspace(api, name=f"Box {n}", node_id=node["id"])
    status, detail = api.request(path=f"{BASE}/machine/{node['id']}")
    assert status == 200
    value = {f["label"]: f["value"] for f in detail["item"]["facts"]}[
        "Cloud workspaces placed here"
    ]
    assert value.endswith(" and 2 more") and value.count(",") == 4


def test_the_verified_machine_has_nothing_known_to_block_and_an_unverified_one_is_not_attention(
    api,
):
    _, node = api.enrolled()
    unverified = by_name(read(api))[node["name"]]
    assert unverified["health"]["state"] == "idle" and unverified["status"]["next"] is None


def test_slow_and_unknown_only_wait_so_they_are_not_attention_states(api, fleet):
    slow = fleet.enroll("runner-slow-2")
    fleet.seen(slow, 400)
    offline = fleet.enroll("runner-offline-2")
    fleet.seen(offline, 4000)
    result = read(api)
    items = by_name(result)
    assert items["runner-slow-2"]["health"]["state"] == "ok"
    assert items["runner-offline-2"]["health"]["state"] == "attention"
    assert items["runner-slow-2"]["usage"]["runs"]["text"].startswith(
        "0 runs recorded in progress; more may be running while it is slow to report"
    )
    assert result["counts"]["attention"] == 1
    # An unknown workspace report waits for the machine to report again: nothing to act on.
    _, node = api.enrolled()
    ws = workspace(api, node_id=node["id"], name="Waiting box")
    executor = FakeExecutor(api.env)
    api.request(
        "POST", f"{BASE}/workspace/{ws['id']}/start", {"expected_seq": 0, "expected_version": 1}
    )
    executor.run(executor.claim(), "running")
    after(api.env, 1000)
    waiting = by_name(read(api))["Waiting box"]
    assert waiting["status"]["code"] == "unknown" and waiting["health"]["state"] == "idle"


def test_a_queued_request_says_there_is_nothing_to_do_and_one_sentence_covers_no_machine(api):
    assert overview.NO_MACHINES_SENTENCE == (
        "No machine is set up yet. Starting will need one, which is not available yet."
    )
    assert "Choose later" not in overview.NO_MACHINES_SENTENCE
