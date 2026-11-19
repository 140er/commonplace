"""Guided machine pairing: one owner request, one runner enrollment, per-step states.

A pairing links one signed-in owner request to one runner enrollment and holds what the
guided flow adds on top of it: the owner's single accept, a closed reason and the steps the
machine reports. It grants nothing by itself. Authority stays in ``runner_enrollments`` and
``runner_machines``: a machine runs Coworkers only when it is enabled and owner-attested, and
this module never attests. Every write is one control transaction with its audit rows. The
one-time code is returned by ``create`` and nowhere else.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets

from commonplace import control_db, urls
from commonplace.runner import enrollment, registry

from .control import StoreError

KINDS = ("runner", "target")
CODE_MINUTES = 15
OFFLINE_SECONDS = 900
MAX_OPEN = 5
LIST_LIMIT = 50
CREATE_PER_MINUTE = 10
CHECK_PER_MINUTE = 10
REPORT_PER_MINUTE = 60
REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
PAIRING_ID = re.compile(r"pair_[0-9a-f]{16}")
HUB = re.compile(r"https?://[A-Za-z0-9.\-]{1,253}(?::[0-9]{1,5})?")
STEPS = ("prerequisites", "files", "service", "check")
HEARTBEAT = "heartbeat"
DEFAULTS = {"max_runtime_mode": "approval-required", "max_in_flight": 1, "max_starts_per_hour": 6}
LISTS = ("projects", "topics", "wings", "requesters")
COPIED = LISTS + tuple(DEFAULTS)
CREATE_FIELDS = frozenset({"request_id", "kind", "label", "copy_from", *COPIED})
CREATE_REQUIRED = frozenset({"request_id", "kind", "label"})
ACCEPT_FIELDS = frozenset({"request_id", "expected_version", "identity_digest"})
CLOSE_FIELDS = frozenset({"request_id", "expected_version"})
SCHEMA = """
CREATE TABLE IF NOT EXISTS machine_pairings (
    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, kind TEXT NOT NULL, label TEXT NOT NULL,
    owner_id TEXT NOT NULL, request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
    enrollment_id TEXT NOT NULL UNIQUE REFERENCES runner_enrollments(id),
    version INTEGER NOT NULL, accepted_by TEXT, accepted_at TEXT, accepted_digest TEXT,
    accept_request_id TEXT, closed_at TEXT, closed_reason TEXT, close_request_id TEXT,
    last_report_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, owner_id, request_id)
);
CREATE TABLE IF NOT EXISTS machine_pairing_steps (
    pairing_id TEXT NOT NULL REFERENCES machine_pairings(id), step TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('ok','failed')), cause TEXT,
    reported_at TEXT NOT NULL, PRIMARY KEY(pairing_id, step)
);
"""

# (label, what is true, what to do next when the owner reads it, what a member reads).
STATES = {
    "waiting": (
        "Waiting for the machine",
        "The code is ready. Nothing has connected yet.",
        "Run the command on the machine. The code stops working after 15 minutes.",
        "The owner has started adding this machine. It has not connected yet.",
    ),
    "connected": (
        "Machine connected, waiting for your approval",
        "The machine used the code and connected. It cannot do anything until you approve it.",
        "Compare the machine's name and fingerprint, then approve or choose This is not my machine.",
        "The machine connected. The owner has to approve it.",
    ),
    "approved": (
        "Approved",
        "You approved this machine. It is finishing its setup.",
        "Nothing to do here. This page updates when the machine reports that its checks passed.",
        "The owner approved this machine. It is finishing its setup.",
    ),
    "ready": (
        "Set up and checked",
        "The machine passed its setup checks.",
        "Coworkers can run here only after a provider is connected and the machine is attested on the Runners screen.",
        "This machine is set up. Coworkers can run on it after the owner connects a provider.",
    ),
    "offline": (
        "Offline",
        "This machine sent signs of life before, and nothing has been heard for over 15 minutes.",
        "Check that the machine is on and online. It returns to Set up and checked by itself.",
        "This machine has not been heard from for over 15 minutes. The owner can check it.",
    ),
    "failed": (
        "A step failed",
        "A setup step failed on the machine.",
        "Read the cause below, fix it and run the same command again, or set the machine up again.",
        "A setup step failed on this machine. The owner can retry it.",
    ),
    "expired": (
        "Code expired",
        "The code was not used within 15 minutes and no longer works.",
        "Choose Set up again for a new command.",
        "The code expired before the machine connected. The owner can set it up again.",
    ),
    "cancelled": (
        "Cancelled",
        "This setup was cancelled before the machine connected. The code no longer works.",
        "Choose Set up again for a new command.",
        "The owner cancelled this setup.",
    ),
    "declined": (
        "Not approved",
        "You said this is not your machine. Its access was removed.",
        "Choose Set up again if you want to add a machine with the same settings.",
        "The owner did not approve this machine.",
    ),
    "revoked": (
        "Access removed",
        "This machine's access was removed.",
        "Choose Set up again to add it back.",
        "This machine's access was removed.",
    ),
}
# Fixed causes a machine may report for a failed step: (what happened, what to do).
CAUSES = {
    "run_as_root": (
        "The command was run as root.",
        "Run it again as the normal account that should own the runner, without sudo.",
    ),
    "unsupported_os": (
        "This system is not supported. Ubuntu 24.04 and Debian 12 or later are.",
        "Use a supported system.",
    ),
    "missing_python": (
        "A supported Python is not installed.",
        "Install Python 3.10 or later, then run the command again.",
    ),
    "missing_prerequisite": (
        "A required program is missing.",
        "Install what the machine printed, then run the command again.",
    ),
    "missing_runner_program": (
        "The runner program is not installed on this machine, and this command does not install it yet.",
        "Install the runner program first (see the runner guide), then run the command again.",
    ),
    "missing_privilege": (
        "The account is not allowed to set up its background service.",
        "Allow the account to keep a service running when logged out, then run the command again.",
    ),
    "no_service_manager": (
        "The machine has no running user service manager.",
        "Log in on the machine with a normal session, then run the command again.",
    ),
    "disk_full": (
        "There is not enough free disk space.",
        "Free some space, then run the command again.",
    ),
    "cannot_reach_hub": (
        "The machine could not reach this site.",
        "Check the machine's internet connection, then run the command again.",
    ),
    "service_failed": (
        "The background service did not start.",
        "Run the command again. If it fails again, set the machine up again.",
    ),
    "check_failed": (
        "The machine's own check did not pass.",
        "Run the command again. If it fails again, set the machine up again.",
    ),
    "unknown": (
        "A step failed for a reason this page does not recognise.",
        "Read what the machine printed, then run the command again.",
    ),
}
# Fixed reasons for every refusal: (what happened, what to do next).
REASONS = {
    "required": ("This is required.", "Fill it in."),
    "invalid": ("This value is not allowed.", "Change it and try again."),
    "unknown_field": ("The request has a field this page does not know.", "Reload the page."),
    "body": ("The request could not be read.", "Reload the page and try again."),
    "not_found": ("That does not exist in this workspace.", "Reload the page."),
    "setup_off": (
        "Machine setup is turned off for this workspace.",
        "Turn machine setup on, then add the machine.",
    ),
    "kind_unavailable": (
        "Script targets cannot be set up from the app yet.",
        "Choose a runner machine.",
    ),
    "label_in_use": (
        "A machine or a waiting setup already uses this name.",
        "Choose another name, or cancel the setup that uses it.",
    ),
    "too_many_open": (
        "Several setups are already waiting.",
        "Finish or cancel one of them first.",
    ),
    "rate_limited": ("Too many tries in a minute.", "Wait a minute and try again."),
    "hub_unavailable": (
        "This site has no public address configured, so a command cannot be made.",
        "Ask the person who runs this site to set its address.",
    ),
    "request_changed": (
        "This request id was already used for different settings.",
        "Reload the page and start again.",
    ),
    "stale_version": (
        "This machine changed since you opened it.",
        "Reload to see where it is now.",
    ),
    "identity_changed": (
        "The machine or its settings changed since you looked.",
        "Reload and compare again before approving.",
    ),
    "not_connected": (
        "The machine has not connected yet.",
        "Run the command on the machine first.",
    ),
    "step_failed": (
        "A setup step failed on the machine.",
        "Fix it first, or choose This is not my machine.",
    ),
    "already_approved": ("This machine was already approved.", "Reload to see where it is now."),
    "already_closed": ("This setup was already closed.", "Reload to see where it is now."),
    "nothing_to_close": (
        "Nothing is left to close: the code expired or the setup ended.",
        "Choose Set up again for a new command.",
    ),
    "closed": ("This setup was closed.", "Choose Set up again for a new command."),
    "expired": ("The code expired.", "Choose Set up again for a new command."),
    "used": (
        "The code was already used.",
        "Choose Set up again for a new command. A code works once.",
    ),
    "cancelled": ("This setup was cancelled.", "Choose Set up again for a new command."),
    "revoked": ("This setup was removed.", "Choose Set up again for a new command."),
    "not_for_this_workspace": (
        "This command belongs to a different workspace.",
        "Copy the command from the workspace you want the machine in.",
    ),
    "code_mismatch": (
        "The code is not recognised.",
        "Copy the whole command again from the app. Do not retype it.",
    ),
}
MEMBER_VIEW = (
    "id",
    "kind",
    "label",
    "state",
    "state_label",
    "sentence",
    "next",
    "cause",
    "steps",
    "last_report_at",
    "created_at",
    "expires_at",
)


def ensure(db):
    """Runner tables first: pairings reference enrollments. Once per database."""
    with db._lock:
        if getattr(db, "_pairing_schema", False):
            return
        registry.ensure(db)
        enrollment.ensure(db)
        db._conn.executescript(SCHEMA)
        db._pairing_schema = True


def refuse(field, reason, code="invalid_request", status=None):
    """A refusal that names its reason and, when it is about one field, the field."""
    status = status or (400 if code == "invalid_request" else 409)
    return StoreError(code, status, field=field, reason=reason)


def _now():
    now = control_db._now()
    return now, control_db._iso(now)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _owner(db, principal):
    try:
        return enrollment.owner_now(db, principal, refusal=403)
    except enrollment.Refused:
        raise StoreError("owner_required", 403, reason="member") from None


def _audit(db, workspace, action, *, owner=None, ids=(), decision="ok", reason=""):
    db.audit(
        action="runner.pairing." + action,
        decision=decision,
        workspace_id=workspace,
        user_id=owner.user_id if owner else None,
        auth="session" if owner else "pairing",
        target_ids=ids,
        reason=reason,
    )


# ---- reading -----------------------------------------------------------------------------------

_SELECT = (
    "SELECT p.*, e.expires_at AS code_expires_at, e.consumed_at, e.revoked_at AS code_revoked_at, "
    "e.machine_id, e.machine_key_digest, e.intent, e.manifest_digest, e.code_hash "
    "FROM machine_pairings p JOIN runner_enrollments e ON e.id=p.enrollment_id "
)


def _steps(db, pairing_id):
    rows = db._conn.execute(
        "SELECT step, state, cause, reported_at FROM machine_pairing_steps WHERE pairing_id=?",
        (pairing_id,),
    ).fetchall()
    return {row["step"]: row for row in rows}


def _age(stamp, now_stamp):
    then, now = control_db._parse(stamp), control_db._parse(now_stamp)
    return max(0, int((now - then).total_seconds()))


def derive(row, steps, now_stamp):
    """The one state of a pairing: ``(state, failed_step_or_None)``. Pure."""
    if row["closed_reason"]:
        return row["closed_reason"], None
    if row["code_revoked_at"]:
        if row["consumed_at"] is None:
            expired = row["code_expires_at"] <= row["code_revoked_at"]
            return ("expired" if expired else "cancelled"), None
        return "revoked", None
    failed = next((s for s in STEPS if s in steps and steps[s]["state"] == "failed"), None)
    if failed:
        return "failed", failed
    if row["accepted_at"]:
        if not all(s in steps and steps[s]["state"] == "ok" for s in STEPS):
            return "approved", None
        beat = steps.get(HEARTBEAT)
        if beat is not None and _age(beat["reported_at"], now_stamp) > OFFLINE_SECONDS:
            return "offline", None
        return "ready", None
    if row["consumed_at"]:
        return "connected", None
    if row["code_expires_at"] <= now_stamp:
        return "expired", None
    return "waiting", None


def fingerprint(row):
    """The machine key digest in groups of eight, exactly as the installer prints it."""
    digest = row["machine_key_digest"]
    return None if not digest else " ".join(re.findall(".{8}", digest))


def identity_digest(row):
    """What the owner is shown and accepts: this pairing, this machine key, these settings."""
    return _digest(
        {
            "pairing": row["id"],
            "enrollment": row["enrollment_id"],
            "kind": row["kind"],
            "label": row["label"],
            "machine_key": row["machine_key_digest"],
            "intent": json.loads(row["intent"]),
        }
    )


def _grant_view(row):
    grant = json.loads(row["intent"])
    return {key: grant.get(key, [] if key in LISTS else DEFAULTS[key]) for key in COPIED}


def _step_view(steps):
    result = []
    for step in STEPS:
        item = steps.get(step)
        entry = {"step": step, "state": item["state"] if item else "pending"}
        if item and item["state"] == "failed":
            entry["cause"] = item["cause"]
        if item:
            entry["reported_at"] = item["reported_at"]
        result.append(entry)
    return result


def _cause(steps, failed):
    if not failed:
        return None
    code = steps[failed]["cause"]
    code = code if code in CAUSES else "unknown"
    return {"step": failed, "code": code, "text": CAUSES[code][0], "next": CAUSES[code][1]}


def view(db, row, now_stamp, *, owner):
    """The projection of one pairing. A member's has no fingerprint, grant, id or digest."""
    steps = _steps(db, row["id"])
    state, failed = derive(row, steps, now_stamp)
    label, sentence, next_owner, next_member = STATES[state]
    cause = _cause(steps, failed)
    result = {
        "id": row["id"],
        "kind": row["kind"],
        "label": row["label"],
        "state": state,
        "state_label": label,
        "sentence": sentence,
        "next": next_owner if owner else next_member,
        "cause": cause,
        "steps": _step_view(steps),
        "last_report_at": row["last_report_at"],
        "created_at": row["created_at"],
        "expires_at": row["code_expires_at"],
    }
    if cause and not owner:
        result["cause"] = {key: cause[key] for key in ("step", "code", "text")}
        result["cause"]["next"] = "Ask the workspace owner to retry it."
    if not owner:
        return {key: result[key] for key in MEMBER_VIEW}
    machine = None
    if row["machine_id"]:
        machine = db._conn.execute(
            "SELECT enabled, binding_state, revoked_at FROM runner_machines WHERE id=?",
            (row["machine_id"],),
        ).fetchone()
    closed = state in ("expired", "cancelled", "declined", "revoked")
    result.update(
        version=row["version"],
        fingerprint=fingerprint(row),
        grant=_grant_view(row),
        identity_digest=identity_digest(row) if row["consumed_at"] else None,
        accepted_at=row["accepted_at"],
        binding_state=machine["binding_state"] if machine and not machine["revoked_at"] else None,
        can_accept=state == "connected",
        can_close=state in ("waiting", "connected", "approved", "ready", "offline", "failed"),
        retry=_retry(row) if closed or state == "failed" else None,
    )
    return result


def _retry(row):
    """The values to prefill when the owner sets this machine up again."""
    return {"kind": row["kind"], "label": row["label"], **_grant_view(row)}


def _rows(db, workspace, where="", args=()):
    return db._conn.execute(
        _SELECT + "WHERE p.workspace_id=? " + where + " ORDER BY p.created_at DESC, p.id DESC",
        (workspace, *args),
    ).fetchall()


def list_pairings(db, principal, *, owner):
    _, stamp = _now()
    with db._lock:
        rows = _rows(db, principal.workspace_id)
        shown = rows[:LIST_LIMIT]
        return {
            "pairings": [view(db, row, stamp, owner=owner) for row in shown],
            "total": len(rows),
            "server_time": stamp,
            "setup_enabled": enrollment.enabled(db, principal.workspace_id),
            "offline_after_seconds": OFFLINE_SECONDS,
        }


def get(db, principal, pairing_id, *, owner):
    _, stamp = _now()
    with db._lock:
        row = _find(db, principal.workspace_id, pairing_id)
        return {"pairing": view(db, row, stamp, owner=owner), "server_time": stamp}


def _find(db, workspace, pairing_id):
    row = None
    if isinstance(pairing_id, str) and PAIRING_ID.fullmatch(pairing_id):
        row = db._conn.execute(
            _SELECT + "WHERE p.workspace_id=? AND p.id=?", (workspace, pairing_id)
        ).fetchone()
    if row is None:
        raise StoreError("not_found", 404, reason="route")
    return row


def options(db, gateway, principal, *, owner):
    result = {
        "kinds": [
            {
                "id": "runner",
                "label": "Runner machine",
                "description": "A machine that runs Coworkers.",
                "available": True,
            },
            {
                "id": "target",
                "label": "Script target",
                "description": "A machine that runs approved scripts over SSH.",
                "available": False,
                "reason": "kind_unavailable",
            },
        ],
        "setup_enabled": enrollment.enabled(db, principal.workspace_id),
        "code_minutes": CODE_MINUTES,
        "offline_after_seconds": OFFLINE_SECONDS,
        "states": {
            key: {"label": v[0], "sentence": v[1], "next": v[2 if owner else 3]}
            for key, v in STATES.items()
        },
        "causes": {key: {"text": v[0], "next": v[1]} for key, v in CAUSES.items()},
        "reasons": {key: {"text": v[0], "next": v[1]} for key, v in REASONS.items()},
        "steps": list(STEPS),
    }
    if not owner:
        return result
    from commonplace.web.agents import grantable_wings

    with db._lock:
        machines = [m for m in registry.machines(db, principal.workspace_id) if not m["revoked_at"]]
    result.update(
        defaults=dict(DEFAULTS),
        modes=sorted(registry.MODES),
        wings=grantable_wings(gateway, principal),
        copy_from=[
            {
                "id": m["id"],
                "label": m["label"],
                **{
                    key: json.loads(m[key]) for key in ("projects", "topics", "wings", "requesters")
                },
                **{key: m[key] for key in DEFAULTS},
            }
            for m in machines
        ],
    )
    return result


# ---- creating ----------------------------------------------------------------------------------


def _body_fields(body):
    if set(body) - CREATE_FIELDS:
        raise refuse(None, "unknown_field")
    for key in sorted(CREATE_REQUIRED - set(body)):
        raise refuse(key, "required")
    request, kind, label = body["request_id"], body["kind"], body["label"]
    if not isinstance(request, str) or not REQUEST_ID.fullmatch(request):
        raise refuse("request_id", "invalid")
    if kind not in KINDS:
        raise refuse("kind", "invalid")
    if kind != "runner":
        raise refuse("kind", "kind_unavailable", "conflict")
    if not isinstance(label, str) or not registry.LABEL.fullmatch(label):
        raise refuse("label", "invalid")
    if registry.MACHINE_ID.fullmatch(label):
        raise refuse("label", "invalid")
    copy = body.get("copy_from")
    if copy is not None and (not isinstance(copy, str) or not 0 < len(copy) <= 80):
        raise refuse("copy_from", "invalid")
    for key in ("projects", "topics", "wings", "requesters"):
        if key in body and (
            not isinstance(body[key], list) or any(not isinstance(v, str) for v in body[key])
        ):
            raise refuse(key, "invalid")
    return body


def _resolve_grant(db, owner, body):
    """The frozen grant: the copied machine's values, then the explicit fields, then defaults."""
    grant = {}
    copy = body.get("copy_from")
    if copy is not None:
        machine = registry.get_machine(db, owner.workspace_id, copy)
        if machine is None or machine["revoked_at"]:
            raise refuse("copy_from", "not_found")
        grant = {
            key: json.loads(machine[key]) for key in ("projects", "topics", "wings", "requesters")
        }
        grant.update({key: machine[key] for key in DEFAULTS})
    grant.update({key: body[key] for key in COPIED if key in body})
    for key in ("projects", "topics", "wings"):
        if not grant.get(key):
            raise refuse(key, "required")
    for key, default in DEFAULTS.items():
        grant.setdefault(key, default)
    grant["label"] = body["label"]
    try:
        return registry._grant(db, owner, grant, initial=True)
    except ValueError as exc:
        text = str(exc)
        field = next(
            (
                key
                for key in (
                    "projects",
                    "topics",
                    "wings",
                    "requesters",
                    "max_in_flight",
                    "max_starts_per_hour",
                    "max_runtime_mode",
                )
                if key in text
            ),
            None,
        )
        raise refuse(field, "invalid") from None


def _envelope(hub, workspace, pairing_id, enrollment_id, label, digest):
    public = {
        "v": 1,
        "hub": hub,
        "workspace_id": workspace,
        "pairing_id": pairing_id,
        "enrollment_id": enrollment_id,
        "label": label,
        "manifest_digest": digest,
        "kind": "runner",
    }
    return base64.urlsafe_b64encode(_canonical(public).encode()).decode().rstrip("=")


def render_command(hub, envelope, code):
    """The one command. The code goes in on standard input; the envelope has public values only.

    The leading space keeps the line out of history on shells that honour it. The line holds the
    one-time code, which is useless after first use or expiry.
    """
    return (
        f" printf '%s' '{code}' | sh -c \"$(curl -fsSL '{hub}/machine-setup.sh')\" "
        f"machine-setup '{envelope}'"
    )


def _open_count(db, workspace, stamp):
    return db._conn.execute(
        "SELECT COUNT(*) FROM machine_pairings p JOIN runner_enrollments e ON e.id=p.enrollment_id "
        "WHERE p.workspace_id=? AND p.closed_reason IS NULL AND e.consumed_at IS NULL "
        "AND e.revoked_at IS NULL AND e.expires_at>?",
        (workspace, stamp),
    ).fetchone()[0]


def _check_wings(gateway, principal, body):
    """Named wings must exist and be grantable. Outside the control lock: the listing can be slow."""
    if "wings" not in body:
        return
    from commonplace.runner.owner_api import _wings

    try:
        _wings(gateway, principal, {"wings": body["wings"]})
    except ValueError:
        raise refuse("wings", "invalid") from None


def create(db, gateway, principal, body):
    """Issue one runner enrollment and its pairing. Returns ``(created, view, setup)``.

    ``setup`` carries the one-time code and the command once; a replay returns ``None``.
    """
    body = _body_fields(body)
    if not enrollment.enabled(db, principal.workspace_id):
        raise refuse(None, "setup_off", "conflict")
    _check_wings(gateway, principal, body)
    hub = urls.APP_URL
    request_hash = _digest(body)
    with db._tx():
        owner = _owner(db, principal)
        _, stamp = _now()
        previous = db._conn.execute(
            _SELECT + "WHERE p.workspace_id=? AND p.owner_id=? AND p.request_id=?",
            (owner.workspace_id, owner.user_id, body["request_id"]),
        ).fetchone()
        if previous:
            if previous["request_hash"] != request_hash:
                raise refuse("request_id", "request_changed", "conflict")
            return False, view(db, previous, stamp, owner=True), None
        if not enrollment.enabled(db, owner.workspace_id):
            raise refuse(None, "setup_off", "conflict")
        if not HUB.fullmatch(hub):
            raise refuse(None, "hub_unavailable", "conflict", 503)
        if _open_count(db, owner.workspace_id, stamp) >= MAX_OPEN:
            raise refuse(None, "too_many_open", "conflict", 429)
        grant = _resolve_grant(db, owner, body)
        digest = _digest({"v": 1, "kind": body["kind"], "label": grant["label"], "grant": grant})
        try:
            _, issued = enrollment.issue(
                db,
                owner,
                {"request_id": body["request_id"], "manifest_digest": digest, "grant": grant},
                minutes=CODE_MINUTES,
                in_tx=True,
            )
        except registry.Conflict as exc:
            reason = "request_changed" if "changed" in str(exc) else "label_in_use"
            raise refuse(
                "request_id" if reason == "request_changed" else "label", reason, "conflict"
            ) from None
        pairing_id = control_db._new_id("pair")
        db._conn.execute(
            "INSERT INTO machine_pairings(id, workspace_id, kind, label, owner_id, request_id, request_hash, enrollment_id, version, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                pairing_id,
                owner.workspace_id,
                body["kind"],
                grant["label"],
                owner.user_id,
                body["request_id"],
                request_hash,
                issued["id"],
                1,
                stamp,
                stamp,
            ),
        )
        _audit(db, owner.workspace_id, "create", owner=owner, ids=(pairing_id, issued["id"]))
        row = db._conn.execute(_SELECT + "WHERE p.id=?", (pairing_id,)).fetchone()
        envelope = _envelope(
            hub, owner.workspace_id, pairing_id, issued["id"], grant["label"], digest
        )
        setup = {
            "code": issued["code"],
            "envelope": envelope,
            "command": render_command(hub, envelope, issued["code"]),
            "expires_at": issued["expires_at"],
        }
        return True, view(db, row, stamp, owner=True), setup


# ---- the owner's decisions ---------------------------------------------------------------------


def _expect(row, version):
    if type(version) is not int or isinstance(version, bool):
        raise refuse("expected_version", "invalid")
    if row["version"] != version:
        raise refuse("expected_version", "stale_version", "conflict")


def _fields(body, allowed, required):
    if set(body) - allowed:
        raise refuse(None, "unknown_field")
    for key in sorted(required - set(body)):
        raise refuse(key, "required")
    request = body.get("request_id")
    if request is not None and (not isinstance(request, str) or not REQUEST_ID.fullmatch(request)):
        raise refuse("request_id", "invalid")


def accept(db, principal, pairing_id, body):
    """The one accept: enable this machine after the owner saw this identity. Returns the view."""
    _fields(body, ACCEPT_FIELDS, ACCEPT_FIELDS)
    with db._tx():
        owner = _owner(db, principal)
        _, stamp = _now()
        row = _find(db, owner.workspace_id, pairing_id)
        if row["accept_request_id"] == body["request_id"] and row["accepted_at"]:
            return view(db, row, stamp, owner=True)
        _expect(row, body["expected_version"])
        steps = _steps(db, row["id"])
        state, _failed = derive(row, steps, stamp)
        if row["accepted_at"]:
            raise refuse(None, "already_approved", "conflict")
        if state == "failed":
            raise refuse(None, "step_failed", "conflict")
        if state != "connected":
            reason = "not_connected" if state == "waiting" else state
            raise refuse(None, reason if reason in REASONS else "closed", "conflict")
        if body["identity_digest"] != identity_digest(row):
            raise refuse("identity_digest", "identity_changed", "conflict")
        machine = registry.get_machine(db, owner.workspace_id, row["machine_id"])
        if machine is None or machine["revoked_at"]:
            raise refuse(None, "revoked", "conflict")
        db._conn.execute(
            "UPDATE runner_machines SET enabled=1, updated_by=?, updated_at=? WHERE id=? AND revoked_at IS NULL",
            (owner.user_id, stamp, machine["id"]),
        )
        db._conn.execute(
            "UPDATE machine_pairings SET accepted_by=?, accepted_at=?, accepted_digest=?, accept_request_id=?, version=version+1, updated_at=? WHERE id=?",
            (owner.user_id, stamp, body["identity_digest"], body["request_id"], stamp, row["id"]),
        )
        _audit(db, owner.workspace_id, "accept", owner=owner, ids=(row["id"], machine["id"]))
        return view(db, _find(db, owner.workspace_id, row["id"]), stamp, owner=True)


def close(db, principal, pairing_id, body):
    """Cancel a waiting setup, refuse a connected machine, or remove an approved one."""
    _fields(body, CLOSE_FIELDS, CLOSE_FIELDS)
    with db._tx():
        owner = _owner(db, principal)
        _, stamp = _now()
        row = _find(db, owner.workspace_id, pairing_id)
        if row["closed_reason"] and row["close_request_id"] == body["request_id"]:
            return view(db, row, stamp, owner=True)
        _expect(row, body["expected_version"])
        state, _failed = derive(row, _steps(db, row["id"]), stamp)
        if row["closed_reason"]:
            raise refuse(None, "already_closed", "conflict")
        if state in ("expired", "cancelled", "declined", "revoked"):
            raise refuse(None, "nothing_to_close", "conflict")
        reason = (
            "cancelled"
            if not row["consumed_at"]
            else "revoked"
            if row["accepted_at"]
            else "declined"
        )
        enrollment_row = db._conn.execute(
            "SELECT * FROM runner_enrollments WHERE id=?", (row["enrollment_id"],)
        ).fetchone()
        enrollment._invalidate(db, enrollment_row, stamp, owner)
        db._conn.execute(
            "UPDATE machine_pairings SET closed_at=?, closed_reason=?, close_request_id=?, version=version+1, updated_at=? WHERE id=?",
            (stamp, reason, body["request_id"], stamp, row["id"]),
        )
        _audit(db, owner.workspace_id, "close", owner=owner, ids=(row["id"], reason))
        return view(db, _find(db, owner.workspace_id, row["id"]), stamp, owner=True)


# ---- what the machine says ---------------------------------------------------------------------


def _store_step(db, row, step, state, cause, stamp):
    """Upsert one step report; only a change is audited. Returns True when something changed."""
    previous = db._conn.execute(
        "SELECT state, cause FROM machine_pairing_steps WHERE pairing_id=? AND step=?",
        (row["id"], step),
    ).fetchone()
    db._conn.execute(
        "INSERT INTO machine_pairing_steps(pairing_id, step, state, cause, reported_at) VALUES (?,?,?,?,?) ON CONFLICT(pairing_id, step) DO UPDATE SET state=excluded.state, cause=excluded.cause, reported_at=excluded.reported_at",
        (row["id"], step, state, cause, stamp),
    )
    changed = previous is None or previous["state"] != state or previous["cause"] != cause
    if changed:
        _audit(
            db,
            row["workspace_id"],
            "report",
            ids=(row["id"], step),
            decision="ok" if state == "ok" else "deny",
            reason=cause or "",
        )
    return changed


def _step_report(report, *, allowed):
    """Validate ``{step, state, cause?}``. A heartbeat is ``{step: 'heartbeat'}``."""
    if not isinstance(report, dict) or set(report) - {"step", "state", "cause"}:
        raise ValueError("invalid report")
    step = report.get("step")
    if step == "heartbeat":
        if set(report) != {"step"}:
            raise ValueError("invalid report")
        return step, None, None
    state, cause = report.get("state"), report.get("cause")
    if step not in allowed or state not in ("ok", "failed"):
        raise ValueError("invalid report")
    if (state == "failed") != (cause is not None) or (cause is not None and cause not in CAUSES):
        raise ValueError("invalid report")
    return step, state, cause


def report(db, principal, data):
    """Machine-self step report. Facts for the owner's page; never authority."""
    from commonplace.runner import setup_approvals

    step, state, cause = _step_report(data, allowed=STEPS)
    with db._tx():
        machine = setup_approvals.machine_now(db, principal)
        workspace = machine["workspace_id"]
        if not enrollment.enabled(db, workspace):
            raise enrollment.Refused(404)
        _, stamp = _now()
        row = db._conn.execute(
            _SELECT + "WHERE p.workspace_id=? AND e.machine_id=?", (workspace, machine["id"])
        ).fetchone()
        if row is None:
            raise enrollment.Refused(404)
        if row["closed_reason"] or row["code_revoked_at"]:
            raise refuse(None, "closed", "conflict")
        _store_step(
            db, row, HEARTBEAT if step == "heartbeat" else step, state or "ok", cause, stamp
        )
        db._conn.execute(
            "UPDATE machine_pairings SET last_report_at=?, updated_at=? WHERE id=?",
            (stamp, stamp, row["id"]),
        )
        row = db._conn.execute(_SELECT + "WHERE p.id=?", (row["id"],)).fetchone()
        return {"ok": True, "state": derive(row, _steps(db, row["id"]), stamp)[0]}


def check(db, data):
    """Code-and-pairing-id authority: may this code still work, and say why when it cannot.

    Used by the installer before it spends the code. A ``report`` field lets it say why a machine
    cannot be set up (the prerequisites step) without redeeming anything. Only the holder of a
    live code can write; every other answer is a reason, never a state change.
    """
    if (
        not isinstance(data, dict)
        or not {"workspace_id", "pairing_id", "code"} <= set(data)
        or set(data) - {"workspace_id", "pairing_id", "code", "report"}
        or any(not isinstance(data[k], str) for k in ("workspace_id", "pairing_id", "code"))
        or len(data["workspace_id"]) > 80
    ):
        raise ValueError("invalid check")
    step = state = cause = None
    if "report" in data:
        step, state, cause = _step_report(data["report"], allowed=("prerequisites",))
        if step == "heartbeat":
            raise ValueError("invalid check")
    workspace = data["workspace_id"]
    with db._tx():
        _, stamp = _now()
        row = None
        if PAIRING_ID.fullmatch(data["pairing_id"]):
            row = db._conn.execute(_SELECT + "WHERE p.id=?", (data["pairing_id"],)).fetchone()
        reason = _check_reason(db, row, workspace, data["code"], stamp)
        if reason is None:
            if step:
                _store_step(db, row, step, state, cause, stamp)
            return {
                "usable": True,
                "label": row["label"],
                "kind": row["kind"],
                "expires_at": row["code_expires_at"],
            }
    return {
        "usable": False,
        "reason": reason,
        "text": REASONS[reason][0],
        "next": REASONS[reason][1],
    }


def _check_reason(db, row, workspace, code, stamp):
    if row is None:
        return "code_mismatch"
    if row["workspace_id"] != workspace:
        return "not_for_this_workspace"
    if not enrollment.enabled(db, workspace):
        return "setup_off"
    state, _failed = derive(row, _steps(db, row["id"]), stamp)
    if row["consumed_at"] and not row["code_revoked_at"] and not row["closed_reason"]:
        return "used"
    if state in ("expired", "cancelled", "revoked"):
        return state
    if state == "declined":
        return "revoked"
    if row["consumed_at"]:
        return "used"
    if row["code_hash"] is None or len(code) != 43:
        return "code_mismatch"
    if not secrets.compare_digest(row["code_hash"], enrollment._hash(code)):
        return "code_mismatch"
    return None
