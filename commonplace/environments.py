"""Account registry of environments a signed-in person can reach.

The phone (and any other device) keeps its own pairing secret. This table stores
only how to find an environment: a stable id, a name, an address and an optional
machine id. ``environments:person`` is requested by name on the native consent
page and recorded as its own unticked choice. It is not an OAuth policy scope,
and ``memory:read`` alone does not read or change these rows.
"""

from __future__ import annotations

import ipaddress
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from commonplace.auth import bearer_from_header
from commonplace.boards._http import HTTPError, path_parts, read_json, send_json

SCOPE = "environments:person"
FIELD = "grant_person_environments"
FLAG = "environments_person"
TEXT = (
    "Allow this app to list and update the environments registered to your account. "
    "It can save the name, address and machine id you send, and it never receives a "
    "pairing secret or token. Disconnect this app to stop access."
)
NATIVE_REDIRECT = "cmmn://auth/callback"
MAX_PER_USER = 100
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_LABEL = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9_])?")
_ALLOWED = frozenset({"name", "endpoint", "machineId"})
_SECRET_KEY = re.compile(
    r"secret|token|password|passwd|credential|pairing|bearer|authorization|api[_-]?key|private[_-]?key",
    re.IGNORECASE,
)

# Additive migration: existing control databases gain the table on startup.
# Pairing secrets and tokens are not columns.
SCHEMA = """
CREATE TABLE IF NOT EXISTS person_environments (
    user_id TEXT NOT NULL REFERENCES users(id),
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    machine_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_seen_at TEXT,
    PRIMARY KEY (user_id, id)
);
"""


def ensure(db) -> None:
    """Create the registry, or add columns an older copy of the table lacks."""
    with db._lock:
        db._conn.executescript(SCHEMA)
        columns = {row[1] for row in db._conn.execute("PRAGMA table_info(person_environments)")}
        if "last_seen_at" not in columns:
            db._conn.execute("ALTER TABLE person_environments ADD COLUMN last_seen_at TEXT")


def note_request(parameters: dict) -> None:
    """Drop ``environments:person`` out of the OAuth scope string and remember the ask.

    A client-supplied flag is ignored. The remaining scopes are validated as before,
    so this name never becomes a policy scope.
    """
    raw = parameters.get("scope")
    parameters.pop(FLAG, None)
    if not isinstance(raw, str) or SCOPE not in raw.split():
        return
    parameters["scope"] = " ".join(part for part in raw.split() if part != SCOPE)
    parameters[FLAG] = "1"


def eligible(db, principal) -> bool:
    return bool(
        principal
        and principal.person
        and principal.auth == "session"
        and not principal.connection_id
        and db.role(principal.workspace_id, principal.user_id) is not None
    )


def consent_wanted(parameters, db, principal) -> bool:
    return bool(
        parameters.get(FLAG) == "1"
        and parameters.get("redirect_uri") == NATIVE_REDIRECT
        and eligible(db, principal)
    )


def wants_grant(parameters, data, db, principal) -> bool:
    return consent_wanted(parameters, db, principal) and data.get(FIELD) == "1"


def record_grant(conn, client, principal, connection_id, at, redirect_uri) -> None:
    """Store the consented capability on this new connection. No secret is written."""
    from commonplace.oauth.http import OAuthError

    member = conn.execute(
        "SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?",
        (principal.workspace_id, principal.user_id),
    ).fetchone()
    if not (
        member
        and principal.person
        and principal.auth == "session"
        and not principal.connection_id
        and redirect_uri == NATIVE_REDIRECT
    ):
        raise OAuthError("invalid_scope")
    conn.execute(
        "INSERT INTO oauth_person_grants "
        "(connection_id, capability, user_id, workspace_id, client_id, created_at, cursor_secret) "
        "VALUES (?,?,?,?,?,?,NULL)",
        (
            connection_id,
            SCOPE,
            principal.user_id,
            principal.workspace_id,
            client["client_id"],
            at,
        ),
    )
    conn.execute(
        "INSERT INTO audit_log(at, workspace_id, user_id, connection_id, auth, action, "
        "decision, reason, target_ids) "
        "VALUES (strftime('%Y-%m-%dT%H:%M:%SZ','now'),?,?,?,'oauth','person_action.grant','ok',?,'[]')",
        (
            principal.workspace_id,
            principal.user_id,
            connection_id,
            json.dumps(
                {
                    "capability": SCOPE,
                    "client": client["client_name"],
                    "client_id": client["client_id"],
                }
            ),
        ),
    )


def install(gateway) -> None:
    ensure(gateway.db)

    def collection(h):
        try:
            _collection(gateway, h)
        except HTTPError as exc:
            send_json(h, exc.status, {"error": str(exc)})

    def item(h):
        try:
            _item(gateway, h)
        except HTTPError as exc:
            send_json(h, exc.status, {"error": str(exc)})

    gateway.add_route("GET", "/api/environments", collection)
    gateway.add_prefix_route("PUT", "/api/environments/", item)
    gateway.add_prefix_route("DELETE", "/api/environments/", item)


def _collection(gateway, h) -> None:
    user_id = _require_user(gateway, h)
    if urlsplit(h.path).query:
        raise HTTPError(400, "no query parameters")
    send_json(h, 200, {"environments": _list(gateway, user_id)})


def _item(gateway, h) -> None:
    user_id = _require_user(gateway, h)
    path = h.path.split("?", 1)[0]
    if not path.startswith("/api/environments/"):
        raise HTTPError(404, "not found")
    parts = path_parts(h, "/api/environments/")
    if len(parts) != 1 or not _ID.fullmatch(parts[0]):
        raise HTTPError(404, "not found")
    env_id = parts[0]
    if h.command == "DELETE":
        if not _remove(gateway, user_id, env_id):
            raise HTTPError(404, "not found")
        send_json(h, 200, {"ok": True})
        return
    media = (h.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if media != "application/json":
        raise HTTPError(415, "JSON required")
    send_json(h, 200, _upsert(gateway, user_id, env_id, read_json(h)))


def _require_user(gateway, h) -> str:
    from commonplace.oauth import store

    header = h.headers.get("Authorization", "")
    token = bearer_from_header(header)
    app = gateway.authenticate(header) if token else None
    if (
        not token
        or app is None
        or app.person
        or app.auth != "oauth"
        or app.runner is not None
        or app.parent_connection_id is not None
    ):
        raise HTTPError(403, "environments:person required")
    with gateway.db._lock:
        grant = gateway.db._conn.execute(
            "SELECT 1 FROM oauth_person_grants g "
            "JOIN oauth_tokens t ON t.connection_id = g.connection_id "
            "AND t.client_id = g.client_id "
            "WHERE g.connection_id = ? AND g.capability = ? AND g.user_id = ? "
            "AND g.workspace_id = ? AND g.revoked_at IS NULL "
            "AND t.token_hash = ? AND t.kind = 'access' "
            "AND t.revoked_at IS NULL AND t.expires_at > ?",
            (
                app.connection_id,
                SCOPE,
                app.user_id,
                app.workspace_id,
                store.digest(token),
                store.now(),
            ),
        ).fetchone()
    if grant is None:
        raise HTTPError(403, "environments:person required")
    return app.user_id


def _list(gateway, user_id) -> list:
    with gateway.db._lock:
        rows = gateway.db._conn.execute(
            "SELECT id, name, endpoint, machine_id, updated_at FROM person_environments "
            "WHERE user_id = ? ORDER BY updated_at DESC, id ASC",
            (user_id,),
        ).fetchall()
    return [_public(row) for row in rows]


def _public(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "endpoint": row["endpoint"],
        "machineId": row["machine_id"],
        "updatedAt": row["updated_at"],
    }


def _upsert(gateway, user_id, env_id, body) -> dict:
    name, endpoint, machine_id = _fields(body)
    now = _iso()
    with gateway.db._lock, gateway.db._tx() as conn:
        current = conn.execute(
            "SELECT name, endpoint, machine_id, updated_at FROM person_environments "
            "WHERE user_id = ? AND id = ?",
            (user_id, env_id),
        ).fetchone()
        if current is None:
            count = conn.execute(
                "SELECT count(*) FROM person_environments WHERE user_id = ?",
                (user_id,),
            ).fetchone()[0]
            if count >= MAX_PER_USER:
                raise HTTPError(409, "environment limit reached")
            conn.execute(
                "INSERT INTO person_environments "
                "(user_id, id, name, endpoint, machine_id, created_at, updated_at, last_seen_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (user_id, env_id, name, endpoint, machine_id, now, now, now),
            )
            updated = now
        else:
            changed = (
                current["name"] != name
                or current["endpoint"] != endpoint
                or current["machine_id"] != machine_id
            )
            updated = now if changed else current["updated_at"]
            conn.execute(
                "UPDATE person_environments SET name = ?, endpoint = ?, machine_id = ?, "
                "updated_at = ?, last_seen_at = ? WHERE user_id = ? AND id = ?",
                (name, endpoint, machine_id, updated, now, user_id, env_id),
            )
    return {
        "id": env_id,
        "name": name,
        "endpoint": endpoint,
        "machineId": machine_id,
        "updatedAt": updated,
    }


def _remove(gateway, user_id, env_id) -> bool:
    with gateway.db._lock, gateway.db._tx() as conn:
        cursor = conn.execute(
            "DELETE FROM person_environments WHERE user_id = ? AND id = ?",
            (user_id, env_id),
        )
        return cursor.rowcount == 1


def _fields(body: dict):
    if any(_SECRET_KEY.search(key) for key in body):
        raise HTTPError(400, "secrets are not accepted")
    if set(body) - _ALLOWED or not {"name", "endpoint"} <= set(body):
        raise HTTPError(400, "only name, endpoint and machineId are accepted")
    name = body["name"]
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 120
        or name != name.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
    ):
        raise HTTPError(400, "name is invalid")
    endpoint = body["endpoint"]
    if not _endpoint_ok(endpoint):
        raise HTTPError(400, "endpoint must be a URL or host without secrets")
    if "machineId" not in body or body["machineId"] is None:
        machine_id = None
    elif isinstance(body["machineId"], str) and _ID.fullmatch(body["machineId"]):
        machine_id = body["machineId"]
    else:
        raise HTTPError(400, "machineId is invalid")
    return name, endpoint, machine_id


def _endpoint_ok(value) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        return False
    if any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
        return False
    if "://" in value:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https"):
            return False
        if parts.username or parts.password or "@" in (parts.netloc or ""):
            return False
        if parts.query or parts.fragment or not parts.hostname:
            return False
        try:
            if parts.port is not None and not 0 < parts.port < 65536:
                return False
        except ValueError:
            return False
        return _host_ok(parts.hostname)
    return _hostport_ok(value)


def _host_ok(hostname: str) -> bool:
    if ":" in hostname:
        try:
            ipaddress.IPv6Address(hostname)
        except ValueError:
            return False
        return True
    try:
        ipaddress.IPv4Address(hostname)
        return True
    except ValueError:
        pass
    if len(hostname) > 253 or hostname.endswith("."):
        return False
    return all(_LABEL.fullmatch(label) for label in hostname.split("."))


def _hostport_ok(value: str) -> bool:
    if value.startswith("["):
        end = value.find("]")
        if end < 2:
            return False
        rest = value[end + 1 :]
        if rest and (not re.fullmatch(r":[0-9]{1,5}", rest) or not 1 <= int(rest[1:]) <= 65535):
            return False
        try:
            ipaddress.IPv6Address(value[1:end])
        except ValueError:
            return False
        return True
    if value.count(":") > 1:
        return False
    host, sep, port = value.partition(":")
    if sep and (not port.isdigit() or not 1 <= int(port) <= 65535):
        return False
    return _host_ok(host)


def _iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
