"""``/api/topology/add-machine`` and ``/api/topology/add-site``: the map's two add buttons.

Both are the signed-in workspace owner's own action, so they sit outside the rules-freshness
inventory of agent writes, like ``/api/topology/discovery``. All rules live in ``add_node``."""

from __future__ import annotations

from commonplace.boards._http import HTTPError, read_json, require_principal, send_json
from commonplace.extensions import ToolContext, ToolError

from . import add_node

ROUTES = {
    "/api/topology/add-machine": (add_node.add_machine, {"project", "topic"}),
    "/api/topology/add-site": (add_node.add_site, {"site", "project", "topic"}),
}


def install(gateway):
    def route_for(path, action, arguments):
        def route(h):
            principal = require_principal(gateway, h)
            if principal is None:
                return
            ctx = ToolContext(principal=principal, gateway=gateway)
            try:
                body = read_json(h)
                if not isinstance(body, dict) or body.keys() - arguments:
                    raise HTTPError(400, "invalid arguments")
                send_json(h, 200, action(ctx, **body))
            except HTTPError as exc:
                send_json(h, exc.status, {"error": str(exc)})
            except add_node.InvalidSite as exc:
                send_json(h, 400, {"error": str(exc).removeprefix("no: ")})
            except ToolError as exc:
                send_json(h, 403, {"error": str(exc).removeprefix("no: ")})

        return route

    for path, (action, arguments) in ROUTES.items():
        gateway.add_route("POST", path, route_for(path, action, arguments))
