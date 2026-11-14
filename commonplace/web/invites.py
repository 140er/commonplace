"""Invite routes: an owner invites, a signed-in person joins, and a person
moves between the workspaces they are in.

``/api/invites`` is for a signed-in owner of the active workspace only; an
owner's token or OAuth connection is refused like a member. ``/app/join`` is
the page behind an invite link. It moves the secret out of the address into a
short-lived HttpOnly cookie, sends a signed-out visitor through the normal
sign-in, and asks for one confirming click before the invite is used.
"""

from __future__ import annotations

import html
import json
from urllib.parse import quote

from commonplace import invites, sessions, urls
from commonplace.control_db import ControlError
from commonplace.http_util import path_parts, query, read_json, require_principal, send_json

INVITE_COOKIE = "cmmn_invite"
INVITE_COOKIE_SECONDS = 3600
JOIN_PATH = "/app/join"
PREFIX = "/api/invites/"
OWNER_REQUIRED = "Sign in as a workspace owner to manage invites"

_PAGE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="referrer" content="no-referrer">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} - Commonplace</title><link rel="stylesheet" href="/app/app.css">
<link rel="stylesheet" href="/app/ui.css">
</head><body><main class="signin"><a class="brand-home" href="/app/" aria-label="Commonplace">
<img class="brand-v2" src="/app/brand/logo-small-dark.svg" alt="Commonplace"></a>
<h1>{title}</h1><p class="lead">{text}</p>{action}</main></body></html>"""
_CONFIRM = (
    '<form method="post" action="/app/join"><button class="blockbtn" name="confirm" value="1">'
    'Join workspace</button><p role="status" data-join-status></p></form>'
    "<noscript><p>Enable JavaScript, then open your invite link again to join.</p></noscript>"
    '<script type="module" src="/app/join.js"></script>'
)
_OPEN = '<p><a class="blockbtn" href="/app/">Open Commonplace</a></p>'
_MESSAGES = {
    "invalid": (
        404,
        "This invite does not work",
        "It was used, revoked or has expired, or the link is incomplete. "
        "Ask the workspace owner for a new invite.",
    ),
    "missing": (
        404,
        "Open your invite link again",
        "Open the invite link in this browser to continue.",
    ),
    "member": (200, "You are already in this workspace", "There is nothing to join."),
    "email": (
        403,
        "This invite is for a different account",
        "Sign out, then sign in with the email address the invite was sent to.",
    ),
}


def _cookie(gateway, value: str, max_age: int) -> str:
    attrs = [
        f"{INVITE_COOKIE}={value}",
        f"Path={JOIN_PATH}",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={max_age}",
    ]
    if gateway.secure_cookies:
        attrs.append("Secure")
    return "; ".join(attrs)


def _send(h, status, body=b"", content_type="text/html; charset=utf-8", headers=()):
    from . import CSP

    h.send_response(status)
    h.send_header("Content-Type", content_type)
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.send_header("Content-Security-Policy", CSP)
    h.send_header("X-Content-Type-Options", "nosniff")
    h.send_header("Referrer-Policy", "no-referrer")
    for name, value in headers:
        h.send_header(name, value)
    h.end_headers()
    h.wfile.write(body)


def _page(h, status, title, text, action="", headers=()):
    body = _PAGE.format(title=html.escape(title), text=html.escape(text), action=action)
    _send(h, status, body.encode("utf-8"), headers=headers)


def _refusal(gateway, h, kind):
    status, title, text = _MESSAGES[kind]
    # A refused secret is of no further use to this browser.
    headers = () if kind == "missing" else (("Set-Cookie", _cookie(gateway, "", 0)),)
    _page(h, status, title, text, _OPEN if kind == "member" else "", headers)


def _person(gateway, h):
    principal = gateway.principal_for(h)
    return principal if principal is not None and principal.person else None


def _move_session(gateway, h, principal, workspace_id) -> tuple:
    """A new session in ``workspace_id`` replaces the caller's current one."""
    previous = sessions.cookie_value(h.headers.get("Cookie"))
    session = sessions.start_session(gateway.db, principal.user_id, workspace_id)
    if previous:
        sessions.end_session(gateway.db, previous)
    return sessions.signin_headers(session, gateway.secure_cookies)


def _owner(gateway, h):
    principal = require_principal(gateway, h)
    if principal is None:
        return None
    if not principal.person or principal.role != "owner":
        send_json(h, 403, {"error": OWNER_REQUIRED})
        return None
    return principal


def _collection(gateway, h):
    db = gateway.db
    principal = _owner(gateway, h)
    if principal is None:
        return
    try:
        if h.command == "GET":
            rows = invites.listing(db, principal.workspace_id, principal.user_id)
            send_json(h, 200, {"invites": rows})
            return
        try:
            data = read_json(h)
        except ValueError:
            return
        if set(data) - {"email", "days"}:
            raise ControlError("use email and days only")
        view, secret = invites.create(
            db,
            principal.workspace_id,
            principal.user_id,
            email=data.get("email"),
            days=data.get("days"),
        )
    except ControlError as exc:
        send_json(h, 400, {"error": str(exc)})
        return
    # The only response that ever carries the secret.
    link = urls.app_link(f"{JOIN_PATH}?t={secret}")
    send_json(h, 201, {"invite": view, "secret": secret, "link": link})


def _revoke(gateway, h):
    db = gateway.db
    principal = _owner(gateway, h)
    if principal is None:
        return
    parts = path_parts(h, PREFIX)
    if len(parts) != 2 or parts[1] != "revoke":
        send_json(h, 404, {"error": "Invite not found"})
        return
    try:
        view = invites.revoke(db, principal.workspace_id, parts[0], principal.user_id)
    except ControlError as exc:
        send_json(h, 409, {"error": str(exc)})
        return
    if view is None:
        send_json(h, 404, {"error": "Invite not found"})
        return
    send_json(h, 200, {"invite": view})


def _join(gateway, h):
    db = gateway.db
    h.close_connection = True
    secret = query(h).get("t")
    if h.command == "GET" and secret is not None:
        if not invites.looks_like_secret(secret):
            _refusal(gateway, h, "invalid")
            return
        # Keep the secret out of the address bar, history and sign-in state.
        cookie = _cookie(gateway, secret, INVITE_COOKIE_SECONDS)
        _send(h, 302, headers=(("Location", JOIN_PATH), ("Set-Cookie", cookie)))
        return
    secret = sessions.cookie_value(h.headers.get("Cookie"), INVITE_COOKIE)
    if not secret:
        _refusal(gateway, h, "missing")
        return
    principal = _person(gateway, h)
    if principal is None:
        if h.command == "GET":
            _send(h, 302, headers=(("Location", "/app/?next=" + quote(JOIN_PATH, safe="")),))
        else:
            _page(h, 401, "Sign in again", "Sign in, then open your invite link again.", _OPEN)
        return
    try:
        if h.command == "GET":
            found = invites.peek(db, secret, principal.user_id)
            _page(
                h,
                200,
                "Join " + found["workspace_name"],
                "You will join this workspace as a member, signed in as "
                + principal.user_email
                + ".",
                _CONFIRM,
            )
            return
        from commonplace.gateway import _same_origin_allowed

        # The shared guard also serves non-browser clients, but joining requires
        # an explicit browser Origin. Fetch metadata alone is not CSRF proof.
        if not h.headers.get_all("Origin", []) or not _same_origin_allowed(gateway, h):
            _page(
                h,
                403,
                "Open your invite link again",
                "A same-origin request is required. Open your invite link in this browser and try again.",
                '<p><a href="/app/join">Try again</a></p>',
            )
            return
        workspace_id = invites.accept(db, secret, principal.user_id)
    except invites.InviteError as exc:
        _refusal(gateway, h, exc.kind)
        return
    headers = [("Location", "/app/"), ("Set-Cookie", _cookie(gateway, "", 0))]
    headers.extend(_move_session(gateway, h, principal, workspace_id))
    _send(h, 303, headers=headers)


def _workspaces(gateway, h):
    db = gateway.db
    principal = require_principal(gateway, h)
    if principal is None:
        return
    if not principal.person:
        send_json(h, 403, {"error": "Sign in to see your workspaces"})
        return
    if h.command == "GET":
        rows = invites.memberships(db, principal.user_id, principal.workspace_id)
        send_json(h, 200, {"workspaces": rows})
        return
    try:
        data = read_json(h)
    except ValueError:
        return
    target = data.get("workspace")
    # Not a member and no such workspace answer alike.
    if (
        set(data) != {"workspace"}
        or not isinstance(target, str)
        or db.role(target, principal.user_id) is None
    ):
        send_json(h, 404, {"error": "Workspace not found"})
        return
    headers = _move_session(gateway, h, principal, target)
    body = json.dumps({"workspace": target}).encode("utf-8")
    h.close_connection = True
    _send(h, 200, body, "application/json", headers)


def _create_workspace(gateway, h):
    from commonplace.gateway import _same_origin_allowed

    principal = require_principal(gateway, h)
    if principal is None:
        return
    # An app grant may represent a person too. Creation requires their browser session.
    if (
        not principal.person
        or principal.auth != "session"
        or h.headers.get("Authorization")
        or not sessions.cookie_value(h.headers.get("Cookie"))
    ):
        send_json(h, 403, {"error": "Sign in in your browser to create a workspace."})
        return
    if not h.headers.get_all("Origin", []) or not _same_origin_allowed(gateway, h):
        send_json(h, 403, {"error": "Open Commonplace in this browser and try again."})
        return
    try:
        data = read_json(h)
    except ValueError:
        return
    try:
        if set(data) != {"name", "request_id"}:
            raise ValueError("Use a workspace name and request id only.")
        workspace, created = invites.create_workspace(
            gateway.db, principal.user_id, name=data["name"], request_id=data["request_id"]
        )
    except (ValueError, ControlError) as exc:
        gateway.db.audit(
            action="workspace.create",
            decision="deny",
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            auth="session",
            reason="creation_refused",
        )
        send_json(h, getattr(exc, "status", 400), {"error": str(exc)})
        return
    send_json(h, 201 if created else 200, {"workspace": workspace})


def install(gateway):
    invites.ensure_schema(gateway.db)

    def bound(handler):
        return lambda h: handler(gateway, h)

    gateway.add_route("GET", "/api/invites", bound(_collection))
    gateway.add_route("POST", "/api/invites", bound(_collection))
    gateway.add_prefix_route("POST", PREFIX, bound(_revoke))
    gateway.add_route("GET", JOIN_PATH, bound(_join))
    gateway.add_route("POST", JOIN_PATH, bound(_join))
    gateway.add_route("GET", "/api/workspaces", bound(_workspaces))
    gateway.add_route("POST", "/api/workspaces", bound(_create_workspace))
    gateway.add_route("POST", "/api/workspaces/switch", bound(_workspaces))
