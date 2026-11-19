"""Cloud workspace lifecycle: start, stop and restart requests and the phases an executor reports.

The owner's REST call only ever records a **request** (``request``); only the executor seam
(``claim`` and ``report``) moves a phase, because only a machine knows what it did. No executor
exists in this version, so a request waits until the owner withdraws it. Nothing here reaches a
machine, opens custody or starts anything. Tests drive the seam with a fake executor.

Every refusal is a ``StoreError`` with a fixed ``reason``. A token (``seq`` plus the record's
``version``) makes a stale confirmation a conflict, and a ``request_id`` makes a retry after a
lost answer return the same request.

Docs: docs/system/compute-overview.md.
"""

from __future__ import annotations

import re
import secrets

from . import workspaces
from .control import StoreError
from .workspaces import (  # noqa: F401 -- has_started re-exported for callers
    FAILURE_REASONS,
    has_started,
)

PHASES = ("not_started", "starting", "running", "stopping", "stopped", "failed")
ACTIONS = ("start", "stop", "restart")
WORKSPACE_ACTIONS = (*ACTIONS, "withdraw")
# A phase an executor reported (starting, running, stopping) is trusted this long after the report; later
# it is unknown. The same window as the runner resource report.
REPORT_STALE_S = 600
# What a stop gives the container before it is ended; the confirmation states it.
STOP_GRACE_S = 30
# An unclaimed request that no machine picked up within this long expires: it is cleared when a machine asks for
# work (``claim``) and whenever the owner asks for anything else, and it shows as expired until then, so an old
# "start" can never be carried out by an executor that appears days later.
REQUEST_TTL_S = 24 * 3600
_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,64}", re.ASCII)

_START_FROM = frozenset({"not_started", "stopped", "failed"})
_STOP_FROM = frozenset({"running", "starting", "failed"})
_RESTART_FROM = frozenset({"running", "failed"})
# Why an action is refused in a phase, one reason each (the app words them).
_REFUSED = {
    "start": {
        "running": "already_running",
        "starting": "already_starting",
        "stopping": "still_stopping",
    },
    "stop": {
        "not_started": "not_started",
        "stopped": "already_stopped",
        "stopping": "already_stopping",
    },
    "restart": {
        "not_started": "not_started",
        "stopped": "not_running",
        "starting": "not_running",
        "stopping": "not_running",
    },
}
# Executor moves: phase -> phases it may report next, and which claimed request it needs.
_MOVES = {
    "not_started": {"starting": ("start", "restart")},
    "stopped": {"starting": ("start", "restart")},
    "failed": {"starting": ("start", "restart"), "stopping": ("stop", "restart")},
    "starting": {
        "running": ("start", "restart"),
        "failed": ("start", "restart"),
        "stopping": ("stop",),
    },
    "running": {"stopping": ("stop", "restart"), "failed": ("start", "restart", "stop")},
    "stopping": {"stopped": ("stop", "restart"), "failed": ("stop", "restart")},
}


def conflict(reason, field=None):
    return StoreError("conflict", 409, field=field, reason=reason)


def schema(conn):
    """The one table. Created in its own ``BEGIN IMMEDIATE`` step (or the caller's open transaction) with
    ``IF NOT EXISTS``, so two processes opening the store together never both create it and a failure
    leaves nothing half made. A discarded record cascades its lifecycle row away."""
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_workspace_lifecycle'"
    ).fetchone():
        return
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cloud_workspace_lifecycle ("
            "workspace_id TEXT PRIMARY KEY REFERENCES cloud_workspaces(id) ON DELETE CASCADE,"
            "phase TEXT NOT NULL DEFAULT 'not_started' CHECK(phase IN "
            "('not_started','starting','running','stopping','stopped','failed')),"
            "reason TEXT, seq INTEGER NOT NULL DEFAULT 0, phase_at REAL NOT NULL, reported_at REAL,"
            "request_action TEXT CHECK(request_action IN ('start','stop','restart')),"
            "requested_by TEXT, requested_at REAL, claimed_by TEXT, claimed_at REAL, claim_token INTEGER,"
            "last_request_id TEXT, last_request_action TEXT)"
        )
    except BaseException:
        if own:
            conn.rollback()
        raise
    if own:
        conn.commit()


def default(now):
    """What a record with no lifecycle row yet says: never started, nothing requested."""
    return {
        "phase": "not_started",
        "reason": None,
        "seq": 0,
        "phase_at": now,
        "reported_at": None,
        "request_action": None,
        "requested_by": None,
        "requested_at": None,
        "claimed_by": None,
        "claimed_at": None,
        "claim_token": None,
        "last_request_id": None,
        "last_request_action": None,
    }


def life_of(conn, workspace_id, now):
    row = conn.execute(
        "SELECT * FROM cloud_workspace_lifecycle WHERE workspace_id=?", (workspace_id,)
    ).fetchone()
    return dict(row) if row is not None else default(now)


def _ensure_row(conn, workspace_id, now):
    conn.execute(
        "INSERT OR IGNORE INTO cloud_workspace_lifecycle(workspace_id,phase_at) VALUES (?,?)",
        (workspace_id, now),
    )


def executor_available(gateway):
    """Whether something can carry a request out. False until a lifecycle executor registers itself."""
    return bool(getattr(gateway, "_compute_workspace_executor", None))


def report_age(life, now):
    return None if life["reported_at"] is None else max(0, int(now - life["reported_at"]))


def phase_trusted(life, now):
    """A moving or running phase is believed only while its report is fresh; stopped, failed and
    never-started are facts that do not expire."""
    if life["phase"] in ("starting", "running", "stopping"):
        age = report_age(life, now)
        return age is not None and age <= REPORT_STALE_S
    return True


def request_expired(life, now):
    """True for an unclaimed request older than ``REQUEST_TTL_S``."""
    return (
        life["request_action"] is not None
        and life["claimed_at"] is None
        and life["requested_at"] is not None
        and now - life["requested_at"] > REQUEST_TTL_S
    )


def effective(life, now):
    """The lifecycle row with an expired request taken out (pure: nothing is written)."""
    if not request_expired(life, now):
        return life
    return {
        **life,
        "request_action": None,
        "requested_by": None,
        "requested_at": None,
        "expired_action": life["request_action"],
    }


def _clear_expired(conn, workspace_id, life, now):
    """Write the expiry down: the request goes and the audit trail says why. ``seq`` stays: the owner's view
    already showed no request, so what they read is still what they act on."""
    if not request_expired(life, now):
        return life
    conn.execute(
        "UPDATE cloud_workspace_lifecycle SET request_action=NULL,requested_by=NULL,requested_at=NULL,"
        "claimed_by=NULL,claimed_at=NULL,claim_token=NULL WHERE workspace_id=?",
        (workspace_id,),
    )
    conn.execute(
        "INSERT INTO audit(object_id,action,user_id,created_at) VALUES (?,?,?,?)",
        (workspace_id, "workspace.request_expired", "system", now),
    )
    return life_of(conn, workspace_id, now)


def request_waiting(conn, workspace_id, now):
    """True while a start, stop or restart request waits for a machine to pick it up (an expired one does not
    count: it is not carried out). A claimed request is not waiting: the machine holds its own copy of the
    machine and settings revision, and an edit after that is reported as "restart needed"."""
    life = life_of(conn, workspace_id, now)
    return (
        life["request_action"] is not None
        and life["claimed_at"] is None
        and not request_expired(life, now)
    )


def _check_token(value, name, *, minimum):
    if type(value) is not int or value < minimum:
        raise workspaces.refuse(name, "invalid")
    return value


def decide(row, life, action, now):
    """None when ``action`` may be requested now, or the reason it may not. Pure over the two rows."""
    life = effective(life, now)
    phase, pending = life["phase"], life["request_action"]
    if action == "withdraw":
        if not pending:
            return "nothing_to_withdraw"
        if life["claimed_at"] is not None and phase_trusted(life, now):
            return "already_picked_up"
        return None
    if row["node_id"] is None:
        return "no_machine"
    if workspaces.status_of(row)[0] == "needs_credential":
        return "needs_credential"
    if pending:
        return "request_pending"
    allowed = {"start": _START_FROM, "stop": _STOP_FROM, "restart": _RESTART_FROM}[action]
    if phase in allowed:
        return None
    return _REFUSED[action].get(phase, "not_running")


def request(
    store,
    principal,
    workspace_id,
    action,
    *,
    session_hash,
    expected_seq,
    expected_version,
    request_id=None,
):
    """Record a start, stop, restart or withdraw request. Returns ``(result, repeated)``.

    ``result`` holds the lifecycle row as it is after the call and the request; nothing has
    reached a machine. A repeat (same ``request_id``, or the same action already waiting)
    changes nothing and is not an error.
    """
    if action not in WORKSPACE_ACTIONS:
        raise StoreError("not_found", 404, reason="route")
    _check_token(expected_seq, "expected_seq", minimum=0)
    _check_token(expected_version, "expected_version", minimum=1)
    if request_id is not None and (
        type(request_id) is not str or not _REQUEST_ID.fullmatch(request_id)
    ):
        raise workspaces.refuse("request_id", "invalid")
    # An expired request is cleared and audited in a transaction of its own, committed before the decision: a
    # refusal below rolls its own transaction back, and the expiry record must not go with it.
    with store._transaction(principal, session_hash) as (conn, _):
        workspaces._row(conn, workspace_id)
        now = store._now()
        _clear_expired(conn, workspace_id, life_of(conn, workspace_id, now), now)
    with store._transaction(principal, session_hash) as (conn, current):
        row = workspaces._row(conn, workspace_id)
        now = store._now()
        life = life_of(conn, workspace_id, now)
        if request_id is not None and life["last_request_id"] == request_id:
            if life["last_request_action"] != action:
                raise conflict("request_reused", "request_id")
            return _result(life), True
        if action != "withdraw" and life["request_action"] == action:
            return _result(life), True
        if expected_seq != life["seq"] or expected_version != row["version"]:
            raise conflict("stale_state")
        refusal = decide(row, life, action, now)
        if refusal is not None:
            raise conflict(refusal)
        _ensure_row(conn, workspace_id, now)
        if action == "withdraw":
            claimed = life["claimed_at"] is not None
            conn.execute(
                "UPDATE cloud_workspace_lifecycle SET request_action=NULL,requested_by=NULL,"
                "requested_at=NULL,claimed_by=NULL,claimed_at=NULL,claim_token=NULL,seq=seq+1,"
                "last_request_id=?,last_request_action=? WHERE workspace_id=?",
                (request_id, action, workspace_id),
            )
            if claimed:
                # A machine picked the request up and then went quiet: nothing is going to report on it any
                # more, so the phase it left (starting or stopping) would never end. It ends as failed, the
                # machine could not be reached, and the owner can start, stop or restart from there.
                conn.execute(
                    "UPDATE cloud_workspace_lifecycle SET phase='failed',reason='machine_unreachable',"
                    "phase_at=? WHERE workspace_id=?",
                    (now, workspace_id),
                )
                workspaces.set_state(conn, workspace_id, "failed", "machine_unreachable")
            store._audit(conn, current, workspace_id, "workspace.request_withdrawn")
        else:
            conn.execute(
                "UPDATE cloud_workspace_lifecycle SET request_action=?,requested_by=?,requested_at=?,"
                "claimed_by=NULL,claimed_at=NULL,claim_token=NULL,seq=seq+1,"
                "last_request_id=?,last_request_action=? WHERE workspace_id=?",
                (action, current.user_id, now, request_id, action, workspace_id),
            )
            store._audit(conn, current, workspace_id, "workspace." + action + "_requested")
        return _result(life_of(conn, workspace_id, now)), False


def _result(life):
    return {
        "phase": life["phase"],
        "seq": life["seq"],
        "request": None
        if not life["request_action"]
        else {
            "action": life["request_action"],
            "requested_at": life["requested_at"],
            "state": "requested" if life["claimed_at"] is None else "in_progress",
        },
    }


def claim(conn, executor_id, now):
    """Executor seam: take the oldest unclaimed request and move its phase. None when there is none.

    The caller holds a transaction on the Compute store. Never called by REST.
    """
    for stale in conn.execute(
        "SELECT * FROM cloud_workspace_lifecycle WHERE request_action IS NOT NULL AND claimed_at IS NULL "
        "AND requested_at<=?",
        (now - REQUEST_TTL_S,),
    ).fetchall():
        _clear_expired(conn, stale["workspace_id"], dict(stale), now)
    row = conn.execute(
        "SELECT l.*, w.revision AS settings_revision, w.node_id AS node_id "
        "FROM cloud_workspace_lifecycle l JOIN cloud_workspaces w ON w.id=l.workspace_id "
        "WHERE l.request_action IS NOT NULL AND l.claimed_at IS NULL "
        "ORDER BY l.requested_at, l.workspace_id LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    action = row["request_action"]
    # Phase the claim moves to: a start begins starting; a stop or restart begins stopping.
    target = "starting" if action == "start" else "stopping"
    if target not in _MOVES.get(row["phase"], {}):
        raise conflict("cannot_claim")
    # The claim token is a secret of the claim, not a function of the counter: an executor that was never
    # handed this claim cannot guess it. It stays below 2**62 so SQLite keeps it as an integer.
    token = secrets.randbits(61) + 1
    seq = row["seq"] + 1
    conn.execute(
        "UPDATE cloud_workspace_lifecycle SET claimed_by=?,claimed_at=?,claim_token=?,phase=?,"
        "reason=NULL,phase_at=?,reported_at=?,seq=? WHERE workspace_id=?",
        (executor_id, now, token, target, now, now, seq, row["workspace_id"]),
    )
    conn.execute(
        "INSERT INTO audit(object_id,action,user_id,created_at) VALUES (?,?,?,?)",
        (row["workspace_id"], "workspace.request_claimed", "system", now),
    )
    return {
        "workspace_id": row["workspace_id"],
        "action": action,
        "claim_token": token,
        "node_id": row["node_id"],
        "revision": row["settings_revision"],
    }


def report(conn, workspace_id, claim_token, phase, now, *, reason=None, applied_revision=None):
    """Executor seam: record what the machine did. Refused (and nothing changes) for a stale claim.

    Reporting the phase already recorded only refreshes ``reported_at`` (a heartbeat). A report of
    ``running`` carries the settings revision that was applied; ``failed`` carries a reason from
    ``FAILURE_REASONS``.
    """
    if phase not in PHASES or (phase == "failed") != (reason is not None):
        raise conflict("invalid_report")
    if reason is not None and reason not in FAILURE_REASONS:
        raise conflict("invalid_report")
    life = conn.execute(
        "SELECT * FROM cloud_workspace_lifecycle WHERE workspace_id=?", (workspace_id,)
    ).fetchone()
    if (
        life is None
        or life["request_action"] is None
        or life["claimed_at"] is None
        or life["claim_token"] != claim_token
    ):
        raise conflict("stale_claim")
    if phase == life["phase"]:
        conn.execute(
            "UPDATE cloud_workspace_lifecycle SET reported_at=? WHERE workspace_id=?",
            (now, workspace_id),
        )
        return
    allowed = _MOVES.get(life["phase"], {}).get(phase)
    if allowed is None or life["request_action"] not in allowed:
        raise conflict("invalid_report")
    row = conn.execute(
        "SELECT revision FROM cloud_workspaces WHERE id=?", (workspace_id,)
    ).fetchone()
    # The request is done when a start or restart is running, a stop is stopped, or anything failed.
    done = phase in ("running", "failed") or (
        phase == "stopped" and life["request_action"] == "stop"
    )
    if phase == "running":
        if type(applied_revision) is not int or not 1 <= applied_revision <= row["revision"]:
            raise conflict("invalid_report")
        try:
            workspaces.set_state(
                conn, workspace_id, "ready", None, applied_revision=applied_revision
            )
        except ValueError:
            # The settings were edited after the machine read them: it runs the older revision, which the
            # record shows as "restart needed" (an applied revision behind the saved one), never as current.
            conn.execute(
                "UPDATE cloud_workspaces SET applied_revision=? WHERE id=?",
                (applied_revision, workspace_id),
            )
    elif phase == "failed":
        workspaces.set_state(conn, workspace_id, "failed", reason)
    elif phase == "stopped":
        workspaces.set_state(conn, workspace_id, "pending", None)
    if done:
        conn.execute(
            "UPDATE cloud_workspace_lifecycle SET phase=?,reason=?,phase_at=?,reported_at=?,seq=seq+1,"
            "request_action=NULL,requested_by=NULL,requested_at=NULL,claimed_by=NULL,claimed_at=NULL,"
            "claim_token=NULL WHERE workspace_id=?",
            (phase, reason, now, now, workspace_id),
        )
    else:
        conn.execute(
            "UPDATE cloud_workspace_lifecycle SET phase=?,reason=?,phase_at=?,reported_at=?,seq=seq+1 "
            "WHERE workspace_id=?",
            (phase, reason, now, now, workspace_id),
        )
    conn.execute(
        "INSERT INTO audit(object_id,action,user_id,created_at) VALUES (?,?,?,?)",
        (workspace_id, "workspace.reported_" + phase, "system", now),
    )
