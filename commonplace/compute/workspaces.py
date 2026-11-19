"""Cloud workspace records: image, CPU, memory and storage limits on a Compute machine.

A record describes what a machine will run; nothing here starts, stops or reaches a
machine (that is the later lifecycle card). Validation is pure and shared by the owner
REST routes; every Store entry point takes a Principal and ``session_hash`` like the rest
of Compute. The table is plain explicit SQL (no format-string table names) and is not an
authority table of the execution fence: it grants nothing.

Docs: docs/system/compute-workspaces.md.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
import uuid

from .control import StoreError
from .protocol import identifier, positive_integer

# One cap for creation and for the list, so a record can never exist without being listed.
MAX_WORKSPACES = 100
MAX_LIST = MAX_WORKSPACES
NAME_LIMIT = 60  # code points, counted after NFC normalisation (the browser counts the same way)

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
        "description": "A general-purpose Linux system. A good default for most work.",
    },
    {
        "id": "debian-12-slim",
        "label": "Debian 12 (slim)",
        "reference": "docker.io/library/debian:12-slim",
        "description": "A smaller Linux system with fewer tools preinstalled, so it downloads faster.",
    },
    {
        "id": "python-3-12-slim",
        "label": "Python 3.12 (slim)",
        "reference": "docker.io/library/python:3.12-slim",
        "description": "Linux with Python 3.12 ready to use. Good for Python projects.",
    },
    {
        "id": "node-22-slim",
        "label": "Node.js 22 (slim)",
        "reference": "docker.io/library/node:22-slim",
        "description": "Linux with Node.js 22 ready to use. Good for JavaScript projects.",
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
_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,64}", re.ASCII)
_NUMERIC_LABEL = re.compile(r"(?:0x[0-9a-f]+|[0-9]+)", re.ASCII)
_LOCAL_SUFFIXES = (
    ".local",
    ".localhost",
    ".internal",
    ".lan",
    ".home",
    ".corp",
    ".intranet",
    ".private",
    ".home.arpa",
)
_PRIVATE_FLAG = {True: 1, False: 0}
# Registry logins need a custody purpose and pull-time use that arrive with the start/stop lifecycle, so no
# record may claim to need one yet (docs/system/compute-workspaces.md, "Registry logins").
REGISTRY_LOGINS = False
_BAD_NAME_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


def _bad(field, reason, code="invalid_request"):
    return StoreError(code, 400 if code == "invalid_request" else 409, field=field, reason=reason)


def _conflict(reason, field=None):
    return StoreError("conflict", 409, field=field, reason=reason)


def _private_host(host):
    """True for hosts that name a local or private network, or an IP address in any notation."""
    name = host.partition(":")[0]
    if "." not in name or name.endswith(_LOCAL_SUFFIXES):
        return True
    return _NUMERIC_LABEL.fullmatch(name.rsplit(".", 1)[-1]) is not None


def parse_image(value, *, strict=True):
    """Validate a container image reference and return its parts, or raise StoreError.

    Accepts ``[registry[:port]/]path[:tag][@sha256:<64 hex>]``. Anything that could act as
    an option, a shell word, a URL or user info is refused before it can be stored. ``strict``
    also refuses registries on local or private networks and a registry without a path; it is
    off only when describing a record that was stored before those rules.
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
    if strict and len(parts) == 1 and "." in first:
        # "ghcr.io:443" would otherwise read as an image called ghcr.io with tag 443.
        raise _bad("image", "registry_no_path")
    if len(parts) > 1 and ("." in first or ":" in first or first == "localhost"):
        match = _HOST.fullmatch(first)
        if match is None or (match.group(1) is not None and not 1 <= int(match.group(1)) <= 65535):
            raise _bad("image", "registry")
        if strict and _private_host(first):
            raise _bad("image", "registry_private")
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
    """The stored name: NFC, 1 to NAME_LIMIT code points, visible, with a letter or digit."""
    if type(value) is not str:
        raise _bad("name", "length")
    value = unicodedata.normalize("NFC", value)
    if not value.strip() or len(value) > NAME_LIMIT:
        raise _bad("name", "length")
    # Control, format (zero-width and bidi controls), surrogate, private, unassigned and
    # line/paragraph separators can hide or reorder text; none belongs in a name.
    if value != value.strip() or any(
        unicodedata.category(c) in _BAD_NAME_CATEGORIES for c in value
    ):
        raise _bad("name", "characters")
    if not any(unicodedata.category(c)[0] in "LN" for c in value):
        raise _bad("name", "no_letter")
    return value


def name_key(name):
    """The uniqueness key: case-folded and NFC, so 'Straße' and 'STRASSE' are one name."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", name).casefold())


def schema(conn):
    """Columns added after slice 1 shipped; the backfill keeps stored names unique by key."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cloud_workspaces)")}
    if "name_key" not in columns:
        conn.execute("ALTER TABLE cloud_workspaces ADD COLUMN name_key TEXT")
    if "request_id" not in columns:
        conn.execute("ALTER TABLE cloud_workspaces ADD COLUMN request_id TEXT")
    if "request_hash" not in columns:
        conn.execute("ALTER TABLE cloud_workspaces ADD COLUMN request_hash TEXT")
    pending = conn.execute("SELECT id,name FROM cloud_workspaces WHERE name_key IS NULL").fetchall()
    if pending:
        taken = {row[0] for row in conn.execute("SELECT name_key FROM cloud_workspaces") if row[0]}
        for row in pending:
            key = name_key(row["name"])
            if key in taken:
                key = f"{key}\x00{row['id']}"
            taken.add(key)
            conn.execute("UPDATE cloud_workspaces SET name_key=? WHERE id=?", (key, row["id"]))
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS cloud_workspaces_name_key ON cloud_workspaces(name_key)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS cloud_workspaces_request ON cloud_workspaces(request_id) "
        "WHERE request_id IS NOT NULL"
    )


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
    if value and not REGISTRY_LOGINS:
        raise _bad("private_registry", "unavailable")
    return _PRIVATE_FLAG[value]


def _revision(value):
    try:
        return positive_integer(value)
    except Exception:
        raise _bad("expected_revision", "invalid") from None


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
    """The one status code and reason the record shows; never blank.

    ``restart_needed`` is its own status so a started workspace whose saved settings moved
    on is never described as matching them; ``ready`` means the applied revision is current.
    """
    private = bool(row["private_registry"])
    if row["node_id"] is None:
        return "no_machine", None
    if private and row["registry_credential_id"] is None:
        return "needs_credential", None
    if row["state"] == "failed":
        return "failed", row["state_reason"] if row["state_reason"] in FAILURE_REASONS else None
    if row["applied_revision"] is not None and row["applied_revision"] < row["revision"]:
        return "restart_needed", None
    if row["state"] == "ready":
        return "ready", None
    return "pending", None


def public(conn, row, *, manage):
    """Explicit projection. A member sees neither a machine name nor an id, and nobody gets a
    registry login reference back (none can be stored yet)."""
    parsed = parse_image(row["image_reference"], strict=False)
    status, reason = status_of(row)
    started = row["applied_revision"] is not None
    return {
        "id": row["id"],
        "name": row["name"],
        "image_source": row["image_source"],
        "image": row["image_reference"],
        "image_registry": parsed["registry"],
        "image_tag": parsed["tag"],
        "image_digest": parsed["digest"],
        "image_pinned": parsed["pinned"],
        "curated_label": (_CURATED_BY_REFERENCE.get(row["image_reference"]) or {}).get("label")
        if row["image_source"] == "curated"
        else None,
        "private_registry": bool(row["private_registry"]),
        "credential_bound": row["registry_credential_id"] is not None,
        "cpu_cores": row["cpu_millicores"] / 1000,
        "memory_mib": row["memory_mib"],
        "storage_gib": row["storage_gib"],
        "network": row["network"],
        "node_id": row["node_id"] if manage else None,
        "machine_name": _machine_name(conn, row["node_id"]) if manage else None,
        "machine_assigned": row["node_id"] is not None,
        "status": status,
        "status_reason": reason,
        "started": started,
        "revision": row["revision"],
        "applied_revision": row["applied_revision"],
        "restart_needed": started and row["applied_revision"] < row["revision"],
        "discardable": manage and not started and row["state"] == "pending",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def options():
    """Static form facts: curated images, ranges, defaults, networks, caps."""
    return {
        "images": [dict(item) for item in CURATED_IMAGES],
        "limits": {key: dict(value) for key, value in LIMITS.items()},
        "networks": list(NETWORKS),
        "default_network": DEFAULT_NETWORK,
        "name_limit": NAME_LIMIT,
        "max_workspaces": MAX_WORKSPACES,
        "registry_logins": REGISTRY_LOGINS,
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
            "total": conn.execute("SELECT count(*) FROM cloud_workspaces").fetchone()[0],
            "can_manage": manage,
        }


def get_workspace(store, principal, workspace_id, *, session_hash):
    with store._transaction(principal, session_hash, member=True) as (conn, current):
        row = _row(conn, workspace_id)
        return {
            "workspace": public(conn, row, manage=current.role == "owner"),
            "can_manage": current.role == "owner",
        }


def _taken(conn, key, except_id=""):
    found = conn.execute(
        "SELECT 1 FROM cloud_workspaces WHERE name_key=? AND id<>?", (key, except_id)
    ).fetchone()
    if found is not None:
        raise _conflict("name_taken", "name")


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
    request_id=None,
):
    """Create a record; returns ``(item, created)``. A replayed ``request_id`` with the same
    values returns the earlier record (``created`` False), so a retry after a lost answer
    never trips over its own name."""
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
        if request_id is not None and (
            type(request_id) is not str or not _REQUEST_ID.fullmatch(request_id)
        ):
            raise _bad("request_id", "invalid")
        digest = hashlib.sha256(
            json.dumps(
                [name, image_source, image, millicores, memory, storage, network, node_id, private]
            ).encode()
        ).hexdigest()
        if request_id is not None:
            prior = conn.execute(
                "SELECT * FROM cloud_workspaces WHERE request_id=?", (request_id,)
            ).fetchone()
            if prior is not None:
                if prior["request_hash"] != digest:
                    raise _conflict("request_reused", "request_id")
                return public(conn, prior, manage=True), False
        key = name_key(name)
        _taken(conn, key)
        if conn.execute("SELECT count(*) FROM cloud_workspaces").fetchone()[0] >= MAX_WORKSPACES:
            raise _conflict("limit_reached")
        workspace_id = uuid.uuid4().hex
        now = store._now()
        conn.execute(
            "INSERT INTO cloud_workspaces (id,name,name_key,request_id,request_hash,node_id,"
            "image_source,image_reference,private_registry,registry_credential_id,"
            "registry_credential_version,cpu_millicores,memory_mib,storage_gib,network,state,"
            "state_reason,revision,applied_revision,created_by,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL,?,?,?,?,'pending',NULL,1,NULL,?,?,?)",
            (
                workspace_id,
                name,
                key,
                request_id,
                digest,
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
        return public(conn, _row(conn, workspace_id), manage=True), True


_EDITABLE = frozenset(
    "name image_source image cpu_cores memory_mib storage_gib network node_id private_registry".split()
)


def update_workspace(store, principal, workspace_id, *, session_hash, expected_revision, **changes):
    with store._transaction(principal, session_hash) as (conn, current):
        row = _row(conn, workspace_id)
        _revision(expected_revision)
        if set(changes) - _EDITABLE:
            raise _bad(None, "unknown_field")
        if not changes:
            raise _bad(None, "no_changes")
        if expected_revision != row["revision"]:
            raise _conflict("stale_revision")
        values = dict(row)
        if "name" in changes:
            values["name"] = _name(changes["name"])
            values["name_key"] = name_key(values["name"])
            _taken(conn, values["name_key"], workspace_id)
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
            # A record that already says it needs a login may keep saying so; none can start saying it.
            if changes["private_registry"] is True and row["private_registry"]:
                values["private_registry"] = 1
            else:
                values["private_registry"] = _flag(changes["private_registry"])
        if "node_id" in changes:
            _machine(conn, changes["node_id"])
            if row["applied_revision"] is not None and changes["node_id"] != row["node_id"]:
                raise _conflict("machine_locked", "node_id")
            values["node_id"] = changes["node_id"]
        # A registry login belongs to one registry; changing the image or the private flag drops it.
        before = parse_image(row["image_reference"], strict=False)["registry"]
        after = parse_image(reference, strict=False)["registry"]
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
            "UPDATE cloud_workspaces SET name=?,name_key=?,node_id=?,image_source=?,"
            "image_reference=?,private_registry=?,registry_credential_id=?,"
            "registry_credential_version=?,cpu_millicores=?,memory_mib=?,storage_gib=?,network=?,"
            "revision=?,updated_at=? WHERE id=?",
            (
                values["name"],
                values["name_key"] or name_key(values["name"]),
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


def bind_registry_credential(store, principal, workspace_id, *, session_hash):
    """Registry logins cannot be stored yet: no custody purpose exists for them, and nothing
    would use one before the lifecycle exists. Refused after the owner and concealment checks, writing nothing,
    so a gateway or target credential can never be filed here as a registry login."""
    with store._transaction(principal, session_hash) as (conn, current):
        _row(conn, workspace_id)
        raise _conflict("registry_logins_unavailable")


def discard_workspace(store, principal, workspace_id, *, session_hash, expected_revision):
    """Delete a record that never started. A started workspace owns a container and a volume,
    so removing it waits for the lifecycle card, which confirms and cleans those up."""
    with store._transaction(principal, session_hash) as (conn, current):
        row = _row(conn, workspace_id)
        _revision(expected_revision)
        if row["applied_revision"] is not None or row["state"] != "pending":
            raise _conflict("already_started")
        if expected_revision != row["revision"]:
            raise _conflict("stale_revision")
        conn.execute("DELETE FROM cloud_workspaces WHERE id=?", (workspace_id,))
        store._audit(conn, current, workspace_id, "workspace.discarded")
        return {"id": workspace_id, "name": row["name"]}


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
