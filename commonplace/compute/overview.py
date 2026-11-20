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

from . import pairing, workspace_lifecycle as lifecycle, workspaces
from .control import StoreError

KINDS = ("machine", "runner", "workspace")
REFRESH_SECONDS = 30
MAX_ITEMS = 100
ACTIVITY_LIMIT = 10
SPEND_DAYS = 30
RESOURCE_STALE_S = 600  # a runner resource report older than this is shown as stale
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
# What a member reads for the same failures: the fix is the owner's, so the words say who.
MEMBER_FAILURE_TEXT = {
    "out_of_memory": "It ran out of memory. Ask the workspace owner to raise the memory limit.",
    "storage_full": "Its storage is full. Ask the workspace owner to raise the storage limit.",
    "image_pull_failed": "The machine could not download the image. Ask the workspace owner to check the image address.",
    "image_not_found": "No such image was found. Ask the workspace owner to check the image name and version.",
    "registry_login_failed": "The registry refused the login. Ask the workspace owner to choose a public image.",
    "machine_too_small": "The machine is smaller than these limits. Ask the workspace owner to lower them or choose a bigger machine.",
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
    "not_started": "It has not been started yet, and starting is not available in this version.",
    "not_running": "Only a running workspace can be restarted, and starting and restarting are not available in this version.",
    "state_unknown": "Its state is not known right now (no recent report from its machine), so it can't be restarted. This clears when the machine reports again.",
    "nothing_to_withdraw": "No request is waiting.",
    "already_picked_up": "A machine picked this request up and is reporting on it, so it can't be withdrawn yet. If the machine goes quiet for ten minutes it can be.",
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
NO_RESUME = "Resuming is not available in this version."
# The cloud workspace vocabulary of the workspaces list (compute-workspaces.js: NOT_STARTED_NOTE, NO_MACHINES_SENTENCE and
# the "Saved, not started" label), so the overview and the list never describe one record in two ways. A test compares
# these strings with the script.
SAVED_LABEL = "Saved, not started"
NOT_STARTED_NOTE = "Starting is not available yet, so this only keeps your settings."
NO_MACHINES_SENTENCE = (
    "No machine is set up yet. Starting will need one, which is not available yet."
)
# The one next step per status, for a status a person has to act on or wait out. Pointers name the place.
NEXT_STEP = {
    "offline": "Check that the machine is on and the runner program is running there.",
    "slow": "Wait a little: a busy machine or a poor connection can delay a report. If it stays like this, check the machine's network.",
    "never_connected": "Finish its setup on the machine (Admin, Runner machines lists what to run there).",
    "unknown": "Nothing to do yet; this clears when the machine reports again.",
    # Not verified and verification expired have no step here: no control verifies a machine in this version (enrollment
    # is checked by a signed receipt, never set by hand), and the status sentence says so instead of pointing at one.
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


# The steps a member can take differ from the owner's: they ask the owner.
NEXT_STEP_MEMBER = {"unknown": "Nothing to do yet; this clears when the machine reports again."}


def _status(code, tone, label, detail, *, manage=True, next_text=None):
    return {
        "code": code,
        "tone": tone,
        "label": label,
        "detail": detail,
        "next": next_text or (NEXT_STEP if manage else NEXT_STEP_MEMBER).get(code),
    }


def _runs(count):
    return f"{count} run{'' if count == 1 else 's'}"


def _holding(count):
    """A finished run whose card is still Doing still counts toward the machine's runs at once (the lease counts it)
    until the card moves out of Doing."""
    one = count == 1
    return (
        f"{count} finished run{'' if one else 's'} still count{'s' if one else ''} until "
        f"{'its' if one else 'their'} card{'' if one else 's'} move{'s' if one else ''} out of Doing."
    )


def _in_use(running, held, cap):
    """The one count of a machine's runs at once, the same words in the sentence and the blocker."""
    used = running + held
    parts = [f"{running} in progress"] if running else []
    if held:
        parts.append(_holding(held).rstrip("."))
    return (
        f"{used} of {cap} runs at once {'is' if used == 1 else 'are'} in use: {'; '.join(parts)}."
    )


def _allowed_to_start(machine, item):
    """ "Allowed to start runs" from the status the chip shows, so the two can never disagree."""
    code = item["status"]["code"]
    if code == "revoked":
        return "No: it was removed"
    if code == "disabled":
        return "No: it is turned off"
    if machine["binding_state"] != "owner_attested":
        return "No: it is not set up and approved yet"
    if item["draining"]:
        return "No: it is " + ("drained" if code == "drained" else "draining")
    if code in ("offline", "never_connected"):
        return "Yes, once it reports"
    if code == "full":
        return "Yes, once a run finishes"
    return "Yes"


def _holding_step(cards):
    """What to do about finished runs whose cards are still Doing, naming the cards."""
    one = len(cards) == 1
    names = ", ".join(cards[:3]) + (f" and {len(cards) - 3} more" if len(cards) > 3 else "")
    return (
        f"{names} {'has' if one else 'have'} finished but {'is' if one else 'are'} still in Doing. Move "
        f"{'it' if one else 'them'} out of Doing (to Done, Review or Todo) to free {'its' if one else 'their'} "
        "place."
    )


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


effective_phase = (
    lifecycle.effective_phase
)  # one reading of the phase, shared with ``lifecycle.decide``


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
                f" It expires after {lifecycle.REQUEST_TTL_S // 3600} hours if no machine picks it up; "
                "there is nothing to do meanwhile."
                if manage
                else ""
            ),
        )
    trusted = _trusted(phase, life, now)
    if pending and phase == "stopped":
        if not lifecycle.report_fresh(life, now):
            # A restart a machine picked up, stopped, and then went silent on: not known to be restarting.
            return _status(
                "unknown",
                "quiet",
                "Unknown",
                "A machine picked up a restart and stopped it, and nothing newer has arrived, so it may not be "
                "restarting.",
                manage=manage,
            )
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
        return _status("unknown", "quiet", "Unknown", detail, manage=manage)
    if phase == "running":
        stale_settings = workspaces._restart_needed(row)
        return _status(
            "running",
            "ok",
            "Running",
            "It runs with the settings from before your last change. Restarting is not available in this version, "
            "so the change applies the next time it starts."
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
                " Starting is not available in this version; your latest settings apply the next time it starts."
                if workspaces._restart_needed(row)
                else ""
            ),
        )
    if phase == "failed":
        table = FAILURE_TEXT if manage else {**FAILURE_TEXT, **MEMBER_FAILURE_TEXT}
        text = table.get(
            reason,
            "It failed for a reason this page does not recognise. "
            + ("Check the machine." if manage else "Ask the workspace owner to check the machine."),
        )
        return _status("failed", "bad", "Failed", text, manage=manage)
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
    return isinstance(meta, dict) and meta.get("state") == "verified"


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
    # A workspace that is running (or starting or stopping) is past the check: "nothing can start on it" would contradict it.
    if (
        row["node_id"] is not None
        and not _machine_verified(row)
        and status["code"] not in ("running", "starting", "stopping")
    ):
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
                "text": "No machine can carry out a start yet.",
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
    attention = status["code"] in ("failed", "image_unreadable")  # Unknown only waits for a report
    blockers = _workspace_blockers(row, status, executor, manage, has_machines)
    health = {
        "state": "attention" if attention else "ok" if status["code"] == "running" else "idle",
        "blockers": blockers,
        "issues": [],
    }
    if life.get("expired_action"):
        health["issue_label"] = "Not carried out"
        health["issues"].append(
            f"Its earlier {life['expired_action']} request expired after "
            f"{lifecycle.REQUEST_TTL_S // 3600} hours without a machine picking it up, so it was not carried out."
            + (
                ""
                if executor
                else " Starting is not available in this version, so there is nothing to do yet."
            )
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
                f"Its last verification ended {ago(now - expires)}. Commonplace does not check target machines live. "
                "Verification is not available in this version of the app.",
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
            "Commonplace has not verified this machine yet, so nothing new can start on it. Cloud workspaces can still be "
            "saved for it. Verification is not available in this version of the app.",
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
            # Nobody can act on an unverified machine in this version: it waits (quiet), it is not an attention state.
            "state": "idle" if status["code"] != "verified" else "unknown",
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
        "end_unreported": 0,
        "finished_holding": 0,
        "holding_cards": [],
        "uncertain": 0,
        "queued": 0,
        "starts_hour": 0,
        "resources": None,
        "spend": {},
        "unknown_runs": 0,
        "runs": 0,
        "drain": {"draining": False, "seq": 0, "changed_at": None},
    }


# The run counts of a poll, as literal reads that each name their index. What holds a slot is ``jobs.in_flight`` (the lease's
# own definition, shared); the queue is a grouped read by state; a "started" run that holds no slot is read by time (a run
# stays "started" in ``runner_jobs`` after it ends: only the run ledger learns of the end). ``tests/test_runner_drain.py``
# asserts a SEARCH (not a SCAN) for each.
STARTED_WINDOW_S = (
    7 * 86400
)  # a started run that holds no slot and never reported an end is looked for this far back
FACT_QUEUED_SQL = (
    "SELECT machine_id,count(*) AS n FROM runner_jobs INDEXED BY runner_jobs_state_updated "
    "WHERE state='queued' GROUP BY machine_id"
)
# No LIMIT: the window bounds it, and a cut would make the count quietly wrong.
FACT_STARTED_OPEN_SQL = (
    "SELECT j.id AS id,j.machine_id AS machine_id FROM runner_jobs j INDEXED BY runner_jobs_state_updated "
    "LEFT JOIN run_ledger l ON l.job_id=j.id WHERE j.state='started' AND j.updated_at>? AND l.ended_at IS NULL"
)
FACT_RESOURCES_SQL = (
    "SELECT machine_id,reported_at,resources FROM runner_resources WHERE machine_id IN ({})"
)
FACT_ENDED_SQL = "SELECT job_id FROM run_ledger WHERE ended_at IS NOT NULL AND job_id IN ({})"
# Spend of the last 30 days: the five counted statuses, then the window twice (started, or never started and created).
FACT_SPEND_SQL = (
    "SELECT l.machine_id AS machine_id,e.currency AS currency,count(*) AS runs,sum(e.cost_micros) AS micros "
    "FROM run_ledger l LEFT JOIN cost_events e ON e.id=(SELECT max(id) FROM cost_events WHERE job_id=l.job_id) "
    "WHERE l.status IN (?,?,?,?,?) AND (l.started_at>=? OR (l.started_at IS NULL AND l.created_at>=?)) "
    "GROUP BY l.machine_id,e.currency"
)
FACT_SPEND_PLAIN_SQL = (
    "SELECT l.machine_id AS machine_id,l.currency AS currency,count(*) AS runs,sum(l.cost_micros) AS micros "
    "FROM run_ledger l WHERE l.status IN (?,?,?,?,?) "
    "AND (l.started_at>=? OR (l.started_at IS NULL AND l.created_at>=?)) GROUP BY l.machine_id,l.currency"
)
FACT_STARTS_HOUR_SQL = (
    "SELECT machine_id,count(*) AS n FROM runner_jobs INDEXED BY runner_jobs_starting "
    "WHERE starting_at>? GROUP BY machine_id"
)


def _count_slots(store, conn, machines, of, started_since):
    """Fill ``running``, ``uncertain``, ``finished_holding`` and ``end_unreported`` of each machine's facts (see
    ``_runner_facts``)."""
    from commonplace.runner import jobs

    names = {}
    for machine in machines:
        try:
            worker = store.gateway.db.connection(
                machine["worker_connection_id"], store.workspace_id
            )
            names[machine["id"]] = "conn:" + worker["name"]
        except Exception:  # noqa: BLE001 - a worker connection that is gone holds no cards
            continue
    cards = {}

    def working(machine_id):
        def read():
            if not cards:
                try:
                    cards.update(
                        jobs.working_cards(store.gateway, store.workspace_id, set(names.values()))
                    )
                except sqlite3.OperationalError:
                    cards["_none"] = set()  # no board database yet: nothing is being worked on
            return cards.get(names.get(machine_id), set())

        return read

    slots, held = {}, set()
    for machine in machines:
        rows = jobs.in_flight(conn, machine["id"], working(machine["id"]))
        slots[machine["id"]] = rows
        held.update(row["id"] for row in rows)
    started = [row["id"] for rows in slots.values() for row in rows if row["state"] == "started"]
    ended = set()
    for k in range(0, len(started), 500):
        chunk = started[k : k + 500]
        ended.update(
            row["job_id"]
            for row in conn.execute(FACT_ENDED_SQL.format(",".join("?" for _ in chunk)), chunk)
        )
    for machine_id, rows in slots.items():
        entry = of(machine_id)
        for row in rows:
            if row["state"] == "started" and row["id"] in ended:
                entry["finished_holding"] += 1
                entry["holding_cards"].append(f"{row['project']} card {row['task_number']}")
            else:
                entry["running"] += 1
                if row["state"] == "uncertain":
                    entry["uncertain"] += 1
    for row in conn.execute(FACT_STARTED_OPEN_SQL, (started_since,)):
        if row["id"] not in held:
            of(row["machine_id"])["end_unreported"] += 1


def _runner_facts(store, now, machines=()):
    """Per-machine run counts, resource reports, spend and drain flags from the runner store (``store`` is opened once by
    the caller). What holds a slot is ``jobs.in_flight``, the definition ``jobs.lease`` uses, so the screen and the hub
    cannot disagree: runs that are leased, starting or uncertain, and started runs whose card is still being worked on by
    the machine's worker. Of the started ones, those with no end in the run ledger are **in progress** (``running``);
    those whose end the ledger knows hold a slot only because their card is still Doing (``finished_holding``); started
    runs that hold no slot and never reported an end are ``end_unreported`` (last seven days)."""
    from commonplace.runner import drain

    since_epoch = now - SPEND_DAYS * 86400
    stamp = lambda epoch: datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    facts = {}

    def of(machine_id):
        return facts.setdefault(machine_id, blank_facts())

    with store.connection() as conn:
        for row in conn.execute(FACT_QUEUED_SQL):
            of(row["machine_id"])["queued"] = row["n"]
        _count_slots(store, conn, machines, of, stamp(now - STARTED_WINDOW_S))
        for row in conn.execute(FACT_STARTS_HOUR_SQL, (stamp(now - 3600),)):
            of(row["machine_id"])["starts_hour"] = row["n"]
        if _table(conn, "runner_resources") and machines:
            ids = [machine["id"] for machine in machines]
            for row in conn.execute(FACT_RESOURCES_SQL.format(",".join("?" for _ in ids)), ids):
                # One report that cannot be read is an unreported one for its machine, never a failed overview.
                try:
                    reported = _iso_to_epoch(row["reported_at"])
                    values = json.loads(row["resources"])
                except (TypeError, ValueError):
                    continue
                if reported is None or not isinstance(values, dict):
                    continue
                of(row["machine_id"])["resources"] = {"reported_at": reported, "values": values}
        if _table(conn, "run_ledger"):
            rows = conn.execute(
                FACT_SPEND_SQL if _table(conn, "cost_events") else FACT_SPEND_PLAIN_SQL,
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


RECENT_RUNS_SQL = (
    "SELECT j.id AS id,j.state AS state,j.project AS project,j.topic AS topic,j.board_slug AS board_slug,"
    "j.task_number AS task_number,j.outcome_code AS outcome_code,j.updated_at AS updated_at,"
    "l.status AS ledger_status,l.ended_at AS ended_at FROM runner_jobs j INDEXED BY runner_jobs_machine_updated "
    "LEFT JOIN run_ledger l ON l.job_id=j.id WHERE j.machine_id=? ORDER BY j.updated_at DESC,j.id DESC LIMIT ?"
)


def _recent_runs(conn, machine_id):
    return [dict(row) for row in conn.execute(RECENT_RUNS_SQL, (machine_id, ACTIVITY_LIMIT))]


def runner_status(machine, record, facts, now, setup_only=False):
    """The one status of a runner machine: ``record`` is ``topology.status.machine_status``."""
    # The age of the last signal is the health line's job (``last_seen``); a sentence here says what it means.
    running, held = facts["running"], facts["finished_holding"]
    drain = facts["drain"]
    note = f" It is also set to drain. {DRAIN_SCOPE}" if drain["draining"] else ""
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
    if setup_only:
        # Added through the setup guide and never heartbeated: its setup-only service sends no heartbeat, so the
        # signals here are only its own setup reports. Online or Offline would claim what nothing can see.
        return _status(
            "set_up",
            "quiet",
            "Set up and checked",
            "The machine passed its setup checks. This page cannot see whether it is switched on right now.",
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
                f"Draining. {DRAIN_SCOPE} {_runs(running)} in progress will finish."
                + (f" {_holding(held)}" if held else "")
                + f" {NO_RESUME}",
                next_text=_holding_step(facts["holding_cards"]) if held else None,
            )
        older = facts["end_unreported"]
        return _status(
            "drained",
            "wait",
            "Drained",
            f"Drained. {DRAIN_SCOPE} "
            + (
                f"None is known to be in progress; {_runs(older)} started and never reported an end."
                if older
                else "None is in progress."
            )
            + (f" {_holding(held)}" if held else "")
            + f" {NO_RESUME}",
            next_text=_holding_step(facts["holding_cards"]) if held else None,
        )
    used, cap = running + held, machine["max_in_flight"]
    if used:
        # One word and one count everywhere (chip, sentence, blocker): "Full" when every one of its runs at once is in
        # use, "Busy" when some are, and the same "used of cap" in the sentence and the blocker.
        full = used >= cap
        return _status(
            "full" if full else "busy",
            "ok",
            "Online · Full" if full else "Online · Busy",
            _in_use(running, held, cap) + (" New runs wait." if full else ""),
            next_text=_holding_step(facts["holding_cards"]) if held else None,
        )
    return _status("online", "ok", "Online", "Waiting for work.")


def runner_blockers(machine, record, facts, red_after_s=900):
    blockers = []
    reason = record["reason"]
    drain = facts["drain"]
    if reason == "revoked":
        blockers.append(
            {
                "code": "revoked",
                "text": "Removed: the owner removed this runner. It can't be used again.",
            }
        )
        return blockers
    if reason == "disabled":
        blockers.append(
            {
                "code": "disabled",
                "text": "Turned off: the owner turned it off. Turn it on again in Admin, Runner machines.",
            }
        )
    if machine["binding_state"] != "owner_attested":
        blockers.append(
            {
                "code": "not_approved",
                "text": "Not approved: it can't start runs yet. Approve its setup in Admin, Runner machines.",
            }
        )
    if drain["draining"]:
        # One word per state, the chip's: "Draining" while runs are in progress, "Drained" when none are. That resuming is
        # not available is said once, in the status sentence.
        word = "Draining" if facts["running"] else "Drained"
        blockers.append({"code": "draining", "text": f"{word}: {DRAIN_SCOPE}"})
    if reason == "never_reported":
        blockers.append(
            {
                "code": "never_connected",
                "text": "Never connected: it has not contacted Commonplace yet, so runs would wait.",
            }
        )
    elif reason == "no_heartbeat":
        blockers.append(
            {
                "code": "offline",
                "text": f"Offline: nothing heard for over {span(red_after_s)}, so new card runs would wait.",
            }
        )
    # A draining runner starts nothing whatever its load: "full" would say new runs wait for one to finish, which a drain
    # already overrides, so it is not listed beside it.
    held = facts["finished_holding"]
    if facts["running"] + held >= machine["max_in_flight"] and not drain["draining"]:
        blockers.append(
            {
                "code": "full",
                "text": "Full: "
                + _in_use(facts["running"], held, machine["max_in_flight"])
                + " New runs wait.",
            }
        )
    if facts["starts_hour"] >= machine["max_starts_per_hour"]:
        blockers.append(
            {
                "code": "hourly_cap",
                "text": f"Hourly limit: it reached {machine['max_starts_per_hour']} starts this hour. New runs wait.",
            }
        )
    return blockers


def _runner_usage(machine, facts, now, *, silent=""):
    """``silent``: why the machine's report is stale ("offline", "slow to report"), so a count of runs in progress is only
    what was recorded."""
    running = facts["running"]
    used = running + facts["finished_holding"]
    # The same count the status sentence and the blocker use: how many of its runs at once are in use.
    in_progress = (
        f"{_runs(running)} recorded in progress; more may be running while it is {silent}"
        if silent
        else f"{used} of {machine['max_in_flight']} runs at once in use"
    )
    usage = {
        "runs": {
            "known": True,
            "running": running,
            "max": machine["max_in_flight"],
            "queued": facts["queued"],
            "starts_last_hour": facts["starts_hour"],
            "max_starts_per_hour": machine["max_starts_per_hour"],
            "end_unreported": facts["end_unreported"],
            "finished_holding": facts["finished_holding"],
            "text": f"{in_progress}, "
            f"{facts['queued']} waiting, {facts['starts_hour']} started this hour "
            f"(limit {machine['max_starts_per_hour']})"
            + (f"; {_holding(facts['finished_holding'])}" if facts["finished_holding"] else "")
            + (
                f"; plus {_runs(facts['end_unreported'])} that started and never reported an end"
                if facts["end_unreported"]
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
    usage["summary"] = f"{in_progress} · {resources} · {spend_line}"
    return usage


def runner_item(machine, record, facts, now, red_after_s=900, setup_only=False):
    status = runner_status(machine, record, facts, now, setup_only)
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
    # Needs attention is what a person can act on: offline, turned off, uncertain runs. Slow to report only waits and a
    # removed runner is informational.
    # A finished run whose card is still in Doing is something a person can fix (move the card), so it counts too.
    attention = (
        status["code"] in ("offline", "disabled") or bool(issues) or bool(facts["finished_holding"])
    )
    item = {
        "kind": "runner",
        "id": machine["id"],
        "name": machine["label"],
        "subtitle": "Runner machine",
        "status": status,
        "last_seen": last,
        "health": {
            "state": "unknown"
            if status["code"] in ("never_connected", "set_up")
            else "attention"
            if attention
            else "idle"
            if status["code"] == "revoked"
            else "ok",
            "blockers": blockers,
            "issues": issues,
            "issue_label": "Needs a person",
            "heartbeat": {"status": record["status"], "reason": record["reason"]},
        },
        "usage": _runner_usage(
            machine,
            facts,
            now,
            silent={"offline": "offline", "slow": "slow to report"}.get(status["code"], ""),
        ),
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
    except sqlite3.OperationalError as exc:
        # Only a missing table means "no runner machines here"; a lock timeout or any other fault is not an empty list.
        if "no such table" in str(exc):
            return []
        raise StoreError("authority_unavailable", 503, reason="busy") from None


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
    facts = _runner_facts(runs, now, machines)
    items = []
    setup_only = pairing.setup_only_ids(gateway.db, ws)
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
                setup_only=machine["id"] in setup_only,
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
        "can_manage": manage,
        "executor": {
            "available": executor,
            "text": None
            if executor
            else "Starting and stopping cloud workspaces on a machine is not available in this version.",
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
    "workspace.request_expired": "A request expired without being carried out",
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
                    "text": "Drain started. " + DRAIN_SCOPE
                    if row["action"] == "drain"
                    else "Drain ended: new card runs can start again",
                }
            )
    entries.sort(key=lambda entry: entry["at"] or 0, reverse=True)
    return entries[:ACTIVITY_LIMIT]


def _runs_activity(runs, now):
    result = []
    ended_label = {
        "completed": "Finished",
        "errored": "Ended with an error",
        "stopped": "Stopped",
        "uncertain": "Uncertain",
    }
    for run in runs:
        # A run the ledger says ended is not "Started": the job row stays started after the end, only the ledger knows.
        ended = run.get("ended_at") if run["state"] == "started" else None
        at = _iso_to_epoch(ended or run["updated_at"])
        label = (
            ended_label.get(run.get("ledger_status"), "Finished")
            if ended
            else {
                "queued": "Waiting",
                "leased": "Handed to the machine",
                "starting": "Starting",
                "started": "Started",
                "failed": "Failed",
                "uncertain": "Uncertain",
                "cancelled": "Cancelled",
            }.get(run["state"], run["state"])
        )
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
                "value": (", ".join(placed["names"]) or "None")
                + (
                    f" and {placed['count'] - len(placed['names'])} more"
                    if placed["count"] > len(placed["names"])
                    else ""
                ),
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
            "label": "Allowed to start runs",
            "value": _allowed_to_start(machine, item),
        },
        {
            "label": "Logs",
            "value": "Not collected here: the activity list is the run history.",
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
            "label": "Logs",
            "value": "Not collected here: the activity list is the audit trail.",
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
                "The workspace is then marked Failed (the machine could not be reached). "
                "This does not undo what the machine did: it may still be starting or running."
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
                f"From now on no new card runs start on {name}. Queued runs ({queued}) stay in the queue; none is "
                "cancelled.",
                "Takes effect at once: Commonplace itself stops handing it card runs.",
            ],
            running_work=(
                f"{_runs(running)} in progress will finish normally; none is cancelled."
                if running
                else "None is in progress; nothing is cancelled."
            ),
            will_not=[
                DRAIN_SCOPE,
                "The runner program keeps running: this does not shut the machine down.",
            ],
        )
    else:
        base.update(
            title=f"Resume {name}?",
            effect=[f"New card runs can start on {name} again, within its limits."],
            running_work=(
                f"{_runs(running)} in progress are not affected."
                if running
                else "None is in progress; nothing is affected."
            ),
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


class _AuditFailed(Exception):
    """The hub-wide audit line could not be written, so the drain change is rolled back."""


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
        # The flag, the request id and the drain log line are one transaction in the runner store. The hub-wide audit
        # line goes in just before it commits and is its own write in another database, so the two cannot be one
        # atomic step: a failed audit rolls the drain back, and a crash between the audit and the commit leaves an
        # audit line for a drain that did not happen (never the other way round). The lock order is the lease's: the
        # runner write transaction first, the control lock inside it (``jobs.lease`` does the same), so the two can
        # never wait for each other. A runner removed between the check below and the commit is drained harmlessly
        # (nothing leases to a removed runner).
        with store_runner(store).transaction() as conn:
            if _machine_row(store, machine_id)["revoked_at"]:
                raise conflict("revoked")
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
                try:
                    store.gateway.db.audit(
                        action="runner.drain_started"
                        if action == "drain"
                        else "runner.drain_ended",
                        decision="ok",
                        workspace_id=store.workspace_id,
                        user_id=current.user_id,
                        connection_id=current.connection_id,
                        auth=current.auth,
                        target_ids=(machine_id,),
                    )
                except Exception:
                    logging.getLogger(__name__).exception(
                        "hub audit of a runner drain change failed"
                    )
                    raise _AuditFailed from None
    except drain.Refused as exc:
        raise conflict(
            exc.reason, "request_id" if exc.reason == "request_reused" else None
        ) from None
    except _AuditFailed:
        # Rolled back with its audit line: nothing changed, and the answer says why.
        raise StoreError("authority_unavailable", 503, reason="audit") from None
    except sqlite3.OperationalError:
        # A store that is busy or locked is not an audit problem: nothing changed, try again.
        raise StoreError("authority_unavailable", 503, reason="busy") from None
    return {
        "request": None,
        "item": detail(store, principal, "runner", machine_id, session_hash=session_hash)["item"],
    }, repeated
