"""Authority required to change handoff settings or start a successor."""

from .models import SCOPE_HANDOFF


class HandoffPermissionError(PermissionError):
    pass


def can_launch(principal) -> bool:
    return bool(
        getattr(principal, "person", False)
        or (principal.policy.has_scope(SCOPE_HANDOFF) and principal.policy.trust == "trusted")
    )


def require_launch_authority(principal) -> None:
    if not can_launch(principal):
        raise HandoffPermissionError(
            "handoff changes and launches require handoff:write and trusted trust"
        )
