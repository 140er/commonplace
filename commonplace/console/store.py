"""Frozen console interface; implemented by the owning builder unit."""

from __future__ import annotations


class ActivityStore:
    dropped: int = 0

    def append(self, cid, name, outcome, ms) -> int | None:
        raise NotImplementedError

    def after(self, cid, seq, limit) -> list[dict]:
        raise NotImplementedError

    def tail(self, cid, limit) -> list[dict]:
        raise NotImplementedError

    def latest_at(self, cids) -> dict[str, str]:
        raise NotImplementedError


def exists(gateway, ws) -> bool:
    raise NotImplementedError


def peek(gateway, ws) -> ActivityStore | None:
    raise NotImplementedError


def open_activity(gateway, ws) -> ActivityStore:
    raise NotImplementedError
