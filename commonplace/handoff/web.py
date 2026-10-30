"""Server-rendered Memory pages, registered on the gateway by ``register``.

GET/POST /app/login, POST /app/logout; GET/POST /app/settings/memory;
GET /app/cards/<task ref>; GET /app/threads/<id>?page=1 (100 messages).
Sessions contain a connection reference, never a bearer credential. Anonymous
login sessions expire after ten minutes; authenticated sessions after 12 hours.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import ssl
import time
from dataclasses import replace
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from commonplace.control_db import Principal
from commonplace.policy import Policy

from .models import SCOPE_THREADS_READ, SettingRow
from .recall import ThreadNotFound
from .settings import THRESHOLD_MAX, THRESHOLD_MIN, SettingsError

_LIFETIME = 12 * 60 * 60
_ANONYMOUS_LIFETIME = 10 * 60
_COOKIE = "cmmn_session"
_OPAQUE = re.compile(r"[0-9a-f]{64}\Z")
_STATIC = Path(__file__).with_name("static")


class _Missing(Exception):
    pass


class _BadForm(Exception):
    pass


def _digest(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _safe_link(url, label):
    """HTML escaping does not make a javascript: URL safe."""
    try:
        parts = urlsplit(url)
        valid = (
            parts.scheme in ("http", "https")
            and parts.hostname
            and not parts.username
            and not parts.password
            and not any(ord(char) <= 32 or ord(char) == 127 for char in url)
            and "\\" not in url
        )
    except ValueError:
        valid = False
    if valid:
        return f'<a href="{escape(url)}" rel="noreferrer">{escape(label)}</a>'
    return escape(label)


def _field(name, value):
    return f'<input type="hidden" name="{escape(name)}" value="{escape(str(value))}">'


def _switch(enabled, *, disabled=False):
    return (
        '<label class="switch"><input type="checkbox" role="switch" '
        'name="enabled" value="on" aria-label="Handoff"'
        + (" checked" if enabled else "")
        + (" disabled" if disabled else "")
        + '><span aria-hidden="true"></span></label>'
    )


class _Pages:
    def __init__(self, ctx):
        self.ctx = ctx
        self.db = ctx.control_db
        with self.db._lock:
            self.db._conn.execute(
                "CREATE TABLE IF NOT EXISTS web_sessions ("
                "id_hash TEXT PRIMARY KEY, connection_id TEXT REFERENCES connections(id), "
                "credential_hash TEXT, csrf TEXT NOT NULL, expires_at REAL NOT NULL)"
            )

    def _send(self, request, status, body=b"", *, headers=None, media="text/html; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        request.send_response(status)
        for name, value in {
            "Content-Type": media,
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            # same-origin, not no-referrer: with no-referrer browsers send `Origin: null`
            # on our own form posts, and the origin check refuses them.
            "Referrer-Policy": "same-origin",
            "Content-Security-Policy": "default-src 'none'; style-src 'self'; font-src 'self'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
            "Connection": "close",
            **(headers or {}),
        }.items():
            request.send_header(name, value)
        request.end_headers()
        request.wfile.write(body)
        request.close_connection = True

    def _page(self, request, title, content, session=None, *, status=200, headers=None):
        logout = ""
        if session and session["connection_id"]:
            logout = (
                '<form method="post" action="/app/logout">'
                + _field("csrf", session["csrf"])
                + '<button class="quiet-button">Log out</button></form>'
            )
        self._send(
            request,
            status,
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{escape(title)} · Commonplace</title>"
            '<link rel="stylesheet" href="/app/static/handoff.css"></head><body>'
            '<div class="app"><aside class="side"><div class="brand">Commonplace</div>'
            '<nav><a href="/app/settings/memory" aria-current="page">Memory</a></nav>'
            f"{logout}</aside><main><header><h1>{escape(title)}</h1></header>"
            f'<div class="content">{content}</div></main></div></body></html>',
            headers=headers,
        )

    def _cookie(self, request, value, lifetime):
        secure = isinstance(request.connection, ssl.SSLSocket) or (
            request.headers.get("X-Forwarded-Proto", "").lower() == "https"
        )
        return f"{_COOKIE}={value}; HttpOnly; SameSite=Strict; Path=/app; Max-Age={lifetime}" + (
            "; Secure" if secure else ""
        )

    def _session(self, request):
        # Reject duplicate and malformed cookies rather than choosing one identity.
        values = [
            item.partition("=")[2].strip()
            for header in request.headers.get_all("Cookie", [])
            for item in header.split(";")
            if item.partition("=")[0].strip() == _COOKIE
        ]
        if len(values) != 1 or not _OPAQUE.fullmatch(values[0]):
            return None
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT * FROM web_sessions WHERE id_hash = ?", (_digest(values[0]),)
            ).fetchone()
            if row and row["expires_at"] <= time.time():
                self.db._conn.execute(
                    "DELETE FROM web_sessions WHERE id_hash = ?", (row["id_hash"],)
                )
                return None
        return row

    def _new_session(self, old=None, principal=None):
        identifier, csrf = secrets.token_hex(32), secrets.token_hex(32)
        lifetime = _LIFETIME if principal else _ANONYMOUS_LIFETIME
        with self.db._lock, self.db._tx() as conn:
            credential_hash = None
            if principal:
                connection = conn.execute(
                    "SELECT token_hash FROM connections WHERE id = ? AND auth = 'pat'",
                    (principal.connection_id,),
                ).fetchone()
                if not connection or not connection["token_hash"]:
                    raise _Missing
                credential_hash = connection["token_hash"]
            conn.execute("DELETE FROM web_sessions WHERE expires_at <= ?", (time.time(),))
            if old:
                # Consuming the old session also prevents concurrent login replay.
                removed = conn.execute(
                    "DELETE FROM web_sessions WHERE id_hash = ?", (old["id_hash"],)
                ).rowcount
                if not removed:
                    raise _Missing
            conn.execute(
                "INSERT INTO web_sessions VALUES (?, ?, ?, ?, ?)",
                (
                    _digest(identifier),
                    principal.connection_id if principal else None,
                    credential_hash,
                    csrf,
                    time.time() + lifetime,
                ),
            )
        return identifier, csrf, lifetime

    def _principal(self, session):
        if not session or not session["connection_id"]:
            raise _Missing
        # Same live checks as ControlDB.authenticate_token, using the connection
        # reference instead of retaining or recovering a plaintext credential.
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT c.*, u.email AS user_email, u.disabled_at, m.role "
                "FROM connections c JOIN users u ON u.id = c.user_id "
                "JOIN memberships m ON m.workspace_id = c.workspace_id AND m.user_id = c.user_id "
                "WHERE c.id = ? AND c.auth = 'pat'",
                (session["connection_id"],),
            ).fetchone()
        if (
            not row
            or row["revoked_at"]
            or row["disabled_at"]
            or not row["token_hash"]
            or row["token_hash"] != session["credential_hash"]
        ):
            raise _Missing
        try:
            if row["expires_at"] and datetime.fromisoformat(
                row["expires_at"].replace("Z", "+00:00")
            ) <= datetime.now(timezone.utc):
                raise _Missing
            policy = Policy.from_json(row["policy"])
        except (ValueError, TypeError):
            raise _Missing from None
        return Principal(
            user_id=row["user_id"],
            user_email=row["user_email"],
            workspace_id=row["workspace_id"],
            connection_id=row["id"],
            connection=row["name"],
            client=row["client"],
            auth=row["auth"],
            role=row["role"],
            policy=policy,
        )

    def _form(self, request, session):
        if not session:
            raise _Missing
        origin = request.headers.get("Origin")
        if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
            raise _BadForm
        if origin is not None:
            scheme = "https" if isinstance(request.connection, ssl.SSLSocket) else "http"
            if request.headers.get("X-Forwarded-Proto", "").lower() == "https":
                scheme = "https"
            if origin != f"{scheme}://{request.headers.get('Host', '')}":
                raise _BadForm
        try:
            length = int(request.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192 or request.headers.get("Transfer-Encoding"):
                raise _BadForm
            if request.headers.get_content_type() != "application/x-www-form-urlencoded":
                raise _BadForm
            body = request.rfile.read(length).decode("utf-8")
            fields = parse_qs(body, keep_blank_values=True, max_num_fields=16, errors="strict")
            if any(len(value) != 1 for value in fields.values()):
                raise _BadForm
            data = {key: value[0] for key, value in fields.items()}
        except (ValueError, UnicodeError):
            raise _BadForm from None
        supplied = data.get("csrf", "")
        if not _OPAQUE.fullmatch(supplied) or not hmac.compare_digest(supplied, session["csrf"]):
            raise _BadForm
        return data

    def login(self, request, session):
        if request.command == "GET":
            if session and session["connection_id"]:
                try:
                    self._principal(session)
                except _Missing:
                    pass  # A revoked or stale identity can log in with a new token.
                else:
                    return self._send(request, 303, headers={"Location": "/app/settings/memory"})
            headers = None
            if not session or session["connection_id"]:
                identifier, csrf, lifetime = self._new_session(session)
                headers = {"Set-Cookie": self._cookie(request, identifier, lifetime)}
            else:
                csrf = session["csrf"]
            return self._page(
                request,
                "Log in",
                '<form class="tile login" method="post" action="/app/login">'
                + _field("csrf", csrf)
                + '<label for="token">Token</label><input id="token" name="token" '
                'type="password" required autocomplete="off" spellcheck="false">'
                "<button>Log in</button></form>",
                headers=headers,
            )
        data = self._form(request, session)
        if session["connection_id"]:
            raise _BadForm
        token = data.get("token", "")
        principal = (
            self.ctx.gateway.authenticate(f"Bearer {token}") if token.startswith("cmmn_") else None
        )
        if principal is None:
            raise _Missing
        identifier, _, lifetime = self._new_session(session, principal)
        self._send(
            request,
            303,
            headers={
                "Location": "/app/settings/memory",
                "Set-Cookie": self._cookie(request, identifier, lifetime),
            },
        )

    def settings(self, request, session, principal):
        workspace = principal.workspace_id
        owner = principal.role == "owner" and principal.policy.trust != "untrusted"
        if request.command == "POST":
            data = self._form(request, session)
            if not owner:
                raise _Missing
            action = data.get("action", "")
            scope, ref = data.get("scope", "workspace"), data.get("ref", "")
            if scope not in ("workspace", "agent", "task"):
                raise _BadForm
            if scope == "workspace":
                if ref or action != "save":
                    raise _BadForm
            else:
                if not ref or len(ref) > 200 or any(ord(char) < 32 for char in ref):
                    raise _BadForm
                if scope == "task":
                    ref = self._task_ref(ref)
            if action == "remove":
                self.ctx.settings.clear(workspace, scope, ref, actor=principal.actor, is_owner=True)
            elif action == "save":
                enabled = data.get("enabled", "off")
                if enabled not in ("on", "off"):
                    raise _BadForm
                threshold = None
                if scope == "workspace":
                    try:
                        threshold = float(data.get("threshold", "")) / 100
                    except ValueError:
                        raise _BadForm from None
                    if (
                        not math.isfinite(threshold)
                        or not THRESHOLD_MIN <= threshold <= THRESHOLD_MAX
                    ):
                        raise _BadForm
                existing = next(
                    (
                        row
                        for row in self.ctx.handoff_db.list_settings(workspace)
                        if row.workspace_id == workspace and row.scope == scope and row.ref == ref
                    ),
                    SettingRow(workspace, scope, ref),
                )
                updated = replace(existing, enabled=enabled == "on")
                if scope == "workspace":
                    updated = replace(updated, threshold=threshold)
                self.ctx.settings.set(
                    updated,
                    actor=principal.actor,
                    is_owner=True,
                )
            else:
                raise _BadForm
            return self._send(request, 303, headers={"Location": "/app/settings/memory"})
        setting = self.ctx.settings.effective(workspace)
        rows = self.ctx.handoff_db.list_settings(workspace)
        hidden = _field("csrf", session["csrf"]) + _field("action", "save")
        disabled = "" if owner else " disabled"
        content = (
            '<form class="tile setting" method="post" action="/app/settings/memory">'
            + hidden
            + _field("scope", "workspace")
            + '<div class="setting-row"><h2>Handoff</h2>'
            + _switch(setting.enabled, disabled=not owner)
            + '</div><div class="setting-row"><label for="threshold">Threshold</label>'
            f'<div class="percent"><input id="threshold" name="threshold" type="number" '
            f'min="{THRESHOLD_MIN * 100:g}" max="{THRESHOLD_MAX * 100:g}" step="any" '
            f'value="{setting.threshold * 100:g}" required{disabled}><span>%</span></div></div>'
            + ('<div class="actions"><button>Save</button></div>' if owner else "")
            + "</form>"
            '<section class="overrides"><div class="tile section-title"><h2>Overrides</h2></div>'
        )
        overrides = [
            row for row in rows if row.scope in ("agent", "task") and row.workspace_id == workspace
        ]
        for row in overrides:
            effective = self.ctx.settings.effective(
                workspace,
                agent=row.ref if row.scope == "agent" else "",
                task_ref=row.ref if row.scope == "task" else "",
            )
            identity = f"<span>{escape(row.scope.title())}</span><strong>{escape(row.ref)}</strong>"
            controls = _switch(effective.enabled, disabled=not owner)
            if owner:
                values = (
                    _field("csrf", session["csrf"])
                    + _field("scope", row.scope)
                    + _field("ref", row.ref)
                )
                content += (
                    '<form class="tile override" method="post" action="/app/settings/memory">'
                    + values
                    + identity
                    + controls
                    + '<button name="action" value="save">Save</button>'
                    '<button name="action" value="remove" formnovalidate>Remove</button></form>'
                )
            else:
                content += '<div class="tile override">' + identity + controls + "</div>"
        if not overrides:
            content += '<p class="tile empty">No overrides</p>'
        if owner:
            content += (
                '<form class="tile add-override" method="post" action="/app/settings/memory">'
                + hidden
                + '<label>Override<select name="scope"><option value="agent">Agent</option>'
                '<option value="task">Task</option></select></label><label>Reference'
                '<input name="ref" required maxlength="200"></label>'
                + _switch(False)
                + "<button>Add override</button></form>"
            )
        self._page(request, "Memory", content + "</section>", session)

    @staticmethod
    def _task_ref(value):
        if not re.fullmatch(r"#?[0-9]{1,18}", value):
            raise _BadForm
        return "#" + value.removeprefix("#")

    def card(self, request, session, principal):
        try:
            ref = self._task_ref(unquote(urlsplit(request.path).path.removeprefix("/app/cards/")))
        except _BadForm:
            raise _Missing from None
        if not principal.policy.has_scope(SCOPE_THREADS_READ) or not principal.policy.can_read_wing(
            "threads"
        ):
            raise _Missing
        handoffs = self.ctx.handoff_db.list(principal.workspace_id, task_ref=ref)
        content = ""
        for handoff in handoffs:
            if handoff.workspace_id != principal.workspace_id or handoff.task_ref != ref:
                continue
            if handoff.stored_thread_id:
                try:
                    self.ctx.recall.thread(principal, handoff.stored_thread_id)
                except ThreadNotFound:
                    continue
            elif principal.role != "owner" and handoff.connection_id != principal.connection_id:
                continue
            state = {
                "done": "handed off",
                "launched": "handed off",
                "failed": "handoff failed",
                "launch_failed": "handoff failed",
            }.get(handoff.state, handoff.state)
            content += (
                '<article class="tile handoff"><div class="setting-row"><h2>'
                + escape(handoff.agent)
                + '</h2><span class="state">'
                + escape(state)
                + "</span></div>"
            )
            if handoff.branch:
                content += '<p class="branch">' + escape(handoff.branch) + "</p>"
            if handoff.pr_url:
                content += "<p>" + _safe_link(handoff.pr_url, handoff.pr_url) + "</p>"
            if handoff.state in ("failed", "launch_failed"):
                content += "<p>compacted instead</p>"
            content += '<div class="thread-links">'
            if handoff.stored_thread_id:
                content += (
                    '<a href="/app/threads/'
                    + escape(quote(handoff.stored_thread_id, safe=""))
                    + '">Old thread</a>'
                )
            if handoff.successor:
                content += _safe_link(handoff.successor.url, handoff.successor.agent_ref)
            content += "</div></article>"
        if not content:
            raise _Missing
        self._page(request, ref, content, session)

    def thread(self, request, session, principal):
        parts = urlsplit(request.path)
        identifier = unquote(parts.path.removeprefix("/app/threads/"))
        if not identifier or "/" in identifier or len(identifier) > 200:
            raise _Missing
        try:
            query = parse_qs(parts.query, keep_blank_values=True, max_num_fields=4)
            values = query.get("page", ["1"])
            if len(values) != 1 or not re.fullmatch(r"[1-9][0-9]{0,8}", values[0]):
                raise _BadForm
            page = int(values[0])
        except ValueError:
            raise _BadForm from None
        thread = self.ctx.recall.thread(principal, identifier)
        start = (page - 1) * 100
        if start >= thread.message_count and page != 1:
            raise _Missing
        messages = self.ctx.recall.get(principal, identifier, start, start + 99)
        content = ""
        for message in messages:
            # The span prevents HTML's <pre> initial-newline removal. CR must
            # be a character reference to survive HTML newline normalization.
            text = escape(message.text).replace("\r", "&#13;")
            content += (
                '<article class="tile message"><div class="message-meta"><span>'
                + escape(message.role)
                + "</span><span>"
                + escape(message.kind)
                + "</span>"
                + ("<span>" + escape(message.name) + "</span>" if message.name else "")
                + "</div><pre><span>"
                + text
                + "</span></pre></article>"
            )
        if not messages:
            content = '<p class="tile empty">No messages</p>'
        base = "/app/threads/" + escape(quote(identifier, safe="")) + "?page="
        content += '<nav class="pagination" aria-label="Thread pages">'
        if page > 1:
            content += f'<a href="{base}{page - 1}">Previous</a>'
        if start + 100 < thread.message_count:
            content += f'<a href="{base}{page + 1}">Next</a>'
        self._page(request, identifier, content + "</nav>", session)

    def handle(self, request):
        try:
            path = urlsplit(request.path).path
            if path.startswith("/app/static/"):
                assets = {
                    "handoff.css": "text/css; charset=utf-8",
                    "fonts/geist-mono-latin.woff2": "font/woff2",
                    "fonts/OFL.txt": "text/plain; charset=utf-8",
                }
                name = path.removeprefix("/app/static/")
                if name not in assets:
                    raise _Missing
                return self._send(request, 200, (_STATIC / name).read_bytes(), media=assets[name])
            session = self._session(request)
            if path == "/app/login":
                return self.login(request, session)
            principal = self._principal(session)
            if path == "/app/logout":
                self._form(request, session)
                with self.db._lock:
                    self.db._conn.execute(
                        "DELETE FROM web_sessions WHERE id_hash = ?", (session["id_hash"],)
                    )
                return self._send(
                    request,
                    303,
                    headers={"Location": "/app/login", "Set-Cookie": self._cookie(request, "", 0)},
                )
            if path == "/app/settings/memory":
                return self.settings(request, session, principal)
            if path.startswith("/app/cards/"):
                return self.card(request, session, principal)
            if path.startswith("/app/threads/"):
                return self.thread(request, session, principal)
            raise _Missing
        except (_Missing, ThreadNotFound):
            self._page(request, "Not found", "", status=404)
        except (_BadForm, SettingsError):
            self._page(request, "Invalid form", "", status=400)


def register(ctx) -> None:
    """Register only web routes; never enable handoff or start a worker."""
    pages = _Pages(ctx)
    for method, path in (
        ("GET", "/app/login"),
        ("POST", "/app/login"),
        ("POST", "/app/logout"),
        ("GET", "/app/settings/memory"),
        ("POST", "/app/settings/memory"),
        ("GET", "/app/cards/*"),
        ("GET", "/app/threads/*"),
        ("GET", "/app/static/*"),
    ):
        ctx.gateway.add_route(method, path, pages.handle)
