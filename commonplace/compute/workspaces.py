"""Cloud workspace records: image, CPU, memory and storage limits on a Compute machine.

A record describes what a machine will run; nothing here starts, stops or reaches a
machine (that is the later lifecycle card). Validation is pure and shared by the owner
REST routes; every Store entry point takes a Principal and ``session_hash`` like the rest
of Compute. The table is plain explicit SQL (no format-string table names) and is not an
authority table of the execution fence: it grants nothing.

Docs: docs/system/compute-workspaces.md.
"""

from __future__ import annotations

import json
import math
import re
import uuid

from .control import StoreError
from .protocol import identifier, positive_integer

MAX_WORKSPACES = 200
MAX_LIST = 100
NAME_LIMIT = 60

# Ranges and defaults, served to the form so the browser never hard-codes a second copy.
LIMITS = {
    "cpu_cores": {"min": 0.5, "max": 32, "step": 0.5, "default": 2},
    "memory_mib": {"min": 512, "max": 131072, "step": 128, "default": 4096},
    "storage_gib": {"min": 1, "max": 2048, "step": 1, "default": 20},
}
NETWORKS = ("outbound", "none")
DEFAULT_NETWORK = "outbound"

CURATED_IMAGES = (
    {
        "id": "ubuntu-24-04",
        "label": "Ubuntu 24.04",
        "reference": "docker.io/library/ubuntu:24.04",
        "description": "General-purpose Linux with a familiar package manager.",
    },
    {
        "id": "debian-12-slim",
        "label": "Debian 12 (slim)",
        "reference": "docker.io/library/debian:12-slim",
        "description": "Small Linux base for lean workspaces.",
    },
    {
        "id": "python-3-12-slim",
        "label": "Python 3.12 (slim)",
        "reference": "docker.io/library/python:3.12-slim",
        "description": "Python 3.12 with pip, on a small Debian base.",
    },
    {
        "id": "node-22-slim",
        "label": "Node.js 22 (slim)",
        "reference": "docker.io/library/node:22-slim",
        "description": "Node.js 22 with npm, on a small Debian base.",
    },
)
_CURATED_BY_REFERENCE = {item["reference"]: item for item in CURATED_IMAGES}

# What the lifecycle code may record when a workspace fails; the UI words each one.
FAILURE_REASONS = frozenset(
    {
        "out_of_memory",
        "storage_full",
        "image_pull_failed",
        "image_not_found",
        "registry_login_failed",
        "machine_too_small",
        "limit_not_enforceable",
        "machine_unreachable",
        "start_failed",
    }
)
STATES = ("pending", "ready", "failed")

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"{_LABEL}(?:\.{_LABEL})*(?::([0-9]{{1,5}}))?", re.ASCII)
_COMPONENT = re.compile(r"[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*", re.ASCII)
_TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", re.ASCII)
_DIGEST = re.compile(r"sha256:[a-f0-9]{64}", re.ASCII)
_SAFE = re.compile(r"[A-Za-z0-9._:/@-]+", re.ASCII)
_PRIVATE_FLAG = {True: 1, False: 0}


def _bad(field, reason, code="invalid_request"):
    return StoreError(code, 400 if code == "invalid_request" else 409, field=field, reason=reason)


def parse_image(value):
    """Validate a container image reference and return its parts, or raise StoreError.

    Accepts ``[registry[:port]/]path[:tag][@sha256:<64 hex>]``. Anything that could act as
    an option, a shell word, a URL or user info is refused before it can be stored.
    """
    if type(value) is not str or not value:
        raise _bad("image", "empty")
    if len(value) > 255:
        raise _bad("image", "too_long")
    if "://" in value:
        raise _bad("image", "scheme")
    if not _SAFE.fullmatch(value) or value.startswith(("-", "/", ".")):
        raise _bad("image", "characters")
    name, at, digest = value.partition("@")
    if at and (not _DIGEST.fullmatch(digest) or "@" in digest):
        raise _bad("image", "digest")
    if "@" in name:
        raise _bad("image", "characters")
    tag = None
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        name, _, tag = name.rpartition(":")
        if not _TAG.fullmatch(tag):
            raise _bad("image", "tag")
    parts = name.split("/")
    registry = "docker.io"
    first = parts[0]
    if len(parts) > 1 and ("." in first or ":" in first or first == "localhost"):
        match = _HOST.fullmatch(first)
        if match is None or (match.group(1) is not None and not 1 <= int(match.group(1)) <= 65535):
            raise _bad("image", "registry")
        registry = first
        parts = parts[1:]
    if not parts or any(not _COMPONENT.fullmatch(part) for part in parts):
        raise _bad("image", "name")
    return {
        "reference": value,
        "registry": registry,
        "repository": "/".join(parts),
        "tag": tag,
        "digest": digest or None,
        "pinned": bool(digest),
    }


def _name(value):
    if type(value) is not str or not value.strip() or len(value) > NAME_LIMIT:
        raise _bad("name", "length")
    if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise _bad("name", "characters")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _bad("name", "characters") from None
    return value


def _cpu(value):
    spec = LIMITS["cpu_cores"]
    if type(value) not in (int, float):
        raise _bad("cpu_cores", "not_a_number")
    if not math.isfinite(value) or not spec["min"] <= value <= spec["max"]:
        raise _bad("cpu_cores", "range")
    if (value * 2) % 1:
        raise _bad("cpu_cores", "step")
    return int(round(value * 1000))


def _whole(field, value):
    spec = LIMITS[field]
    if type(value) is not int:
        raise _bad(field, "not_a_number")
    if not spec["min"] <= value <= spec["max"]:
        raise _bad(field, "range")
    if value % spec["step"]:
        raise _bad(field, "step")
    return value


def _image(source, reference):
    if source not in ("curated", "custom"):
        raise _bad("image_source", "choice")
    parsed = parse_image(reference)
    if source == "curated" and reference not in _CURATED_BY_REFERENCE:
        raise _bad("image", "not_curated")
    return parsed


def _flag(value):
    if type(value) is not bool:
        raise _bad("private_registry", "choice")
    return _PRIVATE_FLAG[value]


def _row(conn, workspace_id):
    identifier(workspace_id)
    row = conn.execute("SELECT * FROM cloud_workspaces WHERE id=?", (workspace_id,)).fetchone()
    if row is None:
        raise StoreError("not_found", 404)
    return row


def _machine(conn, node_id):
    if node_id is None:
        return None
    try:
        identifier(node_id)
    except Exception:
        raise _bad("node_id", "choice") from None
    row = conn.execute("SELECT id,metadata FROM nodes WHERE id=?", (node_id,)).fetchone()
    if row is None:
        raise StoreError("not_found", 404, field="node_id", reason="unknown_machine")
    return row


def _machine_name(conn, node_id):
    row = (
        conn.execute("SELECT metadata FROM nodes WHERE id=?", (node_id,)).fetchone()
        if node_id
        else None
    )
    if row is None:
        return None
    return json.loads(row["metadata"]).get("name")


def status_of(row):
    """The one status code and reason the record shows; never blank."""
    private = bool(row["private_registry"])
    if row["node_id"] is None:
        return "no_machine", None
    if private and row["registry_credential_id"] is None:
        return "needs_credential", None
    if row["state"] == "failed":
        return "failed", row["state_reason"] if row["state_reason"] in FAILURE_REASONS else None
    if row["state"] == "ready":
        return "ready", None
    return "pending", None


def public(conn, row, *, manage):
    """Explicit projection. A member never sees the registry reference."""
    parsed = parse_image(row["image_reference"])
    status, reason = status_of(row)
    started = row["applied_revision"] is not None
    result = {
        "id": row["id"],
        "name": row["name"],
        "image_source": row["image_source"],
        "image": row["image_reference"],
        "image_registry": parsed["registry"],
        "image_tag": parsed["tag"],
        "image_digest": parsed["digest"],
        "image_pinned": parsed["pinned"],
        "curated_label": (_CURATED_BY_REFERENCE.get(row["image_reference"]) or {}).get("label"),
        "private_registry": bool(row["private_registry"]),
        "credential_bound": row["registry_credential_id"] is not None,
        "cpu_cores": row["cpu_millicores"] / 1000,
        "memory_mib": row["memory_mib"],
        "storage_gib": row["storage_gib"],
        "network": row["network"],
        "node_id": row["node_id"],
        "machine_name": _machine_name(conn, row["node_id"]),
        "status": status,
        "status_reason": reason,
        "started": started,
        "revision": row["revision"],
        "applied_revision": row["applied_revision"],
        "restart_needed": started and row["applied_revision"] < row["revision"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if manage:
        result["registry_credential_id"] = row["registry_credential_id"]
        result["registry_credential_version"] = row["registry_credential_version"]
    return result


def options():
    """Static form facts: curated images, ranges, defaults, networks."""
    return {
        "images": [dict(item) for item in CURATED_IMAGES],
        "limits": {key: dict(value) for key, value in LIMITS.items()},
        "networks": list(NETWORKS),
        "default_network": DEFAULT_NETWORK,
        "name_limit": NAME_LIMIT,
    }


def list_workspaces(store, principal, *, session_hash):
    with store._transaction(principal, session_hash, member=True) as (conn, current):
        manage = current.role == "owner"
        rows = conn.execute(
            "SELECT * FROM cloud_workspaces ORDER BY created_at DESC,id DESC LIMIT ?",
            (MAX_LIST,),
        ).fetchall()
        return {
            "workspaces": [public(conn, row, manage=manage) for row in rows],
            "can_manage": manage,
        }


def get_workspace(store, principal, workspace_id, *, session_hash):
    with store._transaction(principal, session_hash, member=True) as (conn, current):
        row = _row(conn, workspace_id)
        return {
            "workspace": public(conn, row, manage=current.role == "owner"),
            "can_manage": current.role == "owner",
        }


def _taken(conn, name, except_id=""):
    found = conn.execute(
        "SELECT 1 FROM cloud_workspaces WHERE name=? COLLATE NOCASE AND id<>?", (name, except_id)
    ).fetchone()
    if found is not None:
        raise StoreError("conflict", 409, field="name", reason="name_taken")


def create_workspace(
    store,
    principal,
    *,
    session_hash,
    name,
    image_source,
    image,
    cpu_cores=LIMITS["cpu_cores"]["default"],
    memory_mib=LIMITS["memory_mib"]["default"],
    storage_gib=LIMITS["storage_gib"]["default"],
    network=DEFAULT_NETWORK,
    node_id=None,
    private_registry=False,
):
    with store._transaction(principal, session_hash) as (conn, current):
        name = _name(name)
        _image(image_source, image)
        millicores = _cpu(cpu_cores)
        memory = _whole("memory_mib", memory_mib)
        storage = _whole("storage_gib", storage_gib)
        if network not in NETWORKS:
            raise _bad("network", "choice")
        private = _flag(private_registry)
        _machine(conn, node_id)
        _taken(conn, name)
        if conn.execute("SELECT count(*) FROM cloud_workspaces").fetchone()[0] >= MAX_WORKSPACES:
            raise StoreError("conflict", 409, field=None, reason="limit_reached")
        workspace_id = uuid.uuid4().hex
        now = store._now()
        conn.execute(
            "INSERT INTO cloud_workspaces (id,name,node_id,image_source,image_reference,"
            "private_registry,registry_credential_id,registry_credential_version,"
            "cpu_millicores,memory_mib,storage_gib,network,state,state_reason,revision,"
            "applied_revision,created_by,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,NULL,NULL,?,?,?,?,'pending',NULL,1,NULL,?,?,?)",
            (
                workspace_id,
                name,
                node_id,
                image_source,
                image,
                private,
                millicores,
                memory,
                storage,
                network,
                current.user_id,
                now,
                now,
            ),
        )
        store._audit(conn, current, workspace_id, "workspace.created")
        return public(conn, _row(conn, workspace_id), manage=True)


_EDITABLE = frozenset(
    "name image_source image cpu_cores memory_mib storage_gib network node_id private_registry".split()
)


def update_workspace(store, principal, workspace_id, *, session_hash, expected_revision, **changes):
    with store._transaction(principal, session_hash) as (conn, current):
        row = _row(conn, workspace_id)
        positive_integer(expected_revision)
        if not changes or set(changes) - _EDITABLE:
            raise StoreError()
        if expected_revision != row["revision"]:
            raise StoreError("conflict", 409, field=None, reason="stale_revision")
        values = dict(row)
        if "name" in changes:
            values["name"] = _name(changes["name"])
            _taken(conn, values["name"], workspace_id)
        source = changes.get("image_source", row["image_source"])
        reference = changes.get("image", row["image_reference"])
        if {"image_source", "image"} & set(changes):
            _image(source, reference)
        values["image_source"], values["image_reference"] = source, reference
        if "cpu_cores" in changes:
            values["cpu_millicores"] = _cpu(changes["cpu_cores"])
        if "memory_mib" in changes:
            values["memory_mib"] = _whole("memory_mib", changes["memory_mib"])
        if "storage_gib" in changes:
            values["storage_gib"] = _whole("storage_gib", changes["storage_gib"])
        if "network" in changes:
            if changes["network"] not in NETWORKS:
                raise _bad("network", "choice")
            values["network"] = changes["network"]
        if "private_registry" in changes:
            values["private_registry"] = _flag(changes["private_registry"])
        if "node_id" in changes:
            _machine(conn, changes["node_id"])
            if row["applied_revision"] is not None and changes["node_id"] != row["node_id"]:
                raise StoreError("conflict", 409, field="node_id", reason="machine_locked")
            values["node_id"] = changes["node_id"]
        # A registry login belongs to one registry; changing the image or the private flag drops it.
        before = parse_image(row["image_reference"])["registry"]
        after = parse_image(reference)["registry"]
        if not values["private_registry"] or before != after:
            values["registry_credential_id"] = values["registry_credential_version"] = None
        runtime = (
            "image_source",
            "image_reference",
            "private_registry",
            "cpu_millicores",
            "memory_mib",
            "storage_gib",
            "network",
            "node_id",
            "registry_credential_id",
        )
        stored = ("name",) + runtime + ("registry_credential_version",)
        if all(values[key] == row[key] for key in stored):
            return public(conn, row, manage=True)
        revision = row["revision"] + (any(values[key] != row[key] for key in runtime))
        conn.execute(
            "UPDATE cloud_workspaces SET name=?,node_id=?,image_source=?,image_reference=?,"
            "private_registry=?,registry_credential_id=?,registry_credential_version=?,"
            "cpu_millicores=?,memory_mib=?,storage_gib=?,network=?,revision=?,updated_at=? "
            "WHERE id=?",
            (
                values["name"],
                values["node_id"],
                values["image_source"],
                values["image_reference"],
                values["private_registry"],
                values["registry_credential_id"],
                values["registry_credential_version"],
                values["cpu_millicores"],
                values["memory_mib"],
                values["storage_gib"],
                values["network"],
                revision,
                store._now(),
                workspace_id,
            ),
        )
        store._audit(conn, current, workspace_id, "workspace.updated")
        return public(conn, _row(conn, workspace_id), manage=True)


def bind_registry_credential(
    store, principal, workspace_id, *, session_hash, credential_id, credential_version
):
    """Record a registry login reference, once, on a workspace that says it needs one."""
    with store._transaction(principal, session_hash) as (conn, current):
        row = _row(conn, workspace_id)
        if not row["private_registry"]:
            raise StoreError("conflict", 409, field=None, reason="registry_not_private")
        if row["registry_credential_id"] is not None:
            raise StoreError("credential_already_bound", 409)
        try:
            identifier(credential_id)
            positive_integer(credential_version)
        except Exception:
            raise StoreError() from None
        conn.execute(
            "UPDATE cloud_workspaces SET registry_credential_id=?,registry_credential_version=?,"
            "updated_at=? WHERE id=?",
            (credential_id, credential_version, store._now(), workspace_id),
        )
        store._audit(conn, current, workspace_id, "workspace.credential_bound")
        return public(conn, _row(conn, workspace_id), manage=True)


def set_state(conn, workspace_id, state, reason=None, *, applied_revision=None):
    """Lifecycle write for the later start/stop code; the REST surface never calls it."""
    if state not in STATES or (state == "failed") != (reason is not None):
        raise ValueError("invalid_workspace_state")
    if reason is not None and reason not in FAILURE_REASONS:
        raise ValueError("invalid_workspace_state")
    if applied_revision is None:
        conn.execute(
            "UPDATE cloud_workspaces SET state=?,state_reason=? WHERE id=?",
            (state, reason, workspace_id),
        )
    else:
        conn.execute(
            "UPDATE cloud_workspaces SET state=?,state_reason=?,applied_revision=? WHERE id=?",
            (state, reason, applied_revision, workspace_id),
        )
