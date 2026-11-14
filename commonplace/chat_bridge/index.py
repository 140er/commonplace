"""The hub-wide index of installs, in the control database: ids only, no secrets, no text.

It answers two questions the per-workspace file cannot: which workspace an intake URL belongs
to, and whether a platform team is already installed on this hub (one team, one install).
"""

import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bridge_install_index (
    install_id   TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    platform     TEXT NOT NULL,
    ext_team     TEXT NOT NULL,
    UNIQUE (platform, ext_team)
);
CREATE INDEX IF NOT EXISTS bridge_install_index_by_ws ON bridge_install_index(workspace_id);
"""


class TeamTaken(Exception):
    """The platform team already has an install on this hub (in any workspace)."""


def ensure_schema(db) -> None:
    with db._lock:
        db._conn.executescript(_SCHEMA)


def claim(db, install_id, workspace_id, platform, ext_team) -> None:
    with db._lock:
        try:
            db._conn.execute(
                "INSERT INTO bridge_install_index VALUES (?,?,?,?)",
                (install_id, workspace_id, platform, ext_team),
            )
        except sqlite3.IntegrityError:
            raise TeamTaken() from None


def release(db, install_id) -> None:
    with db._lock:
        db._conn.execute("DELETE FROM bridge_install_index WHERE install_id=?", (install_id,))


def workspace_of(db, install_id):
    with db._lock:
        row = db._conn.execute(
            "SELECT workspace_id FROM bridge_install_index WHERE install_id=?", (install_id,)
        ).fetchone()
    return row["workspace_id"] if row else None


def workspaces(db) -> list:
    """Workspace ids that hold at least one install."""
    with db._lock:
        return [
            r["workspace_id"]
            for r in db._conn.execute("SELECT DISTINCT workspace_id FROM bridge_install_index")
        ]
