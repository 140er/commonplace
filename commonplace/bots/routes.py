"""``/api/bots``: a person's bots, their threads and memory. Person sessions, or the person's own
app holding ``bots:chat`` (``bots:confirm`` for confirm).

    GET    /api/bots                       {bots, model:{ready,label,code}, can_configure}
    POST   /api/bots                       {name, job?, instructions?, board?} -> 201 {bot}
    PATCH  /api/bots/<id>                  {name?, job?, instructions?, archived?} -> {bot} (owner)
    GET    /api/bots/<id>/messages         ?thread=&after=&before=&limit= -> {bot, messages, waiting, running}
    POST   /api/bots/<id>/messages         ?thread= {text, request_id} -> text/event-stream
                                           (?stream=0 -> {message, reply} when the turn ends)
    POST   /api/bots/<id>/stop             ?thread= {request_id} -> {ok}
    POST   /api/bots/<id>/confirm          ?thread= {pending, accept} -> {message}
    POST   /api/bots/<id>/read             ?thread= {upto} -> {ok}
    GET    /api/bots/<id>/memory           ?own=1 {memories}
    POST   /api/bots/<id>/memory           ?own=1 {text} -> {memory}
    DELETE /api/bots/<id>/memory/<mid>     ?own=1 {ok}
    GET    /api/bots/<id>/access           {version, entries}   (owner and members with a share)
    PUT    /api/bots/<id>/access           {version, entries} -> {version, entries} (owner only,
                                           web session only, compare-and-set on version)
    GET    /api/bots/<id>/access/log       ?after=&limit= -> {log}
    GET    /api/bots/<id>/shares           {shares} (owner: all; member: their own row)
    POST   /api/bots/<id>/shares           {user, talk, see_shared} -> {share} (owner, web session)
    PATCH  /api/bots/<id>/shares/<user>    {talk?, see_shared?} -> {share} (owner, web session)
    DELETE /api/bots/<id>/shares/<user>    {ok} (owner, web session; idempotent)
    GET    /api/bots/<id>/threads          {threads, own}
    POST   /api/bots/<id>/threads          {visibility, title?} -> {thread} (private: once; shared: new)
    PATCH  /api/bots/<id>/threads/<tid>    {archived} -> {thread} (the opener)
    GET    /api/bots/settings              {sharing, can_configure}
    PATCH  /api/bots/settings              {sharing} -> {sharing} (workspace owner, web session)

A bot id that is unknown, belongs to someone else or is shared with another person is 404. A
bot's owner has the original thread; a member's thread is their own private one, opened on first
use, and every route that takes ``?thread=`` answers 404 for a thread that is not theirs.
``HANDLERS`` maps (method, path tail) to a handler, so a later route is one more entry.
"""

import json
import queue
import re
import time

from commonplace import bots_grant, redact
from commonplace.boards.service import BoardService
from commonplace.bots import (
    access,
    auth,
    blocks,
    limits,
    modelcfg,
    shares,
    sharing_setting,
    store,
    tools,
    turn,
    updates,
)
from commonplace.bots.errors import SharingOff, Unsupported
from commonplace.extensions import ToolContext, ToolError
from commonplace.http_util import path_parts, query, read_json, send_json

PREFIX = "/api/bots/"
PREVIEW_CHARS = 200
PING_S = 10  # SSE heartbeat
WRITE_TIMEOUT_S = 10  # one blocked socket write ends the relay, never the turn


def _error(h, status, code, text):
    send_json(h, status, {"error": text, "code": code})


def _bots(gateway, principal):
    return store.open_bots(gateway, principal.workspace_id)


def _asks(gateway, principal):
    """This person's open decisions across every project they can read."""
    try:
        return BoardService(ToolContext(principal=principal, gateway=gateway)).needs_me()["asks"]
    except ToolError:
        return []  # a member without the needs-me tool has none waiting


def _who(gateway, user_id) -> dict:
    """A member as other members may see them: id and name (the email's first part if unnamed)."""
    return {"id": user_id, "name": access.display_name(gateway, user_id)}


def _view(gateway, bot, asks, working=()) -> dict:
    waiting = sum(1 for ask in asks if bot["board"] in (None, ask["board"]))
    last = bot["last"]
    return {
        **{key: bot[key] for key in ("id", "name", "job", "instructions", "kind", "board")},
        "archived": bot["archived"],
        "mine": bot["mine"],
        "shared": bot["shared"],
        "owner": _who(gateway, bot["owner_id"]),
        "perms": bot["perms"],
        "frozen": bot["frozen"],
        "last": None
        if last is None
        else {
            "role": last["role"],
            "text": redact.redact(last["text"])[0][:PREVIEW_CHARS],
            "at": last["at"],
        },
        "unread": bot["unread"],
        "waiting": waiting,
        "state": "needs_you" if waiting else "working" if bot["id"] in working else "idle",
    }


def _running(principal, bot_id, thread=None) -> bool:
    return limits.REGISTRY.running(principal.workspace_id, bot_id, thread) is not None


def _working(gateway, principal, bots) -> set[str]:
    """Bots with a turn running, or whose filed work is in progress."""
    return updates.working_bots(gateway, principal, bots) | limits.REGISTRY.running_bots(
        principal.workspace_id, principal.user_id
    )


def _bot(gateway, principal, bot_id) -> dict:
    bots = _bots(gateway, principal)
    return _view(
        gateway,
        bots.roster(principal.user_id, bot_id)[0],
        _asks(gateway, principal),
        _working(gateway, principal, bots),
    )


def _model(gateway, principal) -> dict:
    config, _key, code = modelcfg.resolve(gateway.db, principal.workspace_id)
    return {
        "ready": not code,
        "label": f"{config.provider}/{config.model}" if config else "",
        "code": code,
    }


def _listing(gateway, h, principal):
    boards = BoardService(ToolContext(principal=principal, gateway=gateway)).board_list()
    bots = _bots(gateway, principal)
    bots.ensure_defaults(principal.user_id, [(b["slug"], b["name"]) for b in boards])
    asks = _asks(gateway, principal)
    working = _working(gateway, principal, bots)
    send_json(
        h,
        200,
        {
            "bots": [_view(gateway, bot, asks, working) for bot in bots.roster(principal.user_id)],
            "model": _model(gateway, principal),
            "can_configure": principal.role == "owner",
        },
    )


def _body(h, allowed):
    """The JSON body, or None after a 400 for a malformed one; unknown fields are invalid."""
    try:
        body = read_json(h)
    except ValueError:
        return None
    if set(body) - allowed:
        raise store.Invalid("unknown field")
    return body


def _create(gateway, h, principal):
    body = _body(h, {"name", "job", "instructions", "board"})
    if body is None:
        return
    board = body.get("board")
    if board is not None:
        if not isinstance(board, str):
            raise store.Invalid("invalid board")
        service = BoardService(ToolContext(principal=principal, gateway=gateway))
        if board not in {b["slug"] for b in service.board_list()}:
            _error(h, 400, "invalid", "Unknown project")
            return
    bot = _bots(gateway, principal).create(
        principal.user_id,
        body.get("name"),
        body.get("job", ""),
        body.get("instructions", ""),
        board=board,
    )
    send_json(h, 201, {"bot": _bot(gateway, principal, bot["id"])})


THREAD_ID = re.compile(r"[0-9a-f]{32}")
NO_THREAD_YET = object()  # a member who has not opened their private thread


def _thread(h, bots, principal, bot_id, create=False):
    """The thread this request acts on: ``?thread=``, else the caller's own.

    ``None`` is the owner's original thread. A member's own thread is opened on first use when
    ``create`` (``NO_THREAD_YET`` otherwise). Whether the caller reaches it is the store's call.
    """
    asked = query(h).get("thread")
    if asked is not None:
        if THREAD_ID.fullmatch(asked) is None:
            raise store.Missing()
        return asked
    bot = bots.viewable(bot_id, principal.user_id)
    if bot["owner_id"] == principal.user_id:
        return None
    own = bots.private_thread(bot_id, principal.user_id)
    if own is None and create:
        own = bots.thread_open(bot_id, principal.user_id, "private")["id"]
    return NO_THREAD_YET if own is None else own


def _patch(gateway, h, principal, bot_id, rest):
    if rest:
        _error(h, 404, "not_found", "Not found")
        return
    body = _body(h, {"name", "job", "instructions", "archived"})
    if body is None:
        return
    bots = _bots(gateway, principal)
    if bots.viewable(bot_id, principal.user_id)["owner_id"] != principal.user_id:
        _error(h, 403, "owner_only", "Only the bot's owner can change that.")
        return
    bots.update(bot_id, principal.user_id, **body)
    send_json(h, 200, {"bot": _bot(gateway, principal, bot_id)})


def _messages(gateway, h, principal, bot_id, rest):
    params = query(h)
    try:
        cursors = {key: int(params[key]) for key in ("after", "before", "limit") if key in params}
    except ValueError:
        raise store.Invalid("invalid cursor") from None
    bots = _bots(gateway, principal)
    thread = _thread(h, bots, principal, bot_id)
    if thread is NO_THREAD_YET:
        page = []
    else:
        turn.recover(gateway, principal.workspace_id, principal.user_id, bot_id, thread)
        page = bots.messages(bot_id, principal.user_id, thread_id=thread, **cursors)
    reader = blocks.Reader(gateway, principal)
    views = [blocks.message_view(reader, m) for m in page]
    if thread not in (None, NO_THREAD_YET) and bots.thread_shared(bot_id, thread):
        for view, stored in zip(views, page):  # who spoke, for a thread several people write in
            if stored.get("author_id"):
                view["author"] = _who(gateway, stored["author_id"])
    asks = _asks(gateway, principal)
    bot = bots.roster(principal.user_id, bot_id)[0]
    waiting = [
        {"type": "decision", "card": ask["number"]}
        for ask in asks
        if bot["board"] in (None, ask["board"])
    ]
    running = thread is not NO_THREAD_YET and _running(principal, bot_id, thread)
    send_json(
        h,
        200,
        {
            "bot": _view(gateway, bot, asks, _working(gateway, principal, bots)),
            "messages": views,
            "waiting": blocks.hydrate(reader, waiting),
            "running": running,
        },
    )


def _read(gateway, h, principal, bot_id, rest):
    body = _body(h, {"upto"})
    if body is None:
        return
    bots = _bots(gateway, principal)
    thread = _thread(h, bots, principal, bot_id)
    if thread is not NO_THREAD_YET:
        bots.mark_read(bot_id, principal.user_id, body.get("upto"), thread)
    send_json(h, 200, {"ok": True})


def _note(memory) -> dict:
    return {**memory, "text": redact.redact(memory["text"])[0]}


def _own_notes(h) -> bool:
    return query(h).get("own") == "1"


def _memory_list(gateway, h, principal, bot_id, rest):
    notes = _bots(gateway, principal).memory_list(bot_id, principal.user_id, own=_own_notes(h))
    send_json(h, 200, {"memories": [_note(n) for n in notes]})


def _memory_owner_only(gateway, h, principal, bot_id) -> bool:
    """False (after a 403) when a member writes bot-level notes: those are the owner's."""
    if _own_notes(h) or (
        _bots(gateway, principal).viewable(bot_id, principal.user_id)["owner_id"]
        == principal.user_id
    ):
        return True
    _error(h, 403, "owner_only", "Only the bot's owner can change that.")
    return False


def _memory_add(gateway, h, principal, bot_id, rest):
    body = _body(h, {"text"})
    if body is None or not _memory_owner_only(gateway, h, principal, bot_id):
        return
    memory = _bots(gateway, principal).memory_add(bot_id, principal.user_id, body.get("text"))
    send_json(h, 200, {"memory": _note(memory)})


def _memory_remove(gateway, h, principal, bot_id, rest):
    if not _memory_owner_only(gateway, h, principal, bot_id):
        return
    try:
        memory_id = int(rest[0]) if len(rest) == 1 else None
    except ValueError:
        memory_id = None
    if not _bots(gateway, principal).memory_remove(
        bot_id, principal.user_id, memory_id, own=_own_notes(h)
    ):
        _error(h, 404, "not_found", "Not found")
        return
    send_json(h, 200, {"ok": True})


# Talking to a bot


def _frame(event, data) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


def _relay(h, frames) -> None:
    """Write an SSE response from ``frames`` (a list, or a turn queue ending in None).

    A broken pipe or a write blocked for ``WRITE_TIMEOUT_S`` ends only this relay.
    """
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream; charset=utf-8")
    h.send_header("Cache-Control", "no-store")
    h.send_header("X-Accel-Buffering", "no")
    h.end_headers()
    try:
        h.connection.settimeout(WRITE_TIMEOUT_S)
        h.wfile.flush()
        items = iter(frames) if isinstance(frames, list) else None
        while True:
            if items is not None:
                item = next(items, None)
            else:
                try:
                    item = frames.get(timeout=PING_S)
                except queue.Empty:
                    h.wfile.write(b": ping\n\n")
                    h.wfile.flush()
                    continue
            if item is None:
                return
            h.wfile.write(_frame(*item))
            h.wfile.flush()
    except (OSError, ValueError):
        return  # the client went away; the turn goes on and its reply is stored


def _final(events) -> dict:
    return next(data["message"] for event, data in events if event in ("done", "error"))


def _send(gateway, h, principal, bot_id, rest):
    started = time.monotonic()
    stream = query(h).get("stream", "1") != "0"
    body = _body(h, {"text", "request_id"})
    if body is None:
        return
    text, request_id = body.get("text"), body.get("request_id")
    if not isinstance(text, str) or len(text) > store.TEXT_MAX or not text.strip():
        raise store.Invalid("text")
    if not isinstance(request_id, str) or store.REQUEST_ID.fullmatch(request_id) is None:
        raise store.Invalid("request_id")
    bots = _bots(gateway, principal)
    bot = bots.viewable(bot_id, principal.user_id)
    thread = _thread(h, bots, principal, bot_id, create=True)
    if thread is not None:
        reach = bots.reach(principal.user_id, bot_id, thread)
        if reach.read_only:  # reads the shared thread but may not talk in it
            _error(h, 403, "read_only", "You can read this thread but not write in it.")
            return
        if not reach.can_write:
            raise store.Conflict()  # an archived thread or bot takes no new message
    seen = bots.message_for_request(bot_id, principal.user_id, request_id, thread)
    if seen is not None:
        if seen["text"] != text:
            raise store.Conflict()
        _replay(gateway, h, principal, bot_id, seen, stream, thread)
        return
    run = turn.TurnRun(gateway, principal, bot, request_id, started, thread_id=thread)
    try:
        limits.REGISTRY.admit(run)
    except limits.Busy as busy:
        _error(h, busy.status, "busy", "Busy, try again")
        return
    try:
        # A same-id request may have finished between the first look and admission.
        seen = bots.message_for_request(bot_id, principal.user_id, request_id, thread)
        if seen is None:
            bots.lease_start(bot_id, principal.user_id, run.key)
            message = bots.add_user_message(
                bot_id, principal.user_id, text, request_id, thread_id=thread
            )
    except BaseException:
        bots.lease_end(bot_id, run.key)
        limits.REGISTRY.release(run)
        raise
    if seen is not None:
        limits.REGISTRY.release(run)
        if seen["text"] != text:
            raise store.Conflict()
        _replay(gateway, h, principal, bot_id, seen, stream, thread)
        return
    run.start(message)
    if stream:
        _relay(h, run.queue)
        return
    run.finished.wait()
    if run.reply is None:  # the reply could not be stored; the stream would say the same
        text = turn.SENTENCES["model_error"]
        send_json(h, 500, {"error": text, "code": "model_error", "message": run.view(message)})
        return
    send_json(h, 200, {"message": run.view(message), "reply": run.view(run.reply)})


def _replay(gateway, h, principal, bot_id, message, stream, thread=None):
    events = turn.replay(gateway, principal, bot_id, message, thread)
    if stream:
        _relay(h, events)
        return
    running = limits.REGISTRY.running(principal.workspace_id, bot_id, thread)
    if events[-1][0] == "running" and running is not None:
        running.finished.wait()
        events = turn.replay(gateway, principal, bot_id, message, thread)
    if events[-1][0] == "running":
        _error(h, 409, "busy", "Still running")
        return
    send_json(h, 200, {"message": events[0][1]["message"], "reply": _final(events)})


def _stop(gateway, h, principal, bot_id, rest):
    body = _body(h, {"request_id"})
    if body is None:
        return
    bots = _bots(gateway, principal)
    thread = _thread(h, bots, principal, bot_id)
    if thread is not NO_THREAD_YET:
        bots.reach(principal.user_id, bot_id, thread)
    running = (
        None
        if thread is NO_THREAD_YET
        else limits.REGISTRY.running(principal.workspace_id, bot_id, thread)
    )
    stopping = (
        running is not None
        and running.request_id == body.get("request_id")
        and running.user_id == principal.user_id  # in a shared thread, only the talker's own turn
    )
    if stopping:
        running.cancel.set()
    send_json(h, 200, {"ok": stopping})


def _confirm(gateway, h, principal, bot_id, rest):
    body = _body(h, {"pending", "accept"})
    if body is None:
        return
    if not isinstance(body.get("pending"), str) or type(body.get("accept")) is not bool:
        raise store.Invalid("pending")
    thread = _thread(h, _bots(gateway, principal), principal, bot_id)
    if thread is NO_THREAD_YET:
        raise store.Missing()
    try:
        message = turn.confirm(gateway, principal, bot_id, body["pending"], body["accept"], thread)
    except turn.Refused as refused:
        if refused.code == "expired":
            _error(h, 409, "expired", "This request expired. Ask again.")
        else:
            _error(h, 409, "conflict", "This was already answered.")
        return
    reader = blocks.Reader(gateway, principal)
    send_json(h, 200, {"message": blocks.message_view(reader, message)})


# Access: what a shared bot may reach. Set only by its owner; read by the owner and members.


def _entry(item) -> dict:
    return {key: item[key] for key in ("kind", "ref", "mode", "set_at")}


def _access_view(listed) -> dict:
    return {"version": listed["version"], "entries": [_entry(e) for e in listed["entries"]]}


def _access_get(gateway, h, principal, bot_id, rest):
    if rest == ["log"]:
        _access_log(gateway, h, principal, bot_id, [])
        return
    if rest:
        _error(h, 404, "not_found", "Not found")
        return
    send_json(h, 200, _access_view(_bots(gateway, principal).access_get(bot_id, principal.user_id)))


def _access_log(gateway, h, principal, bot_id, rest):
    params = query(h)
    try:
        cursors = {key: int(params[key]) for key in ("after", "limit") if key in params}
    except ValueError:
        raise store.Invalid("invalid cursor") from None
    rows = _bots(gateway, principal).access_log(bot_id, principal.user_id, **cursors)
    send_json(h, 200, {"log": rows})


def _audit_access(gateway, principal, bot_id, version, changes) -> None:
    """One control-database row per real change: ids, kinds, refs and modes, never text."""
    shown = [
        f"{c['action']}:{c['kind']}:{c['ref']}:{c['mode_before'] or '-'}>{c['mode_after'] or '-'}"
        for c in changes[:8]
    ]
    more = f" +{len(changes) - 8} more" if len(changes) > 8 else ""
    gateway.db.audit(
        action="bot_access:set",
        decision="ok",
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        reason=f"bot={bot_id} version={version} " + ", ".join(shown) + more,
        target_ids=[bot_id],
    )


def _access_put(gateway, h, principal, bot_id, rest):
    if rest or auth.app(h) is not None:  # web session only: an app asking gets 404
        _error(h, 404, "not_found", "Not found")
        return
    body = _body(h, {"version", "entries"})
    if body is None:
        return
    bots = _bots(gateway, principal)
    bot = bots.viewable(bot_id, principal.user_id)
    if bot["owner_id"] != principal.user_id:
        _error(h, 403, "owner_only", "Only the bot's owner can change that.")
        return
    wanted = shares.check_entries(body.get("entries"))
    current = bots.access_get(bot_id, principal.user_id)
    stored = [(e["kind"], e["ref"], e["mode"]) for e in current["entries"]]
    try:
        access.check_grantable(gateway, principal, wanted, stored, set(tools.REGISTRY))
    except access.OutsideScope:
        _error(h, 403, "outside_your_scope", "You cannot grant what you cannot reach yourself.")
        return
    updated = bots.access_set(bot_id, principal.user_id, body.get("version"), body["entries"])
    if updated["version"] != current["version"]:
        _audit_access(
            gateway,
            principal,
            bot_id,
            updated["version"],
            bots.access_changes(bot_id, principal.user_id, updated["version"]),
        )
    send_json(h, 200, _access_view(updated))


# Sharing: who may reach a bot. Changed only by its owner, in a web session.


def _web_only(h) -> bool:
    """False (after a 404) for an app connection: sharing changes have no app scope."""
    if auth.app(h) is None:
        return True
    _error(h, 404, "not_found", "Not found")
    return False


def _owner(gateway, h, principal, bots, bot_id) -> bool:
    """True for the bot's owner; a 403 for a member who can see it (an unknown one is a 404)."""
    if bots.viewable(bot_id, principal.user_id)["owner_id"] == principal.user_id:
        return True
    _error(h, 403, "owner_only", "Only the bot's owner can change that.")
    return False


def _share_view(gateway, share) -> dict:
    return {
        "user": _who(gateway, share["user_id"]),
        "talk": share["talk"],
        "see_shared": share["see_shared"],
        "shared_at": share["shared_at"],
    }


def _audit_share(gateway, principal, bot_id, member_id, action) -> None:
    """One control-database row per real change: ids and the kind of change, never text."""
    gateway.db.audit(
        action="bot_share:set",
        decision="ok",
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        reason=f"bot={bot_id} member={member_id} {action}",
        target_ids=[bot_id],
    )


def _shares_get(gateway, h, principal, bot_id, rest):
    if rest:
        _error(h, 404, "not_found", "Not found")
        return
    rows = _bots(gateway, principal).shares(bot_id, principal.user_id)
    send_json(h, 200, {"shares": [_share_view(gateway, row) for row in rows]})


def _put_share(gateway, h, principal, bots, bot_id, member_id, talk, see_shared):
    """Share (or change a share), idempotently; 201 for a new share, 200 otherwise."""
    before = {r["user_id"]: r for r in bots.shares(bot_id, principal.user_id)}.get(member_id)
    try:
        share = bots.share(bot_id, principal.user_id, member_id, talk, see_shared)
    except SharingOff:
        _error(h, 409, "sharing_off", "Sharing is switched off for this workspace.")
        return
    except store.Limit:
        _error(h, 409, "limit", "That is as many shares as this bot can have.")
        return
    if before is None:
        _audit_share(gateway, principal, bot_id, member_id, "share")
    elif (before["talk"], before["see_shared"]) != (share["talk"], share["see_shared"]):
        _audit_share(gateway, principal, bot_id, member_id, "permissions")
    send_json(h, 200 if before else 201, {"share": _share_view(gateway, share)})


def _shares_post(gateway, h, principal, bot_id, rest):
    if rest or not _web_only(h):
        _error(h, 404, "not_found", "Not found")
        return
    body = _body(h, {"user", "talk", "see_shared"})
    if body is None:
        return
    bots = _bots(gateway, principal)
    if not _owner(gateway, h, principal, bots, bot_id):
        return
    member = body.get("user")
    if (
        not isinstance(member, str)
        or member == principal.user_id
        or access.person(gateway, member, principal.workspace_id) is None
    ):
        raise store.Invalid("invalid member")  # unknown and non-members look the same
    _put_share(
        gateway,
        h,
        principal,
        bots,
        bot_id,
        member,
        body.get("talk", False),
        body.get("see_shared", False),
    )


def _shares_patch(gateway, h, principal, bot_id, rest):
    if len(rest) != 1 or not _web_only(h):
        _error(h, 404, "not_found", "Not found")
        return
    body = _body(h, {"talk", "see_shared"})
    if body is None:
        return
    bots = _bots(gateway, principal)
    if not _owner(gateway, h, principal, bots, bot_id):
        return
    current = {r["user_id"]: r for r in bots.shares(bot_id, principal.user_id)}.get(rest[0])
    if current is None:
        raise store.Missing()
    _put_share(
        gateway,
        h,
        principal,
        bots,
        bot_id,
        rest[0],
        body.get("talk", current["talk"]),
        body.get("see_shared", current["see_shared"]),
    )


def _shares_delete(gateway, h, principal, bot_id, rest):
    if len(rest) != 1 or not _web_only(h):
        _error(h, 404, "not_found", "Not found")
        return
    bots = _bots(gateway, principal)
    if not _owner(gateway, h, principal, bots, bot_id):
        return
    if bots.revoke(bot_id, principal.user_id, rest[0]):
        _audit_share(gateway, principal, bot_id, rest[0], "unshare")
    send_json(h, 200, {"ok": True})


# Threads: a member's private thread, and shared ones


def _thread_view(row) -> dict:
    return {
        key: row[key]
        for key in ("id", "visibility", "title", "created_at", "archived", "unread")
        if key in row
    }


def _threads_get(gateway, h, principal, bot_id, rest):
    if rest:
        _error(h, 404, "not_found", "Not found")
        return
    bots = _bots(gateway, principal)
    rows = bots.threads(bot_id, principal.user_id)
    try:
        own = bots.private_thread(bot_id, principal.user_id)
    except store.Missing:
        own = None  # a member who may only read shared threads has no private one
    send_json(
        h,
        200,
        {
            "threads": [
                {
                    **_thread_view(r),
                    "mine": r["user_id"] == principal.user_id,
                    **(
                        {"opened_by": _who(gateway, r["user_id"])}
                        if r["visibility"] == "shared"
                        else {}
                    ),
                }
                for r in rows
            ],
            "own": own,
        },
    )


def _threads_post(gateway, h, principal, bot_id, rest):
    if rest:
        _error(h, 404, "not_found", "Not found")
        return
    body = _body(h, {"visibility", "title"})
    if body is None:
        return
    bots = _bots(gateway, principal)
    visibility = body.get("visibility")
    existed = visibility == "private" and bots.private_thread(bot_id, principal.user_id) is not None
    thread = bots.thread_open(bot_id, principal.user_id, visibility, body.get("title", ""))
    send_json(h, 200 if existed else 201, {"thread": _thread_view(thread)})


def _threads_patch(gateway, h, principal, bot_id, rest):
    if len(rest) != 1 or THREAD_ID.fullmatch(rest[0]) is None:
        _error(h, 404, "not_found", "Not found")
        return
    body = _body(h, {"archived"})
    if body is None:
        return
    thread = _bots(gateway, principal).thread_archive(
        bot_id, principal.user_id, rest[0], body.get("archived")
    )
    send_json(h, 200, {"thread": _thread_view(thread)})


# The workspace switch for sharing


def _settings(gateway, principal) -> dict:
    return {
        "sharing": sharing_setting.enabled(gateway.db, principal.workspace_id),
        "can_configure": principal.role == "owner",
    }


def _settings_get(gateway, h, principal):
    send_json(h, 200, _settings(gateway, principal))


def _settings_patch(gateway, h, principal):
    if not _web_only(h):
        return
    body = _body(h, {"sharing"})
    if body is None:
        return
    try:
        sharing_setting.set_enabled(gateway.db, principal, body.get("sharing"))
    except sharing_setting.NotOwner:
        _error(h, 403, "owner_only", "Only a workspace owner can change that.")
        return
    except ValueError:
        raise store.Invalid("sharing") from None
    send_json(h, 200, _settings(gateway, principal))


# (method, first segment after the id) -> handler(gateway, h, principal, bot_id, rest)
HANDLERS = {
    ("PATCH", None): _patch,
    ("GET", "messages"): _messages,
    ("POST", "messages"): _send,
    ("POST", "stop"): _stop,
    ("POST", "confirm"): _confirm,
    ("POST", "read"): _read,
    ("GET", "memory"): _memory_list,
    ("POST", "memory"): _memory_add,
    ("DELETE", "memory"): _memory_remove,
    ("GET", "access"): _access_get,
    ("PUT", "access"): _access_put,
    ("GET", "shares"): _shares_get,
    ("POST", "shares"): _shares_post,
    ("PATCH", "shares"): _shares_patch,
    ("DELETE", "shares"): _shares_delete,
    ("GET", "threads"): _threads_get,
    ("POST", "threads"): _threads_post,
    ("PATCH", "threads"): _threads_patch,
}


def _audit(gateway, h, app, tool, status):
    """One row per write request made through an app connection: ids and the outcome only."""
    gateway.db.audit(
        action=bots_grant.AUDIT_WRITE,
        decision="ok" if status < 400 else "refused" if status < 500 else "error",
        workspace_id=app.workspace_id,
        user_id=app.user_id,
        connection_id=app.connection_id,
        auth=app.auth,
        tool=tool,
    )


def _guarded(gateway, h, run, tool="", scope=None):
    principal = auth.person(gateway, h)
    if principal is None:
        return
    app = auth.app(h)
    recorded = []
    if app is not None and h.command != "GET":
        send_response = h.send_response

        def remember(code, *args):
            # Recorded when the status is sent, so the row exists before the client sees it.
            if not recorded:
                recorded.append(code)
                _audit(gateway, h, app, tool, code)
            return send_response(code, *args)

        h.send_response = remember
    try:
        if app is not None and scope and not app.policy.has_scope(scope):
            _error(h, 403, "scope_required", "This app needs another permission for that.")
            return
        run(principal)
    except store.Missing:
        _error(h, 404, "not_found", "Not found")
    except Unsupported:
        _error(h, 400, "unsupported_kind", "That kind of access cannot be granted yet")
    except SharingOff:
        _error(h, 409, "sharing_off", "Sharing is switched off for this workspace.")
    except store.Invalid:
        _error(h, 400, "invalid", "Invalid request")
    except store.Limit:
        _error(h, 409, "limit", f"At most {store.MAX_LIVE_BOTS} bots")
    except store.Conflict:
        _error(h, 409, "conflict", "Conflict")
    except TimeoutError:
        _error(h, 429, "busy", "Busy, try again")
    finally:
        if app is not None and h.command != "GET" and not recorded:
            recorded.append(500)  # died before answering; the gateway's 500 adds no second row
            _audit(gateway, h, app, tool, 500)


# Tails that need more than bots:chat from an app connection.
SCOPES = {("POST", "confirm"): bots_grant.CONFIRM_SCOPE}


def install(gateway) -> None:
    def dispatch(method):
        def handle(h):
            parts = path_parts(h, PREFIX)
            tail = parts[1] if len(parts) > 1 else None
            handler = HANDLERS.get((method, tail)) if parts else None
            if handler is None:
                return _guarded(
                    gateway, h, lambda _principal: _error(h, 404, "not_found", "Not found")
                )
            _guarded(
                gateway,
                h,
                lambda principal: handler(gateway, h, principal, parts[0], parts[2:]),
                tool=f"{method} {tail or 'bot'}",
                scope=SCOPES.get((method, tail)),
            )

        return handle

    gateway.add_route(
        "GET", "/api/bots", lambda h: _guarded(gateway, h, lambda p: _listing(gateway, h, p))
    )
    gateway.add_route(
        "POST",
        "/api/bots",
        lambda h: _guarded(gateway, h, lambda p: _create(gateway, h, p), tool="POST bots"),
    )
    gateway.add_route(
        "GET",
        "/api/bots/settings",
        lambda h: _guarded(gateway, h, lambda p: _settings_get(gateway, h, p)),
    )
    gateway.add_route(
        "PATCH",
        "/api/bots/settings",
        lambda h: _guarded(
            gateway, h, lambda p: _settings_patch(gateway, h, p), tool="PATCH settings"
        ),
    )
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        gateway.add_prefix_route(method, PREFIX, dispatch(method))
