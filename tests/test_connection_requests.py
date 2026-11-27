"""Member connections require owner approval and requester-only secret collection."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta

import pytest

from commonplace import control_db, sessions
from commonplace.account import deletion
from commonplace.boards import BoardService
from commonplace.control_db import ControlError
from commonplace.extensions import ToolContext
from commonplace.policy import Policy
from tests._commonplace_account_helpers import site_fixture  # noqa: F401
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_agent_connections import http, tool

ROUTE = "/api/connection-requests"


@dataclass
class Crew:
    member: str
    owner: str = field(repr=False)
    cookie: str = field(repr=False)


def cookie(hub, user, workspace=None):
    return f"{sessions.COOKIE}={sessions.start_session(hub.db, user, workspace or hub.workspace)}"


@pytest.fixture
def crew(hub):
    member = hub.db.create_user("member@example.test", name="Member Example")
    hub.db.add_member(hub.workspace, member, "member", actor=hub.admin)

    return Crew(member, cookie(hub, hub.admin), cookie(hub, member))


def request(hub, crew, **body):
    status, _, result = http(
        hub, "POST", ROUTE, cookie=crew.cookie, body={"name": "My Coworker", **body}
    )
    assert status == 201
    return result["request"]


def act(hub, crew, row, action, *, owner=False, body=None):
    return http(
        hub,
        "POST",
        f"{ROUTE}/{row['id']}/{action}",
        cookie=crew.owner if owner else crew.cookie,
        body=body or {},
    )


def owner_board(hub):
    return BoardService(
        ToolContext(sessions.person_principal(hub.db, hub.admin, hub.workspace), hub.gateway)
    )


def test_member_can_request_a_connection_without_creating_a_token(hub, crew):
    note = "For my Coworker.\n  Keep these words. Ω"
    status, headers, result = http(
        hub, "POST", ROUTE, cookie=crew.cookie, body={"name": "My Coworker", "note": note}
    )
    assert status == 201
    request = result["request"]
    assert request["status"] == "pending" and request["user_id"] == crew.member
    assert request["name"] == "My Coworker" and request["note"] == note
    assert headers["Cache-Control"] == "no-store"
    assert hub.db.connections(hub.workspace) == []
    assert http(hub, "GET", ROUTE, cookie=crew.cookie)[2]["requests"] == [request]
    assert http(hub, "POST", cookie=crew.cookie, body={"name": "direct"})[0] == 403


def test_member_sees_only_their_requests_and_can_cancel_once(hub, crew):
    row = request(hub, crew)
    other = hub.db.create_user("other-member@example.test")
    hub.db.add_member(hub.workspace, other, "member", actor=hub.admin)
    other_cookie = cookie(hub, other)
    assert http(hub, "GET", ROUTE, cookie=other_cookie)[2]["requests"] == []
    assert http(hub, "POST", f"{ROUTE}/{row['id']}/cancel", cookie=other_cookie, body={})[0] == 404
    assert act(hub, crew, row, "cancel")[2]["request"]["status"] == "cancelled"
    assert act(hub, crew, row, "cancel")[0] == 200
    assert act(hub, crew, row, "approve", owner=True)[0] == 409
    assert hub.db.connections(hub.workspace) == []


def test_pending_requests_appear_only_in_the_signed_in_owners_needs(hub, crew):
    before = owner_board(hub).needs_me()["count"]
    row = request(hub, crew, note="For project planning")
    status, _, needs = http(hub, "GET", "/api/needs-me", cookie=crew.owner)
    assert status == 200
    assert needs["count"] == before + 1
    assert needs["connection_requests"] == [row]
    assert row["requester_name"] == "Member Example"
    assert row["note"] == "For project planning"
    assert http(hub, "GET", "/api/needs-me", cookie=crew.cookie)[0] == 403
    bearer = hub.token("owner-app", profile="full", scopes=["memory:read"], trust="trusted")
    needs = BoardService(ToolContext(hub.db.authenticate_token(bearer), hub.gateway)).needs_me()
    assert "connection_requests" not in needs
    assert act(hub, crew, row, "cancel")[0] == 200
    assert owner_board(hub).needs_me()["connection_requests"] == []


def test_approval_creates_a_named_untrusted_expiring_connection_and_member_collects_once(
    hub, crew, caplog
):
    row = request(hub, crew)
    before = control_db._now()
    status, _, approved = act(hub, crew, row, "approve", owner=True)
    assert status == 200 and "token" not in approved
    approved_row = approved["request"]
    cid = approved_row["connection_id"]
    connection = hub.db.connection(cid, hub.workspace)
    assert connection["user_id"] == crew.member and connection["display_name"] == "My Coworker"
    assert connection["token_hash"] is None and connection["token_hint"] == ""
    assert (
        before + timedelta(days=89)
        < control_db._parse(connection["expires_at"])
        <= control_db._now() + timedelta(days=90)
    )
    policy = Policy.from_json(connection["policy"])
    assert policy.trust == "untrusted" and policy.write_wing == "inbox"
    assert set(policy.scopes) == {"memory:read", "memory:write"}
    assert policy.can_read_wing("shared") and not policy.can_read_wing("private.notes")
    assert policy.can_read_wing("private_other_person") is False
    assert not policy.has_scope("admin") and not policy.has_scope("private:read")
    assert act(hub, crew, row, "approve", owner=True)[2] == approved
    assert len(hub.db.connections(hub.workspace)) == 1
    assert act(hub, crew, row, "decline", owner=True)[0] == 409
    assert act(hub, crew, row, "collect", owner=True)[0] == 404
    assert http(hub, "POST", f"/api/connections/{cid}/rotate", cookie=crew.owner, body={})[0] == 403
    status, headers, collected = act(hub, crew, row, "collect")
    assert status == 200 and headers["Cache-Control"] == "no-store"
    token = collected["token"]
    principal = hub.db.authenticate_token(token)
    assert principal.user_id == crew.member and principal.workspace_id == hub.workspace
    assert principal.role == "member" and principal.policy.trust == "untrusted"
    assert act(hub, crew, row, "collect")[0] == 409
    assert http(hub, "POST", f"/api/connections/{cid}/rotate", cookie=crew.owner, body={})[0] == 403
    status, _, listing = http(hub, "GET", cookie=crew.owner)
    assert status == 200
    assert (
        next(c for c in listing["connections"] if c["id"] == cid)["user_name"] == "Member Example"
    )
    durable = json.dumps(
        [dict(c) for c in hub.db.connections()] + [dict(c) for c in hub.db.audit_entries()]
    )
    assert not any(
        token in text
        for text in (
            durable,
            json.dumps(listing),
            caplog.text,
            json.dumps(owner_board(hub).needs_me()),
        )
    )


def test_requested_connection_uses_existing_restricted_origin_and_private_refusal(hub, crew):
    owner = owner_board(hub)
    owner.board_add("work")
    row = request(hub, crew)
    assert act(hub, crew, row, "approve", owner=True)[0] == 200
    token = act(hub, crew, row, "collect")[2]["token"]
    failed, text = tool(hub, token, "task_add", board="work", title="Member proposed work")
    assert not failed
    shown = owner.task_show(1)
    assert shown["awaiting_release"] and shown["creator"]["trust"] == "untrusted"
    assert 1 in [item["number"] for item in owner.needs_me()["approvals"]]
    context = ToolContext(sessions.person_principal(hub.db, hub.admin, hub.workspace), hub.gateway)
    drawer = context.memory(
        "mempalace_add_drawer",
        {"wing": "private.notes", "room": "notes", "content": "PRIVATE_MEMBER_CANARY"},
    )["drawer_id"]
    failed, answer = tool(hub, token, "memory_get", id=drawer)
    assert failed and "PRIVATE_MEMBER_CANARY" not in answer


@pytest.mark.parametrize("revoker", ["member", "owner"])
def test_requester_and_owner_can_each_revoke_an_approved_connection(hub, crew, revoker):
    row = request(hub, crew)
    approved = act(hub, crew, row, "approve", owner=True)[2]["request"]
    token = act(hub, crew, row, "collect")[2]["token"]
    if revoker == "member":
        assert act(hub, crew, row, "revoke")[0] == 200
        assert act(hub, crew, row, "revoke")[0] == 200
    else:
        path = f"/api/connections/{approved['connection_id']}"
        assert http(hub, "DELETE", path, cookie=crew.owner)[0] == 200
    assert hub.db.authenticate_token(token) is None
    assert act(hub, crew, row, "collect")[0] == 409


def test_decline_is_final_and_preserves_the_optional_reason(hub, crew):
    row = request(hub, crew)
    reason = "Please use a project connection.\n  Exact reason Ω"
    result = act(hub, crew, row, "decline", owner=True, body={"reason": reason})
    assert result[0] == 200 and result[2]["request"]["status"] == "declined"
    assert act(hub, crew, row, "decline", owner=True, body={"reason": "Different"})[2] == result[2]
    assert act(hub, crew, row, "approve", owner=True)[0] == 409
    assert http(hub, "GET", ROUTE, cookie=crew.cookie)[2]["requests"][0]["reason"] == reason
    assert owner_board(hub).needs_me()["connection_requests"] == []
    assert hub.db.connections(hub.workspace) == []


def test_an_expired_or_revoked_approval_cannot_be_collected(hub, crew):
    for expired in (True, False):
        row = request(hub, crew)
        approved = act(hub, crew, row, "approve", owner=True)[2]["request"]
        cid = approved["connection_id"]
        if expired:
            with hub.db._lock:
                hub.db._conn.execute(
                    "UPDATE connections SET expires_at='2000-01-01T00:00:00Z' WHERE id=?", (cid,)
                )
        else:
            assert http(hub, "DELETE", f"/api/connections/{cid}", cookie=crew.owner)[0] == 200
        assert act(hub, crew, row, "collect")[0] == 409
        assert hub.db.connection(cid, hub.workspace)["token_hash"] is None


def test_owner_decisions_are_person_only_and_workspace_scoped(hub, crew):
    row = request(hub, crew)
    alien = hub.db.create_user("alien@example.test")
    elsewhere = hub.db.create_workspace("elsewhere", alien)
    outsider = cookie(hub, alien, elsewhere)
    bearer = hub.token(
        "owner-app", profile="full", scopes=["memory:read", "memory:write"], trust="trusted"
    )
    for action in ("approve", "decline"):
        assert act(hub, crew, row, action)[0] == 403
        path = f"{ROUTE}/{row['id']}/{action}"
        assert http(hub, "POST", path, cookie=outsider, body={})[0] == 404
        assert http(hub, "POST", path, token=bearer, body={})[0] == 403
    assert http(hub, "GET", ROUTE, token=bearer)[0] == 403
    assert (
        http(
            hub,
            "POST",
            ROUTE,
            cookie=crew.cookie,
            body={"name": "Wrong workspace", "workspace_id": elsewhere},
        )[0]
        == 400
    )
    assert (
        http(
            hub, "POST", ROUTE, cookie=crew.cookie, body={"name": "Wrong person", "user_id": alien}
        )[0]
        == 400
    )
    assert (
        http(hub, "POST", ROUTE, cookie=crew.cookie, body={"name": "Unsafe", "trust": "trusted"})[0]
        == 400
    )


def test_competing_decisions_and_collectors_have_one_winner(hub, crew):
    row = request(hub, crew)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda action: act(hub, crew, row, action, owner=True)[0], ("approve", "decline")
            )
        )
    assert sorted(results) == [200, 409]
    listed = http(hub, "GET", ROUTE, cookie=crew.cookie)[2]["requests"][0]
    if listed["status"] == "declined":
        row = request(hub, crew)
        assert act(hub, crew, row, "approve", owner=True)[0] == 200
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: act(hub, crew, row, "collect")[0], range(2)))
    assert sorted(results) == [200, 409]


def test_failed_creation_rolls_back_both_connection_and_decision(hub, crew, monkeypatch):
    row = request(hub, crew)
    create = hub.db.create_token

    def fail(**kwargs):
        create(**kwargs)
        raise ControlError("Creation failed")

    monkeypatch.setattr(hub.db, "create_token", fail)
    assert act(hub, crew, row, "approve", owner=True)[0] == 409
    assert hub.db.connections(hub.workspace) == []
    assert http(hub, "GET", ROUTE, cookie=crew.cookie)[2]["requests"][0]["status"] == "pending"


@pytest.mark.parametrize("phase", ["approve", "collect"])
def test_departed_members_cannot_receive_connections(hub, crew, phase):
    row = request(hub, crew)
    if phase == "collect":
        assert act(hub, crew, row, "approve", owner=True)[0] == 200
    assert (
        hub.db.remove_member(hub.workspace, crew.member, actor=hub.admin, dry_run=False)["state"]
        == "removed"
    )
    assert act(hub, crew, row, phase, owner=phase == "approve")[0] in (401, 409)


def test_csrf_and_invalid_fields_leave_no_request(hub, crew):
    assert (
        http(
            hub,
            "POST",
            ROUTE,
            cookie=crew.cookie,
            body={"name": "My Coworker"},
            Origin="https://elsewhere.invalid",
        )[0]
        == 403
    )
    for body in (
        {},
        {"name": " "},
        {"name": 7},
        {"name": "x" * 121},
        {"name": "ok", "note": []},
        {"name": "ok", "note": "x" * 2001},
    ):
        assert http(hub, "POST", ROUTE, cookie=crew.cookie, body=body)[0] == 400
    assert http(hub, "GET", ROUTE, cookie=crew.cookie)[2]["requests"] == []


def test_account_export_and_account_purge_include_member_requests(site, monkeypatch):
    note = "Export these exact request words. Ω\n  With spaces."
    status, result = site.json(
        "POST",
        ROUTE,
        session=site.session(site.alice, site.team),
        body={"name": "Alice Coworker", "note": note},
    )
    assert status == 201
    exported = site.export(site.alice, site.alice_home).rows("account/connection_requests.ndjson")
    assert len(exported) == 1 and exported[0]["note"] == note
    assert exported[0]["user_id"] == site.alice
    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION", "on")
    assert (
        site.json(
            "POST",
            "/api/account/deletion",
            session=site.session(site.alice, site.alice_home),
            body={"confirm": "alice@example.test"},
        )[0]
        == 200
    )
    assert deletion.purge(site.gateway, site.alice)["state"] == "purged"
    assert site.rows("SELECT * FROM connection_requests WHERE id=?", result["request"]["id"]) == []
    assert site.db.workspace(site.team)["id"] == site.team


def test_workspace_purge_removes_requests_from_a_former_member(site, monkeypatch):
    member = site.db.create_user("past-member@example.test")
    site.db.add_member(site.bob_home, member, "member", actor=site.bob)
    status, result = site.json(
        "POST", ROUTE, session=site.session(member, site.bob_home), body={"name": "Past Coworker"}
    )
    assert status == 201
    with site.db._lock:
        site.db._conn.execute(
            "DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (site.bob_home, member)
        )
    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION", "on")
    assert (
        site.json(
            "POST",
            "/api/account/deletion",
            session=site.session(site.bob, site.bob_home),
            body={"confirm": "bob@example.test"},
        )[0]
        == 200
    )
    assert deletion.purge(site.gateway, site.bob)["state"] == "purged"
    assert site.rows("SELECT * FROM connection_requests WHERE id=?", result["request"]["id"]) == []
    assert site.db.user(member)["disabled_at"] is None
