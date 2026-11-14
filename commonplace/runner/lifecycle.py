"""Key-only shutdown reporting; it cannot restore revoked machine authority."""

import base64
import re
from datetime import datetime, timezone

from commonplace import control_db

from . import enrollment, enrollment_crypto, host_status, install_receipts, registry

SCHEMA = """
CREATE TABLE IF NOT EXISTS runner_lifecycle (
    enrollment_id TEXT PRIMARY KEY REFERENCES runner_enrollments(id),
    workspace_id TEXT NOT NULL, machine_id TEXT NOT NULL,
    sequence INTEGER NOT NULL DEFAULT 0, updated_at TEXT,
    stop_requested_at TEXT, stopped_at TEXT,
    host_verification TEXT NOT NULL DEFAULT 'unavailable', host_scope_digest TEXT
);
"""
FIELDS = {
    "action",
    "purpose",
    "operation",
    "workspace_id",
    "enrollment_id",
    "installation_id",
    "machine_id",
    "sequence",
    "nonce",
    "host_status",
    "signature",
}


def ensure(db):
    with db._lock:
        db._conn.executescript(SCHEMA)


def invalidate_in(db, item, stamp):
    db._conn.execute(
        "INSERT INTO runner_lifecycle(enrollment_id,workspace_id,machine_id,stop_requested_at) VALUES (?,?,?,?) ON CONFLICT(enrollment_id) DO UPDATE SET stop_requested_at=COALESCE(stop_requested_at,excluded.stop_requested_at),host_verification='unavailable',host_scope_digest=NULL",
        (item["id"], item["workspace_id"], item["machine_id"], stamp),
    )


def _bound(db, data):
    if (
        not isinstance(data, dict)
        or set(data) != FIELDS
        or data["action"] != "lifecycle"
        or data["purpose"] != "runner-lifecycle"
        or data["operation"] not in ("pulse", "revoke", "stopped")
        or type(data["sequence"]) is not int
        or not 0 < data["sequence"] < 2**53
        or not isinstance(data["nonce"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", data["nonce"])
    ):
        raise enrollment.Refused(403)
    item = db._conn.execute(
        "SELECT e.*,d.installation_id,d.public_key FROM runner_enrollments e JOIN runner_secret_deliveries d ON d.enrollment_id=e.id WHERE e.workspace_id=? AND e.id=? AND e.machine_id=? AND d.installation_id=? AND e.consumed_at IS NOT NULL",
        (data["workspace_id"], data["enrollment_id"], data["machine_id"], data["installation_id"]),
    ).fetchone()
    if item is None:
        raise enrollment.Refused(403)
    unsigned = {k: v for k, v in data.items() if k != "signature"}
    enrollment_crypto.verify(
        item["public_key"], install_receipts.canonical(unsigned).decode(), data["signature"]
    )
    previous = db._conn.execute(
        "SELECT * FROM runner_lifecycle WHERE enrollment_id=?", (item["id"],)
    ).fetchone()
    if previous and data["sequence"] <= previous["sequence"]:
        raise enrollment.Refused(403)
    return item, previous


def exchange(gateway, data):
    db = gateway.db
    with db._tx():
        item, previous = _bound(db, data)
        # Authenticated pulses have a separate bounded budget from enrollment.
        window = int(control_db._now().timestamp()) // 60
        purpose = "lifecycle:" + item["id"]
        db._conn.execute(
            "INSERT INTO runner_enrollment_attempts(workspace_id,purpose,window_start,attempts) VALUES (?,?,?,1) ON CONFLICT(workspace_id,purpose) DO UPDATE SET attempts=CASE WHEN window_start=excluded.window_start THEN attempts+1 ELSE 1 END,window_start=excluded.window_start",
            (item["workspace_id"], purpose, window),
        )
        count = db._conn.execute(
            "SELECT attempts FROM runner_enrollment_attempts WHERE workspace_id=? AND purpose=?",
            (item["workspace_id"], purpose),
        ).fetchone()[0]
        if count > 90:
            raise enrollment.Refused(429)
        row = registry.get_machine(db, item["workspace_id"], item["machine_id"])
        if row is None:
            raise enrollment.Refused(403)
        operation = data["operation"]
        if operation == "revoke" and not item["revoked_at"]:
            enrollment._invalidate(db, item, control_db._iso(control_db._now()))
            item = db._conn.execute(
                "SELECT e.*,d.installation_id,d.public_key FROM runner_enrollments e JOIN runner_secret_deliveries d ON d.enrollment_id=e.id WHERE e.id=?",
                (item["id"],),
            ).fetchone()
        live = False
        if operation == "pulse" and not item["revoked_at"] and not row["revoked_at"]:
            try:
                install_receipts._identity(db, row)
                live = not (previous and previous["stopped_at"])
            except enrollment.Refused:
                pass
        if operation == "stopped" and not (
            item["revoked_at"] or (previous and previous["stop_requested_at"])
        ):
            raise enrollment.Refused(403)
        verification = "unavailable"
        frozen = None
        if data["host_status"] is not None:
            if not live:
                raise enrollment.Refused(403)
            from . import setup_approvals

            _, scope, frozen = setup_approvals.context(db, row)
            try:
                host_status.verify(
                    data["host_status"],
                    getattr(gateway, "runner_host_issuers", {}),
                    scope,
                    frozen,
                    data["nonce"],
                )
            except Exception:
                raise enrollment.Refused(403) from None
            verification = "verified"
        signer = getattr(gateway, "runner_receipt_signer", None)
        if signer is None:
            raise enrollment.Refused(403)
        stamp = control_db._iso(control_db._now())
        db._conn.execute(
            "INSERT INTO runner_lifecycle(enrollment_id,workspace_id,machine_id,sequence,updated_at,stop_requested_at,stopped_at,host_verification,host_scope_digest) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(enrollment_id) DO UPDATE SET sequence=excluded.sequence,updated_at=excluded.updated_at,stop_requested_at=COALESCE(stop_requested_at,excluded.stop_requested_at),stopped_at=COALESCE(stopped_at,excluded.stopped_at),host_verification=excluded.host_verification,host_scope_digest=excluded.host_scope_digest",
            (
                item["id"],
                item["workspace_id"],
                item["machine_id"],
                data["sequence"],
                stamp,
                None if live else stamp,
                stamp if operation == "stopped" else None,
                verification,
                frozen,
            ),
        )
        if not previous or operation != "pulse" or previous["host_verification"] != verification:
            db.audit(
                action="runner.lifecycle." + operation,
                decision="ok",
                auth="machine-key",
                workspace_id=item["workspace_id"],
                target_ids=(item["machine_id"], item["id"]),
                reason="setup_only"
                if live
                else "stopped"
                if operation == "stopped"
                else "stop_required",
            )
        receipt = {
            "version": 1,
            "purpose": "runner-lifecycle",
            "nonce": data["nonce"],
            "sequence": data["sequence"],
            "workspace_id": item["workspace_id"],
            "enrollment_id": item["id"],
            "installation_id": item["installation_id"],
            "machine_id": item["machine_id"],
            "command": "continue" if live else "stop",
            "lease_seconds": 4 if live else 0,
            "host_verification": verification,
            "agent_ready": False,
        }
        return {
            "receipt": receipt,
            "signature": base64.b64encode(
                signer.sign(install_receipts.canonical(receipt))
            ).decode(),
        }


def owner_view(db, row):
    with db._lock:
        row = registry.get_machine(db, row["workspace_id"], row["id"])
        if row is None or not enrollment.enabled(db, row["workspace_id"]):
            return {}
        item = db._conn.execute(
            "SELECT * FROM runner_lifecycle WHERE workspace_id=? AND machine_id=?",
            (row["workspace_id"], row["id"]),
        ).fetchone()
        if item is None:
            return {}
        fresh = bool(
            item["updated_at"]
            and (
                control_db._now()
                - datetime.strptime(item["updated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
            ).total_seconds()
            < 5
        )
        service = (
            "Stopped"
            if item["stopped_at"]
            else "Revoked; stop unconfirmed"
            if row["revoked_at"] or item["stop_requested_at"]
            else "Running setup only"
            if fresh
            else "Offline; stop unconfirmed"
        )
        verified = fresh and not row["revoked_at"] and item["host_verification"] == "verified"
        if verified:
            from . import setup_approvals

            try:
                verified = setup_approvals.context(db, row)[2] == item["host_scope_digest"]
            except Exception:
                verified = False
        return {
            "lifecycle": {
                "service": service,
                "host_verification": "verified" if verified else "unavailable",
                "agent_ready": False,
            }
        }


def attention(db, owner):
    if not owner.person or owner.role != "owner" or not enrollment.enabled(db, owner.workspace_id):
        return []
    result = []
    for row in registry.machines(db, owner.workspace_id):
        view = owner_view(db, row).get("lifecycle")
        if view and "unconfirmed" in view["service"]:
            result.append(
                {
                    "id": row["id"] + ":stop",
                    "machine_id": row["id"],
                    "kind": "runner_settings",
                    "message": row["label"] + ": " + view["service"],
                    "reason": "stop_unconfirmed",
                }
            )
    return result
