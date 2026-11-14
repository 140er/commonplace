"""The ``topology_discover_site`` tool: an agent hands in a domain and the proxy config
it read, and gets the site's route proposed to the owner.

All rules live in ``site_routes``; this module checks the arguments' shape and words the
reply.
"""

from __future__ import annotations

from commonplace.boards.display import card_url
from commonplace.extensions import WORK_PROFILES, LocalTool, ToolError, register

from . import proxy_adapters, site_routes
from .model import TOPOLOGY_WING, TopologyError
from .proposal_api import _count

TOOL = "topology_discover_site"
ARGUMENTS = {"site", "proxies", "machines", "apps", "ttl_seconds", "board"}
ENDS = {
    "app": "ends at the container or app",
    "host": "ends at the machine",
    "edge": "ends at the proxy or CDN in front",
    "external": "ends at an external / unknown hop",
}


def _tool(ctx, args):
    if not isinstance(args, dict) or args.keys() - ARGUMENTS:
        raise ToolError("no: invalid arguments")
    try:
        result = site_routes.discover(ctx, **args)
    except TopologyError as exc:
        raise ToolError(str(exc)) from None
    lines = []
    if result["hops"]:
        chain = ", ".join(hop["link"] for hop in result["hops"])
        lines.append(f"route {result['site']}: {chain} ({ENDS[result['ends']]})")
    if result["applied"]:
        lines.append(f"applied {_count(result['applied'])} ({result['applied']['id']})")
    if result["proposal"]:
        row = result["proposal"]
        lines.append(
            f"proposal {row['id']} with {_count(row)} waits for the owner: "
            f"#{row['card']} {card_url(ctx, row['card'])}"
        )
    if result["unchanged"]:
        lines.append(f"{result['unchanged']} already on the map")
    if result["withheld"]:
        lines.append(f"{result['withheld']} config lines were left out: they looked like a secret")
    lines += [f"note: {note}" for note in result["notes"]]
    return "\n".join(lines)


_NODE = {"type": "string", "description": "Node key on the map."}
_NAMES = {"type": "array", "maxItems": site_routes.MAX_NAMES, "items": {"type": "string"}}

register(
    LocalTool(
        name=TOOL,
        description="Discover how one website is routed, read-only, and propose the route to the "
        "workspace owner: a public DNS lookup of the domain, then the reverse proxy config you "
        "read on the user's machine and pass in as text, then the container or app it forwards "
        "to. Only site names and upstream targets are taken from the config; credentials in it "
        "are never stored. A domain or upstream outside the known machines ends in an "
        "'External / unknown' hop. A changed route is a decision for the owner, never a silent "
        "rewrite.",
        input_schema={
            "type": "object",
            "properties": {
                "site": {
                    "type": "string",
                    "description": "The public domain name, such as example.com.",
                },
                "proxies": {
                    "type": "array",
                    "maxItems": site_routes.MAX_PROXIES,
                    "items": {
                        "type": "object",
                        "properties": {
                            "node": _NODE,
                            "kind": {"type": "string", "enum": sorted(proxy_adapters.ADAPTERS)},
                            "config": {
                                "type": "string",
                                "maxLength": proxy_adapters.MAX_CONFIG,
                                "description": "The config text as read, unchanged: a Caddyfile "
                                "or Caddy JSON config; Nginx Proxy Manager proxy-host rows as "
                                "JSON or its generated nginx server files.",
                            },
                        },
                        "required": ["node", "kind", "config"],
                        "additionalProperties": False,
                    },
                    "description": "The reverse proxy config of each machine the site may pass.",
                },
                "machines": {
                    "type": "array",
                    "maxItems": site_routes.MAX_MACHINES,
                    "items": {
                        "type": "object",
                        "properties": {
                            "node": _NODE,
                            "addresses": {
                                **_NAMES,
                                "description": "Public addresses or hostnames you saw for it.",
                            },
                            "kind": {
                                "type": "string",
                                "description": "Needed for a node that is not on the map yet; "
                                "cloud for a proxy or CDN in front.",
                            },
                        },
                        "required": ["node", "addresses"],
                        "additionalProperties": False,
                    },
                    "description": "Where the user's machines answer, to match the DNS answer.",
                },
                "apps": {
                    "type": "array",
                    "maxItems": site_routes.MAX_APPS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "node": _NODE,
                            "host": {"type": "string", "description": "Node key of its machine."},
                            "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                            "names": {
                                **_NAMES,
                                "description": "Names the proxy may use for it (container name).",
                            },
                        },
                        "required": ["node", "host"],
                        "additionalProperties": False,
                    },
                    "description": "Containers or apps you saw running, to name the upstream.",
                },
                "ttl_seconds": {"type": "integer"},
                "board": {"type": "string"},
            },
            "required": ["site"],
            "additionalProperties": False,
        },
        handler=_tool,
        scope="memory:write",
        kind="write",
        profiles=WORK_PROFILES,
        available=lambda principal, _gateway: principal.policy.can_write_wing(TOPOLOGY_WING),
    )
)
