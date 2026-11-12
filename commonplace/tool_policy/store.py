"""Tool policy rows and approval requests in the control database.

``tool_policy`` holds one row per (workspace, action) the owner narrowed: ``ask`` or ``off``.
An action without a row is ``allowed``, so an empty table changes nothing.

``tool_approvals`` holds one row per Ask-first request, bound to the calling connection, the
action and a digest of the arguments. A row is ``pending`` until the call it stands for runs
(``used``) or it is closed as ``denied``, ``void`` or ``expired``. A closed row is never
reopened, which is what makes an approval single-use.
"""

from __future__ import annotations

import hashlib
import json
import uuid
import weakref

ALLOWED, ASK, OFF = "allowed", "ask", "off"
MODES = (ALLOWED, ASK, OFF)
CLOSED = ("used", "denied", "void", "expired")

_ready: "weakref.WeakSet" = weakref.WeakSet()


def ensure_schema(db) -> None:
    if db in _ready:
        return
    with db._lock:
        db._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tool_policy (
                workspace_id TEXT NOT NULL, tool TEXT NOT NULL,
                mode TEXT NOT NULL CHECK(mode IN ('ask','off')),
                updated_by TEXT NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY (workspace_id, tool)
            );
            CREATE TABLE IF NOT EXISTS tool_approvals (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, connection_id TEXT NOT NULL,
                tool TEXT NOT NULL, digest TEXT NOT NULL,
                card INTEGER NOT NULL, card_created_at TEXT NOT NULL,
                after_event INTEGER NOT NULL,
                state TEXT NOT NULL
                    CHECK(state IN ('pending','used','denied','void','expired')),
                created_at REAL NOT NULL, expires_at REAL NOT NULL, closed_at REAL
            );
            CREATE INDEX IF NOT EXISTS tool_approvals_binding
                ON tool_approvals(workspace_id, connection_id, tool, digest, state);
            """
        )
    _ready.add(db)


def digest(tool: str, arguments: dict) -> str:
    """One stable digest of an action and its arguments, whatever the key order."""
    canonical = json.dumps(
        {"tool": tool, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def mode(db, workspace_id: str, tool: str) -> str:
    ensure_schema(db)
    with db._lock:
        row = db._conn.execute(
            "SELECT mode FROM tool_policy WHERE workspace_id=? AND tool=?", (workspace_id, tool)
        ).fetchone()
    if row is None:
        return ALLOWED
    # A stored value this version does not know can only narrow.
    return row["mode"] if row["mode"] in (ASK, OFF) else OFF


def modes(db, workspace_id: str) -> dict:
    ensure_schema(db)
    with db._lock:
        rows = db._conn.execute(
            "SELECT tool, mode FROM tool_policy WHERE workspace_id=?", (workspace_id,)
        ).fetchall()
    return {row["tool"]: row["mode"] if row["mode"] in (ASK, OFF) else OFF for row in rows}


def set_modes(db, workspace_id: str, changes: dict, *, actor: str, now: float) -> None:
    """Store every change or none of them."""
    ensure_schema(db)
    with db._lock, db._tx() as conn:
        for tool, value in changes.items():
            if value == ALLOWED:
                conn.execute(
                    "DELETE FROM tool_policy WHERE workspace_id=? AND tool=?",
                    (workspace_id, tool),
                )
            else:
                conn.execute(
                    "INSERT INTO tool_policy(workspace_id,tool,mode,updated_by,updated_at) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(workspace_id,tool) DO UPDATE SET "
                    "mode=excluded.mode, updated_by=excluded.updated_by, "
                    "updated_at=excluded.updated_at",
                    (workspace_id, tool, value, actor, now),
                )


def pending(db, workspace_id: str, connection_id: str, tool: str, digest_: str):
    """The open request for exactly this connection, action and arguments, if any."""
    with db._lock:
        return db._conn.execute(
            "SELECT * FROM tool_approvals WHERE workspace_id=? AND connection_id=? AND tool=? "
            "AND digest=? AND state='pending' ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (workspace_id, connection_id, tool, digest_),
        ).fetchone()


def pending_count(db, workspace_id: str, connection_id: str, now: float) -> int:
    with db._lock:
        return db._conn.execute(
            "SELECT COUNT(*) FROM tool_approvals WHERE workspace_id=? AND connection_id=? "
            "AND state='pending' AND expires_at>?",
            (workspace_id, connection_id, now),
        ).fetchone()[0]


def add(
    db,
    *,
    workspace_id: str,
    connection_id: str,
    tool: str,
    digest_: str,
    card: int,
    card_created_at: str,
    after_event: int,
    now: float,
    expires_at: float,
) -> str:
    identity = uuid.uuid4().hex
    with db._lock:
        db._conn.execute(
            "INSERT INTO tool_approvals(id,workspace_id,connection_id,tool,digest,card,"
            "card_created_at,after_event,state,created_at,expires_at) "
            "VALUES (?,?,?,?,?,?,?,?,'pending',?,?)",
            (
                identity,
                workspace_id,
                connection_id,
                tool,
                digest_,
                card,
                card_created_at,
                after_event,
                now,
                expires_at,
            ),
        )
    return identity


def close(db, identity: str, state: str, now: float) -> bool:
    """Close a pending request. True for the one caller that closed it."""
    if state not in CLOSED:
        raise ValueError(state)
    with db._lock:
        cursor = db._conn.execute(
            "UPDATE tool_approvals SET state=?, closed_at=? WHERE id=? AND state='pending'",
            (state, now, identity),
        )
        return cursor.rowcount == 1
