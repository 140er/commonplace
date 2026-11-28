"""``/api/card-snooze``: snooze or wake one card from the app.

    GET  /api/card-snooze/<n>           {"snoozed": {until, timezone, after_card, text, ...} | null}
    POST /api/card-snooze/<n>           snooze (200, the card); body: {"until": "2026-11-12T09:00",
                                        "timezone": "Europe/Amsterdam"} or {"after_card": 12}
    POST /api/card-snooze/<n>/cancel    unsnooze (200, the card)

Workspace owners, assignees and the current board coordinator share the same rules as
``task_snooze`` / ``task_unsnooze``. Coordinators must supply a reason to park a card.
"""

from __future__ import annotations

from commonplace import http_util
from commonplace.boards.service import BoardService
from commonplace.boards.store import CardRefused
from commonplace.extensions import ToolContext, ToolError
from commonplace.rules.enforce import RulesRefresh, rest_write

PREFIX = "/api/card-snooze"
_KEYS = {"until", "timezone", "after_card", "reason"}


def install(gateway) -> None:
    def route(h):
        principal = http_util.require_principal(gateway, h)
        if principal is None:
            return

        def send(status, body):
            http_util.send_json(h, status, body)

        parts = http_util.path_parts(h, PREFIX + "/")
        from commonplace.gateway import _rest_tool_refusal

        tool = None
        if len(parts) == 1:
            tool = {"GET": "task_show", "POST": "task_snooze"}.get(h.command)
        elif len(parts) == 2 and parts[1] == "cancel" and h.command == "POST":
            tool = "task_unsnooze"
        refusal = _rest_tool_refusal(gateway, principal, tool, h.command, h.path)
        if refusal is not None:
            send(403, refusal)
            return
        service = BoardService(ToolContext(principal=principal, gateway=gateway))
        try:
            if len(parts) == 1 and h.command == "GET":
                send(200, {"snoozed": service.task_show(parts[0]).get("snoozed")})
            elif len(parts) == 1 and h.command == "POST":
                try:
                    body = http_util.read_json(h)
                except ValueError:
                    return
                with rest_write(service.ctx, h, "task_snooze", body):
                    if set(body) - _KEYS:
                        send(400, {"error": "no: unknown field", "code": "invalid_snooze"})
                        return
                    send(200, service.task_snooze(parts[0], **body))
            elif len(parts) == 2 and parts[1] == "cancel" and h.command == "POST":
                with rest_write(service.ctx, h, "task_unsnooze", {}):
                    send(200, service.task_unsnooze(parts[0]))
            else:
                send(404, {"error": "Not found"})
        except RulesRefresh as exc:
            send(428, exc.payload)
        except CardRefused as exc:
            send(
                exc.status,
                {"error": str(exc).removeprefix("no: "), "code": exc.code, **exc.details},
            )
        except ToolError as exc:
            message = str(exc).removeprefix("no: ")
            send(404 if message == "card not found" else 403, {"error": message})

    gateway.add_prefix_route("GET", PREFIX + "/", route)
    gateway.add_prefix_route("POST", PREFIX + "/", route)
