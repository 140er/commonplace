"""One explicit composer intent, routed through existing chat/control authority."""

import json

from . import model_effort

MAX_TASK_CANDIDATES = 256

_SCHEMA = """
CREATE TABLE IF NOT EXISTS coworker_turns (
 conversation_id TEXT NOT NULL REFERENCES conversations(id), actor TEXT NOT NULL,
 request_id TEXT NOT NULL, intent TEXT NOT NULL, mode TEXT NOT NULL, reason TEXT,
 job_id INTEGER REFERENCES jobs(id), model TEXT, reasoning_effort TEXT,
 PRIMARY KEY(conversation_id,actor,request_id)
);
CREATE TRIGGER IF NOT EXISTS coworker_turns_no_update BEFORE UPDATE ON coworker_turns
BEGIN SELECT RAISE(ABORT, 'coworker turns are append-only'); END;
CREATE TRIGGER IF NOT EXISTS coworker_turns_no_delete BEFORE DELETE ON coworker_turns
BEGIN SELECT RAISE(ABORT, 'coworker turns are append-only'); END;
"""


def ensure_schema(conn):
    conn.executescript(_SCHEMA)


def _running(store, conn, user_id, cid, text, bot_id, session_hash):
    target = store._target(conn, cid, text, bot_id)
    if target is None:
        model_effort.deny("bot_required")
    rows = conn.execute(
        # A front job may be done while its canonical worker is still active.
        # Ignore ordinary finished chat jobs. Bound canonical projections and
        # refuse a truncated origin set rather than guess or fall back to chat.
        "SELECT j.* FROM jobs j JOIN messages m ON m.id=j.message_id "
        "WHERE j.conversation_id=? AND j.bot_id=? AND (j.task IS NOT NULL OR "
        "(j.state IN ('done','failed','interrupted','uncertain') AND m.text LIKE '/task %')) "
        "ORDER BY j.id DESC LIMIT ?",
        (cid, target, MAX_TASK_CANDIDATES + 1),
    ).fetchall()
    if len(rows) > MAX_TASK_CANDIDATES:
        model_effort.deny("task_history_limit", 409)
    active = []
    for row in rows:
        view = store._progress_job(conn, row, user_id, session_hash=session_hash)
        progress = view.get("task_progress", {})
        if progress.get("availability") != "available":
            if store._progress_candidate(conn, row):
                model_effort.deny("not_found", 404)
            continue
        if progress.get("status") not in ("todo", "doing"):
            continue
        # Apply control authority before looking at owner/requester-only runner
        # details. A shared reader must not turn that redaction into fallback.
        store._control_context(conn, user_id, row["id"], session_hash)
        if (progress.get("runner") or {}).get("state") not in (
            "queued",
            "starting",
            "started",
            "uncertain",
        ):
            continue
        # A readable run never implies permission to steer it. Refusal cannot
        # fall through to chat, even when the caller offered fallback.
        active.append(row["id"])
    if len(active) > 1:
        model_effort.deny("ambiguous_running_job", 409)
    return active[0] if active else None


def post(
    store,
    user_id,
    conversation_id,
    text,
    request_id,
    bot_id=None,
    *,
    mode="chat",
    fallback=False,
    session_hash=None,
    **overrides,
):
    from .store import MAX_CONTROL_TEXT, MAX_TEXT, _id, _text

    if not isinstance(mode, str) or mode not in ("steer", "chat") or type(fallback) is not bool:
        model_effort.deny()
    _text(text, MAX_CONTROL_TEXT if mode == "steer" else MAX_TEXT)
    _id(request_id)
    if bot_id is not None:
        _id(bot_id)
    if set(overrides) - {"model", "reasoning_effort"}:
        model_effort.deny()
    intent = json.dumps(
        {"text": text, "bot_id": bot_id, "mode": mode, "fallback": fallback, **overrides},
        sort_keys=True,
    )
    actor = "user:" + user_id
    with store._transaction() as conn:
        conversation = store._conversation(conn, user_id, conversation_id)
        if store._authority:
            store._authority.job({"user_id": user_id, "session_hash": session_hash})
        previous = conn.execute(
            "SELECT * FROM coworker_turns WHERE conversation_id=? AND actor=? AND request_id=?",
            (conversation_id, actor, request_id),
        ).fetchone()
        if previous:
            if previous["intent"] != intent:
                model_effort.deny("request_conflict", 409)
            selected = {
                "model": previous["model"],
                "reasoning_effort": previous["reasoning_effort"],
            }
            applied, reason, job_id = previous["mode"], previous["reason"], previous["job_id"]
        else:
            # Refuse a mode switch using an existing legacy chat retry key too.
            if conn.execute(
                "SELECT 1 FROM messages WHERE conversation_id=? AND actor=? AND request_id=?",
                (conversation_id, actor, request_id),
            ).fetchone():
                model_effort.deny("request_conflict", 409)
            _, selected = model_effort.effective(
                conn, store._model_settings, conversation, overrides
            )
            job_id = (
                _running(store, conn, user_id, conversation_id, text, bot_id, session_hash)
                if mode == "steer"
                else None
            )
            reason = None
            applied = mode
            if mode == "steer" and job_id is None:
                if not fallback:
                    model_effort.deny("no_running_job", 409)
                applied, reason = "chat", "no_running_job"
            conn.execute(
                "INSERT INTO coworker_turns VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    conversation_id,
                    actor,
                    request_id,
                    intent,
                    applied,
                    reason,
                    job_id,
                    selected["model"],
                    selected["reasoning_effort"],
                ),
            )
    if applied == "steer":
        result = store.control_task(
            user_id, job_id, "follow_up", request_id, text, session_hash=session_hash, **overrides
        )
        # Existing controls save a canonical comment; they cannot change a live
        # worker's model or promise it consumed a follow-up.
        result["model_effort_applied"] = False
    else:
        result = store.post_message(
            user_id,
            conversation_id,
            text,
            request_id,
            bot_id,
            session_hash=session_hash,
            **overrides,
        )
    result.update(mode=applied, requested_mode=mode, **selected)
    if reason:
        result["reason"] = reason
    return result
