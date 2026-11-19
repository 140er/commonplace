"""Check a bundle of files before anything is stored: size, paths, entry page, secrets.

A bundle is a list of ``{path, text}`` or ``{path, base64}`` items. Everything here is a
pure function of the request: it never touches the store or the disk, so a refused bundle
leaves nothing behind. Every refusal is a ``Refused`` with a fixed reason code from
``reasons``; the text of the reason never quotes file content.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json

from commonplace import redact

MAX_BUNDLE_BYTES = 5 * 1024 * 1024
MAX_FILES = 500
MAX_PATH_BYTES = 255
MAX_DEPTH = 10
ENTRY = "index.html"

# Only credential-shaped values that are unmistakable: the broad kinds (``assignment``,
# ``random_token``, ``bearer``) match ordinary build output and would refuse real bundles.
SECRET_KINDS = (
    "private_key",
    "cmmn_oauth",
    "cmmn_token",
    "session_cookie",
    "aws_key",
    "github_token",
    "gitlab_token",
    "anthropic_key",
    "openai_key",
    "slack_token",
    "stripe_key",
    "google_api_key",
    "npm_token",
    "url_credentials",
)


class Refused(Exception):
    """A bundle or request the preview store will not take: ``reason`` is a fixed code."""

    def __init__(self, reason, **detail):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def clean_path(raw) -> str:
    """The stored form of a path, or ``Refused('bad_path')``.

    Relative, forward slashes only, no empty, dot or hidden segments, no control characters,
    at most ``MAX_DEPTH`` segments and ``MAX_PATH_BYTES`` bytes."""
    if not isinstance(raw, str) or not raw:
        raise Refused("bad_path", file=_shown(raw))
    try:
        size = len(raw.encode("utf-8"))
    except UnicodeEncodeError:
        raise Refused("bad_path", file=_shown(raw)) from None
    parts = raw.split("/")
    bad = (
        size > MAX_PATH_BYTES
        or len(parts) > MAX_DEPTH
        or raw.startswith("/")
        or "\\" in raw
        or any(ord(c) < 32 or ord(c) == 127 for c in raw)
        or any(p == "" or p.startswith(".") for p in parts)
    )
    if bad:
        raise Refused("bad_path", file=_shown(raw))
    return raw


def _shown(raw) -> str:
    text = raw if isinstance(raw, str) else "?"
    return "".join(c if 32 <= ord(c) != 127 else "?" for c in text)[:80]


def _body(item) -> bytes:
    """The bytes of one item; ``ValueError`` when it is not exactly one of text or base64."""
    if not isinstance(item, dict) or set(item) - {"path", "text", "base64"}:
        raise ValueError("each file is {path, text} or {path, base64}")
    if ("text" in item) == ("base64" in item):
        raise ValueError("each file needs exactly one of text or base64")
    if "text" in item:
        if not isinstance(item["text"], str):
            raise ValueError("text must be a string")
        try:
            return item["text"].encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("text must be valid UTF-8") from None
    value = item["base64"]
    if not isinstance(value, str):
        raise ValueError("base64 must be a string")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("base64 is not valid") from None


def parse(files) -> list:
    """Validate ``files`` and return ``[(path, bytes)]`` sorted by path.

    ``ValueError`` for a malformed request (the caller answers 400); ``Refused`` for a bundle
    that breaks a limit or rule."""
    if not isinstance(files, list):
        raise ValueError("files must be a list")
    if not files:
        raise Refused("no_index_html")
    if len(files) > MAX_FILES:
        raise Refused("too_many_files", limit=MAX_FILES)
    out, seen, total = [], set(), 0
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("each file is {path, text} or {path, base64}")
        path = clean_path(item.get("path"))
        # A base64 body decodes to at most three quarters of its length: refuse before decoding.
        encoded = item.get("base64")
        if isinstance(encoded, str) and len(encoded) // 4 * 3 > MAX_BUNDLE_BYTES - total + 3:
            raise Refused("too_large", limit=MAX_BUNDLE_BYTES)
        data = _body(item)
        total += len(data)
        if total > MAX_BUNDLE_BYTES:
            raise Refused("too_large", limit=MAX_BUNDLE_BYTES)
        if path in seen:
            raise Refused("bad_path", file=_shown(path))
        seen.add(path)
        out.append((path, data))
    folders = {"/".join(p.split("/")[:i]) for p in seen for i in range(1, len(p.split("/")))}
    clash = sorted(seen & folders)
    if clash:
        raise Refused("bad_path", file=_shown(clash[0]))
    if ENTRY not in seen:
        raise Refused("no_index_html")
    for path, data in sorted(out):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        found = redact.find_kinds(text, SECRET_KINDS)
        if found:
            raise Refused("contains_secret_like_text", file=_shown(path), kind=found[0])
    return sorted(out)


def digest(items) -> str:
    """One digest for the whole bundle: paths and the digest of each file, in path order."""
    lines = [[path, hashlib.sha256(data).hexdigest()] for path, data in items]
    return hashlib.sha256(json.dumps(lines, separators=(",", ":")).encode()).hexdigest()
