"""Map discovery: "Start agent work on map" as one normal card start per runner machine.

``start`` makes one card per runner machine on the ``compute-map`` board (wing ``topology``)
and starts it through the ordinary ``card_start`` path, so every run is a normal run with a
card, a job and a log. The card's body is the discovery prompt (``card_body``): the agent
runs the product's read-only collector (``collector.py``) and hands its result to
``topology_propose``, so what it finds is a proposal the owner approves in the app, and a
rerun only asks about what really changed.

A machine that cannot be started is never skipped silently: every machine of the workspace
gets one row in the run, with an outcome (``started``, ``running``, ``unreachable`` or
``refused``) and a reason code. The rows are the table ``topology_discovery_runs`` in
``topology.sqlite3``. Nothing here reads or changes anything on a machine.
"""

from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta

from commonplace import urls
from commonplace.boards.service import BoardService, SpaceExists
from commonplace.extensions import ToolError
from commonplace.http_util import log_failure
from commonplace.policy import canonical_wing
from commonplace.redact import redact
from commonplace.runner import jobs, registry
from commonplace.runner.predicate import grant_values
from commonplace.runner.tools import card_start, person_owner

from . import model, status, store
from .model import TOPOLOGY_WING

logger = logging.getLogger(__name__)
BOARD = "compute-map"
OUTCOMES = ("started", "running", "unreachable", "refused")
# A started agent that never ends its card stops blocking a rerun after this long.
ACTIVE_HOURS = 6
ACTIVE_JOBS = ("queued", "leased", "starting", "uncertain")

PROMPT = """\
Map discovery for the Compute map. This is read-only work: you collect facts about THIS machine and about how it reaches the hub and other machines, and you propose them to the topology. You change nothing on this machine.

1. Run exactly this one command and read its JSON output:
   {command}
   If Python or the module is missing, install nothing: say so in a comment on this card and stop.
2. Call topology_propose with the "propose" object of that output exactly as it is (its items and its evidence). Then, when "propose_peers" is not null, call topology_propose once more with that object exactly as it is. Do not add, edit or drop facts, and add nothing else from this machine.
3. Comment on this card with the "summary" line, one line per entry of "unreachable" (what could not be reached, and why), and the answers of topology_propose. Then move this card to done.

Run no other command. Do not open, read or copy key files, passwords or any other credential. Do not change any configuration. If topology_propose refuses something, say so in the comment and stop.
"""

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS topology_discovery_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run TEXT NOT NULL,
        machine_id TEXT NOT NULL, label TEXT NOT NULL,
        outcome TEXT NOT NULL CHECK(outcome IN ('started','running','unreachable','refused')),
        reason TEXT, card INTEGER, job TEXT,
        created_at TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'map', subject TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS topology_discovery_run ON topology_discovery_runs(run)",
)


def _title(label) -> str:
    return f"Map discovery on {label}"


@dataclass(frozen=True)
class Work:
    """What a start asks of a machine: the card's title and body, and which machines may take it.

    ``kind`` and ``subject`` tell runs apart (a second press for the same kind and subject
    while one is underway starts nothing). ``first_only`` stops after the first machine that
    started or already runs it: one agent is enough for one site."""

    kind: str = "map"
    title: Callable = _title
    body: Callable | None = None
    subject: str | None = None
    only: Callable = lambda machine: True
    first_only: bool = False


def card_body(runner_id, hub_url=None) -> str:
    """The discovery prompt for one machine."""
    command = f"python3 -m commonplace.topology.collector --runner-id {runner_id}"
    if hub_url:
        command += f" --hub-url {hub_url}"
    return PROMPT.format(command=command)


@contextmanager
def _writing(topo):
    with topo._write() as conn:
        for statement in _SCHEMA:
            conn.execute(statement)
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(topology_discovery_runs)")
        }
        if "kind" not in columns:  # a table made before add machine / add site
            conn.execute(
                "ALTER TABLE topology_discovery_runs ADD COLUMN kind TEXT NOT NULL DEFAULT 'map'"
            )
            conn.execute("ALTER TABLE topology_discovery_runs ADD COLUMN subject TEXT")
        yield conn


def _of(row, name, default=None):
    return row[name] if name in row.keys() else default


def _now() -> str:
    return model.stamp(model.clock(None))


def _board(service):
    try:
        service.board_add(slug=BOARD, name="Compute map", wing=TOPOLOGY_WING)
    except SpaceExists:
        pass


def _reason(exc) -> str:
    return str(exc).removeprefix("no: ").strip()[:200] or "refused"


def _active(ctx, store_, card):
    """The newest start of a discovery card that is still underway, or ``None``."""
    now = model.clock(None)
    limit = model.stamp(now - timedelta(hours=ACTIVE_HOURS))
    for run in reversed(jobs.status(store_, card, ctx.principal)):
        if run["state"] in ACTIVE_JOBS or (run["state"] == "started" and run["updated_at"] > limit):
            return run
    return None


def _running(ctx, topo, store_, machine_id, work):
    with topo._read() as conn:
        known = (
            conn is not None
            and conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='topology_discovery_runs'"
            ).fetchone()
        )
        rows = (
            conn.execute(
                "SELECT * FROM topology_discovery_runs WHERE machine_id=? AND card IS NOT NULL "
                "AND outcome IN ('started','running') ORDER BY id DESC LIMIT 24",
                (machine_id,),
            ).fetchall()
            if known
            else []
        )
    for row in rows:
        if (_of(row, "kind", "map"), _of(row, "subject")) != (work.kind, work.subject):
            continue
        run = _active(ctx, store_, row["card"])
        if run is not None:
            return row["card"], run["job"]
    return None


def _decide(ctx, machine, signal, project, topic, hub_url, service, store_, topo, work):
    """``(outcome, reason, card, job)`` for one machine."""
    if not machine["enabled"]:
        return "unreachable", "disabled", None, None
    if signal["status"] in ("down", "unknown"):
        return "unreachable", signal["reason"], None, None
    if machine["binding_state"] != "owner_attested":
        return "refused", "not_attested", None, None
    wings = {canonical_wing(w) for w in grant_values(machine, "wings")}
    if canonical_wing(TOPOLOGY_WING) not in wings:
        return "refused", "no_topology_wing", None, None
    projects, topics = grant_values(machine, "projects"), grant_values(machine, "topics")
    chosen_project = project or (projects[0] if projects else None)
    chosen_topic = topic or (topics[0] if topics else None)
    if chosen_project not in projects or chosen_topic not in topics:
        return "refused", "no_project_or_topic", None, None
    active = _running(ctx, topo, store_, machine["id"], work)
    if active:
        return "running", None, *active
    label = redact(machine["label"])[0]
    card = service.task_add(
        title=work.title(label),
        body=(work.body or card_body)(machine["id"], hub_url),
        board=BOARD,
        status="todo",
    )["number"]
    try:
        _, job = card_start(
            ctx, card, machine["id"], chosen_project, chosen_topic, request_key=None
        )
    except Exception as exc:
        service.task_update(card, status="backlog")
        if not isinstance(exc, (ToolError, jobs.Conflict, ValueError)):
            log_failure(logger, "map discovery start failed", exc)
            return "refused", "start_failed", card, None
        return "refused", _reason(exc), card, None
    return "started", None, card, job["job_id"]


def start(ctx, project=None, topic=None, hub_url=None, work=None) -> dict:
    """Start the work on every runner machine ``work`` allows; the owner's button.

    Returns ``{"run", "machines": [{machine, label, outcome, reason, card, job}], "note"}``."""
    if not person_owner(ctx.principal):
        raise ToolError("no: a signed-in workspace owner is required")
    work = work or Work()
    for name, value in (("project", project), ("topic", topic)):
        if value is not None and (not isinstance(value, str) or not value):
            raise ToolError(f"no: invalid {name}")
    gateway, workspace = ctx.gateway, ctx.principal.workspace_id
    hub_url = hub_url if hub_url is not None else urls.AGENT_URL
    machines = [
        m for m in registry.machines(ctx.db, workspace) if not m["revoked_at"] and work.only(m)
    ]
    result = {"run": secrets.token_hex(6), "machines": [], "note": None}
    if not machines:
        result["note"] = "no_runner_machines"
        return result
    signals = status.read_status(gateway, workspace, graph={"nodes": [], "links": []})["machines"]
    service = BoardService(ctx)
    _board(service)
    store_ = jobs.open_store(gateway, workspace)
    jobs.sweep(store_, ctx.db, workspace)
    topo = store.open_topology(gateway, workspace)
    # One start at a time per workspace, rows included: a second press then sees the first
    # one's runs as `running` instead of starting the same machines again.
    lock = gateway.__dict__.setdefault("_topology_discovery_lock", threading.Lock())
    with lock:
        for machine in machines:
            signal = signals.get(machine["id"], {"status": "unknown", "reason": "never_reported"})
            try:
                outcome, reason, card, job = _decide(
                    ctx, machine, signal, project, topic, hub_url, service, store_, topo, work
                )
            except Exception as exc:
                # One machine's failure must not hide the machines already started.
                log_failure(logger, "map discovery could not start a machine", exc)
                outcome, reason, card, job = "refused", "start_failed", None, None
            result["machines"].append(
                {
                    "machine": machine["id"],
                    "label": redact(machine["label"])[0],
                    "outcome": outcome,
                    "reason": reason,
                    "card": card,
                    "job": job,
                }
            )
            if work.first_only and outcome in ("started", "running"):
                break
        with _writing(topo) as conn:
            now = _now()
            for entry in result["machines"]:
                conn.execute(
                    "INSERT INTO topology_discovery_runs "
                    "(run,machine_id,label,outcome,reason,card,job,created_at,kind,subject) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        result["run"],
                        entry["machine"],
                        entry["label"],
                        entry["outcome"],
                        entry["reason"],
                        entry["card"],
                        entry["job"],
                        now,
                        work.kind,
                        work.subject,
                    ),
                )
    return result


def latest(ctx) -> dict:
    """The newest run's rows, each started one with its job's live state."""
    if not ctx.principal.policy.can_read_wing(TOPOLOGY_WING):
        raise ToolError("no: this connection cannot read the topology wing")
    topo = store.open_topology(ctx.gateway, ctx.principal.workspace_id)
    result = {"run": None, "created_at": None, "machines": []}
    with topo._read() as conn:
        known = (
            conn is not None
            and conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='topology_discovery_runs'"
            ).fetchone()
        )
        # Only the map button's own runs: add machine and add site keep theirs apart.
        newest = (
            conn.execute(
                "SELECT * FROM topology_discovery_runs ORDER BY id DESC LIMIT 200"
            ).fetchall()
            if known
            else []
        )
        last = next((row for row in newest if _of(row, "kind", "map") == "map"), None)
        rows = (
            conn.execute(
                "SELECT * FROM topology_discovery_runs WHERE run=? ORDER BY id", (last["run"],)
            ).fetchall()
            if last
            else []
        )
    store_ = jobs.open_store(ctx.gateway, ctx.principal.workspace_id)
    states = {}
    for row in rows:
        if row["card"] is not None and row["card"] not in states:
            try:
                states[row["card"]] = {
                    r["job"]: r["state"] for r in jobs.status(store_, row["card"], ctx.principal)
                }
            except ToolError:
                states[row["card"]] = {}
    for row in rows:
        result["run"], result["created_at"] = row["run"], row["created_at"]
        result["machines"].append(
            {
                "machine": row["machine_id"],
                "label": row["label"],
                "outcome": row["outcome"],
                "reason": row["reason"],
                "card": row["card"],
                "job": row["job"],
                "job_state": states.get(row["card"], {}).get(row["job"]),
            }
        )
    return result
