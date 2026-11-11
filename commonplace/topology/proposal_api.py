"""The write surface of the topology: the ``topology_propose`` tool, its REST routes and
the listener that applies a proposal when the owner answers its decision card.

All rules live in ``proposals``; this module only checks the transport, shapes replies
and maps refusals to statuses.
"""

from __future__ import annotations

import logging

from commonplace import events
from commonplace.boards._http import (
    HTTPError,
    path_parts,
    query,
    read_json,
    require_principal,
    send_json,
)
from commonplace.boards.display import card_url
from commonplace.extensions import WORK_PROFILES, LocalTool, ToolContext, ToolError, register
from commonplace.http_util import log_failure
from commonplace.rules.enforce import RulesRefresh, rest_write

from . import proposals
from .model import MAX_EVIDENCE, TOPOLOGY_WING, TopologyError

logger = logging.getLogger(__name__)

TOOL = "topology_propose"
ARGUMENTS = {"items", "remove", "evidence", "ttl_seconds", "board"}
PREFIX = "/api/topology/proposals"


def _propose(ctx, args):
    if not isinstance(args, dict) or args.keys() - ARGUMENTS:
        raise ToolError("no: invalid arguments")
    try:
        return proposals.propose(ctx, **args)
    except TopologyError as exc:
        raise ToolError(str(exc)) from None


def _count(row) -> str:
    count = len(row["changes"])
    return f"{count} fact" + ("" if count == 1 else "s")


def _tool(ctx, args):
    result = _propose(ctx, args)
    lines = []
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
    return "\n".join(lines)


def _send(h, call):
    """Run a route body and turn refusals into ``{"error": ...}`` replies."""
    try:
        status, payload = call()
    except RulesRefresh as exc:
        send_json(h, 428, exc.payload)
    except HTTPError as exc:
        send_json(h, exc.status, {"error": str(exc)})
    except ToolError as exc:
        message = str(exc).removeprefix("no: ")
        status = 404 if message in ("proposal not found", "board not found") else 403
        if message == "invalid arguments":
            status = 400
        send_json(h, status, {"error": message})
    except TopologyError as exc:
        send_json(h, 400, {"error": str(exc).removeprefix("no: ")})
    else:
        send_json(h, status, payload)


def install(gateway):
    def route(h):
        principal = require_principal(gateway, h)
        if principal is None:
            return
        ctx = ToolContext(principal=principal, gateway=gateway)
        parts = path_parts(h, PREFIX)

        def call():
            if not parts and h.command == "GET":
                return 200, {"proposals": proposals.listing(ctx, query(h).get("state") or None)}
            if not parts and h.command == "POST":
                body = read_json(h)
                with rest_write(ctx, h, TOOL, body):
                    if body.keys() - ARGUMENTS:
                        raise HTTPError(400, "invalid arguments")
                    try:
                        return 201, proposals.propose(ctx, **body)
                    except TopologyError as exc:
                        raise HTTPError(400, str(exc).removeprefix("no: ")) from None
            if len(parts) == 1 and h.command == "GET":
                return 200, proposals.get(ctx, parts[0])
            # Undo is a signed-in owner's write, so it sits outside the rules-freshness
            # inventory, which covers agent writes.
            if len(parts) == 2 and parts[1] == "undo" and h.command == "POST":
                read_json(h)
                return 200, proposals.undo(ctx, parts[0])
            raise HTTPError(404, "not found")

        _send(h, call)

    for method in ("GET", "POST"):
        gateway.add_route(method, PREFIX, route)
        gateway.add_prefix_route(method, PREFIX + "/", route)

    def decided(event):
        """Apply or close the proposal behind a decision card the owner just answered."""
        if event.get("kind") != "card.decided" or not events.is_stored(gateway.db, event):
            return
        try:
            proposals.settle(gateway, event["workspace_id"], card=event["data"]["number"])
        except Exception as exc:
            # The next read of the proposals settles it again.
            log_failure(logger, "topology proposal not settled", exc)

    if getattr(gateway, "_topology_decided", None) is None:
        gateway._topology_decided = decided
        events.add_listener(decided)
        # A closed hub must not keep answering events of another one.
        gateway.db.add_pre_close_callback(lambda: events.remove_listener(decided))


register(
    LocalTool(
        name=TOOL,
        description="Propose topology facts with evidence: nodes, links and site routes to add or "
        "change (items), and subjects to take off the map (remove). A runner's additions about "
        "itself apply at once; every other change becomes one decision the workspace owner "
        "approves or rejects in the app. Never include keys, tokens or passwords: such a write "
        "is refused.",
        input_schema={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "maxItems": proposals.MAX_ITEMS,
                    "items": {"type": "object"},
                    "description": '{"node": key, "fields": {...}}, {"link": [from, to, method], '
                    '"fields": {...}} or {"route": site, "hops": [link ids]}.',
                },
                "remove": {
                    "type": "array",
                    "maxItems": proposals.MAX_ITEMS,
                    "items": {
                        "anyOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "subject": {"type": "string"},
                                    "field": {"type": "string"},
                                },
                                "required": ["subject"],
                                "additionalProperties": False,
                            },
                        ]
                    },
                    "description": "node:<key>, link:<from>><to>:<method> or route:<site>; "
                    "{subject, field} removes one field.",
                },
                "evidence": {
                    "type": "string",
                    "maxLength": MAX_EVIDENCE,
                    "description": "What you saw, verbatim (command output, a config line).",
                },
                "ttl_seconds": {"type": "integer"},
                "board": {"type": "string"},
            },
            "required": ["evidence"],
            "additionalProperties": False,
        },
        handler=_tool,
        scope="memory:write",
        kind="write",
        profiles=WORK_PROFILES,
        available=lambda principal, _gateway: principal.policy.can_write_wing(TOPOLOGY_WING),
    )
)
