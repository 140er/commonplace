"""The workspace file ``meetings.sqlite3``: switches, provider connections, people, meetings.

Opened like ``bots.sqlite3`` (encrypted with the workspace when encryption is on) and only
when something is written: reading a workspace that never used meetings answers the defaults
and creates no file. Provider credentials and meeting links are stored sealed (``seal``) and no
method here returns them. Errors carry fixed text and a code, never user text.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from commonplace.admission import GATE
from commonplace.meetings import adapter, seal
from mempalace.sqlite_crypto import connect, row_factory

LABEL_MAX = 60
SECRET_KEY = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
SECRET_VALUE_MAX, SECRETS_MAX_KEYS, SECRETS_MAX_BYTES = 4096, 16, 16384
LIMITS = {"max_concurrent": (1, 8), "max_minutes": (5, 480), "daily_meetings": (1, 200)}
FLAGS = ("enabled", "transcript_ok", "external_model_ok")
SETTINGS_KEYS = frozenset(FLAGS) | frozenset(LIMITS)
PERSON_KEYS = frozenset({"enabled", "keep_transcript", "language", "notes_wing"})
# A meeting in none of these is active: it holds a join link and may hold segments.
ENDED = ("done", "cancelled", "timeout", "discarded", "interrupted")
RUNNING = ("joining", "lobby", "announcing", "live", "ending", "processing", "removed")
ADVANCE_FIELDS = frozenset({"ext_meeting", "announced_at", "started_at", "end_reason"})
INVITE_KEYS = frozenset(
    {
        "link",
        "title",
        "keep_transcript",
        "board",
        "disclosure",
        "start_at",
        "attested",
        "request_id",
        "connection_id",
    }
)
TITLE_MAX, BOARD_MAX, START_AHEAD_S, JOIN_LEAD_S = 120, 80, 12 * 3600, 120
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{8,64}\Z")
START_AT = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meeting_settings (
  enabled INTEGER NOT NULL DEFAULT 0,
  transcript_ok INTEGER NOT NULL DEFAULT 0,
  external_model_ok INTEGER NOT NULL DEFAULT 0,
  max_concurrent INTEGER NOT NULL DEFAULT 1,
  max_minutes INTEGER NOT NULL DEFAULT 120,
  daily_meetings INTEGER NOT NULL DEFAULT 10,
  changed_by TEXT NOT NULL, changed_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS meeting_settings_one ON meeting_settings((1));
CREATE TABLE IF NOT EXISTS meeting_connections (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  label TEXT NOT NULL,
  secrets BLOB NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0,
  tested_at TEXT,
  created_by TEXT NOT NULL, created_at TEXT NOT NULL, disabled_at TEXT,
  UNIQUE (provider, label)
);
CREATE TABLE IF NOT EXISTS meeting_people (
  user_id TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 0,
  keep_transcript INTEGER NOT NULL DEFAULT 0,
  language TEXT NOT NULL DEFAULT 'en',
  notes_wing TEXT,
  changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meetings (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  connection_id TEXT NOT NULL REFERENCES meeting_connections(id),
  provider TEXT NOT NULL,
  join_ref BLOB,
  ext_meeting TEXT,
  title TEXT NOT NULL,
  bot_name TEXT NOT NULL,
  state TEXT NOT NULL,
  keep_transcript INTEGER NOT NULL DEFAULT 0,
  board TEXT,
  disclosure TEXT NOT NULL DEFAULT 'none' CHECK (disclosure IN ('none','titles','full')),
  attested_at TEXT NOT NULL,
  start_at TEXT,
  announced_at TEXT,
  started_at TEXT, ended_at TEXT,
  end_reason TEXT,
  request_id TEXT NOT NULL,
  lease_at REAL,
  created_at TEXT NOT NULL,
  UNIQUE (user_id, request_id)
);
CREATE INDEX IF NOT EXISTS meetings_by_user ON meetings(user_id, created_at);
CREATE TABLE IF NOT EXISTS meeting_participants (
  meeting_id TEXT NOT NULL, ext_participant TEXT NOT NULL,
  label TEXT NOT NULL,
  host INTEGER NOT NULL DEFAULT 0, joined_at TEXT NOT NULL, left_at TEXT,
  PRIMARY KEY (meeting_id, ext_participant)
);
CREATE TABLE IF NOT EXISTS meeting_segments (
  meeting_id TEXT NOT NULL, seq INTEGER NOT NULL,
  ext_participant TEXT,
  t_start_ms INTEGER NOT NULL, t_end_ms INTEGER NOT NULL,
  text TEXT NOT NULL,
  PRIMARY KEY (meeting_id, seq)
);
CREATE TABLE IF NOT EXISTS meeting_notes (
  id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('summary','decision','action','open_question')),
  text TEXT NOT NULL,
  evidence TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'draft',
  edited INTEGER NOT NULL DEFAULT 0,
  model TEXT NOT NULL,
  filed_ref TEXT,
  filed_request_id TEXT,
  decided_by TEXT, decided_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meeting_chat (
  meeting_id TEXT NOT NULL, ext_event TEXT NOT NULL,
  ext_participant TEXT,
  state TEXT NOT NULL,
  question TEXT, answer TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (meeting_id, ext_event)
);
CREATE TABLE IF NOT EXISTS meeting_outbox (
  id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  ref  TEXT NOT NULL,
  state TEXT NOT NULL,
  ext_post TEXT, attempts INTEGER NOT NULL DEFAULT 0, next_at TEXT,
  UNIQUE (meeting_id, kind, ref)
);
CREATE TABLE IF NOT EXISTS meeting_days (
  day TEXT PRIMARY KEY, n INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS meeting_events (
  meeting_id TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (meeting_id, seq)
);
"""


class Missing(LookupError):
    """No such connection or meeting for this caller."""


class Invalid(ValueError):
    """A value outside its bound; ``code`` and the fixed message never carry user text."""

    def __init__(self, code="invalid", message="Invalid request"):
        super().__init__(message)
        self.code, self.message = code, message


class Refused(Exception):
    """A rule refused the change (``status`` 403 or 409); fixed code and text."""

    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def stamp(now=None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp_ts(ts: float) -> str:
    return stamp(datetime.fromtimestamp(ts, timezone.utc))


def parse_stamp(value: str) -> float:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def is_ended(state: str) -> bool:
    return state in ENDED or state.startswith("failed:")


def plain_text(value, limit, code, what) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > limit
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise Invalid(code, f"The {what} must be 1-{limit} plain characters")
    return value.strip()


def _bool(value):
    if type(value) is not bool:
        raise Invalid("invalid_value", "A switch must be true or false")
    return int(value)


def _label(value) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > LABEL_MAX
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise Invalid("invalid_label", f"The label must be 1-{LABEL_MAX} plain characters")
    return value.strip()


def _secrets(value) -> dict:
    ok = isinstance(value, dict) and 0 < len(value) <= SECRETS_MAX_KEYS
    ok = ok and all(
        isinstance(k, str)
        and SECRET_KEY.fullmatch(k)
        and isinstance(v, str)
        and 0 < len(v) <= SECRET_VALUE_MAX
        for k, v in value.items()
    )
    if not ok or sum(len(k) + len(v) for k, v in value.items()) > SECRETS_MAX_BYTES:
        raise Invalid("invalid_secrets", "The credentials must be named text values")
    return value


def settings_view(row) -> dict:
    return {
        **{name: bool(row[name]) for name in FLAGS},
        **{name: row[name] for name in LIMITS},
    }


DEFAULT_SETTINGS = {
    "enabled": 0,
    "transcript_ok": 0,
    "external_model_ok": 0,
    "max_concurrent": 1,
    "max_minutes": 120,
    "daily_meetings": 10,
    "changed_by": "",
    "changed_at": "",
}
DEFAULT_PERSON = {
    "enabled": 0,
    "keep_transcript": 0,
    "language": "en",
    "notes_wing": None,
    "changed_at": "",
}


def _person(row: dict) -> dict:
    return {**row, "enabled": bool(row["enabled"]), "keep_transcript": bool(row["keep_transcript"])}


def connection_view(row) -> dict:
    """A connection without its credentials; ``has_secrets`` is all a caller learns of them."""
    return {
        "id": row["id"],
        "provider": row["provider"],
        "label": row["label"],
        "enabled": bool(row["enabled"]),
        "tested_at": row["tested_at"],
        "has_secrets": True,
        "created_at": row["created_at"],
        "disabled_at": row["disabled_at"],
    }


def meeting_view(row) -> dict:
    keys = (
        "id provider title bot_name state keep_transcript board disclosure start_at started_at "
        "announced_at ended_at end_reason created_at"
    ).split()
    return {key: row[key] for key in keys}


def status_view(row) -> dict:
    """State and times only: no title, bot name or board, which are inviter-typed text."""
    keys = "id provider state keep_transcript start_at started_at ended_at end_reason created_at"
    return {key: row[key] for key in keys.split()}


def open_meetings(gateway, workspace_id: str) -> "MeetingStore":
    stores = gateway.__dict__.setdefault("_meeting_stores", {})
    if workspace_id not in stores:
        stores[workspace_id] = MeetingStore(
            lambda: gateway.supervisor.workspace_dir(workspace_id),
            str(gateway.supervisor.root.parent),
            workspace_id,
        )
    return stores[workspace_id]


class MeetingStore:
    def __init__(self, locate, data_dir, workspace_id):
        self._locate = locate
        self.data_dir = data_dir
        self.workspace_id = workspace_id
        self._lock = threading.RLock()
        self._conn = None
        self._path = None

    def close(self):
        with self._lock:
            if self._conn is not None:
                self._conn.close()
            self._conn = self._path = None

    def _file(self) -> Path:
        return Path(self._locate()).resolve() / "meetings.sqlite3"

    def exists(self) -> bool:
        return os.path.exists(self._file())

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
                conn.executescript(_SCHEMA)
            except Exception:
                conn.close()
                raise
            self._conn, self._path = conn, path
        return self._conn

    @contextmanager
    def _tx(self, *, write=True):
        """The open connection (None when the file does not exist and nothing is written)."""
        if not self._lock.acquire(timeout=5):
            raise TimeoutError("Meetings busy")
        try:
            conn = self._connection(create=write)
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

    # --- owner switches -------------------------------------------------------------------

    def settings(self) -> dict:
        with self._tx(write=False) as conn:
            row = conn.execute("SELECT * FROM meeting_settings").fetchone() if conn else None
        return dict(row) if row else dict(DEFAULT_SETTINGS)

    def enabled(self) -> bool:
        return bool(self.settings()["enabled"])

    def put_settings(self, user_id, changes: dict) -> dict:
        if not changes or set(changes) - SETTINGS_KEYS:
            raise Invalid("invalid_field", "Unknown or missing settings")
        values = {}
        for name, value in changes.items():
            if name in FLAGS:
                values[name] = _bool(value)
                continue
            low, high = LIMITS[name]
            if type(value) is not int or not low <= value <= high:
                raise Invalid("invalid_limit", f"{name} must be a number from {low} to {high}")
            values[name] = value
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM meeting_settings").fetchone()
            merged = {**(dict(row) if row else DEFAULT_SETTINGS), **values}
            merged.update(changed_by=user_id, changed_at=stamp())
            conn.execute("DELETE FROM meeting_settings")
            conn.execute(
                "INSERT INTO meeting_settings VALUES (:enabled,:transcript_ok,:external_model_ok,"
                ":max_concurrent,:max_minutes,:daily_meetings,:changed_by,:changed_at)",
                merged,
            )
            if not merged["enabled"]:
                self._discard_active(conn)
        return merged

    def _discard_active(self, conn) -> int:
        """Active meetings end as cancelled with everything they hold gone: the join link, the
        working segments and any queued chat post. Notes already generated stay."""
        marks = ",".join("?" * len(ENDED))
        where = f"state NOT IN ({marks}) AND state NOT LIKE 'failed:%'"
        ids = [r["id"] for r in conn.execute(f"SELECT id FROM meetings WHERE {where}", ENDED)]
        for meeting_id in ids:
            conn.execute("DELETE FROM meeting_segments WHERE meeting_id=?", (meeting_id,))
            conn.execute(
                "UPDATE meeting_outbox SET state='cancelled' "
                "WHERE meeting_id=? AND state IN ('queued','sending')",
                (meeting_id,),
            )
        conn.execute(
            f"UPDATE meetings SET join_ref=NULL, state='cancelled', end_reason='cancelled', "
            f"ended_at=? WHERE {where}",
            (stamp(), *ENDED),
        )
        return len(ids)

    def disable(self) -> int:
        """The kill switch: the master switch off and every active meeting discarded. A workspace
        that never used meetings is left untouched (no file is created). Returns how many ended."""
        if not self.exists():
            return 0
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM meeting_settings").fetchone()
            if row is not None and row["enabled"]:
                conn.execute("UPDATE meeting_settings SET enabled=0, changed_at=?", (stamp(),))
            return self._discard_active(conn)

    # --- provider connections -------------------------------------------------------------

    def connections(self) -> list[dict]:
        with self._tx(write=False) as conn:
            rows = (
                conn.execute("SELECT * FROM meeting_connections ORDER BY created_at, id")
                if conn
                else []
            )
            return [connection_view(r) for r in rows]

    def _row(self, conn, connection_id):
        row = None
        if conn is not None:
            row = conn.execute("SELECT * FROM meeting_connections WHERE id=?", (connection_id,))
            row = row.fetchone()
        if row is None:
            raise Missing
        return row

    def add_connection(self, user_id, provider, label, secrets) -> dict:
        if adapter.get(provider if isinstance(provider, str) else "") is None:
            raise Invalid("unknown_provider", "That provider is not available")
        label, secrets = _label(label), _secrets(secrets)
        connection_id = uuid.uuid4().hex
        blob = seal.seal(self.data_dir, self.workspace_id, "secrets", connection_id, secrets)
        try:
            with self._tx() as conn:
                conn.execute(
                    "INSERT INTO meeting_connections(id, provider, label, secrets, created_by, "
                    "created_at) VALUES (?,?,?,?,?,?)",
                    (connection_id, provider, label, blob, user_id, stamp()),
                )
                return connection_view(self._row(conn, connection_id))
        except sqlite3.IntegrityError:
            raise Refused("label_in_use", "A connection with that label exists") from None

    def update_connection(self, connection_id, changes: dict) -> dict:
        if not changes or set(changes) - {"enabled", "label", "secrets"}:
            raise Invalid("invalid_field", "Unknown or missing fields")
        label = _label(changes["label"]) if "label" in changes else None
        enabled = _bool(changes["enabled"]) if "enabled" in changes else None
        blob = None
        if "secrets" in changes:
            blob = seal.seal(
                self.data_dir,
                self.workspace_id,
                "secrets",
                connection_id,
                _secrets(changes["secrets"]),
            )
        try:
            with self._tx() as conn:
                row = self._row(conn, connection_id)
                if label is not None:
                    conn.execute(
                        "UPDATE meeting_connections SET label=? WHERE id=?", (label, connection_id)
                    )
                if blob is not None:
                    # New credentials are untested credentials: switched off until they pass.
                    conn.execute(
                        "UPDATE meeting_connections SET secrets=?, tested_at=NULL, enabled=0, "
                        "disabled_at=? WHERE id=?",
                        (blob, stamp(), connection_id),
                    )
                if enabled is not None:
                    self._set_enabled(conn, connection_id, bool(enabled), row["provider"])
                return connection_view(self._row(conn, connection_id))
        except sqlite3.IntegrityError:
            raise Refused("label_in_use", "A connection with that label exists") from None

    @staticmethod
    def _set_enabled(conn, connection_id, enabled, provider):
        if enabled:
            row = conn.execute(
                "SELECT tested_at FROM meeting_connections WHERE id=?", (connection_id,)
            ).fetchone()
            if row["tested_at"] is None or adapter.get(provider) is None:
                raise Refused("not_tested", "Run a passing test before switching this on")
            conn.execute(
                "UPDATE meeting_connections SET enabled=1, disabled_at=NULL WHERE id=?",
                (connection_id,),
            )
        else:
            conn.execute(
                "UPDATE meeting_connections SET enabled=0, disabled_at=? WHERE id=?",
                (stamp(), connection_id),
            )

    def delete_connection(self, connection_id) -> None:
        with self._tx() as conn:
            self._row(conn, connection_id)
            if conn.execute(
                "SELECT 1 FROM meetings WHERE connection_id=?", (connection_id,)
            ).fetchone():
                raise Refused("in_use", "Meetings used this connection; switch it off instead")
            conn.execute("DELETE FROM meeting_connections WHERE id=?", (connection_id,))

    def open_for_test(self, connection_id) -> adapter.Connection:
        """The connection with its credentials unsealed, for the adapter's test call only."""
        with self._tx(write=False) as conn:
            row = self._row(conn, connection_id)
            blob = bytes(row["secrets"])
        secrets = seal.unseal(self.data_dir, self.workspace_id, "secrets", row["id"], blob)
        return adapter.Connection(row["id"], row["provider"], row["label"], secrets)

    def record_test(self, connection_id, passed: bool) -> dict:
        """A passing test records its time; a failing one clears it and switches the connection
        off, so a connection that stopped working is never left enabled."""
        with self._tx() as conn:
            self._row(conn, connection_id)
            if passed:
                conn.execute(
                    "UPDATE meeting_connections SET tested_at=? WHERE id=?",
                    (stamp(), connection_id),
                )
            else:
                conn.execute(
                    "UPDATE meeting_connections SET tested_at=NULL, enabled=0, disabled_at=? "
                    "WHERE id=?",
                    (stamp(), connection_id),
                )
            return connection_view(self._row(conn, connection_id))

    def has_enabled_connection(self) -> bool:
        with self._tx(write=False) as conn:
            return bool(
                conn
                and conn.execute("SELECT 1 FROM meeting_connections WHERE enabled=1").fetchone()
            )

    # --- people ---------------------------------------------------------------------------

    def person(self, user_id) -> dict:
        with self._tx(write=False) as conn:
            row = (
                conn.execute("SELECT * FROM meeting_people WHERE user_id=?", (user_id,)).fetchone()
                if conn
                else None
            )
        return _person({**(dict(row) if row else DEFAULT_PERSON), "user_id": user_id})

    def put_person(self, user_id, changes: dict, *, transcript_ok: bool) -> dict:
        if not changes or set(changes) - PERSON_KEYS:
            raise Invalid("invalid_field", "Unknown or missing fields")
        values = {}
        for name, value in changes.items():
            if name in ("enabled", "keep_transcript"):
                values[name] = _bool(value)
            elif name == "language":
                from commonplace.voice import LANGUAGES

                if value not in LANGUAGES:
                    raise Invalid("invalid_language", "That language is not supported")
                values[name] = value
            else:
                values[name] = self._wing(value)
        if values.get("keep_transcript") and not transcript_ok:
            raise Refused(
                "transcript_not_allowed", "The workspace owner has not allowed transcripts"
            )
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM meeting_people WHERE user_id=?", (user_id,))
            row = row.fetchone()
            merged = {**(dict(row) if row else DEFAULT_PERSON), **values, "user_id": user_id}
            merged["changed_at"] = stamp()
            conn.execute(
                "INSERT OR REPLACE INTO meeting_people VALUES "
                "(:user_id,:enabled,:keep_transcript,:language,:notes_wing,:changed_at)",
                merged,
            )
        return _person(merged)

    @staticmethod
    def _wing(value):
        if value is None:
            return None
        from mempalace.config import sanitize_name

        try:
            return sanitize_name(value, "notes_wing")
        except ValueError:
            raise Invalid("invalid_wing", "That wing name is not valid") from None

    def refusal_for_invite(self, user_id):
        """Why this person cannot invite a bot right now, or None. The owner's switch is checked
        by the caller first; a person who never opted in is never offered anything."""
        if not self.person(user_id)["enabled"]:
            return Refused("not_opted_in", "Turn meeting notes on for yourself first", 403)
        if not self.has_enabled_connection():
            return Refused("no_connection", "No meeting provider is switched on")
        return None

    # --- meetings -------------------------------------------------------------------------

    def meetings_for(self, user_id) -> list[dict]:
        with self._tx(write=False) as conn:
            rows = (
                conn.execute(
                    "SELECT * FROM meetings WHERE user_id=? ORDER BY created_at DESC, id",
                    (user_id,),
                )
                if conn
                else []
            )
            return [meeting_view(r) for r in rows]

    def status_for(self, user_id) -> list[dict]:
        """Own meetings with counts and no text: the MCP status view."""
        with self._tx(write=False) as conn:
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT * FROM meetings WHERE user_id=? ORDER BY created_at DESC, id LIMIT 50",
                (user_id,),
            ).fetchall()
            out = []
            for row in rows:
                counts = {
                    name: conn.execute(
                        f"SELECT COUNT(*) AS n FROM {table} WHERE meeting_id=?", (row["id"],)
                    ).fetchone()["n"]
                    for name, table in (
                        ("participants", "meeting_participants"),
                        ("segments", "meeting_segments"),
                        ("notes", "meeting_notes"),
                    )
                }
                out.append({**status_view(row), "counts": counts})
            return out

    def overview(self) -> dict:
        """What the owner may see: counts per state and per connection state, never titles."""
        with self._tx(write=False) as conn:
            if conn is None:
                return {"states": {}, "meetings": 0, "connections": 0, "connections_enabled": 0}
            states = {
                r["state"]: r["n"]
                for r in conn.execute("SELECT state, COUNT(*) AS n FROM meetings GROUP BY state")
            }
            c = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(enabled),0) AS e FROM meeting_connections"
            ).fetchone()
        return {
            "states": states,
            "meetings": sum(states.values()),
            "connections": c["n"],
            "connections_enabled": c["e"],
        }

    # --- invite ---------------------------------------------------------------------------

    def _enabled_connection(self, connection_id):
        with self._tx(write=False) as conn:
            rows = (
                conn.execute(
                    "SELECT * FROM meeting_connections WHERE enabled=1 ORDER BY created_at, id"
                ).fetchall()
                if conn
                else []
            )
        if connection_id is not None:
            rows = [r for r in rows if r["id"] == connection_id]
            if not rows:
                raise Invalid("unknown_connection", "That provider connection is not available")
        elif len(rows) != 1:
            raise Invalid("connection_required", "Choose which provider connection to use")
        return rows[0]

    def invite(self, user_id, body: dict, *, bot_name: str, now: float) -> tuple[dict, bool]:
        """Store one invite and return ``(meeting, created)``. A repeat of the same ``request_id``
        returns the first meeting, whatever else the request says. The meeting link is sealed
        before it is stored and is not returned by anything."""
        required = {"link", "title", "attested", "request_id"}
        if set(body) - INVITE_KEYS or not required <= set(body):
            raise Invalid("invalid_field", "Link, title, attestation and request id are required")
        if body["attested"] is not True:
            raise Invalid(
                "attestation_required", "Confirm you may bring a notes bot into this meeting"
            )
        request_id = body["request_id"]
        if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
            raise Invalid("invalid_request_id", "The request id must be 8-64 letters or digits")
        existing = self._by_request(user_id, request_id)
        if existing is not None:
            return meeting_view(existing), False
        title = plain_text(body["title"], TITLE_MAX, "invalid_title", "title")
        disclosure = body.get("disclosure", "none")
        if disclosure not in ("none", "titles", "full"):
            raise Invalid("invalid_disclosure", "Disclosure must be none, titles or full")
        board = body.get("board")
        board = None if board is None else plain_text(board, BOARD_MAX, "invalid_board", "board")
        settings = self.settings()
        if "keep_transcript" in body:
            keep = bool(_bool(body["keep_transcript"]))
            if keep and not settings["transcript_ok"]:
                raise Refused(
                    "transcript_not_allowed", "The workspace owner has not allowed transcripts"
                )
        else:  # the person's default, which the owner's switch can still veto
            keep = self.person(user_id)["keep_transcript"] and bool(settings["transcript_ok"])
        start_at = body.get("start_at")
        if start_at is not None:
            try:
                when = parse_stamp(start_at) if START_AT.fullmatch(start_at or "") else None
            except (TypeError, ValueError):
                when = None
            if when is None or not now - 60 <= when <= now + START_AHEAD_S:
                raise Invalid("invalid_start", "Start time must be within the next 12 hours")
        row = self._enabled_connection(body.get("connection_id"))
        provider = adapter.get(row["provider"])
        if provider is None:
            raise Refused("no_connection", "No meeting provider is switched on")
        if not provider.caps.chat_send:
            raise Refused("no_chat_send", "That provider cannot announce the bot in the meeting")
        link = body["link"]
        try:
            parsed = (
                provider.parse_link(link) if isinstance(link, str) and len(link) <= 2048 else None
            )
        except Exception:
            parsed = None
        if not isinstance(parsed, adapter.JoinRef):
            raise Invalid("invalid_link", "That meeting link is not accepted")
        meeting_id = uuid.uuid4().hex
        blob = seal.seal(self.data_dir, self.workspace_id, "join_ref", meeting_id, parsed.ref)
        stamped = stamp_ts(now)
        try:
            with self._tx() as conn:
                day = stamped[:10]
                used = conn.execute("SELECT n FROM meeting_days WHERE day=?", (day,)).fetchone()
                if used and used["n"] >= settings["daily_meetings"]:
                    raise Refused("daily_limit", "The daily meeting limit is reached", 429)
                conn.execute(
                    "DELETE FROM meeting_days WHERE day < ?", (stamp_ts(now - 7 * 86400)[:10],)
                )
                conn.execute(
                    "INSERT INTO meeting_days(day, n) VALUES (?, 1) "
                    "ON CONFLICT(day) DO UPDATE SET n = n + 1",
                    (day,),
                )
                conn.execute(
                    "INSERT INTO meetings(id, user_id, connection_id, provider, join_ref, "
                    "ext_meeting, title, bot_name, state, keep_transcript, board, disclosure, "
                    "attested_at, start_at, request_id, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,'requested',?,?,?,?,?,?,?)",
                    (
                        meeting_id,
                        user_id,
                        row["id"],
                        row["provider"],
                        blob,
                        parsed.ext_meeting,
                        title,
                        bot_name,
                        int(keep),
                        board,
                        disclosure,
                        stamped,
                        start_at,
                        request_id,
                        stamped,
                    ),
                )
                self._event(conn, meeting_id, "requested", stamped)
                return meeting_view(self._meeting_row(conn, meeting_id)), True
        except sqlite3.IntegrityError:
            existing = self._by_request(user_id, request_id)
            if existing is None:
                raise
            return meeting_view(existing), False

    def _by_request(self, user_id, request_id):
        with self._tx(write=False) as conn:
            if conn is None:
                return None
            return conn.execute(
                "SELECT * FROM meetings WHERE user_id=? AND request_id=?", (user_id, request_id)
            ).fetchone()

    @staticmethod
    def _meeting_row(conn, meeting_id):
        row = conn.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        if row is None:
            raise Missing
        return row

    @staticmethod
    def _event(conn, meeting_id, kind, at):
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM meeting_events WHERE meeting_id=?",
            (meeting_id,),
        ).fetchone()["n"]
        conn.execute(
            "INSERT INTO meeting_events(meeting_id, seq, kind, at) VALUES (?,?,?,?)",
            (meeting_id, seq, kind, at),
        )

    def meeting(self, meeting_id, user_id=None) -> dict:
        """One meeting as its inviter sees it; another person's id is ``Missing``. ``user_id=None``
        is the engine and the owner's stop, never a route's reader."""
        with self._tx(write=False) as conn:
            row = (
                conn.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
                if conn
                else None
            )
        if row is None or (user_id is not None and row["user_id"] != user_id):
            raise Missing
        return meeting_view(row)

    def events_for(self, meeting_id) -> list[str]:
        """The lifecycle journal's kinds in order (tests and diagnostics; it holds no text)."""
        with self._tx(write=False) as conn:
            rows = (
                conn.execute(
                    "SELECT kind FROM meeting_events WHERE meeting_id=? ORDER BY seq", (meeting_id,)
                )
                if conn
                else []
            )
            return [r["kind"] for r in rows]

    # --- lifecycle: claim, lease, transitions ---------------------------------------------

    def run_row(self, meeting_id) -> dict:
        """The meeting row for its worker, without the sealed link."""
        with self._tx(write=False) as conn:
            row = (
                conn.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
                if conn
                else None
            )
        if row is None:
            raise Missing
        return {k: v for k, v in dict(row).items() if k != "join_ref"}

    def open_join_ref(self, meeting_id) -> str:
        with self._tx(write=False) as conn:
            row = self._meeting_row(conn, meeting_id) if conn else None
        if row is None or row["join_ref"] is None:
            raise Missing
        return seal.unseal(
            self.data_dir, self.workspace_id, "join_ref", meeting_id, bytes(row["join_ref"])
        )

    def open_connection(self, connection_id) -> adapter.Connection:
        return self.open_for_test(connection_id)

    def connection_enabled(self, connection_id) -> bool:
        with self._tx(write=False) as conn:
            row = (
                conn.execute(
                    "SELECT enabled FROM meeting_connections WHERE id=?", (connection_id,)
                ).fetchone()
                if conn
                else None
            )
        return bool(row and row["enabled"])

    def due(self, now: float) -> list[str]:
        """Requested meetings whose join time has come (a scheduled one, two minutes early)."""
        horizon = stamp_ts(now + JOIN_LEAD_S)
        with self._tx(write=False) as conn:
            rows = (
                conn.execute(
                    "SELECT id FROM meetings WHERE state='requested' "
                    "AND (start_at IS NULL OR start_at <= ?) ORDER BY created_at, id",
                    (horizon,),
                )
                if conn
                else []
            )
            return [r["id"] for r in rows]

    def pending(self) -> bool:
        """Is anything requested or running (the worker keeps beating while so)."""
        with self._tx(write=False) as conn:
            if conn is None:
                return False
            marks = ",".join("?" * len(ENDED))
            return bool(
                conn.execute(
                    f"SELECT 1 FROM meetings WHERE state NOT IN ({marks}) "
                    "AND state NOT LIKE 'failed:%' LIMIT 1",
                    ENDED,
                ).fetchone()
            )

    def free_slots(self, now: float, lease_s: float) -> int:
        """How many more meetings may start under ``max_concurrent`` right now."""
        marks = ",".join("?" * len(RUNNING))
        with self._tx(write=False) as conn:
            if conn is None:
                return 0
            busy = conn.execute(
                f"SELECT COUNT(*) AS n FROM meetings WHERE state IN ({marks}) AND lease_at > ?",
                (*RUNNING, now - lease_s),
            ).fetchone()["n"]
        return max(0, self.settings()["max_concurrent"] - busy)

    def claim(self, meeting_id, now: float, lease_s: float) -> bool:
        """``requested`` -> ``joining`` with a fresh lease, at most once and only while the
        workspace is under ``max_concurrent``. A second fire of the same meeting finds another
        state and gets False."""
        with self._tx() as conn:
            row = self._meeting_row(conn, meeting_id)
            if row["state"] != "requested":
                return False
            if row["start_at"] and row["start_at"] > stamp_ts(now + JOIN_LEAD_S):
                return False
            limit = self.settings()["max_concurrent"]
            marks = ",".join("?" * len(RUNNING))
            busy = conn.execute(
                f"SELECT COUNT(*) AS n FROM meetings WHERE state IN ({marks}) AND lease_at > ?",
                (*RUNNING, now - lease_s),
            ).fetchone()["n"]
            if busy >= limit:
                return False
            conn.execute(
                "UPDATE meetings SET state='joining', lease_at=? WHERE id=?", (now, meeting_id)
            )
            self._event(conn, meeting_id, "joining", stamp_ts(now))
            return True

    def renew(self, meeting_id, now: float) -> bool:
        """Heartbeat. False once the meeting ended, whoever ended it."""
        marks = ",".join("?" * len(ENDED))
        with self._tx() as conn:
            return (
                conn.execute(
                    f"UPDATE meetings SET lease_at=? WHERE id=? AND state NOT IN ({marks}) "
                    "AND state NOT LIKE 'failed:%'",
                    (now, meeting_id, *ENDED),
                ).rowcount
                == 1
            )

    def advance(self, meeting_id, to, now: float, *, wipe_ref=False, **fields) -> bool:
        """Move a running meeting to ``to``: compare-and-set, False if it ended or if someone asked
        for the end meanwhile. A stop (``removed``) is never overwritten, and neither is a leave
        (``ending``, ``processing``) by a step that would go back to joining, announcing or live,
        so a request that lands during the announcement still ends the meeting without capture."""
        if set(fields) - ADVANCE_FIELDS:
            raise ValueError("unknown field")
        refused = ("removed",)
        if to not in ("ending", "processing"):
            refused += ("ending", "processing")
        sets = ["state=?", *(f"{name}=?" for name in fields)]
        values = [to, *fields.values()]
        if wipe_ref:
            sets.append("join_ref=NULL")
        skipped = (*ENDED, *refused)
        marks = ",".join("?" * len(skipped))
        with self._tx() as conn:
            changed = conn.execute(
                f"UPDATE meetings SET {', '.join(sets)} WHERE id=? AND state NOT IN ({marks}) "
                "AND state NOT LIKE 'failed:%'",
                (*values, meeting_id, *skipped),
            ).rowcount
            if changed:
                self._event(conn, meeting_id, to, stamp_ts(now))
            return bool(changed)

    def finish(self, meeting_id, state, reason, now: float, *, discard: bool) -> bool:
        """End the meeting for good: the sealed link is wiped, queued chat posts are cancelled and,
        with ``discard``, everything captured (working segments, participants) is deleted for real.
        False if it had already ended (a kill switch got there first)."""
        marks = ",".join("?" * len(ENDED))
        with self._tx() as conn:
            changed = conn.execute(
                "UPDATE meetings SET state=?, end_reason=?, ended_at=?, join_ref=NULL, "
                f"lease_at=NULL WHERE id=? AND state NOT IN ({marks}) AND state NOT LIKE 'failed:%'",
                (state, reason, stamp_ts(now), meeting_id, *ENDED),
            ).rowcount
            if not changed:
                return False
            if discard:
                for table in ("meeting_segments", "meeting_participants"):
                    conn.execute(f"DELETE FROM {table} WHERE meeting_id=?", (meeting_id,))
            conn.execute(
                "UPDATE meeting_outbox SET state='cancelled' "
                "WHERE meeting_id=? AND state IN ('queued','sending')",
                (meeting_id,),
            )
            self._event(conn, meeting_id, state, stamp_ts(now))
            return True

    def request_end(self, meeting_id, kind, now: float, user_id=None) -> dict:
        """The inviter's leave (``inviter_left``) or the owner's stop (``stop_requested``). A meeting
        that has not joined yet is simply cancelled; a running one is flagged and its worker acts at
        its next beat (the stop discards, the leave keeps what was captured). An ended meeting is
        returned unchanged."""
        with self._tx() as conn:
            row = self._meeting_row(conn, meeting_id)
            if user_id is not None and row["user_id"] != user_id:
                raise Missing
            if is_ended(row["state"]):
                return meeting_view(row)
            if row["state"] == "requested":
                conn.execute(
                    "UPDATE meetings SET state='cancelled', end_reason='cancelled', ended_at=?, "
                    "join_ref=NULL WHERE id=?",
                    (stamp_ts(now), meeting_id),
                )
                self._event(conn, meeting_id, "cancelled", stamp_ts(now))
            elif kind == "stop_requested":
                conn.execute(
                    "UPDATE meetings SET state='removed', end_reason='stop_requested' WHERE id=?",
                    (meeting_id,),
                )
                self._event(conn, meeting_id, "removed", stamp_ts(now))
            elif row["state"] not in ("removed", "ending", "processing"):
                conn.execute(
                    "UPDATE meetings SET state='ending', end_reason='inviter_left' WHERE id=?",
                    (meeting_id,),
                )
                self._event(conn, meeting_id, "ending", stamp_ts(now))
            return meeting_view(self._meeting_row(conn, meeting_id))

    def expired(self, now: float, lease_s: float) -> list[str]:
        """Running meetings whose worker stopped beating: crashed, restarted or lost."""
        marks = ",".join("?" * len(RUNNING))
        with self._tx(write=False) as conn:
            rows = (
                conn.execute(
                    f"SELECT id FROM meetings WHERE state IN ({marks}) "
                    "AND (lease_at IS NULL OR lease_at < ?) ORDER BY created_at, id",
                    (*RUNNING, now - lease_s),
                )
                if conn
                else []
            )
            return [r["id"] for r in rows]

    # --- participants and the chat outbox -------------------------------------------------

    def participant_seen(self, meeting_id, ext, label, host, now: float) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO meeting_participants(meeting_id, ext_participant, label, host, "
                "joined_at) VALUES (?,?,?,?,?) ON CONFLICT(meeting_id, ext_participant) "
                "DO UPDATE SET left_at=NULL, host=excluded.host, label=excluded.label",
                (meeting_id, ext, label, int(host), stamp_ts(now)),
            )

    def participant_left(self, meeting_id, ext, now: float) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE meeting_participants SET left_at=? WHERE meeting_id=? "
                "AND ext_participant=?",
                (stamp_ts(now), meeting_id, ext),
            )

    def participants_present(self, meeting_id) -> int:
        with self._tx(write=False) as conn:
            return (
                conn.execute(
                    "SELECT COUNT(*) AS n FROM meeting_participants WHERE meeting_id=? "
                    "AND left_at IS NULL",
                    (meeting_id,),
                ).fetchone()["n"]
                if conn
                else 0
            )

    def outbox_begin(self, meeting_id, kind, ref) -> tuple[str, str]:
        """Claim the right to post once. Returns ``(outbox id, prior state)``; the prior state is
        ``new`` or ``queued`` when the caller may post (the row is now ``sending``), anything else
        (``sent``, ``sending``, ``unknown``, ``cancelled``) when it may not."""
        with self._tx() as conn:
            row = conn.execute(
                "SELECT id, state FROM meeting_outbox WHERE meeting_id=? AND kind=? AND ref=?",
                (meeting_id, kind, ref),
            ).fetchone()
            if row is None:
                outbox_id = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO meeting_outbox(id, meeting_id, kind, ref, state, attempts) "
                    "VALUES (?,?,?,?, 'sending', 1)",
                    (outbox_id, meeting_id, kind, ref),
                )
                return outbox_id, "new"
            if row["state"] == "queued":
                conn.execute(
                    "UPDATE meeting_outbox SET state='sending', attempts=attempts+1 WHERE id=?",
                    (row["id"],),
                )
            return row["id"], row["state"]

    def outbox_mark(self, outbox_id, state, ext_post="") -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE meeting_outbox SET state=?, ext_post=COALESCE(NULLIF(?, ''), ext_post) "
                "WHERE id=?",
                (state, ext_post, outbox_id),
            )

    def outbox_state(self, meeting_id, kind, ref):
        with self._tx(write=False) as conn:
            row = (
                conn.execute(
                    "SELECT state FROM meeting_outbox WHERE meeting_id=? AND kind=? AND ref=?",
                    (meeting_id, kind, ref),
                ).fetchone()
                if conn
                else None
            )
        return row["state"] if row else None

    def outbox_unknown(self, meeting_id) -> None:
        """A post that was ``sending`` when its worker died may or may not have gone out."""
        with self._tx() as conn:
            conn.execute(
                "UPDATE meeting_outbox SET state='unknown' WHERE meeting_id=? AND state='sending'",
                (meeting_id,),
            )
