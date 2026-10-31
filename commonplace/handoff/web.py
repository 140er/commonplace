"""Server-rendered handoff pages, registered on the gateway by ``register``.

GET/POST /app/settings/memory; GET /app/cards/<task ref>;
GET /app/threads/<id>?page=1 (100 messages). People sign in through the web
app (``commonplace.sessions``, ``/app/login``); these pages read the same
session. Forms carry a CSRF token derived from the session.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import ssl
from dataclasses import asdict, replace
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from commonplace import sessions

from .models import SCOPE_THREADS_READ, LauncherConfig, SettingRow
from .recall import ThreadNotFound
from .settings import THRESHOLD_MAX, THRESHOLD_MIN, SettingsError, parse_launcher

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


def _safe_launcher(config):
    if config is None:
        return None
    try:
        return parse_launcher(asdict(config))
    except SettingsError:
        return LauncherConfig()


def _switch(enabled, *, disabled=False):
    # The On/Off word is CSS content on .switch-state, so it follows the
    # checkbox before the form is saved, without script.
    return (
        '<label class="switch"><input type="checkbox" role="switch" '
        'name="enabled" value="on" aria-label="Handoff"'
        + (" checked" if enabled else "")
        + (" disabled" if disabled else "")
        + '><span class="track" aria-hidden="true"></span>'
        '<span class="switch-state" aria-hidden="true"></span></label>'
    )


def _on_off(enabled):
    return "On" if enabled else "Off"


def _time(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def _stamp(value, *, date=False):
    moment = _time(value)
    if moment is None:
        return ""
    text = moment.strftime("%d %b %H:%M:%S UTC" if date else "%H:%M:%S")
    return f'<time datetime="{escape(value)}">{escape(text)}</time>'


def _tokens(count):
    if round(count / 1_000) >= 1_000:
        return f"{count / 1_000_000:.1f}".removesuffix(".0") + "M"
    if count >= 1_000:
        return f"{round(count / 1_000)}k"
    return str(count)


_METER_BLOCKS = 20


def _meter(handoff):
    """Context use when the handoff fired, as blocks; past the threshold in amber."""
    if handoff.window_tokens <= 0:
        return ""
    fraction = handoff.used_tokens / handoff.window_tokens
    # Any use of a block lights it, so crossing the threshold always shows amber.
    filled = math.ceil(min(fraction, 1.0) * _METER_BLOCKS)
    limit = math.floor(handoff.threshold * _METER_BLOCKS) if handoff.threshold else _METER_BLOCKS
    blocks = ""
    for index in range(_METER_BLOCKS):
        if index < filled:
            kind = "over" if index >= limit else "on"
        else:
            kind = "limit" if index == limit else ""
        blocks += f'<i class="{kind}"></i>' if kind else "<i></i>"
    reading = (
        f"{_tokens(handoff.used_tokens)} / {_tokens(handoff.window_tokens)} · {fraction * 100:.0f}%"
    )
    threshold = (
        f'<span class="muted">threshold {handoff.threshold * 100:g}%</span>'
        if handoff.threshold
        else ""
    )
    return (
        '<div class="context"><div class="context-row"><span class="label">Context</span>'
        f"<span>{escape(reading)}</span>{threshold}</div>"
        f'<div class="meter" role="meter" aria-label="Context" aria-valuemin="0" '
        f'aria-valuemax="{handoff.window_tokens}" '
        f'aria-valuenow="{min(handoff.used_tokens, handoff.window_tokens)}">'
        f"{blocks}</div></div>"
    )


# render_markdown writes provenance, Goal, then Status; read only from the top, so a
# section quoted inside a later Note can never stand in for them.
_PROVENANCE = re.compile(r"## Provenance\n\n(`{3,})\n(.*?)\n\1(?!`)(?:\n\n|\Z)", re.DOTALL)
_OPENING = re.compile(
    r"(?:## (Goal)\n\n(`{3,})\n(.*?)\n\2(?!`)(?:\n\n|\Z))?"
    r"(?:## (Status)\n\n(`{3,})\n(.*?)\n\5(?!`)(?:\n\n|\Z))?",
    re.DOTALL,
)


def _preview(markdown):
    """Goal and Status, the verbatim quotes the handoff document opens with."""
    markdown = markdown or ""
    items = ""
    provenance = _PROVENANCE.match(markdown)
    if provenance:
        items += (
            "<div><dt>Provenance</dt><dd><pre><span>"
            + escape(provenance[2]).replace("\r", "&#13;")
            + "</span></pre></dd></div>"
        )
        markdown = markdown[provenance.end() :]
    match = _OPENING.match(markdown)
    for name, text in ((match[1], match[3]), (match[4], match[6])):
        if name:
            items += (
                f"<div><dt>{name}</dt><dd><pre><span>"
                + escape(text).replace("\r", "&#13;")
                + "</span></pre></dd></div>"
            )
    return f'<dl class="preview">{items}</dl>' if items else ""


class _Pages:
    def __init__(self, ctx):
        self.ctx = ctx
        self.db = ctx.control_db
        # Signs CSRF tokens; a restart only means reloading the page.
        self._key = secrets.token_bytes(32)

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
        self._send(
            request,
            status,
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{escape(title)} · Commonplace</title>"
            '<link rel="stylesheet" href="/app/static/handoff.css"></head><body>'
            '<div class="app"><aside class="side"><a class="brand" href="/app/">Commonplace</a>'
            '<nav><a href="/app/settings/memory" aria-current="page">Memory</a></nav>'
            f"</aside><main><header><h1>{escape(title)}</h1></header>"
            f'<div class="content">{content}</div></main></div></body></html>',
            headers=headers,
        )

    def _session(self, request):
        """The web app's signed-in person, or ``None``."""
        value = sessions.cookie_value(request.headers.get("Cookie"))
        if not value:
            return None
        principal = sessions.session_principal(self.db, value)
        if principal is None:
            return None
        digest = hashlib.sha256(value.encode("utf-8")).digest()
        csrf = hmac.new(self._key, digest, hashlib.sha256).hexdigest()
        return {"csrf": csrf, "principal": principal}

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
                updated = replace(
                    existing, enabled=enabled == "on", launcher=_safe_launcher(existing.launcher)
                )
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
        )
        overrides = [
            row for row in rows if row.scope in ("agent", "task") and row.workspace_id == workspace
        ]
        content += (
            '<section class="overrides"><div class="tile section-title"><h2>Overrides</h2>'
            f'<span class="count">{len(overrides)}</span></div>'
        )
        for row in overrides:
            effective = self.ctx.settings.effective(
                workspace,
                agent=row.ref if row.scope == "agent" else "",
                task_ref=row.ref if row.scope == "task" else "",
            )
            identity = (
                f'<span class="scope">{escape(row.scope.title())}</span>'
                f"<strong>{escape(row.ref)}</strong>"
            )
            # What the workspace alone would do, so an override that changes it stands out.
            changed = " changed" if effective.enabled != setting.enabled else ""
            controls = (
                f'<span class="from{changed}">Workspace {_on_off(setting.enabled)}</span>'
                + _switch(effective.enabled, disabled=not owner)
            )
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
            stored = None
            if handoff.stored_thread_id:
                try:
                    stored = self.ctx.recall.thread(principal, handoff.stored_thread_id)
                except ThreadNotFound:
                    continue
            elif principal.role != "owner" and handoff.connection_id != principal.connection_id:
                continue
            content += self._handoff(handoff, stored)
        if not content:
            raise _Missing
        self._page(request, ref, content, session)

    @staticmethod
    def _handoff(handoff, stored):
        failed = handoff.state in ("failed", "launch_failed")
        state, tone = {
            "done": ("handed off", "ok"),
            "launched": ("handed off", "ok"),
            "failed": ("handoff failed", "warn"),
            "launch_failed": ("handoff failed", "warn"),
        }.get(handoff.state, (handoff.state, ""))
        html = (
            '<article class="tile handoff"><div class="handoff-head"><h2>'
            + escape(handoff.agent)
            + f'</h2><span class="state {tone}">'
            + escape(state)
            + "</span></div>"
        )
        facts = ""
        if handoff.branch:
            facts += '<span class="branch">' + escape(handoff.branch) + "</span>"
        if handoff.pr_url:
            facts += "<span>" + _safe_link(handoff.pr_url, handoff.pr_url) + "</span>"
        if facts:
            html += f'<p class="facts">{facts}</p>'
        html += _meter(handoff)

        # Old thread → handoff → new thread, each with its time.
        old = '<li class="step"><span class="label">Old thread</span><span class="what">'
        if stored is not None:
            old += (
                '<a href="/app/threads/'
                + escape(quote(handoff.stored_thread_id, safe=""))
                + '">'
                + escape(handoff.stored_thread_id)
                + "</a>"
                + f'<span class="muted">{stored.message_count} messages · in Memory</span>'
            )
        else:
            old += '<span class="muted">not saved</span>'
        old += "</span>" + _stamp(handoff.created_at, date=True) + "</li>"
        middle = (
            '<li class="step via"><span class="label">Handoff</span><span class="what">'
            f"<code>{escape(handoff.id)}</code></span></li>"
        )
        new = '<li class="step"><span class="label">New thread</span><span class="what">'
        if handoff.successor:
            new += _safe_link(handoff.successor.url, handoff.successor.agent_ref)
        if failed:
            new += '<span class="warn-text">compacted instead</span>'
        elif not handoff.successor:
            new += '<span class="muted">waiting</span>'
        new += "</span>"
        if handoff.successor and handoff.completed_at:
            started, finished = _time(handoff.created_at), _time(handoff.completed_at)
            same_day = started and finished and started.date() == finished.date()
            new += _stamp(handoff.completed_at, date=not same_day)
        html += f'<ol class="timeline">{old}{middle}{new}</li></ol>'
        return html + _preview(handoff.doc_markdown) + "</article>"

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
            if session is None:
                return self._send(request, 303, headers={"Location": "/app/login"})
            principal = session["principal"]
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
        ("GET", "/app/settings/memory"),
        ("POST", "/app/settings/memory"),
    ):
        ctx.gateway.add_route(method, path, pages.handle)
    for prefix in ("/app/cards/", "/app/threads/", "/app/static/"):
        ctx.gateway.add_prefix_route("GET", prefix, pages.handle)
