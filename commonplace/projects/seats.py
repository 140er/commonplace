"""Project seat storage and lifecycle (implementation follows S0)."""

from commonplace.projects.types import MainView, Seat

_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_orchestrators (
    workspace_id TEXT NOT NULL,
    board TEXT NOT NULL,
    role TEXT NOT NULL,
    name TEXT NOT NULL,
    connection_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, board, role)
);
"""


def ensure_schema(db) -> None:
    with db._lock:
        db._conn.executescript(_SCHEMA)


def ensure_main(db, workspace_id: str, board: str) -> Seat:
    raise NotImplementedError


def main(gateway, principal, board: str) -> MainView:
    raise NotImplementedError


def on_event(gateway, event: dict) -> None:
    raise NotImplementedError


def install_listener(gateway) -> None:
    pass
