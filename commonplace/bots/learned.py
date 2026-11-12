"""Learned items, their switches and real deletion, in the workspace file ``bots.sqlite3``.

The design is ``docs/design/bot-learning-memory.md``. Every method takes the user id and answers
:class:`Missing` for an item or bot that is not that person's, so a foreign id looks unknown.
Nothing is on by default: a missing policy row is off and a missing state row is mode ``off``.
Forgetting really deletes (row, search index, a checkpointed log); only an action word and a
keyed hash remain. Errors and the log never carry the person's words.
"""

import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from commonplace.bots import learning
from commonplace.bots.learn_recall import RecallMixin
from commonplace.bots.learn_runs import RunsMixin
from commonplace.bots.learn_share import ShareMixin
from commonplace.bots.store import BotStore, Conflict, Invalid, Missing, stamp

KINDS = ("preference", "instruction", "fact", "correction")
MODES = ("off", "ask", "auto")
TEXT_MIN, TEXT_MAX, NOTE_MAX, TOPIC_MAX = 8, 300, 200, 80
PAGE_DEFAULT, PAGE_MAX = 50, 100
PAUSE_MAX_DAYS = 30
LIVE_CAP, DAILY_CAP = 200, 20  # live person items per person; new items per person per UTC day
WORKSPACE = "workspace"
_NOTE_ID = re.compile(r"n([0-9]{1,18})")


class Refused(Exception):
    """The guard or a block says no. ``kind`` is one word (secret, injection, blocked, ...)."""

    def __init__(self, kind):
        super().__init__(kind)
        self.kind = kind


class Capped(Exception):
    """A storage cap says no. ``kind`` is ``live`` or ``daily``."""

    def __init__(self, kind):
        super().__init__(kind)
        self.kind = kind


class Disabled(Exception):
    """A switch says this bot may not learn right now. ``reason`` is a code, never text."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def open_learned(gateway, workspace_id) -> "Learned":
    from commonplace.bots import store

    return Learned(store.open_bots(gateway, workspace_id))


def board_key(slug) -> str:
    return f"board:{slug}"


def _flag(value, name):
    if type(value) is not bool:
        raise Invalid(f"{name} must be true or false")
    return value


def _item(row) -> dict:
    return {
        "id": row["id"],
        "bot_id": row["bot_id"],
        "scope": row["scope"],
        "board": row["board"],
        "kind": row["kind"],
        "topic": row["topic"],
        "text": row["text"],
        "text_by": row["text_by"],
        "note": row["note"],
        "note_by": row["note_by"],
        "source_message_id": row["source_message_id"],
        "source_at": row["source_at"],
        "state": row["state"],
        "pinned": bool(row["pinned"]),
        "replaced_by": row["replaced_by"],
        "shared_from": row["shared_from"],
        "seen_count": row["seen_count"],
        "last_seen_at": row["last_seen_at"],
        "use_count": row["use_count"],
        "last_used_at": row["last_used_at"],
        "created_at": row["created_at"],
    }


def _note(row) -> dict:
    """A ``remember`` note as a list entry (ids ``n<row>`` cannot clash with item ids)."""
    return {
        "id": f"n{row['id']}",
        "bot_id": row["bot_id"],
        "scope": "person",
        "board": None,
        "kind": "note",
        "topic": "",
        "text": row["text"],
        "text_by": "person",
        "note": None,
        "note_by": None,
        "source_message_id": None,
        "source_at": row["created_at"],
        "state": "active",
        "pinned": False,
        "replaced_by": None,
        "shared_from": None,
        "seen_count": 1,
        "last_seen_at": row["created_at"],
        "use_count": 0,
        "last_used_at": None,
        "created_at": row["created_at"],
    }


class Learned(RunsMixin, RecallMixin, ShareMixin):
    def __init__(self, bots: BotStore):
        self._bots = bots
        self._key = None

    def _hash(self, text) -> str:
        if self._key is None:
            self._key = learning.workspace_key(self._bots.directory())
        return learning.quote_hash(self._key, text)

    @staticmethod
    def _log(conn, user_id, bot_id, item_id, action, now=None):
        conn.execute(
            "INSERT INTO bot_learn_log (user_id,bot_id,item_id,action,at) VALUES (?,?,?,?,?)",
            (user_id, bot_id, item_id, action, stamp(now)),
        )

    def _scrub(self) -> None:
        """After a deletion: rewrite the search index without the deleted terms, then fold the
        log into the main file and empty it, so the removed words are not left in either."""
        with self._bots._tx() as conn:
            conn.execute("INSERT INTO bot_learned_fts(bot_learned_fts) VALUES ('optimize')")
        with self._bots._tx(write=False) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()

    # Switches

    def policy(self, key=WORKSPACE) -> dict:
        with self._bots._tx(write=False) as conn:
            return self._policy(conn, key)

    @staticmethod
    def _policy(conn, key) -> dict:
        row = conn.execute("SELECT * FROM bot_learn_policy WHERE key=?", (key,)).fetchone()
        if row is None:
            return {"enabled": False, "shared_enabled": False, "set_by": None, "set_at": None}
        return {
            "enabled": bool(row["enabled"]),
            "shared_enabled": bool(row["shared_enabled"]),
            "set_by": row["set_by"],
            "set_at": row["set_at"],
        }

    def set_policy(self, key, user_id, enabled=None, shared_enabled=None) -> dict:
        """Owner switches. The workspace row uses both; a board row only ``shared_enabled``."""
        changes = {}
        if enabled is not None:
            if key != WORKSPACE:
                raise Invalid("enabled is a workspace switch")
            changes["enabled"] = int(_flag(enabled, "enabled"))
        if shared_enabled is not None:
            changes["shared_enabled"] = int(_flag(shared_enabled, "shared_enabled"))
        if not changes:
            raise Invalid("nothing to change")
        with self._bots._tx() as conn:
            conn.execute("INSERT OR IGNORE INTO bot_learn_policy (key) VALUES (?)", (key,))
            conn.execute(
                "UPDATE bot_learn_policy SET "
                + ",".join(f"{name}=?" for name in changes)
                + ",set_by=?,set_at=? WHERE key=?",
                (*changes.values(), user_id, stamp(), key),
            )
            self._log(conn, user_id, None, None, "setting")
            return self._policy(conn, key)

    # A person's setting for one bot

    @staticmethod
    def _state_row(conn, bot_id, user_id):
        conn.execute(
            "INSERT OR IGNORE INTO bot_learn_state (bot_id,user_id) VALUES (?,?)", (bot_id, user_id)
        )
        return conn.execute("SELECT * FROM bot_learn_state WHERE bot_id=?", (bot_id,)).fetchone()

    def _state_view(self, conn, bot_id, user_id, now=None) -> dict:
        row = conn.execute("SELECT * FROM bot_learn_state WHERE bot_id=?", (bot_id,)).fetchone()
        mode = row["mode"] if row else "off"
        until = row["paused_until"] if row else None
        paused = until is not None and until > stamp(now)
        enabled = self._policy(conn, WORKSPACE)["enabled"]
        return {
            "mode": mode,
            "paused": paused,
            "paused_until": until if paused else None,
            "floor_message_id": row["floor_message_id"] if row else 0,
            "workspace_enabled": enabled,
            "active": enabled and mode != "off" and not paused,
            "counts": self._counts(conn, user_id, bot_id),
        }

    def state(self, bot_id, user_id, now=None) -> dict:
        with self._bots._tx(write=False) as conn:
            BotStore._own(conn, bot_id, user_id)
            return self._state_view(conn, bot_id, user_id, now)

    def configure(self, bot_id, user_id, *, mode=None, pause=False, now=None) -> dict:
        """Set the mode and/or pause. ``pause`` is a number of days (1 to 30) or None to resume;
        ``False`` leaves the pause as it is."""
        if mode is not None and mode not in MODES:
            raise Invalid("invalid mode")
        if pause is not False and pause is not None:
            if type(pause) is not int or not 1 <= pause <= PAUSE_MAX_DAYS:
                raise Invalid("invalid pause")
        if mode is None and pause is False:
            raise Invalid("nothing to change")
        with self._bots._tx() as conn:
            BotStore._own(conn, bot_id, user_id)
            row = self._state_row(conn, bot_id, user_id)
            if mode is not None and mode != row["mode"]:
                conn.execute("UPDATE bot_learn_state SET mode=? WHERE bot_id=?", (mode, bot_id))
                self._log(conn, user_id, bot_id, None, "setting", now)
            if pause is not False:
                until = (
                    None
                    if pause is None
                    else stamp((now or datetime.now(timezone.utc)) + timedelta(days=pause))
                )
                conn.execute(
                    "UPDATE bot_learn_state SET paused_until=? WHERE bot_id=?", (until, bot_id)
                )
                self._log(conn, user_id, bot_id, None, "resume" if until is None else "pause", now)
            return self._state_view(conn, bot_id, user_id, now)

    # Items

    @staticmethod
    def _counts(conn, user_id, bot_id=None) -> dict:
        where, args = "user_id=?", [user_id]
        if bot_id is not None:
            where += " AND bot_id=?"
            args.append(bot_id)
        row = conn.execute(
            "SELECT COALESCE(SUM(scope='person' AND state='active'),0) AS person, "
            "COALESCE(SUM(scope='shared'),0) AS shared, "
            "COALESCE(SUM(state='proposed'),0) AS proposed FROM bot_learned WHERE " + where,
            args,
        ).fetchone()
        notes = conn.execute(
            "SELECT COUNT(*) FROM bot_memory m JOIN bots b ON b.id=m.bot_id WHERE b.user_id=? "
            "AND m.removed_at IS NULL AND m.owner_id IS NULL"
            + (" AND b.id=?" if bot_id is not None else ""),
            args,
        ).fetchone()[0]
        return {**{k: row[k] for k in ("person", "shared", "proposed")}, "notes": notes}

    def counts(self, user_id, bot_id=None) -> dict:
        with self._bots._tx(write=False) as conn:
            if bot_id is not None:
                BotStore._own(conn, bot_id, user_id)
            return self._counts(conn, user_id, bot_id)

    def _gate(self, conn, bot_id, user_id, message_id, now=None):
        """The switches the store itself holds; the engine adds the model and budget."""
        if not self._policy(conn, WORKSPACE)["enabled"]:
            raise Disabled("workspace_off")
        view = self._state_view(conn, bot_id, user_id, now)
        if view["mode"] == "off":
            raise Disabled("bot_off")
        if view["paused"]:
            raise Disabled("paused")
        if type(message_id) is not int or message_id <= view["floor_message_id"]:
            raise Disabled("before_floor")

    def add_item(
        self,
        user_id,
        bot_id,
        *,
        kind,
        topic,
        text,
        source_message_id,
        board=None,
        note=None,
        state="active",
        dedupe=False,
        now=None,
    ) -> dict:
        """Store one learned person item, or refuse. The seam the extractor writes through.

        The text must be a verbatim span of one of this bot's stored user messages and pass the
        guard; a note (the bot's own remark, at most 200 characters) is stored apart and marked.
        With ``dedupe`` (the extractor) a repeated sentence only bumps ``seen_count``, a new text
        for a known topic replaces the old item, and the caps apply; without it a repeat is a
        :class:`Conflict`.
        """
        topic = learning.topic_key(topic)
        text = text.strip() if isinstance(text, str) else text
        if (
            kind not in KINDS
            or state not in ("proposed", "active")
            or topic is None
            or not isinstance(text, str)
            or not TEXT_MIN <= len(text) <= TEXT_MAX
            or (note is not None and (not isinstance(note, str) or not 1 <= len(note) <= NOTE_MAX))
            or (board is not None and (not isinstance(board, str) or not 1 <= len(board) <= 80))
        ):
            raise Invalid("invalid learned item")
        for part in (text, note):
            if part is not None and not (verdict := learning.guard(part)).allowed:
                raise Refused(verdict.kind)
        digest = self._hash(text)
        item_id = uuid.uuid4().hex
        with self._bots._tx() as conn:
            BotStore._own(conn, bot_id, user_id)
            self._gate(conn, bot_id, user_id, source_message_id, now)
            message = conn.execute(
                "SELECT role,text,created_at FROM bot_messages WHERE id=? AND bot_id=?",
                (source_message_id, bot_id),
            ).fetchone()
            if message is None or message["role"] != "user" or text not in message["text"]:
                raise Invalid("not the person's words")
            if conn.execute(
                "SELECT 1 FROM bot_learn_blocks WHERE user_id=? AND quote_hash=?",
                (user_id, digest),
            ).fetchone():
                raise Refused("blocked")
            at = stamp(now)
            if dedupe:
                same = conn.execute(
                    "SELECT id FROM bot_learned WHERE user_id=? AND scope='person' "
                    "AND COALESCE(board,'')=? AND quote_hash=? AND replaced_by IS NULL",
                    (user_id, board or "", digest),
                ).fetchone()
                if same is not None:
                    conn.execute(
                        "UPDATE bot_learned SET seen_count=seen_count+1, last_seen_at=? WHERE id=?",
                        (at, same["id"]),
                    )
                    return _item(self._row(conn, user_id, same["id"]))
                self._room(conn, user_id, now)
            try:
                conn.execute(
                    "INSERT INTO bot_learned (id,user_id,bot_id,scope,board,kind,topic,text,note,"
                    "note_by,source_message_id,source_at,quote_hash,state,last_seen_at,created_at)"
                    " VALUES (?,?,?,'person',?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        item_id,
                        user_id,
                        bot_id,
                        board,
                        kind,
                        topic,
                        text,
                        note,
                        "bot" if note is not None else None,
                        source_message_id,
                        message["created_at"],
                        digest,
                        state,
                        at,
                        at,
                    ),
                )
            except sqlite3.IntegrityError:
                raise Conflict() from None
            if dedupe:
                self._replace(conn, user_id, item_id, topic, board, state)
            self._log(conn, user_id, bot_id, item_id, "learn", now)
            return _item(self._row(conn, user_id, item_id))

    @staticmethod
    def _room(conn, user_id, now=None) -> None:
        """Raise :class:`Capped` when the person is at the live-items or the daily cap."""
        live = conn.execute(
            "SELECT COUNT(*) FROM bot_learned WHERE user_id=? AND scope='person' "
            "AND replaced_by IS NULL",
            (user_id,),
        ).fetchone()[0]
        if live >= LIVE_CAP:
            raise Capped("live")
        day = stamp(now)[:10]  # new items are counted from the log, so a forget gives none back
        today = conn.execute(
            "SELECT COUNT(*) FROM bot_learn_log WHERE user_id=? AND action='learn' AND at>=?",
            (user_id, f"{day}T00:00:00Z"),
        ).fetchone()[0]
        if today >= DAILY_CAP:
            raise Capped("daily")

    @staticmethod
    def _replace(conn, user_id, item_id, topic, board, state) -> None:
        """A new text for a known topic: the old live item is replaced (it shows under
        "earlier"), an old proposal is dropped. A proposal replaces nothing until it is accepted."""
        old = conn.execute(
            "SELECT id,state FROM bot_learned WHERE user_id=? AND scope='person' AND topic=? "
            "AND COALESCE(board,'')=? AND replaced_by IS NULL AND id<>?",
            (user_id, topic, board or "", item_id),
        ).fetchall()
        for row in old:
            if row["state"] == "proposed":
                conn.execute("DELETE FROM bot_learned WHERE id=?", (row["id"],))
            elif state == "active":
                conn.execute(
                    "UPDATE bot_learned SET replaced_by=? WHERE id=?", (item_id, row["id"])
                )

    @staticmethod
    def _row(conn, user_id, item_id):
        row = (
            conn.execute(
                "SELECT * FROM bot_learned WHERE id=? AND user_id=?", (item_id, user_id)
            ).fetchone()
            if isinstance(item_id, str)
            else None
        )
        if row is None:
            raise Missing()
        return row

    @staticmethod
    def _note_row(conn, user_id, item_id):
        match = _NOTE_ID.fullmatch(item_id) if isinstance(item_id, str) else None
        if match is None:
            return None
        return conn.execute(
            "SELECT m.* FROM bot_memory m JOIN bots b ON b.id=m.bot_id "
            "WHERE m.id=? AND b.user_id=? AND m.removed_at IS NULL AND m.owner_id IS NULL",
            (int(match[1]), user_id),
        ).fetchone()

    def get(self, user_id, item_id) -> dict:
        with self._bots._tx(write=False) as conn:
            note = self._note_row(conn, user_id, item_id)
            return _note(note) if note else _item(self._row(conn, user_id, item_id))

    def list(
        self,
        user_id,
        *,
        bot_id=None,
        scope=None,
        state=None,
        kind=None,
        limit=PAGE_DEFAULT,
        offset=0,
    ) -> list[dict]:
        """The person's items (and ``remember`` notes), newest first. Only their own."""
        if (
            scope not in (None, "person", "shared")
            or state not in (None, "proposed", "active")
            or kind not in (None, "note", *KINDS)
            or type(limit) is not int
            or not 1 <= limit <= PAGE_MAX
            or type(offset) is not int
            or offset < 0
        ):
            raise Invalid("invalid filter")
        with self._bots._tx(write=False) as conn:
            if bot_id is not None:
                BotStore._own(conn, bot_id, user_id)
            clauses, args = ["user_id=?"], [user_id]
            for column, value in (("bot_id", bot_id), ("scope", scope), ("state", state)):
                if value is not None:
                    clauses.append(f"{column}=?")
                    args.append(value)
            if kind not in (None, "note"):
                clauses.append("kind=?")
                args.append(kind)
            items = []
            if kind != "note":
                items = [
                    _item(r)
                    for r in conn.execute(
                        "SELECT * FROM bot_learned WHERE "
                        + " AND ".join(clauses)
                        + " ORDER BY seq DESC LIMIT ?",
                        (*args, limit + offset),
                    )
                ]
            if kind in (None, "note") and scope in (None, "person") and state in (None, "active"):
                items += [
                    _note(r)
                    for r in conn.execute(
                        "SELECT m.* FROM bot_memory m JOIN bots b ON b.id=m.bot_id "
                        "WHERE b.user_id=? AND m.removed_at IS NULL AND m.owner_id IS NULL"
                        + (" AND b.id=?" if bot_id is not None else "")
                        + " ORDER BY m.id DESC LIMIT ?",
                        (user_id, *([bot_id] if bot_id is not None else []), limit + offset),
                    )
                ]
        items.sort(key=lambda item: item["created_at"], reverse=True)
        return items[offset : offset + limit]

    def update(self, user_id, item_id, **fields) -> dict:
        """Edit the text of, or pin, one of the person's own items (never a shared copy)."""
        if not fields or set(fields) - {"text", "pinned"}:
            raise Invalid("nothing to change")
        text = fields.get("text")
        if "text" in fields:
            text = text.strip() if isinstance(text, str) else text
            if not isinstance(text, str) or not TEXT_MIN <= len(text) <= TEXT_MAX:
                raise Invalid("invalid text")
            if not (verdict := learning.guard(text)).allowed:
                raise Refused(verdict.kind)
        if "pinned" in fields:
            _flag(fields["pinned"], "pinned")
        with self._bots._tx() as conn:
            if self._note_row(conn, user_id, item_id) is not None:
                raise Invalid("a note cannot be changed")
            row = self._row(conn, user_id, item_id)
            if row["scope"] != "person":
                raise Invalid("a shared item cannot be changed here")
            if "pinned" in fields:
                conn.execute(
                    "UPDATE bot_learned SET pinned=? WHERE id=?", (int(fields["pinned"]), item_id)
                )
            if "text" in fields and text != row["text"]:
                conn.execute(
                    "UPDATE bot_learned SET text=?, text_by='person_edit' WHERE id=?",
                    (text, item_id),
                )
                self._log(conn, user_id, row["bot_id"], item_id, "edit")
            return _item(self._row(conn, user_id, item_id))

    # Forgetting

    @staticmethod
    def _delete(conn, user_id, rows, now=None) -> int:
        """Delete ``rows`` and any shared copy of them; keep only the block hash and a log row."""
        ids = {row["id"] for row in rows}
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        ids |= {
            r["id"]
            for r in conn.execute(
                f"SELECT id FROM bot_learned WHERE user_id=? AND shared_from IN ({marks})",
                (user_id, *ids),
            )
        }
        marks = ",".join("?" * len(ids))
        found = conn.execute(
            f"SELECT id,bot_id,quote_hash FROM bot_learned WHERE id IN ({marks}) ORDER BY seq",
            tuple(ids),
        ).fetchall()
        conn.executemany(
            "INSERT OR IGNORE INTO bot_learn_blocks (user_id,quote_hash,created_at) VALUES (?,?,?)",
            [(user_id, row["quote_hash"], stamp(now)) for row in found],
        )
        conn.execute(f"DELETE FROM bot_learned WHERE id IN ({marks})", tuple(ids))
        for row in found:
            Learned._log(conn, user_id, row["bot_id"], row["id"], "forget", now)
        return len(found)

    def forget(self, user_id, item_id) -> None:
        """Delete one item (or blank one note) for good. A second call is :class:`Missing`."""
        with self._bots._tx() as conn:
            note = self._note_row(conn, user_id, item_id)
            if note is not None:
                conn.execute(
                    "UPDATE bot_memory SET removed_at=?, text='' WHERE id=?", (stamp(), note["id"])
                )
                self._log(conn, user_id, note["bot_id"], item_id, "forget")
            else:
                row = self._row(conn, user_id, item_id)
                if row["scope"] == "shared":
                    # Only this copy goes: the person's own item stays, and its sentence is not
                    # blocked from being learned again.
                    conn.execute("DELETE FROM bot_learned WHERE id=?", (item_id,))
                    self._log(conn, user_id, row["bot_id"], item_id, "forget")
                else:
                    self._delete(conn, user_id, [row])
        self._scrub()

    def forget_all(self, bot_id, user_id) -> dict:
        """Forget everything this bot learned for the person, shared contributions and notes
        included, and make the bot ignore every message it has so far."""
        with self._bots._tx() as conn:
            BotStore._own(conn, bot_id, user_id)
            rows = conn.execute(
                "SELECT id FROM bot_learned WHERE user_id=? AND bot_id=?", (user_id, bot_id)
            ).fetchall()
            forgotten = self._delete(conn, user_id, rows)
            notes = conn.execute(
                "UPDATE bot_memory SET removed_at=?, text='' WHERE bot_id=? AND removed_at IS NULL "
                "AND owner_id IS NULL",
                (stamp(), bot_id),
            ).rowcount
            newest = conn.execute(
                "SELECT COALESCE(MAX(id),0) FROM bot_messages WHERE bot_id=?", (bot_id,)
            ).fetchone()[0]
            self._state_row(conn, bot_id, user_id)
            conn.execute(
                "UPDATE bot_learn_state SET floor_message_id=MAX(floor_message_id,?) "
                "WHERE bot_id=?",
                (newest, bot_id),
            )
            self._log(conn, user_id, bot_id, None, "forget_all")
        self._scrub()
        return {"forgotten": forgotten, "notes": notes, "floor": newest}
