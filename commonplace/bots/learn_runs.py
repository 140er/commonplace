"""The run journal of the learning extractor: when a run may start, its claim and its outcome.

Part of ``Learned`` (``docs/design/bot-learning-memory.md``, section 6). A run is keyed
``(bot_id, upto_message_id)``: only the writer that inserts the ``started`` row proceeds, and a
window whose run was interrupted is never rerun. Nothing here stores or logs the person's words.
"""

import re
import sqlite3
from datetime import datetime, timedelta, timezone

from commonplace.bots.store import BotStore, stamp

MIN_MESSAGES = 3  # new user messages that start a run without a cue
SPACING_S = 300  # between two runs of one bot
LEASE_S = 60  # a started run older than this is interrupted
WINDOW_MESSAGES, WINDOW_CHARS = 6, 4000
PROPOSAL_DAYS = 14  # an unanswered proposal is deleted after this

# Phrases that make one message worth a run on its own. One row per language; add a language by
# adding a row.
CUES = {
    "en": (
        r"always",
        r"never",
        r"from now on",
        r"i prefer",
        r"i like",
        r"i am",
        r"i'm",
        r"my\b",
        r"don't",
        r"do not",
        r"stop\b",
        r"remember",
    ),
    "nl": (
        r"altijd",
        r"nooit",
        r"vanaf nu",
        r"ik wil liever",
        r"ik hou van",
        r"ik ben",
        r"mijn\b",
        r"niet meer",
        r"stop met",
        r"onthoud",
    ),
}
_CUE = re.compile(
    r"\b(?:" + "|".join(cue for cues in CUES.values() for cue in cues) + ")", re.IGNORECASE
)


def has_cue(text) -> bool:
    return _CUE.search(text) is not None


class RunsMixin:
    def _skew(self, now, seconds):
        return stamp((now or datetime.now(timezone.utc)) - timedelta(seconds=seconds))

    def plan(self, bot_id, user_id, now=None) -> dict | None:
        """The window to extract from, or None when any switch or trigger rule says no.

        Nothing is written unless the workspace, the bot and the pause allow learning at all.
        """
        with self._bots._tx(write=False) as conn:
            BotStore._own(conn, bot_id, user_id)
            view = self._state_view(conn, bot_id, user_id, now)
        if not view["active"]:
            return None
        deleted = 0
        with self._bots._tx() as conn:
            self._recover(conn, bot_id, now)
            deleted = self._expire(conn, user_id, now)
            plan = self._window(conn, bot_id, user_id, now)
        if deleted:
            self._scrub()
        return plan

    def _recover(self, conn, bot_id, now) -> None:
        """A run that stopped (restart, crash) is ``failed:interrupted`` and its window is skipped."""
        for row in conn.execute(
            "SELECT upto_message_id FROM bot_learn_runs WHERE bot_id=? AND state='started' "
            "AND started_at<?",
            (bot_id, self._skew(now, LEASE_S)),
        ).fetchall():
            conn.execute(
                "UPDATE bot_learn_runs SET state='failed:interrupted', finished_at=? "
                "WHERE bot_id=? AND upto_message_id=?",
                (stamp(now), bot_id, row["upto_message_id"]),
            )
            conn.execute(
                "UPDATE bot_learn_state SET last_run_message_id=MAX(last_run_message_id,?) "
                "WHERE bot_id=?",
                (row["upto_message_id"], bot_id),
            )

    def _expire(self, conn, user_id, now) -> int:
        """Delete proposals nobody answered in 14 days (a real delete, no block entry)."""
        cutoff = stamp((now or datetime.now(timezone.utc)) - timedelta(days=PROPOSAL_DAYS))
        old = conn.execute(
            "SELECT id,bot_id FROM bot_learned WHERE user_id=? AND state='proposed' "
            "AND created_at<?",
            (user_id, cutoff),
        ).fetchall()
        for row in old:
            conn.execute("DELETE FROM bot_learned WHERE id=?", (row["id"],))
            self._log(conn, user_id, row["bot_id"], row["id"], "reject", now)
        return len(old)

    def _in_flight(self, conn, user_id, now) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM bot_learn_runs r JOIN bots b ON b.id=r.bot_id "
                "WHERE b.user_id=? AND r.state='started' AND r.started_at>=?",
                (user_id, self._skew(now, LEASE_S)),
            ).fetchone()
            is not None
        )

    def _window(self, conn, bot_id, user_id, now) -> dict | None:
        state = conn.execute("SELECT * FROM bot_learn_state WHERE bot_id=?", (bot_id,)).fetchone()
        after = max(state["floor_message_id"], state["last_run_message_id"])
        own = (
            "bot_id=? AND role='user' AND thread_id IS NULL AND (author_id IS NULL OR author_id=?)"
            " AND id>?"
        )
        fresh = conn.execute(
            f"SELECT id,text FROM bot_messages WHERE {own} ORDER BY id DESC LIMIT ?",
            (bot_id, user_id, after, 1000),
        ).fetchall()
        if not fresh:
            return None
        if len(fresh) < MIN_MESSAGES and not any(has_cue(row["text"]) for row in fresh):
            return None
        last = conn.execute(
            "SELECT MAX(started_at) FROM bot_learn_runs WHERE bot_id=?", (bot_id,)
        ).fetchone()[0]
        if last is not None and last >= self._skew(now, SPACING_S):
            return None
        if self._in_flight(conn, user_id, now):
            return None
        chosen, size = [], 0
        for row in fresh[:WINDOW_MESSAGES]:  # newest first; whole messages only
            if size + len(row["text"]) <= WINDOW_CHARS:
                chosen.append({"id": row["id"], "text": row["text"]})
                size += len(row["text"])
        if not chosen:
            return None
        return {"messages": chosen[::-1], "upto": fresh[0]["id"]}

    def claim(self, bot_id, user_id, upto, now=None) -> bool:
        """Insert the ``started`` row. False when another writer holds the window or the person
        already has a run in flight."""
        with self._bots._tx() as conn:
            BotStore._own(conn, bot_id, user_id)
            if self._in_flight(conn, user_id, now):
                return False
            try:
                conn.execute(
                    "INSERT INTO bot_learn_runs (bot_id,upto_message_id,state,started_at) "
                    "VALUES (?,?,'started',?)",
                    (bot_id, upto, stamp(now)),
                )
            except sqlite3.IntegrityError:
                return False
            return True

    def release(self, bot_id, upto) -> None:
        """Give a claimed window back (no model, no budget): the next turn tries it again."""
        with self._bots._tx() as conn:
            conn.execute(
                "DELETE FROM bot_learn_runs WHERE bot_id=? AND upto_message_id=? "
                "AND state='started'",
                (bot_id, upto),
            )

    def running(self, bot_id, upto) -> bool:
        with self._bots._tx(write=False) as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM bot_learn_runs WHERE bot_id=? AND upto_message_id=? "
                    "AND state='started'",
                    (bot_id, upto),
                ).fetchone()
                is not None
            )

    def finish_run(self, bot_id, upto, state, proposed=0, kept=0, dropped=0, now=None) -> None:
        """Close the run (``done`` or ``failed:<code>``) and move the window past it."""
        with self._bots._tx() as conn:
            changed = conn.execute(
                "UPDATE bot_learn_runs SET state=?, finished_at=?, n_proposed=?, n_kept=?, "
                "n_dropped=? WHERE bot_id=? AND upto_message_id=? AND state='started'",
                (state, stamp(now), proposed, kept, dropped, bot_id, upto),
            ).rowcount
            if changed:
                conn.execute(
                    "UPDATE bot_learn_state SET last_run_message_id=MAX(last_run_message_id,?) "
                    "WHERE bot_id=?",
                    (upto, bot_id),
                )

    def runs(self, bot_id) -> list[dict]:
        with self._bots._tx(write=False) as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM bot_learn_runs WHERE bot_id=? ORDER BY upto_message_id",
                    (bot_id,),
                )
            ]

    def live_topics(self, user_id, limit=30) -> list[str]:
        with self._bots._tx(write=False) as conn:
            return [
                row["topic"]
                for row in conn.execute(
                    "SELECT topic FROM bot_learned WHERE user_id=? AND scope='person' "
                    "AND state='active' AND replaced_by IS NULL GROUP BY topic "
                    "ORDER BY MAX(seq) DESC LIMIT ?",
                    (user_id, limit),
                )
            ]
