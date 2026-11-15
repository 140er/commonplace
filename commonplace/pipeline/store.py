"""Pipeline events in the workspace file ``pipeline.sqlite3``: validate, append, read.

One row of ``pipeline_events`` is one thing that happened to one pull request (a step and
its state at a time). The table is append-only and idempotent: an event id already recorded
for the PR is skipped, never changed, so a feeder may repeat a request. A request is
validated whole and written in one transaction, so a refusal stores nothing and a crash
leaves the earlier events untouched. The file sits next to the other workspace stores (it is
encrypted with the workspace and holds one workspace only); a read never creates it.
Callers authorize first; this module only validates, stores and reads.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from commonplace.admission import GATE
from commonplace.topology.model import secret_kinds
from mempalace.sqlite_crypto import connect, row_factory

from .compute import LABELS, MAX_FUTURE, STATES, parse_at, stamp

FILENAME = "pipeline.sqlite3"
MAX_EVENTS = 200
MAX_PR_EVENTS = 1000
MAX_TEXT = 300
OLDEST = datetime(2020, 1, 1, tzinfo=timezone.utc)
_REPO = re.compile(r"[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{0,119}")
_EVENT_KEYS = {"id", "step", "state", "at", "ref", "detail"}
_BODY_KEYS = {"repo", "pr", "title", "events"}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_prs (
    repo TEXT NOT NULL, pr INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (repo, pr)
);
CREATE TABLE IF NOT EXISTS pipeline_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL, pr INTEGER NOT NULL, id TEXT NOT NULL,
    step TEXT NOT NULL, state TEXT NOT NULL, at TEXT NOT NULL,
    ref TEXT, detail TEXT, received_at TEXT NOT NULL,
    UNIQUE (repo, pr, id)
);
CREATE INDEX IF NOT EXISTS pipeline_events_pr ON pipeline_events(repo, pr, at);
"""


class Invalid(ValueError):
    """The request is not an acceptable set of events (a plain 400); nothing was stored."""


def _text(value, field, *, required=False, limit=MAX_TEXT):
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise Invalid(f"{field} must be text")
    value = value.strip()
    if len(value) > limit:
        raise Invalid(f"{field} is longer than {limit} characters")
    if any(ord(ch) < 32 and ch != "\t" for ch in value):
        raise Invalid(f"{field} must be plain text")
    if secret_kinds(value):
        raise Invalid(f"{field} looks like it holds a secret; send no secrets")
    return value


def validate(body, now: datetime) -> dict:
    """The body as ``{repo, pr, title, events}`` with checked fields, or ``Invalid``."""
    if not isinstance(body, dict) or body.keys() - _BODY_KEYS:
        raise Invalid("the body holds repo, pr, title and events only")
    repo = body.get("repo")
    if not isinstance(repo, str) or not _REPO.fullmatch(repo):
        raise Invalid("repo must look like owner/name")
    pr = body.get("pr")
    if isinstance(pr, bool) or not isinstance(pr, int) or not 1 <= pr <= 10**9:
        raise Invalid("pr must be a whole number from 1")
    title = _text(body.get("title"), "title")
    events = body.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENTS:
        raise Invalid(f"events must be a list of 1 to {MAX_EVENTS} events")
    out = []
    for position, event in enumerate(events, 1):
        where = f"event {position}"
        if not isinstance(event, dict) or event.keys() - _EVENT_KEYS:
            raise Invalid(f"{where} holds id, step, state, at, ref and detail only")
        ident = event.get("id")
        if not isinstance(ident, str) or not _ID.fullmatch(ident):
            raise Invalid(f"{where}: id must be 1 to 120 plain characters")
        if event.get("step") not in LABELS:
            raise Invalid(f"{where}: unknown step; use one of {', '.join(LABELS)}")
        if event.get("state") not in STATES:
            raise Invalid(f"{where}: unknown state; use one of {', '.join(STATES)}")
        try:
            at = parse_at(event.get("at"))
        except ValueError as exc:
            raise Invalid(f"{where}: {exc}") from None
        if at > now + MAX_FUTURE:
            raise Invalid(f"{where}: the time is more than 5 minutes in the future")
        if at < OLDEST:
            raise Invalid(f"{where}: the time is before 2020")
        out.append(
            {
                "id": ident,
                "step": event["step"],
                "state": event["state"],
                "at": stamp(at),
                "ref": _text(event.get("ref"), f"{where}: ref") or None,
                "detail": _text(event.get("detail"), f"{where}: detail") or None,
            }
        )
    return {"repo": repo, "pr": pr, "title": title, "events": out}


def open_pipeline(gateway, workspace_id: str) -> "PipelineStore":
    """The gateway's one store for a workspace; it follows the workspace's directory."""
    stores = gateway.__dict__.setdefault("_pipeline_stores", {})
    if workspace_id not in stores:
        supervisor = gateway.supervisor
        stores[workspace_id] = PipelineStore(lambda: supervisor.workspace_dir(workspace_id))
    return stores[workspace_id]


class PipelineStore:
    """One workspace's pipeline file. ``directory`` is a path or a callable returning it."""

    def __init__(self, directory):
        self._directory = directory if callable(directory) else lambda: directory
        self._lock = threading.RLock()
        self._conn = None
        self._path = None

    @property
    def path(self) -> Path:
        return Path(self._directory()).resolve() / FILENAME

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
            self._conn = self._path = None

    def _connection(self, create):
        path = self.path
        if path != self._path and self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._conn is None:
            if not create and not path.exists():
                return None
            with GATE.creating(path):
                path.parent.mkdir(parents=True, exist_ok=True)
                conn = connect(str(path), timeout=5, check_same_thread=False, isolation_level=None)
            try:
                conn.row_factory = row_factory
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.executescript(_SCHEMA)
            except Exception:
                conn.close()
                raise
            self._conn, self._path = conn, path
        return self._conn

    def record(self, request: dict, now: datetime) -> dict:
        """Append a validated request. Returns ``{accepted, duplicates}``; one transaction."""
        repo, pr = request["repo"], request["pr"]
        received = stamp(now)
        with self._lock:
            conn = self._connection(create=True)
            conn.execute("BEGIN IMMEDIATE")
            try:
                known = conn.execute(
                    "SELECT count(*) AS n FROM pipeline_events WHERE repo=? AND pr=?", (repo, pr)
                ).fetchone()["n"]
                accepted = 0
                for event in request["events"]:
                    cursor = conn.execute(
                        "INSERT OR IGNORE INTO pipeline_events"
                        "(repo, pr, id, step, state, at, ref, detail, received_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            repo,
                            pr,
                            event["id"],
                            event["step"],
                            event["state"],
                            event["at"],
                            event["ref"],
                            event["detail"],
                            received,
                        ),
                    )
                    accepted += cursor.rowcount
                if known + accepted > MAX_PR_EVENTS:
                    raise Invalid(f"a PR holds at most {MAX_PR_EVENTS} events")
                if accepted:  # a request of known ids changes nothing, not even the title
                    conn.execute(
                        "INSERT INTO pipeline_prs(repo, pr, title, created_at, updated_at) "
                        "VALUES (?,?,?,?,?) ON CONFLICT(repo, pr) DO UPDATE SET "
                        "title=CASE WHEN excluded.title != '' THEN excluded.title ELSE title END, "
                        "updated_at=excluded.updated_at",
                        (repo, pr, request["title"], received, received),
                    )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return {"accepted": accepted, "duplicates": len(request["events"]) - accepted}

    def prs(self, limit: int) -> list[dict]:
        """Up to ``limit`` PRs, the most recent activity first, each with its events."""
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT p.repo, p.pr, p.title, "
                "(SELECT max(e.at) FROM pipeline_events e WHERE e.repo=p.repo AND e.pr=p.pr) AS last "
                "FROM pipeline_prs p ORDER BY last DESC, p.created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._with_events(conn, dict(row)) for row in rows]

    def get(self, repo: str, pr: int) -> dict | None:
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return None
            row = conn.execute(
                "SELECT repo, pr, title FROM pipeline_prs WHERE repo=? AND pr=?", (repo, pr)
            ).fetchone()
            return self._with_events(conn, dict(row)) if row else None

    @staticmethod
    def _with_events(conn, row: dict) -> dict:
        row["events"] = [
            dict(e)
            for e in conn.execute(
                "SELECT seq, id, step, state, at, ref, detail FROM pipeline_events "
                "WHERE repo=? AND pr=? ORDER BY at, seq",
                (row["repo"], row["pr"]),
            )
        ]
        return row
