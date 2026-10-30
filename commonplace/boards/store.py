"""Per-workspace SQLite cards, their append-only history, and memory links."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from commonplace.extensions import ToolError

STATUSES = ("backlog", "todo", "doing", "review", "done")
TASK_COLUMNS = {
    "repo": "TEXT",
    "branch": "TEXT",
    "pr_url": "TEXT",
    "pr_number": "INTEGER",
    "checks": "TEXT",
    "status_line": "TEXT",
    "heartbeat_at": "TEXT",
    "labels": "TEXT",
}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def task_number(ref) -> int:
    if isinstance(ref, bool) or not isinstance(ref, (str, int)):
        raise ToolError("no: card not found")
    try:
        number = int(str(ref).removeprefix("#"))
    except ValueError:
        raise ToolError("no: card not found") from None
    if number < 1:
        raise ToolError("no: card not found")
    return number


def task_row(conn, ref):
    row = conn.execute(
        "SELECT t.*, b.slug AS board, b.wing AS board_wing, "
        "b.claim_minutes, b.done_needs_review FROM tasks t "
        "JOIN boards b ON b.id=t.board_id WHERE t.id=?",
        (task_number(ref),),
    ).fetchone()
    if row is None:
        raise ToolError("no: card not found")
    return row


def require_visible(row, principal) -> None:
    if principal is not None and not principal.policy.can_read_wing(row["board_wing"]):
        raise ToolError("no: card not found")


def card(conn, row) -> dict:
    """The public card shape, deliberately independent of SQLite's schema."""
    return {
        "number": row["id"],
        "board": row["board"],
        "title": row["title"],
        "body": row["body"],
        "status": row["status"],
        "assignee": row["assignee"],
        "claim_until": row["claim_until"],
        "ask": row["ask"],
        "ask_options": json.loads(row["ask_options"] or "[]"),
        "ask_to": row["ask_to"],
        "return_to": row["return_to"],
        "flags": json.loads(row["flags"] or "[]"),
        **{key: row[key] for key in TASK_COLUMNS if key != "labels"},
        "labels": json.loads(row["labels"] or "[]"),
        "links": conn.execute(
            "SELECT count(*) FROM task_links WHERE task_id=?", (row["id"],)
        ).fetchone()[0],
        "version": row["version"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "position": row["position"],
    }


def append_event(conn, number, kind, actor, *, trust="trusted", **fields):
    conn.execute(
        "INSERT INTO task_events(task_id,kind,text,from_status,to_status,target,choice,"
        "stamp_user,stamp_connection,stamp_trust,flags,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            number,
            kind,
            fields.get("text"),
            fields.get("from_status"),
            fields.get("to_status"),
            fields.get("target"),
            fields.get("choice"),
            fields.get("user_id", actor),
            actor.removeprefix("conn:") if actor.startswith("conn:") else None,
            trust,
            json.dumps(fields.get("flags", [])),
            now(),
        ),
    )


def expire_claims(conn, board_id):
    expired = conn.execute(
        "SELECT id,assignee FROM tasks WHERE board_id=? AND status='doing' AND claim_until<=?",
        (board_id, now()),
    ).fetchall()
    for row in expired:
        conn.execute(
            "UPDATE tasks SET status='todo',assignee=NULL,claim_until=NULL,"
            "version=version+1,updated_at=?,updated_by='system' WHERE id=?",
            (now(), row["id"]),
        )
        append_event(
            conn,
            row["id"],
            "expire",
            "system",
            target=row["assignee"],
            from_status="doing",
            to_status="todo",
        )
    return expired


class BoardStore:
    """No shared connection: separate processes compete through SQLite's lock.

    The optional principal on memory-link methods is for policy-aware callers.
    Calls without it are the contract's trusted, local workspace API.
    """

    def __init__(self, workspace_dir):
        directory = Path(workspace_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = str(directory / "boards.sqlite3")
        with self.connection() as conn:
            conn.executescript(Path(__file__).with_name("schema.sql").read_text())
            conn.execute("BEGIN IMMEDIATE")
            try:
                columns = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
                for name, datatype in {**TASK_COLUMNS, "created_by": "TEXT"}.items():
                    if name not in columns:
                        conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {datatype}")
                conn.execute(
                    "UPDATE tasks SET created_by=CASE WHEN stamp_connection IS NOT NULL "
                    "THEN 'conn:' || stamp_connection ELSE 'user:' || stamp_user END "
                    "WHERE created_by IS NULL"
                )
                link_columns = {r[1] for r in conn.execute("PRAGMA table_info(task_links)")}
                if "title" not in link_columns:
                    conn.execute("ALTER TABLE task_links ADD COLUMN title TEXT NOT NULL DEFAULT ''")
                if "invalidated" not in link_columns:
                    conn.execute(
                        "ALTER TABLE task_links ADD COLUMN invalidated INTEGER NOT NULL DEFAULT 0"
                    )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @property
    def changes(self) -> int:
        with self.connection() as conn:
            return conn.execute("SELECT value FROM meta WHERE key='changes'").fetchone()[0]

    def board_for_task(self, task_ref, *, principal=None) -> dict:
        with self.connection() as conn:
            row = task_row(conn, task_ref)
            require_visible(row, principal)
            return dict(
                conn.execute("SELECT * FROM boards WHERE id=?", (row["board_id"],)).fetchone()
            )

    def link_drawer(self, task_ref, drawer_id: str, actor: str, title: str = "", *, principal=None):
        if not isinstance(drawer_id, str) or not drawer_id:
            raise ToolError("no: memory id is required")
        with self.transaction() as conn:
            row = task_row(conn, task_ref)
            require_visible(row, principal)
            if principal is not None and not principal.policy.can_write_wing(row["board_wing"]):
                raise ToolError("no: this connection cannot change this board")
            expire_claims(conn, row["board_id"])
            row = task_row(conn, row["id"])
            if principal is not None:
                # Linking only adds; it never needs the card's hold.
                actor = principal.actor
            user = principal.user_id if principal is not None else actor
            connection = actor.removeprefix("conn:") if actor.startswith("conn:") else None
            added = conn.execute(
                "INSERT OR IGNORE INTO task_links(task_id,drawer_id,stamp_user,stamp_connection,"
                "created_at,title) VALUES (?,?,?,?,?,?)",
                (row["id"], drawer_id, user, connection, now(), title),
            ).rowcount
            if added:
                conn.execute(
                    "UPDATE tasks SET version=version+1,updated_at=?,updated_by=? WHERE id=?",
                    (now(), actor, row["id"]),
                )
                if row["status"] == "doing" and row["assignee"] == actor and connection:
                    until = (
                        datetime.now(timezone.utc) + timedelta(minutes=row["claim_minutes"])
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    conn.execute("UPDATE tasks SET claim_until=? WHERE id=?", (until, row["id"]))
                append_event(conn, row["id"], "link", actor, user_id=user, target=drawer_id)

    def task_links(self, task_ref, *, principal=None) -> list[dict]:
        with self.connection() as conn:
            row = task_row(conn, task_ref)
            require_visible(row, principal)
            return self.links(conn, row["id"])

    @staticmethod
    def links(conn, number) -> list[dict]:
        return [
            {
                "drawer_id": r["drawer_id"],
                "title": r["title"],
                "invalidated": bool(r["invalidated"]),
            }
            for r in conn.execute(
                "SELECT drawer_id,title,invalidated FROM task_links WHERE task_id=? ORDER BY rowid",
                (number,),
            )
        ]

    def cards_for_drawer(self, drawer_id, *, principal=None) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT t.id,t.title,t.status,b.slug,b.wing FROM tasks t "
                "JOIN task_links l ON l.task_id=t.id JOIN boards b ON b.id=t.board_id "
                "WHERE l.drawer_id=? ORDER BY t.id",
                (drawer_id,),
            )
            return [
                {
                    "number": r["id"],
                    "title": r["title"],
                    "status": r["status"],
                    "board": r["slug"],
                    "wing": r["wing"],
                }
                for r in rows
                if principal is None or principal.policy.can_read_wing(r["wing"])
            ]
