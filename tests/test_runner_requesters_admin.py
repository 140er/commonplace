"""Owner-managed runner requesters: Admin listing, candidates and the owner-only write."""

# ruff: noqa: F811

import json

from commonplace.policy import Policy
from tests._commonplace_helpers import hub_fixture as hub_fixture
from tests.runner_harness import (
    cli_token,
    http,
    member,
    owners,  # noqa: F401
    signup_hub,  # noqa: F401
)
from tests.test_admin_api import agent, clock, cookie, get
from tests.test_admin_runtime_api import OLD, machine
from tests.test_runner_owner import edit, enroll, machine_row


def requesters(hub, owner, mach):
    return machine_row(hub, owner, mach)["requesters"]


def test_admin_machine_rows_list_requesters(hub):
    clock(hub)
    mid, *_ = machine(hub)
    live, _ = agent(hub, "planner")
    gone, _ = agent(hub, "retired", expires_at=OLD)
    hub.db._conn.execute(
        "UPDATE runner_machines SET requesters=? WHERE id=?",
        (json.dumps([live, gone, "conn_missing"]), mid),
    )
    status, data, _ = get(hub, "runtime", cookie(hub))
    assert status == 200
    row = data["machines"]["items"][0]
    assert row["requesters"] == [
        {"id": live, "name": "planner", "state": "active"},
        {"id": gone, "name": "retired", "state": "inactive"},
        {"id": "conn_missing", "name": None, "state": "inactive"},
    ]
    assert "token" not in json.dumps(row)


def test_admin_lists_only_eligible_requester_candidates(hub):
    clock(hub)
    mid, mc, wc, *_ = machine(hub)
    good, _ = agent(hub, "planner")
    agent(hub, "expired", expires_at=OLD)
    agent(hub, "revoked", revoked_at=OLD)
    agent(hub, "oauth-app", auth="oauth")
    untrusted = Policy.build(scopes=["memory:read"], read_wings=["notes"], trust="untrusted")
    agent(hub, "untrusted-agent", policy=untrusted.to_json())
    status, data, _ = get(hub, "runtime", cookie(hub))
    assert status == 200
    candidates = data["machines"]["requester_candidates"]
    assert [item["id"] for item in candidates] == [good]
    assert candidates[0]["name"] == "planner"
    assert mc not in json.dumps(candidates) and wc not in json.dumps(candidates)


def test_owner_adds_and_removes_requesters(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    mach = enroll(hub, owner)
    first, _ = cli_token(owner, "planner-one")
    second, _ = cli_token(owner, "planner-two")
    status, body = edit(hub, owner, mach, body={"requesters": [first]})
    assert status == 200, (status, body)
    assert requesters(hub, owner, mach) == [first]
    status, body = edit(hub, owner, mach, body={"requesters": [first, second]})
    assert status == 200, (status, body)
    status, body = edit(hub, owner, mach, body={"requesters": [second]})
    assert status == 200, (status, body)
    assert requesters(hub, owner, mach) == [second]


def test_removing_a_requester_keeps_a_lapsed_peer(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    mach = enroll(hub, owner)
    live, _ = cli_token(owner, "planner-live")
    lapsed, _ = cli_token(owner, "planner-lapsed")
    status, body = edit(hub, owner, mach, body={"requesters": [live, lapsed]})
    assert status == 200, (status, body)
    hub.db._conn.execute(
        "UPDATE connections SET expires_at='2000-01-01T00:00:00Z' WHERE id=?", (lapsed,)
    )
    # Removing the live one leaves the lapsed peer in place instead of refusing the edit.
    status, body = edit(hub, owner, mach, body={"requesters": [lapsed]})
    assert status == 200, (status, body)
    assert requesters(hub, owner, mach) == [lapsed]
    # A lapsed connection can still not be newly added.
    other = enroll(hub, owner, label="second-machine")
    status, body = edit(hub, owner, other, body={"requesters": [lapsed]})
    assert status == 400, (status, body)


def test_only_the_owner_writes_requesters(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    mach = enroll(hub, owner)
    keep, _ = cli_token(owner, "planner-keep")
    other, _ = cli_token(owner, "planner-other")
    assert edit(hub, owner, mach, body={"requesters": [keep]})[0] == 200
    _, trusted_token = cli_token(owner, "bystander")
    callers = [
        {"cookie": member(hub, owner, owners["B"])["cookie"]},
        {"token": trusted_token},
        {"token": mach["worker_token"]},
        {"token": mach["machine_token"]},
    ]
    for auth in callers:
        for body in ({"requesters": [keep, other]}, {"requesters": []}):
            status, response = edit(hub, owner, mach, body=body, **auth)
            assert status in (401, 403, 404), (status, response)
    status, _ = http(
        hub.port, "PATCH", "/api/runners/" + mach["machine_id"], body={"requesters": []}
    )
    assert status in (401, 403, 404)
    assert requesters(hub, owner, mach) == [keep]
