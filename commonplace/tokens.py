"""Personal access tokens: ``cmmn_`` plus 32 random bytes, stored as SHA-256."""

from __future__ import annotations

import hashlib
import hmac
import secrets

PREFIX = "cmmn_"
_BODY_BYTES = 32
# token_urlsafe(32) is 43 characters.
_BODY_LEN = 43
_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def new_token() -> str:
    """A fresh token. Show it once; store only :func:`token_hash`."""
    return PREFIX + secrets.token_urlsafe(_BODY_BYTES)


def looks_like_token(value: str) -> bool:
    """Cheap shape check before any database lookup."""
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return False
    body = value[len(PREFIX) :]
    return len(body) == _BODY_LEN and set(body) <= _ALPHABET


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hashes_match(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("ascii"), b.encode("ascii"))


def token_hint(token: str) -> str:
    """What a list shows instead of the token: prefix and the last 4 characters."""
    return f"{PREFIX}…{token[-4:]}"
