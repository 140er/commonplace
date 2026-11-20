"""Stored aggregate snapshots, scoped to a machine and its enrolling person."""

from __future__ import annotations

import json
import re
import time
from urllib.parse import parse_qs, urlsplit

from commonplace import sessions
from commonplace.http_util import send_json

from . import activity_schema as wire

PREFIX = "/api/compute/activity"
REPORT = "/api/runner/activity"
WINDOWS = {"1": 86400, "7": 7 * 86400, "30": 30 * 86400}
PAGE = 25
SCHEMA = """
CREATE TABLE IF NOT EXISTS compute_activity_consent (
    workspace_id TEXT NOT NULL, machine_id TEXT NOT NULL, owner_id TEXT NOT NULL,
    connection_id TEXT NOT NULL, grant_version INTEGER NOT NULL,
    enabled INTEGER NOT NULL, updated_at INTEGER NOT NULL,
    PRIMARY KEY(workspace_id,machine_id)
);
CREATE TABLE IF NOT EXISTS compute_activity_snapshots (
    workspace_id TEXT NOT NULL, machine_id TEXT NOT NULL, owner_id TEXT NOT NULL,
    connection_id TEXT NOT NULL, grant_version INTEGER NOT NULL,
    window_seconds INTEGER NOT NULL, collected_at INTEGER NOT NULL,
    received_at INTEGER NOT NULL, snapshot TEXT NOT NULL,
    PRIMARY KEY(workspace_id,machine_id,window_seconds)
);
"""


class Refused(Exception):
    def __init__(self, status=404, reason="not_found"):
        self.status, self.reason = status, reason
        super().__init__(reason)


def _owner(db, principal):
    if principal is None:
        raise Refused(401, "sign_in")
    if not principal.person or principal.auth != "session":
        raise Refused(403, "owner_required")
    live = sessions.person_principal(db, principal.user_id, principal.workspace_id)
    if live is None or live.role != "owner":
        raise Refused(403, "owner_required")
    return live


def _host(db, owner, machine_id):
    row = db._conn.execute(
        "SELECT id,workspace_id,enrolled_by,machine_connection_id,grant_version,enabled,revoked_at "
        "FROM runner_machines WHERE workspace_id=? AND id=? AND enrolled_by=?",
        (owner.workspace_id, machine_id, owner.user_id),
    ).fetchone()
    if row is None or row["revoked_at"] or not row["enabled"]:
        raise Refused()
    return row


def _consent(db, machine):
    row = db._conn.execute(
        "SELECT enabled,connection_id,grant_version,owner_id FROM compute_activity_consent "
        "WHERE workspace_id=? AND machine_id=?",
        (machine["workspace_id"], machine["id"]),
    ).fetchone()
    return bool(
        row
        and row["enabled"]
        and row["connection_id"] == machine["machine_connection_id"]
        and row["grant_version"] == machine["grant_version"]
        and row["owner_id"] == machine["enrolled_by"]
    )


def consent(db, principal, machine_id, enabled):
    if type(enabled) is not bool:
        raise Refused(400, "invalid_request")
    with db._tx():
        owner = _owner(db, principal)
        machine = _host(db, owner, machine_id)
        db._conn.execute(
            "INSERT INTO compute_activity_consent VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(workspace_id,machine_id) DO UPDATE SET owner_id=excluded.owner_id,"
            "connection_id=excluded.connection_id,grant_version=excluded.grant_version,"
            "enabled=excluded.enabled,updated_at=excluded.updated_at",
            (
                owner.workspace_id,
                machine_id,
                owner.user_id,
                machine["machine_connection_id"],
                machine["grant_version"],
                int(enabled),
                int(time.time()),
            ),
        )
        db.audit(
            action="compute.activity.consent",
            decision="ok",
            workspace_id=owner.workspace_id,
            user_id=owner.user_id,
            auth="session",
            target_ids=(machine_id,),
            reason="enabled" if enabled else "disabled",
        )
    return {"enabled": enabled}


def _report_machine(db, principal):
    if principal is None or principal.person or principal.runner != "machine":
        raise Refused()
    live = db.authenticate_connection(principal.connection_id)
    if live is None or live.runner != "machine" or live.workspace_id != principal.workspace_id:
        raise Refused()
    row = db._conn.execute(
        "SELECT id,workspace_id,enrolled_by,machine_connection_id,grant_version,enabled,revoked_at "
        "FROM runner_machines WHERE workspace_id=? AND machine_connection_id=?",
        (live.workspace_id, live.connection_id),
    ).fetchone()
    if row is None:
        raise Refused()
    owner = sessions.person_principal(db, row["enrolled_by"], live.workspace_id)
    machine = _host(db, _owner(db, owner), row["id"])
    if not _consent(db, machine):
        raise Refused(409, "activity_consent_required")
    return machine


def report(db, principal, snapshot):
    snapshot = wire.validate(snapshot)
    now = int(time.time())
    seconds = snapshot["window_end"] - snapshot["window_start"]
    if seconds not in WINDOWS.values() or snapshot["collected_at"] > now + 60:
        raise Refused(400, "invalid_activity_snapshot")
    with db._tx():
        machine = _report_machine(db, principal)
        previous = db._conn.execute(
            "SELECT collected_at,snapshot FROM compute_activity_snapshots "
            "WHERE workspace_id=? AND machine_id=? AND window_seconds=?",
            (machine["workspace_id"], machine["id"], seconds),
        ).fetchone()
        raw = json.dumps(snapshot, allow_nan=False, separators=(",", ":"))
        if previous and (
            previous["collected_at"] > snapshot["collected_at"]
            or (
                previous["collected_at"] == snapshot["collected_at"] and previous["snapshot"] != raw
            )
        ):
            raise Refused(409, "older_snapshot")
        db._conn.execute(
            "INSERT INTO compute_activity_snapshots VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(workspace_id,machine_id,window_seconds) DO UPDATE SET "
            "owner_id=excluded.owner_id,connection_id=excluded.connection_id,grant_version=excluded.grant_version,"
            "collected_at=excluded.collected_at,received_at=excluded.received_at,snapshot=excluded.snapshot",
            (
                machine["workspace_id"],
                machine["id"],
                machine["enrolled_by"],
                machine["machine_connection_id"],
                machine["grant_version"],
                seconds,
                snapshot["collected_at"],
                now,
                raw,
            ),
        )
    return {"ok": True, "received_at": now}


def read(
    db, principal, machine_id=None, *, window="1", offset=0, thread_id=None, live=False, model=""
):
    if (
        window not in WINDOWS
        or type(offset) is not int
        or not 0 <= offset < wire.MAX_THREADS
        or model
        and model not in wire.MODELS
    ):
        raise Refused(400, "invalid_request")
    with db._lock:
        owner = _owner(db, principal)
        if machine_id is None:
            rows = db._conn.execute(
                "SELECT id,workspace_id,enrolled_by,machine_connection_id,grant_version,enabled,revoked_at "
                "FROM runner_machines WHERE workspace_id=? AND enrolled_by=? AND revoked_at IS NULL "
                "AND enabled=1 ORDER BY id LIMIT 101",
                (owner.workspace_id, owner.user_id),
            ).fetchall()
            hosts = [{"id": row["id"], "enabled": _consent(db, row)} for row in rows[:100]]
            return {
                "hosts": hosts,
                "reason": "no_source" if not hosts else "host_limit" if len(rows) > 100 else None,
            }
        machine = _host(db, owner, machine_id)
        if not _consent(db, machine):
            return {"state": "not_enabled", "snapshot": None, "reason": "activity_consent_required"}
        row = db._conn.execute(
            "SELECT snapshot,received_at FROM compute_activity_snapshots WHERE workspace_id=? AND machine_id=? "
            "AND owner_id=? AND connection_id=? AND grant_version=? AND window_seconds=?",
            (
                owner.workspace_id,
                machine_id,
                owner.user_id,
                machine["machine_connection_id"],
                machine["grant_version"],
                WINDOWS[window],
            ),
        ).fetchone()
        if row is None:
            return {"state": "missing", "snapshot": None, "reason": "collector_not_reported"}
        snapshot = wire.loads(row["snapshot"].encode())
        if thread_id is not None:
            thread = next((r for r in snapshot["threads"] if r["id"] == thread_id), None)
            if thread is None:
                raise Refused()
            return {
                "thread": thread,
                "collected_at": snapshot["collected_at"],
                "window_start": snapshot["window_start"],
                "window_end": snapshot["window_end"],
            }
        thread_models = sorted({item["model"] for item in snapshot["threads"]})
        selected = sorted(snapshot["threads"], key=lambda item: item["seconds"], reverse=True)
        if live:
            selected = [
                item
                for item in selected
                if item["status"] in {"queued", "preparing", "starting", "running", "waiting"}
            ]
        if model:
            selected = [item for item in selected if item["model"] == model]
        total = len(selected)
        snapshot["threads"] = [
            {k: v for k, v in item.items() if k != "buckets"}
            for item in selected[offset : offset + PAGE]
        ]
        now = int(time.time())
        return {
            "state": "stale"
            if now
            - min(
                snapshot["collected_at"],
                snapshot["window_end"],
                snapshot["coverage"]["source_updated"] or 0,
            )
            > 180
            else "ready",
            "reason": None,
            "snapshot": snapshot,
            "received_at": row["received_at"],
            "as_of": now,
            "thread_models": thread_models,
            "page": {
                "offset": offset,
                "total": total,
                "next": offset + PAGE if offset + PAGE < total else None,
            },
        }


def _body(h, *, snapshot=False):
    try:
        length = int(h.headers.get("Content-Length", "0"))
        media = h.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if (
            h.headers.get("Transfer-Encoding")
            or media != "application/json"
            or not 0 < length <= (wire.MAX_BYTES if snapshot else 128)
        ):
            raise ValueError
        raw = h.rfile.read(length)
        if len(raw) != length:
            raise ValueError
        if snapshot:
            return wire.loads(raw)
        body = json.loads(raw, object_pairs_hook=wire._pairs)
        if type(body) is not dict or set(body) != {"enabled"} or type(body["enabled"]) is not bool:
            raise ValueError
        return body
    except (ValueError, UnicodeError, RecursionError):
        raise Refused(400, "invalid_activity_snapshot" if snapshot else "invalid_request") from None


def handle(gateway, h):
    try:
        url = urlsplit(h.path)
        principal = gateway.principal_for(h)
        if url.path == REPORT:
            with gateway.db._lock:
                _report_machine(gateway.db, principal)
            if h.command != "POST" or url.query:
                raise Refused(400, "invalid_request")
            return send_json(h, 200, report(gateway.db, principal, _body(h, snapshot=True)))
        owner = _owner(gateway.db, principal)
        tail = url.path.removeprefix(PREFIX)
        parts = tail[1:].split("/") if tail.startswith("/") else []
        if tail and (not parts or not re.fullmatch(r"mach_[0-9a-f]{16}", parts[0])):
            raise Refused()
        if h.command == "POST":
            from commonplace.gateway import _session_write_allowed

            if url.query or len(parts) != 2 or parts[1] != "consent":
                raise Refused()
            if not _session_write_allowed(gateway, h):
                raise Refused(403, "same_origin_required")
            with gateway.db._lock:
                _host(gateway.db, owner, parts[0])
            return send_json(h, 200, consent(gateway.db, owner, parts[0], _body(h)["enabled"]))
        if h.command != "GET" or len(parts) > 2:
            raise Refused()
        query = parse_qs(url.query, keep_blank_values=True, strict_parsing=True)
        if set(query) - {"window", "offset", "view", "model"} or any(
            len(values) != 1 for values in query.values()
        ):
            raise Refused(400, "invalid_request")
        window = query.get("window", ["1"])[0]
        offset = int(query.get("offset", ["0"])[0])
        view = query.get("view", ["all"])[0]
        if view not in {"all", "live"}:
            raise Refused(400, "invalid_request")
        thread_id = parts[1] if len(parts) == 2 else None
        if thread_id is not None and not re.fullmatch(r"thread_[0-9a-f]{24}", thread_id):
            raise Refused()
        return send_json(
            h,
            200,
            read(
                gateway.db,
                owner,
                parts[0] if parts else None,
                window=window,
                offset=offset,
                thread_id=thread_id,
                live=view == "live",
                model=query.get("model", [""])[0],
            ),
        )
    except Refused as exc:
        h.close_connection = True
        send_json(h, exc.status, {"error": exc.reason})
    except (ValueError, wire.InvalidSnapshot):
        h.close_connection = True
        send_json(h, 400, {"error": "invalid_activity_request"})
    except Exception:
        h.close_connection = True
        send_json(h, 503, {"error": "activity_unavailable"})


def install(gateway):
    with gateway.db._lock:
        gateway.db._conn.executescript(SCHEMA)
    for method in ("GET", "POST"):
        gateway.add_route(method, PREFIX, lambda h: handle(gateway, h))
        gateway.add_prefix_route(method, PREFIX + "/", lambda h: handle(gateway, h))
    gateway.add_route("POST", REPORT, lambda h: handle(gateway, h))
