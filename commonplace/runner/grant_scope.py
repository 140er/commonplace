"""Versioned grant scope records: validation, canonical bytes and the local producer.

A record is what a runner reports about one machine, never proof of what T3 permits:
null means unknown, [] means known empty. Rejected values never reach an error text.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

VERSION = 1
MODES = ("approval-required", "auto-accept-edits", "auto", "full-access")
MAX_RECORD_BYTES = 16 * 1024
MAX_PROJECTS = 32
MAX_SERVERS = 32
MAX_TOOLS = 128
SHA256 = re.compile(r"[0-9a-f]{64}")

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_LABEL = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_TOOL = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}")
# Prefix only: interior text such as task-sk-1 stays a valid identifier.
_MARKER = re.compile(r"\s*(?:bearer\s|sk-|ghp_|github_pat_|xox|cp_pat_|cmmn_)", re.IGNORECASE)
_STRATEGIES = ("root", "existing_worktree")
_TOP_KEYS = {"version", "provider_instance_id", "t3", "runner"}
_T3_KEYS = {"max_runtime_mode", "workspace_strategies", "projects", "mcp", "mode_inheritance"}
_RUNNER_KEYS = {"workspace_strategy", "projects", "topic_models"}
_PROJECT_KEYS = {"label", "project_id", "runtime_ceiling"}
_SERVER_KEYS = {"server_id", "tools"}


class ScopeInvalid(ValueError):
    """Carries only the fixed code, so a rejected value cannot be echoed."""

    def __init__(self):
        super().__init__("scope_invalid")


def _require(condition):
    if not condition:
        raise ScopeInvalid


def _unique_pairs(pairs):
    keys = [key for key, _ in pairs]
    _require(len(set(keys)) == len(keys))
    return dict(pairs)


def _no_constant(_value):
    raise ScopeInvalid


def loads(text):
    """Parse JSON, refusing duplicate keys at every depth and nonfinite constants."""
    try:
        return json.loads(text, object_pairs_hook=_unique_pairs, parse_constant=_no_constant)
    except (ValueError, RecursionError) as exc:
        raise ScopeInvalid from exc


def _text(value, pattern):
    _require(isinstance(value, str) and pattern.fullmatch(value) and not _MARKER.match(value))
    return value


def _object(value, keys):
    _require(isinstance(value, dict) and value.keys() == keys)
    return value


def _bounded(value, limit):
    _require(isinstance(value, list) and len(value) <= limit)
    return value


def _distinct(values):
    _require(len(set(values)) == len(values))
    return values


def _ids(value):
    return sorted(_distinct([_text(item, _ID) for item in _bounded(value, MAX_PROJECTS)]))


def _strategies(value):
    _require(isinstance(value, list) and all(item in _STRATEGIES for item in value))
    return sorted(_distinct(value))


def _by_bytes(objects):
    return sorted(objects, key=canonical_bytes)


def _mcp(value):
    servers, total = [], 0
    for server in _bounded(value, MAX_SERVERS):
        _object(server, _SERVER_KEYS)
        tools = server["tools"]
        if tools is not None:
            tools = sorted(_distinct([_text(item, _TOOL) for item in _bounded(tools, MAX_TOOLS)]))
            total += len(tools)
        servers.append({"server_id": _text(server["server_id"], _ID), "tools": tools})
    _require(total <= MAX_TOOLS)
    _distinct([server["server_id"] for server in servers])
    return _by_bytes(servers)


def _runner_projects(value):
    projects = []
    for project in _bounded(value, MAX_PROJECTS):
        _object(project, _PROJECT_KEYS)
        _require(project["runtime_ceiling"] in MODES)
        projects.append(
            {
                "label": _text(project["label"], _LABEL),
                "project_id": _text(project["project_id"], _ID),
                "runtime_ceiling": project["runtime_ceiling"],
            }
        )
    _distinct([project["label"] for project in projects])
    _distinct([project["project_id"] for project in projects])
    return _by_bytes(projects)


def _optional(value, read):
    return None if value is None else read(value)


def canonical_bytes(record):
    return json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def digest(record):
    return hashlib.sha256(canonical_bytes(record)).hexdigest()


def validate(record):
    """Return the normalized record or raise ``ScopeInvalid``; never truncates."""
    _object(record, _TOP_KEYS)
    _require(type(record["version"]) is int and record["version"] == VERSION)
    t3 = _object(record["t3"], _T3_KEYS)
    runner = _object(record["runner"], _RUNNER_KEYS)
    _require(t3["mode_inheritance"] == "unknown")
    _require(runner["workspace_strategy"] == "root" and runner["topic_models"] is None)
    _require(t3["max_runtime_mode"] is None or t3["max_runtime_mode"] in MODES)
    normalized = {
        "version": VERSION,
        "provider_instance_id": _optional(
            record["provider_instance_id"], lambda value: _text(value, _ID)
        ),
        "t3": {
            "max_runtime_mode": t3["max_runtime_mode"],
            "workspace_strategies": _optional(t3["workspace_strategies"], _strategies),
            "projects": _optional(t3["projects"], _ids),
            "mcp": _optional(t3["mcp"], _mcp),
            "mode_inheritance": "unknown",
        },
        "runner": {
            "workspace_strategy": "root",
            "projects": _runner_projects(runner["projects"]),
            "topic_models": None,
        },
    }
    _require(len(canonical_bytes(normalized)) <= MAX_RECORD_BYTES)
    return normalized


def _provider_instance_id():
    """The only configured T3 instance, or None when there is no single one to report."""
    try:
        base = Path(os.environ.get("T3CODE_HOME") or Path.home() / ".t3").expanduser()
        text = (base / "userdata" / "settings.json").read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, RuntimeError) as exc:
        raise ScopeInvalid from exc
    settings = loads(text)
    _require(isinstance(settings, dict))
    if "providerInstances" not in settings:
        return None
    instances = settings["providerInstances"]
    _require(isinstance(instances, dict))
    for key, value in instances.items():
        _text(key, _ID)
        _require(isinstance(value, dict))
    return next(iter(instances)) if len(instances) == 1 else None


def local_record(config, secrets):
    """Build the local record from validated config; T3 access fields stay unknown."""
    record = validate(
        {
            "version": VERSION,
            "provider_instance_id": _provider_instance_id(),
            "t3": {
                "max_runtime_mode": None,
                "workspace_strategies": None,
                "projects": None,
                "mcp": None,
                "mode_inheritance": "unknown",
            },
            "runner": {
                "workspace_strategy": "root",
                "projects": [
                    {
                        "label": label,
                        "project_id": project["project_id"],
                        "runtime_ceiling": project.get("runtime_ceiling", MODES[0]),
                    }
                    for label, project in config["projects"].items()
                ],
                "topic_models": None,
            },
        }
    )
    text = canonical_bytes(record).decode("utf-8")
    for secret in secrets:
        _require(secret not in text and json.dumps(secret, ensure_ascii=False)[1:-1] not in text)
    return record


def check_lines(config, secrets):
    record = local_record(config, secrets)
    return [
        "grant scope: " + canonical_bytes(record).decode("utf-8"),
        "grant scope sha256: " + digest(record),
        "Reported, not verified",
    ]
