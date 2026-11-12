"""Tables of the learning memory (``docs/design/bot-learning-memory.md``), added to ``bots.sqlite3``.

Plain SQL, applied by ``BotStore`` every time the file is opened. Nothing here is on by default:
a missing policy row is off and a missing state row is mode ``off``.
"""

LEARN_SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_learn_policy (
    key TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    shared_enabled INTEGER NOT NULL DEFAULT 0,
    set_by TEXT,
    set_at TEXT
);
CREATE TABLE IF NOT EXISTS bot_learn_state (
    bot_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'off' CHECK (mode IN ('off','ask','auto')),
    paused_until TEXT,
    floor_message_id INTEGER NOT NULL DEFAULT 0,
    last_run_message_id INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bot_learned (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('person','shared')),
    board TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('preference','instruction','fact','correction')),
    topic TEXT NOT NULL,
    text TEXT NOT NULL,
    text_by TEXT NOT NULL DEFAULT 'person' CHECK (text_by IN ('person','person_edit')),
    note TEXT,
    note_by TEXT CHECK (note_by IS NULL OR note_by = 'bot'),
    source_message_id INTEGER NOT NULL,
    source_at TEXT NOT NULL,
    quote_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('proposed','active')),
    pinned INTEGER NOT NULL DEFAULT 0,
    replaced_by TEXT,
    shared_from TEXT,
    seen_count INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT NOT NULL,
    use_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS bot_learned_dedupe
    ON bot_learned(user_id, scope, COALESCE(board,''), quote_hash) WHERE replaced_by IS NULL;
CREATE INDEX IF NOT EXISTS bot_learned_by_user ON bot_learned(user_id, state);
CREATE INDEX IF NOT EXISTS bot_learned_by_board ON bot_learned(board, scope);
CREATE INDEX IF NOT EXISTS bot_learned_by_bot ON bot_learned(bot_id);
CREATE INDEX IF NOT EXISTS bot_learned_by_origin ON bot_learned(shared_from);
CREATE VIRTUAL TABLE IF NOT EXISTS bot_learned_fts USING fts5(
    text, topic, tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS bot_learned_fts_insert AFTER INSERT ON bot_learned
BEGIN INSERT INTO bot_learned_fts(rowid, text, topic) VALUES (NEW.seq, NEW.text, NEW.topic); END;
CREATE TRIGGER IF NOT EXISTS bot_learned_fts_update AFTER UPDATE OF text, topic ON bot_learned
BEGIN
    DELETE FROM bot_learned_fts WHERE rowid = OLD.seq;
    INSERT INTO bot_learned_fts(rowid, text, topic) VALUES (NEW.seq, NEW.text, NEW.topic);
END;
CREATE TRIGGER IF NOT EXISTS bot_learned_fts_delete AFTER DELETE ON bot_learned
BEGIN DELETE FROM bot_learned_fts WHERE rowid = OLD.seq; END;
CREATE TABLE IF NOT EXISTS bot_learn_blocks (
    user_id TEXT NOT NULL,
    quote_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, quote_hash)
);
CREATE TABLE IF NOT EXISTS bot_learn_runs (
    bot_id TEXT NOT NULL,
    upto_message_id INTEGER NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    n_proposed INTEGER NOT NULL DEFAULT 0,
    n_kept INTEGER NOT NULL DEFAULT 0,
    n_dropped INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bot_id, upto_message_id)
);
CREATE TABLE IF NOT EXISTS bot_learn_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    bot_id TEXT,
    item_id TEXT,
    action TEXT NOT NULL CHECK (action IN
        ('learn','accept','reject','edit','share','forget','forget_all','pause','resume','setting')),
    at TEXT NOT NULL
);
"""

# Removing a memory note blanks its text; nothing else about a note may change afterwards.
MEMORY_TRIGGER = """
DROP TRIGGER IF EXISTS bot_memory_tombstone_only;
CREATE TRIGGER bot_memory_tombstone_only BEFORE UPDATE ON bot_memory
WHEN OLD.removed_at IS NOT NULL OR NEW.text IS NOT '' OR NEW.bot_id IS NOT OLD.bot_id
    OR NEW.created_at IS NOT OLD.created_at OR NEW.removed_at IS NULL
BEGIN SELECT RAISE(ABORT, 'bot memory is append-only'); END;
"""
