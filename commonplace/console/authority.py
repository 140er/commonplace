"""Frozen console interface; implemented by the owning builder unit."""

from datetime import datetime

from commonplace.console import types


def agents(gateway, principal) -> list[types.AgentView]:
    raise NotImplementedError


def agent(gateway, principal, cid) -> types.AgentView:
    raise NotImplementedError


def status(last_seen: str | None, now: datetime) -> str:
    raise NotImplementedError
