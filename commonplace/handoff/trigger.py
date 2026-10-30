"""Decide when a thread is full enough to hand off. Owner: part T05b.

Used by the client hook on every tool call, so it must stay well under the
500 ms hook budget on a 50 MB transcript: read only the file's tail.
"""

from __future__ import annotations

from typing import Optional

from .models import ContextUsage, HandoffSettings

# Known context windows by model-name prefix; "[1m]" in a model name means 1M.
MODEL_WINDOWS: dict = {}


def window_for_model(model: str, override: Optional[int] = None) -> int:
    raise NotImplementedError


def usage_from_tail(
    path: str,
    *,
    fmt: str = "auto",
    override_window: Optional[int] = None,
    tail_bytes: int = 262_144,
) -> Optional[ContextUsage]:
    """The last model call's context size: Claude Code ``message.usage``
    (input + cache_read + cache_creation + output tokens), Codex
    ``token_count`` events. ``None`` if the tail has none."""
    raise NotImplementedError


def should_handoff(usage: Optional[ContextUsage], settings: HandoffSettings) -> bool:
    """On, usage known, and ``usage.fraction >= settings.threshold``."""
    raise NotImplementedError
