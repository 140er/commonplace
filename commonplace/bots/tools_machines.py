"""Bot tools for machines: list them, start an agent on one, cancel a start that has not launched.

Starting and stopping are confirm tools: the model's call only creates a pending action with a
server-built label, and the person's tap runs exactly that. Everything goes through
``bots.machines``, so the runner's own authority checks decide; a refusal comes back as a plain
sentence. Running commands is only offered while some machine reports that it can.
"""

import logging

from commonplace.bots import access, blocks, machines
from commonplace.bots.tools import OutsideBot, Result, Tool, register
from commonplace.extensions import ToolError
from commonplace.redact import redact

LOG = logging.getLogger(__name__)
NOT_VERIFIED = "Attested by the workspace owner, not verified by Commonplace."
IN_FLIGHT = frozenset(("queued", "leased", "starting"))
LISTED = 8  # machines handed to the model; the block shows them all
REF = {"type": "integer", "minimum": 1, "description": "The task's number"}
MACHINE = {
    "type": "string",
    "minLength": 1,
    "maxLength": 80,
    "description": "A machine's name from list_machines; leave out when only one can be used",
}


def _clean(text, limit=80) -> str:
    return redact(text)[0][:limit]


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
    """The listed machine's own name; a machine the person may not use is refused up front.

    A shared bot lists only the machines its owner granted (and the owner may use): a machine
    the person may use but the bot may not is refused as outside the bot's access.
    """
    for machine in turn.machines():
        if ref in (machine["id"], machine["label"]):
            return machine["label"]
    if turn.bot.get("shared"):
        for machine in machines.list_machines(turn.gateway, turn.principal()):
            if ref in (machine["id"], machine["label"]):
                raise OutsideBot("other")
    raise ToolError(machines.SENTENCES["machine_not_found"])


def _machine_arg(turn, args):
    """The machine to start on: as named, or for a shared bot the one granted machine.

    A shared bot never lets the runner pick among machines it was not granted: with none named,
    exactly one granted machine that can start an agent is used; none is refused as outside the
    bot's access, several ask which one. A bot that is not shared passes the argument through.
    """
    named = args.get("machine")
    if named:
        _machine_name(turn, named)
        return named
    if not turn.bot.get("shared"):
        return None
    own = [m for m in machines.list_machines(turn.gateway, turn.principal())]
    if not any(m["capabilities"]["start_agent"] for m in own):
        return None  # the runner says why this person cannot start anything
    capable = [m for m in turn.machines() if m["capabilities"]["start_agent"]]
    if not capable:
        raise OutsideBot("other")
    if len(capable) > 1:
        labels = ", ".join(m["label"] for m in capable)
        raise ToolError(f"More than one machine can take this task; say which one: {labels}.")
    return capable[0]["id"]


# list_machines


def _brief(machine) -> dict:
    """One machine for the model: flat and short (a tool result is cut at 4,000 characters)."""
    return {
        "id": machine["id"],
        "label": machine["label"],
        "trust": machine["trust"],
        "can_start_agent": machine["capabilities"]["start_agent"],
        "can_run_command": machine["capabilities"]["run_command"],
        "projects": machine["grants"]["projects"][:8],
        "topics": machine["grants"]["topics"][:8],
        "wings": machine["grants"]["wings"][:8],
        "max_runtime_mode": machine["grants"]["max_runtime_mode"],
        "last_seen": machine["last_seen"],
    }


def _list(turn, args):
    principal = turn.principal()
    listed = turn.machines()
    data = {"machines": [_brief(m) for m in listed[:LISTED]], "note": NOT_VERIFIED}
    if len(listed) > LISTED:
        data["not_shown"] = len(listed) - LISTED
    if not listed:
        data["reason"] = machines.why_none(turn.gateway, principal)
        if turn.bot.get("shared") and machines.list_machines(turn.gateway, principal):
            data["reason"] = access.SENTENCES["outside_bot_other"]
    block = {"type": "machines"}
    if turn.bot.get("shared"):
        block["ids"] = [m["id"] for m in listed]  # the block shows only what the bot may use
    return Result(True, data, blocks=(block,))


def hydrate_machines(reader, stored) -> dict:
    listed = machines.list_machines(reader.gateway, reader.principal)
    if "ids" in stored:
        listed = [m for m in listed if m["id"] in stored["ids"]]
    return {**stored, "machines": listed}


# start_work


def _start_check(turn, args) -> None:
    """Refused before any chip exists: the task is not this project's, the machine is unknown,
    or the start itself would be refused (not an owner, the task's state, the machine's area)."""
    _task(turn, args)
    machine = _machine_arg(turn, args)
    try:
        machines.preflight(turn.gateway, turn.principal(), args["ref"], machine)
    except machines.Refused as exc:
        raise ToolError(exc.text) from None


def _start_label(turn, args) -> str:
    label = f"Start an agent on “{_clean(_task(turn, args)['title'])}”"
    if args.get("machine"):
        label += f" on {_clean(_machine_name(turn, args['machine']))}"
    return label


def _start(turn, args):
    try:
        started = machines.start_agent(
            turn.gateway,
            turn.principal(),
            args["ref"],
            machine=_machine_arg(turn, args),
            request_key=f"{turn.request_id}:{turn.step}",
        )
    except machines.Refused as exc:
        return Result(False, say=exc.text)
    return Result(True, started, blocks=({"type": "work", "card": args["ref"]},))


# stop_work


def _stop_label(turn, args) -> str:
    return f"Cancel the agent start on “{_clean(_task(turn, args)['title'])}”"


def _live(turn, args):
    """The starts that can still be cancelled, or the plain sentence for why there are none."""
    rows = machines.start_status(turn.gateway, turn.principal(), args["ref"])
    live = [row for row in rows if row["state"] in IN_FLIGHT]
    if live:
        return live, ""
    if any(row["state"] == "started" for row in rows):
        return [], machines.SENTENCES["already_finished"]
    if any(row["state"] == "uncertain" for row in rows):
        return [], machines.SENTENCES["uncertain"]
    return [], "No agent start is waiting on that task, so there is nothing to cancel."


def _stop_check(turn, args) -> None:
    _task(turn, args)
    live, refusal = _live(turn, args)
    if not live:
        raise ToolError(refusal)


def _stop(turn, args):
    live, refusal = _live(turn, args)
    if not live:
        return Result(False, say=refusal)
    principal = turn.principal()
    cancelled, requested = [], []
    shown = ({"type": "work", "card": args["ref"]},)
    for row in live:
        try:
            state = machines.cancel_start(turn.gateway, principal, row["job"])["state"]
        except machines.Refused as exc:
            if not (cancelled or requested):
                return Result(False, say=exc.text)
            done = len(cancelled) + len(requested)
            return Result(
                False,
                {"cancelled": cancelled, "cancel_requested": requested},
                blocks=shown,
                say=f"I cancelled {done} of {len(live)} starts, but not the rest: {exc.text}",
            )
        (cancelled if state == "cancelled" else requested).append(row["job"])
    return Result(True, {"cancelled": cancelled, "cancel_requested": requested}, blocks=shown)


# run_command: only offered while some machine can run commands


def _can_run(turn) -> bool:
    return any(m["capabilities"]["run_command"] for m in turn.machines())


def _run_check(turn, args) -> None:
    turn.words(args["command"])
    if not _can_run(turn):
        raise ToolError(machines.SENTENCES["unsupported"])
    _machine_name(turn, args["machine"])


def _run_label(turn, args) -> str:
    command = _clean(turn.words(args["command"]), 200)
    return f"Run a command on {_clean(_machine_name(turn, args['machine']))}: {command}"


def _run(turn, args):
    command = turn.words(args["command"])
    try:
        name = _machine_name(turn, args["machine"])
        outcome = machines.run_command(turn.gateway, turn.principal(), name, command)
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
            reach="machine",
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
            reach="machine",
            description=(
                "Start an agent on a machine for one task. The person has to tap to confirm; say "
                "that it is waiting for their tap, never that it started. Name a machine only when "
                "more than one can be used."
            ),
            schema=_schema({"ref": REF, "machine": MACHINE}, ["ref"]),
            kind="confirm",
            run=_start,
            check=_start_check,
            label=_start_label,
            scope="project",
        )
    )
    register(
        Tool(
            name="stop_work",
            reach="machine",
            description=(
                "Cancel an agent start that has not launched yet. An agent that is already running "
                "cannot be stopped from here. The person has to tap to confirm."
            ),
            schema=_schema({"ref": REF}, ["ref"]),
            kind="confirm",
            run=_stop,
            check=_stop_check,
            label=_stop_label,
            scope="project",
        )
    )
    register(
        Tool(
            name="run_command",
            reach="machine",
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
            check=_run_check,
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
        except Exception as exc:  # the block stays as the core hydrator made it
            LOG.warning("work block start lookup failed: %s", type(exc).__name__)
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
