"""Workspace owners approve public clients through the real OAuth routes."""

import json
import re
import sqlite3
from urllib.parse import parse_qs, urlsplit

import pytest

from commonplace import oauth, sessions
from commonplace.oauth import store
from tests._commonplace_helpers import hub_fixture as hub_fixture
from tests.test_commonplace_oauth import Client, assert_error


@pytest.fixture
def client(hub):
    return Client(hub)


def form(client, cid):
    path = client.authorize_path(cid)
    status, _, page = client.request("GET", path)
    assert status == 200, page
    request = re.search(r'name="request" value="([^"]+)"', page)[1]
    return path, {"request": request, "decision": "allow"}, page


def allow(client, cid, *, approve=True):
    path, data, _ = form(client, cid)
    if approve:
        data["approve_client"] = "1"
    status, headers, text = client.request("POST", path, data)
    assert status == 303, text
    return parse_qs(urlsplit(headers["Location"]).query)["code"][0]


def member(client):
    db = client.hub.db
    uid = db.create_user("member@example.test")
    db.add_member(client.hub.workspace, uid, "member", actor=client.hub.admin)
    client.login("member@example.test")
    return uid


def test_unapproved_registration_and_get_do_not_authorize(client):
    cid = client.register()
    client.login()
    path, data, _ = form(client, cid)
    assert_error(client.request("POST", path, data), "access_denied", 403)
    assert client.hub.db.connections() == []
    with client.hub.db._lock:
        assert client.hub.db._conn.execute("SELECT count(*) FROM oauth_codes").fetchone()[0] == 0


@pytest.mark.parametrize("tick", [False, True])
def test_member_cannot_approve_unapproved_client(client, tick):
    cid = client.register()
    member(client)
    path, data, page = form(client, cid)
    if tick:
        data["approve_client"] = "1"
    assert_error(client.request("POST", path, data), "access_denied", 403)
    assert 'name="approve_client"' not in page
    assert "workspace owner" in page
    assert not client.hub.db.connections()


@pytest.mark.parametrize("auth", ["pat", "oauth"])
def test_agent_cannot_approve_with_an_owner_form(client, auth):
    cid = client.register()
    if auth == "pat":
        token = client.hub.owner_token()
    else:
        client.login()
        other = client.register(client_name="Established client")
        code = allow(client, other)
        status, _, raw = client.exchange(other, code)
        assert status == 200
        token = json.loads(raw)["access_token"]
    client.login()
    path, data, _ = form(client, cid)
    data["approve_client"] = "1"
    assert_error(
        client.request(
            "POST", path, data, cookies=False, headers={"Authorization": "Bearer " + token}
        ),
        "invalid_request",
        401,
    )


def test_owner_approval_is_explicit_and_authorization_is_single_use(client):
    cid = client.register()
    client.login()
    path, data, page = form(client, cid)
    assert 'name="approve_client" value="1"' in page
    assert "Approve this client for" in page and "home" in page
    assert " checked" not in page
    data["approve_client"] = "1"
    status, headers, text = client.request("POST", path, data)
    assert status == 303, text
    code = parse_qs(urlsplit(headers["Location"]).query)["code"][0]
    assert client.exchange(cid, code)[0] == 200
    assert_error(client.request("POST", path, data), "invalid_request", 403)
    with client.hub.db._lock:
        rows = client.hub.db._conn.execute("SELECT * FROM oauth_client_approvals").fetchall()
    assert len(rows) == 1
    assert (rows[0]["workspace_id"], rows[0]["client_id"], rows[0]["approved_by"]) == (
        client.hub.workspace,
        cid,
        client.hub.admin,
    )
    assert_error(client.exchange(cid, code))


def test_approved_client_keeps_member_consent_and_token_paths(client):
    cid = client.register()
    client.login()
    owner_code = allow(client, cid)
    assert client.exchange(cid, owner_code)[0] == 200
    uid = member(client)
    code = allow(client, cid, approve=False)
    status, _, raw = client.exchange(cid, code)
    assert status == 200, raw
    pair = json.loads(raw)
    principal = client.hub.gateway.authenticate("Bearer " + pair["access_token"])
    assert principal.user_id == uid and principal.role == "member" and not principal.person
    assert "private:read" not in principal.policy.scopes
    assert client.refresh(cid, pair["refresh_token"])[0] == 200
    assert_error(client.refresh(cid, pair["refresh_token"]))
    assert client.hub.gateway.authenticate("Bearer " + pair["access_token"]) is None


def test_approval_does_not_cross_workspace_or_client(client):
    cid = client.register()
    client.login()
    allow(client, cid)
    other_client = client.register(client_name="Same callback, new client")
    member(client)
    path, data, _ = form(client, other_client)
    assert_error(client.request("POST", path, data), "access_denied", 403)
    db = client.hub.db
    other_owner = db.create_user("other-owner@example.test")
    other_workspace = db.create_workspace("other", other_owner)
    db.add_member(other_workspace, client.hub.admin, "member", actor=other_owner)
    session = sessions.start_session(db, client.hub.admin, other_workspace)
    client.cookies[sessions.COOKIE] = session
    path, data, _ = form(client, cid)
    data["approve_client"] = "1"
    assert_error(client.request("POST", path, data), "access_denied", 403)
    # The other workspace's own owner can approve the same registration separately.
    client.cookies[sessions.COOKIE] = sessions.start_session(db, other_owner, other_workspace)
    assert client.exchange(cid, allow(client, cid))[0] == 200


def test_owner_lost_ownership_after_get_cannot_approve(client):
    cid = client.register()
    client.login()
    path, data, _ = form(client, cid)
    data["approve_client"] = "1"
    with client.hub.db._lock:
        client.hub.db._conn.execute(
            "UPDATE memberships SET role='member' WHERE workspace_id=? AND user_id=?",
            (client.hub.workspace, client.hub.admin),
        )
    assert_error(client.request("POST", path, data), "access_denied", 403)
    assert not client.hub.db.connections()


def test_approval_rechecks_the_owner_inside_code_creation(client, monkeypatch):
    cid = client.register()
    client.login()
    principal = sessions.person_principal(client.hub.db, client.hub.admin, client.hub.workspace)
    original = client.hub.gateway.principal_for
    monkeypatch.setattr(client.hub.gateway, "principal_for", lambda h: principal)
    path, data, _ = form(client, cid)
    data["approve_client"] = "1"
    with client.hub.db._lock:
        client.hub.db._conn.execute(
            "UPDATE memberships SET role='member' WHERE workspace_id=? AND user_id=?",
            (client.hub.workspace, client.hub.admin),
        )
    assert_error(client.request("POST", path, data), "access_denied", 403)
    monkeypatch.setattr(client.hub.gateway, "principal_for", original)


def test_approval_code_and_request_roll_back_together(client, monkeypatch):
    cid = client.register()
    client.login()
    path, data, _ = form(client, cid)
    data["approve_client"] = "1"
    original = store.create_code

    def fail_after_code(*args, **kwargs):
        original(*args, **kwargs)
        raise oauth.OAuthError("server_error", "Synthetic rollback", 500)

    monkeypatch.setattr(store, "create_code", fail_after_code)
    assert_error(client.request("POST", path, data), "server_error", 500)
    with client.hub.db._lock:
        for table in ("oauth_client_approvals", "oauth_codes", "connections"):
            assert client.hub.db._conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    monkeypatch.setattr(store, "create_code", original)
    assert client.request("POST", path, data)[0] == 303


def test_exchange_rechecks_approval_and_reinstall_never_grandfathers(client):
    cid = client.register()
    client.login()
    code = allow(client, cid)
    with client.hub.db._lock:
        client.hub.db._conn.execute("DROP TABLE IF EXISTS oauth_client_approvals")
    # Model installation over a pre-approval database with an outstanding code.
    store.ensure_schema(client.hub.db)
    assert_error(client.exchange(cid, code))
    assert not client.hub.gateway.authenticate("Bearer cmmn_at_unknown")
    with client.hub.db._lock:
        assert client.hub.db._conn.execute("SELECT count(*) FROM oauth_tokens").fetchone()[0] == 0


def test_approval_withdrawn_after_get_cannot_issue_a_code(client):
    cid = client.register()
    client.login()
    allow(client, cid)
    path, data, _ = form(client, cid)
    with client.hub.db._lock:
        client.hub.db._conn.execute("DELETE FROM oauth_client_approvals")
    assert_error(client.request("POST", path, data), "access_denied", 403)


def test_existing_tokens_keep_expiry_and_revocation_without_new_authorization(client):
    cid = client.register()
    client.login()
    status, _, raw = client.exchange(cid, allow(client, cid))
    assert status == 200
    pair = json.loads(raw)
    with client.hub.db._lock:
        client.hub.db._conn.execute("DROP TABLE IF EXISTS oauth_client_approvals")
    store.ensure_schema(client.hub.db)
    assert client.hub.gateway.authenticate("Bearer " + pair["access_token"])
    assert client.refresh(cid, pair["refresh_token"])[0] == 200
    member(client)
    path, data, _ = form(client, cid)
    assert_error(client.request("POST", path, data), "access_denied", 403)
    assert (
        client.request("POST", "/oauth/revoke", {"client_id": cid, "token": pair["access_token"]})[
            0
        ]
        == 200
    )
    assert client.hub.gateway.authenticate("Bearer " + pair["access_token"]) is None


def test_non_person_account_session_cannot_approve(client):
    cid = client.register()
    client.login()
    path, data, _ = form(client, cid)
    data["approve_client"] = "1"
    with client.hub.db._lock:
        client.hub.db._conn.execute("UPDATE users SET is_person=0 WHERE id=?", (client.hub.admin,))
    assert_error(client.request("POST", path, data), "access_denied", 403)


def test_approval_schema_is_idempotent_and_workspace_classified(client):
    from commonplace.account import tables

    cid = client.register()
    client.login()
    allow(client, cid)
    store.ensure_schema(client.hub.db)
    store.ensure_schema(client.hub.db)
    with client.hub.db._lock:
        conn = client.hub.db._conn
        assert tables.unclassified(tables.present(conn)) == []
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO oauth_client_approvals SELECT * FROM oauth_client_approvals")


def test_owner_removal_clears_their_workspace_approval(client):
    cid = client.register()
    client.login()
    allow(client, cid)
    db = client.hub.db
    other = db.create_user("second-owner@example.test")
    db.add_member(client.hub.workspace, other, "owner", actor=client.hub.admin)
    result = db.remove_member(
        client.hub.workspace,
        client.hub.admin,
        actor=client.hub.admin,
        force_owner=True,
        dry_run=False,
    )
    assert result["delete"]["oauth_client_approvals"] == 1
    with db._lock:
        assert not store.client_approved(db._conn, client.hub.workspace, cid)


def test_session_is_rechecked_after_authentication(client, monkeypatch):
    cid = client.register()
    session = client.login()
    path, data, _ = form(client, cid)
    data["approve_client"] = "1"
    principal = sessions.person_principal(client.hub.db, client.hub.admin, client.hub.workspace)
    monkeypatch.setattr(client.hub.gateway, "principal_for", lambda h: principal)
    with client.hub.db._lock:
        client.hub.db._conn.execute(
            "UPDATE sessions SET revoked_at=? WHERE id_hash=?", (store.iso(), store.digest(session))
        )
    assert_error(client.request("POST", path, data), "invalid_request", 403)
    assert not client.hub.db.connections()
