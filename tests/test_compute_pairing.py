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
        self.credentials = {}

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
        credentials = decrypt(self.key, envelope)
        self.credentials[made["pairing"]["id"]] = credentials
        return credentials

    def report(self, credentials, body, token="machine_token"):
        return http(
            self.hub.port, "POST", STATUS, token=credentials[token], body=body, origin=False
        )

    def accept(self, made, finish=True, **changes):
        """The owner's approval. The machine finishes its four steps first, as it must (finish=False: not yet)."""
        credentials = self.credentials.get(made["pairing"]["id"])
        if finish and credentials and not all(s["state"] == "ok" for s in self.get(made)["steps"]):
            self.finish_steps(credentials)
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


def clock(monkeypatch):
    """Move time from one fixed start: ``at(seconds=...)`` sets the clock to start + that.

    The clock stands still from the moment the fixture runs. If it kept running until the first ``at``, whatever
    the test did first (a slow machine can take seconds over a few requests) would be older than the offset
    assumes, and a rule at a 15-minute boundary would read one second short.
    """
    start = control_db._now()

    def at(**offset):
        now = start + timedelta(**offset)
        monkeypatch.setattr(control_db, "_now", lambda: now)
        return now

    at()
    return at


def test_the_clock_fixture_stands_still_until_it_is_moved(monkeypatch):
    # A slow machine: every look at the clock is a second later than the last.
    begin = control_db._now()
    ticking = iter([begin + timedelta(seconds=n) for n in range(10_000)])
    monkeypatch.setattr(control_db, "_now", lambda: next(ticking))
    at = clock(monkeypatch)
    first = control_db._now()
    assert control_db._now() == first
    assert at(seconds=pairing.OFFLINE_SECONDS + 1) - first == timedelta(
        seconds=pairing.OFFLINE_SECONDS + 1
    ), "the offset is from where the test started, not from a later reading"


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
    # The launcher is fetched first and the rest runs only if that worked (a failed download is not an empty script).
    assert made["setup"]["command"].startswith(
        " S=$(curl -fsS '" + pairing.urls.APP_URL + "/machine-setup.sh') && printf '%s' '"
    )
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
    assert item["next"] == "Waiting for the owner's approval."
    assert "you" not in item["sentence"].lower().split(), (
        "a member reads the owner's decision, not their own"
    )
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
        {"step": "service", "state": "ok"},
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
    # Groups of eight over the whole digest, the way the installer prints them.
    assert re.fullmatch(r"(?:[0-9a-f]{8} ){7}[0-9a-f]{8}", item["fingerprint"])
    assert item["grant"]["wings"] == ["shared"] and item["grant"]["projects"] == ["demo"]
    assert item["grant"]["max_runtime_mode"] == "auto-accept-edits"
    assert item["can_accept"] is False, (
        "approval needs all four steps done: the machine prints its fingerprint last"
    )
    assert item["binding_state"] == "unbound"
    assert re.fullmatch(r"[0-9a-f]{64}", item["identity_digest"])
    flow.finish_steps(credentials)
    assert flow.get(made)["can_accept"] is True
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
    credentials = flow.connect(made)
    item = flow.get(made)
    path = f"/{item['id']}/accept"
    # The authority is the server's: before all four steps are done an approval is refused, whatever the page does.
    early = {
        "request_id": "early",
        "expected_version": item["version"],
        "identity_digest": item["identity_digest"],
    }
    status, result = flow.call("POST", path, early)
    assert (status, result["reason"]) == (409, "not_finished")
    assert result["reason"] in pairing.REASONS
    flow.report(credentials, {"step": "files", "state": "ok"})
    assert flow.call("POST", path, early)[1]["reason"] == "not_finished", (
        "one step of four is not enough"
    )
    flow.finish_steps(credentials)
    item = flow.get(made)
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
    assert item["state"] == "ready" and item["accepted_at"] and item["version"] == 2
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
    flow.finish_steps(flow.connect(made))
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
    flow.finish_steps(flow.connect(made))
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
    at = clock(monkeypatch)
    made = flow.made()
    credentials = flow.connect(made)
    assert [s["state"] for s in flow.get(made)["steps"]] == ["pending"] * 4
    for step in pairing.STEPS:
        status, result = flow.report(credentials, {"step": step, "state": "ok"})
        assert status == 200 and result["state"] == "connected", (
            "finished, but still waiting for the owner"
        )
    flow.accept(made)
    item = flow.get(made)
    assert item["state"] == "ready" and item["state_label"] == "Set up and checked"
    assert all(s["state"] == "ok" for s in item["steps"])
    # No heartbeat has ever arrived, so silence proves nothing: it stays ready.
    at(seconds=pairing.OFFLINE_SECONDS * 4)
    assert flow.get(made)["state"] == "ready"
    at(seconds=0)
    assert flow.report(credentials, {"step": "heartbeat"})[1]["state"] == "ready"
    assert [s["step"] for s in flow.get(made)["steps"]] == list(pairing.STEPS)
    now = at(seconds=pairing.OFFLINE_SECONDS + 1)
    item = flow.get(made)
    assert item["state"] == "offline" and item["last_report_at"] < control_db._iso(now)
    at(seconds=pairing.OFFLINE_SECONDS + 2)
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
    status, result = flow.accept(made, finish=False)
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
    flow.finish_steps(credentials)
    item = flow.get(made)
    real = flow.hub.db.audit

    def broken(*args, **kwargs):
        if kwargs.get("action") == "runner.pairing.accept":
            raise RuntimeError("audit unavailable")
        return real(*args, **kwargs)

    monkeypatch.setattr(flow.hub.db, "audit", broken)
    status, _ = flow.accept(made, finish=False)
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


class LockedOnly:
    """The control connection, refusing every statement made without the control lock held."""

    def __init__(self, db):
        self._db, self._real = db, db._conn

    def execute(self, *args, **kwargs):
        assert self._db._lock._is_owned(), f"unlocked statement: {args[0][:60]}"
        return self._real.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_every_route_reads_the_shared_connection_only_under_the_control_lock(flow):
    """One shared connection serves every request thread: a read outside the lock raced others (SQLITE_MISUSE) and
    showed a signed-in person the sign-in page in a real browser."""
    from types import SimpleNamespace

    from commonplace import sessions

    made = flow.made()
    pid = made["pairing"]["id"]
    db = flow.hub.db
    store = SimpleNamespace(gateway=flow.hub.gateway)
    owner = sessions.person_principal(db, flow.owner["user_id"], flow.owner["workspace"])
    sent = []

    def handler(command):
        return SimpleNamespace(command=command)

    from commonplace.compute import pairing_routes

    real_send = pairing_routes.send_json
    pairing_routes.send_json = lambda h, status, body: sent.append((status, body))
    db._conn = LockedOnly(db)
    try:
        auth = {"session_hash": "unused"}
        for parts in ([], ["options"], [pid]):
            pairing_routes.dispatch(handler("GET"), store, owner, parts, auth, None)
        reader = lambda h, fields, optional: {"request_id": "r", "expected_version": 99}  # noqa: E731
        for action in ("accept", "close"):
            with pytest.raises(Exception) as refused:
                pairing_routes.dispatch(handler("POST"), store, owner, [pid, action], auth, reader)
            assert getattr(refused.value, "reason", None) in (
                "stale_version",
                "identity_digest",
                "required",
            )
    finally:
        db._conn = db._conn._real
        pairing_routes.send_json = real_send
    assert [status for status, _ in sent] == [200, 200, 200]


# ---- review round 1: expiry beats an old report, files before the code is spent, stalled, https --------------


def test_a_failed_check_never_outlives_the_code(flow, monkeypatch):
    """Minute 16 of a 15-minute code: the owner and the machine both read expired, not the stale failure."""
    made = flow.made()
    report = {"step": "prerequisites", "state": "failed", "cause": "disk_full"}
    assert flow.check(made, report=report)[1]["usable"] is True
    assert flow.get(made)["state"] == "failed"
    freeze(monkeypatch, minutes=pairing.CODE_MINUTES + 1)
    answer = flow.check(made)[1]
    assert (answer["usable"], answer["reason"]) == (False, "expired")
    item = flow.get(made)
    assert item["state"] == "expired" and item["cause"] is None
    assert item["retry"]["label"] == "build-box"
    # An expired code takes no report either, so a stale cause cannot come back.
    assert flow.check(made, report=report)[1]["reason"] == "expired"
    assert flow.get(made)["state"] == "expired"


def test_a_step_that_failed_after_the_code_was_spent_stays_failed_past_the_codes_lifetime(
    flow, monkeypatch
):
    made = flow.made()
    credentials = flow.connect(made)
    flow.report(credentials, {"step": "service", "state": "failed", "cause": "service_failed"})
    freeze(monkeypatch, minutes=pairing.CODE_MINUTES + 5)
    assert flow.get(made)["state"] == "failed"


def test_a_files_failure_before_the_code_is_spent_is_shown_and_the_code_stays_usable(flow):
    made = flow.made()
    report = {"step": "files", "state": "failed", "cause": "disk_full"}
    assert flow.check(made, report=report)[1]["usable"] is True
    item = flow.get(made)
    assert item["state"] == "failed" and item["cause"]["step"] == "files"
    assert item["cause"]["text"] == pairing.CAUSES["disk_full"][0]
    assert flow.connect(made)["machine_id"], "the code was never spent"
    # Spent now: the same report is refused and stores nothing new.
    assert flow.check(made, report={"step": "files", "state": "ok"})[1]["reason"] == "used"


def test_a_machine_that_connected_and_never_finished_reads_stalled_for_owner_and_member(
    people, monkeypatch
):
    flow, _, colleague = people
    at = clock(monkeypatch)
    one, two = flow.made(), flow.made(label="second-box")
    flow.connect(one)
    flow.connect(two)
    # A record approved before the server required four finished steps (the only way an approved setup has
    # unfinished steps now) is written directly.
    with flow.hub.db._lock:
        flow.hub.db._conn.execute(
            "UPDATE machine_pairings SET accepted_at=?, accepted_by=? WHERE id=?",
            (control_db._iso(control_db._now()), flow.owner["user_id"], two["pairing"]["id"]),
        )
    assert flow.get(two)["state"] == "approved"
    assert flow.get(one)["stalled"] is False and flow.get(one)["can_accept"] is False
    at(seconds=pairing.OFFLINE_SECONDS + 1)
    for made, state in ((one, "connected"), (two, "approved")):
        item = flow.get(made)
        assert item["stalled"] is True and item["state"] == state
        assert (item["sentence"], item["next"]) == (pairing.STALLED[0], pairing.STALLED[1])
        assert item["can_accept"] is False
        seen = flow.get(made, who=colleague)
        assert (seen["sentence"], seen["next"]) == (pairing.STALLED[0], pairing.STALLED[2])
    # The server refuses an approval of a stalled setup, with its own reason.
    refused = flow.accept(one, finish=False)
    assert (refused[0], refused[1]["reason"]) == (409, "stalled")
    assert refused[1]["reason"] in pairing.REASONS
    # A machine that did finish is never stalled.
    credentials = flow.connect(flow.made(label="third-box"))
    at(seconds=0)
    flow.finish_steps(credentials)
    at(seconds=pairing.OFFLINE_SECONDS * 3)
    assert not any(
        row["stalled"] for row in flow.call("GET")[1]["pairings"] if row["label"] == "third-box"
    )


def test_the_command_is_https_unless_it_points_at_this_machine(flow, monkeypatch):
    monkeypatch.setattr(pairing.urls, "APP_URL", "http://hub.example.test")
    status, result = flow.create()
    assert (status, result["reason"]) == (503, "hub_unavailable")
    monkeypatch.setattr(pairing.urls, "APP_URL", "http://127.0.0.1:8765")
    assert flow.create()[0] == 201


def test_no_failed_next_step_points_at_a_command_the_page_does_not_hold():
    """One next step for a failed machine: choose Set up again. The page cannot reissue a command."""
    for code, (_, then) in pairing.CAUSES.items():
        assert "run the command again" not in then.lower(), code
        assert "Set up again" in then, code
    assert "same command" not in pairing.STATES["failed"][2]
    assert "Set up again" in pairing.STATES["failed"][2]


# ---- review round 2: closing a failed setup, probes, member words, denials in the audit ---------------------


def test_removing_a_failed_or_stalled_setup_is_never_called_a_decline(flow, monkeypatch):
    at = clock(monkeypatch)
    failed = flow.made()
    credentials = flow.connect(failed)
    flow.report(credentials, {"step": "service", "state": "failed", "cause": "service_failed"})
    status, result = flow.close(failed)
    item = result["pairing"]
    assert status == 200 and item["state"] == "abandoned"
    assert item["state_label"] == "Setup removed"
    for text in (item["sentence"], item["next"], item["state_label"]):
        assert "not your machine" not in text.lower() and "did not approve" not in text.lower(), (
            text
        )
    assert item["retry"]["label"] == "build-box" and item["can_close"] is False
    assert flow.hub.db.authenticate_token(credentials["machine_token"]) is None
    # A setup that connected and never finished is removed the same way.
    stuck = flow.made(label="stuck-box")
    flow.connect(stuck)
    at(seconds=pairing.OFFLINE_SECONDS + 1)
    assert flow.close(stuck)[1]["pairing"]["state"] == "abandoned"
    # The words "Not approved" are only for the owner's own refusal of a healthy connected machine.
    fine = flow.made(label="fine-box")
    flow.finish_steps(flow.connect(fine))
    refused = flow.close(fine)[1]["pairing"]
    assert refused["state"] == "declined" and refused["sentence"] == pairing.STATES["declined"][1]
    assert flow.check(fine)[1]["reason"] == "revoked"


def test_a_member_reads_the_owners_approval_in_the_third_person(people):
    flow, _, colleague = people
    waiting = flow.made()
    assert "ready" not in flow.get(waiting, who=colleague)["sentence"].lower()
    flow.connect(waiting)
    item = flow.get(waiting, who=colleague)
    assert item["state_label"] == "Machine connected, waiting for the owner's approval"
    assert item["next"] == "Waiting for the owner's approval."
    options = flow.call("GET", "/options", who=colleague)[1]["states"]["connected"]
    assert options["label"] == item["state_label"]
    owner = flow.get(waiting)
    assert owner["state_label"] == "Machine connected, waiting for your approval"
    assert "fingerprint" in owner["next"].lower() and "Machine fingerprint:" in owner["next"]


def test_a_probe_only_asks_whether_the_setup_is_still_open(flow):
    made = flow.made()
    credentials = flow.connect(made)
    before = flow.get(made)
    answer = flow.report(credentials, {"step": "probe"})
    assert answer[0] == 200 and answer[1]["ok"] is True
    after = flow.get(made)
    assert after["last_report_at"] == before["last_report_at"] and after["steps"] == before["steps"]
    with flow.hub.db._lock:
        assert (
            flow.hub.db._conn.execute("SELECT count(*) FROM machine_pairing_steps").fetchone()[0]
            == 0
        )
    assert flow.check(made, report={"step": "probe"})[0] == 400
    flow.close(made)
    status, result = flow.report(credentials, {"step": "probe"})
    assert status != 200 or result.get("ok") is not True, "a closed setup does not answer ok"


def test_a_refused_check_is_in_the_audit_trail_and_a_wrong_typed_cause_is_a_bad_request(flow):
    made = flow.made()
    assert flow.check(made, code="x" * 43)[1]["reason"] == "code_mismatch"
    denied = [r for r in audit_rows(flow.hub) if r["action"] == "runner.pairing.check"]
    assert (
        len(denied) == 1
        and denied[0]["decision"] == "deny"
        and denied[0]["reason"] == "code_mismatch"
    )
    assert made["setup"]["code"] not in json.dumps([dict(r) for r in denied])
    for cause in (["disk_full"], {"x": 1}, 7):
        status, result = flow.check(
            made, report={"step": "files", "state": "failed", "cause": cause}
        )
        assert status == 400, cause
