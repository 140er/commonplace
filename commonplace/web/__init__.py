"""The local, cookie-authenticated Commonplace web app."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlsplit

from commonplace import events, sessions
from commonplace.http_util import query, require_principal, send_json

STATIC = Path(__file__).parent / "static"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self'; font-src 'self'; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


def _response(h, status, body=b"", content_type="text/plain; charset=utf-8", headers=None):
    h.send_response(status)
    h.send_header("Content-Type", content_type)
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.send_header("Content-Security-Policy", CSP)
    h.send_header("X-Content-Type-Options", "nosniff")
    h.send_header("Referrer-Policy", "no-referrer")
    for name, value in (headers or {}).items():
        h.send_header(name, value)
    h.end_headers()
    h.wfile.write(body)


def _static(h):
    path = unquote(urlsplit(h.path).path)
    relative = path.removeprefix("/app").lstrip("/")
    if (
        "\\" in relative
        or "\x00" in relative
        or any(part in ("..", ".") for part in relative.split("/"))
    ):
        _response(h, 404, b"Not found")
        return
    target = (STATIC / relative).resolve()
    if not target.is_relative_to(STATIC.resolve()):
        _response(h, 404, b"Not found")
        return
    if not target.is_file():
        # Extensionless app paths are SPA routes. Missing assets stay real 404s.
        if Path(relative).suffix or relative.startswith(("api/", "fonts/")):
            _response(h, 404, b"Not found")
            return
        target = STATIC / "index.html"
    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if target.suffix == ".js":
        mime = "text/javascript"
    if mime.startswith("text/"):
        mime += "; charset=utf-8"
    _response(h, 200, target.read_bytes(), mime)


def install(gateway):
    def login(h):
        session = sessions.redeem_login_link(gateway.db, query(h).get("t", ""))
        if not session or sessions.session_principal(gateway.db, session) is None:
            _response(h, 400, b"Sign in link is invalid or expired")
            return
        _response(
            h,
            302,
            headers={
                "Location": "/app/",
                "Set-Cookie": sessions.set_cookie(session, gateway.secure_cookies),
            },
        )

    def logout(h):
        # SameSite=Lax cookies and the origin check prevent cross-site sign-out.
        origin = h.headers.get("Origin")
        if origin and urlsplit(origin).netloc != h.headers.get("Host"):
            send_json(h, 403, {"error": "Origin refused"})
            return
        session = sessions.cookie_value(h.headers.get("Cookie"))
        if session:
            sessions.end_session(gateway.db, session)
        h.close_connection = True
        _response(h, 204, headers={"Set-Cookie": sessions.clear_cookie()})

    def me(h):
        principal = require_principal(gateway, h)
        if principal is not None:
            send_json(
                h,
                200,
                {
                    "user": principal.user_id,
                    "email": principal.user_email,
                    "workspace": principal.workspace_id,
                    "actor": principal.actor,
                    "people": _people(gateway.db, principal.workspace_id),
                },
            )

    def feed(h):
        principal = require_principal(gateway, h)
        if principal is None:
            return
        # Events carry labels without theme ACLs. Restrict unfilterable metadata
        # to people and agents already permitted to read the entire workspace.
        if not principal.person and not (
            principal.policy.has_scope("events:read") and principal.policy.sees_all_wings
        ):
            send_json(h, 403, {"error": "Workspace event access required"})
            return
        try:
            seq = int(query(h).get("after", "0"))
            if not 0 <= seq <= 9223372036854775807:
                raise ValueError
        except ValueError:
            send_json(h, 400, {"error": "Invalid event cursor"})
            return
        # The snapshot and read share the lock. The contract's seq is the latest
        # sequence, so return every unread event rather than skipping a batch.
        with gateway.db._lock:
            latest = events.latest_seq(gateway.db, principal.workspace_id)
            batch = events.after(gateway.db, principal.workspace_id, min(seq, latest), latest or 1)
        send_json(h, 200, {"events": batch, "seq": latest})

    def connections(h):
        principal = require_principal(gateway, h)
        if principal is None:
            return
        if principal.role != "owner":
            send_json(h, 403, {"error": "Owner access required"})
            return
        fields = (
            "id",
            "name",
            "client",
            "auth",
            "user_id",
            "created_at",
            "expires_at",
            "revoked_at",
            "last_used_at",
        )
        rows = gateway.db.connections(principal.workspace_id)
        send_json(h, 200, {"connections": [{key: row[key] for key in fields} for row in rows]})

    gateway.add_route("GET", "/app/login", login)
    gateway.add_route("POST", "/app/logout", logout)
    gateway.add_route("GET", "/api/me", me)
    gateway.add_route("GET", "/api/events", feed)
    gateway.add_route("GET", "/api/connections", connections)
    gateway.add_prefix_route("GET", "/app/", _static)


def _people(db, workspace_id) -> dict:
    """``user:<id>`` -> the name the app shows: the user's name, else the
    part of the email before the @."""
    people = {}
    for member in db.members(workspace_id):
        name = db.user(member["id"])["name"] or member["email"].split("@", 1)[0]
        people[f"user:{member['id']}"] = name
    return people
