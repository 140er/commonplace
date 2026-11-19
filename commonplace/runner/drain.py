"""Drain: a runner machine that starts no new card runs while the runs it has finish.

The hub enforces it itself: ``capacity.available`` answers 0 for a drained machine, so ``jobs.lease``
hands it nothing and queued runs stay queued (none is cancelled). Runs already started are not
touched. The runner program is not stopped: the hub has no channel that could stop it, and chat and
Coworker sessions do not read this flag. One row per machine in the workspace runner store,
read only when the table exists. A counter (``seq``) makes a stale confirmation a conflict.

Docs: docs/system/compute-overview.md.
"""

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS runner_drain ("
    "machine_id TEXT PRIMARY KEY, draining INTEGER NOT NULL CHECK(draining IN (0,1)),"
    "seq INTEGER NOT NULL, changed_by TEXT NOT NULL, changed_at TEXT NOT NULL,"
    "last_request_id TEXT, last_request_action TEXT)"
)
# Every change, in the same transaction as the flag: the audit line that cannot be missing.
LOG_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS runner_drain_log ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, machine_id TEXT NOT NULL, action TEXT NOT NULL "
    "CHECK(action IN ('drain','resume')), by TEXT NOT NULL, at TEXT NOT NULL)"
)


class Refused(Exception):
    """A drain change that cannot be made; ``reason`` is a fixed code the app words."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _exists(conn):
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runner_drain'"
        ).fetchone()
        is not None
    )


def _ensure(conn):
    """The tables, and the request-id columns on a table made before they existed."""
    conn.execute(SCHEMA)
    conn.execute(LOG_SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(runner_drain)")}
    for name in ("last_request_id", "last_request_action"):
        if name not in columns:
            conn.execute("ALTER TABLE runner_drain ADD COLUMN " + name + " TEXT")


def state(conn, machine_id):
    """``{draining, seq, changed_by, changed_at}``; a machine never drained reads as active, seq 0."""
    row = (
        conn.execute("SELECT * FROM runner_drain WHERE machine_id=?", (machine_id,)).fetchone()
        if _exists(conn)
        else None
    )
    if row is None:
        return {"draining": False, "seq": 0, "changed_by": None, "changed_at": None}
    return {
        "draining": bool(row["draining"]),
        "seq": row["seq"],
        "changed_by": row["changed_by"],
        "changed_at": row["changed_at"],
    }


def is_drained(conn, machine_id):
    return state(conn, machine_id)["draining"]


def log(conn, machine_id, limit):
    """The newest drain changes of a machine (empty where nothing was ever changed)."""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runner_drain_log'"
    ).fetchone():
        return []
    return conn.execute(
        "SELECT action,by,at FROM runner_drain_log WHERE machine_id=? ORDER BY id DESC LIMIT ?",
        (machine_id, limit),
    ).fetchall()


def change(conn, machine_id, *, draining, expected_seq, by, now, request_id=None):
    """Set the flag on the caller's transaction and return ``(state, repeated)``.

    Asking for the state the machine is already in is a repeat and changes nothing. A ``request_id`` is
    kept with the flag: the same id with the same action is a repeat even after the machine moved on, and
    the same id for the other action is ``request_reused``."""
    _ensure(conn)
    action = "drain" if draining else "resume"
    row = conn.execute(
        "SELECT last_request_id,last_request_action FROM runner_drain WHERE machine_id=?",
        (machine_id,),
    ).fetchone()
    if request_id is not None and row is not None and row["last_request_id"] == request_id:
        if row["last_request_action"] != action:
            raise Refused("request_reused")
        return state(conn, machine_id), True
    current = state(conn, machine_id)
    if current["draining"] is draining:
        return current, True
    if expected_seq != current["seq"]:
        raise Refused("stale_state")
    seq = current["seq"] + 1
    conn.execute(
        "INSERT INTO runner_drain(machine_id,draining,seq,changed_by,changed_at,last_request_id,"
        "last_request_action) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(machine_id) DO UPDATE SET draining=excluded.draining,seq=excluded.seq,"
        "changed_by=excluded.changed_by,changed_at=excluded.changed_at,"
        "last_request_id=excluded.last_request_id,last_request_action=excluded.last_request_action",
        (machine_id, int(draining), seq, by, now, request_id, action),
    )
    conn.execute(
        "INSERT INTO runner_drain_log(machine_id,action,by,at) VALUES (?,?,?,?)",
        (machine_id, action, by, now),
    )
    return state(conn, machine_id), False
