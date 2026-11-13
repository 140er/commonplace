"""``bridge_status``: the one agent tool of the bridge, read-only and for the workspace owner.

The tool is hidden until the workspace has an install. Agents never install, map, link or
read credentials; this tool lists installs (platform, label,
on or off, tested, mapped channel count, last intake time) and nothing else.
"""

import json

from commonplace.chat_bridge import store
from commonplace.extensions import ALL_PROFILES, LocalTool, register

_registered = False


def _available(principal, gateway) -> bool:
    """Visible only once the workspace has an install, so the tool list of a workspace that
    never used the bridge is exactly what it was without this module."""
    if gateway is None or getattr(principal, "runner", None) is not None:
        return False
    bridge = store.open_bridge(gateway, principal.workspace_id)
    return bridge.exists() and bool(bridge.installs())


def _status(ctx, args) -> str:
    rows = store.open_bridge(ctx.gateway, ctx.principal.workspace_id).status()
    keys = ("id", "platform", "label", "enabled", "tested_at", "channels", "last_intake")
    return json.dumps({"installs": [{k: r[k] for k in keys} for r in rows]}, indent=2)


def register_tools() -> None:
    global _registered
    if _registered:
        return
    register(
        LocalTool(
            name="bridge_status",
            description=(
                "Read-only (workspace owner): the chat-platform installs of this workspace, "
                "whether each is on, tested, how many channels are mapped and when it last "
                "received an event. Never returns credentials."
            ),
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=_status,
            scope="memory:read",
            kind="read",
            profiles=ALL_PROFILES,
            trusted_only=True,
            owner_only=True,
            available=_available,
        )
    )
    _registered = True
