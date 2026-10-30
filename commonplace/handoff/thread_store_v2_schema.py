"""Private v2 file identity and schema. Production continues to open v1."""

APPLICATION_ID = 0x43505432  # CPT2
USER_VERSION = 2
FILENAME = "threads.v2.sqlite3"

SCHEMA = """
CREATE TABLE threads (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    source_thread_id TEXT NOT NULL,
    parent_stored_id TEXT NOT NULL DEFAULT '',
    message_count INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    wing TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    UNIQUE(workspace_id, source_thread_id)
);
CREATE TABLE dict (
    id INTEGER PRIMARY KEY,
    frame TEXT NOT NULL CHECK(frame IN ('raw', 'text')),
    data BLOB NOT NULL
);
CREATE TABLE block (
    thread_id TEXT NOT NULL REFERENCES threads(id),
    idx INTEGER NOT NULL,
    first_line INTEGER NOT NULL,
    n_lines INTEGER NOT NULL,
    first_seq INTEGER NOT NULL,
    last_seq INTEGER NOT NULL,
    text_dict INTEGER REFERENCES dict(id),
    ztext BLOB NOT NULL,
    raw_dict INTEGER REFERENCES dict(id),
    zraw BLOB NOT NULL,
    PRIMARY KEY(thread_id, idx)
);
CREATE TABLE line (
    thread_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    block_idx INTEGER NOT NULL,
    PRIMARY KEY(thread_id, idx),
    FOREIGN KEY(thread_id, block_idx) REFERENCES block(thread_id, idx)
);
CREATE TABLE message (
    thread_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    line_idx INTEGER NOT NULL,
    role TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    call_id TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY(thread_id, seq),
    FOREIGN KEY(thread_id, line_idx) REFERENCES line(thread_id, idx)
);
CREATE TABLE blob (
    key BLOB PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('decoded', 'text')),
    data BLOB NOT NULL
);
CREATE TABLE chunk (
    thread_id TEXT NOT NULL REFERENCES threads(id),
    idx INTEGER NOT NULL,
    seq_start INTEGER NOT NULL,
    seq_end INTEGER NOT NULL,
    off INTEGER NOT NULL,
    len INTEGER NOT NULL,
    UNIQUE(thread_id, idx)
);
CREATE TABLE index_meta (index_version INTEGER NOT NULL, indexed_chunks INTEGER NOT NULL);
INSERT INTO index_meta VALUES (0, 0);
CREATE TABLE handoff_docs (handoff_id TEXT PRIMARY KEY, markdown TEXT NOT NULL);
CREATE TABLE fact_extraction (
    thread_id TEXT PRIMARY KEY REFERENCES threads(id),
    version INTEGER NOT NULL,
    next_seq INTEGER NOT NULL CHECK(next_seq >= 0)
);
CREATE TRIGGER dict_replace BEFORE INSERT ON dict
WHEN EXISTS(SELECT 1 FROM dict WHERE id=new.id)
BEGIN SELECT RAISE(ABORT, 'Thread dictionaries are immutable'); END;
CREATE TRIGGER sealed_block_replace BEFORE INSERT ON block
WHEN EXISTS(SELECT 1 FROM block WHERE thread_id=new.thread_id AND idx=new.idx)
 AND new.idx < (SELECT MAX(idx) FROM block WHERE thread_id=new.thread_id)
BEGIN SELECT RAISE(ABORT, 'Only the last thread block may be extended'); END;
CREATE TRIGGER used_dict_update BEFORE UPDATE ON dict
BEGIN SELECT RAISE(ABORT, 'A used thread dictionary is immutable'); END;
CREATE TRIGGER used_dict_delete BEFORE DELETE ON dict
BEGIN SELECT RAISE(ABORT, 'A used thread dictionary is immutable'); END;
CREATE TRIGGER sealed_block_update BEFORE UPDATE ON block
WHEN old.idx < (SELECT MAX(idx) FROM block WHERE thread_id=old.thread_id)
BEGIN SELECT RAISE(ABORT, 'Only the last thread block may be extended'); END;
CREATE TRIGGER block_delete BEFORE DELETE ON block
BEGIN SELECT RAISE(ABORT, 'Thread blocks are append-only'); END;
"""
