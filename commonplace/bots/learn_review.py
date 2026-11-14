"""Review of learned items: part of ``Learned`` (``docs/design/bot-learning-memory.md``, section 7).

Ask mode stores a proposal; the person accepts it (it becomes an active item), rejects it (it is
deleted and its sentence is blocked from being proposed again) or edits it first. Every method
takes the user id and answers :class:`Missing` for an item that is not that person's. The list
view adds a ``group`` to each item (person, shared, proposed, earlier, old) so one screen can
show everything with its source. Nothing here stores or logs the person's words.
"""

from datetime import datetime, timezone

from commonplace.bots.learn_recall import RETIRE_DAYS, _age_days
from commonplace.bots.store import Conflict, Missing, stamp


def group(item, now=None) -> str:
    """Which part of the review list an item belongs to."""
    if item["state"] == "proposed":
        return "proposed"
    if item["replaced_by"]:
        return "earlier"
    if item["scope"] == "shared":
        return "shared"
    if item["kind"] != "note" and not item["pinned"]:
        age = _age_days(item, now or datetime.now(timezone.utc))
        if age >= RETIRE_DAYS:
            return "old"
    return "person"


class ReviewMixin:
    def accept(self, user_id, item_id, now=None) -> dict:
        """Make the person's proposal an active item (it replaces an older item of its topic)."""
        from commonplace.bots.learned import WORKSPACE, Disabled, _item

        with self._bots._tx() as conn:
            row = self._row(conn, user_id, item_id)
            if row["scope"] != "person":
                raise Missing()
            if row["state"] != "proposed":
                raise Conflict()
            if not self._policy(conn, WORKSPACE)["enabled"]:
                raise Disabled("workspace_off")
            conn.execute("UPDATE bot_learned SET state='active' WHERE id=?", (item_id,))
            self._replace(conn, user_id, item_id, row["topic"], row["board"], "active")
            self._log(conn, user_id, row["bot_id"], item_id, "accept", now)
            return _item(self._row(conn, user_id, item_id))

    def reject(self, user_id, item_id, now=None) -> None:
        """Delete the person's proposal for good and block the same sentence from returning."""
        with self._bots._tx() as conn:
            row = self._row(conn, user_id, item_id)
            if row["scope"] != "person":
                raise Missing()
            if row["state"] != "proposed":
                raise Conflict()
            conn.execute(
                "INSERT OR IGNORE INTO bot_learn_blocks (user_id,quote_hash,created_at) "
                "VALUES (?,?,?)",
                (user_id, row["quote_hash"], stamp(now)),
            )
            conn.execute("DELETE FROM bot_learned WHERE id=?", (item_id,))
            self._log(conn, user_id, row["bot_id"], item_id, "reject", now)
        self._scrub()

    def bot_names(self, user_id) -> dict:
        """The names of the person's bots by id, for the source line of each item."""
        with self._bots._tx(write=False) as conn:
            return {
                r["id"]: r["name"]
                for r in conn.execute("SELECT id,name FROM bots WHERE user_id=?", (user_id,))
            }
