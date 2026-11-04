"""Frozen S0 interface; implementation belongs to its owning unit."""

from commonplace.chat.types import Handoff

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_handoffs (              -- M1: one row per work-request message
    workspace_id TEXT NOT NULL,
    board TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,                    -- the poster; the card is filed as them
    coordinator TEXT,                         -- boards.coordinator actor at post time, or NULL
    state TEXT NOT NULL CHECK (state IN ('waiting','filing','claimed','filed','failed')),
    code TEXT NOT NULL DEFAULT '',            -- HANDOFF_CODES
    task INTEGER,                             -- card number when filed or claimed by a linked card
    due_at TEXT NOT NULL,                     -- created_at + ACK_WAIT_S, UTC
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, board, message_id)   -- dedup per message
);
CREATE INDEX IF NOT EXISTS chat_handoffs_due ON chat_handoffs(state, due_at);
"""


def ensure_schema(db) -> None:
    with db._lock:
        db._conn.executescript(_SCHEMA)


def is_work_request(text: str) -> bool:
    raise NotImplementedError


def open(db, workspace_id, board, message_id, user_id, coordinator: str | None, now: float) -> bool:
    raise NotImplementedError


def due(db, now: float, limit: int = 20) -> list[Handoff]:
    raise NotImplementedError


def settle(gateway, handoff: Handoff, now: float) -> str:
    raise NotImplementedError


def recover(db, gateway, now: float) -> None:
    return None
