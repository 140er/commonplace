"""The local, cookie-authenticated Commonplace web app."""

from __future__ import annotations

import logging
import mimetypes
import threading
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from pathlib import Path
from urllib.parse import unquote, urlsplit

from commonplace import events, plans, sessions, sync_feed, urls
from commonplace.http_util import log_failure, query, require_principal, send_json

LOG = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
CONNECTION_WINGS_TIMEOUT_S = 1.0
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


def signin_recovery(h, state, status, headers=None):
    """A token-free recovery document for navigations that cannot reach the SPA."""
    title, message = {
        "expired": (
            "Get a new link",
            "This sign-in link is invalid or expired. Links work once and for 15 minutes. Request a new link to continue.",
        ),
        "throttled": (
            "Try again shortly",
            "Too many sign-in attempts. Wait a moment, then retry. Your email and workspace are safe.",
        ),
        "sent": (
            "Check your email",
            "If this email can sign in, a link is on its way. Check your inbox and spam folder.",
        ),
    }[state]
    destination = "/app/" if state == "sent" else f"/app/?signin={state}"
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} - Commonplace</title><link rel="stylesheet" href="/app/app.css">
<link rel="stylesheet" href="/app/ui.css"><link rel="stylesheet" href="/app/workbench.css">
<link rel="stylesheet" href="/app/signin-recovery.css"></head><body>
<main class="signin-recovery"><a class="brand-home" href="/app/">Commonplace</a>
<h1>{title}</h1><p role="status">{message}</p>
<a class="btn primary" href="{destination}">Retry</a>
</main></body></html>""".encode()
    _response(h, status, body, "text/html; charset=utf-8", headers)


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
        target = (STATIC / "index.html").resolve()
        if not target.is_relative_to(STATIC.resolve()):
            _response(h, 404, b"Not found")
            return
    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if target.suffix == ".js":
        mime = "text/javascript"
    if mime.startswith("text/"):
        mime += "; charset=utf-8"
    _response(h, 200, target.read_bytes(), mime)


def _connection_wings(gateway, principal, slot):
    """Bound an optional read even if worker startup or queueing blocks."""
    from commonplace.extensions import ToolError
    from commonplace.rules.checks import deadline

    from .agents import grantable_wings

    if not slot.acquire(blocking=False):
        raise ToolError("Memory wing listing is busy")
    result = Future()

    def list_wings():
        try:
            # Also bound the worker transport, not just the HTTP caller's wait.
            with deadline(CONNECTION_WINGS_TIMEOUT_S):
                wings = grantable_wings(gateway, principal)
            result.set_result(wings)
        except Exception as exc:
            result.set_exception(exc)
        finally:
            slot.release()

    try:
        threading.Thread(target=list_wings, daemon=True).start()
    except Exception:
        slot.release()
        raise
    try:
        return result.result(timeout=CONNECTION_WINGS_TIMEOUT_S)
    except (FutureTimeoutError, TimeoutError):
        raise ToolError("Memory wing listing timed out") from None


def _phone_feed(gateway, h, principal):
    from commonplace import person_actions

    if principal.person or not person_actions.enabled():
        return False
    from commonplace import person_events

    return person_events.handle(gateway, h)


def install(gateway):
    # A timed-out read may still be unwinding; never queue more optional reads.
    connection_wings_slot = threading.BoundedSemaphore()

    def login(h):
        token = query(h).get("t", "")
        if not token:
            _response(h, 302, headers={"Location": "/app/"})
            return
        session = sessions.redeem_login_link(gateway.db, token)
        if not session or sessions.session_principal(gateway.db, session) is None:
            _response(h, 302, headers={"Location": "/app/?signin=expired"})
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
        if require_principal(gateway, h) is None:
            return
        session = sessions.cookie_value(h.headers.get("Cookie"))
        if session:
            sessions.end_session(gateway.db, session)
        h.close_connection = True
        _response(h, 204, headers={"Set-Cookie": sessions.clear_cookie()})

    def me(h):
        principal = require_principal(gateway, h)
        if principal is not None:
            from commonplace.instance_admin import flag

            from . import admin

            try:
                context = admin.ReadContext(gateway.admin_clock)
                can_instance = admin.instance_authorized(gateway, principal, context)
                can_manage = flag(gateway, principal, context)
            except Exception as exc:
                log_failure(LOG, "Admin capability unavailable", exc)
                can_instance = False
                can_manage = False
            send_json(
                h,
                200,
                {
                    "can_admin": principal.person and principal.role == "owner",
                    "can_admin_instance": can_instance,
                    "can_manage_instance": can_manage,
                    "user": principal.user_id,
                    "email": principal.user_email,
                    "workspace": principal.workspace_id,
                    "workspace_name": gateway.db.workspace(principal.workspace_id)["name"],
                    "actor": principal.actor,
                    "people": _people(gateway.db, principal.workspace_id),
                },
            )

    def plan(h):
        principal = require_principal(gateway, h)
        if principal is None:
            return
        if not plans.enabled():
            send_json(h, 200, {"enabled": False})
            return
        account = plans.workspace_account(gateway.db, principal.workspace_id)
        current = plans.get_plan(gateway.db, account) if account else None
        used = (
            gateway.storage_meter.used(
                principal.workspace_id, gateway.workspace_dir(principal.workspace_id)
            )
            if account
            else 0
        )
        send_json(
            h,
            200,
            {
                "enabled": True,
                "plan": current.name if current else None,
                "storage": {"used": used, "limit": current.storage_bytes if current else None},
                "transcription": {
                    "used_today": plans.transcription_used(gateway.db, account) if account else 0,
                    "limit": current.transcription_seconds if current else None,
                },
                "sync": {
                    "interval_seconds": plans.sync_interval(gateway.db, account) if account else 0
                },
            },
        )

    def feed(h):
        principal = require_principal(gateway, h)
        if principal is None or _phone_feed(gateway, h, principal):
            return
        if query(h).get("feed") == "sync":
            sync_feed.handle(gateway, h, principal)
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
        if principal.person and principal.role != "owner":
            send_json(
                h,
                200,
                events.member_page(gateway.db, gateway, principal, seq, query(h).get("epoch", "")),
            )
            return
        with gateway.db._lock:
            latest = events.latest_seq(gateway.db, principal.workspace_id)
            if query(h).get("epoch"):
                # A member-era ordinal is not a raw workspace cursor.
                response = {"events": [], "seq": latest, "reset": True}
            else:
                batch = events.after(
                    gateway.db, principal.workspace_id, min(seq, latest), latest or 1
                )
                response = {"events": batch, "seq": latest}
        send_json(h, 200, response)

    def connections(h):
        principal = require_principal(gateway, h)
        if principal is None:
            return
        if principal.role != "owner":
            send_json(h, 403, {"error": "Owner access required"})
            return
        from commonplace import bots_grant, decide_grant
        from commonplace.extensions import ToolError

        from .agents import connection_view, decide_view

        rows = gateway.db.connections(principal.workspace_id)
        view = decide_view(gateway, principal)
        try:
            wings = _connection_wings(gateway, principal, connection_wings_slot)
        except ToolError:
            # Connection management remains available while memory is offline.
            wings = []
        send_json(
            h,
            200,
            {
                "connections": [connection_view(row, **view) for row in rows],
                "decide_consent": decide_grant.SCOPE_TEXT,
                "bots_consent": {
                    "chat": bots_grant.CHAT_TEXT,
                    "confirm": bots_grant.CONFIRM_TEXT,
                    "learned": bots_grant.LEARNED_TEXT,
                },
                "wings": wings,
                "mcp_url": urls.AGENT_URL + "/mcp",
            },
        )

    from .. import capacity
    from ..topology import node_panel as topology_node
    from . import admin, agents, goals, invites, task_types

    admin.install(gateway)
    capacity.install(gateway)
    invites.install(gateway)
    agents.install(gateway)
    task_types.install(gateway)
    goals.install(gateway)
    topology_node.install(gateway)

    gateway.add_route("GET", "/app/login", login)
    gateway.add_route("POST", "/app/logout", logout)
    gateway.add_route("GET", "/api/me", me)
    gateway.add_route("GET", "/api/plan", plan)
    gateway.add_route("GET", "/api/events", feed)
    gateway.add_route("GET", "/api/connections", connections)
    from . import instance_admin

    instance_admin.install(gateway)
    gateway.add_prefix_route("GET", "/app/", _static)


def _people(db, workspace_id) -> dict:
    """``user:<id>`` -> the name the app shows: the user's name, else the
    part of the email before the @."""
    people = {}
    for member in db.members(workspace_id):
        name = db.user(member["id"])["name"] or member["email"].split("@", 1)[0]
        people[f"user:{member['id']}"] = name
    return people
