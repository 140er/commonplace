"""Frozen S0 interface; implementation belongs to its owning unit."""

from commonplace.chat.types import Job, Reply

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_front_jobs (            -- model answers
    workspace_id TEXT NOT NULL,
    board TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending','running','done','superseded','skipped','failed')),
    code TEXT NOT NULL DEFAULT '',            -- JOB_CODES
    called INTEGER NOT NULL DEFAULT 0,        -- 1 when a model request was sent (daily caps)
    created_at TEXT NOT NULL,                 -- UTC ISO-8601
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, board, message_id)
);
CREATE INDEX IF NOT EXISTS chat_front_jobs_due ON chat_front_jobs(state, created_at);
"""


def ensure_schema(db) -> None:
    with db._lock:
        db._conn.executescript(_SCHEMA)


_REPLIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_replies (
    id INTEGER PRIMARY KEY,
    board TEXT NOT NULL,
    message_id INTEGER NOT NULL,             -- space_messages.id answered
    for_user TEXT NOT NULL,                  -- poster's user id; only they read it (OD5)
    kind TEXT NOT NULL CHECK (kind IN ('receipt','answer','handoff')),
    code TEXT NOT NULL,                      -- RECEIPT_CODES, 'ok' for answers, HANDOFF_CODES for handoff notices
    text TEXT NOT NULL,                      -- redacted before storage
    redacted TEXT NOT NULL DEFAULT '[]',     -- JSON list of redact KINDS
    task INTEGER,
    model TEXT,                              -- 'provider/model' for answers, NULL otherwise
    created_at TEXT NOT NULL,
    UNIQUE (board, message_id, kind)
);
CREATE INDEX IF NOT EXISTS chat_replies_by_user ON chat_replies(for_user, board, id);
CREATE TRIGGER IF NOT EXISTS chat_replies_no_update BEFORE UPDATE ON chat_replies
BEGIN SELECT RAISE(ABORT, 'chat replies are append-only'); END;
CREATE TRIGGER IF NOT EXISTS chat_replies_no_delete BEFORE DELETE ON chat_replies
BEGIN SELECT RAISE(ABORT, 'chat replies are append-only'); END;
"""


def open_replies(gateway, workspace_id: str) -> "ReplyStore":
    raise NotImplementedError


class ReplyStore:
    def add(
        self, *, board, message_id, for_user, kind, code, text, redacted, model, task=None
    ) -> Reply | None:
        raise NotImplementedError

    def page(self, *, board, for_user, message_ids=(), after_id=0, limit=100) -> list[Reply]:
        raise NotImplementedError

    def latest_id(self, *, board, for_user) -> int:
        raise NotImplementedError


def enqueue(db, workspace_id, board, message_id, user_id) -> bool:
    raise NotImplementedError


def claim(db) -> Job | None:
    raise NotImplementedError


def finish(db, job: Job, state: str, code: str, *, called: bool = False) -> None:
    raise NotImplementedError


def recover(db, now: float) -> None:
    return None


def counts_today(db, workspace_id, user_id: str | None = None) -> dict:
    raise NotImplementedError
