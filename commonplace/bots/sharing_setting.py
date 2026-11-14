"""The workspace switch for sharing bots (control side; one flag, no conversation text).

Off by default. Only a signed-in workspace owner changes it. Turning it off suspends every share
without changing it (the bot store answers ``Missing`` to members while it is off).
"""

from commonplace.bots.errors import stamp

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_sharing_settings (
    workspace_id TEXT PRIMARY KEY,
    sharing INTEGER NOT NULL DEFAULT 0 CHECK (sharing IN (0,1)),
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class NotOwner(PermissionError):
    """Only a signed-in workspace owner may change the switch."""


def ensure_schema(db) -> None:
    with db._lock:
        db._conn.executescript(_SCHEMA)


def enabled(db, workspace_id) -> bool:
    """The switch for ``workspace_id`` (false when never set)."""
    ensure_schema(db)
    with db._lock:
        row = db._conn.execute(
            "SELECT sharing FROM bot_sharing_settings WHERE workspace_id=?", (workspace_id,)
        ).fetchone()
    return row is not None and bool(row["sharing"])


def set_enabled(db, principal, on) -> bool:
    """Turn sharing on or off for the principal's workspace; one audit row per real change."""
    if type(on) is not bool:
        raise ValueError("sharing must be true or false")
    if not principal.person or principal.role != "owner":
        raise NotOwner()
    ensure_schema(db)
    with db._lock, db._tx() as conn:
        row = conn.execute(
            "SELECT sharing FROM bot_sharing_settings WHERE workspace_id=?",
            (principal.workspace_id,),
        ).fetchone()
        if (row is not None and bool(row["sharing"])) == on:
            return on
        conn.execute(
            "INSERT INTO bot_sharing_settings (workspace_id,sharing,updated_by,updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET sharing=excluded.sharing, "
            "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (principal.workspace_id, int(on), principal.user_id, stamp()),
        )
        db.audit(
            action="bots.sharing",
            decision="ok",
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            reason="on" if on else "off",
        )
    return on
