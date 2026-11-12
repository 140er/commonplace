"""Workspace invites: an owner lets one more person join as a member.

An invite is a secret shown once to the owner who made it. Only its SHA-256
is stored. It works once, expires (seven days unless the owner picks 1-30)
and can be revoked until it is used. An invite may name an email address;
then only the account with that address can use it.

Accepting adds the signed-in person to the workspace with the existing
``member`` role and nothing else: no connection, no token, no scope. What a
member may read and write is decided where it always was
(:func:`commonplace.sessions.person_policy`, :meth:`Policy.for_role`). An
invite never creates an account; the person signs in the usual way first.

The invite stops working when the owner who made it is no longer an owner of
that workspace, so a removed owner leaves no open doors behind.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from datetime import timedelta
from typing import Optional

from .control_db import ControlDB, ControlError, _iso, _new_id, _now

PREFIX = "inv_"
DEFAULT_DAYS = 7
MAX_DAYS = 30
MAX_PENDING = 50
LIST_LIMIT = 200
_SECRET = re.compile(r"inv_[A-Za-z0-9_-]{43}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_invites (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    secret_hash TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL DEFAULT '' COLLATE NOCASE,
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_by TEXT,
    accepted_at TEXT,
    accepted_by TEXT REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_workspace_invites_workspace
    ON workspace_invites(workspace_id, created_at);
"""


class InviteError(ControlError):
    """A refused invite. ``kind`` is ``invalid``, ``member`` or ``email``.

    ``invalid`` covers unknown, used, expired and revoked alike, so an answer
    never tells which of those a guessed or stale secret was.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _invalid() -> InviteError:
    return InviteError("invalid", "This invite is not valid any more")


def ensure_schema(db: ControlDB) -> None:
    with db._lock:
        db._conn.executescript(SCHEMA)


def looks_like_secret(value) -> bool:
    return isinstance(value, str) and _SECRET.fullmatch(value) is not None


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _state(row, now: str) -> str:
    if row["accepted_at"]:
        return "accepted"
    if row["revoked_at"]:
        return "revoked"
    return "expired" if row["expires_at"] <= now else "pending"


def _view(db: ControlDB, row) -> dict:
    """What an owner sees. Never the secret or its hash."""
    accepted = None
    if row["accepted_by"]:
        accepted = db.user(row["accepted_by"])["email"]
    return {
        "id": row["id"],
        "email": row["email"],
        "state": _state(row, _iso(_now())),
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "revoked_at": row["revoked_at"],
        "accepted_at": row["accepted_at"],
        "accepted_by": row["accepted_by"],
        "accepted_by_email": accepted,
    }


def _days(value) -> int:
    if value is None:
        return DEFAULT_DAYS
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_DAYS:
        raise ControlError(f"days must be a whole number from 1 to {MAX_DAYS}")
    return value


def _email(value) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise ControlError("email must be text")
    value = value.strip().lower()
    local, _, domain = value.partition("@")
    if len(value) > 254 or not local or "." not in domain or any(c.isspace() for c in value):
        raise ControlError("Enter a valid email address")
    return value


def create(db: ControlDB, workspace_id: str, actor_id: str, *, email=None, days=None) -> tuple:
    """A new invite. Returns ``(view, secret)``; the secret is not kept."""
    ensure_schema(db)
    email, days = _email(email), _days(days)
    secret = PREFIX + secrets.token_urlsafe(32)
    invite_id = _new_id("invite")
    now = _now()
    with db._lock, db._tx() as conn:
        db.require_owner(workspace_id, actor_id)
        pending = conn.execute(
            "SELECT count(*) AS n FROM workspace_invites WHERE workspace_id = ? "
            "AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > ?",
            (workspace_id, _iso(now)),
        ).fetchone()["n"]
        if pending >= MAX_PENDING:
            raise ControlError(
                f"This workspace already has {MAX_PENDING} open invites; revoke one first"
            )
        conn.execute(
            "INSERT INTO workspace_invites(id, workspace_id, secret_hash, email, created_by, "
            "created_at, expires_at) VALUES (?,?,?,?,?,?,?)",
            (
                invite_id,
                workspace_id,
                _hash(secret),
                email,
                actor_id,
                _iso(now),
                _iso(now + timedelta(days=days)),
            ),
        )
        row = conn.execute("SELECT * FROM workspace_invites WHERE id = ?", (invite_id,)).fetchone()
    db.audit(
        action="invite.create",
        decision="ok",
        workspace_id=workspace_id,
        user_id=actor_id,
        target_ids=[invite_id],
    )
    return _view(db, row), secret


def listing(db: ControlDB, workspace_id: str, actor_id: str) -> list:
    """This workspace's invites, newest first, for one of its owners."""
    ensure_schema(db)
    with db._lock:
        db.require_owner(workspace_id, actor_id)
        rows = db._conn.execute(
            "SELECT * FROM workspace_invites WHERE workspace_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (workspace_id, LIST_LIMIT),
        ).fetchall()
        return [_view(db, row) for row in rows]


def revoke(db: ControlDB, workspace_id: str, invite_id: str, actor_id: str) -> Optional[dict]:
    """Revoke an unused invite. ``None``: no such invite in this workspace."""
    ensure_schema(db)
    with db._lock, db._tx() as conn:
        db.require_owner(workspace_id, actor_id)
        row = conn.execute(
            "SELECT * FROM workspace_invites WHERE id = ? AND workspace_id = ?",
            (invite_id, workspace_id),
        ).fetchone()
        if row is None:
            return None
        if row["accepted_at"]:
            raise ControlError("This invite was already used")
        changed = False
        if not row["revoked_at"]:
            conn.execute(
                "UPDATE workspace_invites SET revoked_at = ?, revoked_by = ? "
                "WHERE id = ? AND accepted_at IS NULL AND revoked_at IS NULL",
                (_iso(_now()), actor_id, invite_id),
            )
            changed = True
            row = conn.execute(
                "SELECT * FROM workspace_invites WHERE id = ?", (invite_id,)
            ).fetchone()
    if changed:
        db.audit(
            action="invite.revoke",
            decision="ok",
            workspace_id=workspace_id,
            user_id=actor_id,
            target_ids=[invite_id],
        )
    return _view(db, row)


def _usable(conn, secret, user_id: str, now: str):
    """The invite row ``user_id`` may accept now, or raise :class:`InviteError`."""
    if not looks_like_secret(secret):
        raise _invalid()
    row = conn.execute(
        "SELECT * FROM workspace_invites WHERE secret_hash = ?", (_hash(secret),)
    ).fetchone()
    if row is None or _state(row, now) != "pending":
        raise _invalid()
    inviter = conn.execute(
        "SELECT m.role, u.disabled_at FROM memberships m JOIN users u ON u.id = m.user_id "
        "WHERE m.workspace_id = ? AND m.user_id = ?",
        (row["workspace_id"], row["created_by"]),
    ).fetchone()
    if inviter is None or inviter["role"] != "owner" or inviter["disabled_at"]:
        raise _invalid()
    user = conn.execute("SELECT email, disabled_at FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None or user["disabled_at"]:
        raise _invalid()
    member = conn.execute(
        "SELECT 1 FROM memberships WHERE workspace_id = ? AND user_id = ?",
        (row["workspace_id"], user_id),
    ).fetchone()
    if member is not None:
        raise InviteError("member", "You are already in this workspace")
    if row["email"] and row["email"].lower() != user["email"].lower():
        raise InviteError("email", "This invite is for a different account")
    return row


def peek(db: ControlDB, secret, user_id: str) -> dict:
    """What ``user_id`` would join, without using the invite."""
    ensure_schema(db)
    with db._lock:
        row = _usable(db._conn, secret, user_id, _iso(_now()))
        return {
            "workspace": row["workspace_id"],
            "workspace_name": db.workspace(row["workspace_id"])["name"],
        }


def accept(db: ControlDB, secret, user_id: str) -> str:
    """Use the invite once: ``user_id`` becomes a member. Returns the workspace id."""
    ensure_schema(db)
    now = _iso(_now())
    with db._lock, db._tx() as conn:
        row = _usable(conn, secret, user_id, now)
        used = conn.execute(
            "UPDATE workspace_invites SET accepted_at = ?, accepted_by = ? "
            "WHERE id = ? AND accepted_at IS NULL AND revoked_at IS NULL",
            (now, user_id, row["id"]),
        )
        if used.rowcount != 1:
            raise _invalid()
        try:
            conn.execute(
                "INSERT INTO memberships(workspace_id, user_id, role, created_at) "
                "VALUES (?,?,'member',?)",
                (row["workspace_id"], user_id, now),
            )
        except sqlite3.IntegrityError:
            raise InviteError("member", "You are already in this workspace") from None
    db.audit(
        action="invite.accept",
        decision="ok",
        workspace_id=row["workspace_id"],
        user_id=user_id,
        reason=f"{user_id}:member",
        target_ids=[row["id"]],
    )
    return row["workspace_id"]


def memberships(db: ControlDB, user_id: str, current: str = "") -> list:
    """The workspaces ``user_id`` is in, their own first; never any other workspace."""
    with db._lock:
        rows = db._conn.execute(
            "SELECT w.id, w.name, m.role, w.personal_of FROM memberships m "
            "JOIN workspaces w ON w.id = m.workspace_id WHERE m.user_id = ? "
            "ORDER BY w.personal_of IS ? DESC, w.name COLLATE NOCASE, w.id",
            (user_id, user_id),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "role": row["role"],
            "personal": row["personal_of"] == user_id,
            "current": row["id"] == current,
        }
        for row in rows
    ]
