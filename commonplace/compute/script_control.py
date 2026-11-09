"""Owner script objects and independently attested, explicit target runtimes.

The existing Compute transaction/fence encloses local bundle writes. Bundles are
immutable in a separate crypto-aware database; references live with existing
canonical task/enrollment records. Runtime receipts extend the already guarded
enrollment_verifications row, using the existing pinned provisioning keys.
No public path reads a key, resolves a runtime filesystem or verifies signatures.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .control import StoreError
from .protocol import ADMISSION_MODE, canonical, enrollment_digest, finite, identifier
from .scripts import OPERATION, ScriptBinding, ScriptBundleStore, ScriptManifest, digest

SCRIPT_RUNTIME_CONTRACT = "confined-python3-v1/bwrap-userns-seccomp-cgroup2-tmpfs-v1"


@dataclass(frozen=True)
class ScriptRuntimeConfig:
    """Trusted startup target binding; paths and keys stay on the target host."""

    node_id: str
    runtime_digest: str
    key_principal: str
    contract: str = SCRIPT_RUNTIME_CONTRACT

    def __post_init__(self):
        identifier(self.node_id)
        digest(self.runtime_digest)
        digest(self.key_principal)
        if self.contract != SCRIPT_RUNTIME_CONTRACT:
            raise ValueError("unsupported_script_runtime")


@dataclass(frozen=True)
class ScriptRuntimeReceipt:
    """Offline verifier's evidence, never accepted by a management endpoint.

    The signer independently verifies protected target configuration, exclusive
    controller SSH key, exact runtime inventory and *actual* confinement support.
    A synthetic signed receipt proves controller wiring only.
    """

    key_id: str
    workspace: str
    gateway: str
    node: str
    enrollment: str
    enrollment_receipt: str
    runtime_digest: str
    key_principal: str
    verified_at: float
    expires_at: float
    signature: bytes
    contract: str = SCRIPT_RUNTIME_CONTRACT

    def signed_bytes(self):
        from .target_helper import TARGET_HELPER_COMMAND

        for name in ("key_id", "workspace", "gateway", "node"):
            identifier(getattr(self, name))
        for name in ("enrollment", "enrollment_receipt", "runtime_digest", "key_principal"):
            digest(getattr(self, name))
        finite(self.verified_at)
        finite(self.expires_at)
        if self.contract != SCRIPT_RUNTIME_CONTRACT:
            raise ValueError("unsupported_script_runtime")
        return canonical(
            {
                "v": 1,
                "purpose": "compute-confined-script-runtime",
                "operation": OPERATION,
                "runtime": "python3.v1",
                "privilege": "unprivileged",
                "controller_contract": ADMISSION_MODE,
                "target_helper_command": TARGET_HELPER_COMMAND,
                **{k: v for k, v in asdict(self).items() if k != "signature"},
            }
        )


def schema(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(enrollment_verifications)")}
    if "script_runtime" not in columns:
        conn.execute("ALTER TABLE enrollment_verifications ADD COLUMN script_runtime TEXT")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS script_bundle_refs (
          id TEXT PRIMARY KEY, node_id TEXT NOT NULL REFERENCES nodes(id),
          task_number INTEGER NOT NULL, user_id TEXT NOT NULL,
          request_id TEXT NOT NULL, request TEXT NOT NULL, digest TEXT NOT NULL,
          created_at REAL NOT NULL, UNIQUE(user_id,request_id)
        );
        CREATE TRIGGER IF NOT EXISTS script_bundle_refs_no_update
        BEFORE UPDATE ON script_bundle_refs BEGIN SELECT RAISE(ABORT,'immutable script'); END;
        CREATE TRIGGER IF NOT EXISTS script_bundle_refs_no_delete
        BEFORE DELETE ON script_bundle_refs BEGIN SELECT RAISE(ABORT,'immutable script'); END;
        """
    )


def _configured(store, node_id):
    from . import ExecutionConfig, WorkspaceExecutionConfig

    config = getattr(store.gateway.db, "_compute_config", None)
    if type(config) is ExecutionConfig and store._authority_fence is not None:
        for workspace in config.workspaces:
            if (
                type(workspace) is WorkspaceExecutionConfig
                and workspace.workspace == store.workspace_id
                and workspace.enabled is True
                and workspace.scripts_enabled is True
                and workspace.admission_mode == ADMISSION_MODE
            ):
                matches = [r for r in workspace.script_runtimes if r.node_id == node_id]
                if len(matches) == 1 and type(matches[0]) is ScriptRuntimeConfig:
                    return matches[0]
    raise StoreError("scripts_unavailable", 503)


def _verifier_key(store, key_id):
    for workspace in store.gateway.db._compute_config.workspaces:
        if workspace.workspace == store.workspace_id:
            key = workspace.provisioning_keys.get(key_id)
            if type(key) is bytes and len(key) == 32:
                return key
    raise StoreError("scripts_unavailable", 503)


def runtime_binding(store, conn, principal, node_id):
    """Current exact signed runtime and enrollment, without private-key access."""
    runtime = _configured(store, node_id)
    node, nm, gateway, gm = store._node(conn, principal, node_id)
    proof = conn.execute(
        "SELECT * FROM enrollment_verifications WHERE node_id=?", (node_id,)
    ).fetchone()
    now = store._now()
    enrollment = enrollment_digest(
        store._enrollment(gateway, gm), store._route(node, nm, gateway["id"])
    )
    if (
        proof is None
        or gm["state"] != "verified"
        or nm["state"] != "verified"
        or proof["enrollment"] != enrollment
        or not proof["verified_at"] <= now < proof["expires_at"]
        or not proof["script_runtime"]
    ):
        raise StoreError("scripts_unavailable", 503)
    attestation = json.loads(proof["script_runtime"])
    if (
        attestation.get("verifier_digest")
        != hashlib.sha256(_verifier_key(store, attestation.get("key_id"))).hexdigest()
    ):
        raise StoreError("scripts_unavailable", 503)
    expected = {
        "workspace": store.workspace_id,
        "gateway": gateway["id"],
        "node": node_id,
        "enrollment": enrollment,
        "enrollment_receipt": proof["receipt_digest"],
        "runtime_digest": runtime.runtime_digest,
        "key_principal": runtime.key_principal,
        "contract": runtime.contract,
    }
    if any(attestation.get(k) != v for k, v in expected.items()) or not (
        attestation["verified_at"] <= now < attestation["expires_at"]
    ):
        raise StoreError("scripts_unavailable", 503)
    return attestation


def accept_runtime(service, principal, node_id, *, session_hash, receipt):
    """Private provisioning seam using the existing verifier key registry."""
    if not service.enabled or type(receipt) is not ScriptRuntimeReceipt:
        raise StoreError("verification_unavailable", 409)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        public = service._provisioning_keys[receipt.key_id]
        raw = receipt.signed_bytes()
        Ed25519PublicKey.from_public_bytes(public).verify(receipt.signature, raw)
        with service._guard():
            with service.store._journal() as conn:
                store = service.store
                current = store._owner(principal, session_hash)
                runtime = _configured(store, node_id)
                if public != _verifier_key(store, receipt.key_id):
                    raise ValueError
                node, nm, gateway, gm = store._node(conn, current, node_id)
                proof = conn.execute(
                    "SELECT * FROM enrollment_verifications WHERE node_id=?", (node_id,)
                ).fetchone()
                now = store._now()
                if (
                    proof is None
                    or (receipt.workspace, receipt.gateway, receipt.node, receipt.enrollment)
                    != (
                        store.workspace_id,
                        gateway["id"],
                        node_id,
                        enrollment_digest(
                            store._enrollment(gateway, gm),
                            store._route(node, nm, gateway["id"]),
                        ),
                    )
                    or receipt.enrollment_receipt != proof["receipt_digest"]
                    or (receipt.runtime_digest, receipt.key_principal, receipt.contract)
                    != (runtime.runtime_digest, runtime.key_principal, runtime.contract)
                    or not receipt.verified_at <= now < receipt.expires_at
                    or not receipt.verified_at < receipt.expires_at <= receipt.verified_at + 3600
                    or receipt.expires_at > proof["expires_at"]
                ):
                    raise ValueError
                document = json.loads(raw)
                document["receipt_digest"] = hashlib.sha256(raw + receipt.signature).hexdigest()
                document["verifier_digest"] = hashlib.sha256(public).hexdigest()
                conn.execute(
                    "UPDATE enrollment_verifications SET script_runtime=? WHERE node_id=?",
                    (canonical(document).decode(), node_id),
                )
                store._audit(conn, current, node_id, "script_runtime.verified")
    except Exception:
        raise StoreError("verification_unavailable", 409) from None


def logical_binding(store, conn, principal, node_id, task_number):
    node, nm, gateway, gm = store._node(conn, principal, node_id)
    authority = store._authority(principal, gateway["board"], task_number)
    outer, inner = store._enrollment(gateway, gm), store._route(node, nm, gateway["id"])
    return ScriptBinding(
        workspace=store.workspace_id,
        project=authority["board"],
        user=principal.user_id,
        agent=principal.actor,
        task=str(task_number),
        gateway=gateway["id"],
        node=node_id,
        enrollment=enrollment_digest(outer, inner),
        gateway_epoch=outer.epoch,
        node_epoch=inner.epoch,
        gateway_credential_version=outer.credential_version,
        target_credential_version=inner.credential_version,
        authority_epoch=None,
    )


def bundles(store, conn, principal):
    path = Path(store._path() if callable(store._path) else store._path)

    def authorize(context, binding, action):
        if context is not principal or binding.user != principal.user_id:
            return False
        store._authority(principal, binding.project, int(binding.task))
        node, _, gateway, _ = store._node(conn, principal, binding.node)
        return node["gateway_id"] == binding.gateway and gateway["board"] == binding.project

    return ScriptBundleStore(
        path.parent / "compute-scripts.sqlite3", workspace=store.workspace_id, authorize=authorize
    )


def _access(store, conn, principal, bundle_id):
    row = store._row(conn, "script_bundle_refs", bundle_id)
    if row["user_id"] != principal.user_id:
        raise StoreError("not_found", 404)
    _, _, gateway, _ = store._node(conn, principal, row["node_id"])
    store._authority(principal, gateway["board"], row["task_number"])
    return row


def read_bundle(store, conn, principal, bundle_id):
    row = _access(store, conn, principal, bundle_id)
    manifest, script = bundles(store, conn, principal).read(bundle_id, context=principal)
    if manifest.digest != row["digest"]:
        raise StoreError("conflict", 409)
    return row, manifest, script


def _public(row, manifest, script=None):
    result = {
        "id": row["id"],
        "node_id": row["node_id"],
        "task_number": row["task_number"],
        "manifest": manifest.document(),
        "digest": manifest.digest,
    }
    if script is not None:
        result["source"] = script.decode("utf-8", errors="strict")
    return result


def create_script(
    store,
    principal,
    *,
    session_hash,
    node_id,
    task_number,
    request_id,
    source,
    arguments,
    output_paths,
):
    if type(source) is not str:
        raise StoreError()
    try:
        script = source.encode("utf-8", errors="strict")
    except UnicodeError:
        raise StoreError() from None
    with store._transaction(principal, session_hash) as (conn, current):
        identifier(request_id)
        binding = logical_binding(store, conn, current, node_id, task_number)
        payload = canonical(
            {
                "node_id": node_id,
                "task_number": task_number,
                "source_sha256": hashlib.sha256(script).hexdigest(),
                "arguments": arguments,
                "output_paths": output_paths,
            }
        ).decode()
        existing = conn.execute(
            "SELECT * FROM script_bundle_refs WHERE user_id=? AND request_id=?",
            (current.user_id, request_id),
        ).fetchone()
        if existing is not None:
            if existing["request"] != payload:
                raise StoreError("conflict", 409)
            row, manifest, _ = read_bundle(store, conn, current, existing["id"])
            return _public(row, manifest), False
        runtime = runtime_binding(store, conn, current, node_id)
        bundle_id = hashlib.sha256(
            canonical([store.workspace_id, current.user_id, request_id])
        ).hexdigest()
        manifest = ScriptManifest.create(
            binding,
            bundle_id,
            script,
            runtime_digest=runtime["runtime_digest"],
            arguments=arguments,
            output_paths=output_paths,
        )
        store._capacity(conn, "script_bundle_refs", 256)
        bundles(store, conn, current).put(manifest, script, context=current)
        conn.execute(
            "INSERT INTO script_bundle_refs VALUES(?,?,?,?,?,?,?,?)",
            (
                bundle_id,
                node_id,
                task_number,
                current.user_id,
                request_id,
                payload,
                manifest.digest,
                store._now(),
            ),
        )
        store._audit(conn, current, bundle_id, "script.created")
        return _public(store._row(conn, "script_bundle_refs", bundle_id), manifest), True


def get_script(store, principal, bundle_id, *, session_hash):
    with store._transaction(principal, session_hash) as (conn, current):
        row, manifest, script = read_bundle(store, conn, current, bundle_id)
        return _public(row, manifest, script)


def list_scripts(store, principal, *, session_hash):
    with store._transaction(principal, session_hash) as (conn, current):
        result = []
        for row in conn.execute("SELECT id FROM script_bundle_refs ORDER BY created_at DESC,id"):
            try:
                ref, manifest, _ = read_bundle(store, conn, current, row["id"])
                result.append(_public(ref, manifest))
            except StoreError as error:
                if error.code != "not_found":
                    raise
            if len(result) == 100:
                break
        return result


def approved_contract(store, conn, principal, node_id, task_number, arguments):
    if type(arguments) is not dict or set(arguments) != {"bundle_id"}:
        raise StoreError()
    row, manifest, _ = read_bundle(store, conn, principal, arguments["bundle_id"])
    runtime = runtime_binding(store, conn, principal, node_id)
    binding = logical_binding(store, conn, principal, node_id, task_number)
    if (row["node_id"], row["task_number"], manifest.binding, manifest.runtime_digest) != (
        node_id,
        task_number,
        binding,
        runtime["runtime_digest"],
    ):
        raise StoreError("conflict", 409)
    return {
        "version": "logical-script-proposal-v1",
        "manifest": manifest.document(),
        "manifest_digest": manifest.digest,
        "runtime": runtime,
    }


def capable(store, conn, principal):
    for row in conn.execute("SELECT id FROM nodes LIMIT 1000"):
        try:
            runtime_binding(store, conn, principal, row["id"])
            return True
        except (StoreError, ValueError, KeyError):
            continue
    return False
