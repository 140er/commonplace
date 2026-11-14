"""Owner-approved enrollment intent, atomic consumption and encrypted recovery.

All authority, credential and audit writes share the control transaction. No
runtime is activated here; a redeemed machine remains disabled and unbound.
"""

import hashlib
import json
import re
import secrets
import threading
from datetime import timedelta

from commonplace import control_db, sessions

from . import enrollment_crypto, registry

CODE_MINUTES = 5
RECOVERY_MINUTES = 10
ATTEMPTS_PER_MINUTE = 10
DIGEST = re.compile(r"[0-9a-f]{64}")
REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
SCHEMA = """
CREATE TABLE IF NOT EXISTS runner_install_settings (
    workspace_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    version INTEGER NOT NULL, updated_by TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runner_enrollments (
    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, machine_label TEXT NOT NULL,
    code_hash TEXT UNIQUE, expires_at TEXT NOT NULL, consumed_at TEXT, revoked_at TEXT,
    owner_id TEXT NOT NULL, request_id TEXT NOT NULL, manifest_digest TEXT NOT NULL,
    settings_version INTEGER NOT NULL, intent TEXT NOT NULL, machine_id TEXT,
    machine_key_digest TEXT, created_at TEXT NOT NULL,
    UNIQUE(workspace_id, owner_id, request_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS runner_enrollments_outstanding
    ON runner_enrollments(workspace_id, machine_label)
    WHERE consumed_at IS NULL AND revoked_at IS NULL;
CREATE TABLE IF NOT EXISTS runner_secret_deliveries (
    enrollment_id TEXT PRIMARY KEY REFERENCES runner_enrollments(id),
    workspace_id TEXT NOT NULL, installation_id TEXT NOT NULL, public_key TEXT NOT NULL,
    ciphertext TEXT, expires_at TEXT NOT NULL, acknowledged_at TEXT,
    challenge_hash TEXT, challenge_expires_at TEXT
);
CREATE TABLE IF NOT EXISTS runner_enrollment_attempts (
    workspace_id TEXT NOT NULL, purpose TEXT NOT NULL, window_start INTEGER NOT NULL,
    attempts INTEGER NOT NULL, PRIMARY KEY(workspace_id, purpose)
);
"""


class Refused(Exception):
    def __init__(self, status=403):
        self.status = status
        super().__init__("enrollment refused")


def ensure(db):
    with db._lock:
        db._conn.executescript(SCHEMA)
    from . import lifecycle

    lifecycle.ensure(db)


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _time():
    now = control_db._now()
    return now, control_db._iso(now)


def settings(db, workspace):
    with db._lock:
        return db._conn.execute(
            "SELECT * FROM runner_install_settings WHERE workspace_id=?", (workspace,)
        ).fetchone()


def enabled(db, workspace):
    row = settings(db, workspace)
    return bool(row and row["enabled"] == 1 and type(row["version"]) is int and row["version"] > 0)


def owner_now(db, owner, *, refusal=404):
    if owner is None or not owner.person or owner.auth != "session":
        raise Refused(refusal)
    live = sessions.person_principal(db, owner.user_id, owner.workspace_id)
    if live is None or live.role != "owner":
        raise Refused(refusal)
    return live


def _audit(db, workspace, action, *, owner=None, ids=(), decision="ok", reason=""):
    db.audit(
        action="runner.enrollment." + action,
        decision=decision,
        workspace_id=workspace,
        user_id=owner.user_id if owner else None,
        auth="session" if owner else "enrollment",
        target_ids=ids,
        reason=reason,
    )


def status(row):
    return {
        key: row[key]
        for key in (
            "id",
            "machine_label",
            "expires_at",
            "consumed_at",
            "revoked_at",
            "manifest_digest",
            "settings_version",
            "machine_id",
        )
    }


def _require_enabled(db, workspace):
    if not enabled(db, workspace):
        raise Refused(404)


def _purge_delivery(db, enrollment_id):
    db._conn.execute(
        "UPDATE runner_secret_deliveries SET ciphertext=NULL, challenge_hash=NULL, challenge_expires_at=NULL WHERE enrollment_id=?",
        (enrollment_id,),
    )


def _invalidate(db, row, now, owner=None, *, action="revoke"):
    if row["revoked_at"]:
        return
    db._conn.execute(
        "UPDATE runner_enrollments SET revoked_at=?, code_hash=NULL WHERE id=?", (now, row["id"])
    )
    _purge_delivery(db, row["id"])
    if row["machine_id"]:
        from . import lifecycle

        lifecycle.invalidate_in(db, row, now)
    from . import setup_approvals

    setup_approvals.invalidate_in(db, row["machine_id"])
    if row["machine_id"]:
        machine = registry.get_machine(db, row["workspace_id"], row["machine_id"])
        if machine and not machine["revoked_at"]:
            for kind in ("machine", "worker"):
                db._conn.execute(
                    "UPDATE connections SET revoked_at=?, token_hash=NULL WHERE id=?",
                    (now, machine[f"{kind}_connection_id"]),
                )
            db._conn.execute(
                "UPDATE runner_machines SET revoked_at=?, enabled=0, grant_version=grant_version+1, binding_epoch=binding_epoch+1, binding_state='unbound', binding_topics=NULL, binding_provider_instance_id=NULL, binding_worker_connection_id=NULL, binding_attested_by=NULL, binding_attested_at=NULL, binding_grant_version=NULL, updated_at=? WHERE id=?",
                (now, now, machine["id"]),
            )
    _audit(db, row["workspace_id"], action, owner=owner, ids=(row["id"], row["machine_id"]))


def revoke_machine_in(db, owner, machine_id):
    """Invalidate this flow's recovery when the ordinary machine control revokes."""
    present = db._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runner_enrollments'"
    ).fetchone()
    if present:
        _, now = _time()
        rows = db._conn.execute(
            "SELECT * FROM runner_enrollments WHERE workspace_id=? AND machine_id=? AND revoked_at IS NULL",
            (owner.workspace_id, machine_id),
        ).fetchall()
        for row in rows:
            _invalidate(db, row, now, owner)


def _sweep_in(db, workspace, now):
    rows = db._conn.execute(
        "SELECT * FROM runner_enrollments WHERE workspace_id=? AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at<=?",
        (workspace, now),
    ).fetchall()
    for row in rows:
        _invalidate(db, row, now, action="expire")
    expired = db._conn.execute(
        "SELECT enrollment_id FROM runner_secret_deliveries WHERE workspace_id=? AND ciphertext IS NOT NULL AND expires_at<=?",
        (workspace, now),
    ).fetchall()
    for row in expired:
        _purge_delivery(db, row[0])
        _audit(db, workspace, "delivery_expire", ids=(row[0],))
    db._conn.execute(
        "UPDATE runner_secret_deliveries SET challenge_hash=NULL, challenge_expires_at=NULL WHERE workspace_id=? AND challenge_expires_at<=?",
        (workspace, now),
    )


def sweep(db):
    """Expire pending codes and purge recovery ciphertext, including while off."""
    with db._tx():
        _, now = _time()
        from . import setup_approvals

        if db._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='runner_browser_requests'"
        ).fetchone():
            setup_approvals.sweep_in(db)
        for row in db._conn.execute("SELECT workspace_id FROM runner_install_settings").fetchall():
            _sweep_in(db, row[0], now)


def set_enabled(db, owner, value):
    if type(value) is not bool:
        raise ValueError("invalid settings")
    with db._tx():
        owner = owner_now(db, owner)
        _, now = _time()
        previous = settings(db, owner.workspace_id)
        if previous and previous["enabled"] == int(value):
            return {"enabled": value, "version": previous["version"]}
        # A missing disabled row stays missing: an off read/write creates no intent.
        if previous is None and not value:
            return {"enabled": False, "version": 0}
        if previous:
            rows = db._conn.execute(
                "SELECT * FROM runner_enrollments WHERE workspace_id=? AND revoked_at IS NULL",
                (owner.workspace_id,),
            ).fetchall()
            for row in rows:
                _invalidate(db, row, now, owner)
        version = previous["version"] + 1 if previous else 1
        db._conn.execute(
            "INSERT INTO runner_install_settings(workspace_id, enabled, version, updated_by, updated_at) VALUES (?,?,?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET enabled=excluded.enabled, version=excluded.version, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (owner.workspace_id, int(value), version, owner.user_id, now),
        )
        _audit(db, owner.workspace_id, "enable" if value else "disable", owner=owner)
        return {"enabled": value, "version": version}


def issue(db, owner, data):
    if set(data) != {"request_id", "manifest_digest", "grant"}:
        raise ValueError("invalid enrollment intent")
    request, digest = data["request_id"], data["manifest_digest"]
    if (
        not isinstance(request, str)
        or not REQUEST_ID.fullmatch(request)
        or not isinstance(digest, str)
        or not DIGEST.fullmatch(digest)
    ):
        raise ValueError("invalid enrollment intent")
    with db._tx():
        owner = owner_now(db, owner)
        _require_enabled(db, owner.workspace_id)
        now, stamp = _time()
        _sweep_in(db, owner.workspace_id, stamp)
        grant = registry._grant(db, owner, data["grant"], initial=True)
        intent = json.dumps(grant, sort_keys=True, separators=(",", ":"))
        previous = db._conn.execute(
            "SELECT * FROM runner_enrollments WHERE workspace_id=? AND owner_id=? AND request_id=?",
            (owner.workspace_id, owner.user_id, request),
        ).fetchone()
        if previous:
            if previous["intent"] != intent or previous["manifest_digest"] != digest:
                raise registry.Conflict("enrollment request changed")
            return 200, status(previous)
        pending = db._conn.execute(
            "SELECT 1 FROM runner_enrollments WHERE workspace_id=? AND machine_label=? AND consumed_at IS NULL AND revoked_at IS NULL",
            (owner.workspace_id, grant["label"]),
        ).fetchone()
        machine = registry.get_machine(db, owner.workspace_id, grant["label"])
        if pending or (machine and not machine["revoked_at"]):
            raise registry.Conflict("label already in use")
        eid, code = control_db._new_id("enr"), secrets.token_urlsafe(32)
        expires = control_db._iso(now + timedelta(minutes=CODE_MINUTES))
        version = settings(db, owner.workspace_id)["version"]
        db._conn.execute(
            "INSERT INTO runner_enrollments(id, workspace_id, machine_label, code_hash, expires_at, owner_id, request_id, manifest_digest, settings_version, intent, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                eid,
                owner.workspace_id,
                grant["label"],
                _hash(code),
                expires,
                owner.user_id,
                request,
                digest,
                version,
                intent,
                stamp,
            ),
        )
        _audit(db, owner.workspace_id, "issue", owner=owner, ids=(eid,))
        return 201, {
            **status(
                db._conn.execute("SELECT * FROM runner_enrollments WHERE id=?", (eid,)).fetchone()
            ),
            "code": code,
        }


def inspect(db, owner, eid, *, revoke=False):
    with db._tx():
        owner = owner_now(db, owner)
        _require_enabled(db, owner.workspace_id)
        _, now = _time()
        _sweep_in(db, owner.workspace_id, now)
        row = db._conn.execute(
            "SELECT * FROM runner_enrollments WHERE workspace_id=? AND id=?",
            (owner.workspace_id, eid),
        ).fetchone()
        if row is None:
            raise Refused(404)
        if revoke:
            _invalidate(db, row, now, owner)
            row = db._conn.execute("SELECT * FROM runner_enrollments WHERE id=?", (eid,)).fetchone()
        return status(row)


def charge(db, workspace, purpose):
    """Durable workspace-wide counters survive restarts and include denied tries."""
    with db._tx():
        _require_enabled(db, workspace)
        now, _ = _time()
        window = int(now.timestamp()) // 60
        db._conn.execute(
            "INSERT INTO runner_enrollment_attempts(workspace_id,purpose,window_start,attempts) VALUES (?,?,?,1) ON CONFLICT(workspace_id,purpose) DO UPDATE SET attempts=CASE WHEN window_start=excluded.window_start THEN attempts+1 ELSE 1 END, window_start=excluded.window_start",
            (workspace, purpose, window),
        )
        row = db._conn.execute(
            "SELECT attempts FROM runner_enrollment_attempts WHERE workspace_id=? AND purpose=?",
            (workspace, purpose),
        ).fetchone()
        limited = row[0] > ATTEMPTS_PER_MINUTE
        if limited:
            _audit(db, workspace, "denied", decision="deny", reason="rate_limited")
    if limited:
        raise Refused(429)


def deny(db, workspace):
    with db._tx():
        if enabled(db, workspace):
            _audit(db, workspace, "denied", decision="deny", reason="refused")


def exchange(db, data):
    if set(data) != {
        "workspace_id",
        "machine_label",
        "manifest_digest",
        "code",
        "installation_id",
        "public_key",
    }:
        raise Refused()
    if (
        not all(isinstance(value, str) for value in data.values())
        or len(data["code"]) != 43
        or not REQUEST_ID.fullmatch(data["installation_id"])
    ):
        raise Refused()
    key, pem, key_digest = enrollment_crypto.public_key(data["public_key"])
    with db._tx():
        workspace = data["workspace_id"]
        _require_enabled(db, workspace)
        now, stamp = _time()
        _sweep_in(db, workspace, stamp)
        row = db._conn.execute(
            "SELECT * FROM runner_enrollments WHERE workspace_id=? AND code_hash=?",
            (workspace, _hash(data["code"])),
        ).fetchone()
        if (
            row is None
            or row["consumed_at"]
            or row["revoked_at"]
            or row["expires_at"] <= stamp
            or row["machine_label"] != data["machine_label"]
            or row["manifest_digest"] != data["manifest_digest"]
            or row["settings_version"] != settings(db, workspace)["version"]
        ):
            raise Refused()
        owner = sessions.person_principal(db, row["owner_id"], workspace)
        owner = owner_now(db, owner, refusal=403)
        consumed = db._conn.execute(
            "UPDATE runner_enrollments SET consumed_at=?, code_hash=NULL, machine_key_digest=? WHERE id=? AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at>?",
            (stamp, key_digest, row["id"], stamp),
        )
        if consumed.rowcount != 1:
            raise Refused()
        credentials = registry.enroll_in(db, owner, json.loads(row["intent"]))
        machine_id = credentials["machine_id"]
        db._conn.execute("UPDATE runner_machines SET enabled=0 WHERE id=?", (machine_id,))
        db._conn.execute(
            "UPDATE runner_enrollments SET machine_id=? WHERE id=?", (machine_id, row["id"])
        )
        context = json.dumps(
            {
                "version": 1,
                "enrollment_id": row["id"],
                "workspace_id": workspace,
                "machine_label": row["machine_label"],
                "machine_key_digest": key_digest,
                "manifest_digest": row["manifest_digest"],
                "installation_id": data["installation_id"],
                "machine_id": machine_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        envelope = enrollment_crypto.seal(key, credentials, context)
        db._conn.execute(
            "INSERT INTO runner_secret_deliveries(enrollment_id,workspace_id,installation_id,public_key,ciphertext,expires_at) VALUES (?,?,?,?,?,?)",
            (
                row["id"],
                workspace,
                data["installation_id"],
                pem,
                json.dumps(envelope),
                control_db._iso(now + timedelta(minutes=RECOVERY_MINUTES)),
            ),
        )
        _audit(db, workspace, "exchange", ids=(row["id"], machine_id))
        return envelope


def recover(db, data):
    identity = {"workspace_id", "enrollment_id", "installation_id", "public_key", "action"}
    action = data.get("action")
    if (
        action not in ("challenge", "recover", "ack")
        or set(data) != identity | (set() if action == "challenge" else {"challenge", "signature"})
        or not all(isinstance(value, str) for value in data.values())
    ):
        raise Refused()
    _, _, key_digest = enrollment_crypto.public_key(data["public_key"])
    with db._tx():
        workspace = data["workspace_id"]
        _require_enabled(db, workspace)
        now, stamp = _time()
        _sweep_in(db, workspace, stamp)
        row = db._conn.execute(
            "SELECT d.*, e.machine_key_digest, e.machine_id, e.owner_id, e.revoked_at, e.settings_version FROM runner_secret_deliveries d JOIN runner_enrollments e ON e.id=d.enrollment_id WHERE d.enrollment_id=? AND d.workspace_id=?",
            (data["enrollment_id"], workspace),
        ).fetchone()
        if (
            row is None
            or row["ciphertext"] is None
            or row["revoked_at"]
            or row["expires_at"] <= stamp
            or row["installation_id"] != data["installation_id"]
            or row["machine_key_digest"] != key_digest
            or row["settings_version"] != settings(db, workspace)["version"]
        ):
            raise Refused()
        owner_now(db, sessions.person_principal(db, row["owner_id"], workspace), refusal=403)
        machine = registry.get_machine(db, workspace, row["machine_id"])
        if (
            machine is None
            or machine["revoked_at"]
            or any(
                db.authenticate_connection(machine[f"{kind}_connection_id"]) is None
                for kind in ("machine", "worker")
            )
        ):
            raise Refused()
        if action == "challenge":
            challenge = (
                "runner-recovery-v1:" + row["enrollment_id"] + ":" + secrets.token_urlsafe(32)
            )
            db._conn.execute(
                "UPDATE runner_secret_deliveries SET challenge_hash=?, challenge_expires_at=? WHERE enrollment_id=?",
                (
                    _hash(challenge),
                    control_db._iso(now + timedelta(seconds=60)),
                    row["enrollment_id"],
                ),
            )
            _audit(db, workspace, "challenge", ids=(row["enrollment_id"],))
            return {"challenge": challenge}
        if (
            row["challenge_hash"] is None
            or row["challenge_expires_at"] <= stamp
            or not secrets.compare_digest(row["challenge_hash"], _hash(data["challenge"]))
        ):
            raise Refused()
        # Bind the signature to the operation as well as this short server nonce.
        # The challenge is consumed by either operation; it can never be replayed.
        enrollment_crypto.verify(
            row["public_key"], action + ":" + data["challenge"], data["signature"]
        )
        changed = db._conn.execute(
            "UPDATE runner_secret_deliveries SET challenge_hash=NULL, challenge_expires_at=NULL WHERE enrollment_id=? AND challenge_hash=?",
            (row["enrollment_id"], row["challenge_hash"]),
        )
        if changed.rowcount != 1:
            raise Refused()
        if action == "ack":
            _purge_delivery(db, row["enrollment_id"])
            db._conn.execute(
                "UPDATE runner_secret_deliveries SET acknowledged_at=? WHERE enrollment_id=?",
                (stamp, row["enrollment_id"]),
            )
            result = {"ok": True}
        else:
            result = json.loads(row["ciphertext"])
        _audit(db, workspace, action, ids=(row["enrollment_id"], row["machine_id"]))
        return result


class Sweeper:
    """Start only after explicit setup opt-in; no worker is created while off."""

    def __init__(self, db):
        self.db = db
        self.stopped = threading.Event()
        self.thread = None

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(
                target=self._run, name="runner-enrollment-expiry", daemon=True
            )
            self.thread.start()

    def _run(self):
        while not self.stopped.wait(60):
            try:
                sweep(self.db)
            except Exception:
                # Expiry is checked transactionally on every authority use too.
                # A later maintenance pass retries without retaining exception data.
                continue

    def stop(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=2)
