"""Frozen console interface; implemented by the owning builder unit."""

from commonplace.console import types


def latest_ids(gateway, agent: types.AgentView) -> tuple[int, int]:
    raise NotImplementedError


def recent(gateway, agent: types.AgentView) -> list[dict]:
    raise NotImplementedError
