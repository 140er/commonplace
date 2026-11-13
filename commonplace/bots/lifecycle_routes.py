"""Workspace-owner routes for shared bots (design section 7 and 9): the list, disable, enable, adopt.

    GET  /api/bots/shared             {bots} every shared bot: owner, members, access; never a thread
    POST /api/bots/<id>/disable       {ok, frozen}    freeze a shared bot for everyone (reversible)
    POST /api/bots/<id>/enable        {ok, frozen}    lift a disable (an owner-left bot needs adopt)
    POST /api/bots/<id>/adopt         {ok, owner}     take over a frozen, ownerless shared bot

Workspace owners in a web session only; a member, an agent token and an app get 404 (the same answer
as for an unknown id). Every real change writes one ids-only audit row (``bot_lifecycle:set``).
"""

from commonplace.bots import access, auth, lifecycle, store
from commonplace.http_util import send_json


def _owner_only(h, principal) -> bool:
    """True for a workspace owner's own web session; anyone else is a plain 404."""
    if auth.app(h) is None and principal.person and principal.role == "owner":
        return True
    raise store.Missing()


def _audit(gateway, principal, bot_id, action) -> None:
    gateway.db.audit(
        action="bot_lifecycle:set",
        decision="ok",
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        reason=f"bot={bot_id} {action}",
        target_ids=[bot_id],
    )


def _who(gateway, user_id) -> dict:
    return {"id": user_id, "name": access.display_name(gateway, user_id)}


def shared_list(gateway, h, principal) -> None:
    _owner_only(h, principal)
    lifecycle.sweep(gateway, principal.workspace_id)
    bots = store.open_bots(gateway, principal.workspace_id).shared_bots()
    send_json(
        h,
        200,
        {
            "bots": [
                {
                    **{key: bot[key] for key in ("id", "name", "job", "archived", "frozen")},
                    "frozen_reason": bot["frozen_reason"],
                    "owner": _who(gateway, bot["owner_id"]),
                    "members": [
                        {
                            **{k: m[k] for k in ("talk", "see_shared")},
                            "user": _who(gateway, m["user_id"]),
                        }
                        for m in bot["members"]
                    ],
                    "access": bot["access"],
                }
                for bot in bots
            ]
        },
    )


def _bots(gateway, principal, h, rest):
    if rest:
        raise store.Missing()
    _owner_only(h, principal)
    lifecycle.sweep(gateway, principal.workspace_id)
    return store.open_bots(gateway, principal.workspace_id)


def disable(gateway, h, principal, bot_id, rest) -> None:
    bots = _bots(gateway, principal, h, rest)
    if bots.disable(bot_id, principal.user_id):
        _audit(gateway, principal, bot_id, "disable")
    send_json(h, 200, {"ok": True, "frozen": True})


def enable(gateway, h, principal, bot_id, rest) -> None:
    bots = _bots(gateway, principal, h, rest)
    if bots.enable(bot_id, principal.user_id):
        _audit(gateway, principal, bot_id, "enable")
    send_json(h, 200, {"ok": True, "frozen": False})


def adopt(gateway, h, principal, bot_id, rest) -> None:
    bots = _bots(gateway, principal, h, rest)
    bots.adopt(bot_id, principal.user_id)
    _audit(gateway, principal, bot_id, "adopt")
    send_json(h, 200, {"ok": True, "owner": _who(gateway, principal.user_id)})
