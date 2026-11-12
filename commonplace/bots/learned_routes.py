"""Routes of the learning memory: a person's items and per-bot setting, the owner's switches.

    GET    /api/bots/<id>/learning               {learning}                 (mode, pause, counts)
    PATCH  /api/bots/<id>/learning               {mode?, pause?} -> {learning}
    GET    /api/bots/<id>/learned                {items, counts}
    POST   /api/bots/<id>/learned/forget-all     {} -> {forgotten, notes, floor}
    GET    /api/learned                          ?bot=&scope=&state=&kind=&limit=&offset= -> {items, counts}
    GET    /api/learned/<id>                     {item}
    PATCH  /api/learned/<id>                     {text?, pinned?} -> {item}
    DELETE /api/learned/<id>                     {ok}
    GET    /api/learning/settings                {enabled, shared_enabled, can_configure}
    PATCH  /api/learning/settings                {enabled?, shared_enabled?}      (workspace owner)
    GET    /api/projects/<slug>/learning         {board, shared_enabled, can_configure}
    PATCH  /api/projects/<slug>/learning         {shared_enabled}                 (workspace owner)

Only a signed-in person reaches these: an app token (even one holding ``bots:chat``), an agent
token or a machine credential is answered 404, and a cross-origin write is refused by the
gateway. An item or bot that is not the caller's is 404. Everything off is the default.
"""

from commonplace import redact
from commonplace.boards.service import BoardService
from commonplace.bots import auth, learned, routes, store
from commonplace.extensions import ToolContext
from commonplace.http_util import path_parts, query, send_json

ITEMS = "/api/learned/"


def _learned(gateway, principal) -> learned.Learned:
    return learned.open_learned(gateway, principal.workspace_id)


def _view(item) -> dict:
    return {
        **item,
        "text": redact.redact(item["text"])[0],
        "note": None if item["note"] is None else redact.redact(item["note"])[0],
    }


def _person_only(h) -> bool:
    """Learned items are the person's: an app acting for them (before the read scope of a later
    slice) is not enough, so it gets the answer an unknown route gets."""
    if auth.app(h) is None:
        return True
    routes._error(h, 404, "not_found", "Not found")
    return False


def _refused(h, refused):
    send_json(h, 400, {"error": "That cannot be kept", "code": "refused", "reason": refused.kind})


def _guarded(gateway, h, run):
    def checked(principal):
        if not _person_only(h):
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
    send_json(h, 200, {"items": [_view(i) for i in page], "counts": counts})


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


def _bot_handler(handler):
    def handle(gateway, h, principal, bot_id, rest):
        if _person_only(h):
            try:
                handler(gateway, h, principal, bot_id, rest)
            except learned.Refused as refused:
                _refused(h, refused)

    return handle


# /api/learned/<id>


def _item(gateway, h, principal):
    parts = path_parts(h, ITEMS)
    if len(parts) != 1:
        routes._error(h, 404, "not_found", "Not found")
        return
    items = _learned(gateway, principal)
    if h.command == "GET":
        send_json(h, 200, {"item": _view(items.get(principal.user_id, parts[0]))})
    elif h.command == "PATCH":
        body = routes._body(h, {"text", "pinned"})
        if body is None:
            return
        send_json(h, 200, {"item": _view(items.update(principal.user_id, parts[0], **body))})
    elif h.command == "DELETE":
        items.forget(principal.user_id, parts[0])
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


def project_route(gateway, h, slug):
    """``/api/projects/<slug>/learning``; called by the projects router."""

    def run(principal):
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
    ("GET", "learned"): _bot_handler(_bot_learned),
    ("POST", "learned"): _bot_handler(_bot_learned),
}


def install(gateway) -> None:
    routes.HANDLERS.update(BOT_HANDLERS)
    gateway.add_route(
        "GET", "/api/learned", lambda h: _guarded(gateway, h, lambda p: _listing(gateway, p, h))
    )
    for method in ("GET", "PATCH"):
        gateway.add_route(
            method,
            "/api/learning/settings",
            lambda h: _guarded(gateway, h, lambda p: _settings(gateway, h, p)),
        )
    for method in ("GET", "PATCH", "DELETE"):
        gateway.add_prefix_route(
            method, ITEMS, lambda h: _guarded(gateway, h, lambda p: _item(gateway, h, p))
        )
