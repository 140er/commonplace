"""Owner-managed runner requesters: Admin listing, candidates and the owner-only per-id edits."""

# ruff: noqa: F811

import json
from types import SimpleNamespace

import pytest

from commonplace.policy import Policy
from commonplace.runner import registry
from commonplace.web import admin
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

MISSING = "conn_" + "0" * 16


def requesters(hub, owner, mach):
    return machine_row(hub, owner, mach)["requesters"]


def add(hub, owner, mach, ref, **auth):
    return edit(hub, owner, mach, "POST", "/requesters", {"requester": ref}, **auth)


def remove(hub, owner, mach, ref, **auth):
    return edit(hub, owner, mach, "DELETE", "/requesters/" + ref, None, **auth)


def store(hub, mach, refs):
    hub.db._conn.execute(
        "UPDATE runner_machines SET requesters=? WHERE id=?",
        (json.dumps(refs), mach["machine_id"]),
    )


def foreign_agent(hub, name="foreign-agent"):
    other = hub.db.create_user(f"{name}@example.com")
    workspace = hub.db.create_workspace(name, other)
    cid, _ = hub.db.create_token(
        workspace_ref=workspace,
        user_ref=other,
        name=name,
        client="synthetic",
        policy=Policy.build(scopes=["memory:read"], read_wings=["notes"], trust="trusted"),
        actor=other,
        expires_days=None,
    )
    return cid


# ── Admin read side ──


def test_admin_machine_rows_list_requesters(hub):
    clock(hub)
    mid, *_ = machine(hub)
    live, _ = agent(hub, "planner")
    gone, _ = agent(hub, "retired", expires_at=OLD)
    lowered, _ = agent(
        hub,
        "lowered",
        policy=Policy.build(
            scopes=["memory:read"], read_wings=["notes"], trust="untrusted"
        ).to_json(),
    )
    oauth, _ = agent(hub, "oauth-app", auth="oauth")
    outside = foreign_agent(hub)
    hub.db._conn.execute(
        "UPDATE runner_machines SET requesters=? WHERE id=?",
        (json.dumps([live, gone, lowered, oauth, outside, MISSING, "not an id"]), mid),
    )
    status, data, _ = get(hub, "runtime", cookie(hub))
    assert status == 200
    row = data["machines"]["items"][0]
    assert row["requesters"] == [
        {"id": live, "name": "planner", "state": "active"},
        {"id": gone, "name": "retired", "state": "inactive"},
        {"id": lowered, "name": "lowered", "state": "inactive"},
        {"id": oauth, "name": "oauth-app", "state": "inactive"},
        {"id": outside, "name": None, "state": "inactive"},
        {"id": MISSING, "name": None, "state": "inactive"},
    ]
    assert row["requesters_total"] == 7
    assert "foreign-agent" not in json.dumps(data) and "token" not in json.dumps(row)


def test_admin_lists_only_eligible_requester_candidates(hub, monkeypatch):
    clock(hub)
    mid, mc, wc, *_ = machine(hub)
    good, _ = agent(hub, "planner")
    agent(hub, "expired", expires_at=OLD)
    agent(hub, "revoked", revoked_at=OLD)
    agent(hub, "oauth-app", auth="oauth")
    untrusted = Policy.build(scopes=["memory:read"], read_wings=["notes"], trust="untrusted")
    agent(hub, "untrusted-agent", policy=untrusted.to_json())
    foreign_agent(hub)
    status, data, _ = get(hub, "runtime", cookie(hub))
    assert status == 200
    candidates = data["machines"]["requester_candidates"]
    assert [item["id"] for item in candidates] == [good]
    assert candidates[0]["name"] == "planner"
    assert data["machines"]["requester_candidates_more"] is False
    assert mc not in json.dumps(candidates) and wc not in json.dumps(candidates)
    assert "foreign-agent" not in json.dumps(data)
    # Past the cap the list says it is cut short.
    monkeypatch.setattr(admin, "CANDIDATE_LIMIT", 1)
    agent(hub, "second-planner")
    data = get(hub, "runtime", cookie(hub))[1]["machines"]
    assert len(data["requester_candidates"]) == 1 and data["requester_candidates_more"] is True


def test_requester_view_is_built_only_for_the_page(hub, monkeypatch):
    clock(hub)
    ids = [machine(hub, n)[0] for n in (1, 2, 3)]
    live, _ = agent(hub, "planner")
    for mid in ids:
        hub.db._conn.execute(
            "UPDATE runner_machines SET requesters=? WHERE id=?", (json.dumps([live]), mid)
        )
    monkeypatch.setattr(admin, "PAGE_SIZE", 2)
    built = []
    real = admin.requester_views

    def counting(*args, **kwargs):
        built.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(admin, "requester_views", counting)
    data = get(hub, "runtime", cookie(hub))[1]["machines"]
    assert len(data["items"]) == 2 and data["has_more"] is True
    assert len(built) == 2, "requesters are resolved only for the machines on the page"


# ── Owner writes ──


def test_owner_adds_and_removes_one_requester_at_a_time(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    mach = enroll(hub, owner)
    first, _ = cli_token(owner, "planner-one")
    second, _ = cli_token(owner, "planner-two")
    status, body = add(hub, owner, mach, first)
    assert status == 200 and body["requesters"] == [first], (status, body)
    assert add(hub, owner, mach, second)[0] == 200
    assert add(hub, owner, mach, second)[0] == 200, "adding twice is harmless"
    assert requesters(hub, owner, mach) == [first, second]
    assert remove(hub, owner, mach, first)[0] == 200
    assert remove(hub, owner, mach, first)[0] == 200, "removing twice is harmless"
    assert requesters(hub, owner, mach) == [second]


def test_add_and_remove_keep_entries_the_page_cannot_show(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    mach = enroll(hub, owner)
    hidden = [f"conn_{n:016x}" for n in range(1, 34)]
    store(hub, mach, hidden)
    fresh, _ = cli_token(owner, "planner-new")
    status, body = add(hub, owner, mach, fresh)
    assert status == 200, (status, body)
    assert requesters(hub, owner, mach) == [*hidden, fresh]
    assert remove(hub, owner, mach, hidden[0])[0] == 200
    assert requesters(hub, owner, mach) == [*hidden[1:], fresh], "the 33rd entry stays"
    assert hidden[32] in requesters(hub, owner, mach)


def test_a_stale_page_cannot_regrant_a_removed_requester(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    mach = enroll(hub, owner)
    x, _ = cli_token(owner, "planner-x")
    y, _ = cli_token(owner, "planner-y")
    z, _ = cli_token(owner, "planner-z")
    assert add(hub, owner, mach, x)[0] == 200 and add(hub, owner, mach, y)[0] == 200
    # One session removes X; another session still shows [X, Y] and adds Z.
    assert remove(hub, owner, mach, x)[0] == 200
    assert add(hub, owner, mach, z)[0] == 200
    assert requesters(hub, owner, mach) == [y, z]
    # A stale remove of an entry that is already gone changes nothing.
    assert remove(hub, owner, mach, x)[0] == 200
    assert requesters(hub, owner, mach) == [y, z]


def test_adding_vets_only_the_added_entry(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    mach = enroll(hub, owner)
    lapsed, _ = cli_token(owner, "planner-lapsed")
    hub.db._conn.execute(
        "UPDATE connections SET expires_at='2000-01-01T00:00:00Z' WHERE id=?", (lapsed,)
    )
    other, _ = cli_token(owner, "planner-other")
    untrusted, _ = cli_token(owner, "planner-untrusted", trust="untrusted")
    for bad in (lapsed, untrusted, MISSING, mach_worker(hub, owner, mach)):
        status, body = add(hub, owner, mach, bad)
        assert status == 400, (bad, status, body)
    assert requesters(hub, owner, mach) == []
    # A lapsed entry the machine already holds does not block adding or removing others.
    store(hub, mach, [lapsed])
    assert add(hub, owner, mach, other)[0] == 200
    assert remove(hub, owner, mach, other)[0] == 200
    assert requesters(hub, owner, mach) == [lapsed]


def mach_worker(hub, owner, mach):
    return machine_row(hub, owner, mach)["worker_connection_id"]


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
    status, body = edit(hub, owner, mach, body={"requesters": [lapsed]})
    assert status == 200, (status, body)
    assert requesters(hub, owner, mach) == [lapsed]
    other = enroll(hub, owner, label="second-machine")
    status, body = edit(hub, owner, other, body={"requesters": [lapsed]})
    assert status == 400, (status, body)


def test_only_the_owner_writes_requesters(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    mach = enroll(hub, owner)
    keep, _ = cli_token(owner, "planner-keep")
    other, _ = cli_token(owner, "planner-other")
    assert add(hub, owner, mach, keep)[0] == 200
    _, trusted_token = cli_token(owner, "bystander")
    callers = [
        {"cookie": member(hub, owner, owners["B"])["cookie"]},
        {"token": trusted_token},
        {"token": mach["worker_token"]},
        {"token": mach["machine_token"]},
    ]
    for auth in callers:
        for status, response in (
            edit(hub, owner, mach, body={"requesters": []}, **auth),
            add(hub, owner, mach, other, **auth),
            remove(hub, owner, mach, keep, **auth),
        ):
            assert status in (401, 403, 404), (status, response)
    status, _ = http(
        hub.port, "PATCH", "/api/runners/" + mach["machine_id"], body={"requesters": []}
    )
    assert status in (401, 403, 404)
    assert requesters(hub, owner, mach) == [keep]


def test_an_owner_of_another_workspace_cannot_edit_requesters(signup_hub, owners):
    hub, owner, outsider = signup_hub, owners["A"], owners["B"]
    assert outsider["workspace"] != owner["workspace"]
    mach = enroll(hub, owner)
    keep, _ = cli_token(owner, "planner-keep")
    theirs, _ = cli_token(outsider, "outsider-agent")
    assert add(hub, owner, mach, keep)[0] == 200
    auth = {"cookie": outsider["cookie"]}
    attempts = [
        edit(hub, owner, mach, body={"requesters": [theirs]}, **auth),
        edit(hub, owner, mach, body={"requesters": []}, **auth),
        add(hub, owner, mach, theirs, **auth),
        add(hub, owner, mach, keep, **auth),
        remove(hub, owner, mach, keep, **auth),
    ]
    for status, response in attempts:
        assert status in (403, 404), (status, response)
    assert requesters(hub, owner, mach) == [keep]
    # The machine's own owner cannot name another workspace's agent either.
    for status, response in (add(hub, owner, mach, theirs), add(hub, outsider, mach, theirs)):
        assert status in (400, 404), (status, response)
    assert requesters(hub, owner, mach) == [keep]


def test_registry_refuses_a_non_owner_on_its_own(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    mach = enroll(hub, owner)
    keep, _ = cli_token(owner, "planner-keep")
    assert add(hub, owner, mach, keep)[0] == 200
    caller = SimpleNamespace(
        person=True, role="member", workspace_id=owner["workspace"], user_id=owner["user_id"]
    )
    mid = mach["machine_id"]
    for call in (
        lambda: registry.patch(hub.db, caller, mid, {"requesters": []}),
        lambda: registry.add_requester(hub.db, caller, mid, keep),
        lambda: registry.remove_requester(hub.db, caller, mid, keep),
    ):
        with pytest.raises(PermissionError):
            call()
    agent_caller = SimpleNamespace(
        person=False, role="owner", workspace_id=owner["workspace"], user_id=owner["user_id"]
    )
    with pytest.raises(PermissionError):
        registry.remove_requester(hub.db, agent_caller, mid, keep)
    assert requesters(hub, owner, mach) == [keep]


def test_a_registry_permission_error_is_a_403(signup_hub, owners, monkeypatch):
    hub, owner = signup_hub, owners["A"]
    mach = enroll(hub, owner)

    def refuse(*args, **kwargs):
        raise PermissionError("a signed-in workspace owner is required")

    for name in ("patch", "add_requester", "remove_requester"):
        monkeypatch.setattr(registry, name, refuse)
    ref, _ = cli_token(owner, "planner-ref")
    for status, response in (
        edit(hub, owner, mach, body={"requesters": []}),
        add(hub, owner, mach, ref),
        remove(hub, owner, mach, ref),
    ):
        assert status == 403, (status, response)
