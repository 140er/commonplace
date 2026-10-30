"""Small HTTP helpers shared by Commonplace's REST routes."""

from __future__ import annotations

import json
import traceback
from urllib.parse import parse_qsl, unquote, urlsplit

MAX_JSON_BYTES = 1024 * 1024


def log_failure(logger, message, exc):
    """Keep diagnostic stack locations without exception text or local values."""
    frames = [(f.filename, f.lineno, f.name) for f in traceback.extract_tb(exc.__traceback__)]
    logger.error("%s (%s), stack=%s", message, type(exc).__name__, frames)


def _invalid_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def read_json(h) -> dict:
    """Read an object, replying 400 on malformed input before raising ValueError.

    Route handlers should catch ValueError and return; the response is already sent.
    Closing a rejected request prevents unread bytes becoming another HTTP request.
    """
    try:
        if h.headers.get("Transfer-Encoding"):
            raise ValueError("Transfer encoding is not supported")
        length = int(h.headers.get("Content-Length", "0"))
        if not 0 < length <= MAX_JSON_BYTES:
            raise ValueError("JSON body must be between 1 byte and 1 MiB")
        body = h.rfile.read(length)
        if len(body) != length:
            raise ValueError("Incomplete JSON body")
        result = json.loads(body.decode("utf-8"), parse_constant=_invalid_constant)
        if not isinstance(result, dict):
            raise ValueError("JSON body must be an object")
    except (ValueError, UnicodeError, RecursionError) as exc:
        h.close_connection = True
        send_json(h, 400, {"error": "Invalid JSON body"})
        raise ValueError("Invalid JSON body") from exc
    return result


def _send(h, status, body, content_type):
    h.send_response(status)
    h.send_header("Content-Type", content_type)
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.send_header("X-Content-Type-Options", "nosniff")
    h.end_headers()
    h.wfile.write(body)


def send_json(h, status, obj):
    _send(h, status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json")


def send_text(h, status, text, content_type="text/plain; charset=utf-8"):
    _send(h, status, text.encode("utf-8"), content_type)


def require_principal(gateway, h):
    principal = gateway.principal_for(h)
    if principal is None:
        send_json(h, 401, {"error": "Sign in"})
    return principal


def path_parts(h, prefix):
    path = urlsplit(h.path).path
    if not path.startswith(prefix):
        return []
    return [unquote(part) for part in path[len(prefix) :].split("/") if part]


def query(h) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(h.path).query, keep_blank_values=True))
