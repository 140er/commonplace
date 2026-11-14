"""Learning memory, shared rules: the secret guard, normalisation and the keyed quote hash.

``guard`` is the one seam every learned text passes before it is written. Today its body combines
the redactor and the privacy scan; the fail-closed secret scanner (card #457) replaces the body
without touching a caller. Whatever goes wrong inside it is a refusal, never a pass.
"""

import hashlib
import hmac
import os
import re
import secrets
from pathlib import Path
from typing import NamedTuple

from commonplace import privacy, redact

KEY_FILE = "bots-learn.key"
_MARKER = re.compile(r"\[(?:redacted|omitted):[a-z_]+\]")
_SPACE = re.compile(r"\s+")


class Allow(NamedTuple):
    allowed: bool = True


class Refuse(NamedTuple):
    kind: str  # secret | injection | sensitive | marker | input | error
    allowed: bool = False


def guard(text) -> Allow | Refuse:
    """Allow ``text`` to be stored as a learned item, or say in one word why not."""
    try:
        if not isinstance(text, str) or not text.strip():
            return Refuse("input")
        if _MARKER.search(text):
            return Refuse("marker")
        if redact.redact_value(text)[0] != text:
            return Refuse("secret")
        flags = privacy.scan(text)
        if privacy.has_secret(flags):
            return Refuse("secret")
        if any(flag.startswith("injection:") for flag in flags):
            return Refuse("injection")
        if privacy.is_health(flags):
            return Refuse("sensitive")
    except Exception:
        return Refuse("error")
    return Allow()


def normalise(text: str) -> str:
    """Whitespace- and case-insensitive form of ``text`` (what the quote hash is taken over)."""
    return _SPACE.sub(" ", text).strip().casefold()


def topic_key(topic) -> str | None:
    """A dedupe key of 1 to 80 characters, or None."""
    if not isinstance(topic, str):
        return None
    key = normalise(topic)
    return key if 1 <= len(key) <= 80 else None


def workspace_key(directory) -> bytes:
    """The per-workspace hash key. It lives in its own file beside the workspace, not in a
    database, so a copy of ``bots.sqlite3`` alone cannot be used to test guesses against it."""
    path = Path(directory) / KEY_FILE
    try:
        return _read_key(path)
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f"{KEY_FILE}.{secrets.token_hex(8)}")
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(secrets.token_bytes(32))
        try:
            os.link(pending, path)  # atomic and exclusive: the first writer wins
        except FileExistsError:
            pass
    finally:
        os.unlink(pending)
    return _read_key(path)


def _read_key(path: Path) -> bytes:
    key = path.read_bytes()
    if len(key) != 32:
        raise ValueError("learning key is damaged")
    return key


def quote_hash(key: bytes, text: str) -> str:
    return hmac.new(key, normalise(text).encode("utf-8"), hashlib.sha256).hexdigest()
