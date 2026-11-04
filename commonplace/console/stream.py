"""Frozen console interface; implemented by the owning builder unit."""

from commonplace.console import types


def encode_cursor(a: int, e: int, r: int, c: int) -> str:
    raise NotImplementedError


def decode_cursor(text: str) -> tuple[int, int, int, int]:
    raise NotImplementedError


def page(gateway, principal, agent: types.AgentView, cursor: str | None, limit: int | None) -> dict:
    raise NotImplementedError
