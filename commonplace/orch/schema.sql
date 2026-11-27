-- Orchestrator tables in the workspace's runner.sqlite3 (commonplace.orch.store).
-- Idempotent: every object is CREATE ... IF NOT EXISTS, with no user_version gate,
-- so a file created by an older build or by a test seed opens without a rebuild.
-- Keep the first three tables and their index byte-for-byte equal (whitespace aside)
-- to the raw-SQL seed used by the acceptance tests; comments stay outside statements.

-- Config versions. Board key = board slug. channel: 'live' or 'exp:<name>'.
-- At most one accepted row per (board, channel); the live config is the row with
-- channel='live' AND status='accepted'. sha256 is over the body's UTF-8 bytes.
CREATE TABLE IF NOT EXISTS orch_configs (
 board TEXT NOT NULL,
 version INTEGER NOT NULL CHECK (version >= 1),
 parent_version INTEGER,
 channel TEXT NOT NULL,
 status TEXT NOT NULL CHECK (status IN ('proposed','accepted','rejected','superseded')),
 source_ref TEXT,
 sha256 TEXT NOT NULL,
 body TEXT NOT NULL,
 proposed_by TEXT NOT NULL,
 proposer_trust TEXT NOT NULL,
 decided_by TEXT,
 decided_at TEXT,
 created_at TEXT NOT NULL,
 PRIMARY KEY (board, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS orch_configs_one_accepted
 ON orch_configs(board, channel) WHERE status='accepted';

-- Per-board trigger cursor (task_events.id already handled). A row exists only while
-- the board's live config has [runner]. cursor NULL = arming; gen changes at every
-- configuring accept and every restore-ack reset.
CREATE TABLE IF NOT EXISTS orch_cursor (
 board TEXT PRIMARY KEY,
 cursor INTEGER,
 gen INTEGER NOT NULL
);

-- Last generation handed out per board; never deleted, so a re-created cursor row
-- never reuses a generation.
CREATE TABLE IF NOT EXISTS orch_cursor_gen (
 board TEXT PRIMARY KEY,
 last_gen INTEGER NOT NULL
);

-- One row: this store's incarnation and the restore flag. A restore marker left by
-- commonplace.backup.restore() becomes restore_flag=1 with a new incarnation on the
-- first open; only the typed acknowledgement clears it.
CREATE TABLE IF NOT EXISTS orch_state (
 id INTEGER PRIMARY KEY CHECK (id = 1),
 incarnation TEXT NOT NULL,
 restore_flag INTEGER NOT NULL CHECK (restore_flag IN (0, 1)),
 restore_after TEXT,
 restore_marker TEXT,
 restored_at TEXT,
 acked_by TEXT,
 acked_at TEXT,
 updated_at TEXT NOT NULL
);

-- Benchmark-gated promotion (commonplace.orch.benchmark). Append-only: a benchmark
-- compares one accepted exp:<name> candidate with the live version on replayed tasks;
-- results are never updated or deleted. Money is integer micro-USD.
CREATE TABLE IF NOT EXISTS orch_benchmarks (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 board TEXT NOT NULL,
 channel TEXT NOT NULL,
 candidate_version INTEGER NOT NULL,
 candidate_sha256 TEXT NOT NULL,
 live_version INTEGER NOT NULL,
 live_sha256 TEXT NOT NULL,
 thresholds TEXT NOT NULL,
 promote INTEGER NOT NULL CHECK (promote IN (0,1)),
 reasons TEXT NOT NULL,
 summary TEXT NOT NULL,
 recorded_by TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orch_benchmark_results (
 benchmark_id INTEGER NOT NULL,
 side TEXT NOT NULL CHECK (side IN ('live','candidate')),
 task TEXT NOT NULL,
 passed INTEGER NOT NULL CHECK (passed IN (0,1)),
 cost_micro_usd INTEGER CHECK (cost_micro_usd >= 0),
 tokens INTEGER CHECK (tokens >= 0),
 latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
 PRIMARY KEY (benchmark_id, side, task)
);
-- One row per promotion to live and per rollback of one. previous_version is the live
-- version the row superseded; new_version is the live row it created.
CREATE TABLE IF NOT EXISTS orch_promotions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 board TEXT NOT NULL,
 kind TEXT NOT NULL CHECK (kind IN ('promote','rollback')),
 benchmark_id INTEGER,
 of_promotion INTEGER,
 previous_version INTEGER NOT NULL,
 new_version INTEGER NOT NULL,
 decided_by TEXT NOT NULL,
 created_at TEXT NOT NULL
);

-- Machine-side subscription replays. Attempts are append-only; a plan pause keeps
-- completed sides and can only resume explicitly against the same pinned inputs.
CREATE TABLE IF NOT EXISTS orch_replays (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 board TEXT NOT NULL,
 channel TEXT NOT NULL,
 live_version INTEGER NOT NULL,
 candidate_version INTEGER NOT NULL,
 specification TEXT NOT NULL,
 state TEXT NOT NULL,
 reason TEXT,
 benchmark_id INTEGER,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orch_replay_attempts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 replay_id INTEGER NOT NULL,
 task TEXT NOT NULL,
 side TEXT NOT NULL CHECK(side IN ('live','candidate')),
 state TEXT NOT NULL,
 reason TEXT,
 passed INTEGER NOT NULL CHECK(passed IN (0,1)),
 tokens INTEGER,
 tokens_reason TEXT,
 latency_ms INTEGER NOT NULL,
 test_exit INTEGER
);
