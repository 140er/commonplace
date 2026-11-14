"""Immutable workspace.script.v1 bundles; no authority is granted by a value.

Integration API: ScriptBinding uses existing controller identities/enrollment;
ScriptManifest.create(binding, bundle_id, script, ...) preserves exact UTF-8 bytes.
ScriptBundleStore(path, workspace=..., authorize=...) is lazy, uses workspace
SQLCipher discovery, and calls authorize(context, binding, action) on every read
or write. The callback must check the real current owner/task/object authority.
No identity/task registry or execution side effect is introduced here.

ScriptRequest.digest is the complete *dispatch* digest, including the fresh
attempt/epoch and exact consumed consent. Logical proposal bindings explicitly
use authority_epoch=None: they never pretend to snapshot a future global epoch.
Compute approves its immutable proposal (including the complete manifest), then
binds that consent into this fresh request and its one-use outer SSH permit.
Legacy integer-epoch manifests retain their original wire/digest, and cannot be
upgraded into the logical proposal mode. No approved manifest is ever rewritten.
Public projections must use project_result, never log bundles/raw remote bytes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from commonplace import redact
from commonplace.admission import GATE
from mempalace.sqlite_crypto import connect

from .protocol import ProtocolDenied, canonical, exact_fields, finite, identifier

OPERATION = "workspace.script.v1"
CONTROLLER_CONTRACT = "controller-ssh-permit-v1"
MAX_SCRIPT_BYTES = 32768
MAX_BUNDLES = 256
MAX_LIST = 100
MAX_MANIFEST_BYTES = 6000
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_PART = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\Z")


class ScriptDenied(ProtocolDenied):
    """Fixed code only; never include script, output, paths or exception text."""


def digest(value):
    if type(value) is not str or not _HEX.fullmatch(value):
        raise ScriptDenied("invalid_digest")
    return value


def bounded_integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ScriptDenied("invalid_limit")
    return value


def relative_path(value):
    """Portable, literal relative filename; reserved job paths are excluded."""
    if type(value) is not str or len(value) > 192:
        raise ScriptDenied("invalid_path")
    parts = value.split("/")
    if not 1 <= len(parts) <= 4 or any(
        not _PART.fullmatch(part) or part in {".", ".."} for part in parts
    ):
        raise ScriptDenied("invalid_path")
    if parts[0] in {"script.py", ".compute"}:
        raise ScriptDenied("invalid_path")
    return value


def script_bytes(value):
    if type(value) is not bytes or not 1 <= len(value) <= MAX_SCRIPT_BYTES or b"\x00" in value:
        raise ScriptDenied("invalid_script")
    try:
        value.decode("utf-8", errors="strict")
    except UnicodeError:
        raise ScriptDenied("invalid_script") from None
    return value


@dataclass(frozen=True)
class ScriptBinding:
    workspace: str
    project: str
    user: str
    agent: str
    task: str
    gateway: str
    node: str
    enrollment: str
    gateway_epoch: int = 1
    node_epoch: int = 1
    gateway_credential_version: int = 1
    target_credential_version: int = 1
    authority_epoch: int | None = 1

    def __post_init__(self):
        for name in ("workspace", "project", "user", "agent", "task", "gateway", "node"):
            identifier(getattr(self, name))
        digest(self.enrollment)
        for name in (
            "gateway_epoch",
            "node_epoch",
            "gateway_credential_version",
            "target_credential_version",
        ):
            bounded_integer(getattr(self, name), 1, 2**31 - 1)
        if self.authority_epoch is not None:
            bounded_integer(self.authority_epoch, 1, 2**31 - 1)

    @classmethod
    def from_dict(cls, value):
        exact_fields(value, cls.__dataclass_fields__)
        return cls(**value)


@dataclass(frozen=True)
class ScriptLimits:
    wall_seconds: int = 10
    cpu_seconds: int = 5
    memory_bytes: int = 134217728
    processes: int = 16
    file_bytes: int = 1048576
    output_bytes: int = 4096
    artifacts_bytes: int = 65536
    events: int = 32

    def __post_init__(self):
        for name, low, high in (
            ("wall_seconds", 1, 30),
            ("cpu_seconds", 1, 15),
            ("memory_bytes", 16777216, 268435456),
            ("processes", 4, 32),
            ("file_bytes", 1, 1048576),
            ("output_bytes", 1, 4096),
            ("artifacts_bytes", 1, 65536),
            ("events", 1, 32),
        ):
            bounded_integer(getattr(self, name), low, high)

    @classmethod
    def from_dict(cls, value):
        exact_fields(value, cls.__dataclass_fields__)
        return cls(**value)


@dataclass(frozen=True)
class ScriptInput:
    path: str
    sha256: str
    size: int

    def __post_init__(self):
        relative_path(self.path)
        digest(self.sha256)
        bounded_integer(self.size, 0, 65536)

    @classmethod
    def from_dict(cls, value):
        exact_fields(value, cls.__dataclass_fields__)
        return cls(**value)


@dataclass(frozen=True, repr=False)
class ScriptManifest:
    binding: ScriptBinding
    bundle_id: str
    script_sha256: str
    script_size: int
    runtime_digest: str
    arguments: tuple[str, ...] = field(default=(), repr=False)
    input_paths: tuple[ScriptInput, ...] = ()
    output_paths: tuple[str, ...] = ()
    limits: ScriptLimits = field(default_factory=ScriptLimits)
    runtime: str = "python3.v1"
    privilege: str = "unprivileged"
    operation: str = OPERATION

    def __repr__(self):
        return "<ScriptManifest redacted>"

    def __post_init__(self):
        if not isinstance(self.binding, ScriptBinding) or not isinstance(self.limits, ScriptLimits):
            raise ScriptDenied("invalid_manifest")
        identifier(self.bundle_id)
        digest(self.script_sha256)
        digest(self.runtime_digest)
        bounded_integer(self.script_size, 1, MAX_SCRIPT_BYTES)
        if (self.runtime, self.privilege, self.operation) != (
            "python3.v1",
            "unprivileged",
            OPERATION,
        ):
            raise ScriptDenied("unsupported_operation")
        if type(self.arguments) not in (tuple, list) or len(self.arguments) > 16:
            raise ScriptDenied("invalid_arguments")
        for value in self.arguments:
            if type(value) is not str or "\x00" in value:
                raise ScriptDenied("invalid_arguments")
            try:
                if len(value.encode("utf-8")) > 256:
                    raise ScriptDenied("invalid_arguments")
            except UnicodeError:
                raise ScriptDenied("invalid_arguments") from None
        for name in ("input_paths", "output_paths"):
            values = getattr(self, name)
            if type(values) not in (tuple, list) or len(values) > 8:
                raise ScriptDenied("invalid_path")
            object.__setattr__(self, name, tuple(values))
        if any(not isinstance(x, ScriptInput) for x in self.input_paths):
            raise ScriptDenied("invalid_path")
        paths = [x.path for x in self.input_paths] + list(self.output_paths)
        for path in paths:
            relative_path(path)
        if len(set(paths)) != len(paths) or any(
            a.startswith(b + "/") for a in paths for b in paths if a != b
        ):
            raise ScriptDenied("path_overlap")
        object.__setattr__(self, "arguments", tuple(self.arguments))
        if len(canonical(self.document())) > MAX_MANIFEST_BYTES:
            raise ScriptDenied("manifest_too_large")

    def document(self):
        return asdict(self)

    @property
    def digest(self):
        return hashlib.sha256(canonical(self.document())).hexdigest()

    def verify(self, script):
        script_bytes(script)
        if len(script) != self.script_size or not hmac.compare_digest(
            hashlib.sha256(script).hexdigest(), self.script_sha256
        ):
            raise ScriptDenied("script_digest_mismatch")

    @classmethod
    def create(cls, binding, bundle_id, script, *, runtime_digest, **kwargs):
        script_bytes(script)
        return cls(
            binding=binding,
            bundle_id=bundle_id,
            script_sha256=hashlib.sha256(script).hexdigest(),
            script_size=len(script),
            runtime_digest=runtime_digest,
            **kwargs,
        )

    @classmethod
    def from_dict(cls, value):
        exact_fields(value, cls.__dataclass_fields__)
        fields = dict(value)
        fields["binding"] = ScriptBinding.from_dict(fields["binding"])
        fields["limits"] = ScriptLimits.from_dict(fields["limits"])
        if type(fields["input_paths"]) not in (tuple, list) or len(fields["input_paths"]) > 8:
            raise ScriptDenied("invalid_path")
        fields["input_paths"] = tuple(ScriptInput.from_dict(x) for x in fields["input_paths"])
        return cls(**fields)


@dataclass(frozen=True, repr=False)
class ScriptRequest:
    execution: str
    attempt: str
    manifest: ScriptManifest
    expires_at: float
    controller_contract: str = CONTROLLER_CONTRACT
    authority_epoch: int | None = None
    approval_digest: str | None = None

    def __repr__(self):
        return "<ScriptRequest redacted>"

    def __post_init__(self):
        identifier(self.execution)
        identifier(self.attempt)
        finite(self.expires_at)
        if not isinstance(self.manifest, ScriptManifest):
            raise ScriptDenied("invalid_manifest")
        if self.controller_contract != CONTROLLER_CONTRACT:
            raise ScriptDenied("unsupported_admission_mode")
        if self.manifest.binding.authority_epoch is None:
            bounded_integer(self.authority_epoch, 1, 2**63 - 1)
            digest(self.approval_digest)
        elif self.authority_epoch is not None or self.approval_digest is not None:
            raise ScriptDenied("binding_mismatch")

    def document(self):
        document = asdict(self)
        if self.manifest.binding.authority_epoch is not None:
            del document["authority_epoch"], document["approval_digest"]
        return document

    @property
    def digest(self):
        return hashlib.sha256(canonical(self.document())).hexdigest()

    @classmethod
    def from_dict(cls, value):
        fields = set(cls.__dataclass_fields__)
        if type(value) is dict and not ({"authority_epoch", "approval_digest"} & value.keys()):
            fields -= {"authority_epoch", "approval_digest"}
        exact_fields(value, fields)
        fields = dict(value)
        fields["manifest"] = ScriptManifest.from_dict(fields["manifest"])
        request = cls(**fields)
        if set(value) != set(request.document()):
            raise ScriptDenied("invalid_fields")
        return request


_SCHEMA = """
CREATE TABLE IF NOT EXISTS script_bundles_v1 (
 bundle_id TEXT PRIMARY KEY, workspace TEXT NOT NULL, manifest BLOB NOT NULL,
 digest TEXT NOT NULL, script BLOB NOT NULL,
 CHECK(length(manifest)<=6000), CHECK(length(script) BETWEEN 1 AND 32768)
);
CREATE TRIGGER IF NOT EXISTS script_bundles_v1_no_update
 BEFORE UPDATE ON script_bundles_v1 BEGIN SELECT RAISE(ABORT,'immutable bundle'); END;
CREATE TRIGGER IF NOT EXISTS script_bundles_v1_no_delete
 BEFORE DELETE ON script_bundles_v1 BEGIN SELECT RAISE(ABORT,'immutable bundle'); END;
"""


def _stored_manifest(raw, expected_digest):
    try:
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ScriptDenied("bundle_unavailable")
        manifest = ScriptManifest.from_dict(json.loads(bytes(raw)))
        if manifest.digest != expected_digest:
            raise ScriptDenied("bundle_unavailable")
        return manifest
    except Exception:
        raise ScriptDenied("bundle_unavailable") from None


class ScriptBundleStore:
    """Bounded verbatim storage; authorize(context,binding,action) must return True.

    Intended path: existing protected workspace/compute-scripts.sqlite3. Reusing
    sqlite_crypto.connect honors configured encryption and refuses locked keys.
    put(manifest,script,context=...) is exactly idempotent; get returns
    (manifest, original_bytes); list returns authorized manifests only, max100.
    The parent must place writes under its existing participating authority fence.
    """

    def __init__(self, path, *, workspace, authorize=None):
        self.path = Path(path)
        self.workspace = identifier(workspace)
        self.authorize = authorize

    def _allow(self, context, binding, action):
        if (
            not isinstance(binding, ScriptBinding)
            or binding.workspace != self.workspace
            or self.authorize is None
        ):
            raise ScriptDenied("script_authority_denied")
        try:
            allowed = self.authorize(context, binding, action) is True
        except Exception:
            raise ScriptDenied("script_authority_denied") from None
        if not allowed:
            raise ScriptDenied("script_authority_denied")

    @contextmanager
    def _transaction(self):
        conn = None
        try:
            with GATE.creating(self.path):
                conn = connect(str(self.path), timeout=0.25, isolation_level=None)
            conn.execute("PRAGMA synchronous=FULL")
            conn.executescript(_SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception as error:
            if conn is not None:
                conn.rollback()
            if isinstance(error, ProtocolDenied):
                raise
            raise ScriptDenied("bundle_unavailable") from None
        finally:
            if conn is not None:
                conn.close()

    def put(self, manifest, script, *, context):
        if not isinstance(manifest, ScriptManifest):
            raise ScriptDenied("invalid_manifest")
        self._allow(context, manifest.binding, "put")
        manifest.verify(script)
        raw = canonical(manifest.document())
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT manifest,script FROM script_bundles_v1 WHERE bundle_id=?",
                (manifest.bundle_id,),
            ).fetchone()
            if row is not None:
                if bytes(row[0]) != raw or bytes(row[1]) != script:
                    raise ScriptDenied("bundle_conflict")
                return manifest
            if conn.execute("SELECT count(*) FROM script_bundles_v1").fetchone()[0] >= MAX_BUNDLES:
                raise ScriptDenied("bundle_capacity")
            conn.execute(
                "INSERT INTO script_bundles_v1 VALUES(?,?,?,?,?)",
                (
                    manifest.bundle_id,
                    self.workspace,
                    raw,
                    manifest.digest,
                    script,
                ),
            )
        return manifest

    def get(self, bundle_id, *, binding, context):
        identifier(bundle_id)
        self._allow(context, binding, "get")
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT manifest,digest,script FROM script_bundles_v1 WHERE bundle_id=? AND workspace=?",
                (bundle_id, self.workspace),
            ).fetchone()
        if row is None:
            raise ScriptDenied("bundle_not_found")
        manifest = _stored_manifest(row[0], row[1])
        if manifest.binding != binding:
            raise ScriptDenied("bundle_not_found")
        script = bytes(row[2])
        manifest.verify(script)
        return manifest, script

    def read(self, bundle_id, *, context):
        """Read by immutable identity, authorizing the actual stored audience.

        Unlike get(binding=...), this supports old private object lookup after
        unrelated dispatch epochs change. It still rechecks current authority.
        """
        identifier(bundle_id)
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT manifest,digest,script FROM script_bundles_v1 WHERE bundle_id=? AND workspace=?",
                (bundle_id, self.workspace),
            ).fetchone()
        if row is None:
            raise ScriptDenied("bundle_not_found")
        manifest = _stored_manifest(row[0], row[1])
        self._allow(context, manifest.binding, "get")
        script = bytes(row[2])
        manifest.verify(script)
        return manifest, script

    def list(self, *, binding, context, limit=MAX_LIST):
        bounded_integer(limit, 1, MAX_LIST)
        self._allow(context, binding, "list")
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT manifest,digest FROM script_bundles_v1 WHERE workspace=? ORDER BY bundle_id LIMIT ?",
                (self.workspace, MAX_BUNDLES),
            ).fetchall()
        result = []
        for row in rows:
            manifest = _stored_manifest(row[0], row[1])
            if manifest.binding == binding:
                result.append(manifest)
                if len(result) >= limit:
                    break
        return tuple(result)


def project_result(result):
    """Controller-only bounded credential redaction; caller checks object audience.

    Never redact individual output chunks: a credential may span chunks. Oversize
    or binary fields are omitted whole. Original scripts stay verbatim in storage.
    """
    projected = {
        name: getattr(result, name)
        for name in (
            "state",
            "code",
            "exit_code",
            "cancel_requested",
            "termination_confirmed",
        )
    }
    for name in ("stdout", "stderr"):
        raw = getattr(result, name)
        try:
            text = raw.decode("utf-8", errors="strict") if len(raw) <= 4096 else None
        except UnicodeError:
            text = None
        projected[name] = redact.redact(text)[0] if text is not None else "[omitted:too_long]"
    projected["artifacts"] = [
        {
            "path": redact.redact(path)[0],
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        for path, data in result.artifacts
    ]
    return projected
