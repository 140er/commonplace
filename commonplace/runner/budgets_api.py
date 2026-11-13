"""Budget routes: ``GET|PUT|DELETE /api/settings/budgets``, for the signed-in owner only.

There is no tool for them. Anyone else, an agent token, a machine credential or a member,
gets the answer an unknown route gets (404), so the existence of a budget is not shown to
them. Every change is audited. The routes only set limits the hub itself enforces on new
starts (``budgets``); nothing is bought or paid.
"""

import logging
import sqlite3

from commonplace.control_db import ControlError
from commonplace.extensions import ToolContext, ToolError
from commonplace.http_util import log_failure, query, read_json, require_principal, send_json

from . import budgets, costs, jobs
from .costs_api import _person_owner

logger = logging.getLogger(__name__)


class NotFound(ToolError):
    """The caller is not a signed-in workspace owner (REST answers 404)."""


def _owner(ctx):
    if not _person_owner(ctx.principal):
        raise NotFound("no: not found")


def _store(ctx):
    return jobs.open_store(ctx.gateway, ctx.principal.workspace_id)


def list_budgets(ctx):
    _owner(ctx)
    with _store(ctx).connection() as conn:
        setting = costs.get_setting(conn)
        return {"window": setting, "budgets": budgets.status(conn, jobs._now())}


def _audit(ctx, action, *targets):
    ctx.db.audit(
        action=action,
        decision="allow",
        workspace_id=ctx.principal.workspace_id,
        user_id=ctx.principal.user_id,
        connection_id=ctx.principal.connection_id,
        auth=ctx.principal.auth,
        target_ids=targets,
    )


def set_budget(ctx, body):
    _owner(ctx)
    try:
        fields = budgets.clean_budget(body)
        if fields["kind"] == "agent":
            try:
                ctx.db.connection(fields["key"], ctx.principal.workspace_id)
            except ControlError:
                raise ValueError("unknown agent connection") from None
        store = _store(ctx)
        now = jobs._now()
        with store.transaction() as conn, ctx.db._lock, ctx.db._tx():
            entry = budgets.set_budget(conn, fields, "user:" + ctx.principal.user_id, now)
            _audit(ctx, "costs.budget", entry["id"])
    except ValueError as exc:
        raise ToolError("no: " + str(exc)) from None
    return entry


def clear_budget(ctx, args):
    _owner(ctx)
    try:
        kind, key = budgets.clean_scope(args.get("kind"), args.get("key"))
    except ValueError as exc:
        raise ToolError("no: " + str(exc)) from None
    with _store(ctx).transaction() as conn, ctx.db._lock, ctx.db._tx():
        removed = budgets.clear_budget(conn, kind, key)
        if removed is None:
            raise NotFound("no: not found")
        _audit(ctx, "costs.budget_clear", removed)
    return {"removed": removed}


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
                    if not isinstance(body, dict):
                        raise ToolError("no: invalid budget")
                    body.pop("rules_seq", None)
                    payload = set_budget(ctx, body)
                elif action == "delete":
                    payload = clear_budget(ctx, query(h))
                else:
                    payload = list_budgets(ctx)
                send_json(h, 200, payload)
            except NotFound:
                send_json(h, 404, {"error": "not found"})
            except ToolError as exc:
                send_json(h, 400, {"error": str(exc).removeprefix("no: ")})
            except sqlite3.OperationalError:
                send_json(h, 409, {"error": "busy"})
            except Exception as exc:
                log_failure(logger, "budget request failed", exc)
                send_json(h, 500, {"error": "budget request failed"})

        return handle

    gateway.add_route("GET", "/api/settings/budgets", serve("get"))
    gateway.add_route("PUT", "/api/settings/budgets", serve("put"))
    gateway.add_route("DELETE", "/api/settings/budgets", serve("delete"))
