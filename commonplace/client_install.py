"""Install user-level native connections and hooks without changing trust policy.

Native formats: https://learn.chatgpt.com/docs/hooks and
https://code.claude.com/docs/en/hooks. Generic MCP clients use server initialize
instructions and the onboarding pointers, rather than a made-up hook format.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import re
import shlex
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from mempalace.fsio import atomic_write_text

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

DEFAULT_URL = "https://app.cmmn.dev"
MAX_CONFIG_BYTES = 2 * 1024 * 1024
_WINDOWS_COMMAND = "powershell.exe -NoProfile -NonInteractive -EncodedCommand "
_EVENTS = {"SessionStart": "session-start", "PostToolUse": "check-local", "Stop": "check-local"}


class _Refused(ValueError):
    """A reason safe to report without revealing configuration contents."""


def _endpoint(url):
    try:
        if (
            not isinstance(url, str)
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)
            or any(c in url for c in ("\\", "#", "${"))
        ):
            raise ValueError
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
        ):
            raise ValueError
        _ = parsed.port
    except ValueError:
        raise _Refused(
            "Invalid Commonplace URL; use an HTTP endpoint without credentials or query"
        ) from None
    path = parsed.path if parsed.path not in {"", "/"} else "/mcp"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _safe_path(path):
    if ".." in path.parts:
        raise _Refused("Parent traversal is refused")
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
            raise _Refused("Symlink or junction paths are refused")


def _read(path):
    _safe_path(path)
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise _Refused("Configuration must be a regular file")
        raw = stream.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise _Refused("Configuration exceeds the size limit")
    return raw


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _Refused("Duplicate JSON keys; existing configuration was preserved")
        result[key] = value
    return result


def _json(text):
    value = json.loads(text or "{}", object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise _Refused("Configuration must contain a JSON object")
    return value


def _table(parent, key):
    value = parent.setdefault(key, {})
    if not isinstance(value, dict):
        raise _Refused("Existing configuration has an incompatible table")
    return value


def _windows_command(arguments):
    # Encoded PowerShell avoids a second shell expanding $, %, or backticks
    # in executable/project paths. Each actual argument is a literal string.
    script = "& " + " ".join("'" + value.replace("'", "''") + "'" for value in arguments)
    return _WINDOWS_COMMAND + base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _handler(client, event, home, project, url, token_env):
    executable = sys.executable
    if (
        not executable
        or not Path(executable).is_absolute()
        or any(c in executable for c in "\r\n\x00")
    ):
        raise _Refused("Cannot identify the Python executable")
    arguments = [
        executable,
        "-I",
        "-m",
        "commonplace.client_hooks",
        "--event",
        event,
        "--client",
        client,
        "--home",
        str(home),
        "--url",
        url,
        "--token-env",
        token_env,
    ]
    arguments += ["--no-project"] if project is None else ["--project", str(project)]
    handler = {"type": "command", "timeout": 2, "statusMessage": "Commonplace: " + event}
    if client == "claude":
        # Native exec form is portable and never shell-parses paths or URLs.
        handler.update(command=arguments[0], args=arguments[1:])
    else:
        windows = _windows_command(arguments)
        handler["command"] = windows if os.name == "nt" else shlex.join(arguments)
        if os.name == "nt":
            handler["commandWindows"] = windows
    return handler


def _ours(handler, event):
    if handler.get("type") != "command" or handler.get("statusMessage") != "Commonplace: " + event:
        return False
    command = handler.get("command", "")
    if not isinstance(command, str):
        return False
    if command.startswith(_WINDOWS_COMMAND):
        try:
            command = base64.b64decode(command[len(_WINDOWS_COMMAND) :], validate=True).decode(
                "utf-16-le"
            )
        except (ValueError, UnicodeError):
            return False
    arguments = handler.get("args", [])
    return (isinstance(arguments, list) and "commonplace.client_hooks" in arguments) or bool(
        re.search(r"(?:^|[\s'\"])commonplace\.client_hooks(?:$|[\s'\"])", command)
    )


def _hooks(text, client, **options):
    config = _json(text)
    hooks = _table(config, "hooks")
    for native_event, event in _EVENTS.items():
        groups = hooks.setdefault(native_event, [])
        if not isinstance(groups, list):
            raise _Refused("Existing hooks have an incompatible format")
        desired = _handler(client, event, **options)
        found = False
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise _Refused("Existing hooks have an incompatible format")
            for index, handler in enumerate(group["hooks"]):
                if not isinstance(handler, dict):
                    raise _Refused("Existing hooks have an incompatible format")
                if _ours(handler, event):
                    group["hooks"][index] = desired
                    found = True
        if not found:
            groups.append({"hooks": [desired]})
    return json.dumps(config, indent=2, ensure_ascii=True, allow_nan=False) + "\n"


def _claude_connection(text, url, token_env):
    config = _json(text)
    server = _table(_table(config, "mcpServers"), "commonplace")
    if server.get("type", "http") not in {"http", "streamable-http"} or "command" in server:
        raise _Refused("Existing Commonplace connection uses a different transport")
    headers = _table(server, "headers")
    # Header names are case insensitive: do not leave an older bearer alongside
    # the environment reference, or silently keep a helper overriding it.
    if "headersHelper" in server:
        raise _Refused("Existing Commonplace connection uses a header helper")
    for name in list(headers):
        if name.lower() == "authorization":
            del headers[name]
    headers["Authorization"] = "Bearer ${" + token_env + "}"
    server.update(type="http", url=url)
    return json.dumps(config, indent=2, ensure_ascii=True, allow_nan=False) + "\n"


def _codex_connection(text, url, token_env):
    before = tomllib.loads(text)
    expected = copy.deepcopy(before)
    server = _table(_table(expected, "mcp_servers"), "commonplace")
    if "command" in server or any(
        isinstance(server.get(key), dict)
        and any(name.lower() == "authorization" for name in server[key])
        for key in ("http_headers", "env_http_headers")
    ):
        raise _Refused(
            "Existing Commonplace transport or authentication conflicts with HTTP bearer configuration"
        )
    updates = {"url": url, "bearer_token_env_var": token_env}
    server.update(updates)
    if before == expected:
        return text
    # Edit just the connection's two scalar fields. Verify the entire decoded
    # document against the expected result, preserving all other TOML values,
    # comments, tables, and security settings. Unusual syntax is left untouched.
    heading = re.search(
        r"(?m)^[ \t]*\[[ \t]*(?:mcp_servers|\"mcp_servers\"|'mcp_servers')[ \t]*\.[ \t]*(?:commonplace|\"commonplace\"|'commonplace')[ \t]*\][ \t]*(?:#[^\n]*)?$",
        text,
    )
    if heading:
        start = heading.end()
        boundary = re.search(r"(?m)^[ \t]*\[", text[start:])
        end = start + boundary.start() if boundary else len(text)
        section = text[start:end]
        for key, value in updates.items():
            pattern = rf"(?m)^[ \t]*(?:{key}|\"{key}\"|'{key}')[ \t]*=[^\n]*"
            line = key + " = " + json.dumps(value)
            if re.search(pattern, section):
                section = re.sub(pattern, lambda _match, line=line: line, section)
            else:
                section = "\n" + line + section
        updated = text[:start] + section.rstrip("\n") + "\n\n" + text[end:]
    else:
        updated = (
            text.rstrip("\n")
            + "\n\n[mcp_servers.commonplace]\n"
            + "".join(key + " = " + json.dumps(value) + "\n" for key, value in updates.items())
        )
    if tomllib.loads(updated) != expected:
        raise _Refused(
            "Cannot safely update this TOML layout; existing configuration was preserved"
        )
    return updated


def _install(path, transform, *, dry_run):
    record = {"path": str(path)}
    try:
        raw = _read(path)
        text = raw.decode("utf-8") if raw is not None else ""
        updated = transform(text)
        if updated == text:
            return {**record, "status": "unchanged"}
        # JSON formatting alone is not a reason to rewrite a user's file.
        if path.suffix == ".json" and raw is not None and _json(updated) == _json(text):
            return {**record, "status": "unchanged"}
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _safe_path(path)
            if _read(path) != raw:
                raise _Refused("Configuration changed during installation; retry installation")
            atomic_write_text(path, updated, follow_symlinks=False)
        return {**record, "status": "created" if raw is None else "updated"}
    except _Refused as exc:
        return {**record, "status": "refused", "reason": str(exc)}
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        return {
            **record,
            "status": "refused",
            "reason": "Cannot safely read or update configuration; original preserved",
        }


def install_client_config(
    *,
    home: Path,
    project: Path | None = None,
    url: str | None = None,
    token_env="COMMONPLACE_TOKEN",
    dry_run=False,
) -> dict:
    """Configure native clients without reading a token or granting hook trust."""
    warnings = [
        "Codex requires native review/trust of new hooks via /hooks. MCP initialize supplies startup guidance before hook trust."
    ]
    try:
        endpoint = _endpoint(
            url if url is not None else os.environ.get("COMMONPLACE_URL") or DEFAULT_URL
        )
        if not isinstance(token_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token_env):
            raise _Refused("Invalid token environment variable name")
        home = Path(home).expanduser().absolute()
        project = Path(project).expanduser().absolute() if project is not None else None
        options = dict(home=home, project=project, url=endpoint, token_env=token_env)
        files = []
        for client, relative in (
            ("claude", ".claude/settings.json"),
            ("codex", ".codex/hooks.json"),
        ):
            files.append(
                _install(
                    home / relative,
                    lambda text, client=client: _hooks(text, client, **options),
                    dry_run=dry_run,
                )
            )
        for relative, transform in (
            (".claude.json", _claude_connection),
            (".codex/config.toml", _codex_connection),
        ):
            files.append(
                _install(
                    home / relative,
                    lambda text, transform=transform: transform(text, endpoint, token_env),
                    dry_run=dry_run,
                )
            )
    except _Refused as exc:
        return {"files": [], "warnings": [str(exc)], "dry_run": dry_run}
    return {"files": files, "warnings": warnings, "dry_run": dry_run}
