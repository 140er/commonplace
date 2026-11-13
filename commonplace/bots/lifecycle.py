"""Members and owners leaving, a workspace owner disabling a shared bot, and adopting an ownerless one.

Design: ``docs/design/shared-team-bots.md`` section 9. Nothing here deletes data. A removed member's
shares end (``member_removed``) and their messages, private threads and the bot stay in the file; a
removed owner's bots are frozen; a disabled bot is frozen the same way and can be enabled again; a
workspace owner adopts a frozen shared custom bot, and the old owner's original thread stays behind
a floor the adopter's reads never cross (messages are append-only, so the thread is not moved).

``frozen_reason`` is ``owner_left`` or ``disabled``. A freeze or a lift is one ``freeze`` row in the
access log: ``ref`` is the reason and ``mode_after`` is ``frozen`` or ``lifted``.
"""

import logging

from commonplace.bots.errors import Conflict, Invalid, Limit, Missing, stamp
from commonplace.http_util import log_failure

logger = logging.getLogger(__name__)
OWNER_LEFT, DISABLED = "owner_left", "disabled"
MAX_SHARED_BOTS = 20


def _freeze(conn, bot, actor_id, reason, at) -> None:
    conn.execute("UPDATE bots SET frozen_at=?, frozen_reason=? WHERE id=?", (at, reason, bot["id"]))
    conn.execute(
        "UPDATE bot_pending SET state='cancelled' WHERE bot_id=? AND state='open'", (bot["id"],)
    )
    _row(conn, bot["id"], bot["access_version"], actor_id, "freeze", ref=reason, after="frozen")


def _row(conn, bot_id, version, actor_id, action, **fields) -> None:
    conn.execute(
        "INSERT INTO bot_access_log (bot_id,version,actor_id,action,kind,ref,mode_before,"
        "mode_after,member_id,at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            bot_id,
            version,
            actor_id,
            action,
            None,
            fields.get("ref"),
            None,
            fields.get("after"),
            fields.get("member"),
            stamp(),
        ),
    )


class LifecycleMixin:
    """The lifecycle half of :class:`commonplace.bots.store.BotStore`."""

    def member_left(self, user_id, actor_id) -> dict:
        """A workspace member was removed: end their live shares and freeze the bots they own.

        Idempotent (a second call changes nothing). Their messages, threads and the bots stay.
        Returns ``{shares, frozen}``, the counts of what this call changed.
        """
        if not isinstance(user_id, str) or not user_id or not isinstance(actor_id, str):
            raise Invalid("invalid member")
        at = stamp()
        ended = frozen = 0
        with self._tx() as conn:
            for share in conn.execute(
                "SELECT s.bot_id, b.access_version FROM bot_shares s JOIN bots b ON b.id=s.bot_id "
                "WHERE s.user_id=? AND s.revoked_at IS NULL",
                (user_id,),
            ).fetchall():
                conn.execute(
                    "UPDATE bot_shares SET revoked_at=?, revoked_by=?, revoke_reason='member_removed' "
                    "WHERE bot_id=? AND user_id=? AND revoked_at IS NULL",
                    (at, actor_id, share["bot_id"], user_id),
                )
                _row(
                    conn,
                    share["bot_id"],
                    share["access_version"],
                    actor_id,
                    "unshare",
                    member=user_id,
                )
                ended += 1
            conn.execute(
                "UPDATE bot_pending SET state='cancelled' WHERE user_id=? AND state='open'",
                (user_id,),
            )
            for bot in conn.execute(
                "SELECT * FROM bots WHERE user_id=? AND (frozen_at IS NULL OR frozen_reason=?)",
                (user_id, DISABLED),
            ).fetchall():
                # A bot a workspace owner disabled stays frozen, but is now ownerless: only an
                # adoption lifts it, never ``enable``.
                _freeze(conn, bot, actor_id, OWNER_LEFT, at)
                frozen += 1
        return {"shares": ended, "frozen": frozen}

    def people(self) -> set[str]:
        """Everyone this file knows as an owner or a live sharer (for the membership sweep)."""
        with self._tx(write=False) as conn:
            return {
                r[0]
                for r in conn.execute(
                    "SELECT user_id FROM bots WHERE frozen_at IS NULL UNION "
                    "SELECT user_id FROM bot_shares WHERE revoked_at IS NULL"
                )
            }

    def disable(self, bot_id, actor_id) -> bool:
        """Freeze a shared bot for everyone (the caller checks the workspace owner). True when
        this call froze it; a bot that is not shared is an unknown id."""
        with self._tx() as conn:
            bot = self._shared_row(conn, bot_id)
            if bot["frozen_at"] is not None:
                return False
            _freeze(conn, bot, actor_id, DISABLED, stamp())
            return True

    def enable(self, bot_id, actor_id) -> bool:
        """Lift a disable. A bot whose owner left stays frozen until it is adopted (conflict)."""
        with self._tx() as conn:
            bot = self._shared_row(conn, bot_id)
            if bot["frozen_at"] is None:
                return False
            if bot["frozen_reason"] != DISABLED:
                raise Conflict()
            conn.execute("UPDATE bots SET frozen_at=NULL, frozen_reason=NULL WHERE id=?", (bot_id,))
            _row(
                conn,
                bot_id,
                bot["access_version"],
                actor_id,
                "freeze",
                ref=DISABLED,
                after="lifted",
            )
            return True

    @staticmethod
    def _shared_row(conn, bot_id):
        bot = (
            conn.execute("SELECT * FROM bots WHERE id=?", (bot_id,)).fetchone()
            if isinstance(bot_id, str)
            else None
        )
        if bot is None or not bot["shared"]:
            raise Missing()
        return bot

    def adopt(self, bot_id, adopter_id) -> dict:
        """The adopter (a workspace owner; the caller checks) takes over a frozen shared custom bot.

        The access list and the shares are kept; the list is evaluated against the adopter's
        rights from now on, so it can only shrink. The old owner's original thread stays in the
        file behind a floor: the adopter reads only what is written after the adoption, and the
        events of what the old owner filed are no longer theirs to get.
        """
        with self._tx() as conn:
            bot = self._shared_row(conn, bot_id)
            if bot["kind"] != "custom" or bot["frozen_reason"] != OWNER_LEFT:
                raise Missing() if bot["frozen_at"] is None else Conflict()
            old = bot["user_id"]
            if adopter_id == old:
                raise Conflict()
            if (
                conn.execute(
                    "SELECT COUNT(*) FROM bots WHERE user_id=? AND shared=1", (adopter_id,)
                ).fetchone()[0]
                >= MAX_SHARED_BOTS
                or conn.execute(
                    "SELECT COUNT(*) FROM bots WHERE user_id=? AND archived_at IS NULL",
                    (adopter_id,),
                ).fetchone()[0]
                >= 50
            ):
                raise Limit()
            floor = conn.execute(
                "SELECT COALESCE(MAX(id),0) FROM bot_messages WHERE bot_id=?", (bot_id,)
            ).fetchone()[0]
            conn.execute(
                "UPDATE bot_work SET user_id=? WHERE bot_id=? AND user_id IS NULL", (old, bot_id)
            )
            conn.execute(
                "UPDATE bot_forwards SET user_id=? WHERE bot_id=? AND user_id IS NULL",
                (old, bot_id),
            )
            version = bot["access_version"] + 1
            if conn.execute(
                "UPDATE bot_shares SET revoked_at=?, revoked_by=?, revoke_reason='unshared' "
                "WHERE bot_id=? AND user_id=? AND revoked_at IS NULL",
                (stamp(), adopter_id, bot_id, adopter_id),
            ).rowcount:
                _row(conn, bot_id, version, adopter_id, "unshare", member=adopter_id)
            conn.execute(
                "UPDATE bots SET user_id=?, frozen_at=NULL, frozen_reason=NULL, thread_floor=?, "
                "read_upto=MAX(read_upto, ?), access_version=? WHERE id=?",
                (adopter_id, floor, floor, version, bot_id),
            )
            _row(conn, bot_id, version, adopter_id, "adopt", member=old)
            return {"version": version, "previous_owner": old}

    def shared_bots(self) -> list[dict]:
        """Every shared bot of the workspace for its workspace owner: who owns it, who it is shared
        with and what it may reach. Never a thread, a message or a note."""
        with self._tx(write=False) as conn:
            found = []
            for bot in conn.execute(
                "SELECT * FROM bots WHERE shared=1 ORDER BY created_at, id"
            ).fetchall():
                found.append(
                    {
                        "id": bot["id"],
                        "name": bot["name"],
                        "job": bot["job"],
                        "owner_id": bot["user_id"],
                        "archived": bot["archived_at"] is not None,
                        "frozen": bot["frozen_at"] is not None,
                        "frozen_reason": bot["frozen_reason"],
                        "members": [
                            {
                                "user_id": s["user_id"],
                                "talk": bool(s["talk"]),
                                "see_shared": bool(s["see_shared"]),
                            }
                            for s in conn.execute(
                                "SELECT * FROM bot_shares WHERE bot_id=? AND revoked_at IS NULL "
                                "ORDER BY shared_at, user_id",
                                (bot["id"],),
                            )
                        ],
                        "access": [
                            {"kind": a["kind"], "ref": a["ref"], "mode": a["mode"]}
                            for a in conn.execute(
                                "SELECT * FROM bot_access WHERE bot_id=? ORDER BY kind, ref",
                                (bot["id"],),
                            )
                        ],
                    }
                )
            return found


def member_removed(gateway, workspace_id, user_id, actor_id) -> dict | None:
    """The hook for a member removed from a workspace: every removal path calls it once the
    membership row is gone (the control database has none yet; the sweep below catches the rest).

    Never creates a bots file for a workspace that has none, and never raises into the caller.
    """
    from commonplace.bots import store

    try:
        if not store.exists(gateway, workspace_id):
            return None
        done = store.open_bots(gateway, workspace_id).member_left(user_id, actor_id)
    except Exception as exc:  # removing a member must not fail on a bots file
        log_failure(logger, "Bot shares not ended", exc)
        return None
    if done["shares"] or done["frozen"]:
        gateway.db.audit(
            action="bot_lifecycle:set",
            decision="ok",
            workspace_id=workspace_id,
            user_id=None if actor_id == "system" else actor_id,
            reason=f"member_removed member={user_id} shares={done['shares']} frozen={done['frozen']}",
            target_ids=[user_id],
        )
    return done


def sweep(gateway, workspace_id) -> None:
    """Apply the removal hook to people who left the workspace without it having run (a removal
    path that bypassed the hook). Re-adding before a sweep cannot be undone, so removal paths
    call the hook themselves; this only closes the gap for the rest."""
    from commonplace.bots import store

    if not store.exists(gateway, workspace_id):
        return
    bots = store.open_bots(gateway, workspace_id)
    for user_id in bots.people():
        if gateway.db.role(workspace_id, user_id) is None:
            member_removed(gateway, workspace_id, user_id, "system")
