"""Sharing a bot with workspace members: tables, ``reach`` and the store-level share/access API.

Design: ``docs/design/shared-team-bots.md`` (sections 3 to 6). This is the store foundation only:
nothing here is reached by a route or a tool yet, and a bot that is not shared behaves as before.

Every read and write of a thread goes through :meth:`SharesMixin._reach`. The caller (a route in a
later slice) checks workspace membership; the store checks the share, the workspace switch and
the thread. A share that is not live, a switch that is off and a thread of someone else all look
like an unknown id (:class:`Missing`). Errors never carry conversation text.
"""

import sqlite3
import uuid
from dataclasses import dataclass

from commonplace.bots.errors import (
    PAGE_DEFAULT,
    PAGE_MAX,
    REQUEST_ID,
    TEXT_MAX,
    Conflict,
    Invalid,
    Limit,
    Missing,
    SharingOff,
    Unsupported,
    bounded,
    message,
    stamp,
)

MAX_SHARED_BOTS = 20  # per owner
MAX_SHARES = 50  # live shares per bot
MAX_SEE_SHARED = 25  # live members with see_shared per bot
MAX_ACCESS_ENTRIES = 200
REF_MAX, TITLE_MAX = 200, 60
GRANTABLE = {"project": ("read", "write"), "machine": ("use",), "tool": ("use",)}
REVOKE_REASONS = ("owner", "member_removed", "unshared", "disabled")

SCHEMA_SHARES = """
CREATE TABLE IF NOT EXISTS bot_shares (
    bot_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    talk INTEGER NOT NULL DEFAULT 0,
    see_shared INTEGER NOT NULL DEFAULT 0,
    shared_by TEXT NOT NULL,
    shared_at TEXT NOT NULL,
    history_from INTEGER NOT NULL,
    revoked_at TEXT,
    revoked_by TEXT,
    revoke_reason TEXT CHECK (revoke_reason IN ('owner','member_removed','unshared','disabled')),
    PRIMARY KEY (bot_id, user_id),
    CHECK (talk = 1 OR see_shared = 1 OR revoked_at IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS bot_threads (
    id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility IN ('private','shared')),
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS bot_threads_private ON bot_threads(bot_id, user_id)
    WHERE visibility = 'private';
CREATE INDEX IF NOT EXISTS bot_threads_by_bot ON bot_threads(bot_id, visibility);
CREATE TABLE IF NOT EXISTS bot_reads (
    bot_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    upto INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bot_id, thread_id, user_id)
);
CREATE TABLE IF NOT EXISTS bot_access (
    bot_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('project','wing','machine','tool')),
    ref TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('read','write','use')),
    set_by TEXT NOT NULL,
    set_at TEXT NOT NULL,
    PRIMARY KEY (bot_id, kind, ref)
);
CREATE TABLE IF NOT EXISTS bot_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (
        action IN ('grant','change','remove','share','unshare','permissions','freeze','adopt')),
    kind TEXT,
    ref TEXT,
    mode_before TEXT,
    mode_after TEXT,
    member_id TEXT,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS bot_access_log_by_bot ON bot_access_log(bot_id, id);
CREATE TRIGGER IF NOT EXISTS bot_access_log_no_update BEFORE UPDATE ON bot_access_log
BEGIN SELECT RAISE(ABORT, 'bot access log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS bot_access_log_no_delete BEFORE DELETE ON bot_access_log
BEGIN SELECT RAISE(ABORT, 'bot access log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS bots_shared_is_custom BEFORE UPDATE OF shared ON bots
WHEN NEW.shared = 1 AND NEW.kind <> 'custom'
BEGIN SELECT RAISE(ABORT, 'only custom bots can be shared'); END;
"""

# Columns the sharing design adds to tables of the first release.
_ADDED = (
    ("bots", "shared", "INTEGER NOT NULL DEFAULT 0 CHECK (shared IN (0,1))"),
    ("bots", "access_version", "INTEGER NOT NULL DEFAULT 0"),
    ("bots", "frozen_at", "TEXT"),
    ("bot_messages", "thread_id", "TEXT"),
    ("bot_messages", "author_id", "TEXT"),
    ("bot_memory", "owner_id", "TEXT"),
    ("bot_work", "user_id", "TEXT"),
    ("bot_work", "thread_id", "TEXT"),
    ("bot_forwards", "user_id", "TEXT"),
    ("bot_forwards", "thread_id", "TEXT"),
    ("bot_pending", "thread_id", "TEXT"),
)
_REQUEST_INDEX = (
    "CREATE UNIQUE INDEX bot_messages_request "
    "ON bot_messages(bot_id, COALESCE(thread_id, ''), request_id) WHERE role = 'user'"
)


def added_columns():
    return _ADDED


def ensure_request_index(conn) -> None:
    """Make the request-id index per thread (an older file has it per bot only)."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='bot_messages_request'"
    ).fetchone()
    if row is not None and "thread_id" in row["sql"]:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DROP INDEX IF EXISTS bot_messages_request")
        conn.execute(_REQUEST_INDEX)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


@dataclass(frozen=True)
class Reach:
    """What one person may do in one thread of one bot (the answer of ``BotStore.reach``).

    ``kind``: ``owner`` (the original thread of the bot's owner, ``thread_id`` None), ``private``
    (a member's own thread) or ``shared``. ``read_from`` is the first message id the person may
    read in a shared thread (the share's ``history_from``; 0 for the owner and private threads).
    """

    kind: str
    bot_id: str
    thread_id: str | None
    owner: bool
    read_from: int
    can_write: bool
    frozen: bool = False


def _share(row) -> dict:
    return {
        "user_id": row["user_id"],
        "talk": bool(row["talk"]),
        "see_shared": bool(row["see_shared"]),
        "shared_by": row["shared_by"],
        "shared_at": row["shared_at"],
        "history_from": row["history_from"],
        "revoked_at": row["revoked_at"],
        "revoke_reason": row["revoke_reason"],
    }


def _thread(row) -> dict:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "visibility": row["visibility"],
        "title": row["title"],
        "created_at": row["created_at"],
        "archived": row["archived_at"] is not None,
    }


def _entry(row) -> dict:
    return {key: row[key] for key in ("kind", "ref", "mode", "set_by", "set_at")}


def _check_entries(entries) -> dict:
    """``{(kind, ref): mode}`` of a well-formed list; fixed-text errors."""
    if not isinstance(entries, list) or len(entries) > MAX_ACCESS_ENTRIES:
        raise Invalid("entries must be a list of at most 200")
    wanted = {}
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"kind", "ref", "mode"}:
            raise Invalid("invalid entry")
        kind, ref, mode = item["kind"], item["ref"], item["mode"]
        if kind == "wing":
            raise Unsupported("unsupported_kind")
        if kind not in GRANTABLE or mode not in GRANTABLE[kind]:
            raise Invalid("invalid entry")
        bounded(ref, "ref", 1, REF_MAX)
        if (kind, ref) in wanted:
            raise Invalid("duplicate entry")
        wanted[(kind, ref)] = mode
    return wanted


class SharesMixin:
    """The sharing half of :class:`commonplace.bots.store.BotStore`."""

    _sharing = None  # callable returning the workspace switch; absent means off (fails closed)

    def _sharing_on(self) -> bool:
        try:
            return bool(self._sharing()) if self._sharing is not None else False
        except Exception:
            return False

    # Reach

    def _live_share(self, conn, bot_id, user_id):
        return conn.execute(
            "SELECT * FROM bot_shares WHERE bot_id=? AND user_id=? AND revoked_at IS NULL",
            (bot_id, user_id),
        ).fetchone()

    def _reach(self, conn, user_id, bot_id, thread_id=None) -> Reach:
        if not isinstance(bot_id, str) or not isinstance(user_id, str):
            raise Missing()
        if thread_id is not None and not isinstance(thread_id, str):
            raise Missing()
        bot = conn.execute("SELECT * FROM bots WHERE id=?", (bot_id,)).fetchone()
        if bot is None:
            raise Missing()
        owner = bot["user_id"] == user_id
        share = None
        if not owner:
            if not bot["shared"] or bot["frozen_at"] is not None or not self._sharing_on():
                raise Missing()
            share = self._live_share(conn, bot_id, user_id)
            if share is None:
                raise Missing()
        frozen = bot["frozen_at"] is not None
        live = bot["archived_at"] is None and not frozen
        if thread_id is None and owner:
            return Reach("owner", bot_id, None, True, 0, live, frozen)
        if thread_id is None:
            row = conn.execute(
                "SELECT * FROM bot_threads WHERE bot_id=? AND user_id=? AND visibility='private'",
                (bot_id, user_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM bot_threads WHERE id=? AND bot_id=?", (thread_id, bot_id)
            ).fetchone()
        if row is None:
            raise Missing()
        live = live and row["archived_at"] is None
        if row["visibility"] == "private":
            if row["user_id"] != user_id or (not owner and not share["talk"]):
                raise Missing()
            return Reach("private", bot_id, row["id"], owner, 0, live, frozen)
        if owner:
            return Reach("shared", bot_id, row["id"], True, 0, live, frozen)
        opened = row["user_id"] == user_id
        if not (share["see_shared"] or (share["talk"] and opened)):
            raise Missing()
        can_write = bool(share["talk"]) and (opened or bool(share["see_shared"])) and live
        return Reach("shared", bot_id, row["id"], False, share["history_from"], can_write, frozen)

    def reach(self, user_id, bot_id, thread_id=None) -> Reach:
        """What ``user_id`` may do in ``thread_id`` of ``bot_id``; :class:`Missing` when nothing.

        ``thread_id`` None is the caller's own private thread (the owner's original thread, or
        a member's thread once opened).
        """
        with self._tx(write=False) as conn:
            return self._reach(conn, user_id, bot_id, thread_id)

    def _viewer(self, conn, user_id, bot_id):
        """The bot row for its owner or a member with a live share (else :class:`Missing`)."""
        if not isinstance(bot_id, str) or not isinstance(user_id, str):
            raise Missing()
        bot = conn.execute("SELECT * FROM bots WHERE id=?", (bot_id,)).fetchone()
        if bot is None:
            raise Missing()
        if bot["user_id"] != user_id and (
            not bot["shared"]
            or bot["frozen_at"] is not None
            or not self._sharing_on()
            or self._live_share(conn, bot_id, user_id) is None
        ):
            raise Missing()
        return bot

    # Shares

    @staticmethod
    def _log(conn, bot_id, version, actor, action, **fields):
        conn.execute(
            "INSERT INTO bot_access_log (bot_id,version,actor_id,action,kind,ref,mode_before,"
            "mode_after,member_id,at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                bot_id,
                version,
                actor,
                action,
                fields.get("kind"),
                fields.get("ref"),
                fields.get("before"),
                fields.get("after"),
                fields.get("member"),
                stamp(),
            ),
        )

    def share(self, bot_id, owner_id, member_id, talk, see_shared) -> dict:
        """Share a custom bot with ``member_id`` (or change the live share's flags).

        Idempotent: the same flags again change nothing and log nothing. A new share starts
        its shared-thread history at the newest message (an old row is never restored). The
        first share of a bot with a ``board`` seeds one ``project:read`` grant for it.
        """
        if type(talk) is not bool or type(see_shared) is not bool or not (talk or see_shared):
            raise Invalid("a share needs talk or see_shared")
        if not isinstance(member_id, str) or not member_id or member_id == owner_id:
            raise Invalid("invalid member")
        if not self._sharing_on():
            raise SharingOff()
        with self._tx() as conn:
            bot = self._own(conn, bot_id, owner_id)
            if bot["kind"] != "custom":
                raise Invalid("only custom bots can be shared")
            row = self._live_share(conn, bot_id, member_id)
            if row is not None and (bool(row["talk"]), bool(row["see_shared"])) == (
                talk,
                see_shared,
            ):
                return _share(row)
            if see_shared and not (row is not None and row["see_shared"]):
                if (
                    conn.execute(
                        "SELECT COUNT(*) FROM bot_shares WHERE bot_id=? AND see_shared=1 "
                        "AND revoked_at IS NULL",
                        (bot_id,),
                    ).fetchone()[0]
                    >= MAX_SEE_SHARED
                ):
                    raise Limit()
            version = bot["access_version"]
            if row is not None:
                conn.execute(
                    "UPDATE bot_shares SET talk=?, see_shared=? WHERE bot_id=? AND user_id=?",
                    (int(talk), int(see_shared), bot_id, member_id),
                )
                self._log(conn, bot_id, version, owner_id, "permissions", member=member_id)
                return _share(self._live_share(conn, bot_id, member_id))
            if (
                conn.execute(
                    "SELECT COUNT(*) FROM bot_shares WHERE bot_id=? AND revoked_at IS NULL",
                    (bot_id,),
                ).fetchone()[0]
                >= MAX_SHARES
            ):
                raise Limit()
            if not bot["shared"]:
                if (
                    conn.execute(
                        "SELECT COUNT(*) FROM bots WHERE user_id=? AND shared=1", (owner_id,)
                    ).fetchone()[0]
                    >= MAX_SHARED_BOTS
                ):
                    raise Limit()
                conn.execute("UPDATE bots SET shared=1 WHERE id=?", (bot_id,))
                if bot["board"] is not None:
                    version += 1
                    conn.execute("UPDATE bots SET access_version=? WHERE id=?", (version, bot_id))
                    conn.execute(
                        "INSERT OR IGNORE INTO bot_access (bot_id,kind,ref,mode,set_by,set_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (bot_id, "project", bot["board"], "read", owner_id, stamp()),
                    )
                    self._log(
                        conn,
                        bot_id,
                        version,
                        owner_id,
                        "grant",
                        kind="project",
                        ref=bot["board"],
                        after="read",
                    )
            history = conn.execute(
                "SELECT COALESCE(MAX(id),0) FROM bot_messages WHERE bot_id=?", (bot_id,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO bot_shares (bot_id,user_id,talk,see_shared,shared_by,shared_at,"
                "history_from) VALUES (?,?,?,?,?,?,?) ON CONFLICT(bot_id,user_id) DO UPDATE SET "
                "talk=excluded.talk, see_shared=excluded.see_shared, "
                "shared_by=excluded.shared_by, shared_at=excluded.shared_at, "
                "history_from=excluded.history_from, revoked_at=NULL, revoked_by=NULL, "
                "revoke_reason=NULL",
                (bot_id, member_id, int(talk), int(see_shared), owner_id, stamp(), history),
            )
            self._log(conn, bot_id, version, owner_id, "share", member=member_id)
            return _share(self._live_share(conn, bot_id, member_id))

    def revoke(self, bot_id, owner_id, member_id, reason="owner") -> bool:
        """End a share. True when a live share ended; revoking again is a no-op (False)."""
        if reason not in REVOKE_REASONS:
            raise Invalid("invalid reason")
        with self._tx() as conn:
            bot = self._own(conn, bot_id, owner_id)
            if not isinstance(member_id, str) or self._live_share(conn, bot_id, member_id) is None:
                return False
            conn.execute(
                "UPDATE bot_shares SET revoked_at=?, revoked_by=?, revoke_reason=? "
                "WHERE bot_id=? AND user_id=? AND revoked_at IS NULL",
                (stamp(), owner_id, reason, bot_id, member_id),
            )
            self._log(conn, bot_id, bot["access_version"], owner_id, "unshare", member=member_id)
            return True

    def shares(self, bot_id, user_id, include_revoked=False) -> list[dict]:
        """The owner sees every share; a member sees only their own live row."""
        with self._tx(write=False) as conn:
            bot = self._viewer(conn, user_id, bot_id)
            if bot["user_id"] != user_id:
                return [_share(self._live_share(conn, bot_id, user_id))]
            return [
                _share(r)
                for r in conn.execute(
                    "SELECT * FROM bot_shares WHERE bot_id=? "
                    + ("" if include_revoked else "AND revoked_at IS NULL ")
                    + "ORDER BY shared_at, user_id",
                    (bot_id,),
                )
            ]

    def shared_state(self, bot_id, owner_id) -> dict:
        """``{shared, frozen, access_version}`` of one of the owner's bots."""
        with self._tx(write=False) as conn:
            bot = self._own(conn, bot_id, owner_id)
            return {
                "shared": bool(bot["shared"]),
                "frozen": bot["frozen_at"] is not None,
                "access_version": bot["access_version"],
            }

    # Access: what the bot may reach, set only by its owner

    def access_get(self, bot_id, user_id) -> dict:
        with self._tx(write=False) as conn:
            bot = self._viewer(conn, user_id, bot_id)
            return {
                "version": bot["access_version"],
                "entries": [
                    _entry(r)
                    for r in conn.execute(
                        "SELECT * FROM bot_access WHERE bot_id=? ORDER BY kind, ref", (bot_id,)
                    )
                ],
            }

    def access_set(self, bot_id, owner_id, version, entries) -> dict:
        """Replace the access list if ``version`` is current (else :class:`Conflict`).

        One log row per changed entry and one version step per real change; a list equal to the
        stored one writes nothing. Only a shared bot has a list.
        """
        if type(version) is not int:
            raise Invalid("invalid version")
        wanted = _check_entries(entries)
        with self._tx() as conn:
            bot = self._own(conn, bot_id, owner_id)
            if not bot["shared"]:
                raise Invalid("bot is not shared")
            if bot["access_version"] != version:
                raise Conflict()
            stored = {
                (r["kind"], r["ref"]): r["mode"]
                for r in conn.execute("SELECT * FROM bot_access WHERE bot_id=?", (bot_id,))
            }
            changes = [
                ("remove" if key not in wanted else "change" if key in stored else "grant", key)
                for key in sorted(set(stored) | set(wanted))
                if stored.get(key) != wanted.get(key)
            ]
            if changes:
                new = version + 1
                at = stamp()
                for action, (kind, ref) in changes:
                    if action == "remove":
                        conn.execute(
                            "DELETE FROM bot_access WHERE bot_id=? AND kind=? AND ref=?",
                            (bot_id, kind, ref),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO bot_access (bot_id,kind,ref,mode,set_by,set_at) "
                            "VALUES (?,?,?,?,?,?) ON CONFLICT(bot_id,kind,ref) DO UPDATE SET "
                            "mode=excluded.mode, set_by=excluded.set_by, set_at=excluded.set_at",
                            (bot_id, kind, ref, wanted[(kind, ref)], owner_id, at),
                        )
                    self._log(
                        conn,
                        bot_id,
                        new,
                        owner_id,
                        action,
                        kind=kind,
                        ref=ref,
                        before=stored.get((kind, ref)),
                        after=wanted.get((kind, ref)),
                    )
                conn.execute("UPDATE bots SET access_version=? WHERE id=?", (new, bot_id))
        return self.access_get(bot_id, owner_id)

    def access_log(self, bot_id, user_id, after=0, limit=PAGE_DEFAULT) -> list[dict]:
        """Log rows after id ``after``, oldest first (owner and members; ids and codes only)."""
        if type(limit) is not int or not 1 <= limit <= PAGE_MAX:
            raise Invalid("limit must be 1-100")
        if type(after) is not int or not 0 <= after < 2**63:
            raise Invalid("invalid cursor")
        with self._tx(write=False) as conn:
            self._viewer(conn, user_id, bot_id)
            return [
                {key: r[key] for key in r.keys() if key != "bot_id"}
                for r in conn.execute(
                    "SELECT * FROM bot_access_log WHERE bot_id=? AND id>? ORDER BY id LIMIT ?",
                    (bot_id, after, limit),
                )
            ]

    # Threads

    def thread_open(self, bot_id, user_id, visibility, title="") -> dict:
        """Open ``user_id``'s private thread (idempotent) or a new shared thread.

        A member needs a live share with ``talk``. The owner has the original thread and
        needs no private row, so a private thread for the owner is refused.
        """
        if visibility not in ("private", "shared"):
            raise Invalid("invalid visibility")
        bounded(title, "title", 0, TITLE_MAX)
        with self._tx() as conn:
            bot = self._viewer(conn, user_id, bot_id)
            owner = bot["user_id"] == user_id
            if visibility == "private" and owner:
                raise Invalid("the owner uses the original thread")
            if not owner and not self._live_share(conn, bot_id, user_id)["talk"]:
                raise Missing()
            if bot["archived_at"] is not None or bot["frozen_at"] is not None:
                raise Invalid("bot is not open")
            if visibility == "private":
                row = conn.execute(
                    "SELECT * FROM bot_threads WHERE bot_id=? AND user_id=? "
                    "AND visibility='private'",
                    (bot_id, user_id),
                ).fetchone()
                if row is not None:
                    return _thread(row)
            thread_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO bot_threads (id,bot_id,user_id,visibility,title,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (thread_id, bot_id, user_id, visibility, title, stamp()),
            )
            return _thread(
                conn.execute("SELECT * FROM bot_threads WHERE id=?", (thread_id,)).fetchone()
            )

    def threads(self, bot_id, user_id) -> list[dict]:
        """The threads this person reaches, oldest first (the owner's original thread has no row)."""
        with self._tx(write=False) as conn:
            self._viewer(conn, user_id, bot_id)
            found = []
            for row in conn.execute(
                "SELECT * FROM bot_threads WHERE bot_id=? ORDER BY created_at, id", (bot_id,)
            ):
                try:
                    self._reach(conn, user_id, bot_id, row["id"])
                except Missing:
                    continue
                found.append(_thread(row))
            return found

    def thread_archive(self, bot_id, user_id, thread_id, archived) -> dict:
        """The opener archives or restores a thread they reach."""
        if type(archived) is not bool:
            raise Invalid("archived must be true or false")
        with self._tx() as conn:
            self._reach(conn, user_id, bot_id, thread_id)
            row = conn.execute("SELECT * FROM bot_threads WHERE id=?", (thread_id,)).fetchone()
            if row["user_id"] != user_id:
                raise Missing()
            conn.execute(
                "UPDATE bot_threads SET archived_at=? WHERE id=?",
                (stamp() if archived else None, thread_id),
            )
            return _thread(
                conn.execute("SELECT * FROM bot_threads WHERE id=?", (thread_id,)).fetchone()
            )

    # Messages of a thread (the same path serves the owner's original thread)

    def thread_post(self, bot_id, user_id, thread_id, text, request_id) -> dict:
        """Store a person's message once per ``(bot, thread, request id)``.

        The same text returns the stored row, other text is a :class:`Conflict`; another
        thread's message with the same id is never touched.
        """
        bounded(text, "text", 1, TEXT_MAX)
        if not isinstance(request_id, str) or REQUEST_ID.fullmatch(request_id) is None:
            raise Invalid("invalid request id")
        with self._tx() as conn:
            reach = self._reach(conn, user_id, bot_id, thread_id)
            if not reach.can_write:
                raise Missing()
            row = conn.execute(
                "SELECT * FROM bot_messages WHERE bot_id=? AND thread_id IS ? AND request_id=? "
                "AND role='user'",
                (bot_id, reach.thread_id, request_id),
            ).fetchone()
            if row is None:
                return self._add(
                    conn,
                    bot_id,
                    "user",
                    text,
                    request_id=request_id,
                    thread_id=reach.thread_id,
                    author_id=None if reach.thread_id is None else user_id,
                )
            if row["text"] != text:
                raise Conflict()
            return message(row)

    def thread_reply(
        self, bot_id, user_id, thread_id, reply_to, text, blocks=(), model=None, state="ok"
    ) -> dict:
        """The one bot reply to a message of this thread."""
        bounded(text, "text", 0, TEXT_MAX * 4)
        if state != "ok" and state != "stopped" and not state.startswith("failed:"):
            raise Invalid("invalid state")
        with self._tx() as conn:
            reach = self._reach(conn, user_id, bot_id, thread_id)
            if not reach.can_write:
                raise Missing()
            if not conn.execute(
                "SELECT 1 FROM bot_messages WHERE id=? AND bot_id=? AND thread_id IS ? "
                "AND role='user'",
                (reply_to, bot_id, reach.thread_id),
            ).fetchone():
                raise Invalid("reply_to is not a message of this thread")
            try:
                return self._add(
                    conn,
                    bot_id,
                    "bot",
                    text,
                    blocks=list(blocks),
                    reply_to=reply_to,
                    model=model,
                    state=state,
                    thread_id=reach.thread_id,
                )
            except sqlite3.IntegrityError:
                raise Conflict() from None

    def thread_messages(
        self, bot_id, user_id, thread_id=None, after=None, before=None, limit=PAGE_DEFAULT
    ) -> list[dict]:
        """A page of a thread, oldest first, never older than the reader's ``read_from``."""
        if type(limit) is not int or not 1 <= limit <= PAGE_MAX:
            raise Invalid("limit must be 1-100")
        for value in (after, before):
            if value is not None and (type(value) is not int or not 0 <= value < 2**63):
                raise Invalid("invalid cursor")
        if after is not None and before is not None:
            raise Invalid("invalid cursor")
        with self._tx(write=False) as conn:
            reach = self._reach(conn, user_id, bot_id, thread_id)
            floor = max(reach.read_from, after or 0)
            if after is not None:
                rows = conn.execute(
                    "SELECT * FROM bot_messages WHERE bot_id=? AND thread_id IS ? AND id>? "
                    "ORDER BY id LIMIT ?",
                    (bot_id, reach.thread_id, floor, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM bot_messages WHERE bot_id=? AND thread_id IS ? AND id>? "
                    "AND id<? ORDER BY id DESC LIMIT ?",
                    (
                        bot_id,
                        reach.thread_id,
                        floor,
                        2**63 - 1 if before is None else before,
                        limit,
                    ),
                ).fetchall()[::-1]
        return [
            {**message(r), "thread_id": r["thread_id"], "author_id": r["author_id"]} for r in rows
        ]
