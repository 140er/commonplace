"""Current owner-approved installation settings; machine reports grant no authority."""

import base64
import json
import re
from datetime import timedelta

from commonplace import control_db, sessions

from . import enrollment, registry, runtime_manifest

PATH = "/api/runner/install-manifest"
SCHEMA = """
CREATE TABLE IF NOT EXISTS runner_install_manifests (
    enrollment_id TEXT PRIMARY KEY REFERENCES runner_enrollments(id),
    workspace_id TEXT NOT NULL, machine_id TEXT NOT NULL,
    version INTEGER NOT NULL, approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL, snapshot TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runner_setup_requests (
    id TEXT PRIMARY KEY, enrollment_id TEXT NOT NULL REFERENCES runner_enrollments(id),
    workspace_id TEXT NOT NULL, machine_id TEXT NOT NULL,
    grant_version INTEGER NOT NULL, binding_epoch INTEGER NOT NULL,
    state TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(enrollment_id, grant_version, binding_epoch)
);
"""


class ApprovalRequired(Exception):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def ensure(db):
    with db._lock:
        db._conn.executescript(SCHEMA)
    from . import runtime_status

    runtime_status.ensure(db)
    from . import setup_approvals

    setup_approvals.ensure(db)


def installation(db, workspace, machine_id):
    if not db._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runner_enrollments'"
    ).fetchone():
        return None
    return db._conn.execute(
        "SELECT e.*, d.installation_id FROM runner_enrollments e JOIN runner_secret_deliveries d ON d.enrollment_id=e.id WHERE e.workspace_id=? AND e.machine_id=?",
        (workspace, machine_id),
    ).fetchone()


def _identity(db, row):
    enrollment._require_enabled(db, row["workspace_id"])
    item = installation(db, row["workspace_id"], row["id"])
    if (
        item is None
        or item["revoked_at"]
        or row["revoked_at"]
        or item["settings_version"] != enrollment.settings(db, row["workspace_id"])["version"]
    ):
        raise enrollment.Refused(404)
    enrollment.owner_now(db, sessions.person_principal(db, item["owner_id"], row["workspace_id"]))
    for kind in ("machine", "worker"):
        if db.authenticate_connection(row[kind + "_connection_id"]) is None:
            raise enrollment.Refused(404)
    return item


def _scope(row):
    return {
        "grant_version": row["grant_version"],
        "binding_epoch": row["binding_epoch"],
        "worker_connection_id": row["worker_connection_id"],
        "provider_instance_id": row["binding_provider_instance_id"],
        "topics": json.loads(row["topics"]),
        "wings": json.loads(row["wings"]),
        "requesters": json.loads(row["requesters"]),
        "max_runtime_mode": row["max_runtime_mode"],
        "max_in_flight": row["max_in_flight"],
        "max_starts_per_hour": row["max_starts_per_hour"],
        "project_labels": json.loads(row["projects"]),
    }


def owner_view(db, row):
    """Only the existing owner registry view exposes the settings revision."""
    with db._lock:
        if (
            not enrollment.enabled(db, row["workspace_id"])
            or installation(db, row["workspace_id"], row["id"]) is None
        ):
            return {}
        approved = db._conn.execute(
            "SELECT version,snapshot FROM runner_install_manifests WHERE machine_id=? AND workspace_id=?",
            (row["id"], row["workspace_id"]),
        ).fetchone()
        from . import setup_approvals

        return {
            **setup_approvals.owner_view(db, row),
            "install_settings": {
                "settings_version": approved["version"] if approved else 0,
                "approved": json.loads(approved["snapshot"]) if approved else None,
            },
        }


def approve_in(db, owner, row, settings):
    """Called only inside the owner attestation transaction, never by a machine."""
    try:
        owner = enrollment.owner_now(db, owner)
        _identity(db, row)
    except enrollment.Refused:
        raise registry.NotFound("installation not found") from None
    if not isinstance(settings, dict) or set(settings) - {"runtime"} != {
        "grant_version",
        "binding_epoch",
        "settings_version",
        "projects",
    }:
        raise ValueError("invalid installation settings")
    for key in ("grant_version", "binding_epoch"):
        if type(settings[key]) is not int or settings[key] != row[key]:
            raise registry.Conflict("installation settings changed")
    previous = db._conn.execute(
        "SELECT version FROM runner_install_manifests WHERE machine_id=? AND workspace_id=?",
        (row["id"], owner.workspace_id),
    ).fetchone()
    if type(settings["settings_version"]) is not int or settings["settings_version"] != (
        previous["version"] if previous else 0
    ):
        raise registry.Conflict("installation settings changed")
    projects = settings["projects"]
    if not isinstance(projects, dict) or set(projects) != set(json.loads(row["projects"])):
        raise ValueError("projects must match the machine grant")
    ids = set()
    for project in projects.values():
        if (
            not isinstance(project, dict)
            or set(project) != {"project_id", "checkout", "runtime_ceiling"}
            or not isinstance(project["project_id"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", project["project_id"])
            or project["project_id"] in ids
            or project["checkout"] != "shared"
            or project["runtime_ceiling"] not in registry.MODES
            or registry.MODES.index(project["runtime_ceiling"])
            > registry.MODES.index(row["max_runtime_mode"])
        ):
            raise ValueError("invalid approved project settings")
        ids.add(project["project_id"])
    snapshot = {**_scope(row), "projects": projects}
    if "runtime" in settings:
        snapshot["runtime"] = runtime_manifest.validate(settings["runtime"], snapshot["topics"])
    from . import setup_approvals

    setup_approvals.invalidate_in(db, row["id"])
    stamp = control_db._iso(control_db._now())
    item = installation(db, owner.workspace_id, row["id"])
    db._conn.execute(
        "INSERT INTO runner_install_manifests(enrollment_id,workspace_id,machine_id,version,approved_by,approved_at,snapshot) VALUES (?,?,?,1,?,?,?) ON CONFLICT(enrollment_id) DO UPDATE SET version=version+1, approved_by=excluded.approved_by, approved_at=excluded.approved_at, snapshot=excluded.snapshot",
        (
            item["id"],
            owner.workspace_id,
            row["id"],
            owner.user_id,
            stamp,
            canonical(snapshot).decode(),
        ),
    )
    db._conn.execute(
        "UPDATE runner_setup_requests SET state='approved' WHERE enrollment_id=? AND state='pending'",
        (item["id"],),
    )
    db.audit(
        action="runner.installation.approve",
        decision="ok",
        workspace_id=owner.workspace_id,
        user_id=owner.user_id,
        auth="session",
        target_ids=(row["id"], item["id"]),
    )


def _current(db, row, item):
    approved = db._conn.execute(
        "SELECT * FROM runner_install_manifests WHERE enrollment_id=?", (item["id"],)
    ).fetchone()
    if (
        approved is None
        or row["binding_state"] != "owner_attested"
        or row["binding_grant_version"] != row["grant_version"]
        or row["binding_worker_connection_id"] != row["worker_connection_id"]
        or set(json.loads(row["binding_topics"] or "[]")) != set(json.loads(row["topics"]))
    ):
        return None
    try:
        enrollment.owner_now(
            db, sessions.person_principal(db, approved["approved_by"], row["workspace_id"])
        )
        enrollment.owner_now(
            db, sessions.person_principal(db, row["binding_attested_by"], row["workspace_id"])
        )
    except enrollment.Refused:
        return None
    snapshot = json.loads(approved["snapshot"])
    return approved if all(snapshot.get(k) == v for k, v in _scope(row).items()) else None


def _pending_in(db, row, item):
    db._conn.execute(
        "INSERT INTO runner_setup_requests(id,enrollment_id,workspace_id,machine_id,grant_version,binding_epoch,state,created_at) VALUES (?,?,?,?,?,?,'pending',?) ON CONFLICT(enrollment_id,grant_version,binding_epoch) DO UPDATE SET state='pending'",
        (
            control_db._new_id("setup"),
            item["id"],
            row["workspace_id"],
            row["id"],
            row["grant_version"],
            row["binding_epoch"],
            control_db._iso(control_db._now()),
        ),
    )
    db.audit(
        action="runner.installation.pending",
        decision="deny",
        workspace_id=row["workspace_id"],
        reason="approval_required",
        target_ids=(row["id"], item["id"]),
    )
    return db._conn.execute(
        "SELECT * FROM runner_setup_requests WHERE enrollment_id=? AND grant_version=? AND binding_epoch=?",
        (item["id"], row["grant_version"], row["binding_epoch"]),
    ).fetchone()


def attention(db, owner):
    """Owner-only Needs me projection; the reminder itself grants no authority."""
    if not owner.person or owner.role != "owner" or not enrollment.enabled(db, owner.workspace_id):
        return []
    with db._lock:
        rows = db._conn.execute(
            "SELECT r.id,r.machine_id,r.grant_version,r.binding_epoch FROM runner_setup_requests r JOIN runner_machines m ON m.id=r.machine_id JOIN runner_enrollments e ON e.id=r.enrollment_id WHERE r.workspace_id=? AND r.state='pending' AND m.revoked_at IS NULL AND e.revoked_at IS NULL AND r.grant_version=m.grant_version AND r.binding_epoch=m.binding_epoch ORDER BY r.created_at,r.id",
            (owner.workspace_id,),
        ).fetchall()
    from . import runtime_status, setup_approvals

    return (
        setup_approvals.attention(db, owner)
        + runtime_status.attention(db, owner)
        + [
            {
                **dict(row),
                "kind": "runner_settings",
                "message": "Review runner binding and project settings",
                "reason": "approval_required",
            }
            for row in rows
        ]
    )


def fetch(gateway, principal, nonce):
    db = gateway.db
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ValueError("invalid receipt challenge")
    with db._tx():
        if principal is None or principal.runner != "machine":
            raise enrollment.Refused(404)
        live = db.authenticate_connection(principal.connection_id)
        if live is None or live.runner != "machine" or live.workspace_id != principal.workspace_id:
            raise enrollment.Refused(404)
        row = db._conn.execute(
            "SELECT * FROM runner_machines WHERE workspace_id=? AND machine_connection_id=?",
            (live.workspace_id, live.connection_id),
        ).fetchone()
        if row is None:
            raise enrollment.Refused(404)
        item = _identity(db, row)
        approved = _current(db, row, item)
        if approved is None:
            _pending_in(db, row, item)
            result = None
        else:
            snapshot = json.loads(approved["snapshot"])
            now = control_db._now()
            receipt = {
                **snapshot,
                "version": 1,
                "workspace_id": row["workspace_id"],
                "machine_id": row["id"],
                "machine_key_digest": item["machine_key_digest"],
                "enrollment_id": item["id"],
                "installation_id": item["installation_id"],
                "manifest_digest": item["manifest_digest"],
                "settings_version": approved["version"],
                "install_settings_version": item["settings_version"],
                "approved_by": approved["approved_by"],
                "approved_at": approved["approved_at"],
                "attested_at": row["binding_attested_at"],
                "nonce": nonce,
                "issued_at": control_db._iso(now),
                "expires_at": control_db._iso(now + timedelta(seconds=60)),
                "agent_ready": False,
            }
            signer = getattr(gateway, "runner_receipt_signer", None)
            if signer is None:
                raise RuntimeError("receipt signing unavailable")
            result = {
                "receipt": receipt,
                "signature": base64.b64encode(signer.sign(canonical(receipt))).decode(),
            }
            db.audit(
                action="runner.installation.fetch",
                decision="ok",
                workspace_id=row["workspace_id"],
                connection_id=live.connection_id,
                auth=live.auth,
                target_ids=(row["id"], item["id"]),
            )
    if result is None:
        raise ApprovalRequired()
    return result
