"""Workspace-local resource reports and the configured runner's start ceiling."""

import json

from . import headroom, jobs, registry

SCHEMA = """CREATE TABLE IF NOT EXISTS runner_resources (
 machine_id TEXT PRIMARY KEY, reported_at TEXT NOT NULL, resources TEXT NOT NULL
)"""
REPORT_SECONDS = 600  # covers the runner's longest normal poll interval


def report(store, machine, data):
    headroom.validate(data)
    with store.transaction() as conn:
        conn.execute(SCHEMA)
        conn.execute(
            "INSERT OR REPLACE INTO runner_resources VALUES (?,?,?)",
            (machine["id"], jobs._now(), json.dumps(data)),
        )
    return {"ok": True}


def available(conn, machine, now):
    """Upper bound on concurrent starts, including hourly and resource headroom.

    Older clients without reports retain their granted caps. Stale reports cannot
    attest headroom. The local runner always probes again immediately before launch.
    """
    hourly = conn.execute(
        "SELECT COUNT(*) FROM runner_jobs WHERE machine_id=? AND starting_at>?",
        (machine["id"], jobs._after(now, -3600)),
    ).fetchone()[0]
    bounds = [machine["max_in_flight"], max(0, machine["max_starts_per_hour"] - hourly)]
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='runner_resources'").fetchone():
        row = conn.execute(
            "SELECT * FROM runner_resources WHERE machine_id=?", (machine["id"],)
        ).fetchone()
        if row:
            bounds.extend(
                headroom.slots(json.loads(row["resources"]))
                if row["reported_at"] > jobs._after(now, -REPORT_SECONDS)
                else [0]
            )
    return min(bounds)


def board_capacity(gateway, ws, conn, board, cap, now):
    from commonplace.orch import store

    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='orch_configs'").fetchone():
        return None
    version = store.accepted_in(conn, board)
    config = version.config() if version else None
    if config is None or config.runner is None:
        return None
    machine = registry.get_machine(gateway.db, ws, config.runner.machine)
    capacity = 0
    if (
        machine
        and machine["enabled"]
        and not machine["revoked_at"]
        and machine["binding_state"] == "owner_attested"
    ):
        capacity = available(conn, machine, now)
    return {"runner_capacity": capacity, "effective_start_limit": min(cap, capacity)}
