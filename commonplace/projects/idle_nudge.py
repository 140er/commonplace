"""Nudge a board's orchestrator when open work exists but no card reaches done.

A board is stalled when it has actionable cards (todo or doing, not waiting on a
dependency or a person) and no card moved to done for N minutes. The nudge is one
``board.idle_nudge`` event addressed to the lease holder, or the coordinator seat
without a lease; the normal webhook wake path delivers it and the app shows it.
It re-arms when a card reaches done or after another N minutes.

The timer starts at the latest of: the last done card, the moment a sweep first
saw actionable work on the board, and the previous nudge. A board with no
actionable work forgets its start, so an empty or fully blocked board never
nudges and a card added to a long-quiet board starts a fresh clock.
"""

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from commonplace import events
from commonplace.boards.store import active_blockers
from commonplace.http_util import log_failure

logger = logging.getLogger(__name__)

KIND = "board.idle_nudge"
DEFAULT_MINUTES = 20
MAX_MINUTES = 1440
SWEEP_SECONDS = 60
_FMT = "%Y-%m-%dT%H:%M:%SZ"
TABLES = (
    "CREATE TABLE IF NOT EXISTS project_idle_nudge ("
    "board_id INTEGER PRIMARY KEY REFERENCES boards(id), "
    "minutes INTEGER CHECK(minutes BETWEEN 0 AND 1440))",
    "CREATE TABLE IF NOT EXISTS project_idle_nudge_state ("
    "board_id INTEGER PRIMARY KEY REFERENCES boards(id), "
    "work_since TEXT, nudged_at TEXT, summary TEXT)",
)


def _iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime(_FMT)


def _parse(value):
    return datetime.strptime(value, _FMT).replace(tzinfo=timezone.utc) if value else None


def _has(conn, table) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone())


def get(conn, board_id) -> dict:
    """``minutes`` is the stored value (None: default, 0: off); ``effective`` is the live N."""
    stored = None
    if _has(conn, "project_idle_nudge"):
        row = conn.execute(
            "SELECT minutes FROM project_idle_nudge WHERE board_id=?", (board_id,)
        ).fetchone()
        stored = row["minutes"] if row else None
    effective = DEFAULT_MINUTES if stored is None else stored
    return {"minutes": stored, "effective": effective, "enabled": effective > 0}


def put(conn, board_id, body):
    if not isinstance(body, dict) or set(body) != {"minutes"}:
        raise ValueError("Invalid idle nudge settings")
    value = body["minutes"]
    if value is not None and (type(value) is not int or not 0 <= value <= MAX_MINUTES):
        raise ValueError("Invalid idle nudge minutes")
    conn.execute(TABLES[0])
    conn.execute(
        "INSERT OR REPLACE INTO project_idle_nudge(board_id,minutes) VALUES (?,?)",
        (board_id, value),
    )


def snapshot(conn, board_id, now: datetime) -> dict:
    """Counts of actionable work and the last done time for one board."""
    at = _iso(now)
    last_done = conn.execute(
        "SELECT MAX(done_at) FROM tasks WHERE board_id=? AND status='done'", (board_id,)
    ).fetchone()[0]
    counts = {"todo": 0, "doing": 0, "checks": 0, "expired": 0}
    for row in conn.execute(
        "SELECT * FROM tasks WHERE board_id=? AND status IN ('todo','doing')", (board_id,)
    ):
        person = row["ask"] or any(
            (row[key] or "").startswith("user:") for key in ("assignee", "ask_to")
        )
        awaiting_release = row["restricted_origin"] != 0 and not row["approved_by"]
        if person or awaiting_release or active_blockers(conn, row["id"]):
            continue
        counts[row["status"]] += 1
        if row["checks"] == "pending":
            counts["checks"] += 1
        if row["status"] == "doing" and row["claim_until"] and row["claim_until"] <= at:
            counts["expired"] += 1
    return {**counts, "last_done_at": last_done}


def _state(conn, board_id):
    if not _has(conn, "project_idle_nudge_state"):
        return {"work_since": None, "nudged_at": None, "summary": None}
    row = conn.execute(
        "SELECT * FROM project_idle_nudge_state WHERE board_id=?", (board_id,)
    ).fetchone()
    return dict(row) if row else {"work_since": None, "nudged_at": None, "summary": None}


def banner(conn, board_id):
    """The latest nudge while it is still unanswered by a done card, else None."""
    if not _has(conn, "project_idle_nudge_state"):
        return None
    state = _state(conn, board_id)
    if not state["nudged_at"] or not state["summary"]:
        return None
    done = conn.execute(
        "SELECT MAX(done_at) FROM tasks WHERE board_id=? AND status='done'", (board_id,)
    ).fetchone()[0]
    if done and done > state["nudged_at"]:
        return None
    return {"at": state["nudged_at"], **json.loads(state["summary"])}


def due(conn, board, now: datetime):
    """``(payload or None, new work_since)`` for one board; reads only."""
    setting = get(conn, board["id"])
    if not setting["enabled"]:
        return None, None
    snap = snapshot(conn, board["id"], now)
    if not (snap["todo"] or snap["doing"]):
        return None, None
    state = _state(conn, board["id"])
    work_since = state["work_since"] or _iso(now)
    anchor = max(filter(None, (snap["last_done_at"], work_since, state["nudged_at"])))
    if now < _parse(anchor) + timedelta(minutes=setting["effective"]):
        return None, work_since
    started = max(filter(None, (snap["last_done_at"], work_since)))
    stalled = int((now - _parse(started)).total_seconds() // 60)
    payload = {
        "board": board["slug"],
        "minutes": setting["effective"],
        "stalled_minutes": stalled,
        "todo": snap["todo"],
        "doing": snap["doing"],
        "checks": snap["checks"],
        "expired": snap["expired"],
    }
    return payload, work_since


def _holder(gateway, ws, slug, now: datetime):
    """The live lease holder of the board, or None."""
    if not (Path(gateway.workspace_dir(ws)) / "runner.sqlite3").exists():
        return None
    from commonplace.orch import store as orch_store

    try:
        with orch_store.open_existing(gateway, ws).connection() as conn:
            row = conn.execute(
                "SELECT holder_actor FROM orch_leases WHERE board=? AND token_hash IS NOT NULL "
                "AND released_at IS NULL AND expires_at>? ORDER BY claimed_at DESC LIMIT 1",
                (slug, _iso(now)),
            ).fetchone()
    except sqlite3.Error:
        return None
    return row["holder_actor"] if row else None


def recipient(gateway, ws, board, now: datetime):
    """Lease holder, else the board's coordinator seat, else None."""
    return _holder(gateway, ws, board["slug"], now) or board["coordinator"] or None


def sweep_workspace(gateway, ws, now: datetime) -> list[dict]:
    from commonplace.boards.service import open_store
    from commonplace.projects import orchestration

    directory = gateway.workspace_dir(ws)
    if not (Path(directory) / "boards.sqlite3").exists():
        return []
    sent, changes = [], []
    with orchestration.board_read(directory) as conn:
        for board in conn.execute("SELECT * FROM boards WHERE archived=0").fetchall():
            payload, work_since = due(conn, board, now)
            state = _state(conn, board["id"])
            target = recipient(gateway, ws, board, now) if payload else None
            if payload and target:
                payload = {**payload, "coordinator": target}
                changes.append((board, work_since, _iso(now), payload))
            elif work_since != state["work_since"]:
                changes.append((board, work_since, None, None))
    if not changes:
        return sent
    store = open_store(directory, db=gateway.db, workspace_id=ws)
    with store.transaction() as conn:
        for table in TABLES:
            conn.execute(table)
        for board, work_since, nudged_at, payload in changes:
            conn.execute(
                "INSERT INTO project_idle_nudge_state(board_id,work_since,nudged_at,summary) "
                "VALUES (?,?,?,?) ON CONFLICT(board_id) DO UPDATE SET work_since=excluded.work_since,"
                "nudged_at=COALESCE(excluded.nudged_at, nudged_at),"
                "summary=COALESCE(excluded.summary, summary)",
                (board["id"], work_since, nudged_at, json.dumps(payload) if payload else None),
            )
    for board, _, _, payload in changes:
        if payload is None:
            continue
        events.emit(
            gateway.db,
            ws,
            KIND,
            subject=board["slug"],
            actor="system",
            data=payload,
        )
        sent.append(payload)
    return sent


def sweep(gateway, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    sent = []
    for workspace in gateway.db.workspaces():
        try:
            sent += sweep_workspace(gateway, workspace["id"], now)
        except Exception as exc:
            log_failure(logger, "idle nudge sweep failed", exc)
    return sent


class IdleNudgeWorker:
    def __init__(self, gateway, *, start=True, interval=SWEEP_SECONDS, now=None):
        self.gateway, self.interval = gateway, interval
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._stop = threading.Event()
        self.thread = None
        if start:
            self.thread = threading.Thread(target=self._run, name="idle-nudge", daemon=True)
            self.thread.start()

    def stop(self):
        self._stop.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=5)

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                sweep(self.gateway, self.now())
            except Exception as exc:
                log_failure(logger, "idle nudge worker failed", exc)


def install(gateway):
    if getattr(gateway, "idle_nudge_worker", None) is not None:
        return
    gateway.idle_nudge_worker = IdleNudgeWorker(
        gateway, start=os.environ.get("COMMONPLACE_IDLE_NUDGE_WORKER", "on") != "off"
    )
    gateway.db.add_pre_close_callback(gateway.idle_nudge_worker.stop)
