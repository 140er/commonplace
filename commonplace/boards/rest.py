"""Contract REST routes; all policy and state changes run in BoardService."""

from commonplace.extensions import ToolContext, ToolError

from ._http import HTTPError, path_parts, query, read_json, require_principal, send_json
from .display import with_urls
from .service import BoardService, VersionClash


def install(gateway):
    def route(h):
        principal = require_principal(gateway, h)
        if principal is None:
            return
        service = BoardService(ToolContext(principal=principal, gateway=gateway))
        parts = path_parts(h, "/api/")
        method = h.command
        try:
            status, result = dispatch(service, h, parts, method)
        except VersionClash as exc:
            send_json(
                h, 409, {"error": "card changed", "current": with_urls(service.ctx, exc.current)}
            )
            return
        except HTTPError as exc:
            send_json(h, exc.status, {"error": str(exc)})
            return
        except ToolError as exc:
            message = str(exc).removeprefix("no: ")
            send_json(
                h,
                404 if message in ("card not found", "board not found") else 403,
                {"error": message},
            )
            return
        except TypeError:
            send_json(h, 400, {"error": "invalid arguments"})
            return
        send_json(h, status, with_urls(service.ctx, result))

    for method in ("GET", "POST", "PATCH", "DELETE"):
        for prefix in ("/api/boards/", "/api/cards/", "/api/needs-me/"):
            gateway.add_prefix_route(method, prefix, route)


def dispatch(service, h, parts, method):
    if len(parts) in (2, 3) and parts[0] == "boards":
        tool = None
        if len(parts) == 2 and method == "PATCH":
            tool = "board_set"
        elif len(parts) == 3 and parts[2] == "chat":
            tool = {"GET": "space_read", "POST": "space_post"}.get(method)
        if tool:
            # These new aliases enforce MCP visibility here, keeping gateway/handoff untouched.
            p = service.principal
            if not p.person and not p.policy.tool_visible(tool):
                raise HTTPError(403, "tool not allowed for this connection")
            if tool == "space_read":
                try:
                    cursors = {
                        key: int(value)
                        for key, value in query(h).items()
                        if key in ("after", "last", "before")
                    }
                except ValueError:
                    raise HTTPError(400, "invalid cursor") from None
                return 200, service.space_read(parts[1], **cursors)
            body = read_json(h)
            body.pop("board", None)
            if tool == "board_set":
                return 200, service.board_set(parts[1], **body)
            return 201, service.space_post(parts[1], **body)
    if parts == ["boards"]:
        if method == "GET":
            return 200, {"boards": service.board_list()}
        if method == "POST":
            return 201, service.board_add(**read_json(h))
    if len(parts) == 3 and parts[0] == "boards" and parts[2] == "cards":
        if method == "GET":
            return 200, {
                "cards": service.task_list(
                    board=parts[1], status=query(h).get("status") or None, all_statuses=True
                )
            }
        if method == "POST":
            body = read_json(h)
            body.pop("board", None)
            return 201, service.task_add(board=parts[1], **body)
    if parts == ["needs-me"] and method == "GET":
        return 200, service.needs_me()
    if len(parts) in (2, 3) and parts[0] == "cards":
        task = parts[1]
        if len(parts) == 2:
            if method == "GET":
                return 200, service.task_show(task)
            if method == "PATCH":
                body = read_json(h)
                if "version" not in body or body["version"] is None:
                    raise HTTPError(400, "version is required")
                body.pop("task", None)
                return 200, service.task_update(task, **body)
        if len(parts) == 3 and method == "POST":
            handlers = {
                "comments": service.task_comment,
                "handoff": service.task_handoff,
                "decide": service.decide,
            }
            if parts[2] in handlers:
                body = read_json(h)
                # A decision answers the ask the person saw; a newer ask is a 409.
                if parts[2] == "decide" and body.get("version") is None:
                    raise HTTPError(400, "version is required")
                body.pop("task", None)
                return 200, handlers[parts[2]](task, **body)
    raise HTTPError(404, "not found")
