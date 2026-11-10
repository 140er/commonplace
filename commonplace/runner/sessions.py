"""Runner-owned board sessions, independent of per-card starts and todo triggers.

One durable thread binding per board. A leased command is fenced before every host
operation; ambiguous effects are never replayed. Runner polling inspects threads,
not model turns. Only a nonempty actionable board schedules a continuation.
"""

import secrets
from contextlib import ExitStack

from commonplace.boards.store import active_blockers
from commonplace.control_db import ControlError
from commonplace.extensions import ToolError
from commonplace.orch import schema, store as configs
from commonplace.projects import models, orchestration, split
from commonplace.runner import jobs, predicate, registry
from commonplace.sessions import person_principal

SCHEMA = """
CREATE TABLE IF NOT EXISTS runner_sessions (
 board TEXT PRIMARY KEY,
 id TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL CHECK(state IN ('idle','running','stopped','uncertain')),
 paused INTEGER NOT NULL DEFAULT 0 CHECK(paused IN (0,1)),
 thread_id TEXT,
 config_version INTEGER NOT NULL,
 config_sha256 TEXT NOT NULL,
 machine_id TEXT NOT NULL,
 binding_epoch INTEGER NOT NULL,
 project TEXT NOT NULL, topic TEXT NOT NULL, runtime_mode TEXT NOT NULL,
 requested_by TEXT NOT NULL,
 model_provider TEXT NOT NULL, model TEXT NOT NULL,
 action TEXT CHECK(action IN ('launch','continue','inspect','stop')),
 phase TEXT CHECK(phase IN ('queued','leased','starting')),
 lease_token TEXT, lease_until TEXT,
 code TEXT,
 dispatch INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL
);
"""


def _open(gateway, ws):
    st = jobs.open_store(gateway, ws)
    # Convert a pending restore marker through the normal explicit open path.
    configs.open_store(gateway, ws)
    with st.connection() as conn:
        conn.executescript(SCHEMA)
    return st


def board_state(gateway, ws, board):
    """All unfinished cards count unless an explicit dependency or person ask blocks them."""
    with orchestration.board_read(gateway.workspace_dir(ws)) as conn:
        b = conn.execute("SELECT * FROM boards WHERE slug=? AND archived=0", (board,)).fetchone()
        if b is None:
            raise ToolError("no: board not found")
        actionable, blocked = [], []
        for row in conn.execute(
            "SELECT * FROM tasks WHERE board_id=? AND status!='done'", (b["id"],)
        ):
            dependencies = active_blockers(conn, row["id"])
            if dependencies:
                blocked.append(
                    {
                        "number": row["id"],
                        "code": "blocked_by",
                        "reason": "Waiting on "
                        + ", ".join(f"#{d['number']}: {d['title']}" for d in dependencies),
                    }
                )
            elif row["ask"] and (row["ask_to"] or "").startswith("user:"):
                blocked.append(
                    {"number": row["id"], "code": "waits_on_person", "reason": row["ask"]}
                )
            else:
                actionable.append(row["id"])
    return dict(b), {"actionable": actionable, "blocked": blocked}


def _host(conn, board):
    version = configs.accepted_in(conn, board)
    if version is None or not version.config().has_runner:
        return None
    return version


def _authority(gateway, ws, conn, version, board, row=None):
    """Called with runner then control locked, held through the guarded commit."""
    if configs.marker_pending(gateway.workspace_dir(ws)) or configs.restore_state_in(conn).flagged:
        return "restore_pending"
    config = version.config()
    machine = registry.get_machine(gateway.db, ws, config.runner.machine)
    if machine is None or machine["revoked_at"] or not machine["enabled"]:
        return "machine_unavailable"
    if machine["binding_state"] != "owner_attested":
        return "binding_not_attested"
    if row and (
        row["config_version"] != version.version
        or row["config_sha256"] != version.sha256
        or row["binding_epoch"] != machine["binding_epoch"]
    ):
        return "binding_changed"
    try:
        actor = version.decided_by or ""
        principal = (
            person_principal(gateway.db, actor.removeprefix("user:"), ws)
            if actor.startswith("user:")
            else None
        )
    except ControlError:
        principal = None
    if not predicate.requester_ok(gateway, principal, machine):
        return "requester_invalid"
    role = next(
        (r for r in config.roles.values() if r.kind == "orchestrator" and r.parent is None), None
    )
    if role is None or role.harness != "t3" or not role.topic or not role.model_role:
        return "orchestrator_role_invalid"
    if (
        config.runner.project not in predicate.grant_values(machine, "projects")
        or role.topic not in predicate.grant_values(machine, "topics")
        or board["wing"] not in predicate.grant_values(machine, "wings")
        or not principal.policy.can_read_wing(board["wing"])
        or jobs.MODES.index(config.runner.runtime_mode)
        > jobs.MODES.index(machine["max_runtime_mode"])
    ):
        return "grant_changed"
    choice = models.resolve(gateway.db, ws, board["slug"], role.model_role)
    if choice is None:
        return "model_unresolved"
    if row and (row["model_provider"], row["model"]) != (choice.provider, choice.model):
        return "model_changed"
    return None


def _new(conn, gateway, ws, version, board):
    config = version.config()
    role = next(r for r in config.roles.values() if r.kind == "orchestrator" and r.parent is None)
    machine = registry.get_machine(gateway.db, ws, config.runner.machine)
    choice = models.resolve(gateway.db, ws, board["slug"], role.model_role)
    conn.execute(
        "INSERT INTO runner_sessions(board,id,state,config_version,config_sha256,machine_id,binding_epoch,"
        "project,topic,runtime_mode,requested_by,model_provider,model,updated_at) VALUES (?,?, 'idle',?,?,?,?,?,?,?,?,?,?,?)",
        (
            board["slug"],
            "session_" + secrets.token_hex(8),
            version.version,
            version.sha256,
            machine["id"],
            machine["binding_epoch"],
            config.runner.project,
            role.topic,
            config.runner.runtime_mode,
            version.decided_by,
            choice.provider,
            choice.model,
            jobs._now(),
        ),
    )
    return conn.execute("SELECT * FROM runner_sessions WHERE board=?", (board["slug"],)).fetchone()


def status(gateway, ws, board):
    b, work = board_state(gateway, ws, board)
    with split.runner_read(gateway.workspace_dir(ws)) as conn:
        if (
            conn is None
            or not conn.execute("SELECT 1 FROM sqlite_master WHERE name='orch_configs'").fetchone()
        ):
            return {"state": "no_host", "reason": "no host attached", **work}
        version = _host(conn, board)
        if version is None:
            return {"state": "no_host", "reason": "no host attached", **work}
        has_sessions = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='runner_sessions'"
        ).fetchone()
        row = (
            conn.execute("SELECT * FROM runner_sessions WHERE board=?", (board,)).fetchone()
            if has_sessions
            else None
        )
        problem = _authority(gateway, ws, conn, version, b, row)
        return {
            "state": (
                "stopping"
                if row and row["paused"] and row["phase"]
                else "stopped"
                if row and row["paused"] and row["state"] != "uncertain"
                else "unavailable"
                if problem
                else row["state"]
                if row
                else "idle"
            ),
            "reason": problem or (row["code"] if row else None),
            "thread_id": row["thread_id"] if row else None,
            "pending": row["action"] if row else None,
            **work,
        }


def control(gateway, principal, board, action):
    if not (
        principal.person
        and principal.role == "owner"
        and getattr(principal, "runner", None) is None
    ):
        raise ToolError("no: owner only")
    st = _open(gateway, principal.workspace_id)
    b, _ = board_state(gateway, principal.workspace_id, board)
    if not principal.policy.can_read_wing(b["wing"]):
        raise ToolError("no: board not found")
    with ExitStack() as locks, st.transaction() as conn:
        locks.enter_context(gateway.db._lock)
        version = _host(conn, board)
        if version is None:
            raise ToolError("no: no host attached")
        row = conn.execute("SELECT * FROM runner_sessions WHERE board=?", (board,)).fetchone()
        # Stop must remain available even if grants were withdrawn.
        if action == "resume":
            if row is not None and row["state"] == "uncertain":
                raise ToolError("no: session effect uncertain; person must reconcile")
            refusal = _authority(gateway, principal.workspace_id, conn, version, b, row)
            if refusal:
                raise ToolError("no: " + refusal)
        if row is None:
            refusal = _authority(gateway, principal.workspace_id, conn, version, b)
            if refusal:
                raise ToolError("no: " + refusal)
            row = _new(conn, gateway, principal.workspace_id, version, b)
        if action == "stop":
            if row["phase"] == "starting":
                # Keep the fence and accept the in-flight launch acknowledgement,
                # so a stop racing a launch never loses the returned thread binding.
                conn.execute(
                    "UPDATE runner_sessions SET paused=1,updated_at=? WHERE board=?",
                    (jobs._now(), board),
                )
            else:
                conn.execute(
                    "UPDATE runner_sessions SET paused=1,state='stopped',action=?,phase=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE board=?",
                    (
                        "stop" if row["thread_id"] else None,
                        "queued" if row["thread_id"] else None,
                        jobs._now(),
                        board,
                    ),
                )
        elif action == "resume":
            if row["phase"] is not None:
                raise ToolError("no: session command pending")
            conn.execute(
                "UPDATE runner_sessions SET paused=0,state='idle',code=NULL,updated_at=? WHERE board=?",
                (jobs._now(), board),
            )
        else:
            raise ValueError("invalid session control")
    return status(gateway, principal.workspace_id, board)


def lease(gateway, ws, machine_id):
    st = _open(gateway, ws)
    with st.connection() as conn:
        turns = {
            r["board"]: r["dispatch"]
            for r in conn.execute("SELECT board,dispatch FROM runner_sessions")
        }
        boards = sorted(configs.configured_boards_in(conn), key=lambda b: (turns.get(b, 0), b))
    for name in boards:
        b, work = board_state(gateway, ws, name)
        with ExitStack() as locks, st.transaction() as conn:
            locks.enter_context(gateway.db._lock)
            version = _host(conn, name)
            if version is None or version.config().runner.machine != machine_id:
                continue
            row = conn.execute("SELECT * FROM runner_sessions WHERE board=?", (name,)).fetchone()
            if _authority(gateway, ws, conn, version, b, row):
                continue
            if row is None:
                row = _new(conn, gateway, ws, version, b)
            if row["state"] == "uncertain":
                continue
            if row["phase"] in ("leased", "starting"):
                if row["lease_until"] > jobs._now():
                    continue
                if row["phase"] == "starting":
                    conn.execute(
                        "UPDATE runner_sessions SET state='uncertain',phase=NULL,action=NULL,code='effect_unknown' WHERE board=?",
                        (name,),
                    )
                    continue
            action = row["action"]
            if row["paused"]:
                if action not in ("stop", "inspect"):
                    continue
            elif action is None:
                if row["state"] == "running":
                    action = "inspect"
                elif work["actionable"]:
                    action = "continue" if row["thread_id"] else "launch"
                else:
                    continue
            token = secrets.token_urlsafe(32)
            conn.execute(
                "UPDATE runner_sessions SET action=?,phase='leased',lease_token=?,lease_until=?,updated_at=?,dispatch=(SELECT COALESCE(MAX(dispatch),0)+1 FROM runner_sessions) WHERE board=?",
                (action, token, jobs._after(jobs._now(), 120), jobs._now(), name),
            )
            config = version.config()
            role_name = next(
                k for k, r in config.roles.items() if r.kind == "orchestrator" and r.parent is None
            )
            return {
                "job_id": row["id"],
                "board_slug": name,
                "project": row["project"],
                "topic": row["topic"],
                "runtime_mode": row["runtime_mode"],
                "action": action,
                "thread_id": row["thread_id"],
                "lease_token": token,
                "modelSelection": {"instanceId": row["model_provider"], "model": row["model"]},
                "message": schema.role_instructions(config, role_name)
                + f"\nCoordinate board {name}. Keep working until every card is done or explicitly blocked by a dependency or person-directed ask. Capacity is not blocked. Re-read the board after each batch. The machine runner manages this session's continuation and stop; do not start another session.",
            }
    return None


def _fenced(conn, machine_id, token):
    row = conn.execute(
        "SELECT * FROM runner_sessions WHERE machine_id=? AND lease_token=?", (machine_id, token)
    ).fetchone()
    if row is None or row["lease_until"] <= jobs._now():
        raise jobs.Conflict("session lease is no longer current")
    return row


def starting(gateway, ws, machine_id, token):
    st = _open(gateway, ws)
    with st.connection() as conn:
        row = _fenced(conn, machine_id, token)
        name = row["board"]
    with ExitStack() as locks, st.transaction() as conn:
        b, work = board_state(gateway, ws, name)
        locks.enter_context(gateway.db._lock)
        row = _fenced(conn, machine_id, token)
        version = _host(conn, name)
        if version is None or _authority(gateway, ws, conn, version, b, row):
            raise jobs.Conflict("session authority changed")
        if row["phase"] != "leased":
            raise jobs.Conflict("session command already started")
        # A board that cleared after leasing must not spend a model turn.
        if row["action"] in ("launch", "continue") and not work["actionable"]:
            conn.execute(
                "UPDATE runner_sessions SET state='idle',action=NULL,phase=NULL,lease_token=NULL WHERE board=?",
                (name,),
            )
            return False
        conn.execute("UPDATE runner_sessions SET phase='starting' WHERE board=?", (name,))
    return True


def result(gateway, ws, machine_id, token, outcome, thread_id=None):
    if outcome not in ("running", "finished", "stopped", "uncertain"):
        raise ValueError("invalid session outcome")
    if thread_id is not None and (
        not isinstance(thread_id, str) or jobs.THREAD_ID.fullmatch(thread_id) is None
    ):
        raise ValueError("invalid thread id")
    st = _open(gateway, ws)
    with st.connection() as conn:
        candidate = _fenced(conn, machine_id, token)
        name = candidate["board"]
    # Read under the runner write transaction below: claims serialize via runner
    # admission, and an event committed later is found by the next idle sweep.
    with st.transaction() as conn:
        row = _fenced(conn, machine_id, token)
        if row["phase"] != "starting":
            raise jobs.Conflict("session command not started")
        allowed = {
            "launch": ("running", "uncertain"),
            "continue": ("running", "uncertain"),
            "inspect": ("running", "finished", "uncertain"),
            "stop": ("running", "stopped", "uncertain"),
        }
        if outcome not in allowed[row["action"]]:
            raise ValueError("outcome does not match session command")
        if row["thread_id"] and thread_id not in (None, row["thread_id"]):
            raise jobs.Conflict("session thread changed")
        thread_id = thread_id or row["thread_id"]
        if outcome == "running" and not thread_id:
            raise ValueError("thread id required")
        if outcome == "stopped" and row["action"] != "stop":
            raise ValueError("not a stop command")
        state, action = outcome, None
        if outcome == "finished":
            _, work = board_state(gateway, ws, name)
            if work["actionable"]:  # Finish guard: no actionable card may be forgotten.
                state, action = "running", "continue"
            else:
                state = "idle"
        if row["paused"]:
            state = "stopped" if outcome != "uncertain" else "uncertain"
            if outcome == "running":
                action = "inspect" if row["action"] in ("stop", "inspect") else "stop"
            elif outcome in ("finished", "stopped"):
                action = None
        conn.execute(
            "UPDATE runner_sessions SET state=?,thread_id=?,action=?,phase=?,lease_token=NULL,lease_until=NULL,code=?,updated_at=? WHERE board=?",
            (
                state,
                thread_id,
                action,
                "queued" if action else None,
                "effect_unknown" if outcome == "uncertain" else None,
                jobs._now(),
                name,
            ),
        )
    return {"state": state}
