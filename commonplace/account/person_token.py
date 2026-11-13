"""Browser credential re-check and one-use proof for the existing deletion route.

Deletion proofs are deliberately absent from every authenticator and scope list.
Only the deletion POST reads them, together with the consented client's own bearer.
"""

from __future__ import annotations

import json
import re
import secrets
from html import escape

from commonplace import decide_grant, person_actions, ratelimit, sessions
from commonplace.boards._http import HTTPError
from commonplace.boards.service import BoardService
from commonplace.extensions import ToolContext
from commonplace.http_util import read_json, send_json
from commonplace.oauth import store
from commonplace.oauth.http import OAuthError, body, query, redirect

from . import deletion, tables

SECONDS = 300
COOKIE = "cmmn_delete_check"
HEADER = "X-Account-Deletion-Token"
LIMIT = ratelimit.Limit("account-delete-proof", 20, 300)
SCHEMA = """
CREATE TABLE IF NOT EXISTS account_delete_checks (
    request_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    connection_id TEXT NOT NULL REFERENCES connections(id),
    client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
    browser_hash TEXT NOT NULL,
    session_hash TEXT NOT NULL,
    parameters TEXT NOT NULL,
    workspace_set TEXT NOT NULL,
    grace_days INTEGER NOT NULL,
    checked_at INTEGER,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS account_delete_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    connection_id TEXT NOT NULL REFERENCES connections(id),
    client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
    challenge_hash TEXT NOT NULL,
    session_hash TEXT NOT NULL,
    workspace_set TEXT NOT NULL,
    grace_days INTEGER NOT NULL,
    checked_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER,
    revoked_at INTEGER
);
CREATE INDEX IF NOT EXISTS account_delete_tokens_person ON account_delete_tokens(user_id);
"""


def _refuse():
    raise OAuthError("access_denied", "Account deletion proof refused", 403)


def _enabled(db):
    if person_actions.enabled() and deletion.enabled():
        return
    # An observed kill switch invalidates outstanding checks and proofs permanently.
    with db._lock:
        db._conn.execute("DELETE FROM account_delete_checks")
        db._conn.execute(
            "UPDATE account_delete_tokens SET revoked_at = ? WHERE revoked_at IS NULL",
            (store.now(),),
        )
    _refuse()


def _audit(db, action, decision, row=None, reason=""):
    db.audit(
        action="account.delete." + action,
        decision=decision,
        user_id=row["user_id"] if row else None,
        workspace_id=row["workspace_id"] if row else None,
        connection_id=row["connection_id"] if row else None,
        auth="session" if action in ("check", "mint") else "oauth",
        reason=reason,
    )


def _limit(gateway, row):
    # Always applies to this destructive authority, even with general limiting disabled.
    if gateway.limiter.check(
        [(LIMIT, "person:" + row["user_id"]), (LIMIT, "client:" + row["client_id"])]
    ):
        raise OAuthError("slow_down", "Try again later", 429)


def _workspace_set(conn, user_id):
    return json.dumps(
        [
            list(row)
            for row in conn.execute(
                "SELECT workspace_id,role FROM memberships WHERE user_id=? ORDER BY workspace_id",
                (user_id,),
            )
        ],
        separators=(",", ":"),
    )


def _live(gateway, connection_id, client_id):
    db = gateway.db
    row = db._conn.execute(
        "SELECT g.*,c.client_name FROM oauth_person_grants g "
        "JOIN oauth_clients c ON c.client_id=g.client_id "
        "WHERE g.connection_id=? AND g.client_id=? AND g.capability=? AND g.revoked_at IS NULL",
        (connection_id, client_id, person_actions.DELETE_CAPABILITY),
    ).fetchone()
    connection = store._live_connection(db._conn, connection_id)
    if (
        row is None
        or connection is None
        or row["user_id"] != connection["user_id"]
        or row["workspace_id"] != connection["workspace_id"]
        or decide_grant.machinery(db, connection)
    ):
        _refuse()
    person = sessions.person_principal(db, row["user_id"], row["workspace_id"])
    if person is None:
        _refuse()
    service = BoardService(ToolContext(principal=person, gateway=gateway))
    if "conn:" + connection["name"] in service.coordinator_actors():
        _refuse()
    return row, person


def _session(conn, session_hash, row):
    return conn.execute(
        "SELECT 1 FROM sessions WHERE id_hash=? AND user_id=? AND workspace_id=? "
        "AND revoked_at IS NULL AND expires_at>?",
        (session_hash, row["user_id"], row["workspace_id"], store.iso()),
    ).fetchone()


def _browser(h):
    cookies = h.headers.get_all("Cookie", [])
    if len(cookies) != 1:
        return ""
    values = [
        p.strip().partition("=")[2]
        for p in cookies[0].split(";")
        if p.strip().partition("=")[0] == COOKIE
    ]
    return values[0] if len(values) == 1 and re.fullmatch(r"[A-Za-z0-9_-]{43}", values[0]) else ""


def _same_origin(gateway, h):
    from commonplace.gateway import _origin

    origins, hosts = h.headers.get_all("Origin", []), h.headers.get_all("Host", [])
    scheme = "https" if gateway.secure_cookies else "http"
    if (
        h.headers.get_all("Authorization", [])
        or len(origins) != 1
        or len(hosts) != 1
        or _origin(origins[0]) is None
        or _origin(origins[0]) != _origin(scheme + "://" + hosts[0])
        or h.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin"
    ):
        _refuse()


def _parameters(db, values):
    from commonplace.oauth import _validate_authorization

    client, error = _validate_authorization(db, values)
    if (
        error
        or values.get("purpose") != "account_deletion"
        or values["redirect_uri"] != person_actions.NATIVE_REDIRECT
        or values["scope"] != "memory:read"
        or set(values)
        - {
            "client_id",
            "redirect_uri",
            "response_type",
            "code_challenge",
            "code_challenge_method",
            "resource",
            "scope",
            "state",
            "purpose",
            "connection_id",
        }
        or len(values.get("state", "")) > 200
        or not re.fullmatch(r"conn_[a-f0-9]{16}", values.get("connection_id", ""))
    ):
        _refuse()
    return client


def authorize(gateway, h, parameters, client):
    """A link can only open a fresh credential form; it cannot confirm or mint."""
    from commonplace.oauth import _page

    db, row = gateway.db, None
    try:
        _enabled(db)
        _parameters(db, parameters)
        if h.headers.get_all("Authorization", []):
            _refuse()
        with db._lock:
            row, _ = _live(gateway, parameters["connection_id"], client["client_id"])
            _limit(gateway, row)
            session = sessions.cookie_value(h.headers.get("Cookie")) or ""
            # An absent session is fine: this page performs the actual sign-in.
            if session and not _session(db._conn, store.digest(session), row):
                _refuse()
            browser, request = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            db._conn.execute(
                "DELETE FROM account_delete_checks WHERE expires_at<=?", (store.now(),)
            )
            db._conn.execute(
                "INSERT INTO account_delete_checks(request_hash,user_id,workspace_id,connection_id,"
                "client_id,browser_hash,session_hash,parameters,workspace_set,grace_days,expires_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    store.digest(request),
                    row["user_id"],
                    row["workspace_id"],
                    row["connection_id"],
                    row["client_id"],
                    store.digest(browser),
                    store.digest(session) if session else "",
                    json.dumps(parameters),
                    "",
                    deletion.grace_days(),
                    store.now() + SECONDS,
                ),
            )
        cookie = (
            f"{COOKIE}={browser}; Path=/oauth/authorize; HttpOnly; SameSite=Strict; Max-Age=600"
        )
        if gateway.secure_cookies:
            cookie += "; Secure"
        _page(
            h,
            "<h1>Sign in again</h1><p>To request account deletion, enter a new, unused "
            "sign-in token on this website. Your app never receives this credential. "
            "An existing sign-in is not enough.</p>"
            f'<form method="post"><input type="hidden" name="request" value="{request}">'
            '<input type="password" name="login_token" aria-label="Sign-in token" '
            'autocomplete="off" required><button type="submit">Sign in again</button></form>'
            f'<form method="post"><input type="hidden" name="request" value="{request}">'
            '<button name="decision" value="cancel">Cancel</button></form>',
            headers=(("Set-Cookie", cookie),),
        )
    except OAuthError as exc:
        _audit(db, "mint", "deny", row, exc.error)
        raise


def _confirm_page(gateway, h, row, person, request, session):
    from commonplace.oauth import _page

    db = gateway.db
    user = db.user(person.user_id)
    with db._lock:
        effects = deletion.effects(db._conn, tables.present(db._conn), person.user_id)
    lines = "".join(
        f"<li>{label}: {escape(item['name'])}</li>"
        for key, label in (
            ("delete_workspaces", "Delete workspace"),
            ("leave_workspaces", "Leave workspace"),
            ("promote_owner", "Hand workspace to another member"),
        )
        for item in effects[key]
    )
    _page(
        h,
        f"<h1>Confirm account deletion</h1><p>{escape(user['name'] or user['email'])} "
        f"({escape(user['email'])}), this asks to delete your entire account. "
        f"Your account remains usable for {row['grace_days']} days, and you can cancel "
        "the deletion on the website during that waiting period. Afterward, your account "
        "and workspaces with no other members are deleted. Shared workspaces remain; "
        "another active member takes over a workspace you own alone.</p>"
        f"<ul>{lines}</ul><p>Existing protection rules are checked again when the app requests "
        "deletion. This confirmation gives the app a one-use proof lasting five minutes; "
        "it grants no other access.</p>"
        f'<form method="post"><input type="hidden" name="request" value="{request}">'
        '<label>Type your account email to confirm <input name="confirm" type="email" '
        'required autocomplete="off"></label>'
        '<button name="decision" value="confirm">Confirm account deletion</button></form>'
        f'<form method="post"><input type="hidden" name="request" value="{request}">'
        '<button name="decision" value="cancel">Cancel</button></form>',
        headers=sessions.signin_headers(session, gateway.secure_cookies),
    )


def submit(gateway, h):
    from commonplace.oauth import _response

    db, row = gateway.db, None
    try:
        _enabled(db)
        _same_origin(gateway, h)
        data = body(h)
        request = data.get("request", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", request):
            _refuse()
        with db._lock, db._tx() as conn:
            row = conn.execute(
                "SELECT * FROM account_delete_checks WHERE request_hash=?", (store.digest(request),)
            ).fetchone()
            if (
                row is None
                or row["expires_at"] <= store.now()
                or not _browser(h)
                or not secrets.compare_digest(row["browser_hash"], store.digest(_browser(h)))
            ):
                _refuse()
            parameters = json.loads(row["parameters"])
            # Query/form swapping cannot redirect a proof to another authorization.
            incoming = query(h.path)
            _parameters(db, incoming)
            if incoming != parameters:
                _refuse()
            _, person = _live(gateway, row["connection_id"], row["client_id"])
            current_session = sessions.cookie_value(h.headers.get("Cookie")) or ""
            if row["session_hash"] and (
                not secrets.compare_digest(row["session_hash"], store.digest(current_session))
                or not _session(conn, row["session_hash"], row)
            ):
                _refuse()
            if data.get("decision") == "cancel":
                conn.execute(
                    "DELETE FROM account_delete_checks WHERE request_hash=?", (row["request_hash"],)
                )
                location = _response(parameters, error="access_denied")
                _audit(db, "mint", "cancel", row)
                session = None
            elif row["checked_at"] is None:
                _limit(gateway, row)
                if set(data) != {"request", "login_token"}:
                    _refuse()
                credential = data.get("login_token", "")
                if not re.fullmatch(r"[A-Za-z0-9_-]{43}", credential):
                    _refuse()
                # Validate identity before consuming; never turn a different person's
                # credential into authority for the account selected by the app.
                checked = conn.execute(
                    "UPDATE login_links SET used_at=? WHERE token_hash=? AND user_id=? "
                    "AND workspace_id=? AND used_at IS NULL AND expires_at>?",
                    (
                        store.iso(),
                        store.digest(credential),
                        row["user_id"],
                        row["workspace_id"],
                        store.iso(),
                    ),
                ).rowcount
                if checked != 1:
                    _refuse()
                session = sessions.start_session(
                    db, row["user_id"], row["workspace_id"], via="deletion_recheck"
                )
                request = secrets.token_urlsafe(32)
                conn.execute(
                    "UPDATE account_delete_checks SET request_hash=?,session_hash=?,checked_at=?,"
                    "workspace_set=?,expires_at=? WHERE request_hash=? AND checked_at IS NULL",
                    (
                        store.digest(request),
                        store.digest(session),
                        store.now(),
                        _workspace_set(conn, row["user_id"]),
                        store.now() + SECONDS,
                        row["request_hash"],
                    ),
                )
                _audit(db, "check", "ok", row, "client_id=" + row["client_id"])
                location = None
            else:
                if (
                    set(data) != {"request", "decision", "confirm"}
                    or data["decision"] != "confirm"
                    or not 0 <= store.now() - row["checked_at"] < SECONDS
                    or row["workspace_set"] != _workspace_set(conn, row["user_id"])
                    or row["grace_days"] != deletion.grace_days()
                    or data["confirm"].strip().casefold() != person.user_email.casefold()
                ):
                    _refuse()
                _limit(gateway, row)
                token = "cmmn_dt_" + secrets.token_urlsafe(32)
                conn.execute(
                    "INSERT INTO account_delete_tokens(token_hash,user_id,workspace_id,connection_id,"
                    "client_id,challenge_hash,session_hash,workspace_set,grace_days,checked_at,created_at,expires_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        store.digest(token),
                        row["user_id"],
                        row["workspace_id"],
                        row["connection_id"],
                        row["client_id"],
                        parameters["code_challenge"],
                        row["session_hash"],
                        row["workspace_set"],
                        row["grace_days"],
                        row["checked_at"],
                        store.now(),
                        store.now() + SECONDS,
                    ),
                )
                conn.execute(
                    "DELETE FROM account_delete_checks WHERE request_hash=?", (row["request_hash"],)
                )
                _audit(
                    db,
                    "mint",
                    "ok",
                    row,
                    json.dumps({"client_id": row["client_id"], "checked_at": row["checked_at"]}),
                )
                location = _response(parameters, deletion_token=token, expires_in=SECONDS)
                session = None
        if location:
            redirect(h, location, status=303)
        else:
            _confirm_page(gateway, h, row, person, request, session)
    except OAuthError as exc:
        _audit(db, "mint", "deny", row, exc.error)
        raise


def delete(gateway, h):
    """Consume before invoking the unchanged deletion checks; a refusal spends the proof."""
    from commonplace.oauth.http import send_error

    db, row = gateway.db, None
    try:
        _enabled(db)
        tokens = h.headers.get_all(HEADER, [])
        if len(tokens) != 1 or not re.fullmatch(r"cmmn_dt_[A-Za-z0-9_-]{43}", tokens[0]):
            _refuse()
        # The header is only considered on the exact deletion POST; never an auth source.
        app, person, origin = person_actions.authority(gateway, h, person_actions.DELETE_CAPABILITY)
        data = read_json(h)
        if (
            set(data) != {"confirm", "code_verifier"}
            or not isinstance(data["code_verifier"], str)
            or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", data["code_verifier"])
        ):
            _refuse()
        with db._lock:
            with db._tx() as conn:
                row = conn.execute(
                    "SELECT * FROM account_delete_tokens WHERE token_hash=?",
                    (store.digest(tokens[0]),),
                ).fetchone()
                if row is None:
                    _refuse()
                _limit(gateway, row)
                # Recheck live bearer and grant after acquiring the write lock.
                app, person, origin = person_actions.authority(
                    gateway, h, person_actions.DELETE_CAPABILITY
                )
                _live(gateway, app.connection_id, origin["client_id"])
                if (
                    row["user_id"] != person.user_id
                    or row["workspace_id"] != person.workspace_id
                    or row["connection_id"] != app.connection_id
                    or row["client_id"] != origin["client_id"]
                    or not secrets.compare_digest(
                        row["challenge_hash"], store.challenge(data["code_verifier"])
                    )
                    or row["workspace_set"] != _workspace_set(conn, person.user_id)
                    or row["grace_days"] != deletion.grace_days()
                    or not _session(conn, row["session_hash"], row)
                    or row["checked_at"] > row["created_at"]
                    or row["created_at"] - row["checked_at"] >= SECONDS
                    or not row["created_at"] <= store.now() < row["expires_at"]
                ):
                    _refuse()
                used = conn.execute(
                    "UPDATE account_delete_tokens SET used_at=? WHERE token_hash=? "
                    "AND used_at IS NULL AND revoked_at IS NULL AND expires_at>?",
                    (store.now(), row["token_hash"], store.now()),
                ).rowcount
                if used != 1:
                    _refuse()
                _audit(db, "use", "ok", row, "client_id=" + row["client_id"])
            # Still holding the control lock: signout/disconnect cannot race the request.
            result = deletion._record_request(
                gateway, person, deletion._now(gateway), data["confirm"]
            )
        send_json(h, 200, result)
    except deletion.Refused as exc:
        _audit(db, "use", "deny", row, exc.body["code"])
        send_json(h, exc.status, exc.body)
    except (OAuthError, HTTPError) as exc:
        _audit(db, "use", "deny", row, "proof_refused")
        send_error(
            h,
            exc
            if isinstance(exc, OAuthError)
            else OAuthError("access_denied", "Account deletion proof refused", 403),
        )
    except ValueError:
        # read_json already sent the bounded parse error.
        _audit(db, "use", "deny", row, "invalid_request")
