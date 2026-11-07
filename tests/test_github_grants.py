"""Browser-only GitHub grants with synthetic credentials and no remote traffic."""

import importlib
import json
import socket
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from commonplace import sessions, urls
from commonplace.boards.service import BoardService
from commonplace.extensions import ToolContext
from commonplace.social import accounts, providers
from tests.test_commonplace_public_surface import make_surface as make_surface, token
from tests.test_commonplace_social import request, rows


class Remote:
    def __init__(self):
        self.calls = []
        self.scope = ""
        self.expiry = 3600
        self.subject = 123
        self.status = 204
        self.entered = Event()
        self.release = Event()
        self.block = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("access_token"):
            self.entered.set()
            if self.block:
                assert self.release.wait(5)
            return (
                200,
                {},
                {
                    "access_token": "synthetic-issued-token",
                    "scope": self.scope,
                    "expires_in": self.expiry,
                    "refresh_token": "synthetic-refresh",
                },
            )
        if url.endswith("/user"):
            return 200, {"X-OAuth-Scopes": self.scope}, {"id": self.subject, "login": "test-person"}
        assert method == "DELETE" and "/applications/" in url
        return self.status, {}, {}


@pytest.fixture
def setup(make_surface, monkeypatch, tmp_path):
    monkeypatch.setenv("COMMONPLACE_GITHUB_CONNECT", "on")
    monkeypatch.setenv("COMMONPLACE_GITHUB_CLIENT_ID", "synthetic-client")
    monkeypatch.setenv("COMMONPLACE_GITHUB_CLIENT_SECRET", "synthetic-app-secret")
    key = tmp_path / "wrapping.key"
    key.write_bytes(b"k" * 32)
    key.chmod(0o600)
    monkeypatch.setenv("COMMONPLACE_GITHUB_KEY_FILE", str(key))
    monkeypatch.setattr(urls, "APP_URL", "https://app.example")
    surface = make_surface()
    surface.gateway.rate_limits = False
    # Public users, not administrators.
    surface.db._conn.execute("UPDATE users SET is_admin = 0")
    surface.session = sessions.start_session(surface.db, surface.uid, surface.workspace)
    surface.cookie = sessions.COOKIE + "=" + surface.session
    principal = sessions.session_principal(surface.db, surface.session)
    service = BoardService(ToolContext(principal=principal, gateway=surface.gateway))
    service.board_add(slug="work", name="Work")
    surface.card = service.task_add(board="work", title="Review")["number"]
    remote = Remote()
    surface.remote = remote
    if hasattr(surface.gateway, "github"):
        module = importlib.import_module("commonplace.github.client")
        monkeypatch.setattr(module, "request", remote.request)
    original = socket.socket.connect

    def local_only(sock, address):
        assert address[0] == "127.0.0.1", "unexpected network"
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_only)
    return surface


def status(s):
    code, _, raw = request(s, "/api/github", cookie=s.cookie)
    assert code == 200, (code, raw)
    return json.loads(raw)


def write(s, path="/api/github/connect", method="POST", payload=None, **headers):
    data = {"csrf": status(s)["csrf"], "card": s.card, **(payload or {})}
    return request(
        s,
        path,
        method,
        cookie=s.cookie,
        raw=json.dumps(data).encode(),
        headers={"Content-Type": "application/json", "Origin": urls.APP_URL, **headers},
    )


def begin(s):
    code, headers, raw = write(s)
    assert code == 200, (code, raw)
    data = json.loads(raw)
    params = parse_qs(urlsplit(data["authorize_url"]).query, keep_blank_values=True)
    assert params["scope"] == [""] and "offline_access" not in data["authorize_url"]
    assert params["code_challenge_method"] == ["S256"]
    return params["state"][0], headers["Set-Cookie"].split(";", 1)[0]


def finish(s, flow):
    state, cookie = flow
    return request(
        s,
        "/app/auth/github/callback?" + urlencode({"state": state, "code": "synthetic-code"}),
        cookie=s.cookie + "; " + cookie,
    )


def connect(s):
    response = finish(s, begin(s))
    assert response[0] == 303
    assert "synthetic-code" not in response[1]["Location"]
    pending = status(s)
    if pending["state"] == "confirm":
        assert write(s, "/api/github/confirm", payload={"accept": True})[0] == 200
    assert status(s)["state"] == "connected"


def test_owner_identity_first(setup):
    s = setup
    accounts.resolve(
        s.db, "github", providers.Profile("123", "Person", ("owner@example.com",)), signup=False
    )
    tables = {r[0] for r in s.db._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "github_account_owners" in tables
    assert rows(s, "github_account_owners")[0]["user_id"] == s.uid
    # Legacy identity without an owner row must still defeat a foreign grant.
    s.db._conn.execute("DELETE FROM github_account_owners")
    other = s.db.create_user("other@example.com")
    s.db.add_member(s.workspace, other, "member", actor=s.uid)
    s.cookie = sessions.COOKIE + "=" + sessions.start_session(s.db, other, s.workspace)
    finish(s, begin(s))
    if status(s)["state"] == "confirm":
        write(s, "/api/github/confirm", payload={"accept": True})
    assert status(s)["state"] != "connected"
    assert len([c for c in s.remote.calls if c[0] == "DELETE"]) == 1


def test_owner_migration(setup):
    s = setup
    accounts.resolve(
        s.db, "github", providers.Profile("123", "Person", ("owner@example.com",)), signup=False
    )
    tables = {r[0] for r in s.db._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "github_account_owners" in tables
    s.db._conn.execute("DELETE FROM github_account_owners")
    accounts.ensure_schema(s.db)
    accounts.ensure_schema(s.db)
    assert len(rows(s, "github_account_owners")) == 1
    other = s.db.create_user("other@example.com")
    s.db._conn.execute("UPDATE github_account_owners SET user_id=?", (other,))
    with pytest.raises(providers.SignInError, match="github_owner"):
        accounts.ensure_schema(s.db)
    assert rows(s, "github_account_owners")[0]["user_id"] == other


def test_owner_races(setup):
    s = setup
    status(s)
    accounts.reserve_github_owner(s.db._conn, "123", s.uid)
    before = len(rows(s, "users"))
    with pytest.raises(providers.SignInError, match="github_owner"):
        accounts.resolve(
            s.db, "github", providers.Profile("123", "Person", ("new@example.com",)), signup=True
        )
    assert len(rows(s, "users")) == before
    # Numeric subjects of other providers never participate in GitHub ownership.
    assert (
        accounts.resolve(
            s.db, "email", providers.Profile("123", "Person", ("new@example.com",)), signup=True
        )
        != s.uid
    )
    with ThreadPoolExecutor(2) as pool:
        result = list(
            pool.map(
                lambda _: accounts.resolve(
                    s.db,
                    "github",
                    providers.Profile("123", "Person", ("owner@example.com",)),
                    signup=False,
                ),
                range(2),
            )
        )
    assert result == [s.uid, s.uid]


def test_disconnect_callback(setup):
    s = setup
    flow = begin(s)
    s.remote.block = True
    with ThreadPoolExecutor(2) as pool:
        pending = pool.submit(finish, s, flow)
        assert s.remote.entered.wait(5)
        assert write(s, "/api/github", "DELETE")[0] == 200
        assert write(s)[0] == 409
        s.remote.release.set()
        assert pending.result()[0] == 303
    assert status(s)["state"] != "connected"
    deletes = [c for c in s.remote.calls if c[0] == "DELETE"]
    assert len(deletes) == 1 and deletes[0][1].endswith("/token")


def test_deletion_callback(setup):
    s = setup
    flow = begin(s)
    s.remote.block = True
    with ThreadPoolExecutor(2) as pool:
        pending = pool.submit(finish, s, flow)
        assert s.remote.entered.wait(5)
        s.gateway.github.delete_user(s.uid)
        s.remote.release.set()
        assert pending.result()[0] == 303
    assert not rows(s, "github_grants")
    assert not rows(s, "github_account_owners")
    assert len([c for c in s.remote.calls if c[0] == "DELETE"]) == 1


def test_lifecycle_recovery(setup):
    s = setup
    connect(s)
    s.remote.status = 422
    code, _, raw = write(s, "/api/github", "DELETE")
    assert code == 200 and "unconfirmed" in raw.decode().lower()
    assert not rows(s, "github_grants")
    assert write(s)[0] == 409
    assert write(s, payload={"acknowledge": True})[0] == 200
    s.gateway.github.recover()
    assert not rows(s, "github_connect_flows")
    assert status(s)["state"] != "connected"


def test_consent_dispatch(setup):
    s = setup
    flow = begin(s)
    before = (rows(s, "users"), rows(s, "sessions"), rows(s, "user_identities"))
    response = finish(s, flow)
    assert response[0] == 303 and sessions.COOKIE not in repr(response[1].get_all("Set-Cookie", []))
    assert status(s)["state"] == "confirm"
    assert write(s, "/api/github/confirm", payload={"accept": False})[0] == 200
    assert len([c for c in s.remote.calls if c[0] == "DELETE"]) == 1
    finish(s, flow)
    assert len([c for c in s.remote.calls if c[1].endswith("access_token")]) == 1
    assert before == (rows(s, "users"), rows(s, "sessions"), rows(s, "user_identities"))


def test_scopes_secrets(setup, caplog):
    s = setup
    connect(s)
    for call in s.remote.calls:
        assert not call[1].endswith("/emails")
    raw = (
        repr(rows(s, "github_grants"))
        + repr(rows(s, "github_connect_flows"))
        + repr(s.db.audit_entries())
        + caplog.text
        + json.dumps(status(s))
    )
    for secret in (
        "synthetic-issued-token",
        "synthetic-refresh",
        "synthetic-code",
        "synthetic-app-secret",
    ):
        assert secret not in raw
    for path in (s.db.path, s.db.path + "-wal"):
        from pathlib import Path

        if Path(path).exists():
            assert b"synthetic-issued-token" not in Path(path).read_bytes()


@pytest.mark.parametrize("guard", ["bearer", "origin", "csrf", "workspace", "logout"])
def test_security_guards(setup, guard):
    s = setup
    initial = status(s)
    if guard == "bearer":
        auth = token(s)[2]
        assert request(s, "/api/github", headers=auth)[0] == 403
    elif guard == "origin":
        assert write(s, Origin="https://evil.example")[0] == 403
        raw = json.dumps({"csrf": initial["csrf"], "card": s.card}).encode()
        assert (
            request(
                s,
                "/api/github/connect",
                "POST",
                cookie=s.cookie,
                raw=raw,
                headers={"Content-Type": "application/json"},
            )[0]
            == 403
        )
    elif guard == "csrf":
        assert write(s, payload={"csrf": "wrong"})[0] == 403
    else:
        flow = begin(s)
        if guard == "logout":
            sessions.end_session(s.db, s.session)
        else:
            wid = s.db.create_workspace("other", s.uid)
            s.db._conn.execute(
                "UPDATE sessions SET workspace_id=? WHERE id_hash=?",
                (wid, sessions._hash(s.session)),
            )
        finish(s, flow)
        assert not rows(s, "github_grants")
        assert not [c for c in s.remote.calls if c[1].endswith("access_token")]


@pytest.mark.parametrize(
    "scope,expiry",
    [
        (None, 3600),
        ("repo", 3600),
        ("public_repo", 3600),
        ("", None),
        ("", 0),
        ("", -1),
        ("", True),
    ],
)
def test_refused_scope_or_expiry_revokes_one_token(setup, scope, expiry):
    s = setup
    s.remote.scope, s.remote.expiry = scope, expiry
    finish(s, begin(s))
    assert status(s)["state"] == "absent"
    deletes = [c for c in s.remote.calls if c[0] == "DELETE"]
    assert len(deletes) == 1 and deletes[0][1].endswith("/token")
    assert not rows(s, "github_grants")


@pytest.mark.parametrize("scope", ["read:user", "user:email", "read:user user:email"])
def test_inherited_scopes_require_confirmation_even_for_login_subject(setup, scope):
    s = setup
    status(s)
    accounts.resolve(
        s.db, "github", providers.Profile("123", "Person", ("owner@example.com",)), signup=False
    )
    s.remote.scope = scope
    finish(s, begin(s))
    assert status(s)["state"] == "confirm"
    assert not rows(s, "github_grants")
    assert write(s, "/api/github/confirm", payload={"accept": True})[0] == 200
    assert status(s)["state"] == "connected"


def test_one_flow_and_one_grant(setup):
    s = setup
    flow = begin(s)
    assert write(s)[0] == 409
    finish(s, flow)
    write(s, "/api/github/confirm", payload={"accept": True})
    assert status(s)["state"] == "connected"
    assert write(s)[0] == 409
    assert len(rows(s, "github_grants")) == 1
    assert not [c for c in s.remote.calls if c[0] == "DELETE"]


def test_key_permissions_tamper_and_workspace_fail_closed(setup, monkeypatch):
    import os
    from pathlib import Path

    s = setup
    connect(s)
    key = Path(os.environ["COMMONPLACE_GITHUB_KEY_FILE"])
    key.chmod(0o644)
    assert request(s, "/api/github", cookie=s.cookie)[0] == 409
    key.chmod(0o600)
    assert status(s)["state"] == "connected"
    s.db._conn.execute("UPDATE github_grants SET secret=?", (b"tampered",))
    assert request(s, "/api/github", cookie=s.cookie)[0] == 409


def test_workspace_separation_of_connection_state(setup):
    s = setup
    connect(s)
    wid = s.db.create_workspace("other", s.uid)
    s.cookie = sessions.COOKIE + "=" + sessions.start_session(s.db, s.uid, wid)
    assert status(s)["state"] == "absent"
    assert write(s, "/api/github", "DELETE")[0] == 409
    assert len(rows(s, "github_grants")) == 1


def test_disabled_callback_and_hidden_card_do_not_exchange(setup, monkeypatch):
    s = setup
    flow = begin(s)
    monkeypatch.delenv("COMMONPLACE_GITHUB_CONNECT")
    finish(s, flow)
    assert not s.remote.calls
    monkeypatch.setenv("COMMONPLACE_GITHUB_CONNECT", "on")
    assert write(s, payload={"card": 99999})[0] == 409
    assert not s.remote.calls


def test_confirm_disconnect_race_does_not_resurrect(setup):
    s = setup
    finish(s, begin(s))
    assert status(s)["state"] == "confirm"
    assert write(s, "/api/github", "DELETE")[0] == 200
    assert write(s, "/api/github/confirm", payload={"accept": True})[0] == 409
    assert not rows(s, "github_grants")
    assert len([c for c in s.remote.calls if c[0] == "DELETE"]) == 1


def test_login_provisioning_commit_failure_rolls_back_every_control_row(setup):
    import sqlite3

    s = setup
    status(s)
    before = {
        table: rows(s, table)
        for table in (
            "users",
            "workspaces",
            "memberships",
            "user_identities",
            "github_account_owners",
            "account_plans",
        )
    }
    s.db._conn.execute(
        "CREATE TRIGGER fail_identity BEFORE INSERT ON user_identities BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        accounts.resolve(
            s.db, "github", providers.Profile("456", "New", ("new@example.com",)), signup=True
        )
    assert before == {table: rows(s, table) for table in before}


def test_callback_checks_card_visibility_again(setup):
    s = setup
    flow = begin(s)
    s.db._conn.execute(
        "DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (s.workspace, s.uid)
    )
    finish(s, flow)
    assert not s.remote.calls
    assert not rows(s, "github_grants")


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/github"),
        ("POST", "/api/github/connect"),
        ("POST", "/api/github/confirm"),
        ("DELETE", "/api/github"),
    ],
)
def test_agent_and_bearer_cannot_use_browser_routes(setup, method, path):
    s = setup
    initial = status(s)
    auth = token(s)[2]
    response = request(
        s,
        path,
        method,
        cookie=s.cookie,
        raw=json.dumps({"csrf": initial["csrf"], "card": s.card}).encode(),
        headers={**auth, "Origin": urls.APP_URL, "Content-Type": "application/json"},
    )
    assert response[0] == 403
    assert not s.remote.calls
