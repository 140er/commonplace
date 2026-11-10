"""W4 routes through a real gateway, with isolated contract-shaped fake APIs."""

import http.client
import io
import json
import logging
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from commonplace import events, sessions
from commonplace.http_util import path_parts, query, read_json, send_json, send_text
from commonplace.web import STATIC
from tests._commonplace_helpers import hub_fixture as hub_fixture


def request(hub, path, method="GET", body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=5)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def login(hub):
    token = sessions.create_login_link(hub.db, "owner@example.com", hub.workspace)
    status, headers, _body = request(hub, f"/app/login?t={token}")
    assert status == 302
    return {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}, token, headers


def test_login_cookie_identity_replay_and_logout(hub):
    assert request(hub, "/api/me")[0] == 401
    cookie, token, headers = login(hub)
    assert headers["Location"] == "/app/"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert all(value in headers["Set-Cookie"] for value in ("HttpOnly", "SameSite=Lax", "Path=/"))
    status, _headers, body = request(hub, "/api/me", headers=cookie)
    assert status == 200
    assert json.loads(body) == {
        "can_admin": True,
        "can_admin_instance": True,
        "user": hub.admin,
        "email": "owner@example.com",
        "workspace": hub.workspace,
        "workspace_name": hub.db.workspace(hub.workspace)["name"],
        "actor": f"user:{hub.admin}",
        "people": {f"user:{hub.admin}": "owner"},
    }
    status, headers, _ = request(hub, f"/app/login?t={token}")
    assert status == 302 and headers["Location"] == "/app/?signin=expired"
    assert "Set-Cookie" not in headers
    assert request(hub, "/api/me", headers=cookie)[0] == 200
    status, headers, _ = request(
        hub, "/app/logout", "POST", headers={**cookie, "Content-Type": "application/json"}
    )
    assert status == 204
    assert "Max-Age=0" in headers["Set-Cookie"]
    assert request(hub, "/api/me", headers=cookie)[0] == 401


def test_secure_cookie_expired_link_and_disabled_user(hub):
    hub.gateway.secure_cookies = True
    cookie, _token, headers = login(hub)
    assert "; Secure" in headers["Set-Cookie"]
    token = sessions.create_login_link(hub.db, "owner@example.com", hub.workspace)
    with hub.db._lock:
        hub.db._conn.execute("UPDATE login_links SET expires_at='2000-01-01T00:00:00Z'")
    status, headers, _ = request(hub, f"/app/login?t={token}")
    assert status == 302 and headers["Location"] == "/app/?signin=expired"
    assert "Set-Cookie" not in headers
    assert request(hub, "/api/me")[0] == 401
    with hub.db._lock:
        hub.db._conn.execute("UPDATE users SET disabled_at='2026-10-30T00:00:00Z'")
    assert request(hub, "/api/me", headers=cookie)[0] == 401
    token = sessions.create_login_link(hub.db, "owner@example.com", hub.workspace)
    status, headers, _ = request(hub, f"/app/login?t={token}")
    assert status == 302 and headers["Location"] == "/app/?signin=expired"
    assert "Set-Cookie" not in headers


@pytest.mark.parametrize(
    "query_string", ["", "?t=", "?next=https://elsewhere.test", "?t=&signin=expired"]
)
def test_login_without_token_opens_normal_signin(hub, monkeypatch, query_string):
    monkeypatch.setattr(
        hub.gateway.sign_in,
        "providers",
        {"github": SimpleNamespace(id="github", name="GitHub", daily_cap=None)},
    )
    status, headers, body = request(hub, "/app/login" + query_string)
    assert status == 302 and headers["Location"] == "/app/"
    assert "Set-Cookie" not in headers and not body
    status, _, body = request(hub, headers["Location"])
    assert status == 200 and b"<h1>Sign in</h1>" in body
    assert b'<script type="module" src="/app/app.js">' in body
    status, _, body = request(hub, "/app/auth/providers")
    assert status == 200
    [provider] = json.loads(body)["providers"]
    assert provider["id"] == "github" and provider["name"] == "GitHub"
    assert provider["url"].endswith("/app/auth/github")
    assert request(hub, "/api/me")[0] == 401


@pytest.mark.parametrize(
    "token", ["unknown-login-token", "x" * 201, '<script>alert("token")</script>']
)
def test_invalid_login_redirects_without_reflecting_or_logging_token(hub, caplog, capsys, token):
    path = "/app/login?" + urlencode({"t": token, "next": "https://elsewhere.test"})
    with caplog.at_level(logging.DEBUG):
        status, headers, body = request(hub, path)
    assert status == 302 and headers["Location"] == "/app/?signin=expired"
    assert "Set-Cookie" not in headers and not body
    assert headers["Cache-Control"] == "no-store"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert token not in repr(headers) and token.encode() not in body
    output = capsys.readouterr()
    assert token not in caplog.text + output.out + output.err
    status, _, body = request(hub, headers["Location"])
    assert status == 200 and b"<h1>Sign in</h1>" in body
    assert request(hub, "/api/me")[0] == 401
    with hub.db._lock:
        assert hub.db._conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_fresh_user_logout_then_direct_login(hub):
    user = hub.db.create_user("fresh@example.com")
    workspace = hub.db.create_workspace("fresh-home", user, personal=True)
    token = sessions.create_login_link(hub.db, "fresh@example.com", workspace)
    status, headers, _ = request(hub, f"/app/login?t={token}")
    assert status == 302
    cookie = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
    status, _, body = request(hub, "/api/me", headers=cookie)
    assert status == 200
    assert json.loads(body)["user"] == user and json.loads(body)["workspace"] == workspace
    status, headers, _ = request(
        hub, "/app/logout", "POST", headers={**cookie, "Content-Type": "application/json"}
    )
    assert status == 204 and "Max-Age=0" in headers["Set-Cookie"]
    assert all(value in headers["Set-Cookie"] for value in ("HttpOnly", "SameSite=Lax", "Path=/"))
    assert request(hub, "/api/me", headers=cookie)[0] == 401
    for headers in ({}, cookie):
        status, response_headers, _ = request(hub, "/app/login", headers=headers)
        assert status == 302 and response_headers["Location"] == "/app/"
        assert "Set-Cookie" not in response_headers
        status, _, body = request(hub, response_headers["Location"], headers=headers)
        assert status == 200 and b"<h1>Sign in</h1>" in body


def test_logout_refuses_foreign_origin_and_keeps_session(hub):
    cookie, _, _ = login(hub)
    assert (
        request(hub, "/app/logout", "POST", headers={**cookie, "Origin": "https://elsewhere.test"})[
            0
        ]
        == 403
    )
    assert request(hub, "/api/me", headers=cookie)[0] == 200


def test_events_workspace_isolation_cursor_and_large_batch(hub):
    cookie, _, _ = login(hub)
    other = hub.db.create_workspace("other", hub.admin)
    for n in range(205):
        events.emit(hub.db, hub.workspace, "card.updated", subject=f"#{n}", data={"number": n})
        events.emit(hub.db, other, "card.updated", data={"title": "other workspace secret"})
    assert request(hub, "/api/events")[0] == 401
    status, _, body = request(hub, "/api/events?after=0", headers=cookie)
    payload = json.loads(body)
    assert status == 200 and len(payload["events"]) == 205
    assert payload["seq"] == events.latest_seq(hub.db, hub.workspace)
    assert b"other workspace secret" not in body
    assert all(event["workspace_id"] == hub.workspace for event in payload["events"])
    status, _, body = request(hub, f"/api/events?after={payload['seq']}", headers=cookie)
    assert status == 200 and json.loads(body)["events"] == []
    for invalid in ("-1", "oops", "1.5", "999999999999999999999999999"):
        assert request(hub, f"/api/events?after={invalid}", headers=cookie)[0] == 400


def test_bearer_access_and_connection_secrets(hub):
    token = hub.owner_token("desktop")
    auth = {"Authorization": f"Bearer {token}"}
    assert request(hub, "/api/me", headers=auth)[0] == 200
    assert request(hub, "/api/events", headers=auth)[0] == 200
    limited = hub.token(
        "limited", profile="slim", scopes=["memory:read", "events:read"], read_wings=["shared"]
    )
    assert request(hub, "/api/events", headers={"Authorization": f"Bearer {limited}"})[0] == 403
    status, _, body = request(hub, "/api/connections", headers=auth)
    assert status == 200
    connections = json.loads(body)["connections"]
    assert {row["name"] for row in connections} == {"desktop", "limited"}
    assert all("last_used_at" in row for row in connections)
    assert all(key not in body.decode() for key in ("token_hash", "token_hint", token, "policy"))
    person = hub.db.create_user("member@example.com")
    hub.db.add_member(hub.workspace, person, "member", actor=hub.admin)
    raw = sessions.create_login_link(hub.db, "member@example.com", hub.workspace)
    _, headers, _ = request(hub, f"/app/login?t={raw}")
    cookie = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
    assert request(hub, "/api/connections", headers=cookie)[0] == 403


def test_connections_wings_deadline(hub, monkeypatch):
    """Connection management must answer while its optional worker read is blocked."""
    cookie, _, _ = login(hub)
    hub.owner_token("desktop")
    started, release, finished, responded = (threading.Event() for _ in range(4))
    replies, failures, elapsed = [], [], []

    def blocked_wings(principal, name, arguments):
        assert principal.workspace_id == hub.workspace
        assert name == "mempalace_list_wings" and arguments == {}
        started.set()
        try:
            release.wait(5)
            return {"wings": {"late_wing": {}}}
        finally:
            finished.set()

    def load():
        begin = time.monotonic()
        try:
            replies.append(request(hub, "/api/connections", headers=cookie))
        except Exception as exc:
            failures.append(exc)
        finally:
            elapsed.append(time.monotonic() - begin)
            responded.set()

    monkeypatch.setattr(hub.gateway, "call_memory_tool", blocked_wings)
    client = threading.Thread(target=load)
    client.start()
    try:
        assert started.wait(2), "The route must attempt its memory lookup"
        answered_while_blocked = responded.wait(1.5)
    finally:
        release.set()
        client.join(5)
        assert finished.wait(2)
    assert not failures
    print(
        f"connections response: {elapsed[0]:.3f}s; worker still blocked: {answered_while_blocked}"
    )
    assert answered_while_blocked, "Connection management waited for the blocked memory worker"
    assert elapsed[0] < 1.5
    status, _, body = replies[0]
    payload = json.loads(body)
    assert status == 200 and payload["wings"] == []
    assert [row["name"] for row in payload["connections"]] == ["desktop"]
    assert payload["mcp_url"].endswith("/mcp")


def test_connections_wings_deadline_slow_worker(hub, monkeypatch):
    cookie, _, _ = login(hub)
    workers = []
    delay = threading.Event()

    def slow_wings(*_args):
        workers.append(threading.current_thread())
        delay.wait(2)
        return {"wings": {"late_wing": {}}}

    monkeypatch.setattr(hub.gateway, "call_memory_tool", slow_wings)
    begin = time.monotonic()
    try:
        status, _, body = request(hub, "/api/connections", headers=cookie)
        elapsed = time.monotonic() - begin
    finally:
        delay.set()
        for worker in workers:
            worker.join(3)
    print(f"connections response with a 2s worker: {elapsed:.3f}s")
    assert elapsed < 1.5, "Connection management waited for a slow optional memory lookup"
    assert status == 200 and json.loads(body)["wings"] == []


def test_connections_wings_transport_budget(hub, monkeypatch):
    from commonplace.rules.checks import remaining

    cookie, _, _ = login(hub)
    budgets = []

    def wings(*_args):
        budgets.append(remaining(10))
        return {"wings": {"project_atlas": {}}}

    monkeypatch.setattr(hub.gateway, "call_memory_tool", wings)
    status, _, body = request(hub, "/api/connections", headers=cookie)
    assert status == 200 and json.loads(body)["wings"] == ["project_atlas"]
    assert len(budgets) == 1 and 0 < budgets[0] <= 1, "Worker transport needs the lookup deadline"


def test_connections_wings_single_flight(hub, monkeypatch):
    cookie, _, _ = login(hub)
    release = threading.Event()
    workers = []

    def blocked_wings(*_args):
        workers.append(threading.current_thread())
        release.wait(5)
        return {"wings": {}}

    monkeypatch.setattr(hub.gateway, "call_memory_tool", blocked_wings)
    try:
        assert request(hub, "/api/connections", headers=cookie)[0] == 200
        begin = time.monotonic()
        status, _, body = request(hub, "/api/connections", headers=cookie)
        elapsed = time.monotonic() - begin
        started = len(workers)
    finally:
        release.set()
        for worker in workers:
            worker.join(2)
    assert started == 1, "Repeated requests must not accumulate blocked wing lookups"
    assert elapsed < 0.5, "A busy optional lookup must fall back immediately"
    assert status == 200 and json.loads(body)["wings"] == []

    monkeypatch.setattr(
        hub.gateway, "call_memory_tool", lambda *_: {"wings": {"project_atlas": {}}}
    )
    status, _, body = request(hub, "/api/connections", headers=cookie)
    assert status == 200 and json.loads(body)["wings"] == ["project_atlas"]


def test_connections_wings_tool_error_fallback(hub, monkeypatch):
    from commonplace.extensions import ToolError

    cookie, _, _ = login(hub)
    hub.owner_token("desktop")

    def unavailable(*_args):
        raise ToolError("unavailable worker details")

    monkeypatch.setattr(hub.gateway, "call_memory_tool", unavailable)
    status, _, body = request(hub, "/api/connections", headers=cookie)
    assert status == 200 and json.loads(body)["wings"] == []
    assert [row["name"] for row in json.loads(body)["connections"]] == ["desktop"]
    assert b"unavailable worker details" not in body


@pytest.mark.parametrize("path", ["/app/", "/app", "/app/board/demo", "/app/unknown/route"])
def test_static_index_and_spa_fallback(hub, path):
    status, headers, body = request(hub, path)
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert b'<script type="module" src="/app/app.js">' in body
    assert b"Sign in" in body
    assert b'<a class="brand-home" href="/app/"' in body
    assert b'<img class="brand-v2" src="/app/brand/logo-small-dark.svg"' in body
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert "unsafe-inline" not in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize(
    "path",
    [
        "/app/no.js",
        "/app/no.css",
        "/app/../sessions.py",
        "/app/%2e%2e/sessions.py",
        "/app/%2e%2e%5csessions.py",
        "/app/%00",
        "/app/api/unknown",
        "/api/unknown",
    ],
)
def test_static_rejects_missing_assets_api_and_traversal(hub, path):
    status, _, body = request(hub, path)
    assert status == 404 and b"<!doctype html>" not in body


@pytest.mark.parametrize(
    "asset,mime",
    [
        ("app.js", "text/javascript"),
        ("app.css", "text/css"),
        ("brand/logo-small-dark.svg", "image/svg+xml"),
        ("icon.svg", "image/svg+xml"),
        ("fonts/geist-mono-latin.woff2", "font/woff2"),
    ],
)
def test_static_asset_mime(hub, asset, mime):
    status, headers, body = request(hub, f"/app/{asset}")
    assert status == 200 and headers["Content-Type"].startswith(mime) and body


def test_shared_helpers_and_contract_fake_api(hub):
    def fake(h):
        if h.command == "GET":
            send_json(
                h,
                200,
                {
                    "boards": [
                        {"slug": "work", "name": "Work", "wing": "shared", "counts": {"todo": 1}}
                    ]
                },
            )
            return
        try:
            payload = read_json(h)
        except ValueError:
            return
        send_json(h, 201, payload)

    hub.gateway.add_route("GET", "/api/boards", fake)
    hub.gateway.add_route("POST", "/api/boards", fake)
    auth = {"Authorization": "Bearer " + hub.owner_token()}
    assert json.loads(request(hub, "/api/boards", headers=auth)[2])["boards"][0]["slug"] == "work"
    status, _, body = request(
        hub,
        "/api/boards",
        "POST",
        '{"name":"café"}'.encode(),
        {**auth, "Content-Type": "application/json"},
    )
    assert status == 201 and json.loads(body)["name"] == "café"
    for raw in (
        b"",
        b"{",
        b"[]",
        b"null",
        b'{"value": NaN}',
        b"1",
        b"\xff",
    ):
        assert request(hub, "/api/boards", "POST", raw, headers=auth)[0] == 400
    # The gateway rejects the declared size before reading; don't race its
    # connection close by continuing to upload an oversized body.
    assert (
        request(
            hub,
            "/api/boards",
            "POST",
            b"",
            headers={**auth, "Content-Length": str(1024 * 1024 + 1)},
        )[0]
        == 413
    )
    h = SimpleNamespace(path="/api/boards/a%20b/cards/7?q=a+b&empty=&q=last")
    assert path_parts(h, "/api/") == ["boards", "a b", "cards", "7"]
    assert path_parts(h, "/other/") == []
    assert query(h) == {"q": "last", "empty": ""}


def test_helper_response_encoding():
    class Handler:
        headers = {"Content-Length": "-1"}
        rfile = io.BytesIO()
        wfile = io.BytesIO()
        close_connection = False

        def send_response(self, status):
            self.status = status
            self.sent = {}

        def send_header(self, key, value):
            self.sent[key] = value

        def end_headers(self):
            pass

    h = Handler()
    with pytest.raises(ValueError):
        read_json(h)
    assert h.status == 400 and h.close_connection
    h.wfile = io.BytesIO()
    send_text(h, 200, "café", "text/plain; charset=utf-8")
    assert h.sent["Content-Length"] == "5"
    assert h.wfile.getvalue() == "café".encode()


def test_static_modules_have_no_inline_executable_or_network_dependencies():
    html = (STATIC / "index.html").read_text()
    assert "<style" not in html and "onclick=" not in html and 'style="' not in html
    assert "https://" not in html and "http://" not in html
    assert (STATIC / "fonts" / "OFL.txt").is_file()


def test_session_identity_can_use_the_real_memory_worker(hub):
    cookie, _, _ = login(hub)
    principal = sessions.session_principal(hub.db, sessions.cookie_value(cookie["Cookie"]))
    saved = hub.gateway.call_memory_tool(
        principal,
        "mempalace_add_drawer",
        {"wing": "shared", "room": "web", "content": "Keep my exact words — café."},
    )
    assert saved["success"]
    found = hub.gateway.call_memory_tool(
        principal, "mempalace_get_drawer", {"drawer_id": saved["drawer_id"]}
    )
    assert found["content"] == "Keep my exact words — café."
    assert found["metadata"]["hub_user"] == hub.admin


def test_bearer_precedes_cookie_and_expired_session_is_refused(hub):
    cookie, _, _ = login(hub)
    assert request(hub, "/api/me", headers={**cookie, "Authorization": "Bearer invalid"})[0] == 401
    with hub.db._lock:
        hub.db._conn.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00Z'")
    assert request(hub, "/api/me", headers=cookie)[0] == 401


def test_helper_json_size_and_transfer_encoding():
    class Handler:
        def __init__(self, body, headers):
            self.headers = headers
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()

        def send_response(self, status):
            self.status = status

        def send_header(self, *args):
            pass

        def end_headers(self):
            pass

    raw = b'{"text":"' + b"a" * (1024 * 1024 - 11) + b'"}'
    assert len(raw) == 1024 * 1024
    h = Handler(raw, {"Content-Length": str(len(raw))})
    assert len(read_json(h)["text"]) == 1024 * 1024 - 11
    for headers in (
        {"Content-Length": "no"},
        {"Transfer-Encoding": "chunked"},
        {"Content-Length": "5"},
    ):
        h = Handler(b"{}", headers)
        with pytest.raises(ValueError):
            read_json(h)
        assert h.status == 400
