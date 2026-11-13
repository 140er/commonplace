"""Person consent, machine-key status proofs and sealed setup-only host grants."""

import base64
import json
import re
import secrets
from datetime import timedelta

from commonplace import control_db

from . import enrollment, enrollment_crypto, install_receipts, registry

MACHINE_PATHS = ("/api/runner/setup-requests", "/api/runner/secret-delivery")
SCHEMA = """
CREATE TABLE IF NOT EXISTS runner_browser_requests (
    id TEXT PRIMARY KEY, enrollment_id TEXT NOT NULL REFERENCES runner_enrollments(id),
    workspace_id TEXT NOT NULL, machine_id TEXT NOT NULL, owner_id TEXT NOT NULL,
    kind TEXT NOT NULL, provider TEXT NOT NULL, scope_digest TEXT NOT NULL,
    state TEXT NOT NULL, proof_hash TEXT, expires_at TEXT NOT NULL,
    decided_by TEXT, decided_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runner_host_grants (
    request_id TEXT PRIMARY KEY REFERENCES runner_browser_requests(id),
    workspace_id TEXT NOT NULL, machine_id TEXT NOT NULL,
    secret_hash TEXT, ciphertext TEXT, expires_at TEXT NOT NULL,
    delivery_expires_at TEXT NOT NULL, acknowledged_at TEXT, revoked_at TEXT
);
"""


def ensure(db):
    with db._lock:
        db._conn.executescript(SCHEMA)


def _audit(db, row, action, actor=None, *, decision="ok"):
    db.audit(
        action="runner.setup." + action,
        decision=decision,
        workspace_id=row["workspace_id"],
        user_id=actor.user_id if actor else None,
        connection_id=actor.connection_id if actor else None,
        auth="session" if actor and actor.person else "machine",
        target_ids=(row["machine_id"], row["id"]),
    )


def invalidate_in(db, machine_id):
    """Caller holds the control transaction: revoke consent, status and relay together."""
    if not db._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='runner_browser_requests' AND type='table'"
    ).fetchone():
        return
    db._conn.execute(
        "UPDATE runner_browser_requests SET state='revoked',proof_hash=NULL WHERE machine_id=? AND state!='revoked'",
        (machine_id,),
    )
    db._conn.execute(
        "UPDATE runner_host_grants SET secret_hash=NULL,ciphertext=NULL,revoked_at=? WHERE machine_id=? AND revoked_at IS NULL",
        (control_db._iso(control_db._now()), machine_id),
    )


def sweep_in(db):
    stamp = control_db._iso(control_db._now())
    db._conn.execute(
        "UPDATE runner_browser_requests SET state='expired',proof_hash=NULL WHERE expires_at<=? AND state IN ('pending','approved')",
        (stamp,),
    )
    db._conn.execute(
        "UPDATE runner_host_grants SET ciphertext=NULL WHERE delivery_expires_at<=?", (stamp,)
    )
    db._conn.execute(
        "UPDATE runner_host_grants SET secret_hash=NULL,ciphertext=NULL,revoked_at=? WHERE expires_at<=? AND revoked_at IS NULL",
        (stamp, stamp),
    )


def context(db, machine):
    item = install_receipts._identity(db, machine)
    approved = install_receipts._current(db, machine, item)
    if approved is None:
        raise install_receipts.ApprovalRequired()
    snapshot = json.loads(approved["snapshot"])
    runtime = snapshot.get("runtime")
    facts = db._conn.execute(
        "SELECT * FROM runner_installations WHERE enrollment_id=?", (item["id"],)
    ).fetchone()
    if (
        runtime is None
        or facts is None
        or facts["settings_version"] != approved["version"]
        or facts["grant_version"] != machine["grant_version"]
        or facts["binding_epoch"] != machine["binding_epoch"]
    ):
        raise install_receipts.ApprovalRequired()
    if any(f["availability"] != "present" for f in json.loads(facts["components"]).values()):
        raise install_receipts.ApprovalRequired()
    scope = {
        **snapshot,
        "workspace_id": machine["workspace_id"],
        "machine_id": machine["id"],
        "enrollment_id": item["id"],
        "installation_id": item["installation_id"],
        "machine_key_digest": item["machine_key_digest"],
        "manifest_digest": item["manifest_digest"],
        "settings_version": approved["version"],
        "install_settings_version": item["settings_version"],
    }
    return item, scope, enrollment._hash(install_receipts.canonical(scope).decode())


def machine_now(db, principal):
    if principal is None or principal.runner != "machine":
        raise enrollment.Refused(404)
    live = db.authenticate_connection(principal.connection_id)
    if live is None or live.runner != "machine" or live.workspace_id != principal.workspace_id:
        raise enrollment.Refused(404)
    row = db._conn.execute(
        "SELECT * FROM runner_machines WHERE workspace_id=? AND machine_connection_id=? AND revoked_at IS NULL",
        (live.workspace_id, live.connection_id),
    ).fetchone()
    if row is None:
        raise enrollment.Refused(404)
    return row


def _signed(gateway, value):
    signer = getattr(gateway, "runner_receipt_signer", None)
    if signer is None:
        raise RuntimeError("approval signing unavailable")
    return {
        "receipt": value,
        "signature": base64.b64encode(signer.sign(install_receipts.canonical(value))).decode(),
    }


def _request_in(db, machine, kind, provider):
    item, scope, frozen = context(db, machine)
    stamp = control_db._iso(control_db._now())
    previous = db._conn.execute(
        "SELECT * FROM runner_browser_requests WHERE enrollment_id=? AND kind=? AND provider=? AND scope_digest=? AND state IN ('pending','approved','succeeded','denied') AND expires_at>? ORDER BY created_at DESC LIMIT 1",
        (item["id"], kind, provider, frozen, stamp),
    ).fetchone()
    if previous:
        return previous
    rid = control_db._new_id("consent")
    db._conn.execute(
        "INSERT INTO runner_browser_requests(id,enrollment_id,workspace_id,machine_id,owner_id,kind,provider,scope_digest,state,expires_at,created_at) VALUES (?,?,?,?,?,?,?,?,'pending',?,?)",
        (
            rid,
            item["id"],
            machine["workspace_id"],
            machine["id"],
            item["owner_id"],
            kind,
            provider,
            frozen,
            control_db._iso(control_db._now() + timedelta(minutes=5)),
            stamp,
        ),
    )
    row = db._conn.execute("SELECT * FROM runner_browser_requests WHERE id=?", (rid,)).fetchone()
    _audit(db, row, "requested")
    return row


def _current_request(db, machine, rid):
    item, scope, frozen = context(db, machine)
    row = db._conn.execute(
        "SELECT * FROM runner_browser_requests WHERE id=? AND workspace_id=? AND machine_id=? AND enrollment_id=?",
        (rid, machine["workspace_id"], machine["id"], item["id"]),
    ).fetchone()
    if (
        row is None
        or row["scope_digest"] != frozen
        or row["state"] in ("revoked", "expired", "denied")
        or row["expires_at"] <= control_db._iso(control_db._now())
    ):
        raise registry.Conflict("setup approval changed or expired")
    return row, scope


def _view(row):
    return {
        k: row[k]
        for k in ("id", "machine_id", "kind", "provider", "state", "scope_digest", "expires_at")
    }


def fetch(gateway, principal, nonce):
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ValueError("invalid setup challenge")
    db = gateway.db
    with db._tx():
        machine = machine_now(db, principal)
        item, scope, frozen = context(db, machine)
        sweep_in(db)
        requests = []
        for provider in scope["runtime"]["components"]:
            if provider != "host":
                requests.append(_view(_request_in(db, machine, "login", provider)))
        requests.append(_view(_request_in(db, machine, "grant", "host")))
        final = db._conn.execute(
            "SELECT * FROM runner_browser_requests WHERE enrollment_id=? AND kind='attest' AND scope_digest=? AND expires_at>? ORDER BY created_at DESC LIMIT 1",
            (item["id"], frozen, control_db._iso(control_db._now())),
        ).fetchone()
        if final and final["state"] not in ("expired", "revoked", "denied"):
            requests.append(_view(final))
        return _signed(
            gateway,
            {
                "version": 1,
                "purpose": "runner-setup",
                "scope": scope,
                "scope_digest": frozen,
                "requests": requests,
                "nonce": nonce,
                "issued_at": control_db._iso(control_db._now()),
                "expires_at": control_db._iso(control_db._now() + timedelta(seconds=60)),
                "agent_ready": False,
            },
        )


def owner_view(db, machine):
    if not enrollment.enabled(db, machine["workspace_id"]):
        return {}
    try:
        item, scope, frozen = context(db, machine)
    except (enrollment.Refused, install_receipts.ApprovalRequired):
        return {}
    stamp = control_db._iso(control_db._now())
    rows = db._conn.execute(
        "SELECT * FROM runner_browser_requests WHERE enrollment_id=? AND scope_digest=? AND state IN ('pending','approved','succeeded') AND expires_at>? ORDER BY created_at,id",
        (item["id"], frozen, stamp),
    ).fetchall()
    return {
        "setup_approval": {
            "fingerprint": item["machine_key_digest"],
            "scope": scope,
            "requests": [_view(r) for r in rows],
            "agent_ready": False,
        }
    }


def attention(db, owner):
    if not owner.person or owner.role != "owner" or not enrollment.enabled(db, owner.workspace_id):
        return []
    result = []
    with db._tx():
        for machine in registry.machines(db, owner.workspace_id):
            view = owner_view(db, machine).get("setup_approval")
            if view is None:
                continue
            for row in view["requests"]:
                if row["state"] != "pending" and not (
                    row["kind"] == "login" and row["state"] == "approved"
                ):
                    continue
                result.append(
                    {
                        **row,
                        "kind": "runner_consent",
                        "approval_kind": row["kind"],
                        "label": machine["label"],
                        "fingerprint": view["fingerprint"],
                        "message": "Approve provider sign-in"
                        if row["kind"] == "login"
                        else "Approve machine access",
                        "projects": list(view["scope"]["projects"]),
                        "topics": view["scope"]["topics"],
                        "runtime": view["scope"]["max_runtime_mode"],
                        "unsupported": row["provider"] == "claude",
                    }
                )
            if _prerequisites(db, machine, view["scope"]):
                final = _request_in(db, machine, "attest", "host")
                if final["state"] == "pending":
                    result.append(
                        {
                            **_view(final),
                            "kind": "runner_consent",
                            "approval_kind": "attest",
                            "label": machine["label"],
                            "fingerprint": view["fingerprint"],
                            "message": "Confirm this machine",
                            "projects": list(view["scope"]["projects"]),
                            "topics": view["scope"]["topics"],
                            "runtime": view["scope"]["max_runtime_mode"],
                            "binding": {
                                "topics": view["scope"]["topics"],
                                "provider_instance_id": view["scope"]["provider_instance_id"],
                                "worker_connection_id": view["scope"]["worker_connection_id"],
                            },
                        }
                    )
    return result


def decide(gateway, owner, mid, rid, data):
    db = gateway.db
    with db._tx():
        owner = enrollment.owner_now(db, owner)
        machine = registry.require_active(db, owner, mid)
        row, scope = _current_request(db, machine, rid)
        if row["owner_id"] != owner.user_id:
            raise enrollment.Refused(404)
        if (
            not isinstance(data, dict)
            or set(data) != {"approve", "scope_digest", "fingerprint"}
            or type(data["approve"]) is not bool
        ):
            raise ValueError("invalid setup decision")
        if (
            data["scope_digest"] != row["scope_digest"]
            or data["fingerprint"] != scope["machine_key_digest"]
            or row["state"] != "pending"
        ):
            raise registry.Conflict("setup approval changed or expired")
        if row["kind"] == "attest":
            raise ValueError("use the owner attestation action")
        if row["provider"] == "claude" and data["approve"]:
            raise registry.Conflict("browser-only login unavailable")
        if row["kind"] == "grant" and data["approve"]:
            _mint_in(gateway, row, scope)
        stamp = control_db._iso(control_db._now())
        db._conn.execute(
            "UPDATE runner_browser_requests SET state=?,decided_by=?,decided_at=? WHERE id=? AND state='pending'",
            ("approved" if data["approve"] else "denied", owner.user_id, stamp, rid),
        )
        _audit(db, row, "approve" if data["approve"] else "deny", owner)
        return {"ok": True, "agent_ready": False}


def _mint_in(gateway, row, scope):
    db = gateway.db
    delivery = db._conn.execute(
        "SELECT public_key FROM runner_secret_deliveries WHERE enrollment_id=?",
        (row["enrollment_id"],),
    ).fetchone()
    key, _, fingerprint = enrollment_crypto.public_key(delivery["public_key"])
    if fingerprint != scope["machine_key_digest"]:
        raise enrollment.Refused()
    now = control_db._now()
    expiry = control_db._iso(now + timedelta(minutes=10))
    secret = secrets.token_urlsafe(32)
    signed = _signed(
        gateway,
        {
            "version": 1,
            "purpose": "runner-host-grant",
            "secret_hash": enrollment._hash(secret),
            "request_id": row["id"],
            "scope": scope,
            "scope_digest": row["scope_digest"],
            "expires_at": expiry,
            "agent_ready": False,
        },
    )
    envelope = enrollment_crypto.seal(
        key,
        {"secret": secret, "signed": signed},
        install_receipts.canonical(
            {"request_id": row["id"], "scope_digest": row["scope_digest"]}
        ).decode(),
    )
    db._conn.execute(
        "INSERT INTO runner_host_grants(request_id,workspace_id,machine_id,secret_hash,ciphertext,expires_at,delivery_expires_at) VALUES (?,?,?,?,?,?,?)",
        (
            row["id"],
            row["workspace_id"],
            row["machine_id"],
            enrollment._hash(secret),
            json.dumps(envelope),
            expiry,
            expiry,
        ),
    )


def _proof(db, machine, data, action):
    if (
        not isinstance(data, dict)
        or set(data) != {"request_id", "scope_digest", "action", "signature"}
        or data["action"] != action
    ):
        raise ValueError("invalid setup proof")
    row, scope = _current_request(db, machine, data["request_id"])
    if row["scope_digest"] != data["scope_digest"]:
        raise enrollment.Refused()
    key = db._conn.execute(
        "SELECT public_key FROM runner_secret_deliveries WHERE enrollment_id=?",
        (row["enrollment_id"],),
    ).fetchone()[0]
    enrollment_crypto.verify(
        key,
        install_receipts.canonical({k: v for k, v in data.items() if k != "signature"}).decode(),
        data["signature"],
    )
    return row, scope


def report(gateway, principal, data):
    db = gateway.db
    with db._tx():
        machine = machine_now(db, principal)
        row, scope = _proof(db, machine, data, "login_succeeded")
        if row["kind"] != "login" or row["provider"] != "codex" or row["state"] != "approved":
            raise registry.Conflict("login approval required")
        db._conn.execute(
            "UPDATE runner_browser_requests SET state='succeeded',proof_hash=? WHERE id=?",
            (enrollment._hash(data["signature"]), row["id"]),
        )
        _audit(db, row, "login_succeeded", principal)
        return {"ok": True, "agent_ready": False}


def delivery(gateway, principal, data):
    db = gateway.db
    action = data.get("action") if isinstance(data, dict) else None
    if action not in ("fetch", "ack"):
        raise ValueError("invalid delivery action")
    with db._tx():
        machine = machine_now(db, principal)
        row, scope = _proof(db, machine, data, action)
        if row["kind"] != "grant" or row["state"] != "approved":
            raise registry.Conflict("grant approval required")
        grant = db._conn.execute(
            "SELECT * FROM runner_host_grants WHERE request_id=?", (row["id"],)
        ).fetchone()
        stamp = control_db._iso(control_db._now())
        if (
            grant is None
            or grant["revoked_at"]
            or grant["expires_at"] <= stamp
            or grant["delivery_expires_at"] <= stamp
            or not grant["ciphertext"]
        ):
            raise registry.Conflict("grant delivery unavailable")
        if action == "fetch":
            result = json.loads(grant["ciphertext"])
        else:
            db._conn.execute(
                "UPDATE runner_host_grants SET ciphertext=NULL,acknowledged_at=? WHERE request_id=?",
                (stamp, row["id"]),
            )
            db._conn.execute(
                "UPDATE runner_browser_requests SET state='succeeded' WHERE id=?", (row["id"],)
            )
            result = {"ok": True, "agent_ready": False}
        _audit(db, row, "delivery_" + action, principal)
        return result


def _prerequisites(db, machine, scope):
    frozen = enrollment._hash(install_receipts.canonical(scope).decode())
    stamp = control_db._iso(control_db._now())
    providers = set(scope["runtime"]["components"]) - {"host"}
    rows = db._conn.execute(
        "SELECT provider FROM runner_browser_requests WHERE machine_id=? AND scope_digest=? AND kind='login' AND state='succeeded' AND expires_at>?",
        (machine["id"], frozen, stamp),
    ).fetchall()
    grant = db._conn.execute(
        "SELECT 1 FROM runner_host_grants g JOIN runner_browser_requests r ON r.id=g.request_id WHERE g.machine_id=? AND r.scope_digest=? AND r.state='succeeded' AND g.acknowledged_at IS NOT NULL AND g.revoked_at IS NULL AND g.expires_at>?",
        (machine["id"], frozen, stamp),
    ).fetchone()
    return providers == {r[0] for r in rows} and grant is not None


def attest_in(db, owner, machine, data):
    owner = enrollment.owner_now(db, owner)
    if not isinstance(data, dict) or set(data) != {"request_id", "scope_digest", "fingerprint"}:
        raise ValueError("confirm the machine fingerprint")
    row, scope = _current_request(db, machine, data["request_id"])
    if (
        row["owner_id"] != owner.user_id
        or row["kind"] != "attest"
        or row["state"] != "pending"
        or data["scope_digest"] != row["scope_digest"]
        or data["fingerprint"] != scope["machine_key_digest"]
        or not _prerequisites(db, machine, scope)
    ):
        raise registry.Conflict("final setup approval unavailable")
    stamp = control_db._iso(control_db._now())
    db._conn.execute(
        "UPDATE runner_browser_requests SET state='succeeded',decided_by=?,decided_at=? WHERE id=?",
        (owner.user_id, stamp, row["id"]),
    )
    _audit(db, row, "attest", owner)


def present(gateway, principal, data):
    """Relay the public user code once from volatile memory; persist only its hash.

    The provider's device-auth id, access/refresh tokens and authorization code
    never cross this boundary. Losing this volatile presentation requires expiry
    and a fresh device flow, not credential recovery.
    """
    if not isinstance(data, dict) or set(data) != {
        "request_id",
        "scope_digest",
        "action",
        "user_code",
        "signature",
    }:
        raise ValueError("invalid device presentation")
    if (
        data["action"] != "device_code"
        or not isinstance(data["user_code"], str)
        or not re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}", data["user_code"])
    ):
        raise ValueError("invalid device presentation")
    db = gateway.db
    with db._tx():
        machine = machine_now(db, principal)
        row, scope = _current_request(db, machine, data["request_id"])
        if (
            row["kind"] != "login"
            or row["provider"] != "codex"
            or row["state"] != "approved"
            or row["proof_hash"] is not None
            or row["scope_digest"] != data["scope_digest"]
        ):
            raise registry.Conflict("device presentation unavailable")
        key = db._conn.execute(
            "SELECT public_key FROM runner_secret_deliveries WHERE enrollment_id=?",
            (row["enrollment_id"],),
        ).fetchone()[0]
        enrollment_crypto.verify(
            key,
            install_receipts.canonical(
                {k: v for k, v in data.items() if k != "signature"}
            ).decode(),
            data["signature"],
        )
        db._conn.execute(
            "UPDATE runner_browser_requests SET proof_hash=? WHERE id=?",
            (enrollment._hash(data["user_code"]), row["id"]),
        )
        _audit(db, row, "device_presented", principal)
    # Deliberately bounded and short-lived. Every read rechecks live authority.
    presentations = getattr(gateway, "runner_device_presentations", {})
    stamp = control_db._iso(control_db._now())
    presentations = {k: v for k, v in presentations.items() if v["expires_at"] > stamp}
    if len(presentations) >= 128:
        raise RuntimeError("device presentation capacity reached")
    presentations[row["id"]] = {"user_code": data["user_code"], "expires_at": row["expires_at"]}
    gateway.runner_device_presentations = presentations
    return {"ok": True, "agent_ready": False}


def reveal(gateway, owner, mid, rid):
    db = gateway.db
    with db._tx():
        owner = enrollment.owner_now(db, owner)
        machine = registry.require_active(db, owner, mid)
        row, scope = _current_request(db, machine, rid)
        if (
            row["owner_id"] != owner.user_id
            or row["provider"] != "codex"
            or row["kind"] != "login"
            or row["state"] != "approved"
        ):
            raise enrollment.Refused(404)
        value = getattr(gateway, "runner_device_presentations", {}).pop(rid, None)
        if value is None or value["expires_at"] <= control_db._iso(control_db._now()):
            raise registry.Conflict("device presentation unavailable; wait for a fresh sign-in")
        if not secrets.compare_digest(
            enrollment._hash(value["user_code"]), row["proof_hash"] or ""
        ):
            raise enrollment.Refused()
        _audit(db, row, "device_opened", owner)
        return {"url": "https://auth.openai.com/codex/device", **value}
