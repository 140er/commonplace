"""Closed instances admit only existing instance admins or allowlisted people."""

from dataclasses import replace

import pytest

from commonplace import sessions
from tests.test_commonplace_invites import owner_token, world  # noqa: F401
from tests.test_workspace_creation import create


@pytest.fixture
def closed(world, monkeypatch):  # noqa: F811
    monkeypatch.setenv("COMMONPLACE_SIGNUP", "closed")
    monkeypatch.delenv("COMMONPLACE_SIGNUP_ALLOW_EMAILS", raising=False)
    world.db._conn.execute(
        "UPDATE users SET email=? WHERE id=?", ("creator@example.invalid", world.owner.id)
    )
    return world


def assert_creation_audit(site, response, rule):
    workspace = response.json()["workspace"]
    records = [
        dict(row)
        for row in site.db.audit_entries(workspace["id"])
        if row["action"] == "workspace.create"
    ]
    assert len(records) == 1
    assert records[0]["decision"] == "ok"
    assert records[0]["user_id"] == site.owner.id
    assert records[0]["reason"] == rule
    assert "creator@example.invalid" not in str(records)
    assert workspace["name"] not in str(records)


def test_closed_instance_admin_can_create(closed):
    site = closed
    site.db._conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (site.owner.id,))
    assert site.owner.browser.get("/api/instance-admin/workspaces").status == 200
    response = create(site)
    assert response.status == 201, response.text
    assert_creation_audit(site, response, "instance admin")


@pytest.mark.parametrize("mode", ["closed", "", "invalid"])
def test_closed_allowlisted_person_can_create(closed, monkeypatch, mode):
    monkeypatch.setenv("COMMONPLACE_SIGNUP", mode)
    monkeypatch.setenv(
        "COMMONPLACE_SIGNUP_ALLOW_EMAILS",
        "other@example.invalid,\n CREATOR@EXAMPLE.INVALID \t third@example.invalid",
    )
    assert closed.owner.browser.get("/api/instance-admin/workspaces").status == 403
    response = create(closed)
    assert response.status == 201, response.text
    assert_creation_audit(closed, response, "allowlist")


@pytest.mark.parametrize(
    "allow", ["", "other@example.invalid", "creator+extra@example.invalid", "*@example.invalid"]
)
def test_closed_ordinary_member_and_workspace_owner_are_refused(closed, monkeypatch, allow):
    site = closed
    monkeypatch.setenv("COMMONPLACE_SIGNUP_ALLOW_EMAILS", allow)
    site.db.add_member(site.guest.workspace, site.owner.id, "member", actor=site.guest.id)
    before = len(site.db.workspaces())
    response = create(site)
    assert response.status == 403
    assert response.json() == {"error": "This instance is closed to new workspaces."}
    assert len(site.db.workspaces()) == before
    assert site.db._conn.execute("SELECT count(*) FROM workspace_creations").fetchone()[0] == 0


@pytest.mark.parametrize("admin,allowlisted", [(False, False), (True, False), (True, True)])
def test_open_signup_keeps_priority_and_behavior(closed, monkeypatch, admin, allowlisted):
    monkeypatch.setenv("COMMONPLACE_SIGNUP", "open")
    closed.db._conn.execute("UPDATE users SET is_admin=? WHERE id=?", (admin, closed.owner.id))
    if allowlisted:
        monkeypatch.setenv("COMMONPLACE_SIGNUP_ALLOW_EMAILS", "creator@example.invalid")
    response = create(closed)
    assert response.status == 201, response.text
    assert_creation_audit(closed, response, "signup open")


def test_admin_rule_precedes_allowlist_and_rechecks_revocation(closed, monkeypatch):
    site = closed
    site.db._conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (site.owner.id,))
    monkeypatch.setenv("COMMONPLACE_SIGNUP_ALLOW_EMAILS", "creator@example.invalid")
    response = create(site)
    assert response.status == 201
    assert_creation_audit(site, response, "instance admin")
    site.db._conn.execute("UPDATE users SET is_admin=0 WHERE id=?", (site.owner.id,))
    response = create(site)
    assert response.status == 201
    assert_creation_audit(site, response, "allowlist")
    monkeypatch.delenv("COMMONPLACE_SIGNUP_ALLOW_EMAILS")
    response = create(site)
    assert response.status == 403
    assert response.json() == {"error": "This instance is closed to new workspaces."}


@pytest.fixture(params=["instance admin", "allowlist"])
def admitted(closed, monkeypatch, request):
    if request.param == "instance admin":
        closed.db._conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (closed.owner.id,))
    else:
        monkeypatch.setenv("COMMONPLACE_SIGNUP_ALLOW_EMAILS", "creator@example.invalid")
    return closed


def test_closed_admission_still_requires_person_session_and_csrf(admitted):
    site = admitted
    assert create(site, token=owner_token(site, site.owner)).status == 403
    assert (
        create(site, body={"name": "Example", "request_id": "unused", "csrf": "wrong"}).status
        == 403
    )
    site.db._conn.execute("UPDATE users SET is_person=0 WHERE id=?", (site.owner.id,))
    assert create(site).status == 403
    assert len(site.db.workspaces()) == 2


@pytest.mark.parametrize("kind", ["agent", "pipeline", "connection"])
def test_closed_admission_never_accepts_bearer_authority(admitted, kind):
    site = admitted
    principal = replace(
        sessions.person_principal(site.db, site.owner.id, site.owner.workspace),
        auth=kind,
        person=True,
        connection_id="synthetic",
    )

    class Token:
        def authenticate(self, bearer):
            return principal

    site.gateway.authenticators = [Token()]
    assert create(site, token="synthetic").status == 403
    assert len(site.db.workspaces()) == 2


def test_closed_admission_keeps_owned_cap(admitted, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_WORKSPACE_LIMIT", "2")
    assert create(admitted).status == 201
    response = create(admitted)
    assert response.status == 403 and "2 owned workspaces" in response.text


@pytest.mark.parametrize("limit", ["PER_HOUR", "PER_DAY"])
def test_closed_admission_keeps_rate_limits(admitted, monkeypatch, limit):
    monkeypatch.setenv("COMMONPLACE_WORKSPACE_CREATIONS_" + limit, "1")
    assert create(admitted).status == 201
    response = create(admitted)
    assert response.status == 429
    assert dict(response.headers)["Retry-After"]
    assert len(admitted.db.workspaces()) == 3
