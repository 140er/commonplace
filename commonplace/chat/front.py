"""Receipt and listener interfaces."""

from commonplace.chat.types import OrchestratorView


def orchestrator(gateway, principal, board: str) -> OrchestratorView | None:
    raise NotImplementedError


def receipt(gateway, principal, board: str, *, work: bool) -> tuple[str, str]:
    raise NotImplementedError


def on_event(gateway, event: dict) -> None:
    raise NotImplementedError


def install_listener(gateway) -> None:
    return None
