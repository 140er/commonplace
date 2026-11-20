"""``/api/previews``: people list, publish, stop, reopen and (owners) delete the files of previews of their cards.

    GET    /api/previews[?card=<n>|?pr=<url>]  {previews, usage, limits, rule}
    POST   /api/previews                       publish; body {card, name, files:[{path,text|base64}]}
    GET    /api/previews/rules                 the workspace stop rule
    PUT    /api/previews/rules                 owner: {stop_days_after_pr, max_age_days} (null = off)
    GET    /api/previews/<id>                  one preview with its revisions
    POST   /api/previews/<id>/stop             stop serving (files kept)
    POST   /api/previews/<id>/open-again       serve a stopped preview again
    DELETE /api/previews/<id>                  owner: delete the files; body {confirm: <id>}

Only signed-in people use these routes (a Coworker uses the preview tools); every lookup goes
through the card, so a missing preview, another workspace's and a card the caller cannot see
all answer the same 404.
"""

from __future__ import annotations

import json

from commonplace import http_util

from . import service
from .bundle import Refused
from .store import Forbidden, NotFound

PREFIX = "/api/previews"
# 5 MiB of files as base64 (6.7 MiB) plus the JSON around it; the gateway's own cap is 8 MiB.
MAX_BODY = 7 * 1024 * 1024
_PUBLISH_KEYS = {"card", "name", "files"}


def read_body(h) -> dict:
    """Like ``http_util.read_json`` with this route's larger bound; replies 400 itself."""
    try:
        if h.headers.get("Transfer-Encoding"):
            raise ValueError("Transfer encoding is not supported")
        length = int(h.headers.get("Content-Length", "0"))
        if not 0 < length <= MAX_BODY:
            raise ValueError("body size")
        body = h.rfile.read(length)
        if len(body) != length:
            raise ValueError("incomplete body")
        result = json.loads(body.decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("body must be an object")
    except (ValueError, UnicodeError, RecursionError) as exc:
        h.close_connection = True
        http_util.send_json(h, 400, {"error": "Invalid JSON body", "reason": "invalid"})
        raise ValueError("Invalid JSON body") from exc
    return result


def install(gateway) -> None:
    def route(h):
        principal = http_util.require_principal(gateway, h)
        if principal is None:
            return

        def send(status, body):
            http_util.send_json(h, status, body)

        if not principal.person:
            send(
                403,
                {
                    "error": "Sign in as a person to use this page; Coworkers use the preview tools.",
                    "reason": "person_only",
                },
            )
            return
        parts = http_util.path_parts(h, PREFIX + "/")
        method = h.command
        try:
            if not parts and method == "GET":
                q = http_util.query(h)
                card = q.get("card")
                send(
                    200,
                    service.listing(
                        gateway,
                        principal,
                        card=card if card else None,
                        pr=q.get("pr") or None,
                    ),
                )
            elif not parts and method == "POST":
                try:
                    body = read_body(h)
                except ValueError:
                    return
                if set(body) - _PUBLISH_KEYS or not {"card", "name", "files"} <= set(body):
                    raise ValueError("send card, name and files")
                shown, created = service.publish(
                    gateway, principal, body["card"], body["name"], body["files"]
                )
                send(201 if created else 200, shown)
            elif parts == ["rules"] and method == "GET":
                send(200, service.get_rules(gateway, principal))
            elif parts == ["rules"] and method == "PUT":
                try:
                    body = http_util.read_json(h)
                except ValueError:
                    return
                send(200, service.set_rules(gateway, principal, body))
            elif len(parts) == 1 and method == "GET":
                send(200, service.get(gateway, principal, parts[0]))
            elif len(parts) == 1 and method == "DELETE":
                try:
                    body = http_util.read_json(h)
                except ValueError:
                    return
                if not isinstance(body, dict):
                    raise ValueError("send {confirm: <preview id>}")
                send(200, service.delete_files(gateway, principal, parts[0], body.get("confirm")))
            elif len(parts) == 2 and method == "POST" and parts[1] == "stop":
                send(200, service.stop(gateway, principal, parts[0]))
            elif len(parts) == 2 and method == "POST" and parts[1] == "open-again":
                send(200, service.reopen(gateway, principal, parts[0]))
            else:
                send(404, {"error": "Not found", "reason": "not_found"})
        except NotFound:
            send(404, {"error": "Preview or card not found", "reason": "not_found"})
        except Forbidden as exc:
            words = (
                "Only the workspace owner can change this rule."
                if exc.args == ("owner",)
                else "You cannot do that to this preview."
            )
            send(403, {"error": words, "reason": "forbidden"})
        except Refused as exc:
            send(service.REFUSAL_STATUS.get(exc.reason, 422), service.refusal_payload(exc))
        except ValueError as exc:
            send(400, {"error": str(exc), "reason": "invalid"})

    gateway.add_route("GET", PREFIX, route)
    gateway.add_route("POST", PREFIX, route)
    for method in ("GET", "POST", "PUT", "DELETE"):
        gateway.add_prefix_route(method, PREFIX + "/", route)
