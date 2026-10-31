"""Person-only rule review routes; agent publication goes through MCP."""

from commonplace.boards._http import HTTPError, path_parts, read_json, require_principal, send_json
from commonplace.extensions import ToolContext, ToolError

from .service import RuleService, VersionClash


def install(gateway):
    def route(h):
        principal = require_principal(gateway, h)
        if principal is None:
            return
        try:
            if not principal.person or (h.command != "GET" and principal.role != "owner"):
                raise HTTPError(403, "people only" if h.command == "GET" else "owner only")
            service = RuleService(ToolContext(principal=principal, gateway=gateway))
            status, result = dispatch(service, h, path_parts(h, "/api/"), h.command)
        except VersionClash as exc:
            send_json(h, 409, {"error": "rule changed", "current": exc.current})
            return
        except HTTPError as exc:
            send_json(h, exc.status, {"error": str(exc)})
            return
        except ToolError as exc:
            message = str(exc).removeprefix("no: ")
            send_json(
                h,
                404 if message in ("rule not found", "suggestion not found") else 400,
                {"error": message},
            )
            return
        except TypeError:
            send_json(h, 400, {"error": "invalid arguments"})
            return
        send_json(h, status, result)

    for method in ("GET", "POST", "PATCH"):
        gateway.add_prefix_route(method, "/api/rules/", route)


def dispatch(service, h, parts, method):
    if parts == ["rules"]:
        if method == "GET":
            return 200, service.list_rules()
        if method == "POST":
            return 201, service.create(**read_json(h))
    if len(parts) == 3 and parts[:2] == ["rules", "suggestions"] and method == "POST":
        return 200, service.decide_suggestion(parts[2], **read_json(h))
    if len(parts) in (2, 3) and parts[0] == "rules":
        rule_id = parts[1]
        if len(parts) == 3 and parts[2] == "history" and method == "GET":
            return 200, service.history(rule_id)
        if len(parts) == 2 and method == "PATCH":
            body = read_json(h)
            if body.get("version") is None:
                raise HTTPError(400, "version is required")
            return 200, service.edit(rule_id, **body)
        if len(parts) == 3 and method == "POST":
            if parts[2] == "action":
                body = read_json(h)
                if body.get("version") is None:
                    raise HTTPError(400, "version is required")
                return 200, service.action(rule_id, **body)
            if parts[2] == "suggestions":
                return 201, service.suggest(rule_id, **read_json(h))
    raise HTTPError(404, "not found")
