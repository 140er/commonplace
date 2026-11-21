"""Owner-private machine consent, fenced by one durable compare-and-set revision."""

from __future__ import annotations

import sqlite3
from typing import TypedDict

from commonplace import sessions
from commonplace.control_db import ControlDB, ControlError, Principal
from commonplace.runner import registry

from .control import StoreError

MAX_REVISION = 2**63 - 2


class Consent(TypedDict):
    machine_id: str
    allowed: bool
    revision: int


_SCHEMA = """
CREATE TABLE IF NOT EXISTS compute_consents (
    machine_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    allowed INTEGER NOT NULL DEFAULT 0 CHECK(allowed IN (0,1)),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(typeof(revision)='integer' AND revision>=0)
)
"""


def ensure(db: ControlDB) -> None:
    """Add denied records and authority fences atomically, preserving existing revisions."""
    registry.ensure(db)
    machine_fields = (
        "workspace_id",
        "enrolled_by",
        "machine_connection_id",
        "worker_connection_id",
        "grant_version",
        "binding_epoch",
        "binding_state",
        "binding_grant_version",
        "enabled",
        "revoked_at",
    )
    connection_fields = (
        "workspace_id",
        "user_id",
        "auth",
        "revoked_at",
        "expires_at",
        "policy",
        "token_hash",
    )
    with db._tx() as conn:
        conn.execute(_SCHEMA)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS compute_consent_machine_insert
            AFTER INSERT ON runner_machines BEGIN
                INSERT INTO compute_consents(machine_id,workspace_id,user_id)
                VALUES(NEW.id,NEW.workspace_id,NEW.enrolled_by);
            END
        """)
        conn.execute(f"""
            CREATE TRIGGER IF NOT EXISTS compute_consent_machine_change
            AFTER UPDATE OF {",".join(machine_fields)} ON runner_machines
            WHEN {" OR ".join(f"NEW.{field} IS NOT OLD.{field}" for field in machine_fields)}
            BEGIN
                UPDATE compute_consents SET allowed=0,revision=revision+1,
                    workspace_id=NEW.workspace_id,user_id=NEW.enrolled_by
                WHERE machine_id=OLD.id;
            END
        """)
        for event, suffix, condition in (
            (
                "UPDATE OF " + ",".join(connection_fields),
                "change",
                "WHEN "
                + " OR ".join(f"NEW.{field} IS NOT OLD.{field}" for field in connection_fields),
            ),
            ("DELETE", "delete", ""),
        ):
            conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS compute_consent_connection_{suffix}
                BEFORE {event} ON connections {condition} BEGIN
                    UPDATE compute_consents SET allowed=0,revision=revision+1
                    WHERE machine_id IN (SELECT id FROM runner_machines
                        WHERE machine_connection_id=OLD.id OR worker_connection_id=OLD.id);
                END
            """)
        for event, suffix, condition in (
            (
                "UPDATE OF role,user_id,workspace_id",
                "change",
                "WHEN NEW.role IS NOT OLD.role OR NEW.user_id IS NOT OLD.user_id "
                "OR NEW.workspace_id IS NOT OLD.workspace_id",
            ),
            ("DELETE", "delete", ""),
        ):
            conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS compute_consent_membership_{suffix}
                BEFORE {event} ON memberships {condition} BEGIN
                    UPDATE compute_consents SET allowed=0,revision=revision+1
                    WHERE user_id=OLD.user_id AND workspace_id=OLD.workspace_id;
                END
            """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS compute_consent_account_change
            AFTER UPDATE OF disabled_at ON users WHEN NEW.disabled_at IS NOT OLD.disabled_at
            BEGIN
                UPDATE compute_consents SET allowed=0,revision=revision+1 WHERE user_id=OLD.id;
            END
        """)
        conn.execute("""
            INSERT INTO compute_consents(machine_id,workspace_id,user_id)
            SELECT m.id,m.workspace_id,m.enrolled_by FROM runner_machines m
            JOIN users u ON u.id=m.enrolled_by WHERE u.disabled_at IS NULL
            ON CONFLICT(machine_id) DO NOTHING
        """)


def _authority(
    db: ControlDB, principal: Principal, session_hash: str, machine_id: str
) -> sqlite3.Row:
    session = db._conn.execute("SELECT * FROM sessions WHERE id_hash=?", (session_hash,)).fetchone()
    if (
        not principal.person
        or principal.auth != "session"
        or session is None
        or session["revoked_at"]
        or session["expires_at"] <= sessions._now().strftime(sessions._FMT)
        or session["user_id"] != principal.user_id
        or session["workspace_id"] != principal.workspace_id
    ):
        raise StoreError("sign_in", 401)
    try:
        owner = sessions.person_principal(db, principal.user_id, principal.workspace_id)
    except ControlError:
        owner = None
    if owner is None or owner.role != "owner":
        raise StoreError("owner_required", 403)
    if not registry.MACHINE_ID.fullmatch(machine_id):
        raise StoreError("not_found", 404)
    machine = registry.get_machine(db, owner.workspace_id, machine_id)
    if machine is None or machine["enrolled_by"] != owner.user_id:
        raise StoreError("not_found", 404)
    if machine["revoked_at"] or not machine["enabled"]:
        raise StoreError("machine_unavailable", 409)
    for kind in ("machine", "worker"):
        connection = db.authenticate_connection(machine[f"{kind}_connection_id"])
        if (
            connection is None
            or connection.auth != "pat"
            or connection.workspace_id != owner.workspace_id
            or connection.user_id != owner.user_id
            or connection.runner != kind
            or db._conn.execute(
                "SELECT 1 FROM runner_connections WHERE connection_id=? AND machine_id=? AND kind=?",
                (connection.connection_id, machine["id"], kind),
            ).fetchone()
            is None
        ):
            raise StoreError("machine_unavailable", 409)
    row = db._conn.execute(
        "SELECT * FROM compute_consents WHERE machine_id=? AND workspace_id=? AND user_id=?",
        (machine["id"], owner.workspace_id, owner.user_id),
    ).fetchone()
    if row is None:
        raise StoreError("authority_unavailable", 503)
    return row


def _public(row: sqlite3.Row) -> Consent:
    return {
        "machine_id": row["machine_id"],
        "allowed": bool(row["allowed"]),
        "revision": row["revision"],
    }


def read(db: ControlDB, principal: Principal, machine_id: str, *, session_hash: str) -> Consent:
    with db._tx():
        return _public(_authority(db, principal, session_hash, machine_id))


def write(
    db: ControlDB,
    principal: Principal,
    machine_id: str,
    *,
    session_hash: str,
    allowed: bool,
    expected_revision: int,
) -> tuple[Consent, bool]:
    if (
        type(allowed) is not bool
        or type(expected_revision) is not int
        or not 0 <= expected_revision <= MAX_REVISION
    ):
        raise StoreError()
    with db._tx() as conn:
        row = _authority(db, principal, session_hash, machine_id)
        changed = (
            conn.execute(
                "UPDATE compute_consents SET allowed=?,revision=revision+1 "
                "WHERE machine_id=? AND workspace_id=? AND user_id=? AND revision=?",
                (
                    int(allowed),
                    row["machine_id"],
                    principal.workspace_id,
                    principal.user_id,
                    expected_revision,
                ),
            ).rowcount
            == 1
        )
        current = conn.execute(
            "SELECT * FROM compute_consents WHERE machine_id=?", (row["machine_id"],)
        ).fetchone()
        if changed:
            db.audit(
                action="compute.consent.allow" if allowed else "compute.consent.revoke",
                decision="ok",
                workspace_id=principal.workspace_id,
                user_id=principal.user_id,
                auth=principal.auth,
                target_ids=(row["machine_id"],),
            )
        return _public(current), changed
