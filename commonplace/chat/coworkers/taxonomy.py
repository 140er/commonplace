"""Additive rail metadata, computed only from the current person's readable rows.

Legacy coworker personas are workspace-visible, distinct from person-owned team
bots. No names or connection labels are used to bridge those registries. The
team projection reuses BotStore.roster, including its default-off sharing gate.
"""

import json

from mempalace.config import sanitize_name

from . import model_effort

SECTION_MAX = 60
SCHEMA = """
CREATE TABLE IF NOT EXISTS coworker_sections (
 bot_id TEXT PRIMARY KEY REFERENCES bots(id), section TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_directory ON jobs(conversation_id,bot_id,id);
"""
_STATES = {
    "pending": "working",
    "running": "working",
    "done": "live",
    "needs_configuration": "waiting",
    "uncertain": "waiting",
}
_PRIORITY = {"idle": 0, "live": 1, "working": 2, "waiting": 3}
_WORDS = {"idle": "Idle", "live": "Ready", "working": "Working", "waiting": "Needs you"}


def _state(coworkers):
    return max((c["state"] for c in coworkers), key=_PRIORITY.get, default="idle")


def _summary(coworkers):
    if _state(coworkers) == "waiting":
        return "Needs you"
    count = len(coworkers)
    return f"{count} coworker" + ("" if count == 1 else "s")


def _visible(store, bots):
    """Batch the connection read, reusing the control DB's authentication checks."""
    authority = store._authority
    if authority is None:
        return bots
    db = authority.gateway.db
    ids = [b["connection_id"] for b in bots if b["connection_id"] is not None]
    if not ids:
        return bots
    with db._lock:
        rows = db._conn.execute(
            "SELECT c.*,u.email AS user_email,u.disabled_at AS user_disabled,m.role,r.kind AS runner_kind "
            "FROM connections c JOIN users u ON u.id=c.user_id "
            "LEFT JOIN memberships m ON m.workspace_id=c.workspace_id AND m.user_id=c.user_id "
            "LEFT JOIN runner_connections r ON r.connection_id=c.id "
            f"WHERE c.id IN ({','.join('?' for _ in ids)}) AND c.workspace_id=?",
            (*ids, authority.workspace_id),
        ).fetchall()
        visible = {row["id"] for row in rows if db._connection_principal(row) is not None}
    return [b for b in bots if b["connection_id"] is None or b["connection_id"] in visible]


def directory(store, user_id, *, owns=False):
    """Two bounded queries, including one batched audience/unread/job lookup.

    Counts and job summaries never include a conversation of another person.
    Only the latest job for each (readable conversation, bot) represents that
    coworker; a running job in another person's private thread contributes zero.
    """
    from .store import MAX_BOTS, MAX_CONVERSATIONS

    with store._transaction() as conn:
        store._user(user_id)
        rows = conn.execute(
            "SELECT b.*,s.section FROM bots b LEFT JOIN coworker_sections s ON s.bot_id=b.id "
            "ORDER BY b.created_at,b.id LIMIT ?",
            (MAX_BOTS,),
        ).fetchall()
        visible = _visible(store, rows)
        bots = {
            row["id"]: {
                **store._bot(row),
                "section": row["section"],
                "private": False,
                "unread": 0,
                "coworkers": [],
            }
            for row in visible
        }
        rows = conn.execute(
            "WITH readable AS (SELECT c.* FROM conversations c JOIN members m "
            "ON m.conversation_id=c.id WHERE m.user_id=? ORDER BY c.created_at DESC,c.id LIMIT ?) "
            "SELECT c.*,cs.requested AS model_choices,cb.bot_id,j.id AS job_id,j.state AS job_state, "
            "(SELECT json_group_array(user_id) FROM (SELECT user_id FROM members "
            "WHERE conversation_id=c.id ORDER BY user_id)) AS member_ids, "
            "(SELECT count(*) FROM messages msg WHERE msg.conversation_id=c.id "
            "AND msg.actor<>? AND msg.id>coalesce(cr.last_read,0)) AS unread "
            "FROM readable c LEFT JOIN coworker_reads cr ON cr.conversation_id=c.id AND cr.user_id=? "
            "LEFT JOIN coworker_selections cs ON cs.kind='conversation' AND cs.object_id=c.id "
            "LEFT JOIN conversation_bots cb ON cb.conversation_id=c.id "
            "LEFT JOIN jobs j ON j.id=(SELECT max(id) FROM jobs "
            "WHERE conversation_id=c.id AND bot_id=cb.bot_id) "
            "ORDER BY c.created_at DESC,c.id,cb.bot_id",
            (user_id, MAX_CONVERSATIONS, "user:" + user_id, user_id),
        ).fetchall()
        conversations, hidden = {}, set()
        for row in rows:
            cid, bid = row["id"], row["bot_id"]
            if bid is not None and bid not in bots:
                hidden.add(cid)
            item = conversations.setdefault(
                cid,
                {
                    **{key: row[key] for key in ("id", "kind", "title", "board", "created_at")},
                    **model_effort.describe(
                        conn,
                        store._model_settings,
                        row,
                        choices=json.loads(row["model_choices"] or "{}"),
                    ),
                    "member_ids": json.loads(row["member_ids"]),
                    "bot_ids": [],
                    "section": None,
                    "private": row["kind"] == "dm",
                    "unread": row["unread"],
                    "coworkers": [],
                },
            )
            if bid is not None:
                item["bot_ids"].append(bid)
            if row["job_id"] is not None and bid in bots:
                state = _STATES.get(row["job_state"], "idle")
                item["coworkers"].append(
                    {
                        "id": row["job_id"],
                        "bot_id": bid,
                        "conversation_id": cid,
                        "state": state,
                        "summary": _WORDS[state],
                    }
                )
        conversations = {cid: item for cid, item in conversations.items() if cid not in hidden}
        for item in conversations.values():
            if len(item["bot_ids"]) == 1:
                item["section"] = bots[item["bot_ids"][0]]["section"]
            for bid in item["bot_ids"]:
                bots[bid]["unread"] += item["unread"]
                bots[bid]["coworkers"].extend(c for c in item["coworkers"] if c["bot_id"] == bid)
        for item in [*bots.values(), *conversations.values()]:
            item.update(
                state=_state(item["coworkers"]),
                summary=_summary(item["coworkers"]),
                coworker_count=len(item["coworkers"]),
            )
        views = list(conversations.values())
        if owns:
            from .owns import project

            views = project(store._authority, conn, user_id, views)
        return {"bots": list(bots.values()), "conversations": views}


def set_section(store, user_id, bot_id, section):
    """Workspace-owner configuration; audit identifiers only, never the section text."""
    from .store import StoreError, _id

    authority = store._authority
    if authority is None:
        raise StoreError("owner_required", 403)
    _id(bot_id)
    with store._transaction() as conn:
        store._user(user_id, owner=True)
        bot = conn.execute("SELECT * FROM bots WHERE id=?", (bot_id,)).fetchone()
        if bot is None:
            raise StoreError("not_found", 404)
        store._bot_access(bot)
        if section is not None:
            try:
                section = sanitize_name(section, "section")
                if len(section) > SECTION_MAX:
                    raise ValueError()
            except (ValueError, TypeError):
                raise StoreError() from None
        old = conn.execute(
            "SELECT section FROM coworker_sections WHERE bot_id=?", (bot_id,)
        ).fetchone()
        if (old[0] if old else None) != section:
            authority.gateway.db.audit(
                action="coworkers.section",
                decision="ok",
                workspace_id=authority.workspace_id,
                user_id=user_id,
                target_ids=[bot_id],
                reason="clear" if section is None else "set",
            )
            if section is None:
                conn.execute("DELETE FROM coworker_sections WHERE bot_id=?", (bot_id,))
            else:
                conn.execute(
                    "INSERT INTO coworker_sections VALUES (?,?) "
                    "ON CONFLICT(bot_id) DO UPDATE SET section=excluded.section",
                    (bot_id, section),
                )
        return {"bot_id": bot_id, "section": section}


def team_bots(gateway, principal):
    """Read-only projection of exactly the existing person's bot roster.

    Reuse its original-owner read mark or member private-thread read mark;
    never sum over other people's private threads, including for a bot owner.
    The gateway caches this store per workspace. Its owner controls cleanup;
    a request retains the shared handle on both success and exception.
    """
    from commonplace.bots import limits
    from commonplace.bots.errors import stamp
    from commonplace.bots.store import exists, open_bots

    if not exists(gateway, principal.workspace_id):
        return []
    store = open_bots(gateway, principal.workspace_id)
    with gateway.db._lock, store._tx(write=False) as conn:
        roster = store.roster(principal.user_id)
        pending = {
            (r["bot_id"], r["thread_id"])
            for r in conn.execute(
                "SELECT bot_id,thread_id FROM bot_pending WHERE user_id=? AND state='open' AND expires_at>?",
                (principal.user_id, stamp()),
            )
        }
        working = limits.REGISTRY.running_bots(principal.workspace_id, principal.user_id)
        result = []
        for bot in roster:
            state = (
                "waiting"
                if (bot["id"], bot["thread"]) in pending
                else "working"
                if bot["id"] in working
                else "live"
                if bot["last"] is not None and bot["last"]["role"] != "user" and not bot["frozen"]
                else "idle"
            )
            result.append(
                {
                    **{
                        key: bot[key]
                        for key in ("id", "name", "mine", "shared", "thread", "unread")
                    },
                    "section": None,
                    "private": not bot["shared"],
                    "state": state,
                    "summary": _WORDS[state],
                }
            )
        return result
