"""Which bot a project sits under, in the sidebar tree: one row per person and project.

A bot owns 0..n projects (boards); a project sits under at most one of a person's bots, and under
none ("Unassigned") when it has no row or its row has no bot. The link belongs to the person, like the
bots themselves, so one person's arrangement never shows in another person's sidebar and a member
only ever holds links for projects they can read. Nothing here deletes a bot, a message or a link:
archiving a bot keeps its links (its projects read as Unassigned until it is restored), and putting
a project back to Unassigned keeps a row with no bot so the first listing does not link it again.

``seed_projects`` is the migration: insert-only and idempotent, so it runs on every listing. A project
with no row yet goes under the person's live project bot for that board; failing that, under the one
live custom bot whose name equals the project's slug or name once case and punctuation are ignored.
Two matching bots are ambiguous and leave the project Unassigned.
"""

import re

from commonplace.bots.errors import Invalid, stamp

SCHEMA_LINKS = """
CREATE TABLE IF NOT EXISTS bot_projects (
    user_id TEXT NOT NULL,
    board TEXT NOT NULL,
    bot_id TEXT,
    moved_at TEXT NOT NULL,
    PRIMARY KEY (user_id, board)
);
CREATE INDEX IF NOT EXISTS bot_projects_by_bot ON bot_projects(bot_id);
"""

_PUNCTUATION = re.compile(r"[\W_]+")


def fold(text) -> str:
    """A name with case, spaces and punctuation gone: ``HALO avatar`` and ``halo-avatar`` agree."""
    return _PUNCTUATION.sub("", str(text or "").casefold())


def _live_bots(conn, user_id):
    return conn.execute(
        "SELECT id, kind, board, name FROM bots WHERE user_id=? AND archived_at IS NULL "
        "ORDER BY rowid",
        (user_id,),
    ).fetchall()


def _target(live, slug, name):
    """The bot id a project without a row goes under, or ``None`` (stays Unassigned)."""
    for bot in live:
        if bot["kind"] == "project" and bot["board"] == slug:
            return bot["id"]
    wanted = {fold(slug), fold(name)} - {""}
    named = [b["id"] for b in live if b["kind"] == "custom" and fold(b["name"]) in wanted]
    return named[0] if len(named) == 1 else None


class ProjectLinksMixin:
    """The project-link half of :class:`commonplace.bots.store.BotStore`."""

    def seed_projects(self, user_id, boards) -> None:
        """Link every ``(slug, name)`` that has no row yet; never moves or removes a link."""
        with self._tx() as conn:
            self._seed(conn, user_id, boards)

    @staticmethod
    def _seed(conn, user_id, boards) -> None:
        linked = {
            r["board"]
            for r in conn.execute("SELECT board FROM bot_projects WHERE user_id=?", (user_id,))
        }
        live = _live_bots(conn, user_id)
        for slug, name in boards:
            if slug in linked:
                continue
            bot_id = _target(live, slug, name)
            if bot_id is not None:
                conn.execute(
                    "INSERT OR IGNORE INTO bot_projects (user_id,board,bot_id,moved_at) "
                    "VALUES (?,?,?,?)",
                    (user_id, slug, bot_id, stamp()),
                )

    def project_links(self, user_id) -> dict:
        """``{board: bot id}`` for this person's projects under a live bot of theirs.

        A project with no row, no bot, or a bot that is archived is not in the answer: it is
        Unassigned. The caller keeps only the projects the person can read.
        """
        with self._tx(write=False) as conn:
            return {
                r["board"]: r["bot_id"]
                for r in conn.execute(
                    "SELECT p.board, p.bot_id FROM bot_projects p JOIN bots b ON b.id=p.bot_id "
                    "AND b.user_id=p.user_id AND b.archived_at IS NULL WHERE p.user_id=? "
                    "ORDER BY p.board",
                    (user_id,),
                )
            }

    def move_project(self, user_id, board, bot_id) -> str | None:
        """Put ``board`` under the person's own live bot ``bot_id`` (``None``: Unassigned).

        Returns the bot it was under before (``None`` for none). A bot that is not the person's
        is :class:`Missing`, an archived one :class:`Invalid`. The change is one upsert; moving a
        project to where it already is writes nothing new.
        """
        if not isinstance(board, str) or not board:
            raise Invalid("invalid board")
        with self._tx() as conn:
            if bot_id is not None:
                row = self._own(conn, bot_id, user_id)
                if row["archived_at"] is not None:
                    raise Invalid("archived bot")
            before = conn.execute(
                "SELECT bot_id FROM bot_projects WHERE user_id=? AND board=?", (user_id, board)
            ).fetchone()
            before = before["bot_id"] if before else None
            if before != bot_id or bot_id is None:
                conn.execute(
                    "INSERT INTO bot_projects (user_id,board,bot_id,moved_at) VALUES (?,?,?,?) "
                    "ON CONFLICT(user_id,board) DO UPDATE SET bot_id=excluded.bot_id, "
                    "moved_at=excluded.moved_at",
                    (user_id, board, bot_id, stamp()),
                )
            return before

    def release_project(self, user_id, bot_id, board) -> bool:
        """Put ``board`` back to Unassigned if it is under ``bot_id``; whether anything changed.

        The bot must be the person's own (else :class:`Missing`).
        """
        with self._tx() as conn:
            self._own(conn, bot_id, user_id)
            changed = conn.execute(
                "UPDATE bot_projects SET bot_id=NULL, moved_at=? "
                "WHERE user_id=? AND board=? AND bot_id=?",
                (stamp(), user_id, board, bot_id),
            ).rowcount
            return changed > 0


__all__ = ["ProjectLinksMixin", "SCHEMA_LINKS", "fold"]
