"""Parse agent transcripts into verbatim ``ThreadMessage`` lists. Owner: part T01.

Formats: ``claude-code`` (Claude Code session JSONL), ``codex`` (Codex rollout
JSONL), ``messages`` (JSON list of ``{"role", "content"}``, content a string
or a list of content blocks). ``auto`` detects the format.

Lossless: every input line/object becomes at least one message whose ``raw``
is the exact source text; a line holding several content blocks (text plus
tool_use) becomes one message per block, each carrying the same ``raw``.
Unknown line types become ``kind="other"``. Nothing is dropped or rewritten.
"""

from __future__ import annotations

from .models import ThreadMessage

FORMATS = ("auto", "claude-code", "codex", "messages")


def detect_format(text: str) -> str:
    """Return ``claude-code``, ``codex`` or ``messages``. Raises ValueError if none fits."""
    raise NotImplementedError


def parse(text: str, fmt: str = "auto") -> list[ThreadMessage]:
    """Parse a whole transcript. ``seq`` is dense from 0. Raises ValueError on garbage."""
    raise NotImplementedError
