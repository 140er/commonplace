"""``/api/topology/discovery``: the map's "Start agent work on map" button and its last result.

The start is the signed-in workspace owner's own action, so it sits outside the
rules-freshness inventory of agent writes (like undo of a proposal)."""

from __future__ import annotations

from commonplace.boards._http import HTTPError, read_json, require_principal, send_json
from commonplace.extensions import ToolContext, ToolError

from . import discovery

PATH = "/api/topology/discovery"
ARGUMENTS = {"project", "topic"}


def install(gateway):
    def route(h):
        principal = require_principal(gateway, h)
        if principal is None:
            return
        ctx = ToolContext(principal=principal, gateway=gateway)
        try:
            if h.command == "GET":
                send_json(h, 200, discovery.latest(ctx))
                return
            body = read_json(h)
            if not isinstance(body, dict) or body.keys() - ARGUMENTS:
                raise HTTPError(400, "invalid arguments")
            send_json(h, 200, discovery.start(ctx, **body))
        except HTTPError as exc:
            send_json(h, exc.status, {"error": str(exc)})
        except ToolError as exc:
            send_json(h, 403, {"error": str(exc).removeprefix("no: ")})

    for method in ("GET", "POST"):
        gateway.add_route(method, PATH, route)
