"""REST aliases for requester tools, with the ordinary rules freshness gate."""

from commonplace.boards._http import (
    HTTPError,
    path_parts,
    query,
    read_json,
    require_principal,
    send_json,
)
from commonplace.boards.service import BoardService
from commonplace.boards.store import require_visible, task_row
from commonplace.extensions import ToolContext, ToolError
from commonplace.rules.enforce import RulesRefresh, rest_write

from . import jobs
from .start_options import start_options
from .tools import dispatch

NOT_FOUND = {"machine not found", "start not found", "card not found"}
CONFLICTS = {
    "busy",
    "card already has an active start",
    "card was started recently",
    "request_key reused with different arguments",
    "start outcome is uncertain; a workspace owner must resolve it",
    "start is already finished",
    "start is not uncertain",
}


def _launch_output(ctx, job):
    """People may inspect diagnostics only while the job's card remains readable."""
    principal = ctx.principal
    if not principal.person or getattr(principal, "runner", None) is not None:
        raise HTTPError(404, "not found")
    store = jobs.open_store(ctx.gateway, principal.workspace_id)
    with store.connection() as conn:
        row = conn.execute("SELECT * FROM runner_jobs WHERE id=?", (job,)).fetchone()
    if row is None or not row["launch_output"]:
        raise HTTPError(404, "not found")
    service = BoardService(ctx)
    try:
        with service.store.connection() as conn:
            card = task_row(conn, row["task_number"])
            require_visible(card, principal)
            service._board(conn, card["board"])
    except ToolError:
        raise HTTPError(404, "not found") from None
    return {"job": row["id"], "reason": jobs.launch_reason(row)}


def register(gateway):
    def route(handler):
        principal = require_principal(gateway, handler)
        if principal is None:
            return
        ctx = ToolContext(principal, gateway)
        parts = path_parts(handler, "/api/")
        try:
            if (
                len(parts) == 3
                and parts[0] == "card-starts"
                and parts[2] == "launch-output"
                and handler.command == "GET"
            ):
                send_json(handler, 200, _launch_output(ctx, parts[1]))
                return
            if parts == ["card-starts"] and handler.command == "GET":
                result = dispatch(ctx, "card_start_status", query(handler))
                send_json(handler, 200, result)
                return
            if parts == ["card-starts", "options"] and handler.command == "GET":
                task = query(handler).get("task")
                if not task:
                    raise HTTPError(400, "task is required")
                send_json(handler, 200, start_options(ctx, task))
                return
            if parts == ["card-starts"] and handler.command == "POST":
                name = "card_start"
                path_args = {}
            elif (
                len(parts) == 3
                and parts[0] == "card-starts"
                and parts[2] in ("cancel", "resolve")
                and handler.command == "POST"
            ):
                name = "card_start_" + parts[2]
                path_args = {"job": parts[1]}
            else:
                raise HTTPError(404, "not found")
            body = read_json(handler)
            with rest_write(ctx, handler, name, body):
                if path_args and "job" in body:
                    raise ToolError("no: unexpected arguments")
                result = dispatch(ctx, name, {**body, **path_args})
            status, payload = result if name == "card_start" else (200, result)
            send_json(handler, status, payload)
        except RulesRefresh as exc:
            send_json(handler, 428, exc.payload)
        except HTTPError as exc:
            send_json(handler, exc.status, {"error": str(exc)})
        except ToolError as exc:
            message = str(exc).removeprefix("no: ")
            status = getattr(exc, "status", None)
            if not isinstance(status, int) or isinstance(status, bool):
                # Orchestrator refusals carry their own status; others map by text.
                status = 404 if message in NOT_FOUND else 409 if message in CONFLICTS else 403
            # A refused config-driven start also carries its stable code for surfaces.
            code = getattr(exc, "code", None)
            send_json(handler, status, {"error": str(exc), **({"code": code} if code else {})})

    gateway.add_route("POST", "/api/card-starts", route)
    gateway.add_route("GET", "/api/card-starts", route)
    gateway.add_route("GET", "/api/card-starts/options", route)
    gateway.add_prefix_route("POST", "/api/card-starts/", route)
    gateway.add_prefix_route("GET", "/api/card-starts/", route)
