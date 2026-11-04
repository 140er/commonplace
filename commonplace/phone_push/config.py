"""Phone notification configuration."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    url: str
    token: str | None


def load() -> Config | None:
    raise NotImplementedError("card20 stub")
