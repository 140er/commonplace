"""Bot tools for machines: list them, start an agent on one, cancel a start that has not launched.

Starting and stopping are confirm tools: the model's call only creates a pending action with a
server-built label, and the person's tap runs exactly that. Everything goes through
``bots.machines``, so the runner's own authority checks decide; a refusal comes back as a plain
sentence. Running commands is only offered while some machine reports that it can.
"""

from commonplace import redact
from commonplace.bots import blocks, machines
from commonplace.bots.tools import Result, Tool, register
from commonplace.extensions import ToolError

NOT_VERIFIED = "Attested by the workspace owner, not verified by Commonplace."
IN_FLIGHT = frozenset(("queued", "leased", "starting"))
REF = {"type": "integer", "minimum": 1, "description": "The task's number"}
MACHINE = {
    "type": "string",
    "minLength": 1,
    "maxLength": 80,
    "description": "A machine's name from list_machines; leave out when only one can be used",
}


def _clean(text, limit=80) -> str:
    return redact.redact(text)[0][:limit]


def _task(turn, args) -> dict:
    """The task as this person sees it, and only when it belongs to the bot's own project."""
    board = turn.board(args)
    try:
        card = turn.boards().task_show(args["ref"])
    except ToolError:
        raise ToolError("I can't find that task.") from None
    if card["board"] != board:
        raise ToolError("That task is not in this project.")
    return card


def _machine_name(turn, ref) -> str:
    """The listed machine's own name; a machine the person may not use is refused up front."""
    for machine in machines.list_machines(turn.gateway, turn.principal()):
        if ref in (machine["id"], machine["label"]):
            return machine["label"]
    raise ToolError(machines.SENTENCES["machine_not_found"])


# list_machines


def _list(turn, args):
    principal = turn.principal()
    listed = machines.list_machines(turn.gateway, principal)
    data = {"machines": listed, "note": NOT_VERIFIED}
    if not listed:
        data["reason"] = machines.why_none(turn.gateway, principal)
    return Result(True, data, blocks=({"type": "machines"},))


def hydrate_machines(reader, stored) -> dict:
    return {**stored, "machines": machines.list_machines(reader.gateway, reader.principal)}


# start_work


def _start_label(turn, args) -> str:
    label = f"Start an agent on “{_clean(_task(turn, args)['title'])}”"
    if args.get("machine"):
        label += f" on {_clean(_machine_name(turn, args['machine']))}"
    return label


def _start(turn, args):
    _task(turn, args)
    try:
        started = machines.start_agent(
            turn.gateway,
            turn.principal(),
            args["ref"],
            machine=args.get("machine"),
            request_key=f"{turn.request_id}:{turn.step}",
        )
    except machines.Refused as exc:
        return Result(False, say=exc.text)
    return Result(True, started, blocks=({"type": "work", "card": args["ref"]},))


# stop_work


def _stop_label(turn, args) -> str:
    return f"Cancel the agent start on “{_clean(_task(turn, args)['title'])}”"


def _stop(turn, args):
    _task(turn, args)
    principal = turn.principal()
    rows = machines.start_status(turn.gateway, principal, args["ref"])
    live = [row for row in rows if row["state"] in IN_FLIGHT]
    if not live:
        if any(row["state"] == "started" for row in rows):
            return Result(False, say=machines.SENTENCES["already_finished"])
        if any(row["state"] == "uncertain" for row in rows):
            return Result(False, say=machines.SENTENCES["uncertain"])
        return Result(
            False, say="No agent start is waiting on that task, so there is nothing to cancel."
        )
    cancelled, requested = [], []
    try:
        for row in live:
            state = machines.cancel_start(turn.gateway, principal, row["job"])["state"]
            (cancelled if state == "cancelled" else requested).append(row["job"])
    except machines.Refused as exc:
        return Result(False, say=exc.text)
    return Result(
        True,
        {"cancelled": cancelled, "cancel_requested": requested},
        blocks=({"type": "work", "card": args["ref"]},),
    )


# run_command: only offered while some machine can run commands


def _can_run(turn) -> bool:
    return machines.can_run_command(turn.gateway, turn.principal())


def _run_label(turn, args) -> str:
    command = _clean(turn.words(args["command"]), 200)
    return f"Run a command on {_clean(_machine_name(turn, args['machine']))}: {command}"


def _run(turn, args):
    command = turn.words(args["command"])
    try:
        outcome = machines.run_command(turn.gateway, turn.principal(), args["machine"], command)
    except (machines.Refused, machines.Unsupported) as exc:
        return Result(False, say=exc.text)
    return Result(True, outcome)


def _schema(properties, required) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _register() -> None:
    register(
        Tool(
            name="list_machines",
            description=(
                "List the machines this person may use, what each may do and what the owner "
                "granted it. Machines are attested by the owner, never verified. When the list is "
                "empty, `reason` says why."
            ),
            schema=_schema({}, []),
            kind="read",
            run=_list,
            label=lambda turn, args: "Looked at the machines",
        )
    )
    register(
        Tool(
            name="start_work",
            description=(
                "Start an agent on a machine for one task. The person has to tap to confirm; say "
                "that it is waiting for their tap, never that it started. Name a machine only when "
                "more than one can be used."
            ),
            schema=_schema({"ref": REF, "machine": MACHINE}, ["ref"]),
            kind="confirm",
            run=_start,
            label=_start_label,
            scope="project",
        )
    )
    register(
        Tool(
            name="stop_work",
            description=(
                "Cancel an agent start that has not launched yet. An agent that is already running "
                "cannot be stopped from here. The person has to tap to confirm."
            ),
            schema=_schema({"ref": REF}, ["ref"]),
            kind="confirm",
            run=_stop,
            label=_stop_label,
            scope="project",
        )
    )
    register(
        Tool(
            name="run_command",
            description=(
                "Run a command on a machine. `command` must be copied verbatim from the person's "
                "current message. The person has to tap to confirm."
            ),
            schema=_schema(
                {
                    "machine": {**MACHINE, "description": "A machine's name from list_machines"},
                    "command": {"type": "string", "minLength": 1, "maxLength": 2000},
                },
                ["machine", "command"],
            ),
            kind="confirm",
            run=_run,
            label=_run_label,
            available=_can_run,
        )
    )


def _status_aware(core):
    """The work block reads ``working`` while an agent start for the item is in flight."""

    def work(reader, stored):
        shown = core(reader, stored)
        if shown.get("gone") or shown.get("status") != "queued":
            return shown
        try:
            rows = machines.start_status(reader.gateway, reader.principal, stored["card"])
        except Exception:
            return shown
        if any(row["state"] in IN_FLIGHT for row in rows):
            return {**shown, "status": "working"}
        return shown

    work.core = core
    return work


_register()
blocks.HYDRATORS["machines"] = hydrate_machines
if not hasattr(blocks.HYDRATORS["work"], "core"):
    blocks.HYDRATORS["work"] = _status_aware(blocks.HYDRATORS["work"])
