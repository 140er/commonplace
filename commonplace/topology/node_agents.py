"""The agents part of the node detail read: who works on one runner machine right now.

Three existing reads, none of them new data:

- the machine's two connections (its poll connection and its worker), from the control database;
- its runs: start jobs of the runner store that are queued, leased, starting, uncertain, or
  started with no end reported, each with where its run log is;
- its coordinators: boards whose live orchestrator config runs on this machine, with the lease
  holder and the builders that board's checkpoint lists.

A node that is not an enrolled runner machine says so. Nothing is guessed from a node's name.
Grants decide what is shown: connections only to a signed-in workspace owner, and a run or a
board only to a caller that may read the board's wing, so a member sees their own boards' work
and nothing else.
"""

import logging
from datetime import timedelta

from commonplace.boards.store import BoardStore
from commonplace.control_db import ControlError
from commonplace.extensions import ToolContext
from commonplace.orch import durable, schema, store as orch_store
from commonplace.runner import jobs, ledger, registry

from . import model

LOG = logging.getLogger(__name__)
ACTIVE = ("queued", "leased", "starting", "uncertain")
# A started run whose end the machine never reported stops counting as running after this long.
RUN_WINDOW = timedelta(hours=6)
MAX_ROWS = 50
ROLE = "main"


def _owner(principal) -> bool:
    return bool(principal.person and principal.role == "owner")


def _readable_boards(gateway, principal) -> dict:
    """Slug to wing of the unarchived boards this caller may read."""
    with BoardStore(gateway.workspace_dir(principal.workspace_id)).connection() as conn:
        rows = conn.execute("SELECT slug, wing, archived FROM boards").fetchall()
    return {
        row["slug"]: row["wing"]
        for row in rows
        if not row["archived"] and principal.policy.can_read_wing(row["wing"])
    }


def _connections(gateway, principal, machine) -> list[dict]:
    result = []
    for kind in ("machine", "worker"):
        try:
            row = gateway.db.connection(machine[f"{kind}_connection_id"], principal.workspace_id)
        except ControlError:
            continue
        result.append(
            {
                "kind": kind,
                "name": row["name"],
                "last_used_at": row["last_used_at"],
                "revoked": bool(row["revoked_at"]),
            }
        )
    return result


def _runs(gateway, principal, machine, boards, now) -> list[dict]:
    st = jobs.open_store(gateway, principal.workspace_id)
    with st.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM runner_jobs WHERE machine_id=? ORDER BY updated_at DESC, rowid DESC LIMIT 200",
            (machine["id"],),
        ).fetchall()
        ledgers = ledger.views(conn, [row["id"] for row in rows])
    runs = []
    cutoff = model.stamp(now - RUN_WINDOW)
    for row in rows:
        if row["board_slug"] not in boards:
            continue
        run = ledgers.get(row["id"]) or {}
        ended = run.get("status") in ledger.END_STATUSES
        running = row["state"] in ACTIVE or (
            row["state"] == "started" and not ended and row["updated_at"] >= cutoff
        )
        if not running:
            continue
        runs.append(
            {
                "job": row["id"],
                "state": row["state"],
                "board": row["board_slug"],
                "card": row["task_number"],
                "project": row["project"],
                "topic": row["topic"],
                "thread_id": row["thread_id"],
                "updated_at": row["updated_at"],
                "started_at": row["started_at"],
                "log_ref": run.get("log_ref"),
            }
        )
    return runs[:MAX_ROWS]


def _coordinators(gateway, principal, machine, boards) -> list[dict]:
    ctx = ToolContext(principal=principal, gateway=gateway)
    st = orch_store.open_store(gateway, principal.workspace_id)
    with st.connection() as conn:
        configured = orch_store.configured_boards_in(conn)
        versions = {
            slug: orch_store.accepted_in(conn, slug) for slug in configured if slug in boards
        }
    found = []
    for slug, version in versions.items():
        try:
            runner = version.config().runner
        except schema.ConfigError:
            continue
        if runner is None or runner.machine != machine["id"]:
            continue
        try:
            view = durable.checkpoint(ctx, slug, ROLE)
        except durable.DurableError:
            continue
        found.append(
            {
                "board": slug,
                "lease": view["lease"],
                "open_steps": len(view["open"]),
                "threads": [
                    {
                        "thread_id": job["thread_id"],
                        "card": job["task_number"],
                        "state": job["state"],
                        "updated_at": job["updated_at"],
                    }
                    for job in view["jobs"]
                    if job.get("source") == "thread" and job["thread_id"]
                ][:MAX_ROWS],
            }
        )
    return found


def read(gateway, principal, runner_id, *, now, by_key=False) -> dict:
    """The agents of the node whose ``runner_id`` fact is ``runner_id`` (or ``None``).

    ``by_key`` is the read for a key the topology does not hold: ``runner_id`` is then only a
    guess taken from the request, so a machine this workspace does not have answers exactly as
    any other unknown key does (``not_a_runner``), never ``machine_not_found``."""
    if not isinstance(runner_id, str) or not registry.MACHINE_ID.fullmatch(runner_id):
        return {"applicable": False, "reason": "not_a_runner"}
    machine = registry.get_machine(gateway.db, principal.workspace_id, runner_id)
    if machine is None:
        return {"applicable": False, "reason": "not_a_runner" if by_key else "machine_not_found"}
    boards = _readable_boards(gateway, principal)
    return {
        "applicable": True,
        "machine": {
            "id": machine["id"],
            "label": machine["label"],
            "enabled": bool(machine["enabled"]),
            "revoked": bool(machine["revoked_at"]),
        },
        "connections": _connections(gateway, principal, machine) if _owner(principal) else None,
        "runs": _runs(gateway, principal, machine, boards, now),
        "coordinators": _coordinators(gateway, principal, machine, boards),
    }
