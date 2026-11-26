"""Exportable workspace templates: build a package, preview an import, write nothing.

A template is an owner-chosen subset of one workspace (agent roles and seats, skill
references, routines, goals, an orchestrator config proposal, optionally verbatim memory)
as one JSON package that another workspace can later import. This slice is the export and
the preview only: nothing here writes to a workspace.

Package (``commonplace-workspace-template``, integer ``version`` 1)::

    {"format", "version", "sections", "memory_included", "stripped", "redactions",
     "content": {<section>: [...]}, "digest": sha256 of the canonical ``content``}

Every section is built from an allow-list of fields, so tokens, connection names, machine
bindings, runner and trigger grants, user and row ids never enter the package. Free text is
then scanned with ``commonplace.redact`` and a credential-shaped value is replaced by its
marker (listed in ``redactions`` by path and kind, never by value). Verbatim memory is out
unless the section is named and ``include_memory`` is set; it is never rewritten, so a
drawer that looks like a secret refuses the export.

``preview`` takes a package and a ``Source`` read from the target workspace and answers
what an import would create, which names collide (resolution ``skip``: nothing is
overwritten), which fields would be dropped, and refuses a package that is tampered, of
another format or version, declares memory it does not list, or holds anything that looks
like a secret. An orchestrator config is always a proposal; runner and trigger grants are
never imported. Findings carry paths and kinds, never the matching text.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from commonplace import redact
from commonplace.extensions import ToolError

FORMAT = "commonplace-workspace-template"
VERSION = 1
SECTIONS = ("roles", "seats", "skills", "routines", "goals", "orch_config", "memory")
# What an export holds unless the owner names more: nothing opt-in, nothing that proposes
# a live configuration.
DEFAULT_SECTIONS = ("roles", "skills", "routines", "goals")
BOARD_SECTIONS = ("roles", "seats", "skills", "orch_config")
MAX_PACKAGE_BYTES = 2 * 1024 * 1024
# Field classes the export leaves out by construction, named so a reader sees what is absent.
STRIPPED = (
    "connections",
    "factory",
    "fleet",
    "ids",
    "machine_bindings",
    "mcps",
    "owners",
    "runner",
    "secret_refs",
    "triggers",
)

ROLE_FIELDS = ("kind", "parent", "harness", "topic", "model_role", "workflow", "prompt", "area")
_ALLOWED = {
    "roles": {"name", "skills", *ROLE_FIELDS},
    "seats": {"role", "name"},
    "skills": {"id", "version"},
    "routines": {"name", "template", "schedule", "timezone", "state"},
    "goals": {"ref", "parent", "title", "detail", "archived"},
    "memory": {"wing", "room", "text"},
}
_CONFIG_KEYS = {"schema", "limits", "roles", "workflows"}
_GRANT_KEYS = {"runner", "triggers", "mcps"}  # reach outside the workspace: owner acceptance
_SCAN_KINDS = tuple(kind for kind in redact.KINDS if kind not in ("too_long", "json_secret_key"))
_MARKER = re.compile(r"\[(?:redacted|omitted):[a-z_]+\]")


class TemplateError(ToolError):
    """A refusal with a stable ``code``; the text never holds package or workspace content."""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


@dataclass
class Source:
    """One workspace as the template reads it (also the target of a preview)."""

    board: str | None = None
    config_body: str | None = None  # the board's accepted live orchestrator config
    seats: list[dict] = field(default_factory=list)
    routines: list[dict] = field(default_factory=list)
    goals: list[dict] = field(default_factory=list)
    boards: set[str] | None = None  # slugs of the workspace's boards; None when not read
    proposed_shas: set[str] = field(default_factory=set)  # open config proposals of the board


# ------------------------------------------------------------------ secrets


@functools.lru_cache(maxsize=2048)
def _named_secret(key: str) -> bool:
    return bool(redact.redact_value({key: "x"})[1])


def _secret_key(key: Any) -> bool:
    return isinstance(key, str) and _named_secret(key)


def _key_kinds(key: Any) -> list[str]:
    """Credential shapes in the key text itself (a name, not a secret-NAMED key)."""
    return redact.find_kinds(key, _SCAN_KINDS) if isinstance(key, str) else []


def _join(path: str, key: Any) -> str:
    """Paths never carry key text that looks like a credential."""
    label = "<key>" if _key_kinds(key) else str(key)
    return f"{path}.{label}" if path else label


def scan(value: Any, path: str = "") -> list[dict]:
    """Where ``value`` looks like a credential: ``[{"path", "kind"}]``, never the text."""
    found: list[dict] = []
    if isinstance(value, dict):
        for key, item in value.items():
            here = _join(path, key)
            found.extend({"path": here, "kind": kind} for kind in _key_kinds(key))
            if _secret_key(key) and not (isinstance(item, str) and _MARKER.fullmatch(item)):
                found.append({"path": here, "kind": "json_secret_key"})
            found.extend(scan(item, here))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(scan(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        found.extend({"path": path, "kind": kind} for kind in redact.find_kinds(value, _SCAN_KINDS))
    return found


def _redacted(value: Any, path: str, out: list[dict]) -> Any:
    if isinstance(value, dict):
        clean: dict = {}
        for key, item in value.items():
            here = _join(path, key)
            name = key
            kinds = _key_kinds(key)
            if kinds:
                out.append({"path": here, "kinds": kinds})
                name = redact.redact_tail(key, 2 * len(key) + 64)
                while name in clean:
                    name += "_"
            if _secret_key(key) and not (isinstance(item, str) and _MARKER.fullmatch(item)):
                out.append({"path": here, "kinds": ["json_secret_key"]})
                clean[name] = "[redacted:json_secret_key]"
            else:
                clean[name] = _redacted(item, here, out)
        return clean
    if isinstance(value, list):
        return [_redacted(item, f"{path}[{i}]", out) for i, item in enumerate(value)]
    if isinstance(value, str):
        kinds = redact.find_kinds(value, _SCAN_KINDS)
        if kinds:
            out.append({"path": path, "kinds": kinds})
            return redact.redact_tail(value, 2 * len(value) + 64)
    return value


# ------------------------------------------------------------------ package


def digest(content: Any) -> str:
    text = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode()).hexdigest()


def seal(package: dict) -> dict:
    """A copy of ``package`` whose digest matches its content."""
    return {**package, "digest": digest(package.get("content"))}


def load_package(text: str | bytes) -> dict:
    if len(text) > MAX_PACKAGE_BYTES:
        raise TemplateError("package_too_large", f"at most {MAX_PACKAGE_BYTES} bytes")
    try:
        package = json.loads(text)
    except ValueError:
        raise TemplateError("package_invalid", "not JSON") from None
    if not isinstance(package, dict):
        raise TemplateError("package_invalid", "not a JSON object")
    return package


def _config(source: Source):
    from commonplace.orch import schema

    if source.config_body is None:
        raise TemplateError("no_live_config", "the board has no accepted orchestrator config")
    return schema.parse(source.config_body)


def _role_entry(role) -> dict:
    entry: dict[str, Any] = {"name": role.name}
    for key in ROLE_FIELDS:
        value = getattr(role, key)
        if value is not None:
            entry[key] = value
    if role.skills:
        entry["skills"] = [{"id": s.id, "version": s.version} for s in role.skills]
    return entry


def _goals(rows: list[dict]) -> list[dict]:
    """Parents before children, ids renumbered 1..n so no workspace row id leaves."""
    refs: dict[Any, int] = {}
    out: list[dict] = []

    def walk(parent):
        for row in rows:
            if row.get("parent") == parent and row["id"] not in refs:
                refs[row["id"]] = len(refs) + 1
                out.append(
                    {
                        "ref": refs[row["id"]],
                        "parent": refs.get(parent),
                        "title": row["title"],
                        "detail": row.get("detail", ""),
                        "archived": bool(row.get("archived")),
                    }
                )
                walk(row["id"])

    walk(None)
    return out


def _section(name: str, source: Source) -> Any:
    if name == "roles":
        config = _config(source)
        return [_role_entry(config.roles[r]) for r in sorted(config.roles)]
    if name == "skills":
        config = _config(source)
        found = {(s.id, s.version) for role in config.roles.values() for s in role.skills}
        return [{"id": i, "version": v} for i, v in sorted(found)]
    if name == "seats":
        return [{"role": s["role"], "name": s["name"]} for s in source.seats]
    if name == "routines":
        return [{k: r[k] for k in _ALLOWED["routines"]} for r in source.routines]
    if name == "goals":
        return _goals(source.goals)
    config = _config(source)  # orch_config: a proposal without grants, bindings or project
    body: dict[str, Any] = {"schema": config.schema}
    if config.max_agents is not None:
        body["limits"] = {"max_agents": config.max_agents}
    body["roles"] = {
        name: {k: v for k, v in _role_entry(config.roles[name]).items() if k != "name"}
        for name in sorted(config.roles)
    }
    if config.workflows:
        body["workflows"] = {
            n: {"steps": list(config.workflows[n].steps)} for n in sorted(config.workflows)
        }
    return {"proposal_only": True, "config": body}


def _memory(reader: Callable[[], list[dict]] | None) -> list[dict]:
    if reader is None:
        raise TemplateError("memory_unavailable", "no memory reader for this workspace")
    drawers = []
    for item in reader():
        if not isinstance(item, dict) or not all(
            isinstance(item.get(k), str) for k in _ALLOWED["memory"]
        ):
            raise TemplateError("package_invalid", "memory drawers need wing, room and text")
        drawers.append({k: item[k] for k in sorted(_ALLOWED["memory"])})
    found = scan(drawers, "memory")
    if found:
        paths = ", ".join(sorted({f["path"] for f in found})[:10])
        raise TemplateError("memory_contains_secret", f"verbatim memory is not rewritten: {paths}")
    return drawers


def build_package(
    source: Source,
    sections: tuple[str, ...] | list[str] = DEFAULT_SECTIONS,
    *,
    include_memory: bool = False,
    memory_reader: Callable[[], list[dict]] | None = None,
) -> dict:
    chosen = [s for s in SECTIONS if s in set(sections)]
    if set(sections) - set(SECTIONS):
        raise TemplateError("unknown_section", "sections: " + ", ".join(SECTIONS))
    if ("memory" in chosen) != bool(include_memory):
        raise TemplateError("memory_needs_opt_in", "name the memory section and set include_memory")
    if "memory" in chosen and memory_reader is None:
        raise TemplateError("memory_unavailable", "no memory reader for this workspace")
    content = {name: _section(name, source) for name in chosen if name != "memory"}
    redactions: list[dict] = []
    content = _redacted(content, "", redactions)
    if scan(content):  # fail closed: a redaction that left a secret shape is not shipped
        raise TemplateError("secret_found", "the export still holds a credential-shaped value")
    if "memory" in chosen:
        content["memory"] = _memory(memory_reader)
    package = {
        "format": FORMAT,
        "version": VERSION,
        "sections": chosen,
        "memory_included": "memory" in chosen,
        "stripped": list(STRIPPED),
        "redactions": redactions,
        "content": {name: content[name] for name in chosen},
    }
    return seal(package)


# ------------------------------------------------------------------ orchestrator proposal


_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


def _toml_str(text: str) -> str:
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")


def _toml_key(key: str) -> str:
    return key if _BARE_KEY.fullmatch(key) else _toml_str(key)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _toml_str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return (
            "{ " + ", ".join(f"{_toml_key(k)} = {_toml_value(v)}" for k, v in value.items()) + " }"
        )
    raise TemplateError("proposal_invalid", "a config value has no TOML form")


def to_toml(data: dict, _path: tuple[str, ...] = ()) -> str:
    """TOML text for a plain config dict: scalars and lists first, then one table per dict."""
    lines = [
        f"{_toml_key(k)} = {_toml_value(v)}"
        for k, v in data.items()
        if v is not None and not isinstance(v, dict)
    ]
    out = "\n".join(lines) + "\n" if lines else ""
    for key, value in data.items():
        if isinstance(value, dict):
            name = ".".join(_toml_key(p) for p in (*_path, key))
            out += f"\n[{name}]\n" + to_toml(value, (*_path, key))
    return out


def _clean_role(entry: dict) -> dict:
    return {k: copy.deepcopy(v) for k, v in entry.items() if k in _ALLOWED["roles"] - {"name"}}


def plan_proposal(content: dict, target: Source) -> dict | None:
    """The one orchestrator config an import would file as a proposal, or ``None``.

    New roles (and, on a board with no live config, the exported workflows and limits) are
    added to the target's live config, or to a fresh config for the target board. Roles the
    target already has are never touched. Runner, triggers, MCPs and fleet never come from a
    package. Raises ``TemplateError('proposal_invalid')`` when the result is not a valid
    config; the text names a code and path, never a value."""
    from commonplace.orch import schema

    live = None
    if target.config_body is not None:
        live = copy.deepcopy(_config(target).to_dict())
    exported = content.get("orch_config") or {}
    config = exported.get("config") if isinstance(exported, dict) else None
    config = config if isinstance(config, dict) and live is None else {}
    incoming = {e["name"]: _clean_role(e) for e in content.get("roles", [])}
    for name, fields in (config.get("roles") or {}).items():
        incoming.setdefault(name, _clean_role(fields))
    have = set(live["roles"]) if live else set()
    added = {name: fields for name, fields in incoming.items() if name not in have}
    if not added:
        return None
    merged: dict[str, Any] = live or {
        "schema": schema.SCHEMA_ID,
        "project": target.board,
        "roles": {},
    }
    if live is None:
        limits = config.get("limits")
        if isinstance(limits, dict) and "max_agents" in limits:
            merged["limits"] = {"max_agents": limits["max_agents"]}
        flows = config.get("workflows")
        if isinstance(flows, dict) and flows:
            merged["workflows"] = {
                n: {"steps": copy.deepcopy(f.get("steps"))}
                for n, f in flows.items()
                if isinstance(f, dict)
            }
    dropped = []
    for name, fields in added.items():
        if fields.get("workflow") not in merged.get("workflows", {}):
            if fields.pop("workflow", None) is not None:
                dropped.append({"path": f"roles.{name}.workflow", "reason": "workflow_unresolved"})
        merged["roles"][name] = fields
    try:
        expected = schema.validate(merged).to_dict()
        text = to_toml(merged)
        parsed = schema.parse(text)
    except schema.ConfigError as exc:
        raise TemplateError("proposal_invalid", f"{exc.code} {exc.path}".strip()) from None
    if parsed.to_dict() != expected:
        raise TemplateError("proposal_invalid", "the config does not survive being written")
    return {
        "text": text,
        "sha256": parsed.sha256,
        "roles": sorted(added),
        "dropped": dropped,
    }


# ------------------------------------------------------------------ preview


def _refuse(code: str, detail: str) -> dict:
    return {"code": code, "detail": detail}


def _dropped(content: dict) -> list[dict]:
    out: list[dict] = []

    def drop(path, reason):
        out.append({"path": path, "reason": reason})

    for name, items in content.items():
        if name not in SECTIONS:
            drop(name, "unknown_section")
        elif name == "orch_config":
            for key in items:
                if key not in {"proposal_only", "config"}:
                    drop(f"orch_config.{key}", "not_importable")
            config = items.get("config")
            for key in config if isinstance(config, dict) else ():
                if key in _GRANT_KEYS:
                    drop(f"orch_config.config.{key}", "needs_owner_acceptance")
                elif key not in _CONFIG_KEYS:
                    drop(f"orch_config.config.{key}", "not_importable")
            roles = config.get("roles") if isinstance(config, dict) else None
            for role, entry in (roles or {}).items():
                for key in entry:
                    if key not in _ALLOWED["roles"] - {"name"}:
                        drop(f"orch_config.config.roles.{role}.{key}", "not_importable")
        else:
            for index, entry in enumerate(items):
                for key in entry:
                    if key not in _ALLOWED[name]:
                        drop(f"{name}[{index}].{key}", "not_importable")
    return out


def _goal_paths(goals: list[dict], key: str, parent_key: str, ident: str) -> list[tuple[Any, str]]:
    by_id = {g[ident]: g for g in goals}
    out = []
    for goal in goals:
        parts, seen, cursor = [], set(), goal
        while cursor is not None and cursor[ident] not in seen:
            seen.add(cursor[ident])
            parts.append(str(cursor[key]))
            cursor = by_id.get(cursor.get(parent_key))
        out.append((goal[ident], " > ".join(reversed(parts))))
    return out


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


_REQUIRED = {
    "roles": {"name": str},
    "seats": {"role": str, "name": str},
    "skills": {"id": str},
    "routines": {"name": str},
    "goals": {"title": str},
    "memory": {"wing": str, "room": str, "text": str},
}


def _entries_ok(name: str, items: Any) -> bool:
    if not isinstance(items, list):
        return False
    for entry in items:
        if not isinstance(entry, dict):
            return False
        if any(not isinstance(entry.get(k), t) for k, t in _REQUIRED[name].items()):
            return False
        if name == "goals" and not (
            _is_int(entry.get("ref")) and (entry.get("parent") is None or _is_int(entry["parent"]))
        ):
            return False
    return True


def _config_ok(items: Any) -> bool:
    if not isinstance(items, dict):
        return False
    config = items.get("config")
    if config is None:
        return True
    if not isinstance(config, dict):
        return False
    roles = config.get("roles", {})
    return isinstance(roles, dict) and all(isinstance(r, dict) for r in roles.values())


def _check_shape(package: dict) -> bool:
    if not all(isinstance(r, dict) for r in package.get("redactions", [])) or not isinstance(
        package.get("redactions", []), list
    ):
        return False
    for name, items in package["content"].items():
        if name == "orch_config":
            if not _config_ok(items):
                return False
        elif name in SECTIONS and not _entries_ok(name, items):
            return False
    return True


def _collisions(content: dict, target: Source, create: dict, collisions: list) -> bool:
    """Fill ``create`` and ``collisions`` for the board-free and board sections; False when the
    target's own config does not parse."""

    def collide(section, key):
        collisions.append({"section": section, "key": key, "resolution": "skip"})

    target_roles: set[str] = set()
    if any(name in content for name in ("roles", "orch_config")) and target.config_body:
        try:
            target_roles = set(_config(target).roles)
        except ValueError:  # orch ConfigError
            return False
    for entry in content.get("roles", []):
        if entry.get("name") in target_roles:
            collide("roles", entry["name"])
        else:
            create["roles"] = create.get("roles", 0) + 1
    have = {s.get("role") for s in target.seats}
    for entry in content.get("seats", []):
        if entry.get("role") in have:
            collide("seats", entry["role"])
        else:
            create["seats"] = create.get("seats", 0) + 1
    names = {str(r.get("name", "")).casefold() for r in target.routines}
    for entry in content.get("routines", []):
        if str(entry.get("name", "")).casefold() in names:
            collide("routines", str(entry.get("name", "")).casefold())
        else:
            create["routines"] = create.get("routines", 0) + 1
    existing = {p.casefold() for _, p in _goal_paths(target.goals, "title", "parent", "id")}
    for _, path in _goal_paths(content.get("goals", []), "title", "parent", "ref"):
        if path.casefold() in existing:
            collide("goals", path)
        else:
            create["goals"] = create.get("goals", 0) + 1
    return True


def preview(package: Any, target: Source, *, include_memory: bool = False) -> dict:
    """What importing ``package`` into ``target`` would do; reads only, writes nothing."""
    result: dict[str, Any] = {
        "ok": False,
        "refused": [],
        "findings": [],
        "would_create": {},
        "collisions": [],
        "dropped": [],
        "proposals": [],
        "references": [],
        "writes": 0,
    }
    refused = result["refused"]
    if not isinstance(package, dict) or not isinstance(package.get("content"), dict):
        refused.append(_refuse("package_invalid", "not a workspace template package"))
        return result
    content = package["content"]
    if package.get("format") != FORMAT:
        refused.append(_refuse("unsupported_format", f"expected {FORMAT}"))
    if package.get("version") != VERSION:
        refused.append(_refuse("unsupported_version", f"this build reads version {VERSION}"))
    if package.get("digest") != digest(content):
        refused.append(_refuse("digest_mismatch", "content differs from its digest"))
    if "memory" in content and package.get("memory_included") is not True:
        refused.append(_refuse("memory_not_declared", "memory present but not declared"))
    if not _check_shape(package):
        refused.append(_refuse("package_invalid", "a section has the wrong shape"))
    result["findings"] = scan(content) + scan(package.get("redactions", []), "redactions")
    if result["findings"]:
        refused.append(_refuse("secret_found", "the package holds credential-shaped values"))
    if target.board is None and any(name in BOARD_SECTIONS for name in content):
        refused.append(_refuse("target_board_required", "name the target board"))
    if refused:
        return result

    result["dropped"] = _dropped(content)
    for item in package.get("redactions", []):
        result["dropped"].append({"path": item.get("path", ""), "reason": "redacted_at_export"})
    create, collisions = result["would_create"], result["collisions"]
    if not _collisions(content, target, create, collisions):
        refused.append(_refuse("target_config_invalid", "the target config does not parse"))
        return result
    refused.extend(_routine_refusals(content, target))
    plan = None
    try:
        plan = plan_proposal(content, target)
    except TemplateError as exc:
        refused.append(_refuse(exc.code, str(exc).removeprefix(exc.code).lstrip(": ")))
    if refused:
        return result
    create.pop("roles", None)
    entry = None
    if plan is not None:
        result["dropped"].extend(plan["dropped"])
        if plan["sha256"] in target.proposed_shas:
            collisions.append(
                {"section": "config_proposal", "key": plan["sha256"][:12], "resolution": "skip"}
            )
        else:
            create["roles"] = len(plan["roles"])
            entry = {
                "section": "config_proposal",
                "status": "proposal_only",
                "board": target.board,
                "sha256": plan["sha256"],
                "roles": plan["roles"],
                "note": "filed as a proposed version; an owner accepts it by its sha256",
            }
    if "orch_config" in content:
        if target.config_body is not None:
            collisions.append({"section": "orch_config", "key": target.board, "resolution": "skip"})
        result["proposals"].append(
            {
                "section": "orch_config",
                "status": "proposal_only",
                "note": "an owner must accept it; it never becomes live or grants a runner by import",
            }
        )
    if entry is not None:
        result["proposals"].append(entry)
    for skill in content.get("skills", []):
        result["references"].append(
            {"section": "skills", "id": skill.get("id"), "version": skill.get("version")}
        )
    if "memory" in content:
        if include_memory:
            create["memory"] = len(content["memory"])
        else:
            result["dropped"].append({"path": "memory", "reason": "memory_not_included_by_default"})
    result["ok"] = True
    result["preview_id"] = preview_id(package, target, result, include_memory)
    return result


def preview_id(package: dict, target: Source, result: dict, include_memory: bool) -> str:
    """Names one preview: the exact package, target board, options and what it would do."""
    return digest(
        {
            "package": package.get("digest"),
            "board": target.board,
            "include_memory": bool(include_memory),
            **{
                key: result[key]
                for key in ("would_create", "collisions", "dropped", "proposals", "references")
            },
        }
    )


def _routine_refusals(content: dict, target: Source) -> list[dict]:
    """Routines a write would refuse: a bad name, template or schedule, or no board to run on."""
    from commonplace import routines

    existing = {str(r.get("name", "")).casefold() for r in target.routines}
    out: list[dict] = []
    for index, entry in enumerate(content.get("routines", [])):
        if str(entry.get("name", "")).casefold() in existing:
            continue
        here = f"routines[{index}]"
        try:
            routines.check_name(entry.get("name"))
            template = routines.template_fields(entry.get("template"))
            routines.check_schedule(
                entry.get("schedule"), entry.get("timezone", "UTC"), time.time()
            )
        except ToolError:
            out.append(_refuse("routine_invalid", here))
            continue
        if target.boards is not None and routine_board(template, target) is None:
            out.append(_refuse("routine_board_missing", here))
    return out


def routine_board(template: dict, target: Source) -> str | None:
    """The board an imported routine runs on: its own when the target has it, else the target
    board; ``None`` when neither exists. A target whose boards were not read keeps both."""
    boards = target.boards
    if boards is None:
        return template.get("board")
    if template.get("board") in boards:
        return template["board"]
    return target.board if target.board in boards else None


# ------------------------------------------------------------------ reading a workspace


def _read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _missing_table(exc: sqlite3.OperationalError) -> bool:
    return "no such table" in str(exc)


def _unreadable() -> TemplateError:
    return TemplateError("store_unreadable", "a workspace store could not be read; retry")


def _rows(db, sql: str, args: tuple) -> list:
    try:
        with db._lock:
            return db._conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError as exc:
        if _missing_table(exc):  # the table does not exist yet: nothing to read
            return []
        raise _unreadable() from None  # locked or I/O error: not an empty section


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return {}


def read_source(db, workspace_id: str, directory: str | Path, board: str | None) -> Source:
    """Read one workspace without writing: accepted config, seats, routines, goals."""
    directory = Path(directory)
    source = Source(board=board, boards=set())
    runner_db = directory / "runner.sqlite3"
    if board and runner_db.exists():
        from commonplace.orch import store as orch_store

        try:
            with orch_store.Store.existing(directory).connection() as conn:
                version = orch_store.accepted_in(conn, board)
                source.proposed_shas = {
                    row["sha256"]
                    for row in conn.execute(
                        "SELECT sha256 FROM orch_configs WHERE board=? AND status='proposed'",
                        (board,),
                    )
                }
        except sqlite3.OperationalError as exc:
            if not _missing_table(exc):
                raise _unreadable() from None
            version = None
        source.config_body = version.body if version else None
    if board:
        source.seats = [
            {"role": r["role"], "name": r["name"]}
            for r in _rows(
                db,
                "SELECT role, name FROM project_orchestrators WHERE workspace_id=? AND board=? "
                "ORDER BY role",
                (workspace_id, board),
            )
        ]
    source.routines = [
        {
            "name": r["name"],
            "template": _json(r["template"]),
            "schedule": _json(r["schedule"]),
            "timezone": r["timezone"],
            "state": r["state"],
        }
        for r in _rows(
            db,
            "SELECT name, template, schedule, timezone, state FROM routines "
            "WHERE workspace_id=? AND deleted_at IS NULL ORDER BY created_at, id",
            (workspace_id,),
        )
    ]
    boards_db = directory / "boards.sqlite3"
    if boards_db.exists():
        try:
            conn = _read_only(boards_db)
            try:
                source.boards = {
                    r["slug"] for r in conn.execute("SELECT slug FROM boards WHERE archived=0")
                }
                source.goals = [
                    {
                        "id": r["id"],
                        "parent": r["parent_id"],
                        "title": r["title"],
                        "detail": r["detail"],
                        "archived": bool(r["archived"]),
                    }
                    for r in conn.execute(
                        "SELECT id, parent_id, title, detail, archived FROM goals ORDER BY id"
                    )
                ]
            finally:
                conn.close()
        except sqlite3.OperationalError as exc:
            if not _missing_table(exc):
                raise _unreadable() from None
            source.goals = []
    return source
