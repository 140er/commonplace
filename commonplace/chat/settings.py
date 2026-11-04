"""Frozen S0 interface; implementation belongs to its owning unit."""

from commonplace.chat.types import FrontSettings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_model_settings (
    workspace_id TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '*',          -- '*' = workspace; #192: board slug
    role TEXT NOT NULL DEFAULT 'front',       -- #187 accepts only 'front'; #192: 'main','area:<name>','worker'
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    provider TEXT NOT NULL,                   -- #187: 'none'|'openai_compatible'|'anthropic'
    model TEXT NOT NULL DEFAULT '',
    base_url TEXT,                            -- openai_compatible only (required there); NULL otherwise
    key_ref TEXT,                             -- 'env:COMMONPLACE_CHAT_KEY_<NAME>' only; operator-allowlisted workspaces only; never returned
    daily_limit INTEGER NOT NULL DEFAULT 200 CHECK (daily_limit BETWEEN 0 AND 2000),
    user_daily_limit INTEGER NOT NULL DEFAULT 50 CHECK (user_daily_limit BETWEEN 0 AND 2000),  -- m11; validated <= daily_limit
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, scope, role)
);
"""


def ensure_schema(db) -> None:
    with db._lock:
        db._conn.executescript(_SCHEMA)


def front(db, workspace_id: str) -> FrontSettings:
    raise NotImplementedError


def set_front(db, principal, body: dict) -> FrontSettings:
    raise NotImplementedError


def resolve_key(settings: FrontSettings) -> str | None:
    raise NotImplementedError


def key_allowed(settings: FrontSettings) -> bool:
    raise NotImplementedError
