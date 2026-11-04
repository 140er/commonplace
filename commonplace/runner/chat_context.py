"""Bounded, redacted context for the poster's own runner."""

MAX_CONTEXT_JSON_BYTES = 49_152


def build(gateway, poster, board: str, message_id: int, context: str) -> tuple[str, str]:
    raise NotImplementedError
