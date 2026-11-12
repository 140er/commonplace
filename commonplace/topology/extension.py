"""The topology read tools and routes: ``topology_get`` and ``topology_route``.

Same scope and wing grant as any other memory read: the token needs ``memory:read`` and
a read grant on the ``topology`` wing. The store does not authorize, so every entry
point here checks the grant before it reads. A store is one workspace's file, so the
answer never holds another workspace's facts, and it holds no secrets because none
are stored.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from commonplace.extensions import ALL_PROFILES, LocalTool, ToolContext, ToolError, register
from commonplace.http_util import log_failure, query, require_principal, send_json

from . import answers, model, status, store

logger = logging.getLogger(__name__)

_KEY = {"type": "string", "minLength": 1, "maxLength": 63}


class Refused(ToolError):
    """The caller may not read the topology (REST answers 403)."""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _graph(ctx):
    """The workspace's whole map and the instant it was read, after the wing check."""
    if not ctx.principal.policy.can_read_wing(model.TOPOLOGY_WING):
        raise Refused("no: this connection cannot read the topology wing")
    now = _now()
    topo = store.open_topology(ctx.gateway, ctx.principal.workspace_id)
    return store.read_graph(topo, now=now), model.stamp(now)


def _arguments(args, allowed):
    if not isinstance(args, dict) or args.keys() - allowed:
        raise ToolError("no: invalid arguments")
    return args


def topology_get(ctx, args):
    args = _arguments(args, {"node", "site"})
    graph, now = _graph(ctx)
    try:
        answer = answers.get_answer(graph, node=args.get("node"), site=args.get("site"), now=now)
    except model.TopologyError as exc:
        raise ToolError(str(exc)) from None
    if answer["scope"] == "graph":
        # The same graph goes to the status read, so the map and its status describe one moment.
        live = status.read_status(ctx.gateway, ctx.principal.workspace_id, graph=graph)
        answer["status"] = answers.status_words(graph, live)
    return answer


def topology_route(ctx, args):
    args = _arguments(args, {"target", "from"})
    if "target" not in args:
        raise ToolError("no: target is required")
    graph, now = _graph(ctx)
    try:
        return answers.route_answer(graph, args["target"], args.get("from"), now=now)
    except model.TopologyError as exc:
        raise ToolError(str(exc)) from None


register(
    LocalTool(
        name="topology_get",
        description=(
            "Read the compute map: one machine or site by key, or the whole graph of machines, "
            "links and site routes. Every value carries its provenance and freshness; stale "
            "values are marked, and a key nothing is recorded for answers found=false."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "node": {
                    **_KEY,
                    "description": "Key of a machine or site; omit both for the graph.",
                },
                "site": {**_KEY, "description": "Key of a website whose route to read."},
            },
            "additionalProperties": False,
        },
        handler=topology_get,
        scope="memory:read",
        profiles=ALL_PROFILES,
    )
)

register(
    LocalTool(
        name="topology_route",
        description=(
            "Answer 'how do I connect to X' (ordered hops and the method, alias, jump host or "
            "relay per hop, from a machine named in 'from') or 'where is site Y hosted' (the "
            "route from DNS provider through proxy and host to the app). Each hop says how "
            "fresh it is; an unknown or stale hop is stated, never invented."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target": {**_KEY, "description": "Key of the machine or site to reach."},
                "from": {
                    **_KEY,
                    "description": "Key of the machine you start from; used for machines only.",
                },
            },
            "required": ["target"],
            "additionalProperties": False,
        },
        handler=topology_route,
        scope="memory:read",
        profiles=ALL_PROFILES,
    )
)


def install(gateway):
    def serve(tool, handler, names):
        def handle(h):
            principal = require_principal(gateway, h)
            if principal is None:
                return
            try:
                if principal.policy.refusal(tool):
                    raise Refused("no: topology read refused")
                params = query(h)
                if params.keys() - names:
                    raise ToolError("no: invalid arguments")
                ctx = ToolContext(principal=principal, gateway=gateway)
                send_json(h, 200, handler(ctx, params))
            except Refused as exc:
                send_json(h, 403, {"error": str(exc)})
            except ToolError as exc:
                send_json(h, 400, {"error": str(exc)})
            except Exception as exc:
                log_failure(logger, "topology request failed", exc)
                send_json(h, 500, {"error": "topology request failed"})

        return handle

    gateway.add_route("GET", "/api/topology", serve("topology_get", topology_get, {"node", "site"}))
    gateway.add_route(
        "GET", "/api/topology/route", serve("topology_route", topology_route, {"target", "from"})
    )
