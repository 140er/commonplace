"""Compute overview: machines, runners and cloud workspaces with honest status, health and usage.

Read model and owner actions behind ``/api/compute/overview``. Everything is read from what the hub
already keeps (the runner registry, the runner job and ledger store, the topology signals, the
Compute store); nothing here opens a connection to a machine, reads custody or probes. A status
never says "running" or "online" without a fresh report, and a usage value nobody reported is
``{"known": false, "reason": ...}``, never zero. Members read cloud workspaces only, with no
machine name; every action is owner-only and a stale token is a conflict.

Docs: docs/system/compute-overview.md.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone

from . import workspace_lifecycle as lifecycle, workspaces
from .control import StoreError

KINDS = ("machine", "runner", "workspace")
REFRESH_SECONDS = 30
MAX_ITEMS = 100
ACTIVITY_LIMIT = 10
SPEND_DAYS = 30
RESOURCE_STALE_S = 600  # a runner resource report older than this is shown as stale
RECENT_STARTED_S = (
    6 * 3600
)  # a run recorded as started this long ago and not updated is no longer counted
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}", re.ASCII)
_COUNTED = ("started", "completed", "errored", "stopped", "uncertain")

FAILURE_TEXT = {
    "out_of_memory": "It ran out of memory. Raise the memory limit in Change settings.",
    "storage_full": "Its storage is full. Raise the storage limit in Change settings.",
    "image_pull_failed": "The machine could not download the image. Check the image address in Change settings.",
    "image_not_found": "No such image was found. Check the image name and version in Change settings.",
    "registry_login_failed": "The registry refused the login. Choose a public image in Change settings.",
    "machine_too_small": "The machine is smaller than these limits. Lower them or choose a bigger machine in Change settings.",
    "limit_not_enforceable": "The machine cannot enforce these limits, so it was not started. Nothing ran without limits.",
    "machine_unreachable": "The machine could not be reached.",
    "start_failed": "It did not start.",
}
REFUSAL_TEXT = {
    "no_machine": "It needs a machine before it can start. Choose one with Change settings once a machine is set up.",
    "needs_credential": "This image is private and Commonplace can't store registry logins yet. Choose a public image first.",
    "request_pending": "A request is already waiting for this workspace. It has to be carried out or expire before another one can be made.",
    "already_running": "It is already running.",
    "already_starting": "It is already starting.",
    "still_stopping": "It is still stopping. Try again when it has stopped.",
    "already_stopped": "It is already stopped.",
    "already_stopping": "It is already stopping.",
    "not_started": "It has not been started yet. Start it first.",
    "not_running": "Only a running workspace can be restarted. Use Start for a stopped one.",
    "nothing_to_withdraw": "No request is waiting.",
    "already_picked_up": "A machine has already picked this request up, so it can't be withdrawn. Stop the workspace instead.",
    "already_draining": "It is already draining.",
    "not_draining": "It is not draining.",
    "revoked": "This runner was removed, so nothing can be changed on it.",
}
ACTION_LABEL = {
    "start": "Start",
    "stop": "Stop",
    "restart": "Restart",
    "withdraw": "Withdraw request",
    "drain": "Drain",
    "resume": "Resume",
}
WORKSPACE_ACTIONS = lifecycle.WORKSPACE_ACTIONS
RUNNER_ACTIONS = ("drain", "resume")
# Drain stops only the hub handing out card runs (``capacity.available`` is read by ``jobs.lease``); chat and
# Coworker session leases do not read it. Every sentence about drain says this, from this one string.
DRAIN_SCOPE = "No new card runs start here. Chat and Coworker sessions can still start here."
NO_RESUME = "Resuming it is not available in this version."
# The cloud workspace vocabulary of the workspaces list (compute-workspaces.js: NOT_STARTED_NOTE, NO_MACHINES_SENTENCE and
# the "Saved, not started" label), so the overview and the list never describe one record in two ways. A test compares
# these strings with the script.
SAVED_LABEL = "Saved, not started"
NOT_STARTED_NOTE = "Starting is not available yet, so this only keeps your settings."
NO_MACHINES_SENTENCE = "No machine is set up yet, so this stays on Choose later. Starting will need one; that is not available yet."
# The one next step per status, for a status a person has to act on or wait out. Pointers name the place.
NEXT_STEP = {
    "offline": "Check that the machine is on and the runner program is running there. New card runs wait until it reports again.",
    "slow": "Wait a little: a busy machine or a poor connection can delay a report. If it stays like this, check the machine's network.",
    "never_connected": "Finish its setup on the machine (Admin, Runner machines lists what to run there).",
    "unknown": "Nothing newer has been reported. Check the machine that carries it.",
    "not_verified": "Verify it under Advanced on this page, where its settings are.",
    "verification_expired": "Verify it again under Advanced on this page, where its settings are.",
    "disabled": "Turn it on again in Admin, Runner machines.",
}


def conflict(reason, field=None):
    return StoreError("conflict", 409, field=field, reason=reason)


def not_found(reason="record"):
    return StoreError("not_found", 404, reason=reason)


# words


def span(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        n, unit = seconds, "second"
    elif seconds < 3600:
        n, unit = seconds // 60, "minute"
    elif seconds < 172800:
        n, unit = seconds // 3600, "hour"
    else:
        n, unit = seconds // 86400, "day"
    return f"{n} {unit}{'' if n == 1 else 's'}"


def ago(seconds):
    return "just now" if seconds is not None and seconds < 5 else f"{span(seconds)} ago"


def _status(code, tone, label, detail):
    return {
        "code": code,
        "tone": tone,
        "label": label,
        "detail": detail,
        "next": NEXT_STEP.get(code),
    }


def _runs(count):
    return f"{count} run{'' if count == 1 else 's'}"


def _limits_text(row):
    cores = row["cpu_millicores"] / 1000
    cores = int(cores) if cores == int(cores) else cores
    memory = row["memory_mib"] / 1024
    memory = int(memory) if memory == int(memory) else round(memory, 3)
    return (
        f"{cores} CPU {'core' if cores == 1 else 'cores'}, {memory} GiB memory, "
        f"{row['storage_gib']} GiB storage"
    )


def _gib(mb):
    return f"{mb / 1024:.1f} GiB"


def _money(micros, currency):
    return f"{micros / 1_000_000:,.2f} {currency}"


# cloud workspaces


def effective_phase(row, life):
    """The phase to show. A record the old seam marked ready or failed (no lifecycle row) keeps that
    meaning, but a ``ready`` nobody reported has no report time, so it is never trusted."""
    if life["reported_at"] is not None or life["seq"] > 0 or life["phase"] != "not_started":
        return life["phase"], life["reason"] or (
            row["state_reason"] if life["phase"] == "failed" else None
        )
    if row["state"] == "ready":
        return "running", None
    if row["state"] == "failed":
        return "failed", row["state_reason"]
    return ("stopped", None) if row["applied_revision"] is not None else ("not_started", None)


def _saved_status(row, *, manage, executor, has_machines):
    """A record nothing has started: one label and one quiet tone whether or not a machine is chosen (a missing
    machine is not an attention state: the record can stay on Choose later)."""
    note = (
        NOT_STARTED_NOTE
        if not executor
        else "It has been saved in Commonplace and has not been started."
    )
    if row["node_id"] is not None:
        return _status("not_started", "quiet", SAVED_LABEL, note)
    if not manage:
        detail = f"{note} The workspace owner can choose a machine."
    elif has_machines is False:
        detail = NO_MACHINES_SENTENCE
    else:
        detail = f"{note} Choose a machine for it with Change settings."
    return _status("not_started", "quiet", SAVED_LABEL, detail)


def workspace_status(row, life, now, *, manage, executor, has_machines=None):
    phase, reason = effective_phase(row, life)
    base, _ = workspaces.status_of(row)
    pending = life["request_action"]
    claimed = life["claimed_at"] is not None
    age = lifecycle.report_age(life, now)
    if workspaces.public(row, manage=manage)["image_unreadable"]:
        return _status(
            "image_unreadable",
            "wait",
            "Image unreadable",
            "The saved image address can't be read. Choose the image again with Change settings."
            if manage
            else "The saved image address can't be read. The workspace owner can choose the image again.",
        )
    if row["node_id"] is None:
        return _saved_status(row, manage=manage, executor=executor, has_machines=has_machines)
    if base == "needs_credential":
        return _status(
            "private_image",
            "wait",
            "Private image",
            "This image is private and Commonplace can't store registry logins yet. Choose a public image."
            if manage
            else "This image is private, so it can't start. The workspace owner can choose a public image.",
        )
    if pending and not claimed:
        word = {"start": "Start", "stop": "Stop", "restart": "Restart"}[pending]
        when = f" {ago(max(0, int(now - life['requested_at'])))}" if life["requested_at"] else ""
        wait = (
            " Starting cloud workspaces on a machine is not available in this version, so it will wait."
            if not executor
            else " It starts when the machine next asks for work."
        )
        return _status(
            pending + "_requested",
            "wait",
            f"{word} requested",
            f"Commonplace saved the request{when}. No machine has picked it up, so nothing has "
            f"{'started' if pending != 'stop' else 'stopped'} yet.{wait}"
            + (
                f" It expires after {lifecycle.REQUEST_TTL_S // 3600} hours if no machine picks it up. "
                "Withdrawing it is not available in this version."
                if manage
                else ""
            ),
        )
    trusted = _trusted(phase, life, now)
    if pending and phase == "stopped":
        return _status("starting", "wait", "Restarting", "It stopped and is about to start again.")
    if phase in ("running", "starting", "stopping") and not trusted:
        said = {"running": "Running", "starting": "Starting", "stopping": "Stopping"}[phase]
        if age is None:
            detail = f"Nothing has reported on it, so Commonplace does not know whether it is {said.lower()}."
        else:
            detail = (
                f"Its last report said {said} and nothing newer has arrived, "
                f"so it may not be {said.lower()} any more."
            )
        return _status("unknown", "quiet", "Unknown", detail)
    if phase == "running":
        stale_settings = workspaces._restart_needed(row)
        return _status(
            "running",
            "ok",
            "Running",
            "It runs with the settings from before your last change; restart it to apply them."
            if stale_settings
            else "It runs with the saved settings.",
        )
    if phase == "starting":
        return _status("starting", "wait", "Starting", "The machine is starting it.")
    if phase == "stopping":
        return _status("stopping", "wait", "Stopping", "The machine is stopping it.")
    if phase == "stopped":
        return _status(
            "stopped",
            "quiet",
            "Stopped",
            "It is stopped and its saved files are kept."
            + (
                " It starts again with the latest settings."
                if workspaces._restart_needed(row)
                else ""
            ),
        )
    if phase == "failed":
        text = FAILURE_TEXT.get(
            reason, "It failed for a reason this page does not recognise. Check the machine."
        )
        return _status("failed", "bad", "Failed", text)
    return _saved_status(row, manage=manage, executor=executor, has_machines=has_machines)


def _trusted(phase, life, now):
    """A running, starting or stopping phase is believed only while its report is fresh."""
    if phase in ("starting", "running", "stopping"):
        age = lifecycle.report_age(life, now)
        return age is not None and age <= lifecycle.REPORT_STALE_S
    return True


def _machine_verified(row):
    """The chosen machine's enrollment state (``verified`` only after a signed check; never a live probe)."""
    try:
        meta = json.loads(row["node_metadata"]) if row["node_metadata"] else {}
    except ValueError:
        meta = {}
    return meta.get("state") == "verified"


def _workspace_blockers(row, status, executor, manage, has_machines):
    blockers = []
    if row["node_id"] is None:
        if manage and has_machines is False:
            text = "No machine is set up yet; starting will need one, which is not available yet."
        else:
            text = "Machine: not chosen yet." + (
                " Choose one with Change settings." if manage else ""
            )
        blockers.append({"code": "no_machine", "text": text})
    if row["node_id"] is not None and not _machine_verified(row):
        blockers.append(
            {
                "code": "machine_not_verified",
                "text": "Its machine is not verified yet, so nothing can start on it.",
            }
        )
    if status["code"] == "private_image":
        blockers.append(
            {
                "code": "private_image",
                "text": "Its image is private and registry logins can't be stored yet.",
            }
        )
    if not executor:
        blockers.append(
            {
                "code": "not_available",
                "text": "Starting cloud workspaces on a machine is not available in this version.",
            }
        )
    return blockers


def workspace_item(row, life, now, *, manage, executor, has_machines=None):
    life = lifecycle.effective(life, now)  # an expired request is not a request
    status = workspace_status(
        row, life, now, manage=manage, executor=executor, has_machines=has_machines
    )
    public = workspaces.public(row, manage=manage)
    phase, _ = effective_phase(row, life)
    age = lifecycle.report_age(life, now)
    attention = status["code"] in ("failed", "unknown", "image_unreadable")
    blockers = _workspace_blockers(row, status, executor, manage, has_machines)
    health = {
        "state": "attention" if attention else "ok" if status["code"] == "running" else "idle",
        "blockers": blockers,
        "issues": [],
    }
    if life.get("expired_action"):
        health["issues"].append(
            f"Its earlier {life['expired_action']} request expired after "
            f"{lifecycle.REQUEST_TTL_S // 3600} hours without a machine picking it up, so it was not carried out."
        )
    item = {
        "kind": "workspace",
        "id": row["id"],
        "name": row["name"],
        "subtitle": public["image"],
        "status": status,
        "last_seen": None
        if age is None
        else {
            "at": life["reported_at"],
            "age_s": age,
            "source": "report",
            "label": f"Last reported {ago(age)}",
            "prefix": "Last reported",
            "suffix": "",
            "when": ago(age),
        },
        "health": health,
        "usage": {
            "limits": {
                "known": True,
                "text": _limits_text(row) + " (limits)",
                "cpu_cores": public["cpu_cores"],
                "memory_mib": public["memory_mib"],
                "storage_gib": public["storage_gib"],
            },
            "measured": {
                "known": False,
                "reason": "not_measured",
                "text": "Not measured yet: Commonplace does not collect CPU, memory or disk use from cloud workspaces.",
            },
            "summary": _limits_text(row) + ". Use is not measured yet.",
        },
        "placement": {
            "assigned": row["node_id"] is not None,
            "machine_name": public["machine_name"] if manage else None,
            "machine_id": row["node_id"] if manage else None,
        },
        "restart_needed": phase in ("running", "stopped") and workspaces._restart_needed(row),
        "image_source": public["image_source"],
        "network": public["network"],
        "request": None
        if not life["request_action"]
        else {
            "action": life["request_action"],
            "requested_at": life["requested_at"],
            "state": "requested" if life["claimed_at"] is None else "in_progress",
        },
        "basis": {"seq": life["seq"], "version": row["version"]} if manage else None,
        "actions": [],
    }
    if manage:
        item["actions"] = [
            _action(a, lifecycle.decide(row, life, a, now)) for a in WORKSPACE_ACTIONS
        ]
    return item


def _action(action, refusal):
    return {
        "action": action,
        "label": ACTION_LABEL[action],
        "allowed": refusal is None,
        "reason": refusal,
        "refusal": REFUSAL_TEXT.get(refusal) if refusal else None,
        "danger": action in ("stop", "restart", "drain"),
    }


# machines (compute targets)


def _metadata(row):
    """A machine row's metadata as a dict; an unreadable one is an empty one, so a single bad row never
    fails the whole overview (it then reads as not verified, which is what is known about it)."""
    try:
        value = json.loads(row["metadata"])
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def machine_item(row, verification, placed, now):
    metadata = _metadata(row)
    state = metadata.get("state")
    blockers = []
    if state == "verified":
        expires = verification["expires_at"] if verification else None
        if expires is not None and expires <= now:
            status = _status(
                "verification_expired",
                "wait",
                "Verification expired",
                f"Its last verification ended {ago(now - expires)}. Commonplace does not check target machines live.",
            )
            blockers.append(
                {
                    "code": "verification_expired",
                    "text": "Verification expired: cloud workspaces placed here cannot start until it is verified again.",
                }
            )
        else:
            when = (
                f"Verified {ago(now - verification['verified_at'])}. "
                if verification
                else "Verified. "
            )
            status = _status(
                "verified",
                "ok",
                "Verified",
                when
                + "This is a signed enrollment check, not a live check: Commonplace does not probe target machines.",
            )
    else:
        status = _status(
            "not_verified",
            "wait",
            "Not verified",
            "Commonplace has not verified this machine yet. Cloud workspaces can be saved for it, but nothing runs on it.",
        )
        blockers.append(
            {
                "code": "not_verified",
                "text": "Not verified: cloud workspaces placed here cannot start until it is verified.",
            }
        )
    return {
        "kind": "machine",
        "id": row["id"],
        "name": metadata.get("name") or "Machine",
        "subtitle": "Target machine",
        "status": status,
        "last_seen": None,
        "health": {
            "state": "attention" if status["code"] != "verified" else "unknown",
            "blockers": blockers,
            "issues": [],
        },
        "usage": {
            "measured": {
                "known": False,
                "reason": "not_collected",
                "text": "Not collected: Commonplace does not read usage from target machines.",
            },
            "summary": f"{placed['count']} cloud workspace{'' if placed['count'] == 1 else 's'} placed here."
            + " Use is not collected.",
        },
        "placement": {"workspaces": placed["count"], "names": placed["names"]},
        "basis": None,
        "actions": [],
    }


# runners


def _iso_to_epoch(value):
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def _table(conn, name):
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def blank_facts():
    return {
        "running": 0,
        "older_started": 0,
        "uncertain": 0,
        "queued": 0,
        "starts_hour": 0,
        "resources": None,
        "spend": {},
        "unknown_runs": 0,
        "runs": 0,
        "drain": {"draining": False, "seq": 0, "changed_at": None},
    }


def _runner_facts(store, now):
    """Per-machine run counts, resource reports, spend and drain flags from the runner store (``store`` is
    opened once by the caller). Two indexed reads of ``runner_jobs`` serve every count: one by state (index
    ``runner_jobs_machine_state``), one by ``starting_at`` (index ``runner_jobs_starting``)."""
    from commonplace.runner import drain

    since_epoch = now - SPEND_DAYS * 86400
    stamp = lambda epoch: datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    facts = {}

    def of(machine_id):
        return facts.setdefault(machine_id, blank_facts())

    with store.connection() as conn:
        recent_limit = stamp(now - RECENT_STARTED_S)
        for row in conn.execute(
            "SELECT machine_id,state,count(*) AS n,sum(updated_at>?) AS recent FROM runner_jobs "
            "WHERE state IN ('queued','leased','starting','uncertain','started') GROUP BY machine_id,state",
            (recent_limit,),
        ):
            entry = of(row["machine_id"])
            if row["state"] == "queued":
                entry["queued"] += row["n"]
            elif row["state"] == "uncertain":
                entry["uncertain"] += row["n"]
                entry["running"] += row["n"]
            elif row["state"] == "started":
                # Started rows nobody has updated for hours are not counted as in progress, but they are not
                # dropped either: the machine says how many may still be open (``older_started``).
                entry["running"] += row["recent"]
                entry["older_started"] = row["n"] - row["recent"]
            else:
                entry["running"] += row["n"]
        for row in conn.execute(
            "SELECT machine_id,count(*) AS n FROM runner_jobs WHERE starting_at>? GROUP BY machine_id",
            (stamp(now - 3600),),
        ):
            of(row["machine_id"])["starts_hour"] = row["n"]
        if _table(conn, "runner_resources"):
            for row in conn.execute(
                "SELECT machine_id,reported_at,resources FROM runner_resources"
            ):
                of(row["machine_id"])["resources"] = {
                    "reported_at": _iso_to_epoch(row["reported_at"]),
                    "values": json.loads(row["resources"]),
                }
        if _table(conn, "run_ledger"):
            counted = ",".join("?" for _ in _COUNTED)
            if _table(conn, "cost_events"):
                rows = conn.execute(
                    "SELECT l.machine_id AS machine_id,e.currency AS currency,count(*) AS runs,"
                    "sum(e.cost_micros) AS micros FROM run_ledger l LEFT JOIN cost_events e "
                    "ON e.id=(SELECT max(id) FROM cost_events WHERE job_id=l.job_id) "
                    "WHERE l.status IN ("
                    + counted
                    + ") AND (l.started_at>=? OR (l.started_at IS NULL AND l.created_at>=?)) "
                    "GROUP BY l.machine_id,e.currency",
                    (*_COUNTED, stamp(since_epoch), stamp(since_epoch)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT l.machine_id AS machine_id,l.currency AS currency,count(*) AS runs,"
                    "sum(l.cost_micros) AS micros FROM run_ledger l "
                    "WHERE l.status IN ("
                    + counted
                    + ") AND (l.started_at>=? OR (l.started_at IS NULL AND l.created_at>=?)) "
                    "GROUP BY l.machine_id,l.currency",
                    (*_COUNTED, stamp(since_epoch), stamp(since_epoch)),
                ).fetchall()
            for row in rows:
                entry = of(row["machine_id"])
                entry["runs"] += row["runs"]
                if row["currency"] is None or row["micros"] is None:
                    entry["unknown_runs"] += row["runs"]
                else:
                    entry["spend"][row["currency"]] = row["micros"]
        if _table(conn, "runner_drain"):
            for row in conn.execute("SELECT machine_id FROM runner_drain"):
                of(row["machine_id"])["drain"] = drain.state(conn, row["machine_id"])
    return facts


def store_runner(store):
    """The workspace's runner store. Opening one re-runs its schema, so a read opens it once."""
    from commonplace.runner import jobs

    return jobs.open_store(store.gateway, store.workspace_id)


def _recent_runs(conn, machine_id):
    return [
        dict(row)
        for row in conn.execute(
            "SELECT id,state,project,topic,board_slug,task_number,outcome_code,updated_at "
            "FROM runner_jobs WHERE machine_id=? ORDER BY updated_at DESC,id DESC LIMIT ?",
            (machine_id, ACTIVITY_LIMIT),
        )
    ]


def runner_status(machine, record, facts, now):
    """The one status of a runner machine: ``record`` is ``topology.status.machine_status``."""
    # The age of the last signal is the health line's job (``last_seen``); a sentence here says what it means.
    running = facts["running"]
    drain = facts["drain"]
    note = " It is also set to drain: no new card runs start on it." if drain["draining"] else ""
    reason = record["reason"]
    if reason == "revoked":
        return _status(
            "revoked",
            "bad",
            "Revoked",
            "The owner removed this runner and its access is cancelled.",
        )
    if reason == "disabled":
        return _status(
            "disabled",
            "wait",
            "Disabled",
            "The owner turned this runner off. It starts no runs.",
        )
    if reason == "never_reported":
        return _status(
            "never_connected",
            "quiet",
            "Never connected",
            "This machine has not contacted Commonplace yet. Finish its setup and it will report here.",
        )
    if reason == "no_heartbeat":
        return _status(
            "offline",
            "bad",
            "Offline",
            "Nothing has been heard from it for a long time. Runs it already started may still be going; "
            f"new card runs wait until it is back.{note}",
        )
    if reason == "late_heartbeat":
        return _status(
            "slow",
            "wait",
            "Slow to report",
            f"Later than usual. It may be busy or have a poor connection.{note}",
        )
    if drain["draining"]:
        if running:
            return _status(
                "draining",
                "wait",
                "Draining",
                f"{DRAIN_SCOPE} {_runs(running)} in progress will finish. {NO_RESUME}",
            )
        older = facts["older_started"]
        return _status(
            "drained",
            "wait",
            "Drained",
            f"{DRAIN_SCOPE} "
            + (
                f"None is known to be in progress; {_runs(older)} recorded as started long ago may still be open."
                if older
                else "None is in progress."
            )
            + f" {NO_RESUME}",
        )
    if running:
        return _status(
            "busy",
            "ok",
            "Online · Busy",
            f"{running} of {machine['max_in_flight']} runs at once {'is' if running == 1 else 'are'} in progress.",
        )
    return _status("online", "ok", "Online", "Waiting for work.")


def runner_blockers(machine, record, facts, red_after_s=900):
    blockers = []
    reason = record["reason"]
    drain = facts["drain"]
    if reason == "revoked":
        blockers.append(
            {"code": "revoked", "text": "This runner was removed. It can't be used again."}
        )
        return blockers
    if reason == "disabled":
        blockers.append(
            {
                "code": "disabled",
                "text": "It is turned off. Turn it on again in Admin, Runner machines.",
            }
        )
    if machine["binding_state"] != "owner_attested":
        blockers.append(
            {
                "code": "not_approved",
                "text": "It is not approved for work yet. Approve its setup in Admin, Runner machines to let it start runs.",
            }
        )
    if drain["draining"]:
        blockers.append(
            {
                "code": "draining",
                "text": f"It is draining: no new card runs start on it. {NO_RESUME}",
            }
        )
    if reason == "never_reported":
        blockers.append(
            {"code": "never_connected", "text": "It has not connected yet, so runs would wait."}
        )
    elif reason == "no_heartbeat":
        blockers.append(
            {
                "code": "offline",
                "text": f"Commonplace has not heard from it for over {span(red_after_s)}, so new card runs would wait.",
            }
        )
    if facts["running"] >= machine["max_in_flight"]:
        blockers.append(
            {
                "code": "full",
                "text": f"It may be full: {_runs(facts['running'])} recorded in progress, and it runs up to "
                f"{machine['max_in_flight']} at once. New runs wait for one to finish.",
            }
        )
    if facts["starts_hour"] >= machine["max_starts_per_hour"]:
        blockers.append(
            {
                "code": "hourly_cap",
                "text": f"It reached its limit of {machine['max_starts_per_hour']} starts per hour. New runs wait.",
            }
        )
    return blockers


def _runner_usage(machine, facts, now, *, silent=False):
    """``silent``: the machine is offline or slow, so a count of runs in progress is only what was recorded."""
    running = facts["running"]
    in_progress = (
        f"{_runs(running)} recorded in progress; more may be running while it is offline"
        if silent
        else f"{_runs(running)} in progress"
    )
    usage = {
        "runs": {
            "known": True,
            "running": running,
            "max": machine["max_in_flight"],
            "queued": facts["queued"],
            "starts_last_hour": facts["starts_hour"],
            "max_starts_per_hour": machine["max_starts_per_hour"],
            "older_started": facts["older_started"],
            "text": f"{in_progress} (up to {machine['max_in_flight']} at once), "
            f"{facts['queued']} waiting, {facts['starts_hour']} of {machine['max_starts_per_hour']} "
            "starts used this hour"
            + (
                f", plus {_runs(facts['older_started'])} recorded as started long ago that may still be open"
                if facts["older_started"]
                else ""
            ),
        }
    }
    resources = facts["resources"]
    if resources is None:
        usage["resources"] = {
            "known": False,
            "reason": "never_reported",
            "text": "Not reported: this machine has not sent a resource report.",
        }
    else:
        age = max(0, int(now - resources["reported_at"]))
        values = resources["values"]
        parts = []
        if values.get("memory_available_mb") is not None:
            parts.append(f"{_gib(values['memory_available_mb'])} memory free")
        if values.get("cpu_free") is not None:
            parts.append(f"{values['cpu_free']:.1f} CPU cores free")
        stale = age > RESOURCE_STALE_S
        body = ", ".join(parts) if parts else "no values in the report"
        usage["resources"] = {
            "known": bool(parts),
            "reason": None if parts else "unknown_values",
            "memory_available_mb": values.get("memory_available_mb"),
            "cpu_free": values.get("cpu_free"),
            "reported_at": resources["reported_at"],
            "age_s": age,
            "stale": stale,
            "text": f"{body}, last reported {ago(age)}"
            + (" (stale, treat as unknown)" if stale else ""),
        }
        if stale:
            usage["resources"]["known"] = False
            usage["resources"]["reason"] = "stale"
    totals = [
        {"currency": currency, "cost_micros": micros, "text": _money(micros, currency)}
        for currency, micros in sorted(facts["spend"].items())
    ]
    if not facts["runs"]:
        usage["spend"] = {
            "known": True,
            "window_days": SPEND_DAYS,
            "totals": [],
            "unknown_runs": 0,
            "runs": 0,
            "text": f"No runs in the last {SPEND_DAYS} days.",
        }
    else:
        unknown = facts["unknown_runs"]
        known = ", ".join(t["text"] for t in totals)
        text = f"Last {SPEND_DAYS} days: " + (known if totals else "no cost reported")
        if unknown:
            text += f", plus {_runs(unknown)} with unknown cost"
        usage["spend"] = {
            "known": bool(totals) and not unknown,
            "reason": None if totals and not unknown else "unknown_cost",
            "window_days": SPEND_DAYS,
            "totals": totals,
            "unknown_runs": unknown,
            "runs": facts["runs"],
            "text": text,
        }
    # One short line for the list row; the detail view has the long texts above.
    memory = usage["resources"].get("memory_available_mb")
    if usage["resources"].get("stale"):
        resources = f"Resources last reported {ago(usage['resources']['age_s'])} (stale)"
    elif not usage["resources"]["known"]:
        resources = "Resources not reported"
    else:
        resources = f"{_gib(memory)} memory free" if memory is not None else "CPU free reported"
    spend = usage["spend"]
    if not spend["runs"]:
        spend_line = f"No runs in {SPEND_DAYS} days"
    else:
        spend_line = f"{SPEND_DAYS}-day spend " + (
            " + ".join(t["text"] for t in totals) if totals else "unknown"
        )
        if spend["unknown_runs"]:
            spend_line += f" (and {_runs(spend['unknown_runs'])} of unknown cost)"
    usage["summary"] = (
        f"{in_progress} (up to {machine['max_in_flight']} at once) · {resources} · {spend_line}"
    )
    return usage


def runner_item(machine, record, facts, now, red_after_s=900):
    status = runner_status(machine, record, facts, now)
    blockers = runner_blockers(machine, record, facts, red_after_s)
    issues = []
    if facts["uncertain"]:
        issues.append(
            f"{_runs(facts['uncertain'])} uncertain: Commonplace could not confirm whether they started. "
            "They stay uncertain until the owner resolves them on the card; this screen cannot."
        )
    last = None
    if record["last_signal_at"]:
        # A removed or turned-off runner's last contact is from before that, so it is not "seen" now.
        prefix, suffix = {
            "revoked": ("Last contact", ", before it was removed"),
            "disabled": ("Last contact", ", before it was turned off"),
        }.get(status["code"], ("Last seen", ""))
        last = {
            "at": record["last_signal_at"],
            "age_s": record["age_s"],
            "source": record["last_signal_source"],
            "label": f"{prefix} {ago(record['age_s'])}{suffix}",
            "prefix": prefix,
            "suffix": suffix,
            "when": ago(record["age_s"]),
        }
    attention = status["code"] in ("offline", "slow", "revoked", "disabled") or bool(issues)
    item = {
        "kind": "runner",
        "id": machine["id"],
        "name": machine["label"],
        "subtitle": "Runner machine",
        "status": status,
        "last_seen": last,
        "health": {
            "state": "unknown"
            if status["code"] == "never_connected"
            else "attention"
            if attention
            else "ok",
            "blockers": blockers,
            "issues": issues,
            "heartbeat": {"status": record["status"], "reason": record["reason"]},
        },
        "usage": _runner_usage(machine, facts, now, silent=status["code"] == "offline"),
        "placement": {"approved": machine["binding_state"] == "owner_attested"},
        "draining": facts["drain"]["draining"],
        "basis": {"seq": facts["drain"]["seq"]},
        "actions": [],
    }
    item["actions"] = [_action(a, _runner_refusal(machine, facts, a)) for a in RUNNER_ACTIONS]
    return item


def _runner_refusal(machine, facts, action):
    if machine["revoked_at"]:
        return "revoked"
    draining = facts["drain"]["draining"]
    if action == "drain":
        return "already_draining" if draining else None
    return None if draining else "not_draining"


# the read


def _dt(now):
    return datetime.fromtimestamp(now, timezone.utc)


def _marks(values):
    """``?`` placeholders for a bounded id list (the values still go in as parameters)."""
    return ",".join("?" for _ in values)


def _read_compute(store, principal, session_hash, *, workspace_id=None, node_id=None):
    """Compute rows under one transaction, bounded by what is shown: the newest ``MAX_ITEMS`` of each kind, or
    the one record asked for (found by id, so an item beyond the cap is still reachable)."""
    with store._transaction(principal, session_hash, member=True) as (conn, current):
        manage = current.role == "owner"
        now = store._now()
        data = {"manage": manage, "now": now}
        data["workspace_rows"] = workspaces._rows(
            conn, tail="ORDER BY w.created_at DESC,w.id DESC LIMIT ?", params=(MAX_ITEMS,)
        )
        if workspace_id is not None:
            data["workspace_row"] = workspaces._row(conn, workspace_id)
        shown = [row["id"] for row in data["workspace_rows"]] + (
            [workspace_id] if workspace_id else []
        )
        data["lifecycles"] = {
            row["workspace_id"]: dict(row)
            for row in conn.execute(
                "SELECT * FROM cloud_workspace_lifecycle WHERE workspace_id IN ("
                + _marks(shown)
                + ")",
                shown,
            )
        }
        data["workspace_total"] = conn.execute("SELECT count(*) FROM cloud_workspaces").fetchone()[
            0
        ]
        if manage:
            data["node_rows"] = conn.execute(
                "SELECT * FROM nodes ORDER BY created_at DESC,id DESC LIMIT ?", (MAX_ITEMS,)
            ).fetchall()
            if node_id is not None:
                data["node_row"] = conn.execute(
                    "SELECT * FROM nodes WHERE id=?", (node_id,)
                ).fetchone()
            data["node_total"] = conn.execute("SELECT count(*) FROM nodes").fetchone()[0]
            ids = [row["id"] for row in data["node_rows"]] + ([node_id] if node_id else [])
            data["verifications"] = {
                row["node_id"]: dict(row)
                for row in conn.execute(
                    "SELECT node_id,verified_at,expires_at FROM enrollment_verifications "
                    "WHERE node_id IN (" + _marks(ids) + ")",
                    ids,
                )
            }
            data["placed"] = {
                row["node_id"]: {"count": row["n"], "names": []}
                for row in conn.execute(
                    "SELECT node_id,count(*) AS n FROM cloud_workspaces WHERE node_id IN ("
                    + _marks(ids)
                    + ") GROUP BY node_id",
                    ids,
                )
            }
            for row in conn.execute(
                "SELECT node_id,name FROM cloud_workspaces WHERE node_id IN ("
                + _marks(ids)
                + ") ORDER BY name LIMIT ?",
                [*ids, MAX_ITEMS * 5],
            ):
                names = data["placed"][row["node_id"]]["names"]
                if len(names) < 5:
                    names.append(row["name"])
        audit_id = workspace_id if workspace_id is not None else node_id if manage else None
        if audit_id is not None:
            data["audit"] = [
                dict(row)
                for row in conn.execute(
                    "SELECT action,user_id,created_at FROM audit WHERE object_id=? ORDER BY id DESC LIMIT ?",
                    (audit_id, ACTIVITY_LIMIT),
                )
            ]
        return data


def _registry_machines(db, workspace_id):
    """The workspace's runner machines; a hub without the runner installed has none (reading never creates it)."""
    from commonplace.runner import registry

    try:
        return registry.machines(db, workspace_id)
    except sqlite3.OperationalError:
        return []


def _runner_items(store, now, only=None):
    """``(items, total)`` for the workspace's runner machines, owner only: the first ``MAX_ITEMS``, or just
    the machine ``only`` names (found among all of them, so one beyond the cap is still reachable)."""
    from commonplace.topology import status as topo

    gateway, ws = store.gateway, store.workspace_id
    machines = _registry_machines(gateway.db, ws)
    total = len(machines)
    machines = [m for m in machines if m["id"] == only] if only else machines[:MAX_ITEMS]
    if not machines:
        return [], total
    when = _dt(now)
    config = topo.StatusConfig.from_env()
    runs = store_runner(store)
    signals = topo.collect_signals(gateway, ws, now=when, runs=runs)
    facts = _runner_facts(runs, now)
    items = []
    for machine in machines:
        sig = signals.get(machine["id"]) or topo.MachineSignals(
            machine_id=machine["id"],
            enabled=bool(machine["enabled"]),
            revoked=bool(machine["revoked_at"]),
        )
        record = topo.machine_status(sig, now=when, config=config)
        items.append(
            runner_item(
                machine,
                record,
                facts.get(machine["id"]) or blank_facts(),
                now,
                config.red_after_s,
            )
        )
    return items, total


def _summary(items):
    by_status, attention = {}, 0
    for item in items:
        code = item["status"]["code"]
        by_status[code] = by_status.get(code, 0) + 1
        if item["health"]["state"] == "attention":
            attention += 1
    by_kind = {kind: sum(1 for i in items if i["kind"] == kind) for kind in KINDS}
    return {"total": len(items), "by_kind": by_kind, "by_status": by_status, "attention": attention}


def thresholds():
    from commonplace.topology import status as topo

    return topo.StatusConfig.from_env().as_dict() | {"report_stale_s": lifecycle.REPORT_STALE_S}


def read(store, principal, *, session_hash):
    """Everything the screen needs in one read. Members get cloud workspaces only."""
    data = _read_compute(store, principal, session_hash)
    manage, now = data["manage"], data["now"]
    executor = lifecycle.executor_available(store.gateway)
    items, truncated = [], {}
    for row in data["workspace_rows"]:
        life = data["lifecycles"].get(row["id"]) or lifecycle.default(now)
        items.append(
            workspace_item(
                row,
                life,
                now,
                manage=manage,
                executor=executor,
                has_machines=data["node_total"] > 0 if manage else None,
            )
        )
    if data["workspace_total"] > len(data["workspace_rows"]):
        truncated["workspace"] = data["workspace_total"]
    if manage:
        for row in data["node_rows"]:
            placed = data["placed"].get(row["id"], {"count": 0, "names": []})
            items.append(machine_item(row, data["verifications"].get(row["id"]), placed, now))
        if data["node_total"] > len(data["node_rows"]):
            truncated["machine"] = data["node_total"]
        runners, total = _runner_items(store, now)
        items.extend(runners)
        if total > len(runners):
            truncated["runner"] = total
    # By kind, then what needs a person first, then by name, so a problem is at the top of its group.
    order = {"runner": 0, "machine": 1, "workspace": 2}
    items.sort(
        key=lambda item: (
            order[item["kind"]],
            item["health"]["state"] != "attention",
            item["name"].casefold(),
        )
    )
    return {
        "as_of": _dt(now).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "refresh_seconds": REFRESH_SECONDS,
        "thresholds": thresholds(),
        "can_manage": manage,
        "executor": {
            "available": executor,
            "text": None
            if executor
            else "Starting and stopping cloud workspaces on a machine is not available in this version. "
            "Requests are saved and wait.",
        },
        "counts": _summary(items),
        "items": items,
        "hidden": None
        if manage
        else {
            "kinds": ["machine", "runner"],
            "text": "Machines and runners are visible to the workspace owner.",
        },
        "truncated": truncated,
    }


def _ident(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise workspaces.refuse(None, "identifier")
    return value


_ACTIVITY_TEXT = {
    "workspace.created": "Created",
    "workspace.updated": "Settings changed",
    "workspace.start_requested": "Start requested",
    "workspace.stop_requested": "Stop requested",
    "workspace.restart_requested": "Restart requested",
    "workspace.request_withdrawn": "Request withdrawn",
    "workspace.request_claimed": "A machine picked the request up",
    "workspace.reported_starting": "The machine reported: starting",
    "workspace.reported_running": "The machine reported: running",
    "workspace.reported_stopping": "The machine reported: stopping",
    "workspace.reported_stopped": "The machine reported: stopped",
    "workspace.reported_failed": "The machine reported: failed",
    "node.created": "Added",
    "enrollment.verified": "Verified",
    "node.credential_bound": "Credential connected",
}


def _activity_from_audit(rows, now, *, manage):
    result = []
    for row in rows:
        text = _ACTIVITY_TEXT.get(row["action"], "Changed")
        who = "by the owner" if manage and row["user_id"] not in ("system",) else ""
        result.append(
            {
                "at": row["created_at"],
                "age_s": max(0, int(now - row["created_at"])),
                "text": f"{text} {who}".strip(),
            }
        )
    return result


def _runner_activity(store, machine_id, now):
    """Recent runs and drain changes, newest first."""
    from commonplace.runner import drain

    with store_runner(store).connection() as conn:
        entries = _runs_activity(_recent_runs(conn, machine_id), now)
        for row in drain.log(conn, machine_id, ACTIVITY_LIMIT):
            at = _iso_to_epoch(row["at"])
            entries.append(
                {
                    "at": at,
                    "age_s": None if at is None else max(0, int(now - at)),
                    "text": "Drain started: no new card runs start here"
                    if row["action"] == "drain"
                    else "Drain ended: new card runs can start again",
                }
            )
    entries.sort(key=lambda entry: entry["at"] or 0, reverse=True)
    return entries[:ACTIVITY_LIMIT]


def _runs_activity(runs, now):
    result = []
    for run in runs:
        at = _iso_to_epoch(run["updated_at"])
        label = {
            "queued": "Waiting",
            "leased": "Handed to the machine",
            "starting": "Starting",
            "started": "Started",
            "failed": "Failed",
            "uncertain": "Uncertain",
            "cancelled": "Cancelled",
        }.get(run["state"], run["state"])
        result.append(
            {
                "at": at,
                "age_s": None if at is None else max(0, int(now - at)),
                "text": f"{label}: {run['project']} card {run['task_number']}"
                + (f" ({run['outcome_code']})" if run["outcome_code"] else ""),
            }
        )
    return result


def detail(store, principal, kind, item_id, *, session_hash):
    """One item with its recent activity and facts. A member may read a cloud workspace only."""
    if kind not in ("workspace", "runner", "machine"):
        raise not_found("route")
    _ident(item_id)
    if kind == "workspace":
        data = _read_compute(store, principal, session_hash, workspace_id=item_id)
    elif kind == "machine":
        data = _read_compute(store, principal, session_hash, node_id=item_id)
    else:
        data = _read_compute(store, principal, session_hash)
    manage, now = data["manage"], data["now"]
    if kind != "workspace" and not manage:
        raise StoreError("owner_required", 403, reason="member")
    executor = lifecycle.executor_available(store.gateway)
    if kind == "workspace":
        row = data["workspace_row"]
        life = data["lifecycles"].get(item_id) or lifecycle.default(now)
        item = workspace_item(
            row,
            life,
            now,
            manage=manage,
            executor=executor,
            has_machines=data["node_total"] > 0 if manage else None,
        )
        item["activity"] = _activity_from_audit(data["audit"], now, manage=manage)
        item["facts"] = _workspace_facts(row, life, now, manage)
        return {"item": item, "can_manage": manage}
    if kind == "machine":
        row = data["node_row"]
        if row is None:
            raise not_found()
        placed = data["placed"].get(item_id, {"count": 0, "names": []})
        item = machine_item(row, data["verifications"].get(item_id), placed, now)
        item["activity"] = _activity_from_audit(data["audit"], now, manage=True)
        item["facts"] = [
            {
                "label": "Cloud workspaces placed here",
                "value": ", ".join(placed["names"]) or "None",
            },
            {
                "label": "Live status",
                "value": "Not available: Commonplace does not check target machines.",
            },
        ]
        return {"item": item, "can_manage": True}
    items, _ = _runner_items(store, now, only=item_id)
    item = items[0] if items else None
    if item is None:
        raise not_found()
    item["activity"] = _runner_activity(store, item_id, now)
    machine = _machine_row(store, item_id)
    item["facts"] = [
        {"label": "Runs at once", "value": f"Up to {machine['max_in_flight']}"},
        {"label": "Starts per hour", "value": f"Up to {machine['max_starts_per_hour']}"},
        {
            "label": "Approved for work",
            "value": "Yes" if machine["binding_state"] == "owner_attested" else "No",
        },
        {
            "label": "Container logs",
            "value": "Not collected: the activity list is the run history.",
        },
    ]
    return {"item": item, "can_manage": True}


def require(store, principal, kind, item_id, *, session_hash):
    """Owner check plus concealment: the record must exist in this workspace, before any body is read."""
    _ident(item_id)
    with store._transaction(principal, session_hash) as (
        conn,
        _,
    ):  # owner only, here as well as in the route
        if kind == "workspace":
            workspaces._row(conn, item_id)
    if kind != "workspace":
        _machine_row(store, item_id)


def _one_workspace(store, principal, session_hash, workspace_id):
    with store._transaction(principal, session_hash, member=True) as (conn, _):
        return workspaces._row(conn, workspace_id)


def _machine_row(store, machine_id):
    row = next(
        (
            m
            for m in _registry_machines(store.gateway.db, store.workspace_id)
            if m["id"] == machine_id
        ),
        None,
    )
    if row is None:
        raise not_found()
    return row


def _workspace_facts(row, life, now, manage):
    public = workspaces.public(row, manage=manage)
    facts = [
        {"label": "Image", "value": public["image"]},
        {"label": "Limits", "value": _limits_text(row)},
        {
            "label": "Network",
            "value": "No network" if public["network"] == "none" else "Internet access allowed",
        },
        {
            "label": "Machine",
            "value": (public["machine_name"] or "Chosen")
            if manage and public["machine_assigned"]
            else ("A machine is assigned" if public["machine_assigned"] else "None chosen yet"),
        },
        {
            "label": "Container logs",
            "value": "Not collected: the activity list is the audit trail.",
        },
    ]
    if life["reported_at"] is not None:
        facts.append(
            {"label": "Last report from the machine", "value": ago(lifecycle.report_age(life, now))}
        )
    return facts


# preview and action


def preview(store, principal, kind, item_id, action, *, session_hash):
    """What an action would do, before the owner confirms. Read-only; owner only."""
    if kind not in ("workspace", "runner") or action not in (
        WORKSPACE_ACTIONS if kind == "workspace" else RUNNER_ACTIONS
    ):
        raise not_found("route")
    _ident(item_id)
    data = _read_compute(
        store, principal, session_hash, workspace_id=item_id if kind == "workspace" else None
    )
    if not data["manage"]:
        raise StoreError("owner_required", 403, reason="member")
    now = data["now"]
    executor = lifecycle.executor_available(store.gateway)
    if kind == "workspace":
        row = data["workspace_row"]
        life = data["lifecycles"].get(item_id) or lifecycle.default(now)
        item = workspace_item(
            row, life, now, manage=True, executor=executor, has_machines=data["node_total"] > 0
        )
        refusal = lifecycle.decide(row, life, action, now)
        return _workspace_preview(item, row, life, action, refusal, executor)
    items, _ = _runner_items(store, now, only=item_id)
    item = items[0] if items else None
    if item is None:
        raise not_found()
    machine = _machine_row(store, item_id)
    runs = item["usage"]["runs"]
    facts = {"running": runs["running"], "queued": runs["queued"]}
    refusal = _runner_refusal(machine, {"drain": {"draining": item["draining"]}}, action)
    return _runner_preview(item, facts, action, refusal)


def _workspace_preview(item, row, life, action, refusal, executor):
    name = row["name"]
    machine = item["placement"]["machine_name"] or "its machine"
    wait = (
        "A machine carries it out the next time it asks for work."
        if executor
        else "Starting and stopping cloud workspaces on a machine is not available in this version, so the "
        f"request is saved and waits. Nothing is started, stopped or created. It expires after "
        f"{lifecycle.REQUEST_TTL_S // 3600} hours if no machine picks it up."
    )
    base = {
        "kind": "workspace",
        "id": row["id"],
        "action": action,
        "allowed": refusal is None,
        "refusal": None
        if refusal is None
        else {"reason": refusal, "text": REFUSAL_TEXT.get(refusal, "")},
        "basis": item["basis"],
        "executor": {"available": executor},
        "danger": action in ("stop", "restart"),
        "will_not": [],
        "effect": [],
        "running_work": "",
        "confirm_label": ACTION_LABEL[action],
    }
    stop_work = (
        f"Work running inside {name} is cancelled, not finished: the container is asked to stop, given "
        f"{lifecycle.STOP_GRACE_S} seconds, then ended. Files saved in its storage are kept; anything "
        "not saved there is lost. Commonplace does not track which work runs inside a cloud workspace, "
        "so it can't list it here."
    )
    if action == "start":
        base.update(
            title=f"Start {name}?",
            effect=[
                (
                    f"Asks {machine} to create"
                    if executor
                    else f"When {machine} can carry it out, it will create"
                )
                + f" the workspace from its image with {_limits_text(row)}.",
                "Saves the request in Commonplace. " + wait,
            ],
            running_work="Nothing is running in it now, so no work is affected.",
            will_not=[
                "It does not change the saved settings.",
                "It does not touch any other workspace.",
            ],
        )
    elif action == "stop":
        base.update(
            title=f"Stop {name}?",
            effect=[
                (
                    "Asks " + machine + " to stop"
                    if executor
                    else f"When {machine} can carry it out, it will stop"
                )
                + " the workspace.",
                "Saves the request in Commonplace. " + wait,
            ],
            running_work=stop_work,
            will_not=["It does not delete the workspace, its settings or its saved files."],
        )
    elif action == "restart":
        base.update(
            title=f"Restart {name}?",
            effect=[
                "Stops the workspace and starts it again with the saved settings"
                + (
                    " (including the changes made since it last started)."
                    if item["restart_needed"]
                    else "."
                ),
                "Saves the request in Commonplace. " + wait,
            ],
            running_work=stop_work,
            will_not=["It does not delete the workspace or its saved files."],
        )
    else:
        base.update(
            title="Withdraw the request?",
            effect=[
                "Clears the request, which a machine had picked up and then stopped reporting on. "
                "This does not undo what the machine did: the workspace may still be starting or running."
                if life["claimed_at"] is not None
                else "Cancels the request that is waiting. Nothing had been sent to a machine."
            ],
            running_work="Work that may already be running is not touched."
            if life["claimed_at"] is not None
            else "No work is affected.",
            will_not=["It does not stop or start anything."],
            confirm_label="Withdraw request",
            danger=False,
        )
    return base


def _runner_preview(item, facts, action, refusal):
    name = item["name"]
    running = facts.get("running", 0)
    queued = facts.get("queued", 0)
    base = {
        "kind": "runner",
        "id": item["id"],
        "action": action,
        "allowed": refusal is None,
        "refusal": None
        if refusal is None
        else {"reason": refusal, "text": REFUSAL_TEXT.get(refusal, "")},
        "basis": item["basis"],
        "executor": None,
        "danger": action == "drain",
        "confirm_label": ACTION_LABEL[action],
    }
    if action == "drain":
        base.update(
            title=f"Drain {name}?",
            effect=[
                f"From now on no new card runs start on {name}. Queued runs ({queued}) wait in the queue until "
                "it is resumed.",
                "Takes effect at once: Commonplace itself stops handing it card runs.",
            ],
            running_work=f"{_runs(running)} in progress will finish normally; none is cancelled.",
            will_not=[
                "Chat and Coworker sessions can still start here: drain stops card runs only.",
                "The runner program keeps running: this does not shut the machine down.",
            ],
        )
    else:
        base.update(
            title=f"Resume {name}?",
            effect=[f"New card runs can start on {name} again, within its limits."],
            running_work=f"{_runs(running)} in progress are not affected.",
            will_not=["It does not start anything by itself."],
        )
    return base


def act(
    store,
    principal,
    kind,
    item_id,
    action,
    *,
    session_hash,
    expected_seq,
    expected_version=None,
    request_id=None,
):
    """Run an owner action; returns ``(answer, repeated)``. The answer is the item as it is afterwards."""
    if kind not in ("workspace", "runner") or action not in (
        WORKSPACE_ACTIONS if kind == "workspace" else RUNNER_ACTIONS
    ):
        raise not_found("route")
    _ident(item_id)
    if kind == "workspace":
        result, repeated = lifecycle.request(
            store,
            principal,
            item_id,
            action,
            session_hash=session_hash,
            expected_seq=expected_seq,
            expected_version=expected_version,
            request_id=request_id,
        )
        return {
            "request": result["request"],
            "item": detail(store, principal, "workspace", item_id, session_hash=session_hash)[
                "item"
            ],
        }, repeated
    return _runner_act(store, principal, item_id, action, session_hash, expected_seq, request_id)


def _runner_act(store, principal, machine_id, action, session_hash, expected_seq, request_id):
    from commonplace.runner import drain

    if type(expected_seq) is not int or expected_seq < 0:
        raise workspaces.refuse("expected_seq", "invalid")
    if request_id is not None and (type(request_id) is not str or not _ID.fullmatch(request_id)):
        raise workspaces.refuse("request_id", "invalid")
    with store._transaction(principal, session_hash) as (_, current):
        pass  # owner check, before any runner read
    machine = _machine_row(store, machine_id)
    if machine["revoked_at"]:
        raise conflict("revoked")
    now = store._now()
    stamp = _dt(now).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        # The flag, the request id, the drain log line and the hub-wide audit line stand or fall together:
        # the hub audit goes in before the runner transaction commits, so a drain nobody audited cannot
        # exist. The control lock is taken first, as everywhere else (control before any workspace writer).
        with store.gateway.db._lock, store_runner(store).transaction() as conn:
            state, repeated = drain.change(
                conn,
                machine_id,
                draining=action == "drain",
                expected_seq=expected_seq,
                by=current.user_id,
                now=stamp,
                request_id=request_id,
            )
            if not repeated:
                store.gateway.db.audit(
                    action="runner.drain_started" if action == "drain" else "runner.drain_ended",
                    decision="ok",
                    workspace_id=store.workspace_id,
                    user_id=current.user_id,
                    connection_id=current.connection_id,
                    auth=current.auth,
                    target_ids=(machine_id,),
                )
    except drain.Refused as exc:
        raise conflict(
            exc.reason, "request_id" if exc.reason == "request_reused" else None
        ) from None
    except Exception:
        # The change was rolled back with its audit line; say so rather than leave a drain nobody recorded.
        logging.getLogger(__name__).exception("runner drain change failed and was rolled back")
        raise StoreError("authority_unavailable", 503, reason="audit") from None
    return {
        "request": None,
        "item": detail(store, principal, "runner", machine_id, session_hash=session_hash)["item"],
    }, repeated
