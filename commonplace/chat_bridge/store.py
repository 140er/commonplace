"""The workspace file ``chat_bridge.sqlite3``: installs, channel mappings, links and the intake
journal of the chat-platform bridge.

Opened like ``bots.sqlite3`` (encrypted with the workspace when encryption is on) and created
only by the first write: a read on a workspace without the file answers "nothing" and creates
nothing. Ids are ``uuid4().hex``, times are UTC ``YYYY-MM-DDTHH:MM:SSZ``, platform ids are opaque
text. Nothing here stores conversation text. The sealed ``secrets`` column is read by
:meth:`BridgeStore.secrets` alone and is never part of any view.
"""

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from commonplace.admission import GATE
from mempalace.sqlite_crypto import connect, row_factory

DAILY_CAP_DEFAULT, DAILY_CAP_MAX = 200, 10_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bridge_installs (
  id           TEXT PRIMARY KEY,
  platform     TEXT NOT NULL,
  ext_team     TEXT NOT NULL,
  label        TEXT NOT NULL,
  secrets      BLOB NOT NULL,
  enabled      INTEGER NOT NULL DEFAULT 0,
  tested_at    TEXT,
  daily_cap    INTEGER NOT NULL DEFAULT 200,
  installed_by TEXT NOT NULL,
  created_at   TEXT NOT NULL, disabled_at TEXT,
  UNIQUE (platform, ext_team)
);
CREATE TABLE IF NOT EXISTS bridge_channels (
  install_id TEXT NOT NULL REFERENCES bridge_installs(id),
  ext_channel TEXT NOT NULL,
  board       TEXT NOT NULL,
  disclosure  TEXT NOT NULL DEFAULT 'titles' CHECK (disclosure IN ('titles','full')),
  enabled     INTEGER NOT NULL DEFAULT 1,
  mapped_by   TEXT NOT NULL, mapped_at TEXT NOT NULL,
  PRIMARY KEY (install_id, ext_channel)
);
CREATE TABLE IF NOT EXISTS bridge_links (
  id         TEXT PRIMARY KEY,
  install_id TEXT NOT NULL REFERENCES bridge_installs(id),
  ext_user   TEXT NOT NULL,
  user_id    TEXT NOT NULL,
  confirm_ok INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, revoked_at TEXT,
  UNIQUE (install_id, ext_user), UNIQUE (install_id, user_id)
);
CREATE TABLE IF NOT EXISTS bridge_link_codes (
  code_hash TEXT PRIMARY KEY, install_id TEXT NOT NULL, user_id TEXT NOT NULL,
  expires_at TEXT NOT NULL, used_at TEXT, tries INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bridge_events (
  install_id TEXT NOT NULL, ext_event TEXT NOT NULL,
  state      TEXT NOT NULL,
  user_id    TEXT, bot_id TEXT, message_id INTEGER,
  ext_channel TEXT, ext_thread TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (install_id, ext_event)
);
CREATE TABLE IF NOT EXISTS bridge_outbox (
  id TEXT PRIMARY KEY, install_id TEXT NOT NULL,
  ext_event TEXT NOT NULL, part INTEGER NOT NULL,
  target TEXT NOT NULL,
  ref TEXT NOT NULL,
  state TEXT NOT NULL,
  ext_post TEXT, attempts INTEGER NOT NULL DEFAULT 0, next_at TEXT,
  UNIQUE (install_id, ext_event, part, target)
);
CREATE TABLE IF NOT EXISTS bridge_prompts (
  pending TEXT PRIMARY KEY,
  install_id TEXT NOT NULL, ext_user TEXT NOT NULL, ext_channel TEXT NOT NULL, ext_post TEXT
);
"""

# Tables that hold rows of one install, emptied when the install is deleted.
_PER_INSTALL = (
    "bridge_channels",
    "bridge_links",
    "bridge_link_codes",
    "bridge_events",
    "bridge_outbox",
    "bridge_prompts",
)
_VIEW = (
    "id",
    "platform",
    "ext_team",
    "label",
    "enabled",
    "tested_at",
    "daily_cap",
    "installed_by",
    "created_at",
    "disabled_at",
)


class Missing(LookupError):
    """No such install."""


class Conflict(Exception):
    """The install already exists (platform and team)."""


class NotTested(Exception):
    """Enabling needs a successful test first."""


def stamp(now=None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _view(row) -> dict:
    out = {key: row[key] for key in _VIEW}
    out["enabled"] = bool(out["enabled"])
    return out


def open_bridge(gateway, workspace_id: str) -> "BridgeStore":
    stores = gateway.__dict__.setdefault("_bridge_stores", {})
    return stores.setdefault(workspace_id, BridgeStore(gateway.supervisor, workspace_id))


class BridgeStore:
    def __init__(self, supervisor, workspace_id):
        self._supervisor = supervisor
        self._workspace_id = workspace_id
        self._lock = threading.RLock()
        self._conn = None
        self._path = None

    def close(self):
        with self._lock:
            if self._conn is not None:
                self._conn.close()
            self._conn = self._path = None

    def _file(self) -> Path:
        return Path(self._supervisor.workspace_dir(self._workspace_id)).resolve() / (
            "chat_bridge.sqlite3"
        )

    def exists(self) -> bool:
        return self._file().exists()

    def _connection(self, create):
        path = self._file()
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
                conn.execute("PRAGMA foreign_keys=ON")
                conn.executescript(_SCHEMA)
            except Exception:
                conn.close()
                raise
            self._conn, self._path = conn, path
        return self._conn

    @contextmanager
    def _tx(self, *, write=True, create=False):
        """A transaction. Only ``create=True`` makes the file; without it a missing file yields
        ``None`` (callers answer "nothing" or :class:`Missing`) and nothing is written."""
        if not self._lock.acquire(timeout=5):
            raise TimeoutError("Bridge busy")
        try:
            conn = self._connection(create=create)
            if not write or conn is None:
                yield conn
                return
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            self._lock.release()

    # Installs

    def installs(self) -> list:
        with self._tx(write=False) as conn:
            if conn is None:
                return []
            return [
                _view(r)
                for r in conn.execute("SELECT * FROM bridge_installs ORDER BY created_at, id")
            ]

    def get(self, install_id) -> dict:
        with self._tx(write=False) as conn:
            return _view(self._row(conn, install_id))

    @staticmethod
    def _row(conn, install_id):
        row = (
            conn.execute("SELECT * FROM bridge_installs WHERE id=?", (install_id,)).fetchone()
            if conn is not None and isinstance(install_id, str)
            else None
        )
        if row is None:
            raise Missing()
        return row

    def secrets(self, install_id) -> bytes:
        """The sealed blob, for the caller that unseals it in memory. Never part of a view."""
        with self._tx(write=False) as conn:
            return bytes(self._row(conn, install_id)["secrets"])

    def create(
        self, install_id, platform, ext_team, label, sealed, daily_cap, user_id, now=None
    ) -> dict:
        with self._tx(create=True) as conn:
            try:
                conn.execute(
                    "INSERT INTO bridge_installs "
                    "(id,platform,ext_team,label,secrets,enabled,daily_cap,installed_by,created_at) "
                    "VALUES (?,?,?,?,?,0,?,?,?)",
                    (install_id, platform, ext_team, label, sealed, daily_cap, user_id, stamp(now)),
                )
            except sqlite3.IntegrityError:
                raise Conflict() from None
            return _view(self._row(conn, install_id))

    def update(self, install_id, *, label=None, daily_cap=None, sealed=None, now=None) -> dict:
        """Change fields. New credentials switch the install off and clear its test."""
        with self._tx() as conn:
            self._row(conn, install_id)
            if label is not None:
                conn.execute("UPDATE bridge_installs SET label=? WHERE id=?", (label, install_id))
            if daily_cap is not None:
                conn.execute(
                    "UPDATE bridge_installs SET daily_cap=? WHERE id=?", (daily_cap, install_id)
                )
            if sealed is not None:
                conn.execute(
                    "UPDATE bridge_installs SET secrets=?, tested_at=NULL, enabled=0, "
                    "disabled_at=COALESCE(disabled_at, ?) WHERE id=?",
                    (sealed, stamp(now), install_id),
                )
                self._cancel_open(conn, install_id)
            return _view(self._row(conn, install_id))

    def mark_tested(self, install_id, now=None) -> dict:
        with self._tx() as conn:
            self._row(conn, install_id)
            conn.execute(
                "UPDATE bridge_installs SET tested_at=? WHERE id=?", (stamp(now), install_id)
            )
            return _view(self._row(conn, install_id))

    def set_enabled(self, install_id, enabled: bool, now=None) -> dict:
        """Switch on (only after a successful test) or off (cancels queued posts and prompts)."""
        with self._tx() as conn:
            row = self._row(conn, install_id)
            if enabled:
                if row["tested_at"] is None:
                    raise NotTested()
                conn.execute(
                    "UPDATE bridge_installs SET enabled=1, disabled_at=NULL WHERE id=?",
                    (install_id,),
                )
            else:
                conn.execute(
                    "UPDATE bridge_installs SET enabled=0, disabled_at=COALESCE(disabled_at,?) "
                    "WHERE id=?",
                    (stamp(now), install_id),
                )
                self._cancel_open(conn, install_id)
            return _view(self._row(conn, install_id))

    @staticmethod
    def _cancel_open(conn, install_id):
        conn.execute(
            "UPDATE bridge_outbox SET state='cancelled' WHERE install_id=? "
            "AND state IN ('queued','sending')",
            (install_id,),
        )
        conn.execute("DELETE FROM bridge_prompts WHERE install_id=?", (install_id,))

    def disable_all(self, now=None) -> list:
        """The kill switch: every install of this workspace off. Returns the ids switched off."""
        if not self.exists():
            return []
        with self._tx() as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM bridge_installs WHERE enabled=1")]
            for install_id in ids:
                conn.execute(
                    "UPDATE bridge_installs SET enabled=0, disabled_at=COALESCE(disabled_at,?) "
                    "WHERE id=?",
                    (stamp(now), install_id),
                )
                self._cancel_open(conn, install_id)
            return ids

    def delete(self, install_id) -> None:
        with self._tx() as conn:
            self._row(conn, install_id)
            for table in _PER_INSTALL:
                conn.execute(f"DELETE FROM {table} WHERE install_id=?", (install_id,))
            conn.execute("DELETE FROM bridge_installs WHERE id=?", (install_id,))

    # Read-only status

    def status(self) -> list:
        """Per install: the view, the number of mapped channels and the last intake time."""
        with self._tx(write=False) as conn:
            if conn is None:
                return []
            out = []
            for row in conn.execute("SELECT * FROM bridge_installs ORDER BY created_at, id"):
                channels = conn.execute(
                    "SELECT COUNT(*) FROM bridge_channels WHERE install_id=? AND enabled=1",
                    (row["id"],),
                ).fetchone()[0]
                last = conn.execute(
                    "SELECT MAX(created_at) FROM bridge_events WHERE install_id=?", (row["id"],)
                ).fetchone()[0]
                out.append({**_view(row), "channels": channels, "last_intake": last})
            return out


def new_install_id() -> str:
    return uuid.uuid4().hex
