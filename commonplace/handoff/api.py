"""REST API for handoff, on the gateway. Owner: part T11.

All routes need ``Authorization: Bearer cmmn_…`` (``gateway.authenticate``)
and answer JSON; errors are ``{"error": "..."}`` with 400/401/404/409.
A thread or handoff the caller can't see is 404, never 403.

POST /api/handoff                         handoff:write   HandoffRequest → HandoffResult
GET  /api/handoffs?task=#142              threads:read    list
GET  /api/handoffs/<id>                   threads:read
POST /api/handoffs/<id>/successor         handoff:write   {agent_ref, url?, thread_id?}
POST /api/handoffs/<id>/relaunch          owner
GET  /api/threads/<id>                    threads:read    StoredThread
GET  /api/threads/<id>/messages?start=&end=   threads:read
POST /api/threads/<id>/recall             threads:read    {query, limit?, follow_chain?}
GET  /api/threads/<id>/chain              threads:read
GET  /api/settings/handoff?agent=&task=   any token: rows + effective setting
PUT  /api/settings/handoff                owner (members: task rows' enabled only)
DELETE /api/settings/handoff?scope=&ref=  owner
GET  /api/webhooks, POST /api/webhooks, DELETE /api/webhooks/<id>   owner
"""

from __future__ import annotations

import json
import math
import secrets
from dataclasses import fields, is_dataclass
from urllib.parse import parse_qs, unquote, urlsplit

from .db import HandoffDBError
from .models import (
    EVENTS,
    LAUNCHER_KINDS,
    SCOPE_HANDOFF,
    SCOPE_THREADS_READ,
    SETTING_SCOPES,
    THREADS_WING,
    ContextUsage,
    HandoffRequest,
    LauncherConfig,
    SettingRow,
    SuccessorRef,
    ThreadMeta,
)
from .recall import ThreadNotFound
from .settings import SettingsError
from .thread_store import ThreadConflict

MAX_HANDOFF_BYTES = 64 * 1024 * 1024
MAX_BODY_BYTES = 1024 * 1024


class _RequestError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


def _missing() -> None:
    raise _RequestError(404, "not found")


def _public(value):
    """Serialize typed responses without private connection or launcher fields."""
    if is_dataclass(value):
        value = {item.name: getattr(value, item.name) for item in fields(value)}
    if isinstance(value, dict):
        return {
            key: _public(item)
            for key, item in value.items()
            if key not in ("secret", "connection_id")
        }
    if isinstance(value, (list, tuple)):
        return [_public(item) for item in value]
    return value


def _object(value, allowed: set[str]) -> dict:
    if not isinstance(value, dict) or value.keys() - allowed:
        raise _RequestError(400, "invalid request body")
    return value


def _text(data: dict, key: str, default="", *, required: bool = False) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or (required and not value.strip()):
        raise _RequestError(400, f"invalid {key}")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _RequestError(400, f"invalid {key}") from None
    return value


def _integer(value, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise _RequestError(400, f"invalid {name}")
    return value


def _query(query: dict, key: str, default="") -> str:
    values = query.get(key, [default])
    if len(values) != 1:
        raise _RequestError(400, f"invalid {key}")
    return values[0]


def _range_value(query: dict, key: str, default):
    if key not in query:
        return default
    value = _query(query, key)
    if not value.isascii() or not value.isdecimal():
        raise _RequestError(400, f"invalid {key}")
    return int(value)


def _read_json(handler, limit: int = MAX_BODY_BYTES, *, allow_empty: bool = False) -> dict:
    lengths = [value.strip() for value in handler.headers.get_all("Content-Length", [])]
    if allow_empty and handler.headers.get("Transfer-Encoding") is None:
        if not lengths or (len(lengths) == 1 and lengths[0] and not lengths[0].lstrip("0")):
            handler._handoff_body_read = True
            return {}
    media = (handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if media != "application/json":
        raise _RequestError(415, "application/json required")
    if (
        handler.headers.get("Transfer-Encoding") is not None
        or len(lengths) != 1
        or not lengths[0].isascii()
        or not lengths[0].isdecimal()
    ):
        raise _RequestError(400, "invalid content length")
    # Avoid converting arbitrarily long integer headers; leading zeros are valid.
    digits = lengths[0].lstrip("0") or "0"
    if len(digits) > 20:
        raise _RequestError(413, "request too large")
    length = int(digits)
    if length > limit:
        raise _RequestError(413, "request too large")

    def invalid_constant(value):
        raise ValueError("invalid JSON constant")

    try:
        raw = handler.rfile.read(length)
        if len(raw) != length:
            raise ValueError("incomplete body")
        handler._handoff_body_read = True
        data = json.loads(raw.decode("utf-8"), parse_constant=invalid_constant)
    except (ValueError, RecursionError):
        raise _RequestError(400, "invalid JSON") from None
    if not isinstance(data, dict):
        raise _RequestError(400, "JSON object required")
    return data


def _handoff_request(data: dict) -> HandoffRequest:
    _object(data, {item.name for item in fields(HandoffRequest)})
    meta = _object(data.get("meta"), {item.name for item in fields(ThreadMeta)})
    values = {item.name: _text(meta, item.name) for item in fields(ThreadMeta)}
    values["thread_id"] = _text(meta, "thread_id", required=True)
    if "transcript" not in data:
        raise _RequestError(400, "transcript required")
    transcript = _text(data, "transcript")
    transcript_format = _text(data, "transcript_format", "auto")
    if transcript_format not in ("auto", "claude-code", "codex", "messages"):
        raise _RequestError(400, "invalid transcript_format")
    usage = data.get("usage")
    if usage is not None:
        usage = _object(usage, {item.name for item in fields(ContextUsage)})
        usage = ContextUsage(
            used_tokens=_integer(usage.get("used_tokens"), "used_tokens"),
            window_tokens=_integer(usage.get("window_tokens"), "window_tokens", 1),
            model=_text(usage, "model"),
        )
    return HandoffRequest(
        meta=ThreadMeta(**values),
        transcript=transcript,
        transcript_format=transcript_format,
        usage=usage,
        note=_text(data, "note"),
        idempotency_key=_text(data, "idempotency_key"),
    )


def _setting_row(data: dict, workspace_id: str) -> SettingRow:
    _object(data, {"scope", "ref", "enabled", "threshold", "context_window", "launcher"})
    scope = _text(data, "scope", required=True)
    if scope not in SETTING_SCOPES:
        raise _RequestError(400, "invalid scope")
    enabled = data.get("enabled")
    if enabled is not None and type(enabled) is not bool:
        raise _RequestError(400, "invalid enabled")
    threshold = data.get("threshold")
    if threshold is not None:
        try:
            valid_threshold = type(threshold) in (int, float) and math.isfinite(threshold)
        except OverflowError:
            valid_threshold = False
        if not valid_threshold:
            raise _RequestError(400, "invalid threshold")
    window = data.get("context_window")
    if window is not None:
        _integer(window, "context_window", 1)
    launcher = data.get("launcher")
    if launcher is not None:
        _object(launcher, {"kind", "argv", "url", "secret"})
        kind = _text(launcher, "kind", "client")
        argv = launcher.get("argv", [])
        if (
            kind not in LAUNCHER_KINDS
            or not isinstance(argv, list)
            or any(not isinstance(arg, str) for arg in argv)
        ):
            raise _RequestError(400, "invalid launcher")
        try:
            for arg in argv:
                arg.encode("utf-8")
        except UnicodeError:
            raise _RequestError(400, "invalid launcher") from None
        launcher = LauncherConfig(
            kind=kind,
            argv=tuple(argv),
            url=_text(launcher, "url"),
            secret=_text(launcher, "secret"),
        )
    return SettingRow(
        workspace_id=workspace_id,
        scope=scope,
        ref=_text(data, "ref"),
        enabled=enabled,
        threshold=threshold,
        context_window=window,
        launcher=launcher,
    )


class _API:
    def __init__(self, ctx):
        self.ctx = ctx

    def handle(self, handler) -> None:
        # An unread body must never become the next request on this connection.
        handler._handoff_body_read = False
        principal = self.ctx.gateway.authenticate(handler.headers.get("Authorization"))
        if principal is None:
            handler.close_connection = True
            handler._send_json(
                401,
                {"error": "unauthorized"},
                {"WWW-Authenticate": 'Bearer realm="commonplace"', "Connection": "close"},
            )
            return
        try:
            target = urlsplit(handler.path)
            query = parse_qs(target.query, keep_blank_values=True)
            status, result = self.dispatch(handler, principal, target.path, query)
        except _RequestError as error:
            status, result = error.status, {"error": error.message}
        except ThreadNotFound:
            status, result = 404, {"error": "not found"}
        except SettingsError as error:
            status = 403 if "only owners" in str(error).lower() else 400
            result = {"error": str(error)}
        except (HandoffDBError, ThreadConflict):
            status, result = 409, {"error": "handoff conflict"}
        except ValueError:
            status, result = 400, {"error": "invalid request"}
        except Exception:
            status, result = 500, {"error": "internal error"}
        unread_body = not handler._handoff_body_read and (
            handler.headers.get("Content-Length", "0") != "0"
            or handler.headers.get("Transfer-Encoding") is not None
        )
        if status >= 400 or unread_body:
            handler.close_connection = True
        handler._send_json(
            status, result, {"Connection": "close"} if status >= 400 or unread_body else None
        )

    @staticmethod
    def require(principal, scope: str = "", *, owner: bool = False) -> None:
        if (scope and not principal.policy.has_scope(scope)) or (
            owner and principal.role != "owner"
        ):
            _missing()

    @staticmethod
    def readable(principal) -> None:
        _API.require(principal, SCOPE_THREADS_READ)
        if not principal.policy.can_read_wing(THREADS_WING):
            _missing()

    def handoff(self, principal, handoff_id: str):
        record = self.ctx.handoff_db.get(handoff_id)
        if record is None or record.workspace_id != principal.workspace_id:
            _missing()
        return record

    @staticmethod
    def result(principal, result):
        if result.handoff is not None and result.handoff.workspace_id != principal.workspace_id:
            _missing()
        return _public(result)

    def thread(self, principal, thread_id: str):
        record = self.ctx.recall.thread(principal, thread_id)
        if record.workspace_id != principal.workspace_id or not principal.policy.can_read_wing(
            record.wing
        ):
            _missing()
        return record

    def dispatch(self, handler, principal, path: str, query: dict):
        method = handler.command
        workspace = principal.workspace_id
        if path == "/api/handoff":
            self.require(principal, SCOPE_HANDOFF)
            request = _handoff_request(_read_json(handler, MAX_HANDOFF_BYTES))
            if request.meta.parent_handoff_id:
                self.handoff(principal, request.meta.parent_handoff_id)
            return 200, self.result(principal, self.ctx.service.run(principal, request))
        if path == "/api/handoffs":
            self.readable(principal)
            records = self.ctx.handoff_db.list(workspace, task_ref=_query(query, "task"))
            return 200, _public([row for row in records if row.workspace_id == workspace])
        if path == "/api/settings/handoff":
            return self.settings(handler, principal, query)
        if path == "/api/webhooks":
            return self.webhooks(handler, principal)

        # Decode segments separately: an encoded slash is not an opaque id.
        parts = [unquote(part) for part in path.split("/")[3:]]
        if not parts or any(not part or "/" in part for part in parts):
            _missing()
        record_id = parts[0]
        if path.startswith("/api/handoffs/"):
            if method == "GET" and len(parts) == 1:
                self.readable(principal)
                return 200, _public(self.handoff(principal, record_id))
            if method == "POST" and len(parts) == 2 and parts[1] in ("successor", "relaunch"):
                if parts[1] == "relaunch":
                    self.require(principal, owner=True)
                    self.handoff(principal, record_id)
                    _object(_read_json(handler, allow_empty=True), set())
                    return 200, self.result(
                        principal, self.ctx.service.relaunch(principal, record_id)
                    )
                self.require(principal, SCOPE_HANDOFF)
                self.handoff(principal, record_id)
                data = _object(_read_json(handler), {"agent_ref", "url", "thread_id"})
                ref = SuccessorRef(
                    agent_ref=_text(data, "agent_ref", required=True),
                    url=_text(data, "url"),
                    thread_id=_text(data, "thread_id"),
                )
                record = self.ctx.service.register_successor(principal, record_id, ref)
                if record.workspace_id != workspace:
                    _missing()
                return 200, _public(record)
        if path.startswith("/api/threads/"):
            return self.threads(handler, principal, parts, query)
        if path.startswith("/api/webhooks/") and method == "DELETE" and len(parts) == 1:
            self.require(principal, owner=True)
            rows = self.ctx.handoff_db.webhooks(workspace)
            if not any(row["id"] == record_id for row in rows):
                _missing()
            if not self.ctx.handoff_db.remove_webhook(record_id, actor=principal.actor):
                _missing()
            return 200, {"deleted": True}
        _missing()

    def threads(self, handler, principal, parts: list[str], query: dict):
        self.require(principal, SCOPE_THREADS_READ)
        method = handler.command
        thread_id = parts[0]
        action = parts[1] if len(parts) == 2 else ""
        if len(parts) > 2 or (method, action) not in (
            ("GET", ""),
            ("GET", "messages"),
            ("GET", "chain"),
            ("POST", "recall"),
        ):
            _missing()
        record = self.thread(principal, thread_id)
        if not action:
            return 200, _public(record)
        if action == "messages":
            start = _range_value(query, "start", 0)
            end = _range_value(query, "end", None)
            if end is not None and end < start:
                raise _RequestError(400, "invalid end")
            return 200, _public(self.ctx.recall.get(principal, thread_id, start, end))
        if action == "chain":
            chain = []
            for row in self.ctx.recall.chain(principal, thread_id):
                if row.workspace_id != principal.workspace_id or not principal.policy.can_read_wing(
                    row.wing
                ):
                    break
                chain.append(row)
            return 200, _public(chain)
        data = _object(_read_json(handler), {"query", "limit", "follow_chain"})
        query_text = _text(data, "query", required=True)
        limit = _integer(data.get("limit", 8), "limit", 1)
        follow_chain = data.get("follow_chain", True)
        if type(follow_chain) is not bool:
            raise _RequestError(400, "invalid follow_chain")
        hits = self.ctx.recall.recall(
            principal, thread_id, query_text, limit=limit, follow_chain=follow_chain
        )
        return 200, _public(hits)

    def settings(self, handler, principal, query: dict):
        workspace = principal.workspace_id
        owner = principal.role == "owner"
        if handler.command == "GET":
            rows = self.ctx.handoff_db.list_settings(workspace)
            effective = self.ctx.settings.effective(
                workspace, agent=_query(query, "agent"), task_ref=_query(query, "task")
            )
            return 200, {
                "rows": _public([row for row in rows if row.workspace_id == workspace]),
                "effective": _public(effective),
            }
        if handler.command == "PUT":
            row = _setting_row(_read_json(handler), workspace)
            row = self.ctx.settings.set(row, actor=principal.actor, is_owner=owner)
            if row.workspace_id != workspace:
                _missing()
            return 200, _public(row)
        self.require(principal, owner=True)
        scope = _query(query, "scope")
        ref = _query(query, "ref")
        if scope not in SETTING_SCOPES:
            raise _RequestError(400, "invalid scope")
        if not self.ctx.settings.clear(
            workspace, scope, ref, actor=principal.actor, is_owner=owner
        ):
            _missing()
        return 200, {"deleted": True}

    def webhooks(self, handler, principal):
        self.require(principal, owner=True)
        workspace = principal.workspace_id
        if handler.command == "GET":
            return 200, _public(self.ctx.handoff_db.webhooks(workspace))
        data = _object(_read_json(handler), {"url", "secret", "events"})
        url = _text(data, "url", required=True)
        target = urlsplit(url)
        if target.scheme not in ("http", "https") or not target.hostname or target.port == 0:
            raise _RequestError(400, "invalid url")
        events = data.get("events", list(EVENTS))
        if (
            not isinstance(events, list)
            or not events
            or any(not isinstance(event, str) or event not in EVENTS for event in events)
        ):
            raise _RequestError(400, "invalid events")
        secret = (
            _text(data, "secret", required=True) if "secret" in data else secrets.token_urlsafe(32)
        )
        webhook_id = self.ctx.handoff_db.add_webhook(
            workspace, url, secret, tuple(events), actor=principal.actor
        )
        # This is the only response that includes the webhook secret.
        return 201, {"id": webhook_id, "secret": secret}


def register(ctx) -> None:
    api = _API(ctx)
    for method, path in (
        ("POST", "/api/handoff"),
        ("GET", "/api/handoffs"),
        ("GET", "/api/handoffs/*"),
        ("POST", "/api/handoffs/*"),
        ("GET", "/api/threads/*"),
        ("POST", "/api/threads/*"),
        ("GET", "/api/settings/handoff"),
        ("PUT", "/api/settings/handoff"),
        ("DELETE", "/api/settings/handoff"),
        ("GET", "/api/webhooks"),
        ("POST", "/api/webhooks"),
        ("DELETE", "/api/webhooks/*"),
    ):
        ctx.gateway.add_route(method, path, api.handle)
