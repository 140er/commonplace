"""Machine-reported setup facts, never readiness or permission authority."""

import json

from commonplace import control_db

from . import enrollment, install_receipts
from .install_files import digest, encoded
from .runtime_manifest import facts_valid

PATH = "/api/runner/install-status"
SCHEMA = """
CREATE TABLE IF NOT EXISTS runner_installations (
    enrollment_id TEXT PRIMARY KEY REFERENCES runner_enrollments(id),
    workspace_id TEXT NOT NULL, machine_id TEXT NOT NULL,
    settings_version INTEGER NOT NULL, grant_version INTEGER NOT NULL,
    binding_epoch INTEGER NOT NULL, runtime_digest TEXT NOT NULL,
    components TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


def ensure(db):
    with db._lock:
        db._conn.executescript(SCHEMA)


def report(gateway, principal, data):
    db = gateway.db
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
        item = install_receipts._identity(db, row)
        approved = install_receipts._current(db, row, item)
        if approved is None:
            raise install_receipts.ApprovalRequired()
        snapshot = json.loads(approved["snapshot"])
        runtime = snapshot.get("runtime")
        if runtime is None:
            raise install_receipts.ApprovalRequired()
        if not isinstance(data, dict) or set(data) != {
            "settings_version",
            "grant_version",
            "binding_epoch",
            "runtime_digest",
            "components",
        }:
            raise ValueError("invalid runtime report")
        for key, expected in (
            ("settings_version", approved["version"]),
            ("grant_version", row["grant_version"]),
            ("binding_epoch", row["binding_epoch"]),
        ):
            if type(data[key]) is not int or data[key] != expected:
                raise install_receipts.ApprovalRequired()
        if data["runtime_digest"] != digest(encoded(runtime)):
            raise install_receipts.ApprovalRequired()
        if not isinstance(data["components"], dict) or set(data["components"]) != set(
            runtime["components"]
        ):
            raise ValueError("invalid runtime report")
        facts = {name: facts_valid(fact, name) for name, fact in data["components"].items()}
        previous = db._conn.execute(
            "SELECT * FROM runner_installations WHERE enrollment_id=?", (item["id"],)
        ).fetchone()
        if (
            previous
            and all(
                previous[k] == data[k]
                for k in ("settings_version", "grant_version", "binding_epoch", "runtime_digest")
            )
            and json.loads(previous["components"]) == facts
        ):
            return {"ok": True, "agent_ready": False}
        db._conn.execute(
            "INSERT INTO runner_installations(enrollment_id,workspace_id,machine_id,settings_version,grant_version,binding_epoch,runtime_digest,components,updated_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(enrollment_id) DO UPDATE SET settings_version=excluded.settings_version,grant_version=excluded.grant_version,binding_epoch=excluded.binding_epoch,runtime_digest=excluded.runtime_digest,components=excluded.components,updated_at=excluded.updated_at",
            (
                item["id"],
                live.workspace_id,
                row["id"],
                data["settings_version"],
                data["grant_version"],
                data["binding_epoch"],
                data["runtime_digest"],
                install_receipts.canonical(facts).decode(),
                control_db._iso(control_db._now()),
            ),
        )
        db.audit(
            action="runner.installation.components",
            decision="ok",
            workspace_id=live.workspace_id,
            connection_id=live.connection_id,
            auth=live.auth,
            target_ids=(row["id"], item["id"]),
        )
        return {"ok": True, "agent_ready": False}


def attention(db, owner):
    if not owner.person or owner.role != "owner" or not enrollment.enabled(db, owner.workspace_id):
        return []
    result = []
    with db._lock:
        rows = db._conn.execute(
            "SELECT m.* FROM runner_machines m JOIN runner_installations i ON i.machine_id=m.id AND i.workspace_id=m.workspace_id WHERE m.workspace_id=? AND m.revoked_at IS NULL AND i.grant_version=m.grant_version AND i.binding_epoch=m.binding_epoch",
            (owner.workspace_id,),
        ).fetchall()
        for row in rows:
            item = install_receipts.installation(db, owner.workspace_id, row["id"])
            try:
                install_receipts._identity(db, row)
            except enrollment.Refused:
                continue
            approved = install_receipts._current(db, row, item)
            status = db._conn.execute(
                "SELECT * FROM runner_installations WHERE enrollment_id=?", (item["id"],)
            ).fetchone()
            if approved is None or status["settings_version"] != approved["version"]:
                continue
            facts = json.loads(status["components"])
            reason = (
                "component_missing"
                if any(f["availability"] == "missing" for f in facts.values())
                else "unsupported_runtime"
                if any(f["availability"] != "present" for f in facts.values())
                else "sign_in_needed"
            )
            result.append(
                {
                    "id": item["id"] + ":runtime",
                    "machine_id": row["id"],
                    "kind": "runner_settings",
                    "reason": reason,
                    "message": {
                        "component_missing": "Selected runner component missing",
                        "unsupported_runtime": "Runner capability unavailable",
                        "sign_in_needed": "Sign-in needed",
                    }[reason],
                    "components": facts,
                }
            )
    return result
