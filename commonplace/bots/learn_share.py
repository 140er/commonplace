"""The shared scope: part of ``Learned`` (``docs/design/bot-learning-memory.md``, sections 4 and 7).

A shared item is a copy of one of the person's own items, made only by their explicit tap on a
confirm chip. It exists only while the workspace switch, the project switch and the reader's
access to that project all allow it, and all three are re-resolved on every read: switching one
off hides every shared item at once (nothing is deleted), switching back on restores them.
The sharer keeps ``user_id``, the quote and its source link, so the sharer (or a workspace owner
who can read the project) can delete the copy. The person's own item is never touched by that,
and forgetting the person's item deletes its copies (``Learned._delete``).
"""

import sqlite3
import uuid

from commonplace.bots import learning
from commonplace.bots.store import Conflict, Invalid, Missing, stamp

SHARED_CAP = 50  # live shared items per project


class ShareMixin:
    @staticmethod
    def _shared_on(conn, board) -> bool:
        """Whether shared items of ``board`` may exist right now: both owner switches are on."""
        rows = conn.execute(
            "SELECT key,enabled,shared_enabled FROM bot_learn_policy WHERE key IN ('workspace',?)",
            (f"board:{board}",),
        ).fetchall()
        by_key = {r["key"]: r for r in rows}
        workspace, project = by_key.get("workspace"), by_key.get(f"board:{board}")
        return bool(workspace and workspace["enabled"] and workspace["shared_enabled"]) and bool(
            project and project["shared_enabled"]
        )

    def shared_on(self, board) -> bool:
        with self._bots._tx(write=False) as conn:
            return self._shared_on(conn, board)

    def _share_why(self, conn, board) -> str | None:
        """The first switch that is off, as a code; None when sharing into ``board`` is allowed."""
        policy = self._policy(conn, "workspace")
        if not policy["enabled"]:
            return "workspace_off"
        if not policy["shared_enabled"]:
            return "shared_off"
        if not self._policy(conn, f"board:{board}")["shared_enabled"]:
            return "project_off"
        return None

    def share_check(self, user_id, item_id, board, can_read=None) -> dict:
        """The person's item that may be shared into ``board`` right now, or raise.

        :class:`Missing` (not the person's item, or a project they cannot read),
        :class:`Invalid` (not an active current person item) or ``Disabled`` (a switch is off).
        """
        from commonplace.bots.learned import Disabled, _item

        if not isinstance(board, str) or not 1 <= len(board) <= 80:
            raise Invalid("invalid project")
        with self._bots._tx(write=False) as conn:
            row = self._row(conn, user_id, item_id)
            if can_read is None or not can_read(board):
                raise Missing()
            if row["scope"] != "person" or row["state"] != "active" or row["replaced_by"]:
                raise Invalid("not shareable")
            if (why := self._share_why(conn, board)) is not None:
                raise Disabled(why)
            return _item(row)

    def share(self, user_id, item_id, board, can_read=None, now=None) -> dict:
        """Copy the person's item into ``board`` as a shared item (the tap).

        Everything is checked again inside the write, so a switch turned off, a project lost or
        an item forgotten between the chip and the tap refuses the share. A second share of the
        same sentence into the same project is a :class:`Conflict`.
        """
        from commonplace.bots.learned import Capped, Disabled, Refused, _item

        item = self.share_check(user_id, item_id, board, can_read)
        if not (verdict := learning.guard(item["text"])).allowed:
            raise Refused(verdict.kind)
        copy_id = uuid.uuid4().hex
        with self._bots._tx() as conn:
            row = self._row(conn, user_id, item_id)
            if row["scope"] != "person" or row["state"] != "active" or row["replaced_by"]:
                raise Invalid("not shareable")
            if (why := self._share_why(conn, board)) is not None:
                raise Disabled(why)
            live = conn.execute(
                "SELECT COUNT(*) FROM bot_learned WHERE scope='shared' AND board=? "
                "AND replaced_by IS NULL",
                (board,),
            ).fetchone()[0]
            if live >= SHARED_CAP:
                raise Capped("shared")
            try:
                conn.execute(
                    "INSERT INTO bot_learned (id,user_id,bot_id,scope,board,kind,topic,text,"
                    "text_by,source_message_id,source_at,quote_hash,state,shared_from,"
                    "last_seen_at,created_at) VALUES (?,?,?,'shared',?,?,?,?,?,?,?,?,'active',?,?,?)",
                    (
                        copy_id,
                        user_id,
                        row["bot_id"],
                        board,
                        row["kind"],
                        row["topic"],
                        row["text"],
                        row["text_by"],
                        row["source_message_id"],
                        row["source_at"],
                        row["quote_hash"],
                        item_id,
                        stamp(now),
                        stamp(now),
                    ),
                )
            except sqlite3.IntegrityError:
                raise Conflict() from None
            self._log(conn, user_id, row["bot_id"], copy_id, "share", now)
            return _item(self._row(conn, user_id, copy_id))

    def shared_list(self, board, reader_id, can_read=None, limit=50, offset=0) -> list[dict]:
        """The shared items of ``board`` for one reader: nothing unless both switches are on and
        the reader can read the project right now. Newest first."""
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise Invalid("invalid filter")
        if can_read is None or not can_read(board):
            return []
        with self._bots._tx(write=False) as conn:
            if not self._shared_on(conn, board):
                return []
            rows = conn.execute(
                "SELECT * FROM bot_learned WHERE scope='shared' AND board=? AND state='active' "
                "AND replaced_by IS NULL ORDER BY seq DESC LIMIT ? OFFSET ?",
                (board, limit, offset),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "kind": r["kind"],
                "text": r["text"],
                "said": r["source_at"][:10],
                "shared_at": r["created_at"],
                "user_id": r["user_id"],
                "mine": r["user_id"] == reader_id,
            }
            for r in rows
        ]

    def delete_shared(self, item_id, *, user_id, owner_can_read=None) -> None:
        """Delete one shared copy for good. The sharer may; so may a workspace owner who can read
        the project (``owner_can_read(board)``, None for anyone else). Anyone else, or a copy
        that is not there, is :class:`Missing`. The person's own item and the block list are
        not touched: only this copy goes."""
        with self._bots._tx() as conn:
            row = (
                conn.execute(
                    "SELECT * FROM bot_learned WHERE id=? AND scope='shared'", (item_id,)
                ).fetchone()
                if isinstance(item_id, str)
                else None
            )
            if row is None or not (
                row["user_id"] == user_id
                or (owner_can_read is not None and owner_can_read(row["board"]))
            ):
                raise Missing()
            conn.execute("DELETE FROM bot_learned WHERE id=?", (item_id,))
            self._log(conn, user_id, row["bot_id"], item_id, "forget")
        self._scrub()
