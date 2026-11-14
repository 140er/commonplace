"""Cost sums: the ``costs_summary`` tool, ``GET /api/costs`` and the window setting routes.

Reads are scoped like every other read: a person owner sees every run of the workspace;
anyone else sees the runs of boards whose wing their token can read, and the sums cover
only those runs. A machine credential reads nothing here. The window is the workspace's
(default: the UTC calendar month) and only a signed-in owner changes it.
"""

import logging
import sqlite3

from commonplace.boards.service import BoardService
from commonplace.extensions import WORK_PROFILES, LocalTool, ToolContext, ToolError, register
from commonplace.http_util import log_failure, query, read_json, require_principal, send_json

from . import costs, jobs

logger = logging.getLogger(__name__)
ARGUMENTS = {"since", "until"}
_registered = False


class Refused(ToolError):
    """The caller may not do this (REST answers 403)."""


def _person_owner(principal):
    return (
        principal.person
        and principal.role == "owner"
        and getattr(principal, "runner", None) is None
    )


def available(principal, gateway):
    return gateway is not None and getattr(principal, "runner", None) is None


def _boards(ctx):
    """The board slugs the caller may read; None for a person owner, who reads all."""
    principal = ctx.principal
    if getattr(principal, "runner", None) is not None:
        raise Refused("no: not available to a machine")
    if _person_owner(principal):
        return None
    with BoardService(ctx).store.connection() as conn:
        rows = conn.execute("SELECT slug, wing FROM boards").fetchall()
    return {row["slug"] for row in rows if principal.policy.can_read_wing(row["wing"])}


def costs_summary(ctx, args):
    if not isinstance(args, dict) or args.keys() - ARGUMENTS:
        raise ToolError("no: invalid arguments")
    boards = _boards(ctx)
    store = jobs.open_store(ctx.gateway, ctx.principal.workspace_id)
    with store.connection() as conn:
        setting = costs.get_setting(conn)
        try:
            start, end = costs.window(setting, jobs._now(), args.get("since"), args.get("until"))
        except ValueError as exc:
            raise ToolError("no: " + str(exc)) from None
        result = costs.summarize(conn, start, end, boards=boards)
    result["window"].update(setting, custom="since" in args or "until" in args)
    result["scope"] = "workspace" if boards is None else "readable"
    return result


def get_settings(ctx):
    if getattr(ctx.principal, "runner", None) is not None:
        raise Refused("no: not available to a machine")
    store = jobs.open_store(ctx.gateway, ctx.principal.workspace_id)
    with store.connection() as conn:
        return costs.get_setting(conn)


def set_settings(ctx, body):
    if not _person_owner(ctx.principal):
        raise Refused("no: a signed-in workspace owner is required")
    try:
        setting = costs.clean_setting(body)
    except ValueError as exc:
        raise ToolError("no: " + str(exc)) from None
    store = jobs.open_store(ctx.gateway, ctx.principal.workspace_id)
    now = jobs._now()
    with store.transaction() as conn, ctx.db._lock, ctx.db._tx():
        ctx.db.audit(
            action="costs.window",
            decision="allow",
            workspace_id=ctx.principal.workspace_id,
            user_id=ctx.principal.user_id,
            connection_id=ctx.principal.connection_id,
            auth=ctx.principal.auth,
        )
        costs.set_setting(conn, setting, "user:" + ctx.principal.user_id, now)
    return setting


def register_tools():
    global _registered
    if _registered:
        return
    register(
        LocalTool(
            name="costs_summary",
            description=(
                "Read what the workspace's agent runs cost over a window (default: the UTC "
                "calendar month the owner set): sums for the workspace, per agent connection, "
                "project, board and card. Cost is only what machines reported, per currency; "
                "a run with no known cost is counted as unknown and listed, never as zero. "
                "You see the runs of boards you can read."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "since": {
                        "type": "string",
                        "description": "Start of the window: YYYY-MM-DD or a UTC timestamp.",
                    },
                    "until": {
                        "type": "string",
                        "description": "End of the window, exclusive; same format.",
                    },
                },
                "additionalProperties": False,
            },
            handler=costs_summary,
            scope="memory:read",
            profiles=WORK_PROFILES,
            available=available,
        )
    )
    _registered = True


def install(gateway):
    def serve(action):
        def handle(h):
            principal = require_principal(gateway, h)
            if principal is None:
                return
            try:
                ctx = ToolContext(principal=principal, gateway=gateway)
                if action == "put":
                    try:
                        body = read_json(h)
                    except ValueError:
                        return
                    body.pop("rules_seq", None)
                    payload = set_settings(ctx, body)
                elif action == "get":
                    payload = get_settings(ctx)
                else:
                    payload = costs_summary(ctx, query(h))
                send_json(h, 200, payload)
            except Refused as exc:
                send_json(h, 403, {"error": str(exc).removeprefix("no: ")})
            except ToolError as exc:
                send_json(h, 400, {"error": str(exc).removeprefix("no: ")})
            except sqlite3.OperationalError:
                send_json(h, 409, {"error": "busy"})
            except Exception as exc:
                log_failure(logger, "cost request failed", exc)
                send_json(h, 500, {"error": "cost request failed"})

        return handle

    gateway.add_route("GET", "/api/costs", serve("sums"))
    gateway.add_route("GET", "/api/settings/costs", serve("get"))
    gateway.add_route("PUT", "/api/settings/costs", serve("put"))
