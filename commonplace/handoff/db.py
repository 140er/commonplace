"""Handoff tables in the control database.

Uses the ``ControlDB``'s connection, lock and ``_tx()``; tables are created
on construction (``CREATE TABLE IF NOT EXISTS``), so old control DBs upgrade
in place. Full thread text stays in ``thread_store``; the control database
holds settings, pointers and the short handoff document.

Tables: ``handoff_settings`` (unique workspace_id+scope+ref),
``handoffs`` (unique workspace_id+idempotency_key; ``launch_claimed`` flag),
``handoff_webhooks``, ``handoff_outbox``.

State graph (see ``models.STATES``)::

    pending -> stored -> launched -> done
    pending -> failed -> pending (begin retries a failed store)
    stored/launched -> launch_failed -> stored (explicit release_launch)

Successor registration from ``stored`` performs ``stored -> launched -> done``
in one transaction, so callers can register an immediately started successor.
Repeating an identical outcome is a no-op. ``done`` is terminal. A launch
claim is available once in ``stored`` and only an explicit release after
launch failure makes another claim available. Authorization and audit
entries belong to callers; this module never writes audit entries.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from typing import Optional

from commonplace.control_db import ControlDB, _iso, _new_id, _now

from .models import SETTING_SCOPES, Handoff, LauncherConfig, SettingRow, SuccessorRef, ThreadMeta

_SCHEMA = """
CREATE TABLE IF NOT EXISTS handoff_settings (
    workspace_id TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('workspace', 'agent', 'task')),
    ref TEXT NOT NULL CHECK (
        (scope = 'workspace' AND ref = '') OR (scope != 'workspace' AND ref != '')
    ),
    enabled INTEGER CHECK (enabled IN (0, 1)),
    threshold REAL,
    context_window INTEGER,
    launcher TEXT,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, scope, ref)
);
CREATE TABLE IF NOT EXISTS handoffs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    source_thread_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('pending', 'stored', 'launched', 'done', 'failed', 'launch_failed')
    ),
    agent TEXT NOT NULL DEFAULT '',
    task_ref TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL DEFAULT '',
    pr_url TEXT NOT NULL DEFAULT '',
    cwd TEXT NOT NULL DEFAULT '',
    stored_thread_id TEXT NOT NULL DEFAULT '',
    parent_handoff_id TEXT NOT NULL DEFAULT '',
    doc_markdown TEXT NOT NULL DEFAULT '',
    successor_agent_ref TEXT NOT NULL DEFAULT '',
    successor_url TEXT NOT NULL DEFAULT '',
    successor_thread_id TEXT NOT NULL DEFAULT '',
    launch_claimed INTEGER NOT NULL DEFAULT 0 CHECK (launch_claimed IN (0, 1)),
    error TEXT NOT NULL DEFAULT '',
    connection_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_handoffs_task ON handoffs(workspace_id, task_ref);
CREATE INDEX IF NOT EXISTS idx_handoffs_stored ON handoffs(stored_thread_id);
CREATE INDEX IF NOT EXISTS idx_handoffs_successor ON handoffs(workspace_id, successor_thread_id);
CREATE TABLE IF NOT EXISTS handoff_webhooks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    url TEXT NOT NULL,
    secret TEXT NOT NULL,
    events TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS handoff_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    webhook_id TEXT NOT NULL REFERENCES handoff_webhooks(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    event TEXT NOT NULL,
    payload TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_at TEXT NOT NULL,
    delivered_at TEXT,
    gave_up_at TEXT,
    last_error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_handoff_outbox_due
    ON handoff_outbox(next_at, id) WHERE delivered_at IS NULL AND gave_up_at IS NULL;
"""


def _setting(row: sqlite3.Row) -> SettingRow:
    values = dict(row)
    if values["enabled"] is not None:
        values["enabled"] = bool(values["enabled"])
    if values["launcher"] is not None:
        launcher = json.loads(values["launcher"])
        launcher["argv"] = tuple(launcher.get("argv", ()))
        values["launcher"] = LauncherConfig(**launcher)
    return SettingRow(**values)


def _handoff(row: sqlite3.Row) -> Handoff:
    values = dict(row)
    agent_ref = values.pop("successor_agent_ref")
    url = values.pop("successor_url")
    thread_id = values.pop("successor_thread_id")
    values.pop("launch_claimed")
    values["successor"] = SuccessorRef(agent_ref, url, thread_id) if agent_ref else None
    return Handoff(**values)


def _setting_ref(scope: str, ref: str) -> None:
    if scope not in SETTING_SCOPES:
        raise HandoffDBError("unknown handoff setting scope")
    if (scope == "workspace" and ref != "") or (scope != "workspace" and not ref):
        raise HandoffDBError("invalid handoff setting ref")


class HandoffDBError(ValueError):
    pass


class HandoffDB:
    def __init__(self, control_db: ControlDB):
        self._control = control_db
        self._conn = control_db._conn
        self._lock = control_db._lock
        with self._lock:
            self._conn.executescript(_SCHEMA)
            with self._control._tx() as conn:
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(handoffs)")}
                if "cwd" not in columns:
                    conn.execute("ALTER TABLE handoffs ADD COLUMN cwd TEXT NOT NULL DEFAULT ''")

    def _required(self, handoff_id: str) -> sqlite3.Row:
        """Called with the control lock held, inside a write transaction."""
        row = self._conn.execute("SELECT * FROM handoffs WHERE id = ?", (handoff_id,)).fetchone()
        if row is None:
            raise HandoffDBError("handoff not found")
        return row

    def _transition(
        self, handoff_id: str, state: str, allowed: tuple, *, repeat: bool = True, **values
    ) -> Handoff:
        with self._lock, self._control._tx() as conn:
            row = self._required(handoff_id)
            if (
                repeat
                and row["state"] == state
                and all(row[key] == value for key, value in values.items())
            ):
                return _handoff(row)
            if row["state"] not in allowed:
                raise HandoffDBError(f"cannot change handoff from {row['state']} to {state}")
            values.update(state=state, updated_at=_iso(_now()))
            columns = ", ".join(f"{key} = ?" for key in values)
            conn.execute(
                f"UPDATE handoffs SET {columns} WHERE id = ?", (*values.values(), handoff_id)
            )
            return _handoff(self._required(handoff_id))

    # ── settings ──
    def setting_rows(
        self, workspace_id: str, agent: str = "", task_ref: str = ""
    ) -> list[SettingRow]:
        """The workspace row plus the agent and task rows that exist, in that order."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM handoff_settings WHERE workspace_id = ? AND "
                "((scope = 'workspace' AND ref = '') OR "
                "(scope = 'agent' AND ref = ? AND ref != '') OR "
                "(scope = 'task' AND ref = ? AND ref != '')) "
                "ORDER BY CASE scope WHEN 'workspace' THEN 0 WHEN 'agent' THEN 1 ELSE 2 END",
                (workspace_id, agent, task_ref),
            ).fetchall()
        return [_setting(row) for row in rows]

    def list_settings(self, workspace_id: str) -> list[SettingRow]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM handoff_settings WHERE workspace_id = ? "
                "ORDER BY CASE scope WHEN 'workspace' THEN 0 WHEN 'agent' THEN 1 ELSE 2 END, ref",
                (workspace_id,),
            ).fetchall()
        return [_setting(row) for row in rows]

    def put_setting(self, row: SettingRow, *, actor: str) -> SettingRow:
        """Insert or replace one row; ``updated_by``/``updated_at`` stamped here."""
        _setting_ref(row.scope, row.ref)
        launcher = json.dumps(asdict(row.launcher)) if row.launcher is not None else None
        with self._lock, self._control._tx() as conn:
            try:
                conn.execute(
                    "INSERT INTO handoff_settings(workspace_id, scope, ref, enabled, threshold, "
                    "context_window, launcher, updated_by, updated_at) VALUES (?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(workspace_id, scope, ref) DO UPDATE SET enabled = excluded.enabled, "
                    "threshold = excluded.threshold, context_window = excluded.context_window, "
                    "launcher = excluded.launcher, updated_by = excluded.updated_by, "
                    "updated_at = excluded.updated_at",
                    (
                        row.workspace_id,
                        row.scope,
                        row.ref,
                        row.enabled,
                        row.threshold,
                        row.context_window,
                        launcher,
                        actor,
                        _iso(_now()),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise HandoffDBError("invalid handoff setting") from error
            saved = conn.execute(
                "SELECT * FROM handoff_settings WHERE workspace_id = ? AND scope = ? AND ref = ?",
                (row.workspace_id, row.scope, row.ref),
            ).fetchone()
            return _setting(saved)

    def delete_setting(self, workspace_id: str, scope: str, ref: str, *, actor: str) -> bool:
        _setting_ref(scope, ref)
        with self._lock:
            return (
                self._conn.execute(
                    "DELETE FROM handoff_settings WHERE workspace_id = ? AND scope = ? AND ref = ?",
                    (workspace_id, scope, ref),
                ).rowcount
                > 0
            )

    # ── handoffs ──
    def begin(
        self, workspace_id: str, idempotency_key: str, meta: ThreadMeta, *, connection_id: str
    ) -> tuple[Handoff, bool]:
        """Create the ``pending`` row, or return the existing one. ``(handoff, created)``.
        Two concurrent calls with one key: exactly one gets ``created=True``.
        A row in state ``failed`` is reused (reset to pending, created=True) so
        a retry after a store failure can try again."""
        with self._lock, self._control._tx() as conn:
            row = conn.execute(
                "SELECT * FROM handoffs WHERE workspace_id = ? AND idempotency_key = ?",
                (workspace_id, idempotency_key),
            ).fetchone()
            if row is not None:
                if row["state"] != "failed":
                    return _handoff(row), False
                conn.execute(
                    "UPDATE handoffs SET state = 'pending', error = '', updated_at = ? WHERE id = ?",
                    (_iso(_now()), row["id"]),
                )
                return _handoff(self._required(row["id"])), True
            handoff_id = _new_id("hof")
            now = _iso(_now())
            conn.execute(
                "INSERT INTO handoffs(id, workspace_id, idempotency_key, source_thread_id, state, "
                "agent, task_ref, branch, pr_url, cwd, parent_handoff_id, connection_id, created_at, "
                "updated_at) VALUES (?,?,?,?,'pending',?,?,?,?,?,?,?,?,?)",
                (
                    handoff_id,
                    workspace_id,
                    idempotency_key,
                    meta.thread_id,
                    meta.agent,
                    meta.task_ref,
                    meta.branch,
                    meta.pr_url,
                    meta.cwd,
                    meta.parent_handoff_id,
                    connection_id,
                    now,
                    now,
                ),
            )
            return _handoff(self._required(handoff_id)), True

    def mark_stored(self, handoff_id: str, stored_thread_id: str, doc_markdown: str) -> Handoff:
        if not stored_thread_id:
            raise HandoffDBError("stored thread not found")
        return self._transition(
            handoff_id,
            "stored",
            ("pending",),
            stored_thread_id=stored_thread_id,
            doc_markdown=doc_markdown,
        )

    def mark_failed(self, handoff_id: str, error: str) -> Handoff:
        return self._transition(handoff_id, "failed", ("pending",), error=error)

    def claim_launch(self, handoff_id: str) -> bool:
        """True once per launch attempt; only release_launch permits a retry."""
        with self._lock:
            return (
                self._conn.execute(
                    "UPDATE handoffs SET launch_claimed = 1, updated_at = ? "
                    "WHERE id = ? AND launch_claimed = 0 AND state IN ('stored')",
                    (_iso(_now()), handoff_id),
                ).rowcount
                > 0
            )

    def mark_launched(self, handoff_id: str) -> Handoff:
        return self._transition(handoff_id, "launched", ("stored",))

    def mark_launch_failed(self, handoff_id: str, error: str) -> Handoff:
        return self._transition(handoff_id, "launch_failed", ("stored", "launched"), error=error)

    def release_launch(self, handoff_id: str, *, actor: str) -> Handoff:
        """For ``relaunch``: only from ``launch_failed``; clears the claim."""
        return self._transition(
            handoff_id, "stored", ("launch_failed",), repeat=False, launch_claimed=0, error=""
        )

    def set_successor(self, handoff_id: str, ref: SuccessorRef) -> Handoff:
        """Record the successor, state ``done``. Same ref again: no-op.
        A different ref when one is set: ``HandoffDBError``."""
        if not ref.agent_ref:
            raise HandoffDBError("successor not found")
        with self._lock, self._control._tx() as conn:
            row = self._required(handoff_id)
            if row["successor_agent_ref"]:
                if row["successor_agent_ref"] != ref.agent_ref:
                    raise HandoffDBError("handoff already has a different successor")
                return _handoff(row)
            if row["state"] not in ("stored", "launched"):
                raise HandoffDBError(f"cannot register a successor for {row['state']} handoff")
            if row["state"] == "stored":
                conn.execute("UPDATE handoffs SET state = 'launched' WHERE id = ?", (handoff_id,))
            conn.execute(
                "UPDATE handoffs SET successor_agent_ref = ?, successor_url = ?, "
                "successor_thread_id = ?, state = 'done', launch_claimed = 1, updated_at = ? "
                "WHERE id = ?",
                (ref.agent_ref, ref.url, ref.thread_id, _iso(_now()), handoff_id),
            )
            return _handoff(self._required(handoff_id))

    def get(self, handoff_id: str) -> Optional[Handoff]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM handoffs WHERE id = ?", (handoff_id,)
            ).fetchone()
        return _handoff(row) if row is not None else None

    def by_stored_thread(self, stored_thread_id: str) -> Optional[Handoff]:
        if not stored_thread_id:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM handoffs WHERE stored_thread_id = ? ORDER BY rowid LIMIT 1",
                (stored_thread_id,),
            ).fetchone()
        return _handoff(row) if row is not None else None

    def by_successor_thread(self, workspace_id: str, thread_id: str) -> Optional[Handoff]:
        if not thread_id:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM handoffs WHERE workspace_id = ? AND successor_thread_id = ? "
                "ORDER BY rowid LIMIT 1",
                (workspace_id, thread_id),
            ).fetchone()
        return _handoff(row) if row is not None else None

    def list(self, workspace_id: str, *, task_ref: str = "", limit: int = 50) -> list[Handoff]:
        sql = "SELECT * FROM handoffs WHERE workspace_id = ?"
        params: list = [workspace_id]
        if task_ref:
            sql += " AND task_ref = ?"
            params.append(task_ref)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(max(0, int(limit)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_handoff(row) for row in rows]

    # ── webhooks ──
    def add_webhook(
        self, workspace_id: str, url: str, secret: str, events: tuple, *, actor: str
    ) -> str:
        webhook_id = _new_id("whk")
        with self._lock:
            self._conn.execute(
                "INSERT INTO handoff_webhooks(id, workspace_id, url, secret, events, created_by, "
                "created_at) VALUES (?,?,?,?,?,?,?)",
                (webhook_id, workspace_id, url, secret, json.dumps(events), actor, _iso(_now())),
            )
        return webhook_id

    def webhooks(self, workspace_id: str) -> list[dict]:
        """``{id, url, events, created_at}``; never the secret."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, url, events, created_at FROM handoff_webhooks "
                "WHERE workspace_id = ? ORDER BY created_at, rowid",
                (workspace_id,),
            ).fetchall()
        return [dict(row, events=json.loads(row["events"])) for row in rows]

    def webhook_secret(self, webhook_id: str) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT secret FROM handoff_webhooks WHERE id = ?", (webhook_id,)
            ).fetchone()
        if row is None:
            raise HandoffDBError("webhook not found")
        return row["secret"]

    def remove_webhook(self, webhook_id: str, *, actor: str) -> bool:
        with self._lock:
            return (
                self._conn.execute(
                    "DELETE FROM handoff_webhooks WHERE id = ?", (webhook_id,)
                ).rowcount
                > 0
            )

    # ── outbox ──
    def enqueue(self, workspace_id: str, event: str, payload: dict) -> list[int]:
        """One outbox row per matching webhook. Payload: ids only."""
        body = json.dumps(payload, ensure_ascii=False)
        now = _iso(_now())
        with self._lock, self._control._tx() as conn:
            webhooks = conn.execute(
                "SELECT id, events FROM handoff_webhooks WHERE workspace_id = ? ORDER BY rowid",
                (workspace_id,),
            ).fetchall()
            ids = []
            for webhook in webhooks:
                events = json.loads(webhook["events"])
                if event in events or "*" in events:
                    saved = conn.execute(
                        "INSERT INTO handoff_outbox(webhook_id, workspace_id, event, payload, "
                        "next_at) VALUES (?,?,?,?,?)",
                        (webhook["id"], workspace_id, event, body, now),
                    )
                    ids.append(saved.lastrowid)
            return ids

    def due(self, now_iso: str, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT o.*, w.url FROM handoff_outbox o "
                "JOIN handoff_webhooks w ON w.id = o.webhook_id AND w.workspace_id = o.workspace_id "
                "WHERE o.delivered_at IS NULL AND o.gave_up_at IS NULL AND o.next_at <= ? "
                "ORDER BY o.id LIMIT ?",
                (now_iso, max(0, int(limit))),
            ).fetchall()
        return [dict(row, payload=json.loads(row["payload"])) for row in rows]

    def mark_delivered(self, outbox_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE handoff_outbox SET delivered_at = ?, attempts = attempts + 1, last_error = '' "
                "WHERE id = ? AND delivered_at IS NULL AND gave_up_at IS NULL",
                (_iso(_now()), outbox_id),
            )

    def mark_retry(
        self, outbox_id: int, error: str, next_at_iso: str, *, give_up: bool = False
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE handoff_outbox SET attempts = attempts + 1, last_error = ?, next_at = ?, "
                "gave_up_at = ? WHERE id = ? AND delivered_at IS NULL AND gave_up_at IS NULL",
                (error, next_at_iso, _iso(_now()) if give_up else None, outbox_id),
            )
