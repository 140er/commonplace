"""Which agents a person may give a new card to, and whether that starts a run.

The Add card form lists these next to the workspace's people. The list is the agents the
person's console shows (the owner sees every agent, a member only their own), narrowed to the
connections the board accepts as an assignee, and each carries what assigning it does: a run
starts only when the person is the workspace owner, the board has a configured Coworker and
the agent is that Coworker's worker. Every other agent is only handed the card.
"""

import logging

from commonplace import redact
from commonplace.console import authority
from commonplace.extensions import ToolError
from commonplace.http_util import log_failure

logger = logging.getLogger(__name__)


def _worker_actor(service, slug):
    """The actor a person's assignment starts on this board, or None when none does."""
    from commonplace.orch import runner_bridge
    from commonplace.projects import orchestration
    from commonplace.runner import registry
    from commonplace.runner.predicate import requester_ok

    principal, gateway, ws = service.principal, service.ctx.gateway, service.principal.workspace_id
    try:
        if (
            principal.role != "owner"
            or not orchestration.enabled(gateway.db, ws)
            or not runner_bridge.configured(gateway, ws, slug)
        ):
            return None
        config = runner_bridge.resolve_version(gateway, ws, slug).config()
        machine = registry.get_machine(gateway.db, ws, config.runner.machine)
        worker = (
            gateway.db.authenticate_connection(machine["worker_connection_id"])
            if machine is not None
            else None
        )
        return (
            worker.actor
            if worker is not None and requester_ok(gateway, principal, machine)
            else None
        )
    except Exception as exc:
        log_failure(logger, "assignee start lookup failed", exc)
        return None


def board_assignees(service, board):
    principal = service.principal
    if not principal.person:
        raise ToolError("no: only a person lists who a card can go to")
    with service.store.connection() as conn:
        row = service._board(conn, board)
    try:
        service._write(row, submission=True)
    except ToolError:
        return {"board": row["slug"], "agents": []}
    starts = _worker_actor(service, row["slug"])
    agents = []
    for view in authority.agents(service.ctx.gateway, principal):
        if view.parent_id is not None:
            continue
        agent = service.ctx.db.authenticate_connection(view.id)
        if (
            agent is None
            or not agent.policy.has_scope("memory:write")
            or not agent.policy.can_read_wing(row["wing"])
        ):
            continue
        agents.append(
            {
                "ref": agent.actor,
                "name": redact.redact(view.name)[0],
                "starts_run": agent.actor == starts,
            }
        )
    return {"board": row["slug"], "agents": agents}
