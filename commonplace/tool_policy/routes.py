"""``/api/tool-policy``: the workspace owner reads and sets the mode of each tool action.

    GET  /api/tool-policy   {modes, actions: [{name, kind, mode}], approval}
    POST /api/tool-policy   body: {"actions": {"<name>": "allowed" | "ask" | "off", ...}}

Only a signed-in workspace owner may use it (403 otherwise, including for every agent token,
whatever its scopes). There is no MCP tool for it, so an agent cannot change the policy.
"""

from __future__ import annotations

from commonplace import extensions, http_util
from commonplace.policy import TOOL_RULES
from mempalace.tool_capabilities import classify_tool

from . import gate, store

PATH = "/api/tool-policy"
MAX_CHANGES = 500


def catalog() -> dict:
    """Every action the gateway serves, by name: ``read`` or ``write``."""
    kinds = {name: "read" if classify_tool(name) == "read" else "write" for name in TOOL_RULES}
    kinds.update({name: extensions.get(name).kind for name in extensions.names()})
    return kinds


def view(db, workspace_id: str) -> dict:
    kinds, stored = catalog(), store.modes(db, workspace_id)
    # A stored row for an action that is gone stays visible, so the owner can clear it.
    names = sorted(set(kinds) | set(stored))
    return {
        "modes": list(store.MODES),
        "actions": [
            {
                "name": name,
                "kind": kinds.get(name, "unknown"),
                "mode": stored.get(name, store.ALLOWED),
            }
            for name in names
        ],
        "approval": {
            "request_ttl_s": gate.REQUEST_TTL_S,
            "approval_ttl_s": gate.APPROVAL_TTL_S,
        },
    }


def changes(db, workspace_id: str, body: dict) -> dict:
    wanted = body.get("actions")
    if set(body) != {"actions"} or not isinstance(wanted, dict) or not wanted:
        raise ValueError('send {"actions": {"<name>": "allowed" | "ask" | "off"}}')
    if len(wanted) > MAX_CHANGES:
        raise ValueError(f"at most {MAX_CHANGES} actions per request")
    known, stored = catalog(), store.modes(db, workspace_id)
    for name, mode in wanted.items():
        if not isinstance(mode, str) or mode not in store.MODES:
            raise ValueError("a mode is allowed, ask or off")
        # Restoring Allowed is always possible; narrowing needs an action that exists.
        if name not in known and not (name in stored and mode == store.ALLOWED):
            raise ValueError(f"unknown action {name[:120]!r}")
    return wanted


def install(gateway) -> None:
    def route(h):
        principal = http_util.require_principal(gateway, h)
        if principal is None:
            return
        if not principal.person or principal.role != "owner":
            http_util.send_json(
                h, 403, {"error": "no: only a signed-in workspace owner can manage tool policy"}
            )
            return
        db, workspace = gateway.db, principal.workspace_id
        if h.command == "POST":
            try:
                body = http_util.read_json(h)
            except ValueError:
                return
            try:
                wanted = changes(db, workspace, body)
            except ValueError as exc:
                http_util.send_json(h, 400, {"error": f"no: {exc}", "code": "invalid"})
                return
            before = store.modes(db, workspace)
            store.set_modes(db, workspace, wanted, actor=principal.actor, now=gate._clock(gateway))
            for name, mode in sorted(wanted.items()):
                if before.get(name, store.ALLOWED) != mode:
                    db.audit(
                        action="tool_policy.set",
                        decision=mode,
                        workspace_id=workspace,
                        user_id=principal.user_id,
                        auth=principal.auth,
                        tool=name,
                    )
        http_util.send_json(h, 200, view(db, workspace))

    gateway.add_route("GET", PATH, route)
    gateway.add_route("POST", PATH, route)
