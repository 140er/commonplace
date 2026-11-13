"""The tools a bot may call, and what a tool may use while it runs.

A tool never acts with rights of its own: :class:`Turn` re-resolves the person for every call,
so whatever the person may not do over REST, the tool cannot do either. Free text a tool writes
must pass :meth:`Turn.words`, an exact-substring check against the person's stored message.
Kinds: ``read`` and ``routine`` run at once; ``confirm`` never runs from a tool call, the turn
stores a pending action and the person confirms it with a tap.
"""

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable

from commonplace import redact, sessions
from commonplace.boards.service import BoardService
from commonplace.bots import access, machines, store
from commonplace.bots.access import AudienceLimited, OutsideBot, ReadOnly
from commonplace.extensions import ToolContext
from commonplace.http_util import log_failure
from commonplace.rules import enforce

logger = logging.getLogger(__name__)

KINDS = ("read", "routine", "confirm")
SCOPES = ("any", "project")
NAME = re.compile(r"[a-z][a-z0-9_]{0,47}")
MIN_SPAN = 2  # a shorter span is in almost any message, so it proves nothing


class NotYourWords(Exception):
    """A text argument is not a verbatim span of the person's current message."""


class Gone(Exception):
    """The person no longer has access to this workspace (or the project)."""


class Invalid(ValueError):
    """Arguments do not match the tool's schema. The message names the field, never a value."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict  # JSON Schema object, additionalProperties false
    kind: str  # "read" | "routine" | "confirm"
    run: Callable[["Turn", dict], "Result"]
    label: Callable[["Turn", dict], str]  # plain line for action lines and confirm chips
    scope: str = "any"  # "project": only for bots with a board (General: a `project` argument)
    available: Callable[["Turn"], bool] | None = None
    # Raises NotYourWords (or a refusal) before anything is stored or labelled; for a confirm
    # tool it runs before the pending action is created and again before `run` on /confirm.
    check: Callable[["Turn", dict], None] | None = None
    # What the tool reaches, one of ``access.REACHES``; required (``register`` refuses without
    # it). A shared bot's access list decides whether the tool is offered and may run.
    reach: str | None = None
    # A routine tool's line for a chat chip, in the future tense ("Create a task: ..."). A routine
    # tool without one is never offered on a chat surface that confirms by tap.
    chip: Callable[["Turn", dict], str] | None = None


@dataclass
class Result:
    ok: bool
    data: Any = None
    blocks: tuple = ()
    say: str = ""  # plain refusal sentence; on success, an optional plain outcome sentence
    keep: tuple = ()  # our own opaque ids in ``data``: shown to the model as they are, not redacted


REGISTRY: dict[str, Tool] = {}
_lock = threading.Lock()


def register(tool: Tool) -> Tool:
    if NAME.fullmatch(tool.name) is None:
        raise ValueError("invalid tool name")
    if tool.kind not in KINDS or tool.scope not in SCOPES:
        raise ValueError(f"{tool.name}: invalid kind or scope")
    if tool.reach not in access.REACHES:
        raise ValueError(f"{tool.name}: `reach` is required, one of {', '.join(access.REACHES)}")
    if tool.reach.startswith("project:") and tool.scope != "project":
        raise ValueError(f"{tool.name}: a project reach needs scope `project`")
    if tool.scope == "project" and tool.reach not in ("project:read", "project:write", "machine"):
        raise ValueError(f"{tool.name}: a project tool reaches a project or a machine")
    schema = tool.schema
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ValueError(f"{tool.name}: schema must be a closed object")
    if tool.scope == "project" and "project" in schema.get("properties", {}):
        raise ValueError(f"{tool.name}: `project` is supplied by the turn")
    if tool.kind == "confirm" and tool.check is None and _free_text(schema):
        # Free text on a chip must be checked (your words) before the chip exists.
        raise ValueError(f"{tool.name}: a confirm tool with text arguments needs `check`")
    with _lock:
        if tool.name in REGISTRY and REGISTRY[tool.name] is not tool:
            raise ValueError(f"bot tool {tool.name} registered twice")
        REGISTRY[tool.name] = tool
    return tool


def _free_text(schema) -> bool:
    """True when the schema could accept a free string anywhere.

    Only an ``enum`` or ``const`` pins a value. A type list that names ``string``, any branch
    of ``anyOf`` / ``oneOf`` / ``allOf``, an untyped schema, and an open or typed
    ``additionalProperties`` of a nested object all count as free text.
    """
    if not isinstance(schema, dict):
        return schema is not False  # `true` (or anything unknown) accepts a string
    if "enum" in schema or "const" in schema:
        return False
    branches = [b for key in ("anyOf", "oneOf", "allOf") for b in schema.get(key) or ()]
    if any(_free_text(branch) for branch in branches):
        return True
    kind = schema.get("type")
    kinds = [kind] if isinstance(kind, str) else list(kind) if isinstance(kind, list) else []
    if "string" in kinds:
        return True
    if not kinds and not branches:
        return True  # no type at all: any value, a string included
    if any(_free_text(item) for item in (schema.get("properties") or {}).values()):
        return True
    extra = schema.get("additionalProperties", "object" in kinds)
    if "object" in kinds and extra is not False and _free_text(extra):
        return True
    return "array" in kinds and _free_text(schema.get("items", True))


class Turn:
    """What a tool may use: the person (fresh on every call), their boards and their message."""

    def __init__(
        self,
        gateway,
        workspace_id,
        user_id,
        bot,
        message,
        request_id,
        step=0,
        projects=None,
        thread_id=None,
        surface="web",
        disclosure="full",
        tap=False,
    ):
        self.gateway = gateway
        self.db = gateway.db
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.bot = bot
        self.message = message
        self.request_id = request_id
        self.step = step
        self.projects = projects  # readable project slugs offered to a bot without a board
        self.thread_id = thread_id  # the person's thread; None: the owner's original thread
        self.touched = set()  # projects whose content or name reached the model (audience re-check)
        self.surface = surface  # "web", or a chat platform's "dm" / "channel" (the bridge)
        self.disclosure = disclosure  # channel turns: "titles" or "full"
        self.tap = tap  # a direct message whose person may confirm writes by tapping in the chat

    def principal(self):
        principal = sessions.person_principal(self.db, self.user_id, self.workspace_id)
        if principal is None:
            raise Gone()
        return principal

    def boards(self) -> BoardService:
        return BoardService(ToolContext(principal=self.principal(), gateway=self.gateway))

    def audience(self) -> dict | None:
        """The readers of this turn's thread (``None`` unless it is a shared thread).

        :class:`Gone` when the person no longer reaches the thread. Fresh on every call.
        """
        if self.thread_id is None or not self.bot.get("shared"):
            return None
        try:
            return self.store().audience(self.bot["id"], self.user_id, self.thread_id)
        except store.Missing:
            raise Gone() from None

    def effective(self) -> access.Access:
        """What this turn may reach right now: the person's own reach for a bot that is not
        shared, else the bot's list ∩ the talker ∩ the owner, from fresh principals every call.
        In a shared thread also ∩ every person who reads it (the audience rule)."""
        talker = access.person(self.gateway, self.user_id, self.workspace_id)
        if talker is None:
            raise Gone()
        if not self.bot.get("shared"):
            return access.effective(self.gateway, self.bot, None, talker, None)
        owner_id = self.bot.get("owner_id")
        owner = (
            talker
            if owner_id == self.user_id
            else access.person(self.gateway, owner_id, self.workspace_id)
        )
        readers = None
        try:
            grants = self.store().access_get(self.bot["id"], self.user_id)["entries"]
            shared = self.audience()
            if shared is not None:
                readers = access.readers(self.gateway, shared["members"], self.workspace_id)
        except (store.Missing, Gone):
            grants = None  # no live share or sharing is off: nothing is reachable
        except Exception as exc:
            log_failure(logger, "Bot access list or audience unreadable", exc)
            grants = None  # an audience that cannot be checked reaches nothing
        return access.effective(self.gateway, self.bot, grants, talker, owner, readers)

    def readable(self) -> list[str]:
        return list(self.effective().projects)

    def board(self, args, effective=None) -> str:
        """The bot's project; for a bot without one, the validated ``project`` argument.

        A project the asker cannot read is "gone" (as for a missing one). One the asker can
        read but the bot's access does not cover is refused as outside the bot's access.
        """
        board = self.bot.get("board") or args.get("project")
        effective = effective or self.effective()
        if not isinstance(board, str) or not effective.can_read(board):
            if isinstance(board, str) and board in effective.limited:
                raise AudienceLimited()  # the asker may use it; someone else in the thread may not
            if isinstance(board, str) and effective.shared and board in effective.asker:
                raise OutsideBot("project")
            raise Gone()
        self.touched.add(board)
        return board

    def machines(self) -> list[dict]:
        """The machines this turn may use: what the person may use, and for a shared bot also
        what the list grants and the owner may use."""
        listed = machines.list_machines(self.gateway, self.principal())
        if not self.bot.get("shared"):
            return listed
        effective = self.effective()
        owner = access.person(self.gateway, self.bot.get("owner_id"), self.workspace_id)
        owned = {m["id"] for m in machines.list_machines(self.gateway, owner)} if owner else set()
        usable = [m for m in listed if m["id"] in owned and effective.machine_granted(m["id"])]
        for reader in effective.readers:  # a shared thread lists only what every reader may use
            seen = {m["id"] for m in machines.list_machines(self.gateway, reader)}
            usable = [m for m in usable if m["id"] in seen]
        return usable

    def words(self, text) -> str:
        """``text`` trimmed, when it is a verbatim span (2+ characters) of the stored message."""
        if not isinstance(text, str):
            raise NotYourWords()
        span = text.strip()
        if len(span) < MIN_SPAN or span not in self.message["text"]:
            raise NotYourWords()
        return span

    def audit(self, tool, decision) -> None:
        """The audit row a REST write gets: ids and the decision, never text."""
        try:
            principal = self.principal()
        except Gone:
            return
        enforce.audit(self.db, principal, f"bot:{tool}", decision, "transport=bot", transport="bot")

    def store(self) -> store.BotStore:
        return store.open_bots(self.gateway, self.workspace_id)


# Arguments


def _check(schema, value, path) -> None:
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise Invalid(f"{path or 'arguments'} must be an object")
        properties = schema.get("properties", {})
        extra = set(value) - set(properties)
        if extra and schema.get("additionalProperties", True) is False:
            raise Invalid(f"{path or 'arguments'} has unknown fields")
        for name in schema.get("required", ()):
            if name not in value:
                raise Invalid(f"{(path + '.' if path else '') + name} is required")
        for name, item in value.items():
            if name in properties:
                _check(properties[name], item, (path + "." if path else "") + name)
        return
    if kind == "string":
        ok = isinstance(value, str)
        if ok and not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 8000):
            raise Invalid(f"{path} has the wrong length")
    elif kind == "integer":
        ok = type(value) is int
        if ok and not schema.get("minimum", -(2**63)) <= value <= schema.get("maximum", 2**63 - 1):
            raise Invalid(f"{path} is out of range")
    elif kind == "boolean":
        ok = type(value) is bool
    elif kind == "array":
        ok = isinstance(value, list) and len(value) <= schema.get("maxItems", 50)
        if ok:
            for index, item in enumerate(value):
                _check(schema.get("items", {}), item, f"{path}[{index}]")
    else:
        raise Invalid(f"{path} has an unsupported type")
    if not ok:
        raise Invalid(f"{path} must be {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise Invalid(f"{path} is not one of the allowed values")


def validate(schema, args) -> dict:
    """``args`` when they match the schema (types, required, enum, length, no extra fields)."""
    _check(schema, args, "")
    return args


def schema_for(tool: Tool, turn: Turn) -> dict:
    """The schema the model sees: a bot without a board names the project for project tools."""
    if tool.scope != "project" or turn.bot.get("board"):
        return tool.schema
    properties = {
        "project": {
            "type": "string",
            "enum": list(turn.projects or ()),
            "description": "Which project",
        },
        **tool.schema.get("properties", {}),
    }
    return {
        **tool.schema,
        "properties": properties,
        "required": ["project", *tool.schema.get("required", ())],
    }


def _covered(turn, tool, effective) -> bool:
    """Whether a shared bot's effective access covers ``tool`` for some project it could act on."""
    slugs = [turn.bot["board"]] if turn.bot.get("board") else list(effective.projects)
    for project in slugs if tool.scope == "project" else [None]:
        try:
            access.check_tool(effective, tool, project)
        except (OutsideBot, ReadOnly):
            continue
        return True
    return False


def gate(turn, tool, args) -> None:
    """Refuse a call the turn's effective access does not cover; runs on every call, fresh.

    Raises :class:`Gone` (a project the asker cannot read), :class:`OutsideBot` or
    :class:`ReadOnly`. A bot that is not shared only gets the project check it always had.
    """
    effective = turn.effective()
    if effective.shared and tool.reach != "none" and not effective.tool_granted(tool.name):
        raise OutsideBot("other")
    project = turn.board(args, effective) if tool.scope == "project" else None
    access.check_tool(effective, tool, project)


def audience_gate(turn, tool, args) -> None:
    """Refuse, before the arguments are validated, a project that is outside the audience.

    A shared thread's model is only offered the projects every reader may use, so a call naming
    another one would fail validation as an invalid request; when the person could use it
    themselves the answer is the fixed ``audience_limited`` sentence instead.
    """
    project = args.get("project") if isinstance(args, dict) else None
    if tool.scope != "project" or turn.bot.get("board") or not isinstance(project, str):
        return
    if project in turn.effective().limited:
        raise AudienceLimited()


CHANNEL_TITLES = ("project_status", "list_work")  # titles, statuses, counts: no bodies


# The writing tools a chat tap may confirm. The three consequential ones (start an agent,
# approve, create a project) also need the owner's per-install switch; everything else that
# confirms (running a command, sharing or forgetting what a bot learned) stays web-only.
CHAT_TAP = frozenset(
    {
        "create_work",
        "comment_work",
        "tell_orchestrator",
        "prioritize_work",
        "answer_decision",
        "approve_work",
        "create_project",
        "start_work",
        "stop_work",
        "remember",
        "forget",
    }
)
CHAT_CONSEQUENTIAL = frozenset({"start_work", "approve_work", "create_project"})


def surface_allows(turn, tool) -> bool:
    """Whether the turn's surface may use ``tool``. The web is unrestricted. A chat platform's
    direct message gets no routine write unless its person confirms by tap (``turn.tap``): then
    the routine writes are offered as confirm tools, never run unconfirmed; the other confirm
    tools leave their chip in the person's web thread. A channel turn is read-only and only for
    the mapped project: with ``titles`` just its status and work titles, never bodies."""
    if turn.surface == "web":
        return True
    if turn.surface == "dm":
        return tool.kind != "routine" or (
            turn.tap and tool.name in CHAT_TAP and tool.chip is not None
        )
    if tool.kind != "read" or tool.scope != "project" or not turn.bot.get("board"):
        return False
    return turn.disclosure == "full" or tool.name in CHANNEL_TITLES


def effective_kind(turn, tool) -> str:
    """A chat direct message confirms what the web files as routine: nothing writes with the
    person's identity there without their tap."""
    return "confirm" if tool.kind == "routine" and turn.surface == "dm" else tool.kind


def check_words(turn, args) -> None:
    """A text argument of a routine write that became a chip must be the person's own words."""
    if "text" in args:
        turn.words(args["text"])


def offered(turn: Turn) -> list[Tool]:
    """The tools this turn may call, in registration order."""
    out = []
    effective = turn.effective()
    for tool in list(REGISTRY.values()):
        if not surface_allows(turn, tool):
            continue
        if tool.scope == "project" and not turn.bot.get("board") and not turn.projects:
            continue
        if effective.shared and not _covered(turn, tool, effective):
            continue
        if tool.available is not None:
            try:
                if not tool.available(turn):
                    continue
            except Exception:
                continue
        out.append(tool)
    return out


# Built-in tools: the bot's own memory


def _snippet(text, limit=120) -> str:
    text = " ".join(redact.redact(text or "")[0].split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _remember(turn, args):
    text = turn.words(args["text"])
    try:
        note = turn.store().memory_add(turn.bot["id"], turn.user_id, text)
    except store.Invalid:
        return Result(False, say="That note is too long to remember.")
    return Result(True, {"id": note["id"]})


def _forget(turn, args):
    if not turn.store().memory_remove(turn.bot["id"], turn.user_id, args["id"], own=True):
        return Result(False, say="There is no such note.")
    return Result(True, {"id": args["id"]})


register(
    Tool(
        name="remember",
        description=(
            "Save a note to this bot's memory. `text` must be copied verbatim from the person's "
            "current message. Only when the person asks you to remember something."
        ),
        schema={
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 2000}},
            "required": ["text"],
            "additionalProperties": False,
        },
        kind="routine",
        reach="none",
        run=_remember,
        label=lambda turn, args: "Remembered a note",
        chip=lambda turn, args: f"Remember this note: “{_snippet(turn.words(args['text']))}”",
    )
)
register(
    Tool(
        name="forget",
        description="Remove one note from this bot's memory, by its id.",
        schema={
            "type": "object",
            "properties": {"id": {"type": "integer", "minimum": 1}},
            "required": ["id"],
            "additionalProperties": False,
        },
        kind="routine",
        reach="none",
        run=_forget,
        label=lambda turn, args: "Forgot a note",
        chip=lambda turn, args: f"Forget memory note #{args['id']}",
    )
)


def _my_access(turn, args):
    effective = turn.effective()
    if not effective.shared:
        return Result(
            True, {"shared": False, "note": "This bot acts with the person's own access."}
        )
    asked = args.get("project")
    if asked is not None:
        if effective.can_read(asked):
            mode = "write" if effective.can_write(asked) else "read"
            return Result(True, {"project": asked, "reach": mode})
        reason = (
            access.SENTENCES["audience_limited"]
            if asked in effective.limited
            else access.SENTENCES["outside_bot_project"]
            if asked in effective.asker
            else "I can't find that."
        )
        return Result(True, {"project": asked, "reach": "none", "reason": reason})
    return Result(
        True,
        {
            "shared": True,
            "projects": [
                {"project": slug, "write": effective.can_write(slug)} for slug in effective.projects
            ],
            "machines": [m["label"] for m in turn.machines()],
            "tools": sorted(effective.tools & set(REGISTRY)),
            "note": "Only what the bot's owner granted, within this person's own access.",
        },
    )


register(
    Tool(
        name="my_access",
        description=(
            "Say what this bot may reach for this person (projects, machines, tools), or, with "
            "`project`, whether it may use that one and why not."
        ),
        schema={
            "type": "object",
            "properties": {"project": {"type": "string", "minLength": 1, "maxLength": 200}},
            "required": [],
            "additionalProperties": False,
        },
        kind="read",
        reach="none",
        available=lambda turn: bool(turn.bot.get("shared")),
        run=_my_access,
        label=lambda turn, args: "Looked at what I may use",
    )
)
