"""Private HTTP helpers matching the shared MVP http_util contract."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, unquote, urlsplit


class BadRequest(ValueError):
    """The helper has already sent the error response."""


def send_text(h, status, text, content_type):
    body = text.encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", content_type)
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(body)


def send_json(h, status, obj):
    send_text(h, status, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")


def read_json(h) -> dict:
    try:
        length = int(h.headers.get("Content-Length", "0"))
        if length < 0 or length > 1024 * 1024:
            raise ValueError("body size")
        obj = json.loads(h.rfile.read(length).decode("utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("body must be an object")
    except (ValueError, UnicodeError, RecursionError):
        h.close_connection = True
        send_json(h, 400, {"error": "invalid JSON body"})
        raise BadRequest("invalid JSON body") from None
    return obj


def require_principal(gateway, h):
    principal = gateway.principal_for(h)
    if principal is None:
        send_json(h, 401, {"error": "unauthorized"})
    return principal


def path_parts(h, prefix) -> list[str]:
    path = urlsplit(h.path).path
    if not path.startswith(prefix):
        return []
    return [unquote(p) for p in path[len(prefix) :].split("/") if p]


def query(h) -> dict[str, str]:
    return {k: v[-1] for k, v in parse_qs(urlsplit(h.path).query, keep_blank_values=True).items()}
