"""Per-person bot threads in the workspace file ``bots.sqlite3``.

Opened like ``chat.sqlite3`` (encrypted with the workspace when encryption is on). Messages are
append-only and verbatim; a removed memory note keeps its row but loses its text. Every method that a caller
reaches takes the user id and answers :class:`Missing` for a bot that is not that user's, so a
foreign id is indistinguishable from an unknown one. Errors never carry conversation text.
"""

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from commonplace.admission import GATE
from commonplace.bots import sharing_setting
from commonplace.bots.errors import (
    PAGE_DEFAULT,
    PAGE_MAX,
    REQUEST_ID,
    TEXT_MAX,
    Conflict,
    Invalid,
    Limit,
    Missing,
    bounded,
    message,
    stamp,
)
from commonplace.bots.learn_schema import LEARN_SCHEMA, MEMORY_TRIGGER
from commonplace.bots.shares import SCHEMA_SHARES, SharesMixin, added_columns, ensure_request_index
from mempalace.sqlite_crypto import connect, row_factory

MAX_LIVE_BOTS = 50
NAME_MAX, JOB_MAX, INSTRUCTIONS_MAX, NOTE_MAX = 60, 200, 4000, 2000
PENDING_TTL_S = 600
KINDS = ("project", "general", "custom")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bots (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    job TEXT NOT NULL DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL CHECK (kind IN ('project','general','custom')),
    board TEXT,
    read_upto INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS bots_project ON bots(user_id, board) WHERE kind='project';
CREATE UNIQUE INDEX IF NOT EXISTS bots_general ON bots(user_id) WHERE kind='general';
CREATE INDEX IF NOT EXISTS bots_by_user ON bots(user_id);
CREATE TABLE IF NOT EXISTS bot_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user','bot','event')),
    text TEXT NOT NULL,
    blocks TEXT NOT NULL DEFAULT '[]',
    request_id TEXT,
    reply_to INTEGER,
    model TEXT,
    state TEXT NOT NULL DEFAULT 'ok',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS bot_messages_by_bot ON bot_messages(bot_id, id);
CREATE INDEX IF NOT EXISTS bot_messages_by_role ON bot_messages(bot_id, role, id);
CREATE UNIQUE INDEX IF NOT EXISTS bot_messages_reply
    ON bot_messages(bot_id, reply_to) WHERE role='bot';
CREATE TRIGGER IF NOT EXISTS bot_messages_no_update BEFORE UPDATE ON bot_messages
BEGIN SELECT RAISE(ABORT, 'bot messages are append-only'); END;
CREATE TRIGGER IF NOT EXISTS bot_messages_no_delete BEFORE DELETE ON bot_messages
BEGIN SELECT RAISE(ABORT, 'bot messages are append-only'); END;
CREATE TABLE IF NOT EXISTS bot_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    tool TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('started','ok','refused')),
    blocks TEXT NOT NULL DEFAULT '[]',
    UNIQUE (bot_id, request_id, step)
);
CREATE TABLE IF NOT EXISTS bot_pending (
    id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    args TEXT NOT NULL,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','done','cancelled','expired'))
);
CREATE INDEX IF NOT EXISTS bot_pending_by_bot ON bot_pending(bot_id, state);
CREATE TABLE IF NOT EXISTS bot_work (
    bot_id TEXT NOT NULL,
    board TEXT NOT NULL,
    card INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    last_word TEXT,
    last_seq INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bot_id, board, card)
);
CREATE TABLE IF NOT EXISTS bot_forwards (
    bot_id TEXT NOT NULL,
    board TEXT NOT NULL,
    space_message_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    answered_by INTEGER,
    PRIMARY KEY (bot_id, board, space_message_id)
);
CREATE INDEX IF NOT EXISTS bot_forwards_by_post ON bot_forwards(board, space_message_id);
CREATE INDEX IF NOT EXISTS bot_work_by_card ON bot_work(board, card);
CREATE TABLE IF NOT EXISTS bot_leases (
    bot_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    beat_at REAL NOT NULL,
    PRIMARY KEY (bot_id, request_id)
);
CREATE TABLE IF NOT EXISTS bot_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    removed_at TEXT
);
CREATE INDEX IF NOT EXISTS bot_memory_by_bot ON bot_memory(bot_id, id);
CREATE TRIGGER IF NOT EXISTS bot_memory_no_delete BEFORE DELETE ON bot_memory
BEGIN SELECT RAISE(ABORT, 'bot memory is append-only'); END;
"""


# Columns added after the first release; ``_upgrade`` adds them to an existing file.
_ADDED = added_columns() + (
    ("bot_work", "last_word", "TEXT"),
    ("bot_work", "last_seq", "INTEGER NOT NULL DEFAULT 0"),
    ("bot_forwards", "answered_by", "INTEGER"),
)
UPDATE_MAX = 100_000  # the longest space message, so an orchestrator reply is stored whole


_text = bounded


def _name(value):
    return _text(value.strip() if isinstance(value, str) else value, "name", 1, NAME_MAX)


_BOT_KEYS = ("id", "name", "job", "instructions", "kind", "board", "read_upto", "created_at")


def _bot(row) -> dict:
    """The bot's public fields plus who owns it and whether it is shared (for a turn)."""
    return {
        **{key: row[key] for key in _BOT_KEYS},
        "owner_id": row["user_id"],
        "shared": bool(row["shared"]),
        "frozen": row["frozen_at"] is not None,
    }


_message = message


def open_bots(gateway, workspace_id: str) -> "BotStore":
    stores = gateway.__dict__.setdefault("_bot_stores", {})
    if workspace_id not in stores:
        stores[workspace_id] = BotStore(
            gateway.supervisor,
            workspace_id,
            sharing=lambda: sharing_setting.enabled(gateway.db, workspace_id),
        )
    return stores[workspace_id]


class BotStore(SharesMixin):
    def __init__(self, supervisor, workspace_id, sharing=None):
        """``sharing``: callable answering the workspace switch for sharing bots (absent: off)."""
        self._sharing = sharing
        self._supervisor = supervisor
        self._workspace_id = workspace_id
        self._lock = threading.RLock()
        self._conn = None
        self._path = None

    def directory(self) -> Path:
        return Path(self._supervisor.workspace_dir(self._workspace_id)).resolve()

    def close(self):
        with self._lock:
            if self._conn is not None:
                self._conn.close()
            self._conn = self._path = None

    def _connection(self):
        path = Path(self._supervisor.workspace_dir(self._workspace_id)).resolve() / "bots.sqlite3"
        if path != self._path and self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._conn is None:
            with GATE.creating(path):
                path.parent.mkdir(parents=True, exist_ok=True)
                conn = connect(str(path), timeout=5, check_same_thread=False, isolation_level=None)
            try:
                conn.row_factory = row_factory
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA secure_delete=ON")  # forgotten text is overwritten, not left
                conn.executescript(_SCHEMA)
                self._upgrade(conn)
                conn.executescript(SCHEMA_SHARES)
            except Exception:
                conn.close()
                raise
            self._conn, self._path = conn, path
        return self._conn

    @staticmethod
    def _upgrade(conn):
        """Add columns that a file made by an earlier version lacks."""
        for table, column, kind in _ADDED:
            if column not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        conn.executescript(MEMORY_TRIGGER)
        conn.executescript(LEARN_SCHEMA)
        BotStore._blank_removed_notes(conn)
        try:  # SQLite 3.42+: deleted index terms are zeroed too
            conn.execute(
                "INSERT INTO bot_learned_fts(bot_learned_fts, rank) VALUES ('secure-delete', 1)"
            )
        except sqlite3.OperationalError:
            pass

    @staticmethod
    def _blank_removed_notes(conn):
        """Notes removed by an earlier version kept their text; forget means forget."""
        if conn.execute(
            "SELECT 1 FROM bot_memory WHERE removed_at IS NOT NULL AND text<>'' LIMIT 1"
        ).fetchone():
            conn.execute("DROP TRIGGER bot_memory_tombstone_only")
            conn.execute("UPDATE bot_memory SET text='' WHERE removed_at IS NOT NULL")
            conn.executescript(MEMORY_TRIGGER)
        ensure_request_index(conn)

    @contextmanager
    def _tx(self, *, write=True):
        if not self._lock.acquire(timeout=5):
            raise TimeoutError("Bots busy")
        try:
            conn = self._connection()
            if not write:
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

    @staticmethod
    def _own(conn, bot_id, user_id):
        row = (
            conn.execute(
                "SELECT * FROM bots WHERE id=? AND user_id=?", (bot_id, user_id)
            ).fetchone()
            if isinstance(bot_id, str)
            else None
        )
        if row is None:
            raise Missing()
        return row

    @staticmethod
    def _live(conn, user_id):
        return conn.execute(
            "SELECT COUNT(*) FROM bots WHERE user_id=? AND archived_at IS NULL", (user_id,)
        ).fetchone()[0]

    # Bots

    def get(self, bot_id, user_id) -> dict:
        with self._tx(write=False) as conn:
            row = self._own(conn, bot_id, user_id)
            return {**_bot(row), "archived": row["archived_at"] is not None}

    def viewable(self, bot_id, user_id) -> dict:
        """The bot for its owner or a member with a live share (else :class:`Missing`).

        ``get`` stays owner-only; a turn of a shared bot reads the bot through this.
        """
        with self._tx(write=False) as conn:
            row = self._viewer(conn, user_id, bot_id)
            return {**_bot(row), "archived": row["archived_at"] is not None}

    def create(self, user_id, name, job="", instructions="", kind="custom", board=None) -> dict:
        name = _name(name)
        job = _text(job, "job", 0, JOB_MAX)
        instructions = _text(instructions, "instructions", 0, INSTRUCTIONS_MAX)
        if kind not in KINDS or (kind == "project") != (board is not None and kind != "custom"):
            raise Invalid("invalid kind")
        if board is not None and (not isinstance(board, str) or not board):
            raise Invalid("invalid board")
        with self._tx() as conn:
            if self._live(conn, user_id) >= MAX_LIVE_BOTS:
                raise Limit()
            bot_id = uuid.uuid4().hex
            try:
                conn.execute(
                    "INSERT INTO bots (id,user_id,name,job,instructions,kind,board,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (bot_id, user_id, name, job, instructions, kind, board, stamp()),
                )
            except sqlite3.IntegrityError:
                raise Conflict() from None
            return {**_bot(self._own(conn, bot_id, user_id)), "archived": False}

    def ensure_defaults(self, user_id, boards) -> None:
        """One general bot and one project bot per ``(slug, name)``; never recreates an archived one.

        Stops at the live-bot bound; the general bot is created first.
        """
        wanted = [("general", None, "General", "Anything across your projects")] + [
            ("project", slug, name[:NAME_MAX] or slug, "") for slug, name in boards
        ]
        with self._tx() as conn:
            existing = {
                (r["kind"], r["board"])
                for r in conn.execute(
                    "SELECT kind,board FROM bots WHERE user_id=? AND kind<>'custom'", (user_id,)
                )
            }
            live = self._live(conn, user_id)
            for kind, board, name, job in wanted:
                if (kind, board) in existing or live >= MAX_LIVE_BOTS:
                    continue
                conn.execute(
                    "INSERT INTO bots (id,user_id,name,job,kind,board,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, user_id, name, job, kind, board, stamp()),
                )
                live += 1

    def update(self, bot_id, user_id, **fields) -> dict:
        """Change name, job, instructions or ``archived`` (bool) of one bot."""
        changes = {}
        if "name" in fields:
            changes["name"] = _name(fields["name"])
        if "job" in fields:
            changes["job"] = _text(fields["job"], "job", 0, JOB_MAX)
        if "instructions" in fields:
            changes["instructions"] = _text(
                fields["instructions"], "instructions", 0, INSTRUCTIONS_MAX
            )
        if "archived" in fields and type(fields["archived"]) is not bool:
            raise Invalid("archived must be true or false")
        with self._tx() as conn:
            row = self._own(conn, bot_id, user_id)
            if "archived" in fields:
                archived = fields["archived"]
                if not archived and row["archived_at"] is not None:
                    if self._live(conn, user_id) >= MAX_LIVE_BOTS:
                        raise Limit()
                if archived != (row["archived_at"] is not None):
                    changes["archived_at"] = stamp() if archived else None
            if changes:
                conn.execute(
                    "UPDATE bots SET " + ",".join(f"{key}=?" for key in changes) + " WHERE id=?",
                    (*changes.values(), bot_id),
                )
            row = self._own(conn, bot_id, user_id)
            return {**_bot(row), "archived": row["archived_at"] is not None}

    def roster(self, user_id, bot_id=None) -> list[dict]:
        """Live bots (or just ``bot_id``, archived or not) with last message and unread count.

        The person's own bots first, most recent activity first (bots never used come after
        used ones), then the shared bots they hold a live share of: for those the last message
        and the unread count are of the person's own private thread.
        """
        with self._tx(write=False) as conn:
            rows = conn.execute(
                "SELECT b.*, m.id AS last_id, m.role AS last_role, m.text AS last_text, "
                "m.created_at AS last_at, "
                "(SELECT COUNT(*) FROM bot_messages u WHERE u.bot_id=b.id AND u.thread_id IS NULL "
                "AND u.role<>'user' AND u.id>b.read_upto) AS unread "
                "FROM bots b LEFT JOIN bot_messages m ON m.id="
                "(SELECT MAX(id) FROM bot_messages WHERE bot_id=b.id AND thread_id IS NULL) "
                "WHERE b.user_id=? AND (b.id=? OR (? IS NULL AND b.archived_at IS NULL)) "
                "ORDER BY (m.id IS NULL), m.id DESC, b.rowid",
                (user_id, bot_id, bot_id),
            ).fetchall()
            member = self._member_rows(conn, user_id, bot_id) if self._sharing_on() else []
        if bot_id is not None and not rows and not member:
            raise Missing()
        return [self._listed(row, user_id, mine=True) for row in rows] + [
            self._listed(row, user_id, mine=False) for row in member
        ]

    @staticmethod
    def _member_rows(conn, user_id, bot_id):
        return conn.execute(
            "SELECT b.*, s.talk AS s_talk, s.see_shared AS s_see, t.id AS thread, "
            "m.id AS last_id, m.role AS last_role, m.text AS last_text, "
            "m.created_at AS last_at, "
            "(SELECT COUNT(*) FROM bot_messages u WHERE u.bot_id=b.id AND u.thread_id=t.id "
            "AND u.role<>'user' AND u.id>COALESCE((SELECT upto FROM bot_reads r "
            "WHERE r.bot_id=b.id AND r.thread_id=t.id AND r.user_id=?),0)) AS unread "
            "FROM bots b JOIN bot_shares s ON s.bot_id=b.id AND s.user_id=? "
            "AND s.revoked_at IS NULL "
            "LEFT JOIN bot_threads t ON t.bot_id=b.id AND t.user_id=? AND t.visibility='private' "
            "AND s.talk=1 "
            "LEFT JOIN bot_messages m ON m.id=(SELECT MAX(id) FROM bot_messages "
            "WHERE bot_id=b.id AND thread_id=t.id) "
            "WHERE b.shared=1 AND b.frozen_at IS NULL AND b.user_id<>? "
            "AND (b.id=? OR (? IS NULL AND b.archived_at IS NULL)) "
            "ORDER BY (m.id IS NULL), m.id DESC, b.rowid",
            (user_id, user_id, user_id, user_id, bot_id, bot_id),
        ).fetchall()

    @staticmethod
    def _listed(row, user_id, mine) -> dict:
        return {
            **_bot(row),
            "archived": row["archived_at"] is not None,
            "mine": mine,
            "perms": None
            if mine
            else {"talk": bool(row["s_talk"]), "see_shared": bool(row["s_see"])},
            "thread": None if mine else row["thread"],
            "last": {"role": row["last_role"], "text": row["last_text"], "at": row["last_at"]}
            if row["last_id"] is not None
            else None,
            "unread": row["unread"] or 0,
        }

    def mark_read(self, bot_id, user_id, upto, thread_id=None) -> int:
        """Move the read mark forward to ``upto`` (never past the newest message).

        The owner's original thread keeps its mark on the bot; every other thread has one per
        person in ``bot_reads``.
        """
        if type(upto) is not int or not 0 <= upto < 2**63:
            raise Invalid("upto must be a message id")
        with self._tx() as conn:
            thread, _from = self._scope(conn, bot_id, user_id, thread_id)
            newest = conn.execute(
                "SELECT COALESCE(MAX(id),0) FROM bot_messages WHERE bot_id=? AND thread_id IS ?",
                (bot_id, thread),
            ).fetchone()[0]
            if thread is None:
                row = self._own(conn, bot_id, user_id)
                mark = max(row["read_upto"], min(upto, newest))
                conn.execute("UPDATE bots SET read_upto=? WHERE id=?", (mark, bot_id))
                return mark
            seen = conn.execute(
                "SELECT upto FROM bot_reads WHERE bot_id=? AND thread_id=? AND user_id=?",
                (bot_id, thread, user_id),
            ).fetchone()
            mark = max(seen["upto"] if seen else 0, min(upto, newest))
            conn.execute(
                "INSERT INTO bot_reads (bot_id,thread_id,user_id,upto) VALUES (?,?,?,?) "
                "ON CONFLICT(bot_id,thread_id,user_id) DO UPDATE SET upto=excluded.upto",
                (bot_id, thread, user_id, mark),
            )
            return mark

    # Messages

    def _add(self, conn, bot_id, role, text, **fields) -> dict:
        cursor = conn.execute(
            "INSERT INTO bot_messages (bot_id,role,text,blocks,request_id,reply_to,model,state,"
            "created_at,thread_id,author_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                bot_id,
                role,
                text,
                json.dumps(fields.get("blocks") or [], ensure_ascii=False),
                fields.get("request_id"),
                fields.get("reply_to"),
                fields.get("model"),
                fields.get("state", "ok"),
                stamp(),
                fields.get("thread_id"),
                fields.get("author_id"),
            ),
        )
        return _message(
            conn.execute("SELECT * FROM bot_messages WHERE id=?", (cursor.lastrowid,)).fetchone()
        )

    def add_user_message(self, bot_id, user_id, text, request_id, thread_id=None) -> dict:
        """Store a message once per ``request_id`` (per thread). The same text returns the row."""
        _text(text, "text", 1, TEXT_MAX)
        if not isinstance(request_id, str) or REQUEST_ID.fullmatch(request_id) is None:
            raise Invalid("invalid request id")
        with self._tx() as conn:
            thread, _from = self._scope(conn, bot_id, user_id, thread_id, write=True)
            row = conn.execute(
                "SELECT * FROM bot_messages WHERE bot_id=? AND request_id=? AND role='user' "
                "AND thread_id IS ?",
                (bot_id, request_id, thread),
            ).fetchone()
            if row is None:
                return self._add(
                    conn,
                    bot_id,
                    "user",
                    text,
                    request_id=request_id,
                    thread_id=thread,
                    author_id=None if thread is None else user_id,
                )
            if row["text"] != text:
                raise Conflict()
            return _message(row)

    def add_bot_message(
        self,
        bot_id,
        user_id,
        reply_to,
        text,
        blocks=(),
        model=None,
        state="ok",
        learned_used=(),
        thread_id=None,
    ) -> dict:
        """The one reply to user message ``reply_to``; a second reply is a conflict.

        A reply to a turn whose share ended meanwhile is stored only as the fixed
        ``failed:revoked`` reply, in the thread its person opened. ``learned_used``: ids of the
        learned items (the person's, and shared ones of a project they read) this reply's
        request carried; their use count and last use are updated in the same write as the
        reply.
        """
        _text(text, "text", 0, TEXT_MAX * 4)
        if state != "ok" and state not in ("stopped",) and not state.startswith("failed:"):
            raise Invalid("invalid state")
        with self._tx() as conn:
            try:
                thread, _from = self._scope(conn, bot_id, user_id, thread_id)
            except Missing:
                if state != "failed:revoked":
                    raise
                thread = self._opened(conn, bot_id, user_id, thread_id)
            if not conn.execute(
                "SELECT 1 FROM bot_messages WHERE id=? AND bot_id=? AND role='user' "
                "AND thread_id IS ?",
                (reply_to, bot_id, thread),
            ).fetchone():
                raise Invalid("reply_to is not a message of this bot")
            try:
                message = self._add(
                    conn,
                    bot_id,
                    "bot",
                    text,
                    blocks=list(blocks),
                    reply_to=reply_to,
                    model=model,
                    state=state,
                    thread_id=thread,
                )
            except sqlite3.IntegrityError:
                raise Conflict() from None
            if learned_used:
                conn.execute(
                    "UPDATE bot_learned SET use_count=use_count+1, last_used_at=? "
                    "WHERE (user_id=? OR scope='shared') "
                    f"AND id IN ({','.join('?' * len(learned_used))})",
                    (stamp(), user_id, *learned_used),
                )
            return message

    def add_event_message(self, bot_id, user_id, text, blocks=(), thread_id=None) -> dict:
        _text(text, "text", 0, TEXT_MAX)
        with self._tx() as conn:
            thread, _from = self._scope(conn, bot_id, user_id, thread_id, write=True)
            return self._add(conn, bot_id, "event", text, blocks=list(blocks), thread_id=thread)

    def message_for_request(self, bot_id, user_id, request_id, thread_id=None) -> dict | None:
        with self._tx(write=False) as conn:
            thread, _from = self._scope(conn, bot_id, user_id, thread_id)
            row = conn.execute(
                "SELECT * FROM bot_messages WHERE bot_id=? AND request_id=? AND role='user' "
                "AND thread_id IS ?",
                (bot_id, request_id, thread),
            ).fetchone()
            return _message(row) if row else None

    def unanswered(self, bot_id, user_id, thread_id=None) -> list[dict]:
        """User messages of this thread that have no bot reply, oldest first."""
        with self._tx(write=False) as conn:
            thread, _from = self._scope(conn, bot_id, user_id, thread_id)
            return [
                _message(row)
                for row in conn.execute(
                    "SELECT * FROM bot_messages u WHERE u.bot_id=? AND u.role='user' "
                    "AND u.thread_id IS ? "
                    "AND NOT EXISTS (SELECT 1 FROM bot_messages b WHERE b.bot_id=u.bot_id "
                    "AND b.role='bot' AND b.reply_to=u.id) ORDER BY u.id",
                    (bot_id, thread),
                )
            ]

    def reply_to(self, bot_id, user_id, message_id, thread_id=None) -> dict | None:
        """The bot's reply to a message. A person's own thread stays readable here after their
        share ended, so a turn that outlived it can report its fixed reply."""
        with self._tx(write=False) as conn:
            try:
                thread, _from = self._scope(conn, bot_id, user_id, thread_id)
            except Missing:
                thread = self._opened(conn, bot_id, user_id, thread_id)
            row = conn.execute(
                "SELECT * FROM bot_messages WHERE bot_id=? AND role='bot' AND reply_to=? "
                "AND thread_id IS ?",
                (bot_id, message_id, thread),
            ).fetchone()
            return _message(row) if row else None

    def messages(
        self, bot_id, user_id, after=None, before=None, limit=PAGE_DEFAULT, thread_id=None
    ) -> list[dict]:
        """Oldest first. ``after`` pages forward; otherwise the newest ``limit``, older than ``before``.

        ``thread_id`` None is the owner's original thread (a member names their own thread).
        """
        if thread_id is not None or not self._owns(bot_id, user_id):
            return self.thread_messages(bot_id, user_id, thread_id, after, before, limit)
        if type(limit) is not int or not 1 <= limit <= PAGE_MAX:
            raise Invalid("limit must be 1-100")
        for value in (after, before):
            if value is not None and (type(value) is not int or not 0 <= value < 2**63):
                raise Invalid("invalid cursor")
        if after is not None and before is not None:
            raise Invalid("invalid cursor")
        with self._tx(write=False) as conn:
            self._own(conn, bot_id, user_id)
            if after is not None:
                rows = conn.execute(
                    "SELECT * FROM bot_messages WHERE bot_id=? AND thread_id IS NULL AND id>? "
                    "ORDER BY id LIMIT ?",
                    (bot_id, after, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM bot_messages WHERE bot_id=? AND thread_id IS NULL AND id<? "
                    "ORDER BY id DESC LIMIT ?",
                    (bot_id, 2**63 - 1 if before is None else before, limit),
                ).fetchall()[::-1]
        return [_message(row) for row in rows]

    def _owns(self, bot_id, user_id) -> bool:
        with self._tx(write=False) as conn:
            return (
                isinstance(bot_id, str)
                and isinstance(user_id, str)
                and conn.execute(
                    "SELECT 1 FROM bots WHERE id=? AND user_id=?", (bot_id, user_id)
                ).fetchone()
                is not None
            )

    # Running-turn leases: a turn renews its lease while it runs, in whatever process

    def lease_start(self, bot_id, user_id, request_id) -> None:
        with self._tx() as conn:
            self._viewer(conn, user_id, bot_id)
            conn.execute(
                "INSERT OR REPLACE INTO bot_leases (bot_id,request_id,beat_at) VALUES (?,?,?)",
                (bot_id, request_id, time.time()),
            )

    def lease_beat(self, bot_id, request_id) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE bot_leases SET beat_at=? WHERE bot_id=? AND request_id=?",
                (time.time(), bot_id, request_id),
            )

    def lease_end(self, bot_id, request_id) -> None:
        with self._tx() as conn:
            conn.execute(
                "DELETE FROM bot_leases WHERE bot_id=? AND request_id=?", (bot_id, request_id)
            )

    def leased(self, bot_id, request_id, fresh_s) -> bool:
        """True while a turn for this request renewed its lease within ``fresh_s`` seconds."""
        with self._tx(write=False) as conn:
            row = conn.execute(
                "SELECT beat_at FROM bot_leases WHERE bot_id=? AND request_id=?",
                (bot_id, request_id),
            ).fetchone()
        return row is not None and row["beat_at"] > time.time() - fresh_s

    # Action journal

    def action_start(self, bot_id, user_id, request_id, step, tool, args_hash) -> int:
        with self._tx() as conn:
            self._viewer(conn, user_id, bot_id)
            try:
                return conn.execute(
                    "INSERT INTO bot_actions (bot_id,request_id,step,tool,args_hash,state) "
                    "VALUES (?,?,?,?,?,'started')",
                    (bot_id, request_id, step, tool, args_hash),
                ).lastrowid
            except sqlite3.IntegrityError:
                raise Conflict() from None

    def action_finish(self, bot_id, user_id, request_id, step, state, blocks=()) -> bool:
        """``started`` -> ``ok`` or ``refused``; False when the row was not still started."""
        if state not in ("ok", "refused"):
            raise Invalid("invalid state")
        with self._tx() as conn:
            self._journal(conn, bot_id, user_id)
            return (
                conn.execute(
                    "UPDATE bot_actions SET state=?,blocks=? WHERE bot_id=? AND request_id=? "
                    "AND step=? AND state='started'",
                    (state, json.dumps(list(blocks), ensure_ascii=False), bot_id, request_id, step),
                ).rowcount
                == 1
            )

    def actions(self, bot_id, user_id, request_id) -> list[dict]:
        with self._tx(write=False) as conn:
            self._journal(conn, bot_id, user_id)
            return [
                {
                    "step": r["step"],
                    "tool": r["tool"],
                    "args_hash": r["args_hash"],
                    "state": r["state"],
                    "blocks": json.loads(r["blocks"]),
                }
                for r in conn.execute(
                    "SELECT * FROM bot_actions WHERE bot_id=? AND request_id=? ORDER BY step",
                    (bot_id, request_id),
                )
            ]

    # Pending confirmations

    @staticmethod
    def _pending(row) -> dict:
        data = {key: row[key] for key in row.keys()}
        data["args"] = json.loads(data["args"])
        return data

    def pending_create(
        self, bot_id, user_id, tool, args, label, ttl_s=PENDING_TTL_S, thread_id=None
    ) -> dict:
        now = datetime.now(timezone.utc)
        with self._tx() as conn:
            self._viewer(conn, user_id, bot_id)
            pending_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO bot_pending (id,bot_id,user_id,tool,args,label,created_at,expires_at,"
                "thread_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    pending_id,
                    bot_id,
                    user_id,
                    tool,
                    json.dumps(args, ensure_ascii=False),
                    label,
                    stamp(now),
                    stamp(now + timedelta(seconds=ttl_s)),
                    thread_id,
                ),
            )
            return self._pending(
                conn.execute("SELECT * FROM bot_pending WHERE id=?", (pending_id,)).fetchone()
            )

    def pending_get(self, bot_id, user_id, pending_id) -> dict | None:
        with self._tx(write=False) as conn:
            self._viewer(conn, user_id, bot_id)
            row = conn.execute(
                "SELECT * FROM bot_pending WHERE id=? AND bot_id=?", (pending_id, bot_id)
            ).fetchone()
            return self._pending(row) if row else None

    def pending_for(self, user_id, pending_id) -> dict | None:
        """This person's own pending action on a bot they reach (for hydrating a confirm block)."""
        if not isinstance(pending_id, str):
            return None
        with self._tx(write=False) as conn:
            row = conn.execute(
                "SELECT * FROM bot_pending WHERE id=? AND user_id=?", (pending_id, user_id)
            ).fetchone()
            try:
                if row is not None:
                    self._viewer(conn, user_id, row["bot_id"])
            except Missing:
                return None
            return self._pending(row) if row else None

    def pending_resolve(self, bot_id, user_id, pending_id, state, now=None) -> bool:
        """Compare-and-set ``open`` -> ``done``, ``cancelled`` or ``expired``.

        An open row past its expiry can only become ``expired``; the call then returns False.
        """
        if state not in ("done", "cancelled", "expired"):
            raise Invalid("invalid state")
        at = stamp(now)
        with self._tx() as conn:
            self._viewer(conn, user_id, bot_id)
            if state != "expired":
                conn.execute(
                    "UPDATE bot_pending SET state='expired' WHERE id=? AND bot_id=? "
                    "AND state='open' AND expires_at<=?",
                    (pending_id, bot_id, at),
                )
            return (
                conn.execute(
                    "UPDATE bot_pending SET state=? WHERE id=? AND bot_id=? AND state='open'",
                    (state, pending_id, bot_id),
                ).rowcount
                == 1
            )

    # Work the bot filed

    def work_add(self, bot_id, user_id, board, card, message_id, thread_id=None) -> None:
        """Remember a card this person's turn filed: its events go to that person's thread."""
        with self._tx() as conn:
            self._viewer(conn, user_id, bot_id)
            conn.execute(
                "INSERT OR IGNORE INTO bot_work (bot_id,board,card,message_id,user_id,thread_id) "
                "VALUES (?,?,?,?,?,?)",
                (bot_id, board, card, message_id, user_id, thread_id),
            )

    def work_list(self, bot_id, user_id) -> list[dict]:
        """The work this person filed through the bot (never another person's)."""
        with self._tx(write=False) as conn:
            self._viewer(conn, user_id, bot_id)
            return [
                {"board": r["board"], "card": r["card"], "message_id": r["message_id"]}
                for r in conn.execute(
                    "SELECT w.* FROM bot_work w JOIN bots b ON b.id=w.bot_id WHERE w.bot_id=? "
                    "AND COALESCE(w.user_id, b.user_id)=? ORDER BY w.message_id,w.card",
                    (bot_id, user_id),
                )
            ]

    def bots_for_card(self, board, card) -> list[dict]:
        """Every bot that filed ``card``: ``{bot_id, user_id, thread_id, message_id}``, oldest
        first. ``user_id`` is the person whose turn filed it (the bot's owner for older rows)."""
        return self._lookup("bot_work", "card", board, card)

    def work_changed(
        self, bot_id, user_id, board, card, seq, word, text=None, blocks=(), thread_id=None
    ) -> dict | None:
        """Apply hub event ``seq`` to a filed card: remember its status word, post ``text`` if it changed.

        Only an event newer than the last one applied counts, and the check, the marker and the
        message are one write. A redelivered, stale or racing handler is a no-op, and a repeated
        event for the same word adds no second message. The message goes to the thread of the
        person who filed the card, and only while they still reach it. Returns the new event
        message, if any.
        """
        try:
            with self._tx(write=False) as conn:
                self._scope(conn, bot_id, user_id, thread_id, write=True)
                row = conn.execute(
                    "SELECT last_seq FROM bot_work WHERE bot_id=? AND board=? AND card=?",
                    (bot_id, board, card),
                ).fetchone()
            if row is None or row["last_seq"] >= seq:
                return None
            with self._tx() as conn:
                thread, _from = self._scope(conn, bot_id, user_id, thread_id, write=True)
                row = conn.execute(
                    "SELECT last_word FROM bot_work WHERE bot_id=? AND board=? AND card=? "
                    "AND last_seq<?",
                    (bot_id, board, card, seq),
                ).fetchone()
                if row is None:
                    return None
                conn.execute(
                    "UPDATE bot_work SET last_word=?, last_seq=? "
                    "WHERE bot_id=? AND board=? AND card=?",
                    (word, seq, bot_id, board, card),
                )
                if row["last_word"] != word and text is not None:
                    return self._add(
                        conn, bot_id, "event", text, blocks=list(blocks), thread_id=thread
                    )
        except Missing:
            return None  # the person's share (or the bot) ended: the event is dropped
        return None

    def work_by_bot(self, user_id) -> dict[str, list[tuple[str, int]]]:
        """``{bot_id: [(board, card), ...]}`` of unfinished work for this person's live bots."""
        with self._tx(write=False) as conn:
            found: dict[str, list[tuple[str, int]]] = {}
            for r in conn.execute(
                "SELECT w.bot_id, w.board, w.card FROM bot_work w JOIN bots b ON b.id=w.bot_id "
                "WHERE COALESCE(w.user_id, b.user_id)=? AND b.archived_at IS NULL "
                "AND w.last_word IS NOT 'done' "
                "ORDER BY w.bot_id, w.card",
                (user_id,),
            ):
                found.setdefault(r["bot_id"], []).append((r["board"], r["card"]))
            return found

    # Posts forwarded to the space chat

    def forward_add(
        self, bot_id, user_id, board, space_message_id, message_id, thread_id=None
    ) -> None:
        with self._tx() as conn:
            self._viewer(conn, user_id, bot_id)
            conn.execute(
                "INSERT OR IGNORE INTO bot_forwards "
                "(bot_id,board,space_message_id,message_id,user_id,thread_id) VALUES (?,?,?,?,?,?)",
                (bot_id, board, space_message_id, message_id, user_id, thread_id),
            )

    def forward_list(self, bot_id, user_id) -> list[dict]:
        """The posts this person forwarded through the bot (never another person's)."""
        with self._tx(write=False) as conn:
            self._viewer(conn, user_id, bot_id)
            return [
                {
                    "board": r["board"],
                    "space_message_id": r["space_message_id"],
                    "message_id": r["message_id"],
                }
                for r in conn.execute(
                    "SELECT t.* FROM bot_forwards t JOIN bots b ON b.id=t.bot_id WHERE t.bot_id=? "
                    "AND COALESCE(t.user_id, b.user_id)=? ORDER BY t.message_id,t.space_message_id",
                    (bot_id, user_id),
                )
            ]

    def bots_for_post(self, board, space_message_id) -> list[dict]:
        """Every bot that forwarded this space post: ``{bot_id, user_id, thread_id, message_id}``."""
        return self._lookup("bot_forwards", "space_message_id", board, space_message_id)

    def unanswered_forwards(self, board, before) -> list[dict]:
        """Forwarded posts on ``board`` older than message ``before`` that no reply answered yet."""
        with self._tx(write=False) as conn:
            return [
                {
                    "bot_id": r["bot_id"],
                    "user_id": r["user_id"],
                    "thread_id": r["thread_id"],
                    "message_id": r["message_id"],
                    "space_message_id": r["space_message_id"],
                }
                for r in conn.execute(
                    "SELECT t.bot_id, COALESCE(t.user_id, b.user_id) AS user_id, t.thread_id, "
                    "t.message_id, t.space_message_id FROM bot_forwards t "
                    "JOIN bots b ON b.id=t.bot_id WHERE t.board=? AND t.space_message_id<? "
                    "AND t.answered_by IS NULL ORDER BY t.message_id, t.bot_id",
                    (board, before),
                )
            ]

    def forward_answered(
        self, bot_id, user_id, board, reply_id, posts, text, blocks=(), thread_id=None
    ) -> dict | None:
        """The coordinator's reply ``reply_id`` answering this bot's forwarded ``posts``, as one event.

        Only posts no reply answered yet are taken, and they are marked in the same write as the
        message, so a redelivered, racing or later reply adds nothing and never takes them over.
        """
        _text(text, "text", 0, UPDATE_MAX)
        try:
            return self._forward_answered(
                bot_id, user_id, board, reply_id, posts, text, blocks, thread_id
            )
        except Missing:
            return None  # the person's share (or the bot) ended: the reply is dropped

    def _forward_answered(self, bot_id, user_id, board, reply_id, posts, text, blocks, thread_id):
        with self._tx() as conn:
            thread, _from = self._scope(conn, bot_id, user_id, thread_id, write=True)
            marked = sum(
                conn.execute(
                    "UPDATE bot_forwards SET answered_by=? WHERE bot_id=? AND board=? "
                    "AND space_message_id=? AND space_message_id<? AND answered_by IS NULL",
                    (reply_id, bot_id, board, post, reply_id),
                ).rowcount
                for post in posts
            )
            if not marked:
                return None
            return self._add(conn, bot_id, "event", text, blocks=list(blocks), thread_id=thread)

    def _lookup(self, table, column, board, value) -> list[dict]:
        # Internal readers (updates, the front agent) have no user yet; they get the owner from the bot.
        with self._tx(write=False) as conn:
            return [
                {
                    "bot_id": r["bot_id"],
                    "user_id": r["user_id"],
                    "thread_id": r["thread_id"],
                    "message_id": r["message_id"],
                }
                for r in conn.execute(
                    f"SELECT t.bot_id, COALESCE(t.user_id, b.user_id) AS user_id, t.thread_id, "
                    f"t.message_id FROM {table} t "
                    f"JOIN bots b ON b.id=t.bot_id WHERE t.board=? AND t.{column}=? "
                    "ORDER BY t.message_id, t.bot_id",
                    (board, value),
                )
            ]

    # Memory

    def memory_add(self, bot_id, user_id, text) -> dict:
        """A note: the owner's is bot-level (every turn reads it), a member's is theirs alone."""
        _text(text, "text", 1, NOTE_MAX)
        with self._tx() as conn:
            bot = self._viewer(conn, user_id, bot_id)
            at = stamp()
            memory_id = conn.execute(
                "INSERT INTO bot_memory (bot_id,text,created_at,owner_id) VALUES (?,?,?,?)",
                (bot_id, text, at, None if bot["user_id"] == user_id else user_id),
            ).lastrowid
            return {"id": memory_id, "text": text, "at": at}

    def memory_list(self, bot_id, user_id, limit=None, own=False) -> list[dict]:
        """Bot-level notes; with ``own`` a member's own notes (the owner's are bot-level)."""
        with self._tx(write=False) as conn:
            bot = self._viewer(conn, user_id, bot_id)
            holder = user_id if own and bot["user_id"] != user_id else None
            return [
                {"id": r["id"], "text": r["text"], "at": r["created_at"]}
                for r in conn.execute(
                    "SELECT * FROM bot_memory WHERE bot_id=? AND removed_at IS NULL "
                    "AND owner_id IS ? ORDER BY id LIMIT ?",
                    (bot_id, holder, -1 if limit is None else limit),
                )
            ]

    def memory_for_turn(self, bot_id, user_id) -> list[dict]:
        """What one person's turn reads: the bot-level notes and that person's own notes only."""
        with self._tx(write=False) as conn:
            self._viewer(conn, user_id, bot_id)
            return [
                {"id": r["id"], "text": r["text"], "at": r["created_at"]}
                for r in conn.execute(
                    "SELECT * FROM bot_memory WHERE bot_id=? AND removed_at IS NULL "
                    "AND (owner_id IS NULL OR owner_id=?) ORDER BY id",
                    (bot_id, user_id),
                )
            ]

    def memory_remove(self, bot_id, user_id, memory_id, own=False) -> bool:
        """Forget one note: a bot-level note (owner) or, with ``own``, one of the member's own."""
        with self._tx() as conn:
            bot = self._viewer(conn, user_id, bot_id)
            is_owner = bot["user_id"] == user_id
            if not is_owner and not own:
                return False
            return (
                type(memory_id) is int
                and 0 < memory_id < 2**63
                and conn.execute(
                    "UPDATE bot_memory SET removed_at=?, text='' WHERE id=? AND bot_id=? "
                    "AND removed_at IS NULL AND owner_id IS ?",
                    (stamp(), memory_id, bot_id, None if is_owner else user_id),
                ).rowcount
                == 1
            )
