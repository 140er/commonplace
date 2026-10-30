"""Handoff tables in the control database.

Uses the ``ControlDB``'s connection, lock and ``_tx()``; tables are created
on construction (``CREATE TABLE IF NOT EXISTS``), so old control DBs upgrade
in place. Thread text and handoff documents stay in the workspace
``thread_store``; the control database holds settings, pointers and state.
Bind ``document_store_for`` (or call ``set_document_store``) before storing
documents. Hydrated Handoff values contain quotes; their control rows do not.

Tables: ``handoff_settings`` (unique workspace_id+scope+ref),
``handoffs`` (unique workspace_id+idempotency_key; launch claim and lease token).
Events go to the hub's event log (``commonplace.events``), not a table here.

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
from dataclasses import asdict, replace
from datetime import timedelta
from typing import Optional

from commonplace.control_db import ControlDB, _iso, _new_id, _now, _parse

from .models import SETTING_SCOPES, Handoff, LauncherConfig, SettingRow, SuccessorRef, ThreadMeta

PENDING_LEASE_S = 600

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
    lease_token TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    connection_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_handoffs_task ON handoffs(workspace_id, task_ref);
CREATE INDEX IF NOT EXISTS idx_handoffs_stored ON handoffs(stored_thread_id);
CREATE INDEX IF NOT EXISTS idx_handoffs_successor ON handoffs(workspace_id, successor_thread_id);
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
    values.pop("lease_token")
    values["successor"] = SuccessorRef(agent_ref, url, thread_id) if agent_ref else None
    return Handoff(**values)


def _setting_ref(scope: str, ref: str) -> None:
    if scope not in SETTING_SCOPES:
        raise HandoffDBError("unknown handoff setting scope")
    if (scope == "workspace" and ref != "") or (scope != "workspace" and not ref):
        raise HandoffDBError("invalid handoff setting ref")


class HandoffDBError(ValueError):
    pass


class HandoffLeaseError(HandoffDBError):
    """A storage attempt was replaced; it may no longer mutate the handoff."""


class HandoffDB:
    def __init__(self, control_db: ControlDB, *, document_store_for=None):
        self._control = control_db
        self._conn = control_db._conn
        self._lock = control_db._lock
        self._document_store_for = None
        with self._lock:
            self._conn.execute("PRAGMA secure_delete = ON")
            self._conn.executescript(_SCHEMA)
            with self._control._tx() as conn:
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(handoffs)")}
                if "cwd" not in columns:
                    conn.execute("ALTER TABLE handoffs ADD COLUMN cwd TEXT NOT NULL DEFAULT ''")
                if "lease_token" not in columns:
                    conn.execute(
                        "ALTER TABLE handoffs ADD COLUMN lease_token TEXT NOT NULL DEFAULT ''"
                    )
        if document_store_for is not None:
            self.set_document_store(document_store_for)

    def set_document_store(self, store_for) -> None:
        """Keep quotes only in the workspace store; migrate pre-release rows."""
        with self._lock:
            self._document_store_for = store_for
            rows = self._conn.execute("SELECT * FROM handoffs WHERE doc_markdown != ''").fetchall()
            if rows:
                self._conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES ('handoff_docs_scrub_pending', '1')"
                )
            for row in rows:
                with self._control._tx() as conn:
                    store_for(row["workspace_id"]).put_doc(row["id"], row["doc_markdown"])
                    conn.execute("UPDATE handoffs SET doc_markdown = '' WHERE id = ?", (row["id"],))
            if self._conn.execute(
                "SELECT 1 FROM meta WHERE key = 'handoff_docs_scrub_pending'"
            ).fetchone():
                result = self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if result[0]:
                    raise HandoffDBError(
                        "handoff document migration needs exclusive database access"
                    )
                self._conn.execute("DELETE FROM meta WHERE key = 'handoff_docs_scrub_pending'")

    def _handoff(self, row) -> Handoff:
        handoff = _handoff(row)
        if self._document_store_for is not None and handoff.stored_thread_id:
            markdown = self._document_store_for(handoff.workspace_id).doc(handoff.id)
            handoff = replace(handoff, doc_markdown=markdown)
        return handoff

    def _required(self, handoff_id: str) -> sqlite3.Row:
        """Called with the control lock held, inside a write transaction."""
        row = self._conn.execute("SELECT * FROM handoffs WHERE id = ?", (handoff_id,)).fetchone()
        if row is None:
            raise HandoffDBError("handoff not found")
        return row

    def _transition(
        self,
        handoff_id: str,
        state: str,
        allowed: tuple,
        *,
        repeat: bool = True,
        lease_token: Optional[str] = None,
        document: Optional[str] = None,
        **values,
    ) -> Handoff:
        with self._lock, self._control._tx() as conn:
            row = self._required(handoff_id)
            if lease_token is not None and row["lease_token"] != lease_token:
                raise HandoffLeaseError("handoff storage attempt was replaced")
            repeated = (
                repeat
                and row["state"] == state
                and all(row[key] == value for key, value in values.items())
            )
            if not repeated and row["state"] not in allowed:
                raise HandoffDBError(f"cannot change handoff from {row['state']} to {state}")
            if document is not None:
                if self._document_store_for is None:
                    raise HandoffDBError("handoff document store is unavailable")
                store = self._document_store_for(row["workspace_id"])
                if repeated and store.doc(handoff_id) != document:
                    raise HandoffDBError("handoff already has a different document")
                store.put_doc(handoff_id, document)
            if repeated:
                return self._handoff(row)
            values.update(state=state, updated_at=_iso(_now()))
            columns = ", ".join(f"{key} = ?" for key in values)
            conn.execute(
                f"UPDATE handoffs SET {columns} WHERE id = ?", (*values.values(), handoff_id)
            )
            return self._handoff(self._required(handoff_id))

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
        self,
        workspace_id: str,
        idempotency_key: str,
        meta: ThreadMeta,
        *,
        connection_id: str,
        lease_token: str = "",
    ) -> tuple[Handoff, bool]:
        """Create the ``pending`` row, or return the existing one. ``(handoff, created)``.
        Two concurrent calls with one key: exactly one gets ``created=True``.
        A row in state ``failed`` is reused (reset to pending, created=True) so
        a retry after a store failure can try again. A pending writer leases
        its row for ``PENDING_LEASE_S``; an older row can be resumed after a
        crash. The lease refresh and ownership decision share one transaction.
        Services supply a fresh opaque ``lease_token`` and pass it unchanged
        to storage transitions and launch claims. Legacy empty-token rows
        acquire a token on takeover, fencing their original unowned writer.
        ``_now`` is the injectable UTC clock used for lease checks.
        """
        with self._lock, self._control._tx() as conn:
            now = _now()
            row = conn.execute(
                "SELECT * FROM handoffs WHERE workspace_id = ? AND idempotency_key = ?",
                (workspace_id, idempotency_key),
            ).fetchone()
            if row is not None:
                expired = row["state"] == "pending" and (
                    now - _parse(row["updated_at"]) > timedelta(seconds=PENDING_LEASE_S)
                )
                if row["state"] != "failed" and not expired:
                    return self._handoff(row), False
                conn.execute(
                    "UPDATE handoffs SET state = 'pending', error = '', updated_at = ?, "
                    "lease_token = ? WHERE id = ?",
                    (
                        _iso(now),
                        lease_token or (_new_id("lease") if expired or row["lease_token"] else ""),
                        row["id"],
                    ),
                )
                return self._handoff(self._required(row["id"])), True
            handoff_id = _new_id("hof")
            now = _iso(now)
            conn.execute(
                "INSERT INTO handoffs(id, workspace_id, idempotency_key, source_thread_id, state, "
                "agent, task_ref, branch, pr_url, cwd, parent_handoff_id, connection_id, created_at, "
                "updated_at, lease_token) VALUES (?,?,?,?,'pending',?,?,?,?,?,?,?,?,?,?)",
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
                    lease_token,
                ),
            )
            return self._handoff(self._required(handoff_id)), True

    def mark_stored(
        self, handoff_id: str, stored_thread_id: str, doc_markdown: str, *, lease_token: str = ""
    ) -> Handoff:
        if not stored_thread_id:
            raise HandoffDBError("stored thread not found")
        return self._transition(
            handoff_id,
            "stored",
            ("pending",),
            lease_token=lease_token,
            document=doc_markdown,
            stored_thread_id=stored_thread_id,
            doc_markdown="",
        )

    def mark_failed(self, handoff_id: str, error: str, *, lease_token: str = "") -> Handoff:
        return self._transition(
            handoff_id, "failed", ("pending",), lease_token=lease_token, error=error
        )

    def lease_token(self, handoff_id: str) -> str:
        """Read the current token when resuming an already stored handoff."""
        with self._lock:
            return self._required(handoff_id)["lease_token"]

    def claim_launch(self, handoff_id: str, *, lease_token: str = "") -> bool:
        """True once per launch attempt; only release_launch permits a retry."""
        with self._lock, self._control._tx():
            row = self._conn.execute(
                "SELECT lease_token FROM handoffs WHERE id = ?", (handoff_id,)
            ).fetchone()
            if row is not None and row["lease_token"] != lease_token:
                raise HandoffLeaseError("handoff storage attempt was replaced")
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
        Empty URL/thread fields may be filled once. Conflicting populated
        fields, including the agent reference, raise ``HandoffDBError``.
        """
        if not ref.agent_ref:
            raise HandoffDBError("successor not found")
        with self._lock, self._control._tx() as conn:
            row = self._required(handoff_id)
            if row["successor_agent_ref"]:
                if row["successor_agent_ref"] != ref.agent_ref or any(
                    row[column] and value and row[column] != value
                    for column, value in (
                        ("successor_url", ref.url),
                        ("successor_thread_id", ref.thread_id),
                    )
                ):
                    raise HandoffDBError("handoff already has a different successor")
                url = row["successor_url"] or ref.url
                thread_id = row["successor_thread_id"] or ref.thread_id
                if url != row["successor_url"] or thread_id != row["successor_thread_id"]:
                    conn.execute(
                        "UPDATE handoffs SET successor_url = ?, successor_thread_id = ?, "
                        "updated_at = ? WHERE id = ?",
                        (url, thread_id, _iso(_now()), handoff_id),
                    )
                return self._handoff(self._required(handoff_id))
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
            return self._handoff(self._required(handoff_id))

    def get(self, handoff_id: str) -> Optional[Handoff]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM handoffs WHERE id = ?", (handoff_id,)
            ).fetchone()
        return self._handoff(row) if row is not None else None

    def by_key(self, workspace_id: str, idempotency_key: str) -> Optional[Handoff]:
        """Read-only lookup by idempotency key; never creates a row."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM handoffs WHERE workspace_id = ? AND idempotency_key = ?",
                (workspace_id, idempotency_key),
            ).fetchone()
        return self._handoff(row) if row is not None else None

    def by_stored_thread(self, stored_thread_id: str) -> Optional[Handoff]:
        if not stored_thread_id:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM handoffs WHERE stored_thread_id = ? ORDER BY rowid LIMIT 1",
                (stored_thread_id,),
            ).fetchone()
        return self._handoff(row) if row is not None else None

    def by_successor_thread(self, workspace_id: str, thread_id: str) -> Optional[Handoff]:
        if not thread_id:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM handoffs WHERE workspace_id = ? AND successor_thread_id = ? "
                "ORDER BY rowid LIMIT 1",
                (workspace_id, thread_id),
            ).fetchone()
        return self._handoff(row) if row is not None else None

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
        return [self._handoff(row) for row in rows]
