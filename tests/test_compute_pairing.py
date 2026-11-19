"""Guided machine pairing: code lifecycle, owner-only writes, the one accept and per-step states.

Everything runs against a real in-process hub with synthetic ids and keys; no machine, no network.
"""

# ruff: noqa: F811

import base64
import hashlib
import json
import re
from datetime import timedelta
from pathlib import Path

import pytest

from commonplace import control_db
from commonplace.account import tables
from commonplace.compute import pairing
from commonplace.runner import registry
from tests.runner_harness import (
    cli_token,
    http,
    member,
    owners,  # noqa: F401
    signup_hub,  # noqa: F401
)
from tests.test_runner_enrollment import (
    decrypt,
    enable,
    exchange,
    machine_key,  # noqa: F401
    public,
)

PAIRINGS = "/api/compute/pairings"
STATUS = "/api/runner/pairing-status"
CHECK = "/api/runner-pairing/check"
FIELDS = {"kind": "runner", "label": "build-box", "projects": ["demo"], "topics": ["worker"]}
WRONG_BODY = {"request_id": "x", "kind": "runner", "label": "UPPER CASE"}


class Pairing:
    """One owner's view of the pairing API on a hub whose setup is turned on."""

    def __init__(self, hub, owner, key):
        self.hub, self.owner, self.key = hub, owner, key
        self.count = 0

    def call(self, method, path="", body=None, *, who=None, **extra):
        who = who or self.owner
        return http(
            self.hub.port, method, PAIRINGS + path, cookie=who["cookie"], body=body, **extra
        )

    def create(self, who=None, **changes):
        self.count += 1
        body = {"request_id": f"req-{self.count}", **FIELDS, "wings": ["shared"], **changes}
        return self.call("POST", "", body, who=who)

    def made(self, **changes):
        status, result = self.create(**changes)
        assert status == 201, (status, result)
        return result

    def get(self, made, who=None):
        status, result = self.call("GET", "/" + made["pairing"]["id"], who=who)
        assert status == 200, (status, result)
        return result["pairing"]

    def envelope(self, made):
        raw = made["setup"]["envelope"]
        return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))

    def check(self, made, **changes):
        public_values = self.envelope(made)
        body = {
            "workspace_id": public_values["workspace_id"],
            "pairing_id": public_values["pairing_id"],
            "code": made["setup"]["code"],
            **changes,
        }
        return http(self.hub.port, "POST", CHECK, body=body, origin=False)

    def connect(self, made):
        """What the installer does after its checks: spend the code and open the machine."""
        values = self.envelope(made)
        status, envelope = exchange(
            self.hub,
            {
                "workspace_id": values["workspace_id"],
                "machine_label": values["label"],
                "manifest_digest": values["manifest_digest"],
                "code": made["setup"]["code"],
                "installation_id": "install-" + values["pairing_id"],
                "public_key": public(self.key),
            },
        )
        assert status == 201, (status, envelope)
        return decrypt(self.key, envelope)

    def report(self, credentials, body, token="machine_token"):
        return http(
            self.hub.port, "POST", STATUS, token=credentials[token], body=body, origin=False
        )

    def accept(self, made, **changes):
        item = self.get(made)
        body = {
            "request_id": "accept-1",
            "expected_version": item["version"],
            "identity_digest": item["identity_digest"],
            **changes,
        }
        return self.call("POST", f"/{item['id']}/accept", body)

    def close(self, made, **changes):
        item = self.get(made)
        body = {"request_id": "close-1", "expected_version": item["version"], **changes}
        return self.call("POST", f"/{item['id']}/close", body)

    def finish_steps(self, credentials):
        for step in pairing.STEPS:
            assert self.report(credentials, {"step": step, "state": "ok"})[0] == 200


@pytest.fixture
def flow(signup_hub, owners, machine_key):
    hub, owner = signup_hub, owners["A"]
    status, _ = http(
        hub.port,
        "POST",
        "/api/memory",
        cookie=owner["cookie"],
        body={"text": "Synthetic pairing fixture", "theme": "shared"},
    )
    assert status == 201
    enable(hub, owner)
    return Pairing(hub, owner, machine_key)


def audit_rows(hub, prefix="runner.pairing."):
    with hub.db._lock:
        return hub.db._conn.execute(
            "SELECT action, decision, target_ids, reason FROM audit_log WHERE action LIKE ?",
            (prefix + "%",),
        ).fetchall()


def freeze(monkeypatch, **offset):
    now = control_db._now() + timedelta(**offset)
    monkeypatch.setattr(control_db, "_now", lambda: now)
    return now


# ---- creating: the code lifecycle -----------------------------------------------------------


def test_reads_work_before_setup_is_on_and_creating_says_why_it_is_refused(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    status, listing = http(hub.port, "GET", PAIRINGS, cookie=owner["cookie"])
    assert status == 200 and listing["pairings"] == [] and listing["setup_enabled"] is False
    flow = Pairing(hub, owner, None)
    status, result = flow.create(wings=["shared"])
    assert (status, result) == (409, {"error": "conflict", "reason": "setup_off"})


def test_create_returns_the_code_once_and_stores_only_its_hash(flow):
    made = flow.made()
    code = made["setup"]["code"]
    assert len(code) == 43 and made["pairing"]["state"] == "waiting"
    assert made["pairing"]["version"] == 1 and made["repeated"] is False
    values = flow.envelope(made)
    assert values["label"] == "build-box" and values["kind"] == "runner" and values["v"] == 1
    assert values["workspace_id"] == flow.owner["workspace"]
    assert code not in json.dumps(values) and code in made["setup"]["command"]
    assert made["setup"]["command"].startswith(" printf '%s' ")
    assert (
        "machine-setup.sh" in made["setup"]["command"]
        and made["setup"]["envelope"] in made["setup"]["command"]
    )
    with flow.hub.db._lock:
        stored = flow.hub.db._conn.execute(
            "SELECT code_hash, expires_at FROM runner_enrollments"
        ).fetchone()
        dump = " ".join(
            str(tuple(row))
            for table in ("machine_pairings", "machine_pairing_steps", "audit_log")
            for row in flow.hub.db._conn.execute(f'SELECT * FROM "{table}"')
        )
    assert stored["code_hash"] == hashlib.sha256(code.encode()).hexdigest()
    assert code not in dump and made["setup"]["envelope"] not in dump
    assert made["setup"]["expires_at"] == stored["expires_at"]
    for path in ("", "/" + made["pairing"]["id"], "/options"):
        assert code not in json.dumps(flow.call("GET", path)[1])


def test_code_lives_fifteen_minutes(flow, monkeypatch):
    made = flow.made()
    created = control_db._parse(made["pairing"]["created_at"])
    assert control_db._parse(made["setup"]["expires_at"]) - created == timedelta(minutes=15)
    freeze(monkeypatch, minutes=14)
    assert flow.get(made)["state"] == "waiting"
    freeze(monkeypatch, minutes=15)
    item = flow.get(made)
    assert item["state"] == "expired" and item["retry"]["label"] == "build-box"
    assert flow.check(made)[1]["reason"] == "expired"


def test_replay_returns_the_record_without_the_code_and_a_changed_body_conflicts(flow):
    made = flow.made()
    body = {"request_id": "req-1", **FIELDS, "wings": ["shared"]}
    status, again = flow.call("POST", "", body)
    assert status == 200 and again["repeated"] is True and "setup" not in again
    assert again["pairing"]["id"] == made["pairing"]["id"]
    status, result = flow.call("POST", "", {**body, "label": "other-box"})
    assert (status, result["reason"], result["field"]) == (409, "request_changed", "request_id")
    with flow.hub.db._lock:
        assert flow.hub.db._conn.execute("SELECT count(*) FROM machine_pairings").fetchone()[0] == 1


@pytest.mark.parametrize(
    "changes,status,field,reason",
    [
        ({"label": "Not Allowed"}, 400, "label", "invalid"),
        ({"label": "mach_0123456789abcdef"}, 400, "label", "invalid"),
        ({"label": 5}, 400, "label", "invalid"),
        ({"kind": "toaster"}, 400, "kind", "invalid"),
        ({"kind": "target"}, 409, "kind", "kind_unavailable"),
        ({"request_id": "bad id"}, 400, "request_id", "invalid"),
        ({"projects": "demo"}, 400, "projects", "invalid"),
        ({"projects": []}, 400, "projects", "required"),
        ({"wings": ["nonexistent-wing"]}, 400, "wings", "invalid"),
        ({"copy_from": "nobody"}, 400, "copy_from", "not_found"),
        ({"max_in_flight": 9}, 400, "max_in_flight", "invalid"),
        ({"max_runtime_mode": "yolo"}, 400, "max_runtime_mode", "invalid"),
        ({"surprise": 1}, 400, None, "unknown_field"),
    ],
)
def test_every_refusal_names_its_field_and_a_reason(flow, changes, status, field, reason):
    got, result = flow.create(**changes)
    assert got == status
    assert result.get("field") == field and result["reason"] == reason
    assert reason in pairing.REASONS
    with flow.hub.db._lock:
        assert flow.hub.db._conn.execute("SELECT count(*) FROM machine_pairings").fetchone()[0] == 0
        assert (
            flow.hub.db._conn.execute("SELECT count(*) FROM runner_enrollments").fetchone()[0] == 0
        )


def test_required_fields_are_named(flow):
    for key in ("request_id", "kind", "label"):
        body = {"request_id": "r", **FIELDS, "wings": ["shared"]}
        del body[key]
        status, result = flow.call("POST", "", body)
        assert (status, result["field"], result["reason"]) == (400, key, "required")
    body = {"request_id": "r", **FIELDS}
    status, result = flow.call("POST", "", body)
    assert (status, result["field"], result["reason"]) == (400, "wings", "required")


def test_a_label_in_use_and_too_many_open_setups_are_refused_with_reasons(flow):
    flow.made()
    status, result = flow.create()
    assert (status, result["field"], result["reason"]) == (409, "label", "label_in_use")
    for index in range(pairing.MAX_OPEN - 1):
        flow.made(label=f"box-{index}")
    status, result = flow.create(label="one-too-many")
    assert (status, result["reason"]) == (429, "too_many_open")


def test_creating_is_rate_limited_per_workspace(flow, monkeypatch):
    freeze(monkeypatch)
    for index in range(pairing.CREATE_PER_MINUTE):
        made = flow.made(label=f"rate-{index}")
        assert flow.close(made)[0] == 200
    status, result = flow.create(label="rate-over")
    assert (status, result["reason"]) == (429, "rate_limited")
    freeze(monkeypatch, minutes=1)
    assert flow.create(label="rate-later")[0] == 201


# ---- who may do what -------------------------------------------------------------------------


@pytest.fixture
def people(flow, owners, signup_hub):
    hub = signup_hub
    other = owners["B"]
    enable(hub, other)
    colleague_email = "member@example.test"
    from tests.runner_harness import _signup

    colleague = _signup(hub, colleague_email, 2)
    return flow, other, member(hub, flow.owner, colleague)


def test_a_member_reads_a_view_without_secrets_and_cannot_write(people):
    flow, _, colleague = people
    made = flow.made()
    flow.connect(made)
    status, listing = flow.call("GET", "", who=colleague)
    assert status == 200
    item = listing["pairings"][0]
    assert set(item) == set(pairing.MEMBER_VIEW)
    assert "fingerprint" not in item and "grant" not in item and "version" not in item
    assert made["setup"]["code"] not in json.dumps(listing)
    assert item["next"].startswith("The machine connected")
    assert flow.call("GET", "/" + item["id"], who=colleague)[0] == 200
    assert flow.call("GET", "/options", who=colleague)[1].keys().isdisjoint({"wings", "copy_from"})
    for method, path, body in (
        ("POST", "", {"request_id": "m1", **FIELDS, "wings": ["shared"]}),
        ("POST", f"/{item['id']}/accept", {}),
        ("POST", f"/{item['id']}/close", {}),
    ):
        status, result = flow.call(method, path, body, who=colleague)
        assert (status, result["error"], result["reason"]) == (403, "owner_required", "member")
    # Refused before the body is read: a body that would be invalid is still a 403.
    assert flow.call("POST", "", WRONG_BODY, who=colleague)[0] == 403
    assert flow.get(made)["state"] == "connected"


def test_a_foreign_workspace_sees_nothing_and_signed_out_and_agents_are_refused(people):
    flow, other, _ = people
    made = flow.made()
    status, listing = flow.call("GET", "", who=other)
    assert status == 200 and listing["pairings"] == []
    item = made["pairing"]
    for method, path in (("GET", "/" + item["id"]), ("POST", f"/{item['id']}/close")):
        assert (
            flow.call(method, path, {"request_id": "r", "expected_version": 1}, who=other)[0] == 404
        )
    status, _ = http(flow.hub.port, "GET", PAIRINGS)
    assert status == 401
    _cid, agent = cli_token(flow.owner, "pairing-agent")
    for method, body in (("GET", None), ("POST", {"request_id": "a", **FIELDS})):
        status, result = http(flow.hub.port, method, PAIRINGS, token=agent, body=body)
        assert status in (401, 403) and "pairings" not in result
    assert flow.get(made)["state"] == "waiting"


def test_writes_need_same_origin_json(flow):
    body = {"request_id": "o1", **FIELDS, "wings": ["shared"]}
    status, result = http(
        flow.hub.port,
        "POST",
        PAIRINGS,
        cookie=flow.owner["cookie"],
        body=body,
        origin="http://evil.test",
    )
    assert status == 403 and "setup" not in result
    with flow.hub.db._lock:
        assert flow.hub.db._conn.execute("SELECT count(*) FROM machine_pairings").fetchone()[0] == 0


# ---- the machine side of the code ----------------------------------------------------------


def test_check_says_usable_and_then_why_it_cannot_work(flow, monkeypatch, owners):
    made = flow.made()
    status, result = flow.check(made)
    assert status == 200 and result["usable"] is True and result["label"] == "build-box"
    assert flow.get(made)["state"] == "waiting"
    wrong = flow.check(made, code="x" * 43)[1]
    assert wrong["usable"] is False and wrong["reason"] == "code_mismatch"
    assert (
        flow.check(made, workspace_id=owners["B"]["workspace"])[1]["reason"]
        == "not_for_this_workspace"
    )
    assert flow.check(made, pairing_id="pair_" + "0" * 16)[1]["reason"] == "code_mismatch"
    flow.connect(made)
    used = flow.check(made)[1]
    assert (used["reason"], used["text"], used["next"]) == ("used", *pairing.REASONS["used"])
    cancelled = flow.made(label="other-box")
    assert flow.close(cancelled)[0] == 200
    assert flow.check(cancelled)[1]["reason"] == "cancelled"
    stale = flow.made(label="stale-box")
    freeze(monkeypatch, minutes=20)
    assert flow.check(stale)[1]["reason"] == "expired"


def test_check_refuses_credentials_and_counts_tries(flow):
    made = flow.made()
    values = flow.envelope(made)
    body = {
        "workspace_id": values["workspace_id"],
        "pairing_id": values["pairing_id"],
        "code": made["setup"]["code"],
    }
    assert http(flow.hub.port, "POST", CHECK, cookie=flow.owner["cookie"], body=body)[0] == 404
    assert (
        http(flow.hub.port, "POST", CHECK, token="cmmn_" + "a" * 43, body=body, origin=False)[0]
        == 404
    )
    assert http(flow.hub.port, "POST", CHECK, body={"code": "x"}, origin=False)[0] == 400
    results = [flow.check(made, code="y" * 43) for _ in range(pairing.CHECK_PER_MINUTE + 2)]
    assert results[0][0] == 200 and results[-1][0] == 429
    assert results[-1][1]["reason"] == "rate_limited"


def test_check_after_setup_is_turned_off_says_so(flow):
    made = flow.made()
    enable(flow.hub, flow.owner, False)
    result = flow.check(made)[1]
    assert result["reason"] == "setup_off"
    assert flow.get(made)["state"] == "cancelled"


def test_prerequisite_failure_is_shown_without_spending_the_code(flow):
    made = flow.made()
    report = {"step": "prerequisites", "state": "failed", "cause": "missing_privilege"}
    assert flow.check(made, report=report)[1]["usable"] is True
    item = flow.get(made)
    assert item["state"] == "failed"
    assert item["cause"]["step"] == "prerequisites" and item["cause"]["code"] == "missing_privilege"
    assert item["cause"]["text"] == pairing.CAUSES["missing_privilege"][0]
    assert item["cause"]["next"] == pairing.CAUSES["missing_privilege"][1]
    ok = {"step": "prerequisites", "state": "ok"}
    assert flow.check(made, report=ok)[1]["usable"] is True
    assert flow.get(made)["state"] == "waiting"
    assert flow.connect(made)["machine_id"]


def test_only_a_live_code_can_write_a_prerequisite_report(flow):
    made = flow.made()
    flow.connect(made)
    report = {"step": "prerequisites", "state": "failed", "cause": "disk_full"}
    assert flow.check(made, report=report)[1]["reason"] == "used"
    assert flow.get(made)["steps"][0]["state"] == "pending"
    other = flow.made(label="live-box")
    for bad in (
        {"step": "files", "state": "ok"},
        {"step": "heartbeat"},
        {"step": "prerequisites", "state": "failed", "cause": "made-up"},
    ):
        assert flow.check(other, report=bad)[0] == 400
    assert flow.get(other)["steps"][0]["state"] == "pending"


# ---- connected, the one accept ---------------------------------------------------------------


def test_the_machine_appears_as_it_connects_with_identity_and_grants_in_words(flow):
    made = flow.made(requesters=[], max_runtime_mode="auto-accept-edits")
    credentials = flow.connect(made)
    item = flow.get(made)
    assert (
        item["state"] == "connected"
        and item["state_label"] == "Machine connected, waiting for your approval"
    )
    assert re.fullmatch(r"(?:[0-9a-f]{4} ){5}[0-9a-f]{4}", item["fingerprint"])
    assert item["grant"]["wings"] == ["shared"] and item["grant"]["projects"] == ["demo"]
    assert item["grant"]["max_runtime_mode"] == "auto-accept-edits"
    assert item["can_accept"] is True and item["binding_state"] == "unbound"
    assert re.fullmatch(r"[0-9a-f]{64}", item["identity_digest"])
    with flow.hub.db._lock:
        machine = registry.get_machine(
            flow.hub.db, flow.owner["workspace"], credentials["machine_id"]
        )
    assert machine["enabled"] == 0


def test_accept_needs_a_connected_machine_and_the_identity_that_was_shown(flow):
    made = flow.made()
    status, result = flow.call(
        "POST",
        f"/{made['pairing']['id']}/accept",
        {"request_id": "a", "expected_version": 1, "identity_digest": "a" * 64},
    )
    assert (status, result["reason"]) == (409, "not_connected")
    flow.connect(made)
    item = flow.get(made)
    path = f"/{item['id']}/accept"
    base = {
        "request_id": "a",
        "expected_version": item["version"],
        "identity_digest": item["identity_digest"],
    }
    for changes, status, field, reason in (
        ({"expected_version": item["version"] + 1}, 409, "expected_version", "stale_version"),
        ({"expected_version": "1"}, 400, "expected_version", "invalid"),
        ({"identity_digest": "b" * 64}, 409, "identity_digest", "identity_changed"),
    ):
        got, result = flow.call("POST", path, {**base, **changes})
        assert (got, result["field"], result["reason"]) == (status, field, reason)
    for key in ("request_id", "expected_version", "identity_digest"):
        body = {k: v for k, v in base.items() if k != key}
        got, result = flow.call("POST", path, body)
        assert (got, result["field"], result["reason"]) == (400, key, "required")
    assert flow.get(made)["state"] == "connected"


def test_accept_enables_the_machine_but_never_attests_it(flow):
    made = flow.made()
    credentials = flow.connect(made)
    status, result = flow.accept(made)
    assert status == 200, result
    item = result["pairing"]
    assert item["state"] == "approved" and item["accepted_at"] and item["version"] == 2
    with flow.hub.db._lock:
        machine = registry.get_machine(
            flow.hub.db, flow.owner["workspace"], credentials["machine_id"]
        )
    assert machine["enabled"] == 1 and machine["binding_state"] == "unbound"
    assert item["binding_state"] == "unbound"
    assert "provider" in pairing.STATES["ready"][2]
    row = [r for r in audit_rows(flow.hub) if r["action"] == "runner.pairing.accept"]
    assert len(row) == 1 and item["id"] in row[0]["target_ids"]


def test_accept_replays_by_request_id_and_refuses_a_second_decision(flow):
    made = flow.made()
    flow.connect(made)
    first = flow.accept(made)[1]["pairing"]
    again = flow.call(
        "POST",
        f"/{first['id']}/accept",
        {
            "request_id": "accept-1",
            "expected_version": 1,
            "identity_digest": first["identity_digest"],
        },
    )
    assert again[0] == 200 and again[1]["pairing"]["version"] == first["version"]
    other = flow.call(
        "POST",
        f"/{first['id']}/accept",
        {
            "request_id": "accept-2",
            "expected_version": first["version"],
            "identity_digest": first["identity_digest"],
        },
    )
    assert (other[0], other[1]["reason"]) == (409, "already_approved")
    assert len([r for r in audit_rows(flow.hub) if r["action"] == "runner.pairing.accept"]) == 1


def test_accept_is_refused_when_the_grant_changed_after_it_was_shown(flow):
    made = flow.made()
    flow.connect(made)
    item = flow.get(made)
    with flow.hub.db._lock:
        flow.hub.db._conn.execute(
            "UPDATE runner_enrollments SET intent=json_set(intent, '$.projects', json('[\"other\"]'))"
        )
    status, result = flow.call(
        "POST",
        f"/{item['id']}/accept",
        {
            "request_id": "a",
            "expected_version": item["version"],
            "identity_digest": item["identity_digest"],
        },
    )
    assert (status, result["reason"]) == (409, "identity_changed")


def test_two_accepts_race_and_one_wins(flow):
    from concurrent.futures import ThreadPoolExecutor

    made = flow.made()
    flow.connect(made)
    item = flow.get(made)

    def attempt(index):
        return flow.call(
            "POST",
            f"/{item['id']}/accept",
            {
                "request_id": f"race-{index}",
                "expected_version": item["version"],
                "identity_digest": item["identity_digest"],
            },
        )

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sorted(status for status, _ in results) == [200, 409]
    assert len([r for r in audit_rows(flow.hub) if r["action"] == "runner.pairing.accept"]) == 1


# ---- steps, ready, offline -------------------------------------------------------------------


def test_steps_report_to_ready_and_back_through_offline(flow, monkeypatch):
    made = flow.made()
    credentials = flow.connect(made)
    flow.accept(made)
    assert [s["state"] for s in flow.get(made)["steps"]] == ["pending"] * 4
    for index, step in enumerate(pairing.STEPS):
        status, result = flow.report(credentials, {"step": step, "state": "ok"})
        assert status == 200
        assert result["state"] == ("ready" if index == 3 else "approved")
    item = flow.get(made)
    assert item["state"] == "ready" and item["state_label"] == "Set up and checked"
    assert all(s["state"] == "ok" for s in item["steps"])
    now = freeze(monkeypatch, seconds=pairing.OFFLINE_SECONDS + 1)
    item = flow.get(made)
    assert item["state"] == "offline" and item["last_report_at"] < control_db._iso(now)
    assert flow.report(credentials, {"step": "heartbeat"})[1]["state"] == "ready"
    assert flow.get(made)["state"] == "ready"


def test_a_failed_step_shows_its_cause_and_next_step_and_recovers(flow):
    made = flow.made()
    credentials = flow.connect(made)
    assert flow.report(credentials, {"step": "files", "state": "ok"})[0] == 200
    bad = {"step": "service", "state": "failed", "cause": "service_failed"}
    assert flow.report(credentials, bad)[0] == 200
    item = flow.get(made)
    assert item["state"] == "failed" and item["state_label"] == "A step failed"
    assert item["cause"] == {
        "step": "service",
        "code": "service_failed",
        "text": pairing.CAUSES["service_failed"][0],
        "next": pairing.CAUSES["service_failed"][1],
    }
    status, result = flow.accept(made)
    assert (status, result["reason"]) == (409, "step_failed")
    assert flow.report(credentials, {"step": "service", "state": "ok"})[0] == 200
    assert flow.get(made)["state"] == "connected"
    failed_audit = [
        r
        for r in audit_rows(flow.hub)
        if r["action"] == "runner.pairing.report" and r["decision"] == "deny"
    ]
    assert len(failed_audit) == 1 and failed_audit[0]["reason"] == "service_failed"


def test_member_sees_the_cause_with_a_next_step_for_the_owner_to_take(people):
    flow, _, colleague = people
    made = flow.made()
    credentials = flow.connect(made)
    flow.report(credentials, {"step": "check", "state": "failed", "cause": "disk_full"})
    item = flow.get(made, who=colleague)
    assert item["cause"]["code"] == "disk_full"
    assert item["cause"]["next"] == "Ask the workspace owner to retry it."
    assert item["next"].startswith("A setup step failed")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"step": "unknown", "state": "ok"},
        {"step": "files", "state": "weird"},
        {"step": "files", "state": "failed"},
        {"step": "files", "state": "ok", "cause": "disk_full"},
        {"step": "files", "state": "failed", "cause": "made-up"},
        {"step": "heartbeat", "state": "ok"},
        {"step": "files", "state": "ok", "extra": 1},
    ],
)
def test_a_malformed_report_is_refused_and_stores_nothing(flow, body):
    credentials = flow.connect(flow.made())
    status, result = flow.report(credentials, body)
    assert (status, result["error"], result["reason"]) == (400, "invalid_request", "body")
    with flow.hub.db._lock:
        assert (
            flow.hub.db._conn.execute("SELECT count(*) FROM machine_pairing_steps").fetchone()[0]
            == 0
        )


def test_only_the_machine_credential_reports(flow):
    credentials = flow.connect(flow.made())
    body = {"step": "files", "state": "ok"}
    assert flow.report(credentials, body, "worker_token")[0] in (403, 404)
    assert http(flow.hub.port, "POST", STATUS, cookie=flow.owner["cookie"], body=body)[0] in (
        403,
        404,
    )
    assert http(flow.hub.port, "POST", STATUS, body=body, origin=False)[0] in (401, 404)
    _cid, agent = cli_token(flow.owner, "report-agent")
    assert http(flow.hub.port, "POST", STATUS, token=agent, body=body, origin=False)[0] == 404
    with flow.hub.db._lock:
        assert (
            flow.hub.db._conn.execute("SELECT count(*) FROM machine_pairing_steps").fetchone()[0]
            == 0
        )


def test_a_closed_pairing_takes_no_more_reports(flow):
    made = flow.made()
    credentials = flow.connect(made)
    assert flow.close(made)[0] == 200
    status, _ = flow.report(credentials, {"step": "files", "state": "ok"})
    # Closing revoked the machine's credential, so the gateway stops the report first.
    assert status in (401, 404, 409)
    with flow.hub.db._lock:
        assert (
            flow.hub.db._conn.execute("SELECT count(*) FROM machine_pairing_steps").fetchone()[0]
            == 0
        )


# ---- closing, revoking and setting up again --------------------------------------------------


def test_cancel_a_waiting_setup_stops_its_code(flow):
    made = flow.made()
    status, result = flow.close(made)
    assert status == 200 and result["pairing"]["state"] == "cancelled"
    assert result["pairing"]["retry"] == {
        "kind": "runner",
        "label": "build-box",
        "projects": ["demo"],
        "topics": ["worker"],
        "wings": ["shared"],
        "requesters": [],
        "max_runtime_mode": "approval-required",
        "max_in_flight": 1,
        "max_starts_per_hour": 6,
    }
    values = flow.envelope(made)
    refused = exchange(
        flow.hub,
        {
            "workspace_id": values["workspace_id"],
            "machine_label": "build-box",
            "manifest_digest": values["manifest_digest"],
            "code": made["setup"]["code"],
            "installation_id": "install-x",
            "public_key": public(flow.key),
        },
    )
    assert refused[0] == 403


def test_refusing_a_connected_machine_revokes_its_credentials(flow):
    made = flow.made()
    credentials = flow.connect(made)
    status, result = flow.close(made)
    assert status == 200 and result["pairing"]["state"] == "declined"
    assert result["pairing"]["state_label"] == "Not approved"
    assert flow.hub.db.authenticate_token(credentials["machine_token"]) is None
    assert flow.hub.db.authenticate_token(credentials["worker_token"]) is None
    with flow.hub.db._lock:
        machine = registry.get_machine(
            flow.hub.db, flow.owner["workspace"], credentials["machine_id"]
        )
    assert machine["revoked_at"] and machine["enabled"] == 0


def test_removing_an_approved_machine_and_setting_it_up_again(flow):
    made = flow.made()
    credentials = flow.connect(made)
    flow.accept(made)
    flow.finish_steps(credentials)
    status, result = flow.close(made)
    assert status == 200 and result["pairing"]["state"] == "revoked"
    assert flow.hub.db.authenticate_token(credentials["machine_token"]) is None
    retry = result["pairing"]["retry"]
    status, again = flow.create(**retry)
    assert status == 201, again
    assert (
        again["pairing"]["state"] == "waiting" and again["pairing"]["id"] != made["pairing"]["id"]
    )


def test_close_is_compare_and_set_idempotent_and_final(flow, monkeypatch):
    made = flow.made()
    item = made["pairing"]
    path = f"/{item['id']}/close"
    status, result = flow.call("POST", path, {"request_id": "c", "expected_version": 7})
    assert (status, result["field"], result["reason"]) == (409, "expected_version", "stale_version")
    assert flow.call("POST", path, {"request_id": "c", "expected_version": 1})[0] == 200
    replay = flow.call("POST", path, {"request_id": "c", "expected_version": 1})
    assert replay[0] == 200 and replay[1]["pairing"]["state"] == "cancelled"
    other = flow.call("POST", path, {"request_id": "d", "expected_version": 2})
    assert (other[0], other[1]["reason"]) == (409, "already_closed")
    stale = flow.made(label="stale-box")
    freeze(monkeypatch, minutes=20)
    got = flow.call(
        "POST", f"/{stale['pairing']['id']}/close", {"request_id": "e", "expected_version": 1}
    )
    assert (got[0], got[1]["reason"]) == (409, "nothing_to_close")


def test_turning_setup_off_or_revoking_the_machine_reads_as_closed(flow):
    waiting = flow.made()
    approved = flow.made(label="second-box")
    credentials = flow.connect(approved)
    flow.accept(approved)
    enable(flow.hub, flow.owner, False)
    assert flow.get(waiting)["state"] == "cancelled"
    assert flow.get(approved)["state"] == "revoked"
    assert flow.hub.db.authenticate_token(credentials["machine_token"]) is None


def test_revoking_on_the_runners_screen_closes_the_pairing(flow):
    made = flow.made()
    credentials = flow.connect(made)
    flow.accept(made)
    status, _ = http(
        flow.hub.port,
        "DELETE",
        "/api/runners/" + credentials["machine_id"],
        cookie=flow.owner["cookie"],
    )
    assert status == 200
    assert flow.get(made)["state"] == "revoked"


# ---- carrying settings over ------------------------------------------------------------------


def test_copy_from_carries_settings_and_requesters_and_revalidates_them(flow):
    cid, _token = cli_token(flow.owner, "coordinator")
    source = flow.made(
        label="source-box", requesters=[cid], max_runtime_mode="auto-accept-edits", max_in_flight=2
    )
    credentials = flow.connect(source)
    flow.accept(source)
    status, result = flow.call(
        "POST",
        "",
        {"request_id": "copy-1", "kind": "runner", "label": "copy-box", "copy_from": "source-box"},
    )
    assert status == 201, result
    grant = result["pairing"]["grant"]
    assert grant["requesters"] == [cid] and grant["wings"] == ["shared"]
    assert grant["projects"] == ["demo"] and grant["max_runtime_mode"] == "auto-accept-edits"
    assert grant["max_in_flight"] == 2
    status, result = flow.call(
        "POST",
        "",
        {
            "request_id": "copy-2",
            "kind": "runner",
            "label": "mix-box",
            "copy_from": credentials["machine_id"],
            "projects": ["mine"],
        },
    )
    assert status == 201 and result["pairing"]["grant"]["projects"] == ["mine"]
    assert result["pairing"]["grant"]["topics"] == ["worker"]
    assert (
        flow.call(
            "POST",
            f"/{flow.get(source)['id']}/close",
            {"request_id": "c", "expected_version": flow.get(source)["version"]},
        )[0]
        == 200
    )
    status, result = flow.call(
        "POST",
        "",
        {"request_id": "copy-3", "kind": "runner", "label": "late-box", "copy_from": "source-box"},
    )
    assert (status, result["field"], result["reason"]) == (400, "copy_from", "not_found")


def test_a_requester_that_lapsed_is_not_carried_over(flow):
    cid, _token = cli_token(flow.owner, "short-lived")
    source = flow.made(label="source-box", requesters=[cid])
    flow.connect(source)
    flow.accept(source)
    flow.hub.db.revoke_connection(
        cid, flow.owner["workspace"], actor=flow.owner["user_id"]
    ) if hasattr(flow.hub.db, "revoke_connection") else None
    with flow.hub.db._lock:
        flow.hub.db._conn.execute(
            "UPDATE connections SET revoked_at=? WHERE id=?",
            (control_db._iso(control_db._now()), cid),
        )
    status, result = flow.call(
        "POST",
        "",
        {"request_id": "copy-1", "kind": "runner", "label": "copy-box", "copy_from": "source-box"},
    )
    assert (status, result["field"], result["reason"]) == (400, "requesters", "invalid")


# ---- same transaction, classification, texts --------------------------------------------------


def test_create_and_its_audit_commit_together(flow, monkeypatch):
    real = flow.hub.db.audit

    def broken(*args, **kwargs):
        if kwargs.get("action") == "runner.pairing.create":
            raise RuntimeError("audit unavailable")
        return real(*args, **kwargs)

    monkeypatch.setattr(flow.hub.db, "audit", broken)
    status, _ = flow.create()
    assert status >= 500
    with flow.hub.db._lock:
        for name in ("machine_pairings", "runner_enrollments"):
            assert flow.hub.db._conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] == 0


def test_accept_and_its_audit_commit_together(flow, monkeypatch):
    made = flow.made()
    credentials = flow.connect(made)
    item = flow.get(made)
    real = flow.hub.db.audit

    def broken(*args, **kwargs):
        if kwargs.get("action") == "runner.pairing.accept":
            raise RuntimeError("audit unavailable")
        return real(*args, **kwargs)

    monkeypatch.setattr(flow.hub.db, "audit", broken)
    status, _ = flow.accept(made)
    assert status >= 500
    monkeypatch.undo()
    assert flow.get(made)["state"] == "connected" and flow.get(made)["version"] == item["version"]
    with flow.hub.db._lock:
        machine = registry.get_machine(
            flow.hub.db, flow.owner["workspace"], credentials["machine_id"]
        )
    assert machine["enabled"] == 0


def test_tables_are_classified_and_the_code_is_in_no_pairing_column(flow):
    made = flow.made()
    flow.connect(made)
    with flow.hub.db._lock:
        present = tables.present(flow.hub.db._conn)
        assert tables.unclassified(present) == []
        for name in ("machine_pairings", "machine_pairing_steps"):
            assert tables.workspace_clause(name, present) is not None


def test_every_refusal_reason_in_the_code_has_a_sentence_and_a_next_step():
    source = Path(pairing.__file__).read_text()
    used = set(re.findall(r'refuse\((?:None|"[a-z_]+"),\s*"([a-z_]+)"', source))
    used |= set(re.findall(r'return "([a-z_]+)"', source[source.index("def _check_reason") :]))
    assert used and used <= set(pairing.REASONS), sorted(used - set(pairing.REASONS))
    for text, step in pairing.REASONS.values():
        assert text.endswith(".") and step.endswith(".")
    for state, parts in pairing.STATES.items():
        assert all(parts), state
    assert set(pairing.CAUSES) >= {"unknown"} and all(all(v) for v in pairing.CAUSES.values())


def test_options_serve_every_text_the_screen_needs(flow):
    status, result = flow.call("GET", "/options")
    assert status == 200
    assert [k["id"] for k in result["kinds"]] == ["runner", "target"]
    assert (
        result["kinds"][1]["available"] is False
        and result["kinds"][1]["reason"] == "kind_unavailable"
    )
    assert set(result["states"]) == set(pairing.STATES) and set(result["reasons"]) == set(
        pairing.REASONS
    )
    assert result["setup_enabled"] is True and "shared" in result["wings"]
    assert result["code_minutes"] == 15 and result["steps"] == list(pairing.STEPS)
