"""Routes of the learning memory: a person's items and per-bot setting, the owner's switches.

    GET    /api/bots/<id>/learning               {learning}                 (mode, pause, counts)
    PATCH  /api/bots/<id>/learning               {mode?, pause?} -> {learning}
    GET    /api/bots/<id>/learned                {items, counts}
    POST   /api/bots/<id>/learned/forget-all     {} -> {forgotten, notes, floor}
    GET    /api/learned                          ?bot=&scope=&state=&kind=&limit=&offset= -> {items, counts}
    GET    /api/learned/<id>                     {item}
    PATCH  /api/learned/<id>                     {text?, pinned?} -> {item}
    DELETE /api/learned/<id>                     {ok}   (a shared copy: its sharer, or a workspace owner)
    GET    /api/learning/settings                {enabled, shared_enabled, can_configure}
    PATCH  /api/learning/settings                {enabled?, shared_enabled?}      (workspace owner)
    GET    /api/projects/<slug>/learning         {board, shared_enabled, can_configure}
    PATCH  /api/projects/<slug>/learning         {shared_enabled}                 (workspace owner)
    GET    /api/projects/<slug>/learned          {items}   shared items, for members who read the project
    POST   /api/learned/<id>/accept              {} -> {item}   (a proposal becomes active)
    POST   /api/learned/<id>/reject              {} -> {ok}     (a proposal is deleted for good)
    POST   /api/learned/<id>/share               {project?} -> {pending, bot, label}  (a confirm chip;
                                                 the tap is POST /api/bots/<bot>/confirm)

Only a signed-in person reaches these. The one exception is the person's own app holding
``bots:chat`` and ``bots:learned``: it may read the two lists (``GET /api/learned`` and
``GET /api/bots/<id>/learned``) and can change nothing (403 ``read_only`` on any write, 404 on
everything else). An app without ``bots:learned``, an agent token or a machine credential is
answered 404, and a cross-origin write is refused by the gateway. An item or bot that is not the
caller's is 404. Everything off is the default. Each item carries a ``group`` (person, shared,
proposed, earlier, old) and its ``source`` (bot, message and date) for the review list.
"""

from commonplace import bots_grant, redact
from commonplace.boards.service import BoardService
from commonplace.bots import auth, learned, routes, store, tools_learning
from commonplace.bots.learn_review import group
from commonplace.extensions import ToolContext, ToolError
from commonplace.http_util import path_parts, query, send_json

ITEMS = "/api/learned/"


def _learned(gateway, principal) -> learned.Learned:
    return learned.open_learned(gateway, principal.workspace_id)


def _view(item, names=None) -> dict:
    return {
        **item,
        "text": redact.redact(item["text"])[0],
        "note": None if item["note"] is None else redact.redact(item["note"])[0],
        "group": group(item),
        "source": {
            "bot_id": item["bot_id"],
            "bot_name": (names or {}).get(item["bot_id"], ""),
            "message_id": item["source_message_id"],
            "at": item["source_at"],
        },
    }


def _person_only(h, read=False) -> bool:
    """Learned items are the person's. Their own app may only read the lists, and only with
    ``bots:learned``; it can change nothing. Anything else an app does gets the answer an
    unknown route gets."""
    app = auth.app(h)
    if app is None:
        return True
    if app.policy.has_scope(bots_grant.LEARNED_SCOPE):
        if read:
            return True
        if h.command != "GET":
            routes._error(h, 403, "read_only", "This permission can only read learned items.")
            return False
    routes._error(h, 404, "not_found", "Not found")
    return False


def _refused(h, refused):
    send_json(h, 400, {"error": "That cannot be kept", "code": "refused", "reason": refused.kind})


def _guarded(gateway, h, run, read=False):
    def checked(principal):
        if not _person_only(h, read):
            return
        try:
            run(principal)
        except learned.Refused as refused:
            _refused(h, refused)

    routes._guarded(gateway, h, checked)


def _listing(gateway, principal, h, bot_id=None):
    params = query(h)
    try:
        paging = {key: int(params[key]) for key in ("limit", "offset") if key in params}
    except ValueError:
        raise store.Invalid("invalid cursor") from None
    bot_id = bot_id or params.get("bot")
    items = _learned(gateway, principal)
    page = items.list(
        principal.user_id,
        bot_id=bot_id,
        scope=params.get("scope"),
        state=params.get("state"),
        kind=params.get("kind"),
        **paging,
    )
    counts = items.counts(principal.user_id, bot_id)
    names = items.bot_names(principal.user_id)
    send_json(h, 200, {"items": [_view(i, names) for i in page], "counts": counts})


# /api/bots/<id>/...


def _bot_learning(gateway, h, principal, bot_id, rest):
    if rest:
        routes._error(h, 404, "not_found", "Not found")
        return
    items = _learned(gateway, principal)
    if h.command == "GET":
        send_json(h, 200, {"learning": items.state(bot_id, principal.user_id)})
        return
    body = routes._body(h, {"mode", "pause"})
    if body is None:
        return
    view = items.configure(
        bot_id,
        principal.user_id,
        mode=body.get("mode"),
        pause=body["pause"] if "pause" in body else False,
    )
    send_json(h, 200, {"learning": view})


def _bot_learned(gateway, h, principal, bot_id, rest):
    if h.command == "GET" and not rest:
        _listing(gateway, principal, h, bot_id)
    elif h.command == "POST" and rest == ["forget-all"]:
        body = routes._body(h, set())
        if body is None:
            return
        send_json(h, 200, _learned(gateway, principal).forget_all(bot_id, principal.user_id))
    else:
        routes._error(h, 404, "not_found", "Not found")


def _bot_handler(handler, read=False):
    def handle(gateway, h, principal, bot_id, rest):
        if _person_only(h, read):
            try:
                handler(gateway, h, principal, bot_id, rest)
            except learned.Refused as refused:
                _refused(h, refused)

    return handle


def _readable(gateway, principal):
    """``can_read(slug)`` for this person, from their projects right now."""
    slugs = {
        b["slug"]
        for b in BoardService(ToolContext(principal=principal, gateway=gateway)).board_list()
    }
    return slugs.__contains__


def _share(gateway, h, principal, item_id):
    """The Share button: a confirm chip for one item; nothing is shared until it is tapped."""
    if h.command != "POST":
        routes._error(h, 404, "not_found", "Not found")
        return
    body = routes._body(h, {"project"})
    if body is None:
        return
    project = body.get("project")
    if project is not None and (not isinstance(project, str) or not 1 <= len(project) <= 200):
        raise store.Invalid("invalid project")
    item = _learned(gateway, principal).get(principal.user_id, item_id)
    slug = project or item["board"]
    if not isinstance(slug, str):
        routes._error(h, 400, "project_required", tools_learning.SENTENCES["project"])
        return
    try:
        pending = tools_learning.build_pending(gateway, principal, item_id, slug)
    except ToolError as exc:
        routes._error(h, 409, "not_shareable", str(exc))
        return
    send_json(
        h,
        200,
        {
            "pending": pending["id"],
            "bot": pending["bot_id"],
            "label": _view_text(pending["label"]),
            "expires_at": pending["expires_at"],
        },
    )


def _view_text(text) -> str:
    return redact.redact(text)[0]


def _project_learned(gateway, h, principal, slug):
    """Shared items of one project, for members who can read it (the sharer's name included)."""
    if h.command != "GET":
        routes._error(h, 404, "not_found", "Not found")
        return
    can_read = _readable(gateway, principal)
    if not can_read(slug):
        routes._error(h, 404, "not_found", "Not found")
        return
    listed = _learned(gateway, principal).shared_list(
        slug, principal.user_id, can_read, **_paging(h)
    )
    for entry in listed:
        entry["by"] = _member_name(gateway, entry.pop("user_id"))
        entry["text"] = redact.redact(entry["text"])[0]
    send_json(h, 200, {"board": slug, "items": listed})


def _paging(h) -> dict:
    params = query(h)
    try:
        return {key: int(params[key]) for key in ("limit", "offset") if key in params}
    except ValueError:
        raise store.Invalid("invalid cursor") from None


def _member_name(gateway, user_id) -> str:
    try:
        return gateway.db.user(user_id)["email"]
    except Exception:
        return "a former member"


# /api/learned/<id>


def _review(gateway, h, principal, item_id, action):
    """Accept or reject a proposal (ask mode): the same two taps the thread shows."""
    if h.command != "POST":
        routes._error(h, 404, "not_found", "Not found")
        return
    if routes._body(h, set()) is None:
        return
    items = _learned(gateway, principal)
    try:
        if action == "accept":
            item = items.accept(principal.user_id, item_id)
            send_json(h, 200, {"item": _view(item, items.bot_names(principal.user_id))})
        else:
            items.reject(principal.user_id, item_id)
            send_json(h, 200, {"ok": True})
    except store.Conflict:
        routes._error(h, 409, "not_proposed", "That is not waiting for an answer.")
    except learned.Disabled:
        routes._error(h, 409, "learning_off", "Learning is switched off for this workspace.")


def _item(gateway, h, principal):
    parts = path_parts(h, ITEMS)
    if not parts or len(parts) > 2:
        routes._error(h, 404, "not_found", "Not found")
        return
    items = _learned(gateway, principal)
    if parts[1:] == ["share"]:
        _share(gateway, h, principal, parts[0])
        return
    if parts[1:] in (["accept"], ["reject"]):
        _review(gateway, h, principal, parts[0], parts[1])
        return
    if len(parts) != 1:
        routes._error(h, 404, "not_found", "Not found")
        return
    if h.command == "GET":
        item = items.get(principal.user_id, parts[0])
        send_json(h, 200, {"item": _view(item, items.bot_names(principal.user_id))})
    elif h.command == "PATCH":
        body = routes._body(h, {"text", "pinned"})
        if body is None:
            return
        item = items.update(principal.user_id, parts[0], **body)
        send_json(h, 200, {"item": _view(item, items.bot_names(principal.user_id))})
    elif h.command == "DELETE":
        try:
            items.forget(principal.user_id, parts[0])
        except store.Missing:
            # Not the person's own: a workspace owner may still delete a shared copy of a
            # project they read. Anyone else gets the same 404 as for an unknown id.
            if principal.role != "owner":
                raise
            items.delete_shared(
                parts[0], user_id=principal.user_id, owner_can_read=_readable(gateway, principal)
            )
        send_json(h, 200, {"ok": True})
    else:
        routes._error(h, 404, "not_found", "Not found")


# Owner switches


def _owner_only(h, principal) -> bool:
    if principal.role == "owner":
        return True
    routes._error(h, 403, "forbidden", "Only the workspace owner can change this")
    return False


def _audit(gateway, principal, decision):
    gateway.db.audit(
        action="bot:learn_setting",
        decision=decision,
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        connection_id=principal.connection_id,
        auth=principal.auth,
        tool="learn_setting",
    )


def _settings(gateway, h, principal):
    items = _learned(gateway, principal)
    if h.command == "PATCH":
        body = routes._body(h, {"enabled", "shared_enabled"})
        if body is None:
            return
        if not _owner_only(h, principal):
            _audit(gateway, principal, "refused")
            return
        items.set_policy(learned.WORKSPACE, principal.user_id, **body)
        _audit(gateway, principal, "ok")
    policy = items.policy()
    send_json(
        h,
        200,
        {
            "enabled": policy["enabled"],
            "shared_enabled": policy["shared_enabled"],
            "can_configure": principal.role == "owner",
        },
    )


def project_route(gateway, h, slug, rest=("learning",)):
    """``/api/projects/<slug>/learning`` and ``.../learned``; called by the projects router."""

    def run(principal):
        if tuple(rest) == ("learned",):
            _project_learned(gateway, h, principal, slug)
            return
        service = BoardService(ToolContext(principal=principal, gateway=gateway))
        if slug not in {b["slug"] for b in service.board_list()}:
            routes._error(h, 404, "not_found", "Not found")
            return
        items = _learned(gateway, principal)
        key = learned.board_key(slug)
        if h.command == "PATCH":
            body = routes._body(h, {"shared_enabled"})
            if body is None:
                return
            if not _owner_only(h, principal):
                _audit(gateway, principal, "refused")
                return
            items.set_policy(key, principal.user_id, **body)
            _audit(gateway, principal, "ok")
        policy = items.policy(key)
        send_json(
            h,
            200,
            {
                "board": slug,
                "shared_enabled": policy["shared_enabled"],
                "can_configure": principal.role == "owner",
            },
        )

    _guarded(gateway, h, run)


# (method, first segment after the bot id) -> handler, merged into the bots router
BOT_HANDLERS = {
    ("GET", "learning"): _bot_handler(_bot_learning),
    ("PATCH", "learning"): _bot_handler(_bot_learning),
    ("GET", "learned"): _bot_handler(_bot_learned, read=True),
    ("POST", "learned"): _bot_handler(_bot_learned),
}


def install(gateway) -> None:
    routes.HANDLERS.update(BOT_HANDLERS)
    gateway.add_route(
        "GET",
        "/api/learned",
        lambda h: _guarded(gateway, h, lambda p: _listing(gateway, p, h), read=True),
    )
    for method in ("GET", "PATCH"):
        gateway.add_route(
            method,
            "/api/learning/settings",
            lambda h: _guarded(gateway, h, lambda p: _settings(gateway, h, p)),
        )
    for method in ("GET", "PATCH", "DELETE", "POST"):
        gateway.add_prefix_route(
            method, ITEMS, lambda h: _guarded(gateway, h, lambda p: _item(gateway, h, p))
        )
