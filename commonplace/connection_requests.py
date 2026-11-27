"""Member requests, owner decisions, and one-time collection without storing a secret."""

from uuid import uuid4

from commonplace import control_db, sessions, tokens, urls
from commonplace.control_db import ControlError
from commonplace.http_util import path_parts, read_json, require_principal, send_json
from commonplace.policy import Policy

ROUTE = "/api/connection-requests"
TOKEN_DAYS = 90
SCHEMA = """
CREATE TABLE IF NOT EXISTS connection_requests (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('pending','approved','declined','cancelled')),
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    reason TEXT NOT NULL DEFAULT '',
    connection_id TEXT REFERENCES connections(id) ON DELETE SET NULL,
    collected_at TEXT
);
CREATE INDEX IF NOT EXISTS connection_requests_workspace
ON connection_requests(workspace_id, status, created_at);
CREATE INDEX IF NOT EXISTS connection_requests_member
ON connection_requests(user_id, workspace_id, created_at);
CREATE INDEX IF NOT EXISTS connection_requests_connection
ON connection_requests(connection_id);
"""


class Refused(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _person(db, principal, *, owner=False):
    if not principal.person:
        raise Refused(403, "Sign in to manage connection requests.")
    person = sessions.person_principal(db, principal.user_id, principal.workspace_id)
    if person is None or (owner and person.role != "owner"):
        raise Refused(403, "Only the workspace owner can approve or decline connections.")
    return person


def _request(db, principal, ref, *, own=False):
    row = db._conn.execute(
        "SELECT * FROM connection_requests WHERE id=? AND workspace_id=?",
        (ref, principal.workspace_id),
    ).fetchone()
    if row is None or (own and row["user_id"] != principal.user_id):
        raise Refused(404, "Connection request not found.")
    return row


def view(db, row):
    result = dict(row)
    user = db.user(row["user_id"])
    result["requester_name"] = user["name"] or user["email"]
    result["kind"] = "connection_request"
    result["connection"] = None
    if row["connection_id"]:
        connection = db.connection(row["connection_id"], row["workspace_id"])
        result["connection"] = {
            key: connection[key]
            for key in ("id", "name", "display_name", "client", "expires_at", "revoked_at")
        }
    return result


def pending(db, principal):
    if not principal.person or principal.role != "owner":
        return []
    with db._lock:
        if (
            db._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='connection_requests'"
            ).fetchone()
            is None
        ):
            return []
        _person(db, principal, owner=True)
        rows = db._conn.execute(
            "SELECT r.* FROM connection_requests r JOIN users u ON u.id=r.user_id "
            "JOIN memberships m ON m.user_id=r.user_id AND m.workspace_id=r.workspace_id "
            "WHERE r.workspace_id=? AND r.status='pending' AND u.disabled_at IS NULL "
            "ORDER BY r.created_at,r.id",
            (principal.workspace_id,),
        ).fetchall()
        return [view(db, row) for row in rows]


def requested_connection(db, connection_id, workspace_id):
    with db._lock:
        return (
            db._conn.execute(
                "SELECT 1 FROM connection_requests WHERE connection_id=? AND workspace_id=?",
                (connection_id, workspace_id),
            ).fetchone()
            is not None
        )


def submit(db, principal, name, note):
    with db._tx() as conn:
        person = _person(db, principal)
        if person.role != "member":
            raise Refused(403, "Workspace owners can create a connection directly.")
        ref = "request_" + uuid4().hex
        conn.execute(
            "INSERT INTO connection_requests(id,workspace_id,user_id,name,note,status,created_at) "
            "VALUES (?,?,?,?,?,'pending',?)",
            (
                ref,
                person.workspace_id,
                person.user_id,
                name,
                note,
                control_db._iso(control_db._now()),
            ),
        )
        return view(db, _request(db, person, ref))


def decide(db, principal, ref, action, reason=""):
    owner_action = action in ("approve", "decline")
    target = {"approve": "approved", "decline": "declined", "cancel": "cancelled"}[action]
    with db._tx() as conn:
        person = _person(db, principal, owner=owner_action)
        row = _request(db, person, ref, own=not owner_action)
        if row["status"] == target:
            return view(db, row)
        if row["status"] != "pending":
            raise Refused(409, "This connection request has already closed.")
        connection_id = None
        if action == "approve":
            member = sessions.person_principal(db, row["user_id"], person.workspace_id)
            if member is None:
                raise Refused(409, "This person is no longer a member of the workspace.")
            connection_id, _ = db.create_token(
                workspace_ref=person.workspace_id,
                user_ref=member.user_id,
                actor=person.user_id,
                name="member-" + uuid4().hex,
                display_name=row["name"],
                client="generic",
                expires_days=TOKEN_DAYS,
                policy=Policy.build(
                    profile="slim",
                    scopes=[
                        s for s in ("memory:read", "memory:write") if member.policy.has_scope(s)
                    ],
                    read_wings=member.policy.read_wings,
                    write_wing="inbox",
                    trust="untrusted",
                ),
            )
            # No usable secret exists until the requester collects it. The owner's
            # approval response, database and logs never contain the generated secret.
            conn.execute(
                "UPDATE connections SET token_hash=NULL,token_hint='' WHERE id=?", (connection_id,)
            )
        conn.execute(
            "UPDATE connection_requests SET status=?,decided_at=?,decided_by=?,reason=?,"
            "connection_id=? WHERE id=?",
            (
                target,
                control_db._iso(control_db._now()),
                person.user_id,
                reason,
                connection_id,
                ref,
            ),
        )
        db.audit(
            action="connection_request." + action,
            decision="ok",
            workspace_id=person.workspace_id,
            user_id=person.user_id,
            connection_id=connection_id,
            auth=person.auth,
        )
        return view(db, _request(db, person, ref))


def collect(db, principal, ref):
    with db._tx() as conn:
        person = _person(db, principal)
        row = _request(db, person, ref, own=True)
        if row["status"] != "approved" or row["collected_at"] or not row["connection_id"]:
            raise Refused(409, "This connection has no token to collect.")
        connection = db.connection(row["connection_id"], person.workspace_id)
        if connection["revoked_at"] or connection["expires_at"] <= control_db._iso(
            control_db._now()
        ):
            raise Refused(409, "This connection has expired or been revoked.")
        token = tokens.new_token()
        conn.execute(
            "UPDATE connections SET token_hash=?,token_hint=? WHERE id=?",
            (tokens.token_hash(token), tokens.token_hint(token), connection["id"]),
        )
        conn.execute(
            "UPDATE connection_requests SET collected_at=? WHERE id=?",
            (control_db._iso(control_db._now()), ref),
        )
        db.audit(
            action="connection_request.collect",
            decision="ok",
            workspace_id=person.workspace_id,
            user_id=person.user_id,
            connection_id=connection["id"],
            auth=person.auth,
        )
        return {
            "token": token,
            "connection": view(db, _request(db, person, ref))["connection"],
            "mcp_url": urls.AGENT_URL + "/mcp",
        }


def route(gateway, h):
    principal = require_principal(gateway, h)
    if principal is None:
        return
    db = gateway.db
    try:
        _person(db, principal)
        parts = path_parts(h, ROUTE)
        if h.command == "GET" and not parts:
            with db._lock:
                rows = db._conn.execute(
                    "SELECT * FROM connection_requests WHERE workspace_id=? AND user_id=? "
                    "ORDER BY created_at DESC,id",
                    (principal.workspace_id, principal.user_id),
                ).fetchall()
                result = {"member_requests": True, "requests": [view(db, row) for row in rows]}
            send_json(h, 200, result)
            return
        if h.command != "POST" or (
            parts
            and (
                len(parts) != 2
                or parts[1] not in ("approve", "decline", "cancel", "collect", "revoke")
            )
        ):
            raise Refused(404, "Connection request not found.")
        try:
            data = read_json(h)
        except ValueError:
            return
        if not parts:
            name, note = data.get("name"), data.get("note", "")
            if (
                set(data) - {"name", "note"}
                or not isinstance(name, str)
                or not 1 <= len(name) <= 120
                or not name.strip()
                or not isinstance(note, str)
                or len(note) > 2000
            ):
                raise Refused(
                    400,
                    "Use a connection name or purpose (up to 120 characters) and an optional note (up to 2000).",
                )
            send_json(h, 201, {"request": submit(db, principal, name, note)})
            return
        ref, action = parts
        reason = data.get("reason", "")
        if (
            set(data) - ({"reason"} if action == "decline" else set())
            or not isinstance(reason, str)
            or len(reason) > 2000
        ):
            raise Refused(400, "Use an optional decline reason, up to 2000 characters.")
        if action == "collect":
            send_json(h, 200, collect(db, principal, ref))
        elif action == "revoke":
            with db._tx():
                person = _person(db, principal)
                row = _request(db, person, ref, own=True)
                if not row["connection_id"]:
                    raise Refused(409, "This request has no connection to revoke.")
                db.revoke(
                    row["connection_id"], actor=person.user_id, workspace_ref=person.workspace_id
                )
                result = view(db, row)
            send_json(h, 200, {"request": result})
        else:
            send_json(h, 200, {"request": decide(db, principal, ref, action, reason)})
    except Refused as exc:
        send_json(h, exc.status, {"error": str(exc)})
    except ControlError:
        send_json(
            h,
            409,
            {"error": "This connection request could not be completed. Refresh and try again."},
        )


def install(gateway):
    with gateway.db._lock:
        gateway.db._conn.executescript(SCHEMA)
    gateway.add_route("GET", ROUTE, lambda h: route(gateway, h))
    gateway.add_route("POST", ROUTE, lambda h: route(gateway, h))
    gateway.add_prefix_route("POST", ROUTE + "/", lambda h: route(gateway, h))
