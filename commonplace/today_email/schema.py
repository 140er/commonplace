"""Storage for the opt-in email about new Today items."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS today_email_prefs (
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, user_id)
);
CREATE TABLE IF NOT EXISTS today_email_items (
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    number INTEGER NOT NULL,
    noted_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, user_id, number)
);
CREATE TABLE IF NOT EXISTS today_email_sends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    sent_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_today_email_sends
    ON today_email_sends(workspace_id, user_id, sent_at);
"""


def ensure(db):
    with db._lock:
        db._conn.executescript(SCHEMA)
