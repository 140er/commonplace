"""Small, credential-scoped client for canonical Commonplace tools.

Requests go only to the configured endpoint. Failures never include response
bodies, request arguments, credentials or underlying transport exception text.
"""

from __future__ import annotations

import http.client
import json
import math
import os
import re
from itertools import count
from urllib.parse import urlsplit

DEFAULT_URL = "https://app.cmmn.dev"
MAX_MESSAGE_BYTES = 8 * 1024 * 1024


class ClientError(Exception):
    """A client failure whose message is safe to display."""


class _RulesChanged(ClientError):
    """An explicit pre-write refusal; the same write may be tried once."""


def _endpoint(url):
    try:
        if (
            not isinstance(url, str)
            or not url
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)
            or "\\" in url
            or "#" in url
        ):
            raise ValueError
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError
        port = parsed.port
    except ValueError:
        raise ClientError("Invalid Commonplace URL") from None
    path = parsed.path if parsed.path not in {"", "/"} else "/mcp"
    if parsed.query:
        path += "?" + parsed.query
    return parsed.scheme, parsed.hostname, port, path


def _reject_constant(_value):
    raise ValueError


def _decode(raw):
    return json.loads(raw, parse_constant=_reject_constant)


def _post(endpoint, token, timeout, request):
    try:
        body = json.dumps(request, ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError, OverflowError):
        raise ClientError("Invalid Commonplace tool arguments") from None
    if len(body) > MAX_MESSAGE_BYTES:
        raise ClientError("Commonplace request is too large")
    scheme, host, port, path = endpoint
    connection_type = (
        http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    )
    try:
        connection = connection_type(host, port, timeout=timeout)
        try:
            connection.request(
                "POST",
                path,
                body,
                headers={
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise ClientError("Commonplace redirects are refused")
            if response.status in {401, 403}:
                raise ClientError(f"Commonplace authentication failed (HTTP {response.status})")
            if response.status != 200:
                raise ClientError(f"Commonplace request failed (HTTP {response.status})")
            raw = response.read(MAX_MESSAGE_BYTES + 1)
        finally:
            connection.close()
    except (OSError, http.client.HTTPException, ValueError, UnicodeError, OverflowError):
        raise ClientError("Cannot reach Commonplace") from None
    try:
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError
        reply = _decode(raw)
        if (
            not isinstance(reply, dict)
            or reply.get("jsonrpc") != "2.0"
            or type(reply.get("id")) is not type(request["id"])
            or reply.get("id") != request["id"]
            or ("result" in reply) == ("error" in reply)
        ):
            raise ValueError
    except (ValueError, UnicodeError, RecursionError):
        raise ClientError("Invalid response from Commonplace") from None
    return reply


def _tool_text(reply):
    if "error" in reply:
        raise ClientError("Commonplace RPC failed")
    result = reply["result"]
    if not isinstance(result, dict):
        raise ClientError("Invalid tool response from Commonplace")
    content = result.get("content")
    if not isinstance(content, list) or any(not isinstance(block, dict) for block in content):
        raise ClientError("Invalid tool response from Commonplace")
    lines = []
    for block in content:
        if block.get("type") == "text":
            if not isinstance(block.get("text"), str):
                raise ClientError("Invalid tool response from Commonplace")
            lines.append(block["text"])
    if result.get("isError") or "error" in result:
        # Only the explicit gateway refusal is safe to retry. A timeout, an
        # ordinary error, or a mention of rules_changed in stored data is not.
        if (
            result.get("isError") is True
            and "error" not in result
            and len(content) == len(lines) == 1
            and re.match(r"\Ano: rules_changed(?:\Z|[:\s])", lines[0])
        ):
            raise _RulesChanged("Commonplace rules changed; write refused")
        raise ClientError("Commonplace tool failed")
    return "\n".join(lines)


def _arguments(name, arguments):
    if not isinstance(name, str) or not name or not isinstance(arguments, dict):
        raise ClientError("Invalid Commonplace tool arguments")


class Client:
    """Use one endpoint and one named environment credential per instance.

    A bare origin uses /mcp; an explicit endpoint path is preserved. Writes
    fetch effective rules each time and never retry an ambiguous failure.
    """

    def __init__(self, url=None, token_env="COMMONPLACE_TOKEN", timeout=3.0):
        self._endpoint = _endpoint(
            url if url is not None else os.environ.get("COMMONPLACE_URL") or DEFAULT_URL
        )
        if not isinstance(token_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token_env):
            raise ClientError("Invalid token environment variable name")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ClientError("Invalid Commonplace timeout")
        token = os.environ.get(token_env, "")
        if not token or any(not 33 <= ord(c) <= 126 for c in token):
            raise ClientError("Missing or invalid token environment variable")
        self._token = token
        self._timeout = timeout
        self._ids = count(1)

    def call_tool(self, name, arguments=None) -> str:
        """Call an MCP tool, combining its text blocks without rewriting them."""
        arguments = {} if arguments is None else arguments
        _arguments(name, arguments)
        request = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        return _tool_text(_post(self._endpoint, self._token, self._timeout, request))

    def fetch_context(self, query=None, board=None, repo=None) -> str:
        arguments = {
            key: value
            for key, value in {"query": query, "board": board, "repo": repo}.items()
            if value is not None
        }
        return self.call_tool("session_start", arguments)

    def _write_arguments(self, arguments):
        rules = self.call_tool("rules_get", {"effective": True, "format": "md"})
        # The server emits the marker as the final line, outside quoted rules.
        marker = rules.rstrip("\n").rsplit("\n", 1)[-1]
        match = re.fullmatch(r"rules_seq (off|[0-9]+)", marker)
        if match is None:
            raise ClientError("Effective Commonplace rules are unavailable or incomplete")
        prepared = dict(arguments)
        prepared.pop("rules_seq", None)
        if match[1] != "off":
            try:
                prepared["rules_seq"] = int(match[1])
            except ValueError:
                raise ClientError("Invalid Commonplace rules sequence") from None
        return prepared

    def write_tool(self, name, arguments) -> str:
        """Read effective rules, then write; refresh once on a proven refusal."""
        _arguments(name, arguments)
        prepared = self._write_arguments(arguments)
        try:
            return self.call_tool(name, prepared)
        except _RulesChanged:
            return self.call_tool(name, self._write_arguments(arguments))

    def remember(self, text, **optional) -> str:
        """Save complete verbatim text through the caller's permitted route."""
        if not isinstance(text, str):
            raise ClientError("Memory text must be a string")
        return self.write_tool("memory_save", {"text": text, **optional})

    def publish_rule(
        self, text, kind="instruction", agent="self", scope="global", scope_ref=None, key=None
    ) -> str:
        """Publish one verbatim rule; shared proposals still need owner approval."""
        if not isinstance(text, str) or kind not in {"permission", "limit", "instruction", "style"}:
            raise ClientError("Invalid Commonplace rule")
        if agent not in {"self", "*"}:
            raise ClientError("Invalid Commonplace rule target")
        if scope not in {"global", "board", "repo"} or (
            scope == "global" and scope_ref is not None
        ):
            raise ClientError("Invalid Commonplace rule scope")
        rule = {"text": text, "kind": kind, "scope": scope}
        if scope != "global":
            if not isinstance(scope_ref, str) or not scope_ref.strip():
                raise ClientError("A scoped Commonplace rule requires a scope reference")
            rule[scope] = scope_ref
        if key is not None:
            rule["key"] = key
        return self.write_tool("rules_publish", {"rules": [rule], "agent": agent})

    def get_memory_text(self, id) -> str:
        """Decode one complete quoted memory for exact readback verification."""
        reply = self.call_tool("memory_get", {"id": id})
        # Split only on LF: JSON strings may contain literal Unicode line
        # separators, which str.splitlines() would incorrectly split.
        lines = reply.rstrip("\n").split("\n")
        quoted = [line for line in lines if line.startswith("quoted data: ")]
        try:
            if len(quoted) != 1 or quoted[0] != lines[-1]:
                raise ValueError
            text = _decode(quoted[0].removeprefix("quoted data: "))
            if not isinstance(text, str):
                raise ValueError
        except (ValueError, UnicodeError, RecursionError):
            raise ClientError("Invalid complete memory readback from Commonplace") from None
        return text
