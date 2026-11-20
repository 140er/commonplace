"""Model mode routes and the ``model_mode`` tool (``docs/system/model-modes.md``, card #617).

- ``GET|PUT|DELETE /api/settings/model-mode``: the workspace's or one project's mode record, for the
  signed-in owner only. Anyone else (a member, an agent token, a machine credential) gets the answer
  an unknown route gets (404), so the setting's editability is not shown. Every change is audited.
- ``GET|POST|DELETE /api/me/model-connections`` and ``PUT /api/me/model-use-own``: a person's own
  accounts (a provider, a label and one of their machines, never a credential) and their switch.
  Only that person reads or changes them; a foreign or missing id answers the same 404.
- ``GET /api/me/model-mode`` and the read-only ``model_mode`` tool: the mode, where it comes from and
  the account the caller's next run would use. There is no tool that changes a mode.
"""

import logging
import sqlite3

from commonplace.boards.service import BoardService
from commonplace.extensions import WORK_PROFILES, LocalTool, ToolContext, ToolError, register
from commonplace.http_util import log_failure, query, read_json, require_principal, send_json
from commonplace.projects import model_modes, models

logger = logging.getLogger(__name__)
_registered = False
TOOL_ARGUMENTS = {"board"}


class NotFound(ToolError):
    """A hidden or absent object (REST answers 404)."""


def available(principal, gateway):
    return gateway is not None and getattr(principal, "runner", None) is None


def _board(ctx, slug):
    """The board's row when the caller may read it; a hidden board is a plain not-found."""
    if not isinstance(slug, str):
        raise NotFound("no: not found")
    with BoardService(ctx).store.connection() as conn:
        row = conn.execute("SELECT slug, wing FROM boards WHERE slug=?", (slug,)).fetchone()
    if row is None or not ctx.principal.policy.can_read_wing(row["wing"]):
        raise NotFound("no: not found")
    return row


def _guard(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except model_modes.ModeDenied:
        raise NotFound("no: not found") from None
    except model_modes.InvalidMode as exc:
        raise ToolError("no: " + str(exc)) from None


def mode_view(ctx, args):
    """The mode as the caller sees it: the workspace's, and for one project the role-by-role account
    this caller's next run would use. Other people's connections never appear."""
    if not isinstance(args, dict) or args.keys() - TOOL_ARGUMENTS:
        raise ToolError("no: invalid arguments")
    principal, db, ws = ctx.principal, ctx.db, ctx.principal.workspace_id
    person = principal.user_id if principal.person else None
    result = {"workspace": model_modes.effective_mode(db, ws, None)}
    if args.get("board") is None:
        readable = {}
        for slug in model_modes.records(db, ws)["projects"]:
            try:
                _board(ctx, slug)
            except NotFound:
                continue
            readable[slug] = model_modes.effective_mode(db, ws, slug)
        result["projects"] = readable
        return result
    board = _board(ctx, args["board"])["slug"]
    roles = {
        role: effective.choice.provider if effective.choice else None
        for role, effective in models.effective_roles(db, ws, board).items()
    }
    result["project"] = {
        "board": board,
        **model_modes.effective_mode(db, ws, board),
        "roles": model_modes.effective_for(db, ws, board, person, roles),
    }
    return result


def settings(ctx, method, body, args):
    """Owner only: read the records, set one or clear one (a ``board`` key picks a project)."""
    _guard(model_modes.require_owner, ctx.principal)
    slug = (
        (body if isinstance(body, dict) else {}).get("board")
        if method == "PUT"
        else args.get("board")
    )
    if slug is not None:
        slug = _board(ctx, slug)["slug"]
    if method == "GET":
        return model_modes.records(ctx.db, ctx.principal.workspace_id)
    if method == "PUT":
        if not isinstance(body, dict):
            raise ToolError("no: invalid mode")
        fields = {key: value for key, value in body.items() if key != "board"}
        return _guard(model_modes.put, ctx.db, ctx.principal, fields, slug)
    return _guard(model_modes.clear, ctx.db, ctx.principal, slug)


def connections(ctx, method, body, args):
    """A person's own accounts: list, add, revoke, and the use-my-own switch."""
    if method == "GET":
        return _guard(model_modes.list_connections, ctx.db, ctx.principal)
    if method == "POST":
        return _guard(model_modes.add_connection, ctx.db, ctx.principal, body)
    if method == "PUT":
        return _guard(model_modes.set_use_own, ctx.db, ctx.principal, body)
    _guard(model_modes.revoke_connection, ctx.db, ctx.principal, args.get("id"))
    return {"removed": args.get("id")}


def register_tools():
    global _registered
    if _registered:
        return
    register(
        LocalTool(
            name="model_mode",
            description=(
                "Read how models are provided: the workspace's mode (org, personal, hybrid or "
                "unset: no choice made yet) and, for one project, which account each role's next "
                "run would use for you and why. Never shows another person's accounts or any "
                "credential. It only reads: the owner changes a mode in settings."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "board": {
                        "type": "string",
                        "description": "A project's board slug, for the account per role.",
                    }
                },
                "additionalProperties": False,
            },
            handler=mode_view,
            scope="memory:read",
            profiles=WORK_PROFILES,
            available=available,
        )
    )
    _registered = True


def install(gateway):
    def serve(call, methods=None):
        def handle(h):
            principal = require_principal(gateway, h)
            if principal is None:
                return
            try:
                ctx = ToolContext(principal=principal, gateway=gateway)
                body = None
                method = h.command
                if method in ("PUT", "POST"):
                    try:
                        body = read_json(h)
                    except ValueError:
                        return
                    if isinstance(body, dict):
                        body.pop("rules_seq", None)
                payload = call(ctx, method, body, query(h) if method != "PUT" else {})
                send_json(h, 201 if method == "POST" else 200, payload)
            except NotFound:
                send_json(h, 404, {"error": "not found"})
            except model_modes.ModeUnavailable:
                send_json(h, 503, {"error": "model modes are not available right now; retry"})
            except ToolError as exc:
                send_json(h, 400, {"error": str(exc).removeprefix("no: ")})
            except sqlite3.OperationalError:
                send_json(h, 409, {"error": "busy"})
            except Exception as exc:
                log_failure(logger, "model mode request failed", exc)
                send_json(h, 500, {"error": "model mode request failed"})

        return handle

    def read(ctx, method, body, args):
        return mode_view(ctx, args)

    for method in ("GET", "PUT", "DELETE"):
        gateway.add_route(method, "/api/settings/model-mode", serve(settings))
    for method in ("GET", "POST", "DELETE"):
        gateway.add_route(method, "/api/me/model-connections", serve(connections))
    gateway.add_route("PUT", "/api/me/model-use-own", serve(connections))
    gateway.add_route("GET", "/api/me/model-mode", serve(read))
