"""Deletion proof must come from a fresh credential check in the hub browser."""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from urllib.parse import parse_qs, urlsplit

import pytest

from commonplace import oauth, ratelimit, sessions
from commonplace.account import deletion
from commonplace.oauth import store
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_oauth import VERIFIER, Client

CAPABILITY = "account:delete:person"
CONSENT = (
    "Allow this app to request deletion of your account only after you sign in again on this "
    "website and confirm account deletion here. The app never receives your sign-in credential. "
    "The existing waiting period and account protection rules still apply. Each request records "
    "your identity and this app's name. Disconnect this app to stop access."
)
CALLBACK = "cmmn://auth/callback"
TOKEN_HEADER = "X-Account-Deletion-Token"


@pytest.fixture
def phone(hub, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_PHONE_ACTIONS", "on")
    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION", "on")
    hub.gateway.rate_limits = False
    hub.db.create_user("backup-owner@example.test", is_admin=True)
    client = Client(hub)
    client.login()
    client.cid = client.register(client_name="Mobile client", redirect_uris=[CALLBACK])
    return client


def field(html, name="request"):
    return unescape(re.search(r'name="' + name + r'" value="([^"]+)"', html)[1])


def post(client, path, data, **kwargs):
    host, port = client.hub.server.server_address
    headers = {"Origin": f"http://{host}:{port}", **kwargs.pop("headers", {})}
    return client.request("POST", path, data, headers=headers, **kwargs)


def consent(client, *, checked=True, events=False):
    path = client.authorize_path(client.cid, redirect_uri=CALLBACK, scope="memory:read")
    status, _, html = client.request("GET", path)
    assert status == 200, html
    data = {"request": field(html), "decision": "allow"}
    if checked:
        data["grant_person_delete"] = "1"
    if events:
        data["grant_person_events"] = "1"
    status, headers, text = client.request("POST", path, data)
    assert status == 303, text
    code = parse_qs(urlsplit(headers["Location"]).query)["code"][0]
    status, _, text = client.exchange(client.cid, code, redirect_uri=CALLBACK)
    assert status == 200, text
    pair = json.loads(text)
    app = client.hub.gateway.authenticate("Bearer " + pair["access_token"])
    return pair, app


def start(client, app, **extra):
    path = client.authorize_path(
        client.cid,
        redirect_uri=CALLBACK,
        scope="memory:read",
        purpose="account_deletion",
        connection_id=app.connection_id,
        **extra,
    )
    status, headers, html = client.request("GET", path)
    assert status == 200, html
    assert "Sign in again" in html
    assert "deletion_token" not in headers.get("Location", "")
    return path, field(html)


def check(client, path, request, *, email="owner@example.com"):
    credential = sessions.create_login_link(client.hub.db, email, client.hub.workspace)
    return post(client, path, {"request": request, "login_token": credential})


def ready(client, app, *, email="owner@example.com"):
    path, request = start(client, app)
    status, headers, html = check(client, path, request, email=email)
    assert status == 200, html
    assert "Confirm account deletion" in html and "Cancel" in html
    assert "14 days" in html and email in html
    assert "deletion_token" not in headers.get("Location", "")
    return path, field(html)


def mint(client, app, *, email="owner@example.com"):
    path, request = ready(client, app, email=email)
    status, headers, text = post(
        client, path, {"request": request, "decision": "confirm", "confirm": email}
    )
    assert status == 303, text
    values = parse_qs(urlsplit(headers["Location"]).query)
    assert values["state"] == ["opaque + state & = / ✨"]
    assert "code" not in values and "access_token" not in values
    return values["deletion_token"][0]


def use(client, pair, token, *, confirm="owner@example.com", verifier=VERIFIER, **extra):
    return client.request(
        "POST",
        "/api/account/deletion",
        cookies=False,
        headers={
            "Authorization": "Bearer " + pair["access_token"],
            TOKEN_HEADER: token,
            "Content-Type": "application/json",
            **extra,
        },
        raw=json.dumps({"confirm": confirm, "code_verifier": verifier}).encode(),
    )


def rows(client, sql, params=()):
    db = client.hub.db
    with db._lock:
        return [dict(r) for r in db._conn.execute(sql, params)]


def test_consent_is_exact_unticked_and_never_an_oauth_scope(phone):
    path = phone.authorize_path(phone.cid, redirect_uri=CALLBACK, scope="memory:read")
    status, _, html = phone.request("GET", path)
    assert status == 200
    assert CONSENT in unescape(html)
    tag = re.search(r'<input[^>]*name="grant_person_delete"[^>]*>', html)[0]
    assert 'type="checkbox"' in tag and "checked" not in tag
    assert CAPABILITY not in oauth._server_metadata()["scopes_supported"]
    assert CAPABILITY not in sessions.person_policy("owner").scopes
    with pytest.raises(oauth.http.OAuthError):
        store.scope(CAPABILITY)


def test_fresh_check_mints_once_and_preserves_the_grace_period(phone):
    pair, app = consent(phone)
    token = mint(phone, app)
    status, _, text = use(phone, pair, token)
    assert status == 200, text
    result = json.loads(text)
    assert result["state"] == "pending"
    assert deletion.purge_due(phone.hub.gateway) == []
    assert phone.hub.db.user(app.user_id)["disabled_at"] is None
    assert use(phone, pair, token)[0] == 403
    assert len(rows(phone, "SELECT * FROM account_deletions")) == 1


def test_recent_cookie_without_credential_check_cannot_mint(phone):
    _, app = consent(phone)
    path, request = start(phone, app)
    status, _, text = post(
        phone, path, {"request": request, "decision": "confirm", "confirm": "owner@example.com"}
    )
    assert status == 403, text
    assert rows(phone, "SELECT * FROM account_delete_tokens") == []


def test_stale_recheck_cannot_mint(phone, monkeypatch):
    _, app = consent(phone)
    path, request = ready(phone, app)
    now = store.now()
    monkeypatch.setattr(store, "now", lambda: now + 301)
    assert (
        post(
            phone, path, {"request": request, "decision": "confirm", "confirm": "owner@example.com"}
        )[0]
        == 403
    )
    assert rows(phone, "SELECT * FROM account_delete_tokens") == []


@pytest.mark.parametrize("seconds", [300, 301])
def test_expired_token_is_refused(phone, monkeypatch, seconds):
    pair, app = consent(phone)
    token = mint(phone, app)
    now = store.now()
    monkeypatch.setattr(store, "now", lambda: now + seconds)
    assert use(phone, pair, token)[0] == 403
    assert rows(phone, "SELECT * FROM account_deletions") == []


def test_cancel_mints_nothing_and_consumes_the_confirmation(phone):
    _, app = consent(phone)
    path, request = ready(phone, app)
    status, headers, _ = post(phone, path, {"request": request, "decision": "cancel"})
    assert status == 303
    assert parse_qs(urlsplit(headers["Location"]).query)["error"] == ["access_denied"]
    assert rows(phone, "SELECT * FROM account_delete_tokens") == []
    assert (
        post(
            phone, path, {"request": request, "decision": "confirm", "confirm": "owner@example.com"}
        )[0]
        == 403
    )


def test_old_token_without_the_capability_cannot_start_or_delete(phone):
    pair, app = consent(phone, checked=False)
    path = phone.authorize_path(
        phone.cid,
        redirect_uri=CALLBACK,
        scope="memory:read",
        purpose="account_deletion",
        connection_id=app.connection_id,
    )
    assert phone.request("GET", path)[0] == 403
    assert use(phone, pair, "unissued")[0] == 403


def test_concurrent_use_has_one_winner(phone):
    pair, app = consent(phone)
    token = mint(phone, app)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: use(phone, pair, token)[0], range(2)))
    assert sorted(results) == [200, 403]


def test_wrong_client_connection_and_person_are_refused(phone):
    pair, app = consent(phone)
    token = mint(phone, app)
    other = Client(phone.hub)
    other.login()
    other.cid = other.register(client_name="Other mobile client", redirect_uris=[CALLBACK])
    other_pair, _ = consent(other)
    assert use(other, other_pair, token)[0] == 403
    same_client_pair, _ = consent(phone)
    assert use(phone, same_client_pair, token)[0] == 403
    member = phone.hub.db.create_user("member@example.test")
    phone.hub.db.add_member(phone.hub.workspace, member, "member", actor=phone.hub.admin)
    other.login("member@example.test")
    member_pair, _ = consent(other)
    assert use(other, member_pair, token, confirm="member@example.test")[0] == 403
    assert use(phone, pair, token)[0] == 200


def test_default_off_and_forged_tick_grant_nothing(phone, monkeypatch):
    monkeypatch.delenv("COMMONPLACE_PHONE_ACTIONS")
    path = phone.authorize_path(phone.cid, redirect_uri=CALLBACK, scope="memory:read")
    assert "grant_person_delete" not in phone.request("GET", path)[2]
    pair, app = consent(phone)
    assert rows(phone, "SELECT * FROM oauth_person_grants WHERE capability=?", (CAPABILITY,)) == []
    assert use(phone, pair, "unissued")[0] == 403
    assert rows(phone, "SELECT * FROM account_deletions") == []


@pytest.mark.parametrize("seconds,status", [(299, 303), (300, 403), (301, 403)])
def test_freshness_boundary_at_confirmation(phone, monkeypatch, seconds, status):
    _, app = consent(phone)
    path, request = ready(phone, app)
    now = store.now()
    monkeypatch.setattr(store, "now", lambda: now + seconds)
    assert (
        post(
            phone, path, {"request": request, "decision": "confirm", "confirm": "owner@example.com"}
        )[0]
        == status
    )


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://untrusted.example"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Authorization": "Bearer untrusted"},
    ],
)
def test_recheck_rejects_cross_origin_and_bearer_requests(phone, headers):
    _, app = consent(phone)
    path, request = start(phone, app)
    credential = sessions.create_login_link(phone.hub.db, "owner@example.com", phone.hub.workspace)
    assert (
        post(phone, path, {"request": request, "login_token": credential}, headers=headers)[0]
        == 403
    )
    assert rows(phone, "SELECT * FROM account_delete_tokens") == []


def test_missing_origin_browser_cookie_and_wrong_session_are_refused(phone):
    _, app = consent(phone)
    path, request = start(phone, app)
    credential = sessions.create_login_link(phone.hub.db, "owner@example.com", phone.hub.workspace)
    data = {"request": request, "login_token": credential}
    assert phone.request("POST", path, data)[0] == 403
    assert post(phone, path, data, cookies=False)[0] == 403
    previous = phone.cookies[sessions.COOKIE]
    phone.login()
    assert post(phone, path, data)[0] == 403
    phone.cookies[sessions.COOKIE] = previous
    assert post(phone, path, data)[0] == 200


def test_wrong_person_credential_is_not_consumed_or_minted(phone):
    _, app = consent(phone)
    other = phone.hub.db.create_user("other-person@example.test")
    phone.hub.db.add_member(phone.hub.workspace, other, "member", actor=phone.hub.admin)
    path, request = start(phone, app)
    credential = sessions.create_login_link(
        phone.hub.db, "other-person@example.test", phone.hub.workspace
    )
    assert post(phone, path, {"request": request, "login_token": credential})[0] == 403
    assert sessions.redeem_login_link(phone.hub.db, credential) is not None
    assert rows(phone, "SELECT * FROM account_delete_tokens") == []


def test_initial_signin_without_cookie_still_requires_the_browser_check(phone):
    pair, app = consent(phone)
    phone.cookies.clear()
    token = mint(phone, app)
    assert use(phone, pair, token)[0] == 200


def test_mint_is_single_delivery_and_cannot_be_reached_by_a_link(phone):
    _, app = consent(phone)
    path, request = ready(phone, app)
    # GET ignores apparent confirmation parameters and never returns a proof.
    status, headers, html = phone.request("GET", path + "&decision=confirm")
    assert status == 403 and "deletion_token" not in headers.get("Location", "")
    assert rows(phone, "SELECT * FROM account_delete_tokens") == []
    # The rejected GET does not replace the browser-bound confirmation cookie.
    data = {"request": request, "decision": "confirm", "confirm": "owner@example.com"}
    assert post(phone, path, data)[0] == 303
    assert post(phone, path, data)[0] == 403
    assert len(rows(phone, "SELECT * FROM account_delete_tokens")) == 1


@pytest.mark.parametrize("mode", ["signout", "disconnect", "grant", "kill", "deletion-off"])
def test_revocation_blocks_mint_and_outstanding_tokens(phone, monkeypatch, mode):
    pair, app = consent(phone)
    token = mint(phone, app)
    path, request = ready(phone, app)
    db = phone.hub.db
    if mode == "signout":
        sessions.end_session(db, phone.cookies[sessions.COOKIE])
    elif mode == "disconnect":
        store.revoke_connection(
            db, app.connection_id, actor_id=app.user_id, workspace_id=app.workspace_id
        )
    elif mode == "grant":
        db.clear_decide(app.connection_id, reason="consent withdrawn")
    else:
        monkeypatch.delenv(
            "COMMONPLACE_PHONE_ACTIONS" if mode == "kill" else "COMMONPLACE_ACCOUNT_DELETION"
        )
    assert post(
        phone, path, {"request": request, "decision": "confirm", "confirm": "owner@example.com"}
    )[0] in (401, 403)
    assert use(phone, pair, token)[0] in (401, 403)
    monkeypatch.setenv("COMMONPLACE_PHONE_ACTIONS", "on")
    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION", "on")
    assert use(phone, pair, token)[0] in (401, 403)
    assert rows(phone, "SELECT * FROM account_deletions") == []


@pytest.mark.parametrize("kind", ["agent", "child", "seat", "machine", "worker", "coordinator"])
def test_machinery_cannot_mint_or_use_a_consenting_persons_proof(phone, kind):
    from commonplace.boards.service import BoardService
    from commonplace.extensions import ToolContext

    pair, app = consent(phone)
    token = mint(phone, app)
    db = phone.hub.db
    with db._lock:
        if kind == "agent":
            db._conn.execute(
                "UPDATE connections SET started_by='agent' WHERE id=?", (app.connection_id,)
            )
        elif kind == "child":
            parent = db.authenticate_token(phone.hub.owner_token("parent"))
            _, child_token = db.create_token(
                workspace_ref=app.workspace_id,
                user_ref=app.user_id,
                name="child",
                client="test",
                parent=parent.connection_id,
                actor=app.user_id,
                policy=parent.policy,
            )
            pair = {"access_token": child_token}
            app = db.authenticate_token(child_token)
        elif kind == "seat":
            from commonplace.decide_grant import SEAT_CLIENT

            db._conn.execute(
                "UPDATE connections SET client=? WHERE id=?", (SEAT_CLIENT, app.connection_id)
            )
        elif kind in ("machine", "worker"):
            db._conn.execute(
                "INSERT INTO runner_connections(connection_id,machine_id,kind) VALUES (?,?,?)",
                (app.connection_id, "synthetic", kind),
            )
    if kind == "coordinator":
        person = sessions.person_principal(db, app.user_id, app.workspace_id)
        service = BoardService(ToolContext(principal=person, gateway=phone.hub.gateway))
        service.board_add(slug="work", wing="shared")
        with service.store.transaction() as conn:
            conn.execute("UPDATE boards SET coordinator=? WHERE slug='work'", (app.actor,))
    path = phone.authorize_path(
        phone.cid,
        redirect_uri=CALLBACK,
        scope="memory:read",
        purpose="account_deletion",
        connection_id=app.connection_id,
    )
    assert phone.request("GET", path)[0] == 403
    assert use(phone, pair, token)[0] in (401, 403, 404)
    assert rows(phone, "SELECT * FROM account_deletions") == []


def test_plain_connection_and_a_proof_without_its_client_bearer_are_refused(phone):
    _, app = consent(phone)
    token = mint(phone, app)
    ordinary = phone.hub.owner_token("ordinary")
    assert use(phone, {"access_token": ordinary}, token)[0] == 403
    assert (
        phone.request(
            "POST",
            "/api/account/deletion",
            headers={TOKEN_HEADER: token, "Content-Type": "application/json"},
            raw=b"{}",
        )[0]
        == 403
    )
    assert (
        phone.request(
            "POST",
            "/api/account/deletion",
            cookies=False,
            headers={TOKEN_HEADER: token, "Content-Type": "application/json"},
            raw=b"{}",
        )[0]
        == 401
    )


def test_wrong_pkce_and_changed_account_workspace_set_are_refused(phone):
    pair, app = consent(phone)
    token = mint(phone, app)
    assert use(phone, pair, token, verifier="x" * 43)[0] == 403
    assert use(phone, pair, token, verifier="short")[0] == 403
    phone.hub.db.create_workspace("new-workspace", app.user_id)
    assert use(phone, pair, token)[0] == 403
    assert rows(phone, "SELECT * FROM account_deletions") == []


def test_changed_grace_period_is_not_silently_accepted(phone, monkeypatch):
    pair, app = consent(phone)
    token = mint(phone, app)
    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION_GRACE_DAYS", "0")
    assert use(phone, pair, token)[0] == 403


def test_existing_block_rules_keep_the_same_message_and_spend_the_proof(phone):
    pair, app = consent(phone)
    token = mint(phone, app)
    with phone.hub.db._lock:
        phone.hub.db._conn.execute(
            "UPDATE users SET disabled_at='2000-01-01' WHERE email='backup-owner@example.test'"
        )
    web = phone.request(
        "POST",
        "/api/account/deletion",
        headers={"Content-Type": "application/json"},
        raw=b'{"confirm":"owner@example.com"}',
    )
    mobile = use(phone, pair, token)
    assert mobile[0] == web[0] == 409
    assert json.loads(mobile[2]) == json.loads(web[2])
    assert json.loads(mobile[2])["blockers"] == [{"code": "last_admin"}]
    assert use(phone, pair, token)[0] == 403


@pytest.mark.parametrize("account_role", ["member", "owner"])
def test_actual_deletion_keeps_shared_workspace_and_owner_handover_rules(phone, account_role):
    db, workspace = phone.hub.db, phone.hub.workspace
    other = db.create_user("member@example.test")
    db.add_member(workspace, other, "member", actor=phone.hub.admin)
    if account_role == "member":
        email, deleting, remaining = "member@example.test", other, phone.hub.admin
        phone.login(email)
    else:
        email, deleting, remaining = "owner@example.com", phone.hub.admin, other
    pair, app = consent(phone)
    token = mint(phone, app, email=email)
    assert use(phone, pair, token, confirm=email)[0] == 200
    assert db.role(workspace, remaining) == ("owner" if account_role == "member" else "member")
    assert deletion.purge_due(phone.hub.gateway) == []
    now = phone.hub.gateway.wall_clock()
    phone.hub.gateway.wall_clock = lambda: now + 15 * 86400
    assert deletion.purge_due(phone.hub.gateway)[0]["state"] == "purged"
    assert db.role(workspace, remaining) == "owner"
    assert db.user(deleting)["disabled_at"]
    assert rows(phone, "SELECT * FROM account_delete_tokens WHERE user_id=?", (deleting,)) == []
    assert rows(phone, "SELECT * FROM account_delete_checks WHERE user_id=?", (deleting,)) == []


def test_limits_are_per_person_and_per_client_even_without_general_limits(phone):
    from commonplace.account.person_token import LIMIT

    pair, app = consent(phone)
    token = mint(phone, app)
    phone.hub.gateway.limiter = ratelimit.TokenBuckets(clock=lambda: 0)
    for _ in range(LIMIT.capacity):
        assert phone.hub.gateway.limiter.check([(LIMIT, "client:" + phone.cid)]) == 0
    assert use(phone, pair, token)[0] == 429
    phone.hub.gateway.limiter = ratelimit.TokenBuckets(clock=lambda: 0)
    for _ in range(LIMIT.capacity):
        assert phone.hub.gateway.limiter.check([(LIMIT, "person:" + app.user_id)]) == 0
    assert use(phone, pair, token)[0] == 429


def test_proof_is_not_authentication_on_any_installed_route_or_tool(phone):
    from commonplace.gateway import _public_route
    from commonplace.http_util import send_json

    pair, app = consent(phone)
    token = mint(phone, app)
    assert phone.hub.gateway.authenticate("Bearer " + token) is None
    gateway = phone.hub.gateway
    reached = []

    def probe(h):
        reached.append(h.path)
        send_json(h, 200, {"unexpected": True})

    for method, path in list(gateway.routes):
        if _public_route(method, path) or path == "/oauth/authorize":
            continue
        original = gateway.routes[method, path]
        gateway.routes[method, path] = probe
        try:
            response = phone.request(
                method,
                path,
                cookies=False,
                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                raw=b"{}" if method != "GET" else None,
            )
            assert response[0] in (401, 404), (method, path, response[0])
        finally:
            gateway.routes[method, path] = original
    for method, prefix, _ in list(gateway.prefix_routes):
        path = prefix + "unavailable"
        if _public_route(method, path):
            continue
        assert phone.request(
            method,
            path,
            cookies=False,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            raw=b"{}" if method != "GET" else None,
        )[0] in (401, 404), (method, path)
    assert reached == []
    assert (
        phone.request(
            "POST",
            "/mcp",
            cookies=False,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            raw=b'{"id":1,"method":"tools/list"}',
        )[0]
        == 401
    )
    assert use(phone, pair, token)[0] == 200


def test_token_sentinel_is_absent_from_storage_audit_logs_errors_and_export(
    phone, caplog, monkeypatch
):
    import io
    import zipfile

    from commonplace import redact
    from commonplace.account import export

    caplog.set_level(logging.DEBUG)
    pair, app = consent(phone)
    token = mint(phone, app)
    assert token.startswith("cmmn_dt_")
    assert token not in redact.redact("proof " + token)[0]
    assert token not in repr(rows(phone, "SELECT * FROM account_delete_tokens"))
    assert token not in repr(rows(phone, "SELECT * FROM account_delete_checks"))
    response = use(phone, pair, token, confirm=token)
    assert response[0] == 400 and token not in response[2]
    assert token not in use(phone, pair, token)[2]
    assert token not in repr(rows(phone, "SELECT * FROM audit_log"))
    assert token not in caplog.text
    sink = io.BytesIO()
    export.write(phone.hub.gateway, export.plan(phone.hub.gateway, app.user_id), sink)
    with zipfile.ZipFile(sink) as archive:
        contents = b"\n".join(archive.read(name) for name in archive.namelist()).decode()
    assert token not in contents and store.digest(token) not in contents
    assert "[redacted]" in contents
    audit = rows(phone, "SELECT * FROM audit_log WHERE action LIKE 'account.delete.%'")
    minted = [r for r in audit if r["action"] == "account.delete.mint" and r["decision"] == "ok"]
    assert minted[0]["user_id"] == app.user_id
    assert json.loads(minted[0]["reason"])["client_id"] == phone.cid
    assert isinstance(json.loads(minted[0]["reason"])["checked_at"], int)
    assert all(
        json.loads(r["reason"])["client"] == "Mobile client" for r in audit if r["decision"] == "ok"
    )
    assert {r["decision"] for r in audit if r["action"] == "account.delete.use"} == {"ok", "deny"}


def test_confirmation_names_the_person_and_never_echoes_the_credential(phone, caplog):
    caplog.set_level(logging.DEBUG)
    pair, app = consent(phone)
    with phone.hub.db._lock:
        phone.hub.db._conn.execute(
            "UPDATE users SET name='Account holder' WHERE id=?", (app.user_id,)
        )
    path, request = start(phone, app)
    credential = sessions.create_login_link(phone.hub.db, app.user_email, app.workspace_id)
    status, headers, html = post(phone, path, {"request": request, "login_token": credential})
    assert status == 200 and "Account holder" in html
    assert "Delete workspace: home" in html and "14 days" in html
    assert credential not in html and credential not in repr(headers)
    assert credential not in caplog.text
    assert credential not in repr(rows(phone, "SELECT * FROM account_delete_checks"))
    assert credential not in repr(rows(phone, "SELECT * FROM audit_log"))


@pytest.mark.parametrize("change", ["workspace", "user", "client", "scope", "disabled"])
def test_changed_live_binding_never_widens_the_proof(phone, change):
    pair, app = consent(phone)
    token = mint(phone, app)
    db = phone.hub.db
    other_client = (
        phone.register(client_name="Other", redirect_uris=[CALLBACK])
        if change == "client"
        else None
    )
    with db._lock:
        if change == "workspace":
            other = db.create_workspace("other", app.user_id)
            db._conn.execute(
                "UPDATE oauth_person_grants SET workspace_id=? WHERE connection_id=?",
                (other, app.connection_id),
            )
        elif change == "user":
            other = db.create_user("other@example.test")
            db._conn.execute(
                "UPDATE oauth_person_grants SET user_id=? WHERE connection_id=?",
                (other, app.connection_id),
            )
        elif change == "client":
            db._conn.execute(
                "UPDATE oauth_person_grants SET client_id=? WHERE connection_id=?",
                (other_client, app.connection_id),
            )
        elif change == "scope":
            db._conn.execute(
                "UPDATE oauth_tokens SET scope='' WHERE connection_id=?", (app.connection_id,)
            )
        else:
            db._conn.execute("UPDATE users SET disabled_at='2000-01-01' WHERE id=?", (app.user_id,))
    assert use(phone, pair, token)[0] in (401, 403)
    assert rows(phone, "SELECT * FROM account_deletions") == []


def test_only_existing_deletion_post_accepts_the_extra_proof(phone):
    pair, app = consent(phone)
    token = mint(phone, app)
    for method, path in [
        ("GET", "/api/account"),
        ("GET", "/api/account/export?download=1"),
        ("DELETE", "/api/account/deletion"),
        ("GET", "/api/events"),
    ]:
        assert (
            phone.request(
                method,
                path,
                cookies=False,
                headers={
                    "Authorization": "Bearer " + pair["access_token"],
                    TOKEN_HEADER: token,
                    "Content-Type": "application/json",
                },
                raw=b"{}" if method != "GET" else None,
            )[0]
            == 403
        )
    assert use(phone, pair, token)[0] == 200


def test_body_cannot_select_another_account_or_workspace(phone):
    pair, app = consent(phone)
    token = mint(phone, app)
    for extra in ({"user_id": "other"}, {"workspace_id": "other"}, {"client_id": "other"}):
        assert (
            phone.request(
                "POST",
                "/api/account/deletion",
                cookies=False,
                headers={
                    "Authorization": "Bearer " + pair["access_token"],
                    TOKEN_HEADER: token,
                    "Content-Type": "application/json",
                },
                raw=json.dumps(
                    {"confirm": app.user_email, "code_verifier": VERIFIER, **extra}
                ).encode(),
            )[0]
            == 403
        )
    assert use(phone, pair, token)[0] == 200


def test_duplicate_proof_headers_cannot_be_chosen_by_parser_order(phone):
    import http.client

    pair, app = consent(phone)
    token = mint(phone, app)
    host, port = phone.hub.server.server_address
    raw = json.dumps({"confirm": app.user_email, "code_verifier": VERIFIER}).encode()
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.putrequest("POST", "/api/account/deletion")
        conn.putheader("Authorization", "Bearer " + pair["access_token"])
        conn.putheader(TOKEN_HEADER, token)
        conn.putheader(TOKEN_HEADER, "other")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(len(raw)))
        conn.endheaders(raw)
        response = conn.getresponse()
        assert response.status == 403
        assert token not in response.read().decode()
    finally:
        conn.close()
    assert use(phone, pair, token)[0] == 200


def test_audit_failure_never_mints_or_schedules_deletion(phone, monkeypatch):
    pair, app = consent(phone)
    path, request = ready(phone, app)
    real = phone.hub.db.audit

    def fail_mint(**kwargs):
        if kwargs["action"] == "account.delete.mint" and kwargs["decision"] == "ok":
            raise RuntimeError("audit unavailable")
        return real(**kwargs)

    monkeypatch.setattr(phone.hub.db, "audit", fail_mint)
    assert (
        post(phone, path, {"request": request, "decision": "confirm", "confirm": app.user_email})[0]
        == 500
    )
    assert rows(phone, "SELECT * FROM account_delete_tokens") == []
    monkeypatch.setattr(phone.hub.db, "audit", real)
    token = mint(phone, app)

    def fail_use(**kwargs):
        if kwargs["action"] == "account.delete.use" and kwargs["decision"] == "ok":
            raise RuntimeError("audit unavailable")
        return real(**kwargs)

    monkeypatch.setattr(phone.hub.db, "audit", fail_use)
    assert use(phone, pair, token)[0] == 500
    assert rows(phone, "SELECT * FROM account_deletions") == []


def test_proof_timestamps_and_binding_are_bounded(phone):
    pair, app = consent(phone)
    token = mint(phone, app)
    saved = rows(phone, "SELECT * FROM account_delete_tokens")[0]
    assert saved["token_hash"] == store.digest(token)
    assert 0 <= saved["created_at"] - saved["checked_at"] < 300
    assert 0 < saved["expires_at"] - saved["created_at"] <= 300
    assert saved["user_id"] == app.user_id and saved["client_id"] == phone.cid
    assert json.loads(saved["workspace_set"]) == [[app.workspace_id, "owner"]]
    assert saved["challenge_hash"] == store.challenge(VERIFIER)


def test_event_and_deletion_capabilities_are_separate_and_share_no_proof_state(phone):
    def feed(pair, suffix=""):
        return phone.request(
            "GET",
            "/api/events" + suffix,
            cookies=False,
            headers={"Authorization": "Bearer " + pair["access_token"]},
        )

    deletion_pair, _ = consent(phone)
    assert feed(deletion_pair)[0] == 403
    event_pair, event_app = consent(phone, checked=False, events=True)
    assert feed(event_pair)[0] == 200
    path = phone.authorize_path(
        phone.cid,
        redirect_uri=CALLBACK,
        scope="memory:read",
        purpose="account_deletion",
        connection_id=event_app.connection_id,
    )
    assert phone.request("GET", path)[0] == 403
    combined, app = consent(phone, events=True)
    before_feed = feed(combined)
    assert before_feed[0] == 200
    cursor = json.loads(before_feed[2])["cursor"]
    before = rows(
        phone,
        "SELECT * FROM oauth_person_grants WHERE connection_id=? AND capability='events:person'",
        (app.connection_id,),
    )
    assert before[0]["cursor_secret"]
    token = mint(phone, app)
    saved = rows(phone, "SELECT * FROM account_delete_tokens")
    assert "cursor_secret" not in saved[0]
    assert rows(
        phone,
        "SELECT cursor_secret FROM oauth_person_grants WHERE connection_id=? AND capability=?",
        (app.connection_id, CAPABILITY),
    ) == [{"cursor_secret": None}]
    assert feed(combined) == before_feed
    assert rows(phone, "SELECT * FROM account_delete_tokens") == saved
    assert feed(combined, "?cursor=" + token)[0] == 400
    assert use(phone, combined, cursor)[0] == 403
    assert use(phone, combined, token)[0] == 200
    assert (
        rows(
            phone,
            "SELECT * FROM oauth_person_grants WHERE connection_id=? AND capability='events:person'",
            (app.connection_id,),
        )
        == before
    )
    assert feed(combined) == before_feed


def test_revoking_a_deletion_proof_never_changes_event_cursor_state(phone, monkeypatch):
    pair, app = consent(phone, events=True)
    token = mint(phone, app)
    before = rows(
        phone,
        "SELECT * FROM oauth_person_grants WHERE connection_id=? AND capability='events:person'",
        (app.connection_id,),
    )
    monkeypatch.delenv("COMMONPLACE_PHONE_ACTIONS")
    assert use(phone, pair, token)[0] == 403
    monkeypatch.setenv("COMMONPLACE_PHONE_ACTIONS", "on")
    assert use(phone, pair, token)[0] == 403
    assert (
        phone.request(
            "GET",
            "/api/events",
            cookies=False,
            headers={"Authorization": "Bearer " + pair["access_token"]},
        )[0]
        == 200
    )
    assert (
        rows(
            phone,
            "SELECT * FROM oauth_person_grants WHERE connection_id=? AND capability='events:person'",
            (app.connection_id,),
        )
        == before
    )
