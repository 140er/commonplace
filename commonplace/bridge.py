"""Client connections and newline-delimited stdio transport for the MCP gateway."""

from __future__ import annotations

import http.client
import json
import os
import sys
from urllib.parse import urlsplit

MAX_MESSAGE_BYTES = 8 * 1024 * 1024
HTTP_TIMEOUT_S = 660


_CODING_PRESET = {
    "profile": "standard",
    "read_wings": "*",
    "write_wings": "project_*,shared",
    "trust": "trusted",
}
_CHAT_PRESET = {
    "read_wings": "shared,inbox",
    "write_wing": "inbox",
    "trust": "standard",
}
CLIENT_PRESETS = {
    "claude-code": _CODING_PRESET,
    "codex": _CODING_PRESET,
    "t3": _CODING_PRESET,
    "grok": {**_CHAT_PRESET, "trust": "untrusted"},
    "claude-ai": _CHAT_PRESET,
    "chatgpt": _CHAT_PRESET,
}


def create_login_url(db, email, workspace_ref=None, base_url=None) -> str:
    """Create a one-use sign-in URL for an active workspace member.

    Explicit bases produce direct app links. When installed, the canonical URL
    module supplies short links; until then local installations use the app URL.
    """
    from . import sessions
    from .control_db import ControlError

    share_link = None
    base = base_url
    if base is None:
        try:
            from .urls import APP_URL, share_link

            base = APP_URL
        except ModuleNotFoundError as exc:
            if exc.name != "commonplace.urls":
                raise
            base = (
                os.environ.get("COMMONPLACE_APP_URL")
                or os.environ.get("COMMONPLACE_BASE_URL")
                or "http://127.0.0.1:8765"
            )
    parsed = urlsplit(base)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(c.isspace() or ord(c) < 32 for c in base)
    ):
        raise ValueError("invalid base URL")
    _ = parsed.port
    base = base.rstrip("/")
    user = db.user(email)
    if user["disabled_at"]:
        raise ControlError("user is disabled")
    if workspace_ref:
        workspace = db.workspace(workspace_ref)
    else:
        members = [w for w in db.workspaces() if db.role(w["id"], user["id"])]
        personal = [w for w in members if w["personal_of"] == user["id"]]
        if not members:
            raise ControlError("user is in no workspace")
        workspace = (personal or members)[0]
    if db.role(workspace["id"], user["id"]) is None:
        raise ControlError("user is not a workspace member")
    token = sessions.create_login_link(db, user["email"], workspace["id"])
    path = f"/app/login?t={token}"
    return share_link(db, workspace["id"], path) if share_link else base + path


class BridgeError(Exception):
    """A transport failure with a message safe to show to the client."""


def _endpoint(url):
    try:
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
            raise ValueError
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError
        port = parsed.port
    except ValueError:
        raise BridgeError("Invalid MCP URL") from None
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return parsed.scheme, parsed.hostname, port, path


def _reject_constant(_value):
    raise ValueError("Invalid JSON constant")


def _decode(raw):
    return json.loads(raw, parse_constant=_reject_constant)


def _valid_request(request):
    return (
        isinstance(request, dict)
        and request.get("jsonrpc") == "2.0"
        and isinstance(request.get("method"), str)
        and ("params" not in request or isinstance(request["params"], (dict, list)))
        and ("id" not in request or request["id"] is None or type(request["id"]) in (int, str))
    )


def _error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _write(stdout, response):
    stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
    stdout.flush()


def _diagnostic(stderr, message):
    try:
        stderr.write("commonplace connect: " + message + "\n")
        stderr.flush()
    except (OSError, UnicodeError):
        pass


def _post(endpoint, token, request):
    scheme, host, port, path = endpoint
    connection_type = (
        http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    )
    connection = connection_type(host, port, timeout=HTTP_TIMEOUT_S)
    try:
        connection.request(
            "POST",
            path,
            json.dumps(request, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        response = connection.getresponse()
        if response.status == 202:
            return None
        if response.status != 200:
            response.read(MAX_MESSAGE_BYTES + 1)
            if response.status in (401, 403):
                raise BridgeError(f"HTTP {response.status}: authentication failed")
            raise BridgeError(f"HTTP {response.status} from memory hub")
        raw = response.read(MAX_MESSAGE_BYTES + 1)
    except (OSError, http.client.HTTPException, UnicodeError, ValueError):
        raise BridgeError("Cannot reach memory hub") from None
    finally:
        connection.close()
    try:
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError
        result = _decode(raw)
        if (
            not isinstance(result, dict)
            or result.get("jsonrpc") != "2.0"
            or "id" not in result
            or result["id"] != request.get("id")
            or ("result" in result) == ("error" in result)
        ):
            raise ValueError
    except (ValueError, UnicodeError, RecursionError):
        raise BridgeError("Invalid response from memory hub") from None
    return result


def run_bridge(url, token_env="COMMONPLACE_TOKEN", *, stdin=None, stdout=None, stderr=None) -> int:
    """Forward JSON-RPC lines until EOF; return nonzero on transport failure.

    Malformed input gets a JSON-RPC error and does not end the stream. HTTP
    failures end it: calls get a sanitized error, notifications stay silent.
    Redirects are never followed, so the bearer stays at the configured URL.
    """
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        endpoint = _endpoint(url)
        token = os.environ.get(token_env, "")
        if not token or any(not 33 <= ord(c) <= 126 for c in token):
            raise BridgeError("Missing or invalid token environment variable")
    except BridgeError as exc:
        _diagnostic(stderr, str(exc))
        return 1
    try:
        while True:
            line = stdin.readline(MAX_MESSAGE_BYTES + 1)
            if not line:
                return 0
            if len(line) > MAX_MESSAGE_BYTES:
                while line and not line.endswith("\n"):
                    line = stdin.readline(MAX_MESSAGE_BYTES + 1)
                _write(stdout, _error(None, -32600, "Request too large"))
                continue
            if not line.strip():
                continue
            try:
                request = _decode(line)
            except (ValueError, UnicodeError, RecursionError):
                _write(stdout, _error(None, -32700, "Parse error"))
                continue
            if not _valid_request(request):
                _write(stdout, _error(None, -32600, "Invalid request"))
                continue
            if len(line.encode("utf-8")) > MAX_MESSAGE_BYTES:
                _write(stdout, _error(request.get("id"), -32600, "Request too large"))
                continue
            try:
                response = _post(endpoint, token, request)
            except BridgeError as exc:
                _diagnostic(stderr, str(exc))
                if "id" in request:
                    _write(stdout, _error(request["id"], -32000, str(exc)))
                return 1
            if response is not None and "id" in request:
                _write(stdout, response)
    except (OSError, UnicodeError, RecursionError):
        _diagnostic(stderr, "Stdio connection closed")
        return 1
