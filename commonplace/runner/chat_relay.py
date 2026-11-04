"""Content-free chat relay queue and frozen runner contracts."""

CHAT_ONLINE_S = 90
CHAT_WAIT_S = 120
CHAT_LEASE_S = 120
CHAT_MAIN_FRESH_S = 600
DAILY_CAP = 200
MAX_ANSWER_BYTES = 16_384
MAX_BODY_BYTES = 20_480
CONTEXTS = ("minimal", "full")
RELAY_STATES = ("queued", "leased", "done", "failed", "expired", "skipped")
RELAY_CODES = (
    "ok",
    "no_runner",
    "lease_expired",
    "cli_failed",
    "cli_timeout",
    "cli_not_configured",
    "bad_output",
    "no_access",
    "daily_limit",
)
RUNNER_FAIL_CODES = ("cli_failed", "cli_timeout", "cli_not_configured", "bad_output")


class Fenced(Exception):
    """The job's lease no longer authorizes this operation (HTTP 409)."""


def ensure_schema(db) -> None:
    with db._lock:
        db._conn.executescript("""
            CREATE TABLE IF NOT EXISTS chat_runner_jobs (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                board TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                cli TEXT,
                model TEXT,
                state TEXT NOT NULL CHECK (state IN ('queued','leased','done','failed','expired','skipped')),
                code TEXT NOT NULL DEFAULT '',
                machine_id TEXT,
                lease_hash TEXT,
                lease_until TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (workspace_id, board, message_id)
            );
            CREATE INDEX IF NOT EXISTS chat_runner_jobs_user ON chat_runner_jobs(workspace_id, user_id, state, created_at);
            CREATE TABLE IF NOT EXISTS chat_runners_seen (
                machine_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                clis TEXT NOT NULL,
                context TEXT NOT NULL DEFAULT 'minimal' CHECK (context IN ('minimal','full')),
                relay_always INTEGER NOT NULL DEFAULT 0 CHECK (relay_always IN (0,1)),
                seen_at TEXT NOT NULL
            );
        """)


def sweep(db, now: float) -> None:
    raise NotImplementedError


def online(db, workspace_id: str, user_id: str, now: float, cli: str | None = None) -> list[dict]:
    raise NotImplementedError


def main_active(gateway, principal, board: str, now: float) -> bool:
    raise NotImplementedError


def enqueue(gateway, principal, board: str, message_id: int, now: float) -> bool:
    raise NotImplementedError


def lease(
    gateway, machine: dict, clis: list[str], context: str, relay_always: bool, now: float
) -> dict | None:
    raise NotImplementedError


def answer(
    gateway, machine: dict, job: str, lease_token: str, text: str, cli: str, model: str, now: float
) -> dict:
    raise NotImplementedError


def fail(gateway, machine: dict, job: str, lease_token: str, code: str, now: float) -> dict:
    raise NotImplementedError


def caller_state(db, workspace_id: str, user_id: str, now: float, cli: str | None = None) -> dict:
    raise NotImplementedError
