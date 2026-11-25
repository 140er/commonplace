"""Strict person authority and abuse boundaries for additional workspaces."""

import uuid
from dataclasses import replace

import pytest

from commonplace import invites, sessions
from tests.test_commonplace_invites import world  # noqa: F401
from tests.test_workspace_creation import create


@pytest.fixture
def creation(world, monkeypatch):  # noqa: F811
    monkeypatch.setenv("COMMONPLACE_SIGNUP", "open")
    return world


def test_creation_requires_session_bound_csrf(creation):
    site = creation
    body = {"name": "Another", "request_id": str(uuid.uuid4())}
    origin = {"Origin": f"http://127.0.0.1:{site.port}"}
    assert site.owner.browser.post("/api/workspaces", body, headers=origin).status == 403
    own = site.owner.browser.get("/api/workspaces").json()["csrf"]
    other = site.guest.browser.get("/api/workspaces").json()["csrf"]
    assert own != other
    for token in ["", "wrong", "wrongé", other]:
        assert create(site, body={**body, "csrf": token}).status == 403
    assert create(site, body={**body, "csrf": own}).status == 201


def test_owned_cap_counts_cli_workspaces_and_not_memberships(creation, monkeypatch):
    site = creation
    monkeypatch.setenv("COMMONPLACE_WORKSPACE_LIMIT", "2")
    site.db.add_member(site.guest.workspace, site.owner.id, "member", actor=site.guest.id)
    assert create(site).status == 201
    refused = create(site)
    assert refused.status == 403 and "2 owned workspaces" in refused.text
    monkeypatch.setenv("COMMONPLACE_WORKSPACE_LIMIT", "3")
    assert create(site).status == 201


def test_default_hourly_limit(creation):
    for _ in range(3):
        assert create(creation).status == 201
    response = create(creation)
    assert response.status == 429
    assert dict(response.headers)["Retry-After"]


def test_instance_daily_limit_is_shared_and_durable(creation, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_WORKSPACE_CREATIONS_PER_DAY", "1")
    assert create(creation).status == 201
    response = create(creation, browser=creation.guest.browser)
    assert response.status == 429 and "server" in response.text


def test_disk_low_creates_no_rows_or_directory(creation, monkeypatch):
    from mempalace import storage_guard

    site = creation
    monkeypatch.setenv("MEMPALACE_MIN_FREE_BYTES", "100")
    monkeypatch.setattr(storage_guard, "free_bytes", lambda path: 1)
    before = list(site.gateway.supervisor.root.glob("*"))
    response = create(site)
    assert response.status == 507 and "try again" in response.text.lower()
    assert dict(response.headers)["Retry-After"]
    assert len(site.db.workspaces()) == 2
    assert list(site.gateway.supervisor.root.glob("*")) == before


def test_pause_refuses_without_waiting(creation):
    site = creation
    site.gateway.supervisor.pause(drain_s=0)
    try:
        response = create(site)
        assert response.status == 503 and "try again" in response.text.lower()
    finally:
        site.gateway.supervisor.resume()
    assert len(site.db.workspaces()) == 2


def test_slug_is_derived_unique_and_workspace_directory_exists(creation):
    first, second = [create(creation).json()["workspace"] for _ in range(2)]
    assert first["slug"].startswith("new-workspace-")
    assert first["slug"] != second["slug"]
    assert creation.gateway.supervisor.workspace_dir(first["id"]).is_dir()


def test_service_account_session_is_refused(creation):
    site = creation
    with site.db._lock:
        site.db._conn.execute("UPDATE users SET is_person=0 WHERE id=?", (site.owner.id,))
    assert create(site).status == 403
    assert len(site.db.workspaces()) == 2


@pytest.mark.parametrize("kind", ["pat", "oauth", "agent", "connection", "pipeline", "mcp"])
def test_token_kinds_cannot_create_even_with_person_flag(creation, kind):
    site = creation
    principal = replace(
        sessions.person_principal(site.db, site.owner.id, site.owner.workspace),
        auth=kind,
        connection_id="synthetic",
        person=True,
    )

    class Token:
        def authenticate(self, bearer):
            return principal

    site.gateway.authenticators = [Token()]
    assert create(site, token="synthetic").status == 403
    assert len(site.db.workspaces()) == 2


def test_default_cap_is_five_and_only_counts_owners(creation):
    site = creation
    for index in range(4):
        site.db.create_workspace(f"CLI {index}", site.owner.id)
    response = create(site)
    assert response.status == 403 and "5 owned workspaces" in response.text


def test_plan_workspace_allowance_uses_existing_plan_hook(creation, monkeypatch):
    from commonplace import plans

    site = creation
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    monkeypatch.setenv("COMMONPLACE_WORKSPACE_LIMIT", "8")
    for index in range(4):
        site.db.create_workspace(f"CLI {index}", site.owner.id)
    response = create(site)
    assert response.status == 403 and "Your plan allows 5" in response.text
    plans.set_plan(site.db, site.owner.id, "paid", actor=site.owner.id)
    assert create(site).status == 201
    assert plans.get_plan(site.db, site.owner.id) == plans.PAID


def test_cap_is_atomic_under_simultaneous_requests(creation, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setenv("COMMONPLACE_WORKSPACE_LIMIT", "2")
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: create(creation), range(2)))
    assert sorted(response.status for response in responses) == [201, 403]


def test_default_instance_limit_survives_reopen(creation, monkeypatch):
    from commonplace.control_db import ControlDB

    site = creation
    monkeypatch.setenv("COMMONPLACE_WORKSPACE_LIMIT", "30")
    monkeypatch.setenv("COMMONPLACE_WORKSPACE_CREATIONS_PER_HOUR", "30")
    for index in range(20):
        invites.create_workspace(
            site.db, site.owner.id, name=f"Example {index}", request_id=str(uuid.uuid4())
        )
    reopened = ControlDB(site.db.path)
    try:
        with pytest.raises(invites.WorkspaceCreationError) as refused:
            invites.create_workspace(
                reopened, site.guest.id, name="Another", request_id=str(uuid.uuid4())
            )
        assert refused.value.status == 429 and "server" in str(refused.value)
    finally:
        reopened.close()


def test_backup_freeze_and_maintenance_fence_are_retryable(creation):
    from commonplace.admission import GATE
    from commonplace.backup import fence_lock

    GATE.freeze(timeout=0, lease_s=5)
    try:
        response = create(creation)
        assert response.status == 503 and dict(response.headers)["Retry-After"] == "30"
    finally:
        GATE.thaw()
    with fence_lock(creation.gateway.supervisor.root.parent, exclusive=True):
        response = create(creation)
        assert response.status == 503
    assert len(creation.db.workspaces()) == 2
    assert create(creation).status == 201


def test_created_workspace_is_visible_in_instance_admin_without_content(creation):
    site = creation
    site.db._conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (site.owner.id,))
    workspace = create(site).json()["workspace"]
    response = site.owner.browser.get("/api/instance-admin/workspaces")
    assert response.status == 200, response.text
    assert workspace["id"] in response.text
    records = [
        row for row in site.db.audit_entries(workspace["id"]) if row["action"] == "workspace.create"
    ]
    assert len(records) == 1 and records[0]["user_id"] == site.owner.id and records[0]["at"]
    assert workspace["name"] not in str(dict(records[0]))


@pytest.mark.parametrize("name", ["bad\nname", "bad\tname", "bad<script>", "x" * 129])
def test_plain_validation_never_creates_a_workspace(creation, name):
    response = create(creation, body={"name": name, "request_id": str(uuid.uuid4())})
    assert response.status == 400
    assert "error" in response.json() and len(creation.db.workspaces()) == 2


def test_created_workspace_accepts_cards_and_real_memory_search(creation, config, kg, monkeypatch):
    from tests._commonplace_helpers import InProcessLauncher
    from tests._mcp_server_helpers import _patch_mcp_server

    site = creation
    workspace = create(site).json()["workspace"]["id"]
    assert site.owner.browser.post("/api/workspaces/switch", {"workspace": workspace}).status == 200
    response = site.owner.browser.post(
        "/api/boards/commonplace/cards", {"title": "A separate card"}
    )
    assert response.status == 201, response.text
    _patch_mcp_server(monkeypatch, config, kg)
    site.gateway.supervisor.launcher = InProcessLauncher(monkeypatch)
    principal = sessions.person_principal(site.db, site.owner.id, workspace)
    saved = site.gateway.call_memory_tool(
        principal,
        "mempalace_add_drawer",
        {"wing": "shared", "room": "example", "content": "Verbatim violet lantern marker."},
    )
    assert saved["success"]
    found = site.gateway.call_memory_tool(
        principal, "mempalace_search", {"query": "violet lantern"}
    )
    assert any("Verbatim violet lantern marker." in str(row) for row in found["results"])
    assert (
        site.owner.browser.post(
            "/api/workspaces/switch", {"workspace": site.owner.workspace}
        ).status
        == 200
    )
    assert "A separate card" not in site.owner.browser.get("/api/cards").text


def test_cli_workspace_uses_signup_default_board(creation, capsys):
    from commonplace.boards.service import BoardService
    from commonplace.cli import main
    from commonplace.extensions import ToolContext

    site = creation
    assert (
        main(
            [
                "--data",
                str(site.gateway.supervisor.root.parent),
                "workspace",
                "add",
                "CLI example",
                "--owner",
                site.owner.id,
            ]
        )
        == 0
    )
    wid = capsys.readouterr().out.strip()
    principal = sessions.person_principal(site.db, site.owner.id, wid)
    boards = BoardService(ToolContext(principal=principal, gateway=site.gateway)).board_list()
    assert [board["slug"] for board in boards] == ["commonplace"]
    assert site.db.workspace(wid)["initial_space_pending"] == 0


def test_interrupted_default_board_initialization_is_retryable(creation, monkeypatch):
    from commonplace.control_db import ControlError
    from commonplace.social import accounts

    site = creation
    body = {"name": "Retryable", "request_id": str(uuid.uuid4())}
    original = accounts.initialize_workspace
    with monkeypatch.context() as patch:

        def interrupted(*args, **kwargs):
            raise ControlError("Workspace initialization interrupted. Try again.")

        patch.setattr(accounts, "initialize_workspace", interrupted)
        assert create(site, body=body).status == 400
    assert len(site.db.workspaces()) == 3
    [pending] = [row for row in site.db.workspaces() if row["name"] == "Retryable"]
    assert pending["initial_space_pending"] == 1
    assert accounts.initialize_workspace is original
    response = create(site, body=body)
    assert response.status == 200
    assert response.json()["workspace"]["id"] == pending["id"]
    assert len(site.db.workspaces()) == 3
    assert site.db.workspace(pending["id"])["initial_space_pending"] == 0


def test_provisioning_never_removes_a_preexisting_directory(creation, monkeypatch):
    from commonplace import control_db

    site = creation
    wid = "ws_1111111111111111"
    directory = site.gateway.supervisor.workspace_dir(wid)
    directory.mkdir(parents=True)
    marker = directory / "preserve.txt"
    marker.write_text("Existing data")
    monkeypatch.setattr(control_db, "_new_id", lambda prefix: wid)
    response = create(site)
    assert response.status == 500
    assert marker.read_text() == "Existing data"
    assert len(site.db.workspaces()) == 2
    assert site.db._conn.execute("SELECT count(*) FROM workspace_creations").fetchone()[0] == 0


def test_old_account_schema_migrates_without_changing_memberships(tmp_path):
    import sqlite3

    from commonplace.control_db import ControlDB

    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL DEFAULT '', is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, disabled_at TEXT);
            CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                personal_of TEXT, created_at TEXT NOT NULL);
            INSERT INTO users(id,email,created_at) VALUES ('usr_old','fixture@example.test','2026-01-01');
            INSERT INTO workspaces VALUES ('ws_old','Existing','usr_old','2026-01-01');
        """)
    db = ControlDB(str(path))
    try:
        assert db.user("usr_old")["is_person"] == 1
        assert db.workspace("ws_old")["name"] == "Existing"
        service = db.create_user("service@example.test", is_person=False)
        assert db.user(service)["is_person"] == 0
    finally:
        db.close()
