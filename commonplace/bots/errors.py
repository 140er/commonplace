"""Errors and small helpers shared by the bot store and its sharing layer."""

import json
import re
from datetime import datetime, timezone

TEXT_MAX = 8000
PAGE_DEFAULT, PAGE_MAX = 50, 100
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")


class Missing(LookupError):
    """No such bot for this person (unknown and foreign ids look the same)."""


class Invalid(ValueError):
    """A value outside its bound. The message is fixed text, never user content."""


class Unsupported(Invalid):
    """A well-formed value the model does not accept yet (a wing grant before a wing tool exists)."""


class Limit(Exception):
    """A bound is full: live bots per person, shared bots per owner, shares per bot."""


class Conflict(Exception):
    """A request id reused with other text, a second reply, or a stale access version."""


class SharingOff(Exception):
    """The workspace switch for sharing bots is off."""


def stamp(now=None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def bounded(value, name, low, high):
    """``value`` unchanged when its raw length is within ``high`` (and not blank when ``low``)."""
    if not isinstance(value, str) or len(value) > high or (low and not value.strip()):
        raise Invalid(f"{name} must be {low}-{high} characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise Invalid(f"{name} must be valid text") from None
    return value


def message(row) -> dict:
    return {
        "id": row["id"],
        "role": row["role"],
        "text": row["text"],
        "blocks": json.loads(row["blocks"]),
        "at": row["created_at"],
        "state": row["state"],
        "reply_to": row["reply_to"],
        "model": row["model"],
        "request_id": row["request_id"],
    }
