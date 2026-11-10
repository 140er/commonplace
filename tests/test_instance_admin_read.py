"""Metadata console contracts through genuine browser and connection credentials."""

import json
import socket
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from commonplace import sessions
from tests._commonplace_helpers import hub_fixture as hub_fixture
from tests.test_admin_api import agent, clock, cookie, member
from tests.test_admin_runtime_api import machine
from tests.test_commonplace_web import request

PREFIX = "/api/instance-admin/"
DENIED = {"error": "Instance administrator access required"}
KINDS = ("workspaces", "people", "connections", "spaces", "settings", "workspace_settings", "audit")


def route(hub, kind):
    return PREFIX + (
        f"workspaces/{hub.workspace}/{kind.removeprefix('workspace_')}"
        if kind in ("spaces", "workspace_settings")
        else kind
    )


def read(hub, kind="workspaces", headers=None, query=""):
    code, response, raw = request(hub, route(hub, kind) + query, headers=headers or cookie(hub))
    return code, json.loads(raw), response


@pytest.fixture
def foreign(hub):
    uid = hub.db.create_user("elsewhere@example.com", "Elsewhere")
    return hub.db.create_workspace("Elsewhere", uid)


def test_capability_uses_person_and_current_flag(hub, monkeypatch):
    headers = cookie(hub)
    payload = json.loads(request(hub, "/api/me", headers=headers)[2])
    assert payload.get("can_manage_instance") is True
    uid = member(hub)
    other = cookie(hub, "member@example.com")
    assert json.loads(request(hub, "/api/me", headers=other)[2])["can_manage_instance"] is False
    hub.db._conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (uid,))
    data = json.loads(request(hub, "/api/me", headers=other)[2])
    assert data["can_manage_instance"] is True and data["can_admin_instance"] is False
    assert data["can_admin"] is False
    hub.db._conn.execute("UPDATE users SET is_admin=0 WHERE id=?", (hub.admin,))
    assert json.loads(request(hub, "/api/me", headers=headers)[2])["can_manage_instance"] is False
    from commonplace.web import admin

    real = admin.select

    def broken(conn, context, sql, parameters=()):
        if sql.startswith("SELECT is_admin"):
            raise OSError("private-content")
        return real(conn, context, sql, parameters)

    monkeypatch.setattr(admin, "select", broken)
    code, _, raw = request(hub, "/api/me", headers=headers)
    assert code == 200
    data = json.loads(raw)
    assert data["can_admin"] is True
    assert data["can_manage_instance"] is data["can_admin_instance"] is False
    assert read(hub, headers=headers)[:2] == (503, {"error": "source_unavailable"})


def negative(hub, kind):
    if kind == "member":
        member(hub)
        return cookie(hub, "member@example.com")
    if kind == "owner":
        member(hub, role="owner")
        return cookie(hub, "member@example.com")
    if kind == "oauth":
        from tests.test_commonplace_oauth import Client

        client = Client(hub)
        grant = client.grant(client.register())
        hub.db._conn.execute("UPDATE connections SET expires_at=NULL WHERE auth='oauth'")
        return {"Authorization": "Bearer " + grant["access_token"]}
    if kind in ("machine", "worker"):
        return machine(hub)[3 if kind == "machine" else 4]
    cid, headers = agent(hub, kind)
    if kind == "child":
        parent, _ = agent(hub, "parent")
        hub.db._conn.execute(
            "UPDATE connections SET parent_id=?,started_by='agent' WHERE id=?", (parent, cid)
        )
    return headers


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "credential", ("member", "owner", "pat", "oauth", "child", "machine", "worker")
)
@pytest.mark.parametrize("filtered", (False, True))
def test_all_read_routes_negative_authority(hub, monkeypatch, kind, credential, filtered):
    headers = negative(hub, credential)
    query = "?workspace_id=" + hub.workspace if filtered else ""
    assert read(hub, kind, headers, query)[:2] == (403, DENIED)
    from commonplace import instance_admin
    from commonplace.gateway import _GatewayHandler as _Handler
    from commonplace.ratelimit import TokenBuckets as Limiter

    def forbidden(*args, **kwargs):
        raise AssertionError("Denied request opened a target")

    monkeypatch.setattr(instance_admin, "inventory", forbidden)
    if kind == "workspaces" and not filtered:
        hub.gateway.allowed_hosts = frozenset({"allowed.example"})
        assert read(hub, kind, headers)[0] == 421
        hub.gateway.allowed_hosts = None
        assert read(hub, kind, {**headers, "Content-Length": "1048577"})[0] == 413
        hub.gateway.rate_limits = True
        hub.gateway.limiter = Limiter(clock=lambda: 0)
        before = hub.db._conn.execute(
            "SELECT count(*) FROM audit_log WHERE action='instance_admin.denied'"
        ).fetchone()[0]
        for _ in range(600):
            assert read(hub, kind, headers, query)[:2] == (403, DENIED)
        code, data, response = read(hub, kind, headers, query)
        assert code == 429 and int(response["Retry-After"]) > 0
        assert (
            hub.db._conn.execute(
                "SELECT count(*) FROM audit_log WHERE action='instance_admin.denied'"
            ).fetchone()[0]
            - before
            == 600
        )
        row = hub.db._conn.execute(
            "SELECT workspace_id,connection_id,auth,target_ids FROM audit_log WHERE action='instance_admin.denied' ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        assert row["workspace_id"] is None and json.loads(row["target_ids"]) == []
        assert row["auth"] == (
            "session"
            if credential in ("member", "owner")
            else "oauth"
            if credential == "oauth"
            else "pat"
        )
        assert (row["connection_id"] is None) == (credential in ("member", "owner"))
    hub.gateway.rate_limits = False
    real = _Handler._send_json
    closed = []

    def checked(self, status, *args, **kwargs):
        if status in (403, 503):
            closed.append(self.close_connection)
        return real(self, status, *args, **kwargs)

    monkeypatch.setattr(_Handler, "_send_json", checked)
    sock = socket.create_connection(("127.0.0.1", hub.port), timeout=5)
    auth = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    sock.sendall(
        (
            f"GET {route(hub, kind)} HTTP/1.1\r\nHost: localhost\r\n{auth}Content-Length: 40\r\n\r\nGET /health HTTP/1.1\r\nHost: localhost\r\n\r\n"
        ).encode()
    )
    reply = b""
    while chunk := sock.recv(4096):
        reply += chunk
    sock.close()
    assert closed and all(closed)
    assert reply.count(b"HTTP/1.1") == 1 and b"403" in reply
    assert request(
        hub, route(hub, kind), "POST", "{}", {**headers, "Origin": "https://invalid.example"}
    )[0:1] == (403,)


@pytest.mark.parametrize("kind", KINDS)
def test_signed_out_read_routes_use_gateway_challenge(hub, kind):
    code, headers, raw = request(hub, route(hub, kind))
    assert code == 401
    assert json.loads(raw) == {"error": "unauthorized"} and "Bearer" in headers["WWW-Authenticate"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("member_admin", (False, True))
def test_nonmember_admin_and_member_admin_can_read_metadata(hub, foreign, kind, member_admin):
    headers = cookie(hub)
    if member_admin:
        hub.db._conn.execute("UPDATE memberships SET role='member' WHERE user_id=?", (hub.admin,))
    original = hub.workspace
    hub.workspace = foreign
    before = [tuple(r) for r in hub.db._conn.execute("SELECT * FROM memberships")]
    code, data, response = read(hub, kind, headers)
    assert code == 200
    assert response["Cache-Control"] == "no-store"
    assert before == [tuple(r) for r in hub.db._conn.execute("SELECT * FROM memberships")]
    assert (
        sessions.session_principal(hub.db, sessions.cookie_value(headers["Cookie"])).workspace_id
        == original
    )


def test_flag_is_server_fetched_every_request(hub, monkeypatch):
    headers = cookie(hub)
    assert read(hub, headers=headers)[0] == 200
    hub.db._conn.execute("UPDATE users SET is_admin=0 WHERE id=?", (hub.admin,))
    assert read(hub, headers={**headers, "X-Can-Manage-Instance": "true"})[:2] == (403, DENIED)
    hub.db._conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (hub.admin,))
    from commonplace.web import admin

    real = admin.select

    class Missing:
        def fetchone(self):
            return None

    monkeypatch.setattr(
        admin,
        "select",
        lambda c, ctx, sql, p=(): (
            Missing() if sql.startswith("SELECT is_admin") else real(c, ctx, sql, p)
        ),
    )
    assert read(hub, headers=headers)[:2] == (403, DENIED)


def test_workspaces_people_membership_pagination(hub, foreign):
    assert read(hub)[0] == 200
    orphan = hub.db.create_user("orphan@example.com", "No membership")
    uid = member(hub)
    hub.db.add_member(foreign, uid, "owner", actor=hub.db.members(foreign)[0]["id"])
    hub.db._conn.execute("UPDATE users SET disabled_at='2026-01-01T00:00:00Z' WHERE id=?", (uid,))
    first = read(hub, "people", query="?limit=2")[1]
    second = read(hub, "people", query="?limit=2&cursor=" + first["next_cursor"])[1]
    third = read(hub, "people", query="?limit=2&cursor=" + second["next_cursor"])[1]
    rows = first["items"] + second["items"] + third["items"]
    assert len(rows) == first["count"] == 5
    assert len({(r["id"], r["workspace_id"]) for r in rows}) == 5
    assert any(r["id"] == orphan and r["role"] is None for r in rows)
    selected = read(hub, "people", query="?workspace_id=" + foreign + "&role=owner&disabled=true")[
        1
    ]
    assert len(selected["items"]) == 1 and selected["items"][0]["id"] == uid
    assert all(r["removed_at"] is None and "memberships" not in r for r in rows)
    assert read(hub, query="?workspace_id=" + foreign)[1]["count"] == 1
    # Stable ordering is by creation time before opaque random identity.
    for index, row in enumerate(sorted(rows, key=lambda r: r["id"], reverse=True)):
        hub.db._conn.execute(
            "UPDATE users SET created_at=? WHERE id=?",
            (f"2000-01-{index + 1:02d}T00:00:00Z", row["id"]),
        )
    people = read(hub, "people")[1]["items"]
    assert [(r["created_at"], r["id"]) for r in people] == sorted(
        (r["created_at"], r["id"]) for r in people
    )
    for index, wid in enumerate(sorted((hub.workspace, foreign), reverse=True)):
        hub.db._conn.execute(
            "UPDATE workspaces SET created_at=? WHERE id=?",
            (f"2000-01-{index + 1:02d}T00:00:00Z", wid),
        )
    workspaces = read(hub)[1]["items"]
    assert [(r["created_at"], r["id"]) for r in workspaces] == sorted(
        (r["created_at"], r["id"]) for r in workspaces
    )


def test_connection_metadata_no_secrets_or_taxonomy(hub, caplog):
    cid, _ = agent(hub, "safe", client="https://private.example/?token=hidden")
    machine(hub)
    code, data, _ = read(hub, "connections")
    assert code == 200
    row = next(r for r in data["items"] if r["id"] == cid)
    assert row["state"] == "active" and row["paused_at"] is None
    assert any(r["machine_id"] for r in data["items"])
    assert set(row["scopes"]) == {"memory:read", "admin"}
    hub.db._conn.execute("UPDATE connections SET policy='malformed' WHERE id=?", (cid,))
    assert next(r for r in read(hub, "connections")[1]["items"] if r["id"] == cid)["scopes"] == []
    raw = json.dumps(data) + caplog.text
    assert all(
        s not in raw
        for s in ("token_hash", "token_hint", "read_wings", "notes", "private.example", "hidden")
    )


def seed_board(hub, workspace):
    from commonplace.boards.store import BoardStore

    path = Path(hub.supervisor.workspace_dir(workspace))
    store = BoardStore(path)
    with store.connection() as conn:
        conn.execute(
            "INSERT INTO boards(id,slug,name,wing,stamp_user,stamp_trust,created_at) VALUES (1,'garden','Garden','content-sentinel',?,'trusted','2026-01-01T00:00:00Z')",
            (hub.admin,),
        )
        conn.execute(
            "INSERT INTO boards(id,slug,name,wing,stamp_user,stamp_trust,created_at,archived) VALUES (2,'old','Old','content-sentinel',?,'trusted','2026-01-01T00:00:00Z',1)",
            (hub.admin,),
        )
    return path


def test_spaces_and_settings_read_only_absent_store(hub, foreign, monkeypatch):
    headers = cookie(hub)
    hub.workspace = foreign
    assert read(hub, "spaces", headers)[0] == 200
    path = Path(hub.supervisor.workspace_dir(foreign))
    assert not path.exists()
    assert read(hub, "spaces", headers)[1]["state"] == "unknown"
    seed_board(hub, foreign)
    from commonplace import instance_admin
    from commonplace.boards import service, store
    from commonplace.web import admin

    before = (path / "boards.sqlite3").read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("A read initialized domain state")

    monkeypatch.setattr(service.BoardService, "__init__", forbidden)
    monkeypatch.setattr(store.BoardStore, "__init__", forbidden)
    monkeypatch.setattr(hub.gateway, "workspace_dir", forbidden)
    real = admin.read_store
    statements = []

    @contextmanager
    def checked(*args):
        with real(*args) as conn:
            if conn:
                assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
                conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(admin, "read_store", checked)
    code, data, _ = read(hub, "spaces", headers)
    assert code == 200 and [r["archived"] for r in data["items"]] == [False, True]
    assert read(hub, "workspace_settings", headers)[0] == 200
    assert read(hub, "settings", headers)[1]["editable"] is False
    assert "content-sentinel" not in json.dumps(data)
    assert all(
        not s.upper().startswith(("INSERT", "UPDATE", "DELETE", "BEGIN")) for s in statements
    )
    assert before == (path / "boards.sqlite3").read_bytes()
    assert "wing" not in " ".join(statements).lower()
    assert instance_admin.ROUTES
    with (path / "boards.sqlite3").open("wb") as corrupt:
        corrupt.write(b"content-sentinel: corrupt source")
    assert read(hub, "spaces", headers)[:2] == (503, {"error": "source_unavailable"})
    assert read(hub, "workspace_settings", headers)[0] == 503


def test_audit_filters_projection_and_snapshot_cursor(hub, foreign):
    assert read(hub, "audit")[0] == 200
    for _ in range(4):
        hub.db.audit(
            action="instance_admin.read.spaces",
            decision="allow",
            user_id=hub.admin,
            workspace_id=foreign,
            reason="content-sentinel",
            wing="content-sentinel",
            target_ids=["content-sentinel", "workspace:" + foreign],
        )
    hub.db.audit(action="historic-content-sentinel", decision="raw", reason="content-sentinel")
    first = read(hub, "audit", query="?limit=2")[1]
    ceiling = first["snapshot"]
    hub.db.audit(action="instance_admin.read.people", decision="allow", user_id=hub.admin)
    more = read(hub, "audit", query="?limit=2&cursor=" + first["next_cursor"])[1]
    assert more["snapshot"] == ceiling and all(r["seq"] <= ceiling for r in more["items"])
    assert all("content-sentinel" not in json.dumps(d) for d in (first, more))
    query = f"?workspace_id={foreign}&actor_user_id={hub.admin}&action=instance_admin.read.spaces&decision=allow&from=2000-01-01T00:00:00Z&to=2099-01-01T00:00:00Z"
    assert read(hub, "audit", query=query)[1]["count"] == 4
    assert read(hub, "audit", query="?actor_connection_id=conn_0000000000000000")[1]["count"] == 0
    assert all("reason" not in r and "wing" not in r for r in first["items"])
    for query in (
        "?workspace_id=" + foreign,
        "?actor_user_id=" + hub.admin,
        "?action=instance_admin.read.spaces",
        "?decision=allow",
        "?from=2000-01-01T00:00:00Z",
        "?to=2099-01-01T00:00:00Z",
    ):
        code, data, _ = read(hub, "audit", query=query)
        assert code == 200 and "content-sentinel" not in json.dumps(data)


@pytest.mark.parametrize("kind", KINDS)
def test_every_read_is_safely_audited(hub, kind, monkeypatch):
    headers = cookie(hub)
    code, data, _ = read(hub, kind, headers)
    assert code == 200
    row = hub.db._conn.execute("SELECT * FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    assert row["action"] == "instance_admin.read." + kind
    assert row["user_id"] == hub.admin and row["connection_id"] is None and row["auth"] == "session"
    assert row["decision"] == "allow" and row["reason"] == "ok"
    assert row["workspace_id"] == (
        hub.workspace if kind in ("spaces", "workspace_settings") else None
    )
    if kind == "audit":
        assert row["seq"] > data["snapshot"]

    def broken(**kwargs):
        raise OSError("content-sentinel")

    monkeypatch.setattr(hub.db, "audit", broken)
    assert read(hub, kind, headers)[:2] == (503, {"error": "audit_unavailable"})


@pytest.mark.parametrize("kind", KINDS)
def test_query_path_and_object_isolation(hub, kind):
    assert read(hub, kind)[0] == 200
    for query in (
        "?unknown=x",
        "?limit=1&limit=2",
        "?limit=0",
        "?cursor=invalid",
        "?workspace_id=../bad",
        "?is_admin=true",
    ):
        assert read(hub, kind, query=query)[0] == 400
    first = read(hub, "people", query="?limit=1")[1]
    if first["next_cursor"]:
        assert read(hub, "connections", query="?limit=1&cursor=" + first["next_cursor"])[0] == 400
    assert (
        request(
            hub, PREFIX + "workspaces/" + hub.workspace + "%2fextra/spaces", headers=cookie(hub)
        )[0]
        == 400
    )
    assert (
        request(hub, PREFIX + "workspaces/ws_0000000000000000/spaces", headers=cookie(hub))[0]
        == 404
    )
    member(hub)
    assert read(hub, kind, cookie(hub, "member@example.com"), "?unknown=x")[:2] == (403, DENIED)


@pytest.mark.parametrize("kind", KINDS)
def test_read_deadline_clock_and_privacy_faults(hub, monkeypatch, kind, caplog):
    assert read(hub, kind)[0] == 200
    from commonplace import instance_admin
    from commonplace.gateway import _GatewayHandler
    from commonplace.web import admin

    closed = []
    send = _GatewayHandler._send_json

    def checked(handler, status, *args, **kwargs):
        if status in (429, 503):
            closed.append((status, handler.close_connection))
        return send(handler, status, *args, **kwargs)

    monkeypatch.setattr(_GatewayHandler, "_send_json", checked)
    headers = {**cookie(hub), "Content-Length": "40"}
    hub.gateway.rate_limits = True
    with monkeypatch.context() as limiter:
        limiter.setattr(hub.gateway.limiter, "check", lambda policies: 60)
        assert read(hub, kind, headers)[0] == 429
    hub.gateway.rate_limits = False

    clock(hub)
    real = instance_admin.inventory

    def slow(*args):
        result = real(*args)
        hub.gateway.admin_clock.monotonic = lambda: 3
        return result

    monkeypatch.setattr(instance_admin, "inventory", slow)
    assert read(hub, kind)[:2] == (503, {"error": "source_unavailable"})
    clock(hub)
    monkeypatch.setattr(instance_admin, "inventory", real)
    held = threading.Event()
    release = threading.Event()

    def holder():
        with hub.db._lock:
            held.set()
            release.wait(5)

    thread = threading.Thread(target=holder)
    # Enter the bounded audit seam after HTTP authentication, avoiding its existing lock.
    original = hub.db.audit_bounded

    def blocked(context, **kwargs):
        thread.start()
        assert held.wait(3)
        try:
            context.deadline = context.clock.monotonic() + 0.01
            return original(context, **kwargs)
        finally:
            release.set()
            thread.join(3)

    monkeypatch.setattr(hub.db, "audit_bounded", blocked)
    assert read(hub, kind)[:2] == (503, {"error": "audit_unavailable"})

    def broken(*args, **kwargs):
        raise OSError("content-sentinel")

    monkeypatch.setattr(admin, "read_control", broken)
    assert read(hub, kind, headers)[0] == 503
    assert "content-sentinel" not in json.dumps(read(hub, kind)[1]) + caplog.text
    assert {status for status, _ in closed} == {429, 503}
    assert all(value for _, value in closed)


def test_console_admission_not_home_account_quota(hub, foreign, monkeypatch):
    headers = cookie(hub)
    hub.workspace = foreign
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    from commonplace import plans

    plans.set_plan(hub.db, hub.admin, "free", actor=hub.admin)
    monkeypatch.setattr(hub.gateway.storage_meter, "used", lambda *args: 10**12)
    assert read(hub, "spaces", headers)[0] == 200
    assert (
        request(hub, "/api/spaces", "POST", "{}", {**headers, "Content-Type": "application/json"})[
            0
        ]
        == 507
    )
    code, _, raw = request(hub, route(hub, "spaces"), "POST", "{}", headers)
    assert code == 403 and json.loads(raw)["error"] == "JSON and same-origin request required"
    assert (
        request(
            hub, route(hub, "spaces"), "POST", "{}", {**headers, "Content-Type": "application/json"}
        )[0]
        == 404
    )


def test_absent_store_workspace_settings_metadata(hub, foreign, monkeypatch):
    from commonplace import plans

    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    owner = hub.db.members(foreign)[0]["id"]
    hub.db._conn.execute("UPDATE workspaces SET personal_of=? WHERE id=?", (owner, foreign))
    plans.set_plan(hub.db, owner, "free", actor=owner)
    headers = cookie(hub)
    hub.workspace = foreign
    path = Path(hub.supervisor.workspace_dir(foreign))
    assert not path.exists()
    code, data, _ = read(hub, "workspace_settings", headers)
    assert code == 200
    assert data.get("workspace") == {"id": foreign, "name": "Elsewhere", "personal_of": owner}
    assert data["state"] == "unknown"
    assert data["limits"]["source"] == "account_plan"
    assert data["limits"]["account_id"] == owner
    assert data["limits"]["effective_plan"] == "free"
    assert data["limits"]["storage_bytes"] == plans.PLANS["free"].storage_bytes
    assert data["limits"]["transcription_seconds"] == plans.PLANS["free"].transcription_seconds
    assert data["orchestration_enabled"] is False
    assert not path.exists()


@pytest.mark.parametrize("state", ("active", "revoked", "expired"))
def test_connection_state_positive_filter(hub, state):
    cid, _ = agent(hub, "state-filter")
    if state == "revoked":
        hub.db._conn.execute(
            "UPDATE connections SET revoked_at='2000-01-01T00:00:00Z' WHERE id=?", (cid,)
        )
    elif state == "expired":
        hub.db._conn.execute(
            "UPDATE connections SET expires_at='2000-01-01T00:00:00Z' WHERE id=?", (cid,)
        )
    code, data, _ = read(hub, "connections", query="?state=" + state)
    assert code == 200
    assert data["count"] >= 1
    assert any(r["id"] == cid and r["state"] == state for r in data["items"])
    assert all(r["state"] == state for r in data["items"])


def test_audit_real_connection_positive_filter(hub):
    cid, _ = agent(hub, "audit-filter")
    hub.db.audit(action="instance_admin.read.people", decision="allow", connection_id=cid)
    code, data, _ = read(
        hub, "audit", query="?actor_connection_id=" + cid + "&action=instance_admin.read.people"
    )
    assert code == 200
    assert data["count"] == 1
    assert data["items"][0]["actor_connection_id"] == cid


@pytest.mark.parametrize("credential", ("pat", "oauth", "machine"))
def test_non_session_manage_capability_false(hub, credential):
    headers = negative(hub, credential)
    code, _, raw = request(hub, "/api/me", headers=headers)
    if credential == "machine":
        assert read(hub, headers=headers)[:2] == (403, DENIED)
        from commonplace import instance_admin
        from commonplace.web import admin

        assert code == 404 and json.loads(raw) == {"error": "not found"}
        principal = hub.gateway.authenticate(headers["Authorization"])
        assert principal is not None and principal.runner == "machine"
        assert (
            instance_admin.flag(hub.gateway, principal, admin.ReadContext(hub.gateway.admin_clock))
            is False
        )
    else:
        assert code == 200
        assert json.loads(raw).get("can_manage_instance") is False


@pytest.mark.parametrize(
    "query", ("?workspace_id=ws_0000000000000000", "?limit=20", "?cursor=invalid")
)
def test_instance_settings_reject_filters_with_target_free_refusal(hub, query):
    code, data, _ = read(hub, "settings", query=query)
    assert code == 400 and data == {"error": "invalid_request"}
    row = hub.db._conn.execute("SELECT * FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    assert row["workspace_id"] is None
    assert row["decision"] == "refuse"
    assert json.loads(row["target_ids"]) == []


def test_workspace_validation_refusal_has_no_targets(hub):
    assert read(hub, "workspace_settings", query="?unknown=x")[0] == 400
    row = hub.db._conn.execute("SELECT * FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    assert row["decision"] == "refuse"
    assert json.loads(row["target_ids"]) == []
