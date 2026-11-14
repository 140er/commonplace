"""Read-only authority checks shared by request and lease validation."""

import json

from commonplace.boards import task_types
from commonplace.boards.service import BoardService, _waiting_on_person
from commonplace.extensions import ToolContext, ToolError
from commonplace.policy import canonical_wing


class Refused(ToolError):
    """A card cannot be started for the given reason."""

    def __init__(self, reason, detail=""):
        self.reason = reason
        message = (
            "worker unavailable"
            if reason == "worker unavailable"
            else f"card is not startable: {reason}"
        )
        super().__init__(f"no: {message}" + (f": {detail}" if detail else ""))


def grant_values(machine, key):
    """Accept the registry's decoded grants and persisted JSON rows."""
    value = machine[key]
    return json.loads(value) if isinstance(value, str) else value


def requester_gaps(gateway, principal, machine) -> list[str]:
    """Why ``principal`` cannot start on ``machine``: stable slugs, empty when allowed.

    ``requester_ok`` is true exactly when this list is empty; a caller that refuses uses
    the slugs to say which prerequisite is missing."""
    if principal is None or principal.workspace_id != machine["workspace_id"]:
        return ["workspace_mismatch"]
    if getattr(principal, "runner", None) is not None:
        return ["runner_credential"]
    if principal.person:
        return [] if principal.role == "owner" else ["owner_required"]
    live = gateway.db.authenticate_connection(principal.connection_id)
    if (
        live is None
        or live.workspace_id != machine["workspace_id"]
        or getattr(live, "runner", None) is not None
        or live.auth != "pat"
    ):
        return ["pat_required"]
    gaps = []
    if live.policy.trust != "trusted":
        gaps.append("seat_not_trusted")
    if live.connection_id not in grant_values(machine, "requesters"):
        gaps.append("not_on_requester_list")
    return gaps


def requester_ok(gateway, principal, machine) -> bool:
    return not requester_gaps(gateway, principal, machine)


def startable(gateway, machine, row, board) -> None:
    worker = gateway.db.authenticate_connection(machine["worker_connection_id"])
    if worker is None or worker.workspace_id != machine["workspace_id"]:
        raise Refused("worker unavailable")
    service = BoardService(ToolContext(worker, gateway), store=False)
    from commonplace.boards.store import active_blockers
    from commonplace.projects import orchestration as orch
    from commonplace.snooze import store as snooze

    with orch.board_read(gateway.workspace_dir(machine["workspace_id"])) as conn:
        if snooze.active(conn, row["id"]):
            raise Refused("snoozed")
    if orch.enabled(gateway.db, machine["workspace_id"]):
        with orch.board_read(gateway.workspace_dir(machine["workspace_id"])) as conn:
            if active_blockers(conn, row["id"]):
                raise Refused("blocked_by")
    if _waiting_on_person(row):
        raise Refused("waiting_on_person")
    own_claim = row["status"] == "doing" and row["assignee"] == worker.actor
    if row["status"] != "todo" and not own_claim:
        raise Refused("not_todo")
    if row["assignee"] not in (None, worker.actor):
        raise Refused("assigned")
    if row["proposal_status"] in ("pending", "rejected"):
        raise Refused("proposal_open")
    if not service._claimable_origin(row):
        raise Refused("origin_not_released")
    if not task_types.read_eligible(service.ctx.workspace_dir(), row["task_type"], worker):
        raise Refused("task_type_not_allowed")
    granted = [canonical_wing(w) for w in grant_values(machine, "wings")]
    outside = Refused(
        "outside_worker_wing",
        f"board wing {board['wing']!r} is not in the machine's granted wings "
        + ", ".join(repr(w) for w in granted),
    )
    if canonical_wing(board["wing"]) not in granted:
        raise outside
    try:
        service._write(board, row=row, claiming=True)
    except ToolError:
        raise outside from None
    if not worker.policy.can_write_wing(board["wing"]):
        raise outside
