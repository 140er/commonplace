"""Bounded, per-viewer canonical task observations, never shared chat messages.

The durable job/card origin remains the only linkage. GET recomputes this view
from canonical rows after Store fences the originating session, audience, bot,
visibility version and viewer. No provider, launch, sweep, approval or task write
occurs here. BoardService.task_show materializes unbounded history and expires
claims, so this projection uses its board/visibility predicates with bounded
read-only queries. The card link remains the place for full history, artifacts
and the existing owner/task decision flow.

SQLite snapshots are observations, not execution receipts or instantaneous
revocation guarantees. Recheck board/origin authority in a fresh snapshot before
returning; Store holds its existing control/conversation fences throughout.
Legacy jobs lacking the claim-time binding fail closed rather than backfilling
authority from today's group. Nothing is persisted into a group timeline, so a
newly unauthorized member can never receive cached board content on a later GET.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from commonplace import redact
from commonplace.boards.service import BoardService
from commonplace.boards.store import link_visible
from commonplace.control_db import ControlError
from commonplace.extensions import ToolContext, ToolError
from mempalace.sqlite_crypto import connect, row_factory

from .store import StoreError, _number, _text

FIELD_BYTES = 2_000
EVENTS = 6
EVENT_CANDIDATES = 32
EVIDENCE = 5
TEXT_FIELDS = (
    "title",
    "body",
    "status_line",
    "ask",
    "ask_to",
    "review_context",
    "pr_url",
)
EVENT_FIELDS = ("text", "choice", "context")
EVENT_KINDS = ("comment", "ask", "decide", "handoff", "release")


@contextmanager
def _read(path):
    # No constructors/migrations/sweeps: unavailable source stays unavailable.
    conn = connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)
    try:
        conn.row_factory = row_factory
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        yield conn
    finally:
        conn.close()


def _bounded(columns):
    # Omit whole oversized fields, including before transfer out of SQLite.
    # Identifiers here are fixed module constants, never caller/model input.
    return ",".join(
        f"CASE WHEN length(CAST({name} AS BLOB))<={FIELD_BYTES} THEN {name} END AS {name},"
        f"coalesce(length(CAST({name} AS BLOB))>{FIELD_BYTES},0) AS omitted_{name}"
        for name in columns
    )


def _fields(row, names):
    result, omitted, redactions = {}, [], set()
    for name in names:
        if row["omitted_" + name]:
            omitted.append(name)
        elif row[name] is not None:
            result[name], kinds = redact.redact(row[name])
            redactions.update(kinds)
    result["omitted_fields"] = omitted
    result["redacted"] = bool(redactions)
    return result


def _authority(conn, authority, job, task, viewer):
    origin = BoardService(
        ToolContext(principal=authority.user(job["user_id"]), gateway=authority.gateway),
        store=False,
    )
    reader = BoardService(
        ToolContext(principal=authority.user(viewer), gateway=authority.gateway), store=False
    )
    board = origin._board(conn, task["board"])
    origin._write(board)
    reader._board(conn, task["board"])
    row = conn.execute(
        "SELECT t.id,t.board_id,t.version FROM tasks t JOIN chat_task_origins o ON o.task_id=t.id "
        "WHERE t.board_id=? AND o.actor=? AND o.origin_key=? "
        + ("AND t.id=?" if task["number"] is not None else ""),
        (
            board["id"],
            origin.principal.actor,
            f"coworker:{job['conversation_id']}:{job['id']}",
        )
        + ((task["number"],) if task["number"] is not None else ()),
    ).fetchone()
    if row is None:
        raise StoreError("not_found", 404)
    return dict(row), reader.principal


def _runner(authority, job, task, viewer):
    # Same visibility as runner.jobs.status, with an exact originating request
    # key, not another task attempt. Deliberately no lease expiry sweep or replay.
    if viewer.role != "owner" and viewer.user_id != job["user_id"]:
        return None
    path = Path(authority.gateway.workspace_dir(authority.workspace_id)) / "runner.sqlite3"
    if not path.is_file():
        return None
    origin = f"coworker:{job['conversation_id']}:{job['id']}"
    with _read(path) as conn:
        row = conn.execute(
            "SELECT state,updated_at,cancel_requested_at,"
            + _bounded(("outcome_code",))
            + " FROM runner_jobs WHERE task_number=? AND board_slug=? "
            "AND requested_by=? AND request_key=? LIMIT 1",
            (
                task["number"],
                task["board"],
                "user:" + job["user_id"],
                "chat:" + hashlib.sha256(origin.encode()).hexdigest(),
            ),
        ).fetchone()
        if row is None:
            return None
        return {
            **{key: row[key] for key in ("state", "updated_at", "cancel_requested_at")},
            **_fields(row, ("outcome_code",)),
        }


def current(authority, job, projection, user_id):
    """Fence publication after local control-receipt projection as well."""
    try:
        path = Path(authority.gateway.workspace_dir(authority.workspace_id)) / "boards.sqlite3"
        with _read(path) as conn:
            binding, _ = _authority(conn, authority, job, projection, user_id)
            if binding["version"] != projection["version"]:
                raise StoreError("context_changed", 409)
    except (OSError, sqlite3.Error, ControlError, ToolError, ValueError, TypeError, KeyError):
        raise StoreError("progress_unavailable", 404) from None


def project(authority, job, task, user_id):
    """Return allowlisted source facts; failure exposes no source text or IDs."""
    try:
        if task["number"] is not None:
            _number(task["number"])
        _text(task["board"], 100)
        path = Path(authority.gateway.workspace_dir(authority.workspace_id)) / "boards.sqlite3"
        with _read(path) as conn:
            binding, viewer = _authority(conn, authority, job, task, user_id)
            # A crash between canonical commit and front acknowledgement leaves
            # the origin binding durable. Recover its link without replaying any
            # effect or changing the interrupted/failed front job's state.
            task = {"board": task["board"], "number": binding["id"]}
            row = conn.execute(
                "SELECT status,version,updated_at,review_kind,proposal_status,"
                + _bounded(TEXT_FIELDS)
                + " FROM tasks WHERE id=?",
                (task["number"],),
            ).fetchone()
            result = {
                "source": "canonical_task",
                "availability": "available",
                **task,
                **{
                    key: row[key]
                    for key in ("status", "version", "updated_at", "review_kind", "proposal_status")
                },
                **_fields(row, TEXT_FIELDS),
                "events": [],
                "evidence": [],
            }
            events = conn.execute(
                "SELECT id,kind,created_at,"
                + _bounded(EVENT_FIELDS)
                + " FROM task_events WHERE task_id=? ORDER BY id DESC LIMIT ?",
                (task["number"], EVENT_CANDIDATES + 1),
            ).fetchall()
            selected = [r for r in events[:EVENT_CANDIDATES] if r["kind"] in EVENT_KINDS]
            result["events_omitted"] = len(events) > EVENT_CANDIDATES or len(selected) > EVENTS
            result["events"] = [
                {
                    "id": r["id"],
                    "kind": r["kind"],
                    "at": r["created_at"],
                    **_fields(r, EVENT_FIELDS),
                }
                for r in reversed(selected[:EVENTS])
            ]
            # Bound candidates as well as output. Hidden memory metadata is
            # filtered with the canonical link_visible predicate before display.
            links = conn.execute(
                "SELECT invalidated,theme,"
                + _bounded(("drawer_id", "title"))
                + " FROM task_links WHERE task_id=? ORDER BY drawer_id DESC LIMIT ?",
                (task["number"], EVIDENCE),
            ).fetchall()
            # This notice is constant, including when all candidates are hidden;
            # neither counts nor omission flags reveal hidden memory links.
            result["evidence_bounded"] = True
            result["evidence"] = [
                {"invalidated": bool(r["invalidated"]), **_fields(r, ("drawer_id", "title"))}
                for r in reversed(links[:EVIDENCE])
                if link_visible(r["theme"], viewer)
            ]
        result["runner"] = _runner(authority, job, task, viewer)
        with _read(path) as conn:
            current, _ = _authority(conn, authority, job, task, user_id)
            if current != binding:
                raise StoreError("context_changed", 409)
        result["revision"] = hashlib.sha256(
            json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        return result
    except (OSError, sqlite3.Error, ControlError, ToolError, ValueError, TypeError, KeyError):
        raise StoreError("progress_unavailable", 404) from None
