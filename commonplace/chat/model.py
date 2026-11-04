"""Explicitly configured model transport interface."""

from commonplace.chat.types import MAX_OUTPUT_TOKENS, MODEL_TIMEOUT_S, FrontSettings

ANTHROPIC_ORIGIN = "https://api.anthropic.com"


def complete(
    settings: FrontSettings,
    key: str | None,
    system: str,
    turns: list[dict],
    *,
    timeout: float = MODEL_TIMEOUT_S,
    max_tokens: int = MAX_OUTPUT_TOKENS,
) -> str:
    raise NotImplementedError
