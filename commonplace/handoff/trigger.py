"""Decide when a thread is full enough to hand off. Owner: part T06.

Used by the client hook on every tool call, so it must stay well under the
500 ms hook budget on a 50 MB transcript: read only the file's tail.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from .models import DEFAULT_CONTEXT_WINDOW, ContextUsage, HandoffSettings

# Codex rollout events can report a more precise window than these fallbacks.
MODEL_WINDOWS: dict[str, int] = {
    "claude-opus-": 200_000,
    "claude-sonnet-": 200_000,
    "claude-haiku-": 200_000,
    "claude-fable-": 200_000,
    "gpt-6": 400_000,
    "gpt-5": 400_000,
}

_CLAUDE_TOKEN_FIELDS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
)


def window_for_model(model: str, override: Optional[int] = None) -> int:
    """Resolve an explicit override, a 1M marker, then a known model prefix."""
    if override is not None:
        return override
    name = model.strip().lower()
    if "[1m]" in name or name.endswith("-1m"):
        return 1_000_000
    for prefix, window in MODEL_WINDOWS.items():
        if name.startswith(prefix):
            return window
    return DEFAULT_CONTEXT_WINDOW


def _sum_tokens(usage: object, fields: tuple[str, ...]) -> Optional[int]:
    if not isinstance(usage, dict) or not any(field in usage for field in fields):
        return None
    values = [usage.get(field, 0) for field in fields]
    if any(type(value) is not int or value < 0 for value in values):
        return None
    return sum(values)


def _codex_usage(payload: dict) -> Optional[tuple[int, Optional[int]]]:
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    last = info.get("last_token_usage")
    used = _sum_tokens(last, ("total_tokens",))
    if used is None:
        used = _sum_tokens(last, ("input_tokens", "output_tokens"))
    if used is None:
        return None
    window = info.get("model_context_window")
    if type(window) is not int or window <= 0:
        window = None
    return used, window


def _scan_usage(data: bytes, fmt: str, override: Optional[int]) -> Optional[ContextUsage]:
    latest_codex: Optional[tuple[int, Optional[int]]] = None
    model: Optional[str] = None
    for line in reversed(data.split(b"\n")):
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            continue
        if not isinstance(record, dict):
            continue
        kind = record.get("type")
        payload = record.get("payload")
        # T06 uses the latest turn context even if no usage follows it yet.
        if fmt != "claude-code" and kind == "turn_context" and isinstance(payload, dict):
            if model is None:
                name = payload.get("model")
                model = name if isinstance(name, str) else ""
            if latest_codex is not None and model is not None:
                break
        # Once the last Codex usage is found, only its model remains to find.
        if latest_codex is not None:
            continue
        if fmt != "codex" and kind == "assistant":
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            used = _sum_tokens(message.get("usage"), _CLAUDE_TOKEN_FIELDS)
            if used is not None:
                name = message.get("model")
                name = name if isinstance(name, str) else ""
                return ContextUsage(used, window_for_model(name, override), name)
        elif (
            fmt != "claude-code"
            and kind == "event_msg"
            and isinstance(payload, dict)
            and payload.get("type") == "token_count"
        ):
            latest_codex = _codex_usage(payload)
            if latest_codex is not None and model is not None:
                break
    if latest_codex is None:
        return None
    used, reported_window = latest_codex
    window = override if override is not None else reported_window
    return ContextUsage(used, window_for_model(model or "", window), model or "")


def usage_from_tail(
    path: str,
    *,
    fmt: str = "auto",
    override_window: Optional[int] = None,
    tail_bytes: int = 262_144,
) -> Optional[ContextUsage]:
    """The last model call's context size: Claude Code ``message.usage``
    (input + cache_read + cache_creation + output tokens), Codex
    ``token_count`` events. Retry once at 4x the tail size if none are found.
    Missing, unreadable or malformed transcripts return ``None``.
    """
    if fmt not in ("auto", "claude-code", "codex"):
        return None
    if type(tail_bytes) is not int or tail_bytes <= 0:
        return None
    try:
        with open(path, "rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            for budget in (tail_bytes, tail_bytes * 4):
                start = max(0, size - budget)
                # Inspect the preceding byte so a complete first line survives.
                stream.seek(max(0, start - 1))
                data = stream.read(size - max(0, start - 1))
                if start:
                    previous, data = data[:1], data[1:]
                    if previous != b"\n":
                        data = data.partition(b"\n")[2]
                usage = _scan_usage(data, fmt, override_window)
                if usage is not None:
                    return usage
                if not start:
                    break
    except (OSError, ValueError, TypeError, OverflowError):
        return None
    return None


def should_handoff(usage: Optional[ContextUsage], settings: HandoffSettings) -> bool:
    """On, usage known, and ``usage.fraction >= settings.threshold``."""
    return settings.enabled and usage is not None and usage.fraction >= settings.threshold
