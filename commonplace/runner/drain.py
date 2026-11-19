"""Drain: a runner machine that starts no new card runs while the runs it has finish.

The hub enforces it itself: ``capacity.available`` answers 0 for a drained machine, so ``jobs.lease``
hands it nothing and queued runs stay queued (none is cancelled). Runs already started are not
touched. The runner program is not stopped: the hub has no channel that could stop it, and chat and
orchestrator sessions do not read this flag. One row per machine in the workspace runner store,
read only when the table exists. A counter (``seq``) makes a stale confirmation a conflict.

Docs: docs/system/compute-overview.md.
"""

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS runner_drain ("
    "machine_id TEXT PRIMARY KEY, draining INTEGER NOT NULL CHECK(draining IN (0,1)),"
    "seq INTEGER NOT NULL, changed_by TEXT NOT NULL, changed_at TEXT NOT NULL)"
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


def change(conn, machine_id, *, draining, expected_seq, by, now):
    """Set the flag on the caller's transaction and return the new state.

    Asking for the state the machine is already in is a repeat and changes nothing."""
    conn.execute(SCHEMA)
    current = state(conn, machine_id)
    if current["draining"] is draining:
        return current, True
    if expected_seq != current["seq"]:
        raise Refused("stale_state")
    seq = current["seq"] + 1
    conn.execute(
        "INSERT INTO runner_drain(machine_id,draining,seq,changed_by,changed_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(machine_id) DO UPDATE SET draining=excluded.draining,seq=excluded.seq,"
        "changed_by=excluded.changed_by,changed_at=excluded.changed_at",
        (machine_id, int(draining), seq, by, now),
    )
    return state(conn, machine_id), False
