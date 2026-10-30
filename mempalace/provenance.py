"""Verified authorship for writes that arrive through a trusted gateway.

A gateway in front of ``mempalace serve`` authenticates each client itself and
passes the result to the worker in one header. The worker trusts that header
only when it was started with ``MEMPALACE_MCP_TRUSTED_IDENTITY=1`` and a bearer
token, so only the process that holds the token (the gateway) can set it.

While a request runs, :func:`current` returns the verified identity and every
write stamps it into the stored record with :func:`stamp_metadata`. The
client's own ``added_by`` is kept verbatim as ``claimed_by``; ``added_by``
becomes the verified connection name. Without a verified identity (stdio, a
plain ``mempalace serve``) nothing changes.
"""

from __future__ import annotations

import base64
import contextlib
import contextvars
import json
from typing import Iterator, Optional

from .timeutil import utc_now_iso

IDENTITY_HEADER = "X-Mempalace-Verified-Identity"
TRUSTED_IDENTITY_ENV = "MEMPALACE_MCP_TRUSTED_IDENTITY"

# Identity key -> stored metadata field.
FIELDS = {
    "user": "hub_user",
    "connection": "hub_connection",
    "client": "hub_client",
    "auth": "hub_auth",
    "trust": "hub_trust",
    "workspace": "hub_workspace",
}
STAMP_FIELDS = tuple(FIELDS.values()) + ("hub_written_at",)
# Who last changed a record. The original author fields above never change.
UPDATE_FIELDS = ("hub_updated_by", "hub_updated_user", "hub_updated_trust", "hub_updated_at")

_MAX_FIELD_LEN = 200
_current: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "mempalace_verified_identity", default=None
)


def encode_identity(identity: dict) -> str:
    """Header value for ``identity``: base64url JSON, so any name survives HTTP."""
    raw = json.dumps(parse_identity(identity), separators=(",", ":"), ensure_ascii=False)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_identity(value: str) -> dict:
    """Parse a header value from :func:`encode_identity`. Raises ``ValueError``."""
    try:
        raw = base64.urlsafe_b64decode(value.strip().encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"unreadable identity header: {exc}") from None
    return parse_identity(data)


def parse_identity(data) -> dict:
    """Validate an identity mapping: every key present, short non-empty strings."""
    if not isinstance(data, dict):
        raise ValueError("identity must be an object")
    out = {}
    for key in FIELDS:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > _MAX_FIELD_LEN:
            raise ValueError(f"identity field {key!r} must be a non-empty string")
        out[key] = value
    if "person" in data:
        if not isinstance(data["person"], bool):
            raise ValueError("identity person must be a boolean")
        out["person"] = data["person"]
    return out


def current() -> Optional[dict]:
    """The verified identity of the request being served, or ``None``."""
    return _current.get()


@contextlib.contextmanager
def verified(identity: Optional[dict]) -> Iterator[None]:
    """Run the enclosed request as ``identity`` (``None`` clears it)."""
    token = _current.set(parse_identity(identity) if identity is not None else None)
    try:
        yield
    finally:
        _current.reset(token)


def stamp() -> Optional[dict]:
    """The ``hub_*`` fields for a write made now, or ``None`` without an identity."""
    identity = current()
    if identity is None:
        return None
    fields = {field: identity[key] for key, field in FIELDS.items()}
    fields["hub_written_at"] = utc_now_iso()
    return fields


def stamp_metadata(meta: dict) -> dict:
    """Stamp ``meta`` in place with the verified author, if there is one."""
    fields = stamp()
    if fields is None:
        return meta
    claimed = meta.get("added_by")
    if isinstance(claimed, str) and claimed:
        meta["claimed_by"] = claimed
    meta["added_by"] = fields["hub_connection"]
    meta.update(fields)
    return meta


def stamp_update(meta: dict) -> dict:
    """Record the verified identity that changed ``meta``, if there is one."""
    fields = stamp()
    if fields is None:
        return meta
    meta["hub_updated_by"] = fields["hub_connection"]
    meta["hub_updated_user"] = fields["hub_user"]
    meta["hub_updated_trust"] = fields["hub_trust"]
    meta["hub_updated_at"] = fields["hub_written_at"]
    return meta


def stamp_json() -> Optional[str]:
    """The stamp as JSON for stores without per-record metadata (the KG)."""
    fields = stamp()
    return json.dumps(fields, sort_keys=True, ensure_ascii=False) if fields else None


def stamp_from_json(value) -> dict:
    """The ``hub_*`` fields stored by :func:`stamp_json`; ``{}`` when absent."""
    if not value:
        return {}
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in STAMP_FIELDS and isinstance(v, str)}
