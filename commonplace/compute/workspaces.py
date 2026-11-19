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
import ipaddress
import json
import math
import re
import sqlite3
import unicodedata
import uuid

from .control import StoreError
from .protocol import ProtocolDenied, identifier, positive_integer

# One cap for creation and for the list, so a record made through the app is always listed. Records that
# already exist beyond it (older data, or written directly) are counted: the list shows the newest
# MAX_LIST and the response carries ``total``, which the app turns into "Showing the newest N of M".
MAX_WORKSPACES = 100
MAX_LIST = MAX_WORKSPACES
# Bumped with every migration step in ``schema``; ``PRAGMA user_version`` records the one already done.
# 3: the uniqueness key became NFKC-casefold, so every stored key is recomputed once.
SCHEMA_VERSION = 3
NAME_LIMIT = 60  # code points, counted after NFC normalisation (the browser counts the same way)

# Ranges and defaults, served to the form so the browser never hard-codes a second copy.
LIMITS = {
    "cpu_cores": {"min": 0.5, "max": 32, "step": 0.5, "default": 2},
    "memory_mib": {"min": 512, "max": 131072, "step": 128, "default": 4096},
    "storage_gib": {"min": 1, "max": 2048, "step": 1, "default": 20},
}
NETWORKS = ("outbound", "none")
DEFAULT_NETWORK = "outbound"
# Starting points the form offers; each stays editable and sits inside LIMITS (a test pins that).
PRESETS = (
    {"id": "small", "label": "Small", "cpu_cores": 1, "memory_mib": 2048, "storage_gib": 10},
    {"id": "standard", "label": "Standard", "cpu_cores": 2, "memory_mib": 4096, "storage_gib": 20},
    {"id": "large", "label": "Large", "cpu_cores": 4, "memory_mib": 8192, "storage_gib": 50},
)

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
    ".localdomain",
    ".svc",
    ".ts.net",
    ".consul",
)
_DOTTED_QUAD = re.compile(r"(?:0|[1-9][0-9]{0,2})(?:\.(?:0|[1-9][0-9]{0,2})){3}", re.ASCII)
# Not public: "this" network, private, shared, loopback, link-local, protocol and documentation blocks,
# benchmarking, multicast and everything reserved above it (the browser rule carries the same list).
_NON_PUBLIC = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/3",
    )
)
# "localhost4" and "localdomain4" are the usual resolver names for the loopback host and its domain.
_LOCAL_LABEL = re.compile(r"(?:localhost|localdomain)[0-9]*", re.ASCII)
# Registry logins need a custody purpose and pull-time use that arrive with the start/stop lifecycle, so no
# record may claim to need one yet (docs/system/compute-workspaces.md, "Registry logins").
REGISTRY_LOGINS = False
_BAD_NAME_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
# Characters that draw nothing (or only change how a neighbour is drawn) but are letters, marks or symbols
# to Unicode, so the category check above cannot catch them: Hangul and braille fillers, Khmer inherent
# vowels, the combining grapheme joiner and the variation selectors. Any space but U+0020 is refused too.
_HIDDEN_NAME = re.compile(
    "[\u115f\u1160\u17b4\u17b5\u2800\u3164\uffa0\u034f\u180b-\u180f\ufe00-\ufe0f"
    "\U000e0100-\U000e01ef]"
)


def refuse(field, reason, code="invalid_request"):
    """A refusal that names its reason and, when it is about one form field, the field."""
    return StoreError(code, 400 if code == "invalid_request" else 409, field=field, reason=reason)


def _hidden(char):
    return (
        unicodedata.category(char) in _BAD_NAME_CATEGORIES
        or _HIDDEN_NAME.match(char) is not None
        or (char != " " and unicodedata.category(char) == "Zs")
    )


def _conflict(reason, field=None):
    return StoreError("conflict", 409, field=field, reason=reason)


def _host_refusal(host):
    """The reason a registry host is refused, or None.

    ``registry_private``: a name or address on a local or private network. ``registry_ip``: a public
    IPv4 address in plain dotted form, which is refused too (a registry has a name, and a pull by
    address cannot be checked here) but for a reason that says so. Any other numeric notation
    (``0x7f.0.0.1``, ``8.8.8``) could hide a private address and counts as private.
    """
    name = host.partition(":")[0]
    if (
        "." not in name
        or name.endswith(_LOCAL_SUFFIXES)
        or any(_LOCAL_LABEL.fullmatch(label) for label in name.split("."))
    ):
        return "registry_private"
    if _NUMERIC_LABEL.fullmatch(name.rsplit(".", 1)[-1]) is None:
        return None
    if not _DOTTED_QUAD.fullmatch(name) or any(int(part) > 255 for part in name.split(".")):
        return "registry_private"
    address = ipaddress.IPv4Address(name)
    return "registry_private" if any(address in net for net in _NON_PUBLIC) else "registry_ip"


def parse_image(value, *, strict=True):
    """Validate a container image reference and return its parts, or raise StoreError.

    Accepts ``[registry[:port]/]path[:tag][@sha256:<64 hex>]``. Anything that could act as
    an option, a shell word, a URL or user info is refused before it can be stored. ``strict``
    also refuses registries on local or private networks and a registry without a path; it is
    off only when describing a record that was stored before those rules.
    """
    if type(value) is not str or not value:
        raise refuse("image", "empty")
    if len(value) > 255:
        raise refuse("image", "too_long")
    if "://" in value:
        raise refuse("image", "scheme")
    if not _SAFE.fullmatch(value) or value.startswith(("-", "/", ".")):
        raise refuse("image", "characters")
    name, at, digest = value.partition("@")
    if at and ("/" in digest or "." in digest):
        # "user:pass@ghcr.io/app": a login in the address is not a digest, and it must never be stored.
        raise refuse("image", "user_info")
    if at and (not _DIGEST.fullmatch(digest) or "@" in digest):
        raise refuse("image", "digest")
    if "@" in name:
        raise refuse("image", "characters")
    tag = None
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        name, _, tag = name.rpartition(":")
        if not _TAG.fullmatch(tag):
            raise refuse("image", "tag")
    parts = name.split("/")
    registry = "docker.io"
    first = parts[0]
    if strict and len(parts) == 1 and "." in first:
        # "ghcr.io:443" would otherwise read as an image called ghcr.io with tag 443.
        raise refuse("image", "registry_no_path")
    if len(parts) > 1 and ("." in first or ":" in first or first == "localhost"):
        match = _HOST.fullmatch(first)
        if match is None or (match.group(1) is not None and not 1 <= int(match.group(1)) <= 65535):
            raise refuse("image", "registry")
        if strict and (refusal := _host_refusal(first)):
            raise refuse("image", refusal)
        registry = first
        parts = parts[1:]
    if not parts or any(not _COMPONENT.fullmatch(part) for part in parts):
        raise refuse("image", "name")
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
        raise refuse("name", "length")
    value = unicodedata.normalize("NFC", value)
    if not value.strip() or len(value) > NAME_LIMIT:
        raise refuse("name", "length")
    # Control, format (zero-width and bidi controls), surrogate, private, unassigned, separators and the
    # fillers and selectors above can hide or reorder text; none belongs in a name.
    if value != value.strip() or any(_hidden(c) for c in value):
        raise refuse("name", "characters")
    if not any(unicodedata.category(c)[0] in "LN" for c in value):
        raise refuse("name", "no_letter")
    return value


def name_key(name):
    """The uniqueness key: NFKC and case-folded, so 'Straße' and 'STRASSE', 'Ａ' and 'A', '①' and '1' are one name.

    Case folding can produce characters that compose or fold again, so the two steps run twice (the
    Unicode NFKC_Casefold recipe); a test pins that the key is a fixed point.
    """
    key = name
    for _ in range(2):
        key = unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", key).casefold())
    return key


def _migrate(conn):
    """Every step of the migration; the caller owns the transaction, so all of it or none of it applies."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cloud_workspaces)")}
    if "name_key" not in columns:
        conn.execute("ALTER TABLE cloud_workspaces ADD COLUMN name_key TEXT")
    if "request_id" not in columns:
        conn.execute("ALTER TABLE cloud_workspaces ADD COLUMN request_id TEXT")
    if "request_hash" not in columns:
        conn.execute("ALTER TABLE cloud_workspaces ADD COLUMN request_hash TEXT")
    if "version" not in columns:
        # ``version`` counts every accepted edit (a rename too) and is what ``expected_revision`` is checked
        # against; ``revision`` still counts only the edits that need a restart.
        conn.execute("ALTER TABLE cloud_workspaces ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
        conn.execute("UPDATE cloud_workspaces SET version=revision")
    # Recompute every key (older rows have none, or a weaker one). The oldest record keeps the plain key;
    # a later record whose key collides is kept apart by its id. Keys are parked first so the unique
    # index never sees two rows holding the same key part-way through.
    rows = conn.execute("SELECT id,name FROM cloud_workspaces ORDER BY created_at,id").fetchall()
    conn.execute("DROP INDEX IF EXISTS cloud_workspaces_name_key")
    taken = set()
    for row in rows:
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
    # A discarded record keeps its request id here for good, so replaying the create that made it can
    # never bring the record back.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cloud_workspace_tombstones ("
        "request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, discarded_at REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS cloud_workspace_tombstones_no_delete "
        "BEFORE DELETE ON cloud_workspace_tombstones BEGIN "
        "SELECT RAISE(ABORT,'workspace tombstones cannot be deleted'); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS cloud_workspace_tombstones_no_update "
        "BEFORE UPDATE ON cloud_workspace_tombstones BEGIN "
        "SELECT RAISE(ABORT,'workspace tombstones cannot be changed'); END"
    )
    # Last, so a database is never marked done with a step missing (the version is part of the transaction).
    conn.execute("PRAGMA user_version=" + str(SCHEMA_VERSION))  # an integer constant, never input


def schema(conn):
    """Columns, keys and tables added after slice 1 shipped.

    Runs once per database: ``PRAGMA user_version`` records the step already done, so an ordinary
    Compute transaction pays for one read instead of a table scan. A migration is one transaction:
    when the caller has none open it takes ``BEGIN IMMEDIATE`` itself and re-reads the version under
    that lock, so two processes never both migrate and a failure part-way leaves nothing changed.
    """
    if conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
        return
    if conn.in_transaction:
        _migrate(conn)
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
            _migrate(conn)
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def _cpu(value):
    spec = LIMITS["cpu_cores"]
    if type(value) not in (int, float):
        raise refuse("cpu_cores", "not_a_number")
    # Range first: math.isfinite(10**400) raises OverflowError, and a huge integer is simply out of range.
    if (type(value) is float and not math.isfinite(value)) or not (
        spec["min"] <= value <= spec["max"]
    ):
        raise refuse("cpu_cores", "range")
    if (value * 2) % 1:
        raise refuse("cpu_cores", "step")
    return int(round(value * 1000))


def _whole(field, value):
    spec = LIMITS[field]
    if type(value) is not int:
        raise refuse(field, "not_a_number")
    if not spec["min"] <= value <= spec["max"]:
        raise refuse(field, "range")
    if value % spec["step"]:
        raise refuse(field, "step")
    return value


def _image(source, reference):
    if source not in ("curated", "custom"):
        raise refuse("image_source", "choice")
    parsed = parse_image(reference)
    if source == "curated" and reference not in _CURATED_BY_REFERENCE:
        raise refuse("image", "not_curated")
    return parsed


def _flag(value):
    if type(value) is not bool:
        raise refuse("private_registry", "choice")
    if value and not REGISTRY_LOGINS:
        raise refuse("private_registry", "unavailable")
    return int(value)


def _revision(value):
    try:
        return positive_integer(value)
    except Exception:
        raise refuse("expected_revision", "invalid") from None


# The one query every read goes through: a record with its machine's metadata (a JOIN, never a lookup per row).
_SELECT = (
    "SELECT w.*, n.metadata AS node_metadata, l.phase AS lifecycle_phase FROM cloud_workspaces w "
    "LEFT JOIN nodes n ON n.id=w.node_id "
    "LEFT JOIN cloud_workspace_lifecycle l ON l.workspace_id=w.id"
)


def _rows(conn, where="", params=(), tail=""):
    """Records through ``_SELECT``; ``where`` and ``tail`` are fixed SQL text from this module, values go in ``params``."""
    return conn.execute(_SELECT + " " + where + " " + tail, params).fetchall()


def _row(conn, workspace_id):
    """One record with its machine's metadata."""
    try:
        identifier(workspace_id)
    except ProtocolDenied:
        raise refuse(None, "identifier") from None
    found = _rows(conn, "WHERE w.id=?", (workspace_id,))
    row = found[0] if found else None
    if row is None:
        raise StoreError("not_found", 404, reason="record")
    return row


def _machine(conn, node_id):
    if node_id is None:
        return None
    try:
        identifier(node_id)
    except Exception:
        raise refuse("node_id", "choice") from None
    row = conn.execute("SELECT id,metadata FROM nodes WHERE id=?", (node_id,)).fetchone()
    if row is None:
        raise StoreError("not_found", 404, field="node_id", reason="unknown_machine")
    return row


def _machine_name(row):
    return json.loads(row["node_metadata"]).get("name") if row["node_metadata"] else None


def has_started(row):
    """The one meaning of "started": the machine applied a revision, or a start was tried and recorded.

    A start that failed before anything was applied still counts: it may have left a container or a
    volume behind, so the machine stays locked, the record cannot be discarded and History says so.
    """
    # A lifecycle phase other than ``not_started`` (a start under way, a stop, a stopped workspace) counts too.
    # A row built without the lifecycle join simply has none.
    try:
        phase = row["lifecycle_phase"]
    except (KeyError, IndexError):
        phase = None
    return (
        row["applied_revision"] is not None
        or row["state"] != "pending"
        or phase not in (None, "not_started")
    )


def _restart_needed(row):
    return row["applied_revision"] is not None and row["applied_revision"] < row["revision"]


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
    if _restart_needed(row):
        return "restart_needed", None
    if row["state"] == "ready":
        return "ready", None
    return "pending", None


def public(row, *, manage):
    """Explicit projection. A member sees neither a machine name nor an id, and nobody gets a
    registry login reference back (none can be stored yet)."""
    try:
        parsed = parse_image(row["image_reference"], strict=False)
        unreadable = False
    except StoreError:
        # One record that cannot be read must not turn the whole list into an error: it is shown as a
        # fixed state of its own (no image, no parts) and the rest of the list stays.
        parsed = {"registry": None, "tag": None, "digest": None, "pinned": False}
        unreadable = True
    status, reason = status_of(row)
    started = has_started(row)
    return {
        "id": row["id"],
        "name": row["name"],
        "image_source": row["image_source"],
        "image": "" if unreadable else row["image_reference"],
        "image_unreadable": unreadable,
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
        "machine_name": _machine_name(row) if manage else None,
        "machine_assigned": row["node_id"] is not None,
        "status": status,
        "status_reason": reason,
        "started": started,
        "version": row["version"],
        "revision": row["revision"],
        "applied_revision": row["applied_revision"],
        "restart_needed": _restart_needed(row),
        "discardable": manage and not started,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def options():
    """Static form facts: curated images, ranges, defaults, networks, caps."""
    return {
        "images": [dict(item) for item in CURATED_IMAGES],
        "limits": {key: dict(value) for key, value in LIMITS.items()},
        "presets": [dict(item) for item in PRESETS],
        "networks": list(NETWORKS),
        "default_network": DEFAULT_NETWORK,
        "name_limit": NAME_LIMIT,
        "max_workspaces": MAX_WORKSPACES,
        "registry_logins": REGISTRY_LOGINS,
    }


def list_workspaces(store, principal, *, session_hash):
    with store._transaction(principal, session_hash, member=True) as (conn, current):
        manage = current.role == "owner"
        rows = _rows(conn, tail="ORDER BY w.created_at DESC,w.id DESC LIMIT ?", params=(MAX_LIST,))
        return {
            "workspaces": [public(row, manage=manage) for row in rows],
            "total": conn.execute("SELECT count(*) FROM cloud_workspaces").fetchone()[0],
            "can_manage": manage,
        }


def get_workspace(store, principal, workspace_id, *, session_hash):
    with store._transaction(principal, session_hash, member=True) as (conn, current):
        row = _row(conn, workspace_id)
        return {
            "workspace": public(row, manage=current.role == "owner"),
            "can_manage": current.role == "owner",
        }


def _taken(conn, key, except_id=""):
    """Refuse a name another record already has. The stored key of a record that was kept apart by the
    migration carries its id (``key\\x00id``), so every other record's name is folded again here: a
    duplicate is a duplicate whichever key it was stored under."""
    for row in conn.execute("SELECT name,name_key FROM cloud_workspaces WHERE id<>?", (except_id,)):
        if key in (row["name_key"], name_key(row["name"])):
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
            raise refuse("network", "choice")
        private = _flag(private_registry)
        _machine(conn, node_id)
        if request_id is not None and (
            type(request_id) is not str or not _REQUEST_ID.fullmatch(request_id)
        ):
            raise refuse("request_id", "invalid")
        digest = hashlib.sha256(
            json.dumps(
                [name, image_source, image, millicores, memory, storage, network, node_id, private]
            ).encode()
        ).hexdigest()
        if request_id is not None:
            if conn.execute(
                "SELECT 1 FROM cloud_workspace_tombstones WHERE request_id=?", (request_id,)
            ).fetchone():
                # The record this id made was discarded on purpose: a replay must not bring it back.
                raise _conflict("request_discarded", "request_id")
            found = _rows(conn, "WHERE w.request_id=?", (request_id,))
            prior = found[0] if found else None
            if prior is not None:
                if prior["request_hash"] != digest:
                    raise _conflict("request_reused", "request_id")
                return public(prior, manage=True), False
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
            "state_reason,revision,version,applied_revision,created_by,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL,?,?,?,?,'pending',NULL,1,1,NULL,?,?,?)",
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
        return public(_row(conn, workspace_id), manage=True), True


EDITABLE_FIELDS = frozenset(
    "name image_source image cpu_cores memory_mib storage_gib network node_id private_registry".split()
)


def update_workspace(store, principal, workspace_id, *, session_hash, expected_revision, **changes):
    with store._transaction(principal, session_hash) as (conn, current):
        row = _row(conn, workspace_id)
        _revision(expected_revision)
        if set(changes) - EDITABLE_FIELDS:
            raise refuse(None, "unknown_field")
        if not changes:
            raise refuse(None, "no_changes")
        # ``expected_revision`` is the record's ``version``: every accepted edit moved it, a rename included.
        if expected_revision != row["version"]:
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
                raise refuse("network", "choice")
            values["network"] = changes["network"]
        if "private_registry" in changes:
            # A record that already says it needs a login may keep saying so; none can start saying it.
            if changes["private_registry"] is True and row["private_registry"]:
                values["private_registry"] = 1
            else:
                values["private_registry"] = _flag(changes["private_registry"])
        if "node_id" in changes:
            _machine(conn, changes["node_id"])
            if has_started(row) and changes["node_id"] != row["node_id"]:
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
            return public(row, manage=True)
        runtime_changed = any(values[key] != row[key] for key in runtime)
        if runtime_changed:
            from .workspace_lifecycle import request_waiting

            # A start, stop or restart request names this machine and these settings: they stay as asked.
            if request_waiting(conn, workspace_id, store._now()):
                raise _conflict("request_pending")
        revision = row["revision"] + runtime_changed
        try:
            conn.execute(
                "UPDATE cloud_workspaces SET name=?,name_key=?,node_id=?,image_source=?,"
                "image_reference=?,private_registry=?,registry_credential_id=?,"
                "registry_credential_version=?,cpu_millicores=?,memory_mib=?,storage_gib=?,network=?,"
                "revision=?,version=?,updated_at=? WHERE id=?",
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
                    row["version"] + 1,
                    store._now(),
                    workspace_id,
                ),
            )
        except sqlite3.IntegrityError:
            # The name column is unique on its own; a collision the key check missed is still name_taken.
            raise _conflict("name_taken", "name") from None
        store._audit(conn, current, workspace_id, "workspace.updated")
        return public(_row(conn, workspace_id), manage=True)


def bind_registry_credential(store, principal, workspace_id, *, session_hash):
    """Registry logins cannot be stored yet: no custody purpose exists for them, and nothing
    would use one before the lifecycle exists. Refused after the owner and concealment checks, writing nothing,
    so a gateway or target credential can never be filed here as a registry login."""
    with store._transaction(principal, session_hash) as (conn, current):
        _row(conn, workspace_id)
        raise _conflict("registry_logins_unavailable")


def discard_workspace(store, principal, workspace_id, *, session_hash, expected_revision):
    """Delete a record that never started (``has_started``: a failed start counts). A started workspace
    owns a container and a volume, so removing it waits for the lifecycle card, which confirms and
    cleans those up. The create request id outlives the record in a tombstone."""
    with store._transaction(principal, session_hash) as (conn, current):
        row = _row(conn, workspace_id)
        _revision(expected_revision)
        if has_started(row):
            raise _conflict("already_started")
        if expected_revision != row["version"]:
            raise _conflict("stale_revision")
        if row["request_id"] is not None:
            conn.execute(
                "INSERT OR IGNORE INTO cloud_workspace_tombstones "
                "(request_id,request_hash,discarded_at) VALUES (?,?,?)",
                (row["request_id"], row["request_hash"] or "", store._now()),
            )
        conn.execute("DELETE FROM cloud_workspaces WHERE id=?", (workspace_id,))
        store._audit(conn, current, workspace_id, "workspace.discarded")
        return {"id": workspace_id, "name": row["name"]}


def set_state(conn, workspace_id, state, reason=None, *, applied_revision=None):
    """Lifecycle write for the later start/stop code; the REST surface never calls it.

    ``ready`` means the machine runs the saved settings, so it needs ``applied_revision`` and writes
    only when that covers the record's ``revision``, in the same UPDATE that sets the state: a stale
    or missing revision can never leave a record ready with settings the machine does not hold.
    """
    if state not in STATES or (state == "failed") != (reason is not None):
        raise ValueError("invalid_workspace_state")
    if reason is not None and reason not in FAILURE_REASONS:
        raise ValueError("invalid_workspace_state")
    if state == "ready":
        if applied_revision is None:
            raise ValueError("invalid_workspace_state")
        changed = conn.execute(
            "UPDATE cloud_workspaces SET state='ready',state_reason=NULL,applied_revision=? "
            "WHERE id=? AND revision<=?",
            (applied_revision, workspace_id, applied_revision),
        ).rowcount
        if changed != 1:
            raise ValueError("invalid_workspace_state")
    elif applied_revision is None:
        conn.execute(
            "UPDATE cloud_workspaces SET state=?,state_reason=? WHERE id=?",
            (state, reason, workspace_id),
        )
    else:
        conn.execute(
            "UPDATE cloud_workspaces SET state=?,state_reason=?,applied_revision=? WHERE id=?",
            (state, reason, applied_revision, workspace_id),
        )
