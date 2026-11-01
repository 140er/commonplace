PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT OR IGNORE INTO meta VALUES ('seq', 0);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY,
    agent TEXT NOT NULL,
    key TEXT NOT NULL,
    text TEXT NOT NULL,
    summary TEXT,
    agent_text TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('permission','limit','instruction','style')),
    scope TEXT NOT NULL CHECK (scope IN ('global','board','repo')),
    scope_ref TEXT,
    status TEXT NOT NULL CHECK (status IN ('proposed','accepted','edited','retired')),
    source TEXT NOT NULL CHECK (source IN ('agent','owner')),
    pending INTEGER NOT NULL DEFAULT 0,
    retired_by TEXT CHECK (retired_by IN ('agent','owner')),
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    UNIQUE (agent, key)
);
-- migrate() in store.py adds rules.origin_agent, rules.agent_text_by and rule_versions.agent.
CREATE INDEX IF NOT EXISTS rules_by_seq ON rules(seq);
CREATE TABLE IF NOT EXISTS rule_versions (
    id INTEGER PRIMARY KEY,
    rule_id INTEGER NOT NULL REFERENCES rules(id),
    version INTEGER NOT NULL,
    text TEXT NOT NULL,
    summary TEXT,
    kind TEXT NOT NULL,
    scope TEXT NOT NULL,
    scope_ref TEXT,
    status TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('agent','owner','suggestion')),
    note TEXT,
    stamp_user TEXT NOT NULL,
    stamp_connection TEXT,
    stamp_trust TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rule_versions_by_rule ON rule_versions(rule_id, id);
CREATE TRIGGER IF NOT EXISTS rule_versions_no_update BEFORE UPDATE ON rule_versions
BEGIN SELECT RAISE(ABORT, 'rule_versions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS rule_versions_no_delete BEFORE DELETE ON rule_versions
BEGIN SELECT RAISE(ABORT, 'rule_versions is append-only'); END;
CREATE TABLE IF NOT EXISTS rule_suggestions (
    id INTEGER PRIMARY KEY,
    rule_id INTEGER NOT NULL REFERENCES rules(id),
    text TEXT NOT NULL,
    from_agent TEXT NOT NULL,
    base_version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open','accepted','dismissed')),
    stamp_user TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

INSERT OR IGNORE INTO meta VALUES ('store_id', (random() & 0x7fffffffffffffff) | 1);
CREATE TABLE IF NOT EXISTS agent_seq (agent TEXT PRIMARY KEY, token INTEGER NOT NULL);
INSERT OR IGNORE INTO agent_seq VALUES ('*', (random() & 0x7fffffffffffffff) | 1);
CREATE TABLE IF NOT EXISTS agent_served (
    agent TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('served','too_large')),
    rules_seq INTEGER,
    at TEXT NOT NULL
);
