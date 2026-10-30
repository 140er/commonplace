"""Persistent, signed webhook deliveries for Commonplace events.

The listener only queues work. One stoppable daemon per gateway performs HTTP.
Payloads contain an explicit metadata allowlist, never arbitrary event data.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import events
from .extensions import WORK_PROFILES, LocalTool, ToolError, register

logger = logging.getLogger(__name__)
BACKOFF = (10, 60, 300, 1800, 7200, 21600)
MAX_ATTEMPTS = 6
HTTP_TIMEOUT = 5
SCHEMA = """
CREATE TABLE IF NOT EXISTS webhooks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    connection_id TEXT NOT NULL DEFAULT '',
    owner_actor TEXT NOT NULL,
    url TEXT NOT NULL,
    secret_hash TEXT NOT NULL,
    secret TEXT NOT NULL,
    kinds TEXT NOT NULL,
    board TEXT NOT NULL DEFAULT '',
    waiting_on TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS webhooks_workspace ON webhooks(workspace_id, active);
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id TEXT PRIMARY KEY,
    webhook_id TEXT NOT NULL REFERENCES webhooks(id),
    event_seq INTEGER NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    http_status INTEGER,
    error TEXT NOT NULL DEFAULT '',
    next_at REAL NOT NULL,
    delivered_at TEXT,
    payload BLOB NOT NULL,
    UNIQUE(webhook_id, event_seq)
);
CREATE INDEX IF NOT EXISTS webhook_due ON webhook_deliveries(status, next_at);
CREATE TABLE IF NOT EXISTS webhook_attempts (
    delivery_id TEXT NOT NULL REFERENCES webhook_deliveries(id),
    attempt INTEGER NOT NULL,
    at TEXT NOT NULL,
    http_status INTEGER,
    error TEXT NOT NULL,
    PRIMARY KEY(delivery_id, attempt)
);
"""
# Titles are card metadata in the contract. Text/body/ask/note/reply are excluded.
_DATA_FIELDS = frozenset(
    {
        "board",
        "number",
        "title",
        "status",
        "from",
        "to",
        "assignee",
        "ask_to",
        "return_to",
        "actor",
        "id",
        "theme",
        "topic",
        "webhook_id",
        "delivery_id",
    }
)
_PRESETS = {
    "card_changed": ("card.*",),
    "review_needed": ("card.review_needed",),
    "waiting_on_me": ("card.handoff", "card.decided"),
}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_schema(db):
    with db._lock:
        db._conn.executescript(SCHEMA)


def validate_url(url):
    """Validate structurally; HTTP is limited to local and tailnet endpoints."""
    if not isinstance(url, str) or not url or len(url) > 4096:
        raise ToolError("no: invalid webhook URL")
    if any(ord(c) <= 32 or ord(c) == 127 for c in url) or "\\" in url:
        raise ToolError("no: invalid webhook URL")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
        if (
            parts.scheme not in ("http", "https")
            or not host
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
            or port == 0
            or "%" in host
        ):
            raise ValueError()
        host = host.lower().rstrip(".")
        if parts.scheme == "https":
            return url
        if host == "localhost" or (host.endswith(".ts.net") and host != "ts.net"):
            return url
        address = ipaddress.ip_address(host)
        if address.is_loopback or (
            isinstance(address, ipaddress.IPv4Address)
            and address in ipaddress.ip_network("100.64.0.0/10")
        ):
            return url
    except ValueError:
        pass
    raise ToolError("no: use HTTPS or a loopback or tailnet HTTP URL")


def _authorize(principal, action):
    if principal.policy.refusal("webhook_" + action):
        raise ToolError("no: webhook access refused")


def _visible(principal):
    # Tokens stay scoped to their connection even when their holder is an owner.
    if principal.person and principal.role == "owner":
        return "workspace_id = ?", (principal.workspace_id,)
    if principal.connection_id:
        return "workspace_id = ? AND connection_id = ?", (
            principal.workspace_id,
            principal.connection_id,
        )
    return "workspace_id = ? AND owner_actor = ? AND connection_id = ''", (
        principal.workspace_id,
        principal.actor,
    )


def _get(db, principal, webhook_id):
    ensure_schema(db)
    where, args = _visible(principal)
    with db._lock:
        row = db._conn.execute(
            f"SELECT * FROM webhooks WHERE {where} AND id = ? AND active = 1",
            (*args, webhook_id),
        ).fetchone()
    if row is None:
        raise ToolError("no: webhook not found")
    return row


def _public(row):
    return {
        key: (json.loads(row[key]) if key == "kinds" else row[key])
        for key in (
            "id",
            "workspace_id",
            "connection_id",
            "owner_actor",
            "url",
            "kinds",
            "board",
            "waiting_on",
            "active",
            "created_at",
        )
    }


def add(db, principal, url, *, kinds=None, board="", waiting_on=""):
    _authorize(principal, "add")
    validate_url(url)
    if isinstance(kinds, str):
        kinds = [kinds]
    if kinds is None:
        kinds = ["*"]
    if not isinstance(kinds, list) or not kinds or len(kinds) > 100:
        raise ToolError("no: events must be a nonempty list")
    expanded = []
    for kind in kinds:
        if not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9_.]*|\*|card\.\*", kind):
            raise ToolError("no: invalid event kind")
        expanded.extend(_PRESETS.get(kind, (kind,)))
    if not isinstance(board, str) or not isinstance(waiting_on, str):
        raise ToolError("no: board and waiting_on must be strings")
    if "waiting_on_me" in kinds:
        if waiting_on and waiting_on != principal.actor:
            raise ToolError("no: waiting_on_me targets your actor")
        waiting_on = principal.actor
    if waiting_on and not waiting_on.startswith(("conn:", "user:")):
        raise ToolError("no: waiting_on must be an actor reference")
    ensure_schema(db)
    secret = secrets.token_urlsafe(32)
    webhook_id = "wh_" + secrets.token_hex(12)
    with db._lock:
        db._conn.execute(
            "INSERT INTO webhooks(id, workspace_id, connection_id, owner_actor, url, "
            "secret_hash, secret, kinds, board, waiting_on, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                webhook_id,
                principal.workspace_id,
                principal.connection_id,
                principal.actor,
                url,
                hashlib.sha256(secret.encode()).hexdigest(),
                secret,
                json.dumps(expanded),
                board,
                waiting_on,
                _now(),
            ),
        )
    return {**_public(_get(db, principal, webhook_id)), "secret": secret}


def list_webhooks(db, principal):
    _authorize(principal, "list")
    ensure_schema(db)
    where, args = _visible(principal)
    with db._lock:
        rows = db._conn.execute(
            f"SELECT * FROM webhooks WHERE {where} AND active = 1 ORDER BY created_at, id", args
        ).fetchall()
    return [_public(row) for row in rows]


def remove(db, principal, webhook_id):
    _authorize(principal, "remove")
    with db._lock:
        _get(db, principal, webhook_id)
        with db._tx():
            db._conn.execute("UPDATE webhooks SET active = 0 WHERE id = ?", (webhook_id,))
            db._conn.execute(
                "UPDATE webhook_deliveries SET status = 'cancelled' "
                "WHERE webhook_id = ? AND status IN ('pending','retry')",
                (webhook_id,),
            )
    return {"removed": webhook_id}


def deliveries(db, principal, webhook_id):
    _authorize(principal, "list")
    with db._lock:
        _get(db, principal, webhook_id)
        rows = db._conn.execute(
            "SELECT id, webhook_id, event_seq, attempt, status, http_status, error, next_at, "
            "delivered_at FROM webhook_deliveries WHERE webhook_id = ? "
            "ORDER BY rowid DESC LIMIT 100",
            (webhook_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["attempts"] = [
                dict(a)
                for a in db._conn.execute(
                    "SELECT attempt, at, http_status, error FROM webhook_attempts "
                    "WHERE delivery_id = ? ORDER BY attempt",
                    (row["id"],),
                ).fetchall()
            ]
            result.append(item)
    return result


def _matches(row, event):
    if row["workspace_id"] != event["workspace_id"]:
        return False
    kinds = json.loads(row["kinds"])
    kind = event["kind"]
    if not (kind in kinds or "*" in kinds or ("card.*" in kinds and kind.startswith("card."))):
        return False
    data = event["data"]
    if row["board"] and row["board"] != data.get("board"):
        return False
    if row["waiting_on"]:
        return kind in ("card.review_needed", "card.handoff", "card.decided") and row[
            "waiting_on"
        ] in (data.get("ask_to"), data.get("return_to"), data.get("assignee"))
    return True


def _payload(db, event):
    data = {
        k: v
        for k, v in event["data"].items()
        if k in _DATA_FIELDS and (isinstance(v, (str, int, float, bool)) or v is None)
    }
    number = data.get("number")
    if number is None and re.fullmatch(r"#\d+", event["subject"]):
        number = event["subject"][1:]
    path = "/app/"
    if str(number).isdigit():
        path += "#/card/" + str(number)
    try:
        from commonplace import urls
    except ImportError:
        base = (
            os.environ.get("COMMONPLACE_APP_URL")
            or os.environ.get("COMMONPLACE_BASE_URL")
            or "http://127.0.0.1:8765"
        )
        url = base.rstrip("/") + path
    else:
        url = urls.share_link(db, event["workspace_id"], path)
    return json.dumps(
        {
            "id": f"evt_{event['seq']}",
            "seq": event["seq"],
            "kind": event["kind"],
            "at": event["at"],
            "workspace": event["workspace_id"],
            "subject": event["subject"],
            "actor": event["actor"],
            "data": data,
            "url": url,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _enqueue(db, row, event):
    delivery_id = "dlv_" + secrets.token_hex(12)
    with db._lock:
        db._conn.execute(
            "INSERT OR IGNORE INTO webhook_deliveries "
            "(id, webhook_id, event_seq, next_at, payload) VALUES (?,?,?,?,?)",
            (delivery_id, row["id"], event["seq"], time.time(), _payload(db, event)),
        )
    return delivery_id


def test_webhook(db, principal, webhook_id):
    _authorize(principal, "test")
    row = _get(db, principal, webhook_id)
    # A test is deliberately delivered only to the selected registration.
    event = events.emit(
        db,
        principal.workspace_id,
        "webhook.test",
        subject=webhook_id,
        actor=principal.actor,
        data={"webhook_id": webhook_id},
    )
    _enqueue(db, row, event)
    with db._lock:
        delivery = db._conn.execute(
            "SELECT id FROM webhook_deliveries WHERE webhook_id = ? AND event_seq = ?",
            (webhook_id, event["seq"]),
        ).fetchone()
    return {"id": delivery["id"], "status": "pending"}


def _post(url, body, secret, delivery_id, kind):
    validate_url(url)
    parts = urlsplit(url)
    timestamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = cls(parts.hostname, parts.port, timeout=HTTP_TIMEOUT)
    try:
        # http.client does not follow redirects or use environment HTTP proxies.
        conn.request(
            "POST",
            (parts.path or "/") + ("?" + parts.query if parts.query else ""),
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-Commonplace-Event": kind,
                "X-Commonplace-Delivery": delivery_id,
                "X-Commonplace-Signature": f"t={timestamp},v1={signature.hexdigest()}",
            },
        )
        response = conn.getresponse()
        return response.status
    finally:
        conn.close()


class Dispatcher:
    def __init__(self, db, *, schedule=BACKOFF, max_attempts=MAX_ATTEMPTS):
        if not schedule or any(delay < 0 for delay in schedule) or max_attempts < 1:
            raise ValueError("invalid webhook retry schedule")
        self.db = db
        self.schedule = tuple(schedule)
        self.max_attempts = max_attempts
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.thread = threading.Thread(target=self._run, name="commonplace-webhooks", daemon=True)

    def start(self):
        ensure_schema(self.db)
        events.add_listener(self.on_event)
        self.thread.start()
        return self

    def stop(self):
        events.remove_listener(self.on_event)
        self._stop.set()
        self._wake.set()
        self.thread.join(timeout=HTTP_TIMEOUT + 2)

    def on_event(self, event):
        with self.db._lock:
            # Listeners are process-global; accept only events from our control DB.
            stored = self.db._conn.execute(
                "SELECT * FROM events WHERE seq = ?", (event["seq"],)
            ).fetchone()
            if stored is None or events._row(stored) != event:
                return
            rows = self.db._conn.execute(
                "SELECT * FROM webhooks WHERE workspace_id = ? AND active = 1",
                (event["workspace_id"],),
            ).fetchall()
            for row in rows:
                if event["kind"] == "webhook.test":
                    continue
                if _matches(row, event):
                    _enqueue(self.db, row, event)
        self._wake.set()

    def _due(self):
        with self.db._lock:
            return self.db._conn.execute(
                "SELECT d.*, w.url, w.secret, w.workspace_id FROM webhook_deliveries d "
                "JOIN webhooks w ON w.id = d.webhook_id WHERE w.active = 1 "
                "AND d.status IN ('pending','retry') AND d.next_at <= ? "
                "ORDER BY d.next_at, d.rowid LIMIT 1",
                (time.time(),),
            ).fetchone()

    def _deliver(self, row):
        body = bytes(row["payload"])
        kind = json.loads(body)["kind"]
        status, error = None, ""
        attempt = row["attempt"] + 1
        try:
            status = _post(row["url"], body, row["secret"], row["id"], kind)
            if not 200 <= status < 300:
                error = f"HTTP {status}"
        except Exception:
            # Never retain response bodies, URL credentials, or arbitrary exception text.
            error = "delivery connection failed"
        delivered = status is not None and 200 <= status < 300
        dead = not delivered and attempt >= self.max_attempts
        state = "delivered" if delivered else "dead" if dead else "retry"
        delay = self.schedule[min(attempt - 1, len(self.schedule) - 1)]
        with self.db._lock, self.db._tx():
            self.db._conn.execute(
                "INSERT INTO webhook_attempts(delivery_id, attempt, at, http_status, error) "
                "VALUES (?,?,?,?,?)",
                (row["id"], attempt, _now(), status, error),
            )
            self.db._conn.execute(
                "UPDATE webhook_deliveries SET attempt = ?, status = ?, http_status = ?, "
                "error = ?, next_at = ?, delivered_at = ? WHERE id = ?",
                (
                    attempt,
                    state,
                    status,
                    error,
                    time.time() + delay,
                    _now() if delivered else None,
                    row["id"],
                ),
            )
        # Failure notifications are deliverable, but cannot spawn recursive failures.
        if dead and kind != "webhook.failed":
            events.emit(
                self.db,
                row["workspace_id"],
                "webhook.failed",
                subject=row["webhook_id"],
                actor="system",
                data={"webhook_id": row["webhook_id"], "delivery_id": row["id"]},
            )

    def _run(self):
        try:
            while not self._stop.is_set():
                self._wake.clear()
                row = self._due()
                if row is not None:
                    self._deliver(row)
                    continue
                self._wake.wait(0.1)
        except sqlite3.ProgrammingError:
            # A test or embedding application may close its ControlDB first.
            pass
        except Exception:
            logger.exception("webhook dispatcher stopped")
        finally:
            events.remove_listener(self.on_event)


def stop(gateway):
    dispatcher = getattr(gateway, "webhooks", None)
    if dispatcher:
        dispatcher.stop()


def _tool_add(ctx, args):
    result = add(
        ctx.db,
        ctx.principal,
        args.get("url"),
        kinds=args.get("events"),
        board=args.get("board", ""),
        waiting_on=args.get("waiting_on", ""),
    )
    return f"{result['id']} secret {result['secret']}"


def _tool_list(ctx, args):
    return list_webhooks(ctx.db, ctx.principal)


def _tool_remove(ctx, args):
    return remove(ctx.db, ctx.principal, args.get("id"))


def _tool_test(ctx, args):
    return test_webhook(ctx.db, ctx.principal, args.get("id"))


for _name, _handler, _properties, _required in (
    (
        "add",
        _tool_add,
        {
            "url": {"type": "string"},
            "events": {
                "oneOf": [{"type": "array", "items": {"type": "string"}}, {"type": "string"}]
            },
            "board": {"type": "string"},
            "waiting_on": {"type": "string"},
        },
        ["url"],
    ),
    ("list", _tool_list, {}, []),
    ("remove", _tool_remove, {"id": {"type": "string"}}, ["id"]),
    ("test", _tool_test, {"id": {"type": "string"}}, ["id"]),
):
    register(
        LocalTool(
            name="webhook_" + _name,
            description=_name.capitalize() + " webhooks",
            input_schema={
                "type": "object",
                "properties": _properties,
                "required": _required,
                "additionalProperties": False,
            },
            handler=_handler,
            scope="memory:read",
            kind="read" if _name == "list" else "write",
            profiles=WORK_PROFILES,
            trusted_only=True,
        )
    )


def _send_json(h, status, data):
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(raw)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(raw)


def _read_json(h):
    try:
        length = int(h.headers.get("Content-Length", "0"))
        if not 0 < length <= 1024 * 1024:
            raise ValueError()
        value = json.loads(h.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise ToolError("no: invalid JSON body") from None


def _rest(gateway, h):
    principal = gateway.principal_for(h)
    if principal is None:
        _send_json(h, 401, {"error": "unauthorized"})
        return
    parts = urlsplit(h.path).path.removeprefix("/api/webhooks").strip("/").split("/")
    if parts == [""]:
        parts = []
    method = h.command
    action = "list" if method == "GET" else "remove" if method == "DELETE" else "add"
    if len(parts) == 2 and parts[1] == "test":
        action = "test"
    try:
        _authorize(principal, action)
        if not parts and method == "GET":
            _send_json(h, 200, {"webhooks": list_webhooks(gateway.db, principal)})
        elif not parts and method == "POST":
            args = _read_json(h)
            result = add(
                gateway.db,
                principal,
                args.get("url"),
                kinds=args.get("events"),
                board=args.get("board", ""),
                waiting_on=args.get("waiting_on", ""),
            )
            _send_json(h, 201, {"id": result["id"], "secret": result["secret"]})
        elif len(parts) == 1 and method == "DELETE":
            remove(gateway.db, principal, parts[0])
            _send_json(h, 200, {"ok": True})
        elif len(parts) == 2 and parts[1] == "deliveries" and method == "GET":
            _send_json(h, 200, {"deliveries": deliveries(gateway.db, principal, parts[0])})
        elif len(parts) == 2 and parts[1] == "test" and method == "POST":
            delivery = test_webhook(gateway.db, principal, parts[0])
            _send_json(h, 202, {"ok": True, "delivery": delivery})
        else:
            _send_json(h, 404, {"error": "not found"})
    except ToolError as exc:
        message = str(exc)
        status = (
            404
            if message == "no: webhook not found"
            else (403 if message == "no: webhook access refused" else 400)
        )
        _send_json(h, status, {"error": message})


def install(gateway):
    if getattr(gateway, "webhooks", None) is not None:
        return
    gateway.webhooks = Dispatcher(gateway.db).start()
    for method in ("GET", "POST", "DELETE"):
        gateway.add_prefix_route(method, "/api/webhooks/", lambda h: _rest(gateway, h))


def cli_command(args):
    """CLI uses the local administrator's person policy, like other control commands."""
    from .cli import _actor, _db
    from .control_db import ControlError
    from .sessions import person_principal

    db = _db(args)
    try:
        principal = person_principal(db, _actor(db, args), db.workspace(args.workspace)["id"])
        if principal is None or principal.role != "owner":
            raise ControlError("workspace owner required")
        if args.action == "add":
            result = add(
                db,
                principal,
                args.url,
                kinds=args.events,
                board=args.board,
                waiting_on=args.waiting_on,
            )
        elif args.action == "list":
            result = list_webhooks(db, principal)
        else:
            result = remove(db, principal, args.id)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except ToolError as exc:
        raise ControlError(str(exc)) from None
    finally:
        db.close()
