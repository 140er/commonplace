"""Setup proposal tools and routes (card #675, ``docs/system/setup-proposals.md``).

- ``setup_propose`` (MCP) and ``POST /api/setup-proposals``: store a proposed setup (201, or 200 with
  the proposal that already holds the same body).
- ``setup_show`` (MCP), ``GET /api/setup-proposals[?status=]`` and ``GET /api/setup-proposals/<id>``:
  the newest proposals or one, each with its digest, its plain-words diff and, once decided, results.
- ``POST /api/setup-proposals/<id>/apply`` and ``.../reject`` with ``{sha256}``: the signed-in owner's
  decision on the exact version shown. There is no tool for it: an agent token, even the owner's,
  gets 403 ``owner_only``.

Only the workspace owner (the signed-in owner or a connection of the owner) is answered. Everyone
else gets the 404 of an unknown route and does not see the tools.
"""

from __future__ import annotations

import logging
import sqlite3

from commonplace.extensions import WORK_PROFILES, LocalTool, ToolContext, ToolError, register
from commonplace.http_util import (
    log_failure,
    path_parts,
    query,
    read_json,
    require_principal,
    send_json,
)
from commonplace.projects import setups

logger = logging.getLogger(__name__)
ROUTE = "/api/setup-proposals"
_registered = False

MODEL_CHOICE = {
    "type": ["object", "null"],
    "properties": {"provider": {"type": "string"}, "model": {"type": "string"}},
    "required": ["provider", "model"],
    "additionalProperties": False,
}
ROLES = {
    "type": "object",
    "properties": {role: MODEL_CHOICE for role in ("main", "worker", "reviewer", "gate")},
    "additionalProperties": False,
}
SETUP = {
    "bots": {
        "type": "array",
        "maxItems": setups.MAX_BOTS,
        "items": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "name": {"type": "string"},
                "instructions": {"type": "string"},
            },
            "required": ["key", "name"],
            "additionalProperties": False,
        },
    },
    "coworkers": {
        "type": "array",
        "maxItems": setups.MAX_COWORKERS,
        "items": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "board": {"type": "string"},
                "bot": {"type": "string", "description": "The key of a bot in this setup."},
                "title": {"type": "string"},
            },
            "required": ["key", "board", "bot"],
            "additionalProperties": False,
        },
    },
    "models": {
        "type": "object",
        "properties": {
            "workspace": ROLES,
            "projects": {"type": "object", "additionalProperties": ROLES},
        },
        "additionalProperties": False,
    },
}


def available(principal, gateway) -> bool:
    return gateway is not None and setups.is_owner(principal)


def _error_text(exc: setups.SetupError) -> str:
    text = f"no: {exc} ({exc.code})"
    return text + (f" at {exc.path}" if exc.path else "")


def propose_tool(ctx, args):
    try:
        view, _created = setups.propose(ctx, args)
    except setups.SetupError as exc:
        raise ToolError(_error_text(exc)) from None
    return view


def show_tool(ctx, args):
    if not isinstance(args, dict) or set(args) - {"id", "status"}:
        raise ToolError("no: invalid arguments")
    try:
        if args.get("id") is not None:
            return setups.show(ctx, args["id"])
        return setups.listing(ctx, args.get("status"))
    except setups.SetupError as exc:
        raise ToolError(_error_text(exc)) from None


def register_tools() -> None:
    global _registered
    if _registered:
        return
    register(
        LocalTool(
            name="setup_propose",
            description=(
                "Propose one setup for the workspace owner to approve in one tap: bots (key, name, "
                "instructions), a Coworker per project bound to one of those bots (key, board, bot "
                "key, title) and a model per role (main, worker, reviewer, gate) for projects and "
                "the workspace default ({provider, model}, or null to clear). Nothing changes until "
                "the signed-in owner approves this exact version by its sha256. Never put a token, "
                "key or password in a setup."
            ),
            input_schema={"type": "object", "properties": SETUP, "additionalProperties": False},
            handler=propose_tool,
            scope="memory:write",
            kind="write",
            profiles=WORK_PROFILES,
            trusted_only=True,
            owner_only=True,
            available=available,
        )
    )
    register(
        LocalTool(
            name="setup_show",
            description=(
                "Read setup proposals: one by id, or the newest (optionally of one status: "
                "proposed, applying, applied, partial, rejected). Each has its sha256, the "
                "plain-words changes it makes against the workspace now and, once the owner "
                "decided, who decided and the created ids."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "minimum": 1},
                    "status": {"type": "string", "enum": list(setups.STATUSES)},
                },
                "additionalProperties": False,
            },
            handler=show_tool,
            scope="memory:read",
            profiles=WORK_PROFILES,
            owner_only=True,
            available=available,
        )
    )
    _registered = True


def _number(text: str):
    return int(text) if text.isdigit() and len(text) <= 9 else None


def route(gateway, h) -> None:
    principal = require_principal(gateway, h)
    if principal is None:
        return
    try:
        if not setups.is_owner(principal):
            raise setups.Hidden()
        ctx = ToolContext(principal=principal, gateway=gateway)
        parts = path_parts(h, ROUTE + "/") if h.path.startswith(ROUTE + "/") else []
        if h.command == "POST" and not parts:
            try:
                body = read_json(h)
            except ValueError:
                return
            if isinstance(body, dict):
                # The web shell adds its rules version to every write. The freshness check runs on
                # the MCP tool (a checked write); these routes answer the owner only.
                body.pop("rules_seq", None)
            view, created = setups.propose(ctx, body)
            send_json(h, 201 if created else 200, view)
            return
        if h.command == "GET" and not parts:
            args = query(h)
            if set(args) - {"status"}:
                raise setups.SetupError("invalid", "unknown query field")
            send_json(h, 200, setups.listing(ctx, args.get("status")))
            return
        if h.command == "GET" and len(parts) == 1 and _number(parts[0]) is not None:
            send_json(h, 200, setups.show(ctx, _number(parts[0])))
            return
        if (
            h.command == "POST"
            and len(parts) == 2
            and parts[1] in ("apply", "reject")
            and _number(parts[0]) is not None
        ):
            try:
                body = read_json(h)
            except ValueError:
                return
            if isinstance(body, dict):
                # The web shell adds its rules version to every write. The freshness check runs on
                # the MCP tool (a checked write); these routes answer the owner only.
                body.pop("rules_seq", None)
            if not isinstance(body, dict) or set(body) != {"sha256"}:
                raise setups.SetupError("invalid", "send {sha256}", path="sha256")
            settle = setups.apply if parts[1] == "apply" else setups.reject
            send_json(h, 200, settle(ctx, _number(parts[0]), body["sha256"]))
            return
        raise setups.Hidden()
    except setups.Hidden:
        send_json(h, 404, {"error": "not found"})
    except setups.SetupError as exc:
        send_json(h, exc.status, {"error": str(exc), "code": exc.code, "path": exc.path})
    except sqlite3.OperationalError:
        send_json(h, 409, {"error": "busy", "code": "busy"})
    except Exception as exc:
        log_failure(logger, "setup proposal request failed", exc)
        send_json(h, 500, {"error": "setup proposal request failed"})


def install(gateway) -> None:
    for method in ("GET", "POST"):
        gateway.add_route(method, ROUTE, lambda h: route(gateway, h))
    for method in ("GET", "POST"):
        gateway.add_prefix_route(method, ROUTE + "/", lambda h: route(gateway, h))
