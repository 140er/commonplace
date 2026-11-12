"""``/api/card-snooze``: snooze or wake one card from the app.

    GET  /api/card-snooze/<n>           {"snoozed": {until, timezone, after_card, text, ...} | null}
    POST /api/card-snooze/<n>           snooze (200, the card); body: {"until": "2026-11-12T09:00",
                                        "timezone": "Europe/Amsterdam"} or {"after_card": 12}
    POST /api/card-snooze/<n>/cancel    unsnooze (200, the card)

Signed-in workspace owners only (403 otherwise); an assignee agent uses the ``task_snooze`` and
``task_unsnooze`` tools. The rules are ``BoardService.task_snooze`` / ``task_unsnooze``.
"""

from __future__ import annotations

from commonplace import http_util
from commonplace.boards.service import BoardService
from commonplace.boards.store import CardRefused
from commonplace.extensions import ToolContext, ToolError

PREFIX = "/api/card-snooze"
_KEYS = {"until", "timezone", "after_card"}


def install(gateway) -> None:
    def route(h):
        principal = http_util.require_principal(gateway, h)
        if principal is None:
            return

        def send(status, body):
            http_util.send_json(h, status, body)

        if not principal.person or principal.role != "owner":
            send(
                403,
                {
                    "error": "only a signed-in workspace owner can use this; agents use the task_snooze tool"
                },
            )
            return
        parts = http_util.path_parts(h, PREFIX + "/")
        service = BoardService(ToolContext(principal=principal, gateway=gateway))
        try:
            if len(parts) == 1 and h.command == "GET":
                send(200, {"snoozed": service.task_show(parts[0]).get("snoozed")})
            elif len(parts) == 1 and h.command == "POST":
                try:
                    body = http_util.read_json(h)
                except ValueError:
                    return
                if set(body) - _KEYS:
                    send(400, {"error": "no: unknown field", "code": "invalid_snooze"})
                    return
                send(200, service.task_snooze(parts[0], **body))
            elif len(parts) == 2 and parts[1] == "cancel" and h.command == "POST":
                send(200, service.task_unsnooze(parts[0]))
            else:
                send(404, {"error": "Not found"})
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
