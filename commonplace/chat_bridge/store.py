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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from commonplace.admission import GATE
from mempalace.sqlite_crypto import connect, row_factory

DAILY_CAP_DEFAULT, DAILY_CAP_MAX = 200, 10_000
CODE_TRIES = 5  # wrong tries per sender (and bad presentations per code) before it stops working

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
  consequential_taps INTEGER NOT NULL DEFAULT 0,
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
CREATE TABLE IF NOT EXISTS bridge_link_tries (
  install_id TEXT NOT NULL, ext_user TEXT NOT NULL,
  tries INTEGER NOT NULL DEFAULT 0, last_at TEXT NOT NULL, locked_until TEXT,
  PRIMARY KEY (install_id, ext_user)
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

_VIEW = (
    "id",
    "platform",
    "ext_team",
    "label",
    "enabled",
    "tested_at",
    "daily_cap",
    "consequential_taps",
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


class CodeGone(Exception):
    """The link code was used, expired or spent meanwhile."""


_LINK = ("id", "install_id", "ext_user", "user_id", "confirm_ok", "created_at", "revoked_at")
_CHANNEL = ("install_id", "ext_channel", "board", "disclosure", "enabled", "mapped_by", "mapped_at")


def stamp(now=None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _channel(row) -> dict:
    out = {key: row[key] for key in _CHANNEL}
    out["enabled"] = bool(out["enabled"])
    return out


def _view(row) -> dict:
    out = {key: row[key] for key in _VIEW}
    out["enabled"] = bool(out["enabled"])
    out["consequential_taps"] = bool(out["consequential_taps"])
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
                self._migrate(conn)
            except Exception:
                conn.close()
                raise
            self._conn, self._path = conn, path
        return self._conn

    @staticmethod
    def _migrate(conn):
        """Files made before a column existed get it here, off by default."""
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(bridge_installs)")}
        if "consequential_taps" not in columns:
            conn.execute(
                "ALTER TABLE bridge_installs ADD COLUMN consequential_taps INTEGER NOT NULL DEFAULT 0"
            )

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

    def update(
        self, install_id, *, label=None, daily_cap=None, sealed=None, taps=None, now=None
    ) -> dict:
        """Change fields. New credentials switch the install off and clear its test."""
        with self._tx() as conn:
            self._row(conn, install_id)
            if taps is not None:
                conn.execute(
                    "UPDATE bridge_installs SET consequential_taps=? WHERE id=?",
                    (int(taps), install_id),
                )
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
            conn.execute("DELETE FROM bridge_channels WHERE install_id=?", (install_id,))
            conn.execute("DELETE FROM bridge_links WHERE install_id=?", (install_id,))
            conn.execute("DELETE FROM bridge_link_codes WHERE install_id=?", (install_id,))
            conn.execute("DELETE FROM bridge_link_tries WHERE install_id=?", (install_id,))
            conn.execute("DELETE FROM bridge_events WHERE install_id=?", (install_id,))
            conn.execute("DELETE FROM bridge_outbox WHERE install_id=?", (install_id,))
            conn.execute("DELETE FROM bridge_prompts WHERE install_id=?", (install_id,))
            conn.execute("DELETE FROM bridge_installs WHERE id=?", (install_id,))

    # Link codes and links

    def issue_code(self, install_id, user_id, code_hash, expires_at, now=None) -> None:
        """Store the hash of a new code; the person's earlier unused codes for this install end."""
        with self._tx() as conn:
            self._row(conn, install_id)
            conn.execute(
                "DELETE FROM bridge_link_codes WHERE install_id=? AND user_id=? AND used_at IS NULL",
                (install_id, user_id),
            )
            conn.execute(
                "DELETE FROM bridge_link_codes WHERE expires_at < ?",
                (stamp((now or datetime.now(timezone.utc)) - timedelta(days=1)),),
            )
            conn.execute(
                "INSERT INTO bridge_link_codes (code_hash,install_id,user_id,expires_at) "
                "VALUES (?,?,?,?)",
                (code_hash, install_id, user_id, expires_at),
            )

    def code(self, install_id, code_hash):
        with self._tx(write=False) as conn:
            row = (
                conn.execute(
                    "SELECT * FROM bridge_link_codes WHERE install_id=? AND code_hash=?",
                    (install_id, code_hash),
                ).fetchone()
                if conn is not None
                else None
            )
            return dict(row) if row else None

    def burn_code(self, install_id, code_hash, now=None) -> bool:
        with self._tx() as conn:
            return (
                conn.execute(
                    "UPDATE bridge_link_codes SET used_at=? WHERE install_id=? AND code_hash=? "
                    "AND used_at IS NULL",
                    (stamp(now), install_id, code_hash),
                ).rowcount
                == 1
            )

    def bump_code(self, install_id, code_hash) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE bridge_link_codes SET tries=tries+1 WHERE install_id=? AND code_hash=?",
                (install_id, code_hash),
            )

    def locked(self, install_id, ext_user, now=None) -> bool:
        with self._tx(write=False) as conn:
            row = (
                conn.execute(
                    "SELECT locked_until FROM bridge_link_tries WHERE install_id=? AND ext_user=?",
                    (install_id, ext_user),
                ).fetchone()
                if conn is not None
                else None
            )
            return bool(row and row["locked_until"] and row["locked_until"] > stamp(now))

    def wrong_try(self, install_id, ext_user, limit, lock_seconds, now=None) -> bool:
        """Count one wrong code from this sender; True when this try locked them out. Tries
        older than the lock period are forgotten."""
        now = now or datetime.now(timezone.utc)
        with self._tx() as conn:
            row = conn.execute(
                "SELECT tries, last_at FROM bridge_link_tries WHERE install_id=? AND ext_user=?",
                (install_id, ext_user),
            ).fetchone()
            fresh = stamp(now - timedelta(seconds=lock_seconds))
            tries = 1 if row is None or row["last_at"] < fresh else row["tries"] + 1
            locked = stamp(now + timedelta(seconds=lock_seconds)) if tries >= limit else None
            conn.execute(
                "INSERT INTO bridge_link_tries (install_id,ext_user,tries,last_at,locked_until) "
                "VALUES (?,?,?,?,?) ON CONFLICT (install_id, ext_user) DO UPDATE SET "
                "tries=excluded.tries, last_at=excluded.last_at, locked_until=excluded.locked_until",
                (install_id, ext_user, tries, stamp(now), locked),
            )
            conn.execute(
                "DELETE FROM bridge_link_tries WHERE last_at < ?",
                (stamp(now - timedelta(days=1)),),
            )
            return locked is not None

    def link_rows(self, install_id, ext_user, user_id) -> list:
        """Every row (live or revoked) that holds this platform id or this person here."""
        with self._tx(write=False) as conn:
            if conn is None:
                return []
            return [
                {k: r[k] for k in _LINK}
                for r in conn.execute(
                    "SELECT * FROM bridge_links WHERE install_id=? AND (ext_user=? OR user_id=?)",
                    (install_id, ext_user, user_id),
                )
            ]

    def create_link(
        self, link_id, install_id, ext_user, user_id, code_hash, drop=(), now=None
    ) -> dict:
        """Spend the code and bind the platform id in one step. ``drop`` are stale rows (revoked,
        or of people who left) that held the id or the person. A code that is no longer valid
        raises :class:`CodeGone` and changes nothing."""
        stamped = stamp(now)
        with self._tx() as conn:
            spent = conn.execute(
                "UPDATE bridge_link_codes SET used_at=? WHERE install_id=? AND code_hash=? "
                "AND user_id=? AND used_at IS NULL AND expires_at > ? AND tries < ?",
                (stamped, install_id, code_hash, user_id, stamped, CODE_TRIES),
            ).rowcount
            if spent != 1:
                raise CodeGone()
            for old in drop:
                conn.execute("DELETE FROM bridge_links WHERE id=?", (old,))
            conn.execute(
                "INSERT INTO bridge_links (id,install_id,ext_user,user_id,created_at) "
                "VALUES (?,?,?,?,?)",
                (link_id, install_id, ext_user, user_id, stamped),
            )
            conn.execute(
                "DELETE FROM bridge_link_tries WHERE install_id=? AND ext_user=?",
                (install_id, ext_user),
            )
            return self._link(conn, link_id)

    @staticmethod
    def _link(conn, link_id):
        row = (
            conn.execute("SELECT * FROM bridge_links WHERE id=?", (link_id,)).fetchone()
            if conn is not None and isinstance(link_id, str)
            else None
        )
        return {k: row[k] for k in _LINK} if row else None

    def link(self, link_id):
        with self._tx(write=False) as conn:
            return self._link(conn, link_id)

    def link_of(self, install_id, ext_user):
        """The live link of a platform id on an install, or None."""
        with self._tx(write=False) as conn:
            row = (
                conn.execute(
                    "SELECT id FROM bridge_links WHERE install_id=? AND ext_user=? "
                    "AND revoked_at IS NULL",
                    (install_id, ext_user),
                ).fetchone()
                if conn is not None
                else None
            )
            return self._link(conn, row["id"]) if row else None

    def links(self, *, install_id=None, user_id=None) -> list:
        """Live links, of one install or of one person (across installs)."""
        with self._tx(write=False) as conn:
            if conn is None:
                return []
            if install_id is not None:
                cursor = conn.execute(
                    "SELECT * FROM bridge_links WHERE install_id=? AND revoked_at IS NULL "
                    "ORDER BY created_at, id",
                    (install_id,),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM bridge_links WHERE user_id=? AND revoked_at IS NULL "
                    "ORDER BY created_at, id",
                    (user_id,),
                )
            return [{k: r[k] for k in _LINK} for r in cursor]

    def revoke_link(self, link_id, now=None):
        """End a link at once and cancel the open confirmations of its platform id. Returns the
        row as it was, or None when there is no live link."""
        with self._tx() as conn:
            row = self._link(conn, link_id)
            if row is None or row["revoked_at"] is not None:
                return None
            conn.execute("UPDATE bridge_links SET revoked_at=? WHERE id=?", (stamp(now), link_id))
            conn.execute(
                "DELETE FROM bridge_prompts WHERE install_id=? AND ext_user=?",
                (row["install_id"], row["ext_user"]),
            )
            return row

    def set_confirm_ok(self, link_id, user_id, value: bool):
        """The person's own opt-in to confirm from the platform. Turning it off also drops the
        open chips of that platform id. The live link row, or None (not theirs, or ended)."""
        with self._tx() as conn:
            row = self._link(conn, link_id)
            if row is None or row["revoked_at"] is not None or row["user_id"] != user_id:
                return None
            conn.execute("UPDATE bridge_links SET confirm_ok=? WHERE id=?", (int(value), link_id))
            if not value:
                conn.execute(
                    "DELETE FROM bridge_prompts WHERE install_id=? AND ext_user=?",
                    (row["install_id"], row["ext_user"]),
                )
            return self._link(conn, link_id)

    # Chips: one row per posted confirm button

    def add_prompt(self, pending, install_id, ext_user, ext_channel) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO bridge_prompts (pending,install_id,ext_user,ext_channel) "
                "VALUES (?,?,?,?)",
                (pending, install_id, ext_user, ext_channel),
            )

    def prompt(self, install_id, pending):
        """The chip of this install for this pending action, or None."""
        with self._tx(write=False) as conn:
            row = (
                conn.execute(
                    "SELECT * FROM bridge_prompts WHERE install_id=? AND pending=?",
                    (install_id, pending),
                ).fetchone()
                if conn is not None and isinstance(pending, str)
                else None
            )
            return dict(row) if row else None

    def has_installs(self) -> bool:
        with self._tx(write=False) as conn:
            return (
                conn is not None
                and conn.execute("SELECT 1 FROM bridge_installs LIMIT 1").fetchone() is not None
            )

    # Channel mappings (a direct message needs none)

    def channel(self, install_id, ext_channel):
        with self._tx(write=False) as conn:
            row = (
                conn.execute(
                    "SELECT * FROM bridge_channels WHERE install_id=? AND ext_channel=?",
                    (install_id, ext_channel),
                ).fetchone()
                if conn is not None
                else None
            )
            return _channel(row) if row else None

    def channels(self, install_id) -> list:
        with self._tx(write=False) as conn:
            if conn is None:
                return []
            return [
                _channel(r)
                for r in conn.execute(
                    "SELECT * FROM bridge_channels WHERE install_id=? ORDER BY ext_channel",
                    (install_id,),
                )
            ]

    def put_channel(
        self, install_id, ext_channel, board, disclosure, enabled, user_id, now=None
    ) -> dict:
        """Map (or change the mapping of) a channel to one project; the owner's act."""
        with self._tx() as conn:
            self._row(conn, install_id)
            conn.execute(
                "INSERT INTO bridge_channels "
                "(install_id,ext_channel,board,disclosure,enabled,mapped_by,mapped_at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT (install_id, ext_channel) DO UPDATE SET "
                "board=excluded.board, disclosure=excluded.disclosure, enabled=excluded.enabled, "
                "mapped_by=excluded.mapped_by, mapped_at=excluded.mapped_at",
                (install_id, ext_channel, board, disclosure, int(enabled), user_id, stamp(now)),
            )
            row = conn.execute(
                "SELECT * FROM bridge_channels WHERE install_id=? AND ext_channel=?",
                (install_id, ext_channel),
            ).fetchone()
            return _channel(row)

    def delete_channel(self, install_id, ext_channel) -> bool:
        with self._tx() as conn:
            self._row(conn, install_id)
            return (
                conn.execute(
                    "DELETE FROM bridge_channels WHERE install_id=? AND ext_channel=?",
                    (install_id, ext_channel),
                ).rowcount
                == 1
            )

    # Intake journal: one row per platform event, the idempotency key of the whole turn

    def journal(self, install_id, ext_event, state, ext_channel, ext_thread, now=None) -> bool:
        """Record an event once. False when the platform sent this id before (a retry)."""
        stamped = stamp(now)
        with self._tx(create=True) as conn:
            return (
                conn.execute(
                    "INSERT OR IGNORE INTO bridge_events "
                    "(install_id,ext_event,state,ext_channel,ext_thread,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (install_id, ext_event, state, ext_channel, ext_thread, stamped, stamped),
                ).rowcount
                == 1
            )

    def event(self, install_id, ext_event):
        with self._tx(write=False) as conn:
            row = (
                conn.execute(
                    "SELECT * FROM bridge_events WHERE install_id=? AND ext_event=?",
                    (install_id, ext_event),
                ).fetchone()
                if conn is not None
                else None
            )
            return dict(row) if row else None

    def set_event(self, install_id, ext_event, expect, state, now=None, **fields) -> bool:
        """Move an event to ``state`` if it is still in one of (at most three) ``expect`` states:
        a compare-and-set. ``fields`` may set ``user_id``, ``bot_id`` and ``message_id``."""
        assert set(fields) <= {"user_id", "bot_id", "message_id"} and 1 <= len(expect) <= 3
        states = (tuple(expect) + (expect[0],) * 3)[:3]
        with self._tx() as conn:
            return (
                conn.execute(
                    "UPDATE bridge_events SET state=?, updated_at=?, "
                    "user_id=COALESCE(?, user_id), bot_id=COALESCE(?, bot_id), "
                    "message_id=COALESCE(?, message_id) "
                    "WHERE install_id=? AND ext_event=? AND state IN (?,?,?)",
                    (
                        state,
                        stamp(now),
                        fields.get("user_id"),
                        fields.get("bot_id"),
                        fields.get("message_id"),
                        install_id,
                        ext_event,
                        *states,
                    ),
                ).rowcount
                == 1
            )

    def turns_today(self, install_id, now=None) -> int:
        """Events of this install that started a turn today (UTC): the daily cap's count."""
        day = stamp(now)[:10]
        with self._tx(write=False) as conn:
            if conn is None:
                return 0
            return conn.execute(
                "SELECT COUNT(*) FROM bridge_events WHERE install_id=? AND message_id IS NOT NULL "
                "AND created_at >= ?",
                (install_id, day),
            ).fetchone()[0]

    def open_events(self, install_id, state, before) -> list:
        """Events in ``state`` last touched before the ``before`` stamp, oldest first."""
        with self._tx(write=False) as conn:
            if conn is None:
                return []
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM bridge_events WHERE install_id=? AND state=? "
                    "AND updated_at < ? ORDER BY created_at",
                    (install_id, state, before),
                )
            ]

    # Outbox: every post the bridge makes has a row; nothing is posted without one

    def enqueue(self, install_id, ext_event, part, target, ref, state="queued", now=None) -> bool:
        """Queue one part of one reply. False when that (event, part, target) already has a row."""
        with self._tx() as conn:
            return (
                conn.execute(
                    "INSERT OR IGNORE INTO bridge_outbox "
                    "(id,install_id,ext_event,part,target,ref,state) VALUES (?,?,?,?,?,?,?)",
                    (new_install_id(), install_id, ext_event, part, target, ref, state),
                ).rowcount
                == 1
            )

    def outbox(self, install_id, ext_event=None) -> list:
        with self._tx(write=False) as conn:
            if conn is None:
                return []
            if ext_event is None:
                cursor = conn.execute(
                    "SELECT * FROM bridge_outbox WHERE install_id=? ORDER BY ext_event, part",
                    (install_id,),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM bridge_outbox WHERE install_id=? AND ext_event=? ORDER BY part",
                    (install_id, ext_event),
                )
            return [dict(r) for r in cursor]

    def due(self, install_id, now=None, limit=20) -> list:
        """Queued rows whose retry time has come, oldest first."""
        with self._tx(write=False) as conn:
            if conn is None:
                return []
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM bridge_outbox WHERE install_id=? AND state='queued' "
                    "AND (next_at IS NULL OR next_at <= ?) ORDER BY ext_event, part LIMIT ?",
                    (install_id, stamp(now), limit),
                )
            ]

    def claim(self, row_id, now=None):
        """``queued -> sending`` (counting the attempt) for exactly one caller, or None."""
        with self._tx() as conn:
            if (
                conn.execute(
                    "UPDATE bridge_outbox SET state='sending', attempts=attempts+1, next_at=? "
                    "WHERE id=? AND state='queued' AND install_id IN "
                    "(SELECT id FROM bridge_installs WHERE enabled=1)",
                    (stamp(now), row_id),
                ).rowcount
                != 1
            ):
                return None
            return dict(
                conn.execute("SELECT * FROM bridge_outbox WHERE id=?", (row_id,)).fetchone()
            )

    def settle(self, row_id, state, ext_post=None, next_at=None, expect="sending") -> bool:
        """Close a ``sending`` (or ``expect``) row: ``sent``, ``failed``, ``unknown``, or back
        to ``queued``."""
        with self._tx() as conn:
            return (
                conn.execute(
                    "UPDATE bridge_outbox SET state=?, ext_post=COALESCE(?, ext_post), next_at=? "
                    "WHERE id=? AND state=?",
                    (state, ext_post, next_at, row_id, expect),
                ).rowcount
                == 1
            )

    def stuck(self, install_id, before) -> list:
        """Rows left ``sending`` since before the stamp: a crash between the call and its record."""
        with self._tx(write=False) as conn:
            if conn is None:
                return []
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM bridge_outbox WHERE install_id=? AND state='sending' "
                    "AND next_at < ?",
                    (install_id, before),
                )
            ]

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
