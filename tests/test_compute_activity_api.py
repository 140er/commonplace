"""Activity uses the real protected gateway and machine identities."""

from __future__ import annotations

import copy
import json

import pytest
from activity_core_fixtures import CANARY, END, START, source
from activity_http import activity_hub, request  # noqa: F401

from commonplace import sessions
from commonplace.compute.activity_collector import collect, identity
from commonplace.policy import Policy
from commonplace.runner import registry


@pytest.fixture
def enrolled(activity_hub, tmp_path):  # noqa: F811
    hub = activity_hub
    owner = sessions.person_principal(hub.db, hub.user, hub.workspace)
    machine = registry.enroll(
        hub.db,
        owner,
        {
            "label": "activity-source",
            "projects": ["sample"],
            "topics": ["work"],
            "wings": ["shared"],
        },
    )
    path = "/api/compute/activity/" + machine["machine_id"]
    snapshot = collect(source(tmp_path / "source.sqlite"), START, END)
    return hub, machine, path, snapshot


def test_machine_consent_real_report_read_trace_and_staleness(enrolled):
    hub, machine, path, snapshot = enrolled
    assert request(hub, path=path)[1]["state"] == "not_enabled"
    assert (
        request(
            hub,
            "POST",
            "/api/runner/activity",
            snapshot,
            cookie=False,
            token=machine["machine_token"],
        )[0]
        == 409
    )
    assert request(hub, "POST", path + "/consent", {"enabled": True})[:2] == (
        200,
        {"enabled": True},
    )
    assert (
        request(
            hub,
            "POST",
            "/api/runner/activity",
            snapshot,
            cookie=False,
            token=machine["machine_token"],
        )[0]
        == 200
    )
    status, body, _ = request(hub, path=path)
    assert status == 200 and body["state"] == "stale"
    assert body["snapshot"]["metrics"]["agent_hours"] == snapshot["metrics"]["agent_hours"]
    assert all("buckets" not in row for row in body["snapshot"]["threads"])
    thread = snapshot["threads"][0]
    assert request(hub, path=path + "/" + thread["id"])[1]["thread"] == thread
    assert request(hub, path=path + "?window=7")[1]["state"] == "missing"
    assert request(hub, "POST", path + "/consent", {"enabled": False})[0] == 200
    assert request(hub, path=path)[1]["snapshot"] is None
    assert (
        request(
            hub,
            "POST",
            "/api/runner/activity",
            snapshot,
            cookie=False,
            token=machine["machine_token"],
        )[0]
        == 409
    )


def test_anon_member_agent_coowner_foreign_and_host_isolation(enrolled):
    hub, machine, path, snapshot = enrolled
    request(hub, "POST", path + "/consent", {"enabled": True})
    request(
        hub, "POST", "/api/runner/activity", snapshot, cookie=False, token=machine["machine_token"]
    )
    assert request(hub, cookie=False)[0] == 401
    member = hub.db.create_user("member@example.test")
    hub.db.add_member(hub.workspace, member, "member", actor=hub.user)
    cookie = sessions.start_session(hub.db, member, hub.workspace)
    assert request(hub, cookie=cookie)[0] == 403
    coowner = hub.db.create_user("coowner@example.test")
    hub.db.add_member(hub.workspace, coowner, "owner", actor=hub.user)
    co_cookie = sessions.start_session(hub.db, coowner, hub.workspace)
    assert request(hub, cookie=co_cookie)[1]["hosts"] == []
    assert request(hub, path=path, cookie=co_cookie)[0] == 404
    assert request(hub, "POST", path + "/consent", {"enabled": True}, cookie=co_cookie)[0] == 404
    foreign = hub.db.create_workspace("Elsewhere", hub.user)
    foreign_cookie = sessions.start_session(hub.db, hub.user, foreign)
    assert request(hub, path=path, cookie=foreign_cookie)[0] == 404
    _, token = hub.db.create_token(
        workspace_ref=hub.workspace,
        user_ref=hub.user,
        actor=hub.user,
        name="agent",
        client="test",
        policy=Policy.build(profile="full", trust="trusted"),
    )
    assert request(hub, path=path, cookie=False, token=token)[0] == 403
    assert (
        request(hub, "POST", "/api/runner/activity", snapshot, cookie=False, token=token)[0] == 404
    )
    assert (
        request(
            hub,
            "POST",
            "/api/runner/activity",
            snapshot,
            cookie=False,
            token=machine["worker_token"],
        )[0]
        == 403
    )
    assert request(hub, "POST", "/api/runner/activity", snapshot)[0] == 404
    second = registry.enroll(
        hub.db,
        sessions.person_principal(hub.db, hub.user, hub.workspace),
        {
            "label": "another-source",
            "projects": ["sample"],
            "topics": ["work"],
            "wings": ["shared"],
        },
    )
    assert (
        request(
            hub,
            "POST",
            "/api/runner/activity",
            snapshot,
            cookie=False,
            token=second["machine_token"],
        )[0]
        == 409
    )


def test_unknown_payload_no_echo_replay_bad_queries_and_csrf(enrolled, caplog, capsys):
    hub, machine, path, snapshot = enrolled
    assert (
        request(
            hub, "POST", path + "/consent", {"enabled": True}, origin="https://foreign.example.test"
        )[0]
        == 403
    )
    request(hub, "POST", path + "/consent", {"enabled": True})
    bad = copy.deepcopy(snapshot)
    bad["threads"][0]["prompt"] = CANARY
    status, body, _ = request(
        hub, "POST", "/api/runner/activity", bad, cookie=False, token=machine["machine_token"]
    )
    assert status == 400 and CANARY not in json.dumps(body)
    assert request(hub, path=path)[1]["state"] == "missing"
    assert (
        request(
            hub,
            "POST",
            "/api/runner/activity",
            snapshot,
            cookie=False,
            token=machine["machine_token"],
        )[0]
        == 200
    )
    older = copy.deepcopy(snapshot)
    older["collected_at"] -= 1
    assert (
        request(
            hub, "POST", "/api/runner/activity", older, cookie=False, token=machine["machine_token"]
        )[0]
        == 409
    )
    for query in (
        "?offset=-1",
        "?offset=200",
        "?window=invalid",
        "?window=1&window=7",
        "?model=invalid",
        "?secret=" + CANARY,
    ):
        assert request(hub, path=path + query)[0] == 400
    assert CANARY not in caplog.text + capsys.readouterr().out


def test_pagination_grant_change_and_credential_revocation(enrolled):
    hub, machine, path, snapshot = enrolled
    template = snapshot["threads"][0]
    snapshot["threads"] = [
        {
            **copy.deepcopy(template),
            "id": identity("thread", str(n)),
            "model": "other" if n >= 26 else template["model"],
            "status": "queued" if n >= 26 else "completed",
        }
        for n in range(53)
    ]
    snapshot["coverage"]["threads_total"] = snapshot["coverage"]["threads_kept"] = 53
    request(hub, "POST", path + "/consent", {"enabled": True})
    assert (
        request(
            hub,
            "POST",
            "/api/runner/activity",
            snapshot,
            cookie=False,
            token=machine["machine_token"],
        )[0]
        == 200
    )
    pages = [request(hub, path=path + f"?offset={n}")[1] for n in (0, 25, 50)]
    assert [len(p["snapshot"]["threads"]) for p in pages] == [25, 25, 3]
    assert pages[-1]["page"]["next"] is None
    assert len({r["id"] for page in pages for r in page["snapshot"]["threads"]}) == 53
    filtered = request(hub, path=path + "?model=other&view=live")[1]
    assert filtered["page"] == {"offset": 0, "total": 27, "next": 25}
    assert "other" in filtered["thread_models"]
    assert all(row["model"] == "other" for row in filtered["snapshot"]["threads"])
    assert (
        len(request(hub, path=path + "?model=other&view=live&offset=25")[1]["snapshot"]["threads"])
        == 2
    )
    with hub.db._tx():
        hub.db._conn.execute(
            "UPDATE runner_machines SET grant_version=grant_version+1 WHERE id=?",
            (machine["machine_id"],),
        )
    assert request(hub, path=path)[1]["state"] == "not_enabled"
    assert (
        request(
            hub,
            "POST",
            "/api/runner/activity",
            snapshot,
            cookie=False,
            token=machine["machine_token"],
        )[0]
        == 409
    )
    owner = sessions.person_principal(hub.db, hub.user, hub.workspace)
    registry.revoke(hub.db, owner, machine["machine_id"])
    assert request(hub, path=path)[0] == 404
    assert request(
        hub, "POST", "/api/runner/activity", snapshot, cookie=False, token=machine["machine_token"]
    )[0] in (401, 404)
