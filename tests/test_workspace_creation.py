"""A person creates a separate workspace through their own session."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from commonplace import invites, plans, sessions
from commonplace.control_db import ControlDB
from tests.test_commonplace_invites import Browser, owner_token, world  # noqa: F401


@pytest.fixture
def creation(world, monkeypatch):  # noqa: F811
    monkeypatch.setenv("COMMONPLACE_SIGNUP", "open")
    return world


def create(site, *, body=None, browser=None, headers=None, token=None):
    return (browser or site.owner.browser).post(
        "/api/workspaces",
        body if body is not None else {"name": "New workspace", "request_id": str(uuid.uuid4())},
        headers={"Origin": f"http://127.0.0.1:{site.port}", **(headers or {})},
        token=token,
    )


def test_create_is_empty_owned_isolated_and_switches(creation):
    site = creation
    old = site.owner.workspace
    site.db.add_member(old, site.guest.id, "member", actor=site.owner.id)
    before = site.owner.browser.post("/api/boards", {"slug": "private", "name": "Private"})
    assert before.status == 201
    card = site.owner.browser.post("/api/boards/private/cards", {"title": "OLD_CONTENT"})
    assert card.status == 201
    response = create(site)
    assert response.status == 201, response.text
    workspace = response.json()["workspace"]
    wid = workspace["id"]
    assert workspace == {"id": wid, "name": "New workspace", "role": "owner"}
    assert site.owner.browser.me().json()["workspace"] == old
    assert site.db.role(wid, site.owner.id) == "owner"
    assert site.db.role(wid, site.guest.id) is None
    assert wid not in str(site.guest.browser.get("/api/workspaces").json())
    assert site.guest.browser.post("/api/workspaces/switch", {"workspace": wid}).status == 404
    assert site.owner.browser.post("/api/workspaces/switch", {"workspace": wid}).status == 200
    assert site.owner.browser.get("/api/boards").json() == {"boards": []}
    assert site.owner.browser.get("/api/boards/private/cards").status == 404
    assert "OLD_CONTENT" not in site.owner.browser.get("/api/cards").text
    assert site.owner.browser.post("/api/boards", {"slug": "new", "name": "New"}).status == 201
    assert site.owner.browser.post("/api/workspaces/switch", {"workspace": old}).status == 200
    assert site.owner.browser.get("/api/boards/new/cards").status == 404
    assert (
        site.owner.browser.get("/api/boards/private/cards").json()["cards"][0]["title"]
        == "OLD_CONTENT"
    )
    assert any(row["action"] == "workspace.create" for row in site.db.audit_entries(wid))


def test_duplicate_request_creates_one_even_concurrently(creation):
    site = creation
    body = {"name": "One workspace", "request_id": str(uuid.uuid4())}
    before = len(site.db.workspaces())
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: create(site, body=body), range(2)))
    assert sorted(r.status for r in responses) == [200, 201]
    assert responses[0].json() == responses[1].json()
    assert len(site.db.workspaces()) == before + 1
    conflict = create(site, body={**body, "name": "Different name"})
    assert conflict.status == 409 and "request" in conflict.json()["error"].lower()


@pytest.mark.parametrize("value", ["closed", "", "invalid"])
def test_closed_signup_refuses_with_reason(creation, monkeypatch, value):
    site = creation
    before = len(site.db.workspaces())
    monkeypatch.setenv("COMMONPLACE_SIGNUP", value)
    response = create(site)
    assert response.status == 403
    assert response.json()["error"] == "This instance is closed to new workspaces."
    assert len(site.db.workspaces()) == before


def test_creation_rate_limit_is_per_person_and_plain(creation):
    site = creation
    for _ in range(5):
        assert create(site).status == 201
    before = len(site.db.workspaces())
    refused = create(site)
    assert refused.status == 429
    assert "Try again in an hour" in refused.json()["error"]
    assert len(site.db.workspaces()) == before
    assert create(site, browser=site.guest.browser).status == 201
    assert any(r["decision"] == "deny" for r in site.db.audit_entries(site.owner.workspace))


@pytest.mark.parametrize("name", ["", "../bad", "bad/name", "x" * 256, None, 123, "bad\x00name"])
def test_names_use_shared_validation(creation, name):
    response = create(creation, body={"name": name, "request_id": str(uuid.uuid4())})
    assert response.status == 400
    assert len(creation.db.workspaces()) == 2


@pytest.mark.parametrize(
    "headers", [{"Origin": None}, {"Origin": "https://other.example.test"}, {"Origin": "null"}]
)
def test_creation_requires_same_origin(creation, headers):
    assert create(creation, headers=headers).status == 403
    assert len(creation.db.workspaces()) == 2


def test_only_actual_person_session_can_create(creation):
    site = creation
    assert create(site, browser=Browser(site.port)).status == 401
    token = owner_token(site, site.owner)
    assert create(site, token=token).status == 403
    assert len(site.db.workspaces()) == 2


def test_existing_plan_is_preserved_and_missing_plan_uses_signup_hook(creation, monkeypatch):
    site = creation
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    plans.set_plan(site.db, site.owner.id, "paid", actor=site.owner.id)
    assert create(site).status == 201
    assert plans.get_plan(site.db, site.owner.id) == plans.PAID
    assert create(site, browser=site.guest.browser).status == 201
    assert plans.get_plan(site.db, site.guest.id) == plans.FREE


def test_bad_request_id_and_unknown_fields_create_nothing(creation):
    for body in [
        {"name": "Name"},
        {"name": "Name", "request_id": "bad"},
        {"name": "Name", "request_id": str(uuid.uuid4()), "owner": "other"},
    ]:
        assert create(creation, body=body).status == 400
    assert len(creation.db.workspaces()) == 2


def test_plan_hook_refusal_rolls_back_and_explains_reason(creation, monkeypatch):
    def refuse(*args, **kwargs):
        raise plans.LimitReached("Your plan does not allow another workspace.")

    monkeypatch.setattr(plans, "assign_signup_plan", refuse)
    response = create(creation)
    assert response.status == 403
    assert response.json()["error"] == "Your plan does not allow another workspace."
    assert len(creation.db.workspaces()) == 2
    assert creation.db._conn.execute("SELECT count(*) FROM workspace_creations").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["agent", "machine", "runner", "oauth", "app"])
def test_bearer_authority_never_substitutes_for_person_session(creation, kind):
    site = creation
    # Include a person-shaped app grant: even that authority is not a browser session.
    principal = replace(
        sessions.person_principal(site.db, site.owner.id, site.owner.workspace),
        auth=kind,
        person=kind in ("app", "oauth"),
        connection_id="synthetic-connection",
    )

    class Token:
        def authenticate(self, bearer):
            return principal if bearer == "synthetic-token" else None

    site.gateway.authenticators = [Token()]
    assert create(site, token="synthetic-token").status == 403
    assert len(site.db.workspaces()) == 2


def test_request_and_rate_limit_survive_reopening_control_database(creation):
    site = creation
    request_id = str(uuid.uuid4())
    response = create(site, body={"name": "Persistent", "request_id": request_id})
    assert response.status == 201
    reopened = ControlDB(site.db.path)
    try:
        workspace, made = invites.create_workspace(
            reopened, site.owner.id, name="Persistent", request_id=request_id
        )
        assert not made and workspace == response.json()["workspace"]
        for _ in range(4):
            invites.create_workspace(
                reopened, site.owner.id, name="More", request_id=str(uuid.uuid4())
            )
        assert create(site).status == 429
    finally:
        reopened.close()


def test_same_second_creation_preserves_original_signin_home(creation, monkeypatch):
    from commonplace import control_db

    site = creation
    first = site.db.workspace(site.owner.workspace)
    monkeypatch.setattr(control_db, "_now", lambda: control_db._parse(first["created_at"]))
    monkeypatch.setattr(control_db, "_new_id", lambda prefix: "ws_0000000000000000")
    assert create(site).status == 201
    assert sessions.home_workspace(site.db, site.owner.id) == site.owner.workspace
    assert plans.workspace_account(site.db, "ws_0000000000000000") == site.owner.id
