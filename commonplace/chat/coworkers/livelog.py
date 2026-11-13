"""Read-only coworker output pages; no file tails, subscriptions or new storage.

Like the console stream, the stream route returns immediately with a cursor.
The exact chat origin binds every source query. Authority is checked before and
after source reads; a caller-supplied job id never selects diagnostic content.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import threading
import unicodedata
from collections import deque
from html import unescape
from pathlib import Path

from commonplace import ratelimit, redact, sessions
from commonplace.boards.service import BoardService
from commonplace.chat.phone_list import _Plain
from commonplace.control_db import ControlError
from commonplace.extensions import ToolContext, ToolError
from commonplace.http_util import send_json

from . import steps, task_progress
from .store import StoreError

MAX_LINES = 200
LINE_CHARS = 500
MAX_READERS = 8
READ_LIMIT = ratelimit.Limit("coworker-live", 120, 60)
_ESCAPED = re.compile(r"\\(?:u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2})|(?:033|e)(?=[\[\]]))")
_NUMERIC = re.compile(r"&#(x[0-9a-fA-F]{1,6}|[0-9]{1,7});?")
_ANSI = re.compile(
    r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]|"
    r"(?:\x1b\]|\x9d)[^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c|$)|"
    r"(?:\x1b[P^_X]|[\x90\x98\x9e\x9f])[^\x1b\x9c]*(?:\x1b\\|\x9c|$)|"
    r"\x1b[ -/]*[@-Z\\-_]"
)
# Diagnostics are prose: all absolute, home-relative and drive/UNC paths are private.
_PATH = re.compile(r"""(?:[A-Za-z]:[\\/]|~[\\/]|\\\\|/)[^\s<>"'`]*""")
_MARKER = re.compile(r"\[(?:redacted|omitted):[a-z_]+\]")


def _clip(text, limit):
    for match in _MARKER.finditer(text):
        if match.start() < limit < match.end():
            limit = match.start()
            break
    return text[:limit]


def clean(text, *, limit=LINE_CHARS):
    """Normalize -> shared feed redactor -> path masking -> marker-safe cap."""
    if not isinstance(text, str) or len(text) > redact.MAX_CHARS:
        return "[omitted:too_long]"
    for _ in range(4):
        previous = text
        text = _ESCAPED.sub(lambda m: chr(int(m[1] or m[2], 16)) if m[1] or m[2] else "\x1b", text)
        # HTML's decoder replaces numeric C0 references rather than stripping
        # them. Decode these first so they cannot hide a joined credential.
        text = _NUMERIC.sub(_numeric_entity, text)
        plain = _Plain()
        try:
            plain.feed(text)
            plain.close()
        except (ValueError, AssertionError):
            return "[omitted:too_long]"
        text = _ANSI.sub("", unescape("".join(plain.parts)))
        text = "".join(
            c for c in text if c == "\n" or unicodedata.category(c) not in {"Cc", "Cf", "Cs"}
        )
        if text == previous:
            break
    else:
        return "[omitted:too_long]"
    text = redact.redact(text)[0]
    text = _PATH.sub("[redacted:path]", text)
    return _clip(text, limit)


def _numeric_entity(match):
    value = match[1]
    point = int(value[1:], 16) if value.startswith("x") else int(value)
    return chr(point) if point <= 0x10FFFF else ""


class Reads:
    def __init__(self):
        self.slots = threading.BoundedSemaphore(MAX_READERS)
        self.limiter = ratelimit.TokenBuckets(max_entries=1024)


def _reads(gateway):
    with gateway.db._lock:
        if not hasattr(gateway, "_coworker_live_reads"):
            gateway._coworker_live_reads = Reads()
        return gateway._coworker_live_reads


def _board(authority, viewer, board):
    if board is None:
        return
    service = BoardService(ToolContext(authority.user(viewer), authority.gateway), store=False)
    path = Path(authority.gateway.workspace_dir(authority.workspace_id)) / "boards.sqlite3"
    try:
        with task_progress._read(path) as conn:
            service._board(conn, board)
    except (OSError, sqlite3.Error, ControlError, ToolError, ValueError):
        raise StoreError("not_found", 404) from None


def _run(authority, job, projection):
    path = Path(authority.gateway.workspace_dir(authority.workspace_id)) / "runner.sqlite3"
    if not path.is_file():
        return None
    origin = f"coworker:{job['conversation_id']}:{job['id']}"
    with task_progress._read(path) as conn:
        row = conn.execute(
            "SELECT j.id,j.state,j.updated_at,"
            "CASE WHEN j.launch_output IS NULL THEN '' "
            "WHEN length(CAST(j.launch_output AS BLOB))<=16000 THEN j.launch_output "
            "ELSE '[omitted:too_long]' END AS output,"
            "l.agent,l.status,l.ended_at,l.updated_at AS ledger_at "
            "FROM runner_jobs j LEFT JOIN run_ledger l ON l.job_id=j.id "
            "AND l.task_number=j.task_number AND l.board_slug=j.board_slug "
            "AND l.requested_by=j.requested_by "
            "WHERE j.task_number=? AND j.board_slug=? AND j.requested_by=? AND j.request_key=? "
            "LIMIT 1",
            (
                projection["number"],
                projection["board"],
                "user:" + job["user_id"],
                "chat:" + hashlib.sha256(origin.encode()).hexdigest(),
            ),
        ).fetchone()
    if row is None:
        return None
    display = "Run"
    if row["agent"]:
        agent = authority.gateway.db.authenticate_connection(row["agent"])
        if agent is None or agent.workspace_id != authority.workspace_id:
            return None
        display = agent.connection
    output = row["output"] or ""
    if len(output) > 4000:
        output = "[omitted:too_long]"
    return {
        "display_name": display,
        "state": row["status"] if row["ended_at"] else row["state"],
        "at": row["ledger_at"] if row["ended_at"] else row["updated_at"],
        "output": output,
    }


def snapshot(store, principal, conversation_id, session_hash):
    """Bounded source observations, under the existing conversation/control fence."""
    authority = store._authority
    observed, lines = [], deque(maxlen=MAX_LINES)
    with store._transaction() as conn:
        conversation = store._conversation(conn, principal.user_id, conversation_id)
        _board(authority, principal.user_id, conversation["board"])
        bots = conn.execute(
            "SELECT b.* FROM bots b JOIN conversation_bots cb ON b.id=cb.bot_id "
            "WHERE cb.conversation_id=? ORDER BY b.id",
            (conversation_id,),
        ).fetchall()
        bots = [dict(bot) for bot in bots]
        for bot in bots:
            store._bot_access(bot)
        rows = conn.execute(
            "SELECT * FROM jobs WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
            (conversation_id, steps.MAX_JOBS),
        ).fetchall()
        for job in reversed(rows):
            try:
                authority.job({"user_id": principal.user_id, "session_hash": session_hash})
                store._runtime_access(conn, job)
                store._check_visibility(conn, job)
                if job["progress_binding"] != store._progress_binding(conn, job):
                    continue
                run, projection = None, None
                if store._progress_candidate(conn, job):
                    projected = store._progress_job(
                        conn, job, principal.user_id, session_hash=session_hash
                    )
                    projection = projected.get("task_progress", {})
                    if projection.get("availability") != "available":
                        continue
                    run = _run(authority, job, projection)
                    task_progress.current(authority, job, projection, principal.user_id)
                store._runtime_access(conn, job)
                store._check_visibility(conn, job)
                if job["progress_binding"] != store._progress_binding(conn, job):
                    continue
                entry = {
                    "id": job["id"],
                    "bot_id": job["bot_id"],
                    "step_state": projection["status"] if projection else job["state"],
                    "at": projection["updated_at"] if projection else job["updated_at"],
                    "summary": projection.get("status_line") if projection else None,
                    "run": run,
                }
                if run:
                    if steps.state(entry["step_state"]) not in {"done", "waiting"}:
                        entry["step_state"] = run["state"]
                    # Redact the entire output before splitting: private keys can span lines.
                    output = clean(run["output"], limit=redact.MAX_CHARS)
                    version = hashlib.sha256(output.encode()).hexdigest()
                    for index, text in enumerate(output.splitlines()):
                        identity = f"{job['id']}:{version}:{index}"
                        lines.append(
                            {
                                "id": hashlib.sha256(identity.encode()).hexdigest()[:24],
                                "at": run["at"],
                                "text": _clip(text, LINE_CHARS),
                            }
                        )
                observed.append(entry)
            except (StoreError, OSError, sqlite3.Error, ControlError, ToolError):
                continue  # An unavailable or newly unreadable source discloses no facts.
        authority.job({"user_id": principal.user_id, "session_hash": session_hash})
        store._conversation(conn, principal.user_id, conversation_id)
        _board(authority, principal.user_id, conversation["board"])
        return {"working_on": steps.tree(bots, observed), "lines": list(lines)}


def _cursor(scope, position):
    raw = json.dumps([1, scope, position], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def page(snapshot, workspace_id, conversation_id, cursor=None, limit=MAX_LINES):
    if type(limit) is not int or not 1 <= limit <= MAX_LINES:
        raise StoreError()
    scope = hashlib.sha256(f"{workspace_id}:{conversation_id}".encode()).hexdigest()[:24]
    position = None
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 160:
                raise ValueError
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            version, bound, position = json.loads(raw)
            if (
                type(version) is not int
                or version != 1
                or bound != scope
                or not isinstance(position, str)
                or re.fullmatch(r"(?:[0-9a-f]{24})?", position) is None
                or _cursor(scope, position) != cursor
            ):
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise StoreError() from None
    lines = snapshot["lines"][-MAX_LINES:]
    ids = [line["id"] for line in lines]
    reset = bool(position and position not in ids)
    forward = position is not None and not reset
    remaining = lines[ids.index(position) + 1 :] if position in ids else lines
    selected = remaining[:limit] if forward else remaining[-limit:]
    last = selected[-1]["id"] if selected else "" if reset else position or ""
    return {
        "lines": selected,
        "cursor": _cursor(scope, last),
        "more": forward and len(remaining) > len(selected),
        "reset": reset,
        "working_on": snapshot["working_on"],
        "poll_after_ms": 1000,
    }


def handle(gateway, h, principal, session, store, conversation_id, params):
    reads = _reads(gateway)
    if reads.limiter.check([(READ_LIMIT, principal.workspace_id + ":" + principal.user_id)]):
        raise StoreError("rate_limited", 429)
    if not reads.slots.acquire(blocking=False):
        raise StoreError("live_log_busy", 429)
    try:
        data = snapshot(store, principal, conversation_id, sessions._hash(session))
        from .routes import _numeric

        return send_json(
            h,
            200,
            page(
                data,
                principal.workspace_id,
                conversation_id,
                params.get("cursor"),
                _numeric(params.get("limit", str(MAX_LINES))),
            ),
        )
    finally:
        reads.slots.release()
