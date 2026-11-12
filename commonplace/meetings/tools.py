"""The one MCP tool: read-only status of the caller's own meetings (no text of any kind)."""

from __future__ import annotations

from commonplace.extensions import ALL_PROFILES, LocalTool, register
from commonplace.meetings import store


def meeting_status(ctx, args):
    meetings = store.open_meetings(ctx.gateway, ctx.principal.workspace_id)
    return {"meetings": meetings.status_for(ctx.principal.user_id)}


def _available(principal, gateway) -> bool:
    return store.open_meetings(gateway, principal.workspace_id).enabled()


register(
    LocalTool(
        name="meeting_status",
        description=(
            "State, times and counts of your own meeting-notes bot meetings. No notes, "
            "transcript or participant text; it cannot invite, join, file or approve anything."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=meeting_status,
        scope="events:read",
        kind="read",
        profiles=ALL_PROFILES,
        available=_available,
    )
)
