"""Project tools: what a bot calls so its person can run a project in plain language.

Every tool acts through the board services as the person (a fresh principal per call), so what
the person may not do the tool cannot do either. Text a tool writes (a title, body, comment,
forwarded message, decision reply or project name) must be a verbatim span of the person's
current message (``Turn.words``). The engine audits routine and confirm tools; a service
refusal comes back as one plain sentence with the next step, never the service's own text.
"""

import functools
import re
from collections import Counter

from commonplace import redact
from commonplace.boards import priority as priorities
from commonplace.boards.service import (
    SpaceError,
    SpaceExists,
    SpaceForbidden,
    SpaceMissing,
    VersionClash,
)
from commonplace.bots import blocks
from commonplace.bots.tools import Result, Tool, register
from commonplace.chat import front
from commonplace.extensions import ToolError

LIST_LIMIT = 20
TITLE_LIMIT = 200
NOTES_SHOWN = 3
NOTE_CHARS = 500
STATUSES = ("queued", "working", "needs_you", "ready", "blocked", "done")

NOT_FOUND = "I cannot find that work in this project. Ask me to list the work to see what exists."
QUESTION_NOT_FOUND = (
    "I cannot find that question in this project. Ask me to list your questions first."
)
CHANGED = "That changed since you were asked. Ask me again to see where it stands now."
REFUSED = (
    "That was not done. You may not have permission for it here, or the work is in a state that "
    "does not allow it. Ask the project owner if you need it."
)
OWNER_ONLY = "Only the project owner can do that."
PRIORITY_OWNER_ONLY = "Only the project owner can set a priority."
BAD_POSITION = "Put it before or after another open task in the same column, not both."
BAD_NEIGHBOUR = "That position is not possible. Pick another task in the same column."
NAME_TAKEN = "There is already a project with a similar name. Pick another name."
NAME_EMPTY = "Give the project a name with at least one letter or number."
CREATE_DENIED = "You are not allowed to create projects here. Ask the workspace owner."
NOTHING_CHANGED = "Say what to change: a priority, or a position before or after another task."
CHOICE_OR_REPLY = "Give either one of the offered answers or your own reply, not both."
NOT_AN_OPTION = "That is not one of the offered answers. Ask me to list your questions again."
NOT_OPEN = "That question is not open any more."

WAIT_TEXT = {
    "blocked_by": "wait for other work to finish",
    "waits_on_person": "wait for a person to answer",
    "claim_expired": "have an agent that stopped reporting",
    "plan_review": "wait for plan review",
    "not_released": "wait for release",
    "no_agent": "cannot start because no agent is connected",
    "project_limit": "wait for a free agent slot",
    "checks_pending": "wait for checks to finish",
}


class Plain(ToolError):
    """A refusal that is already one plain sentence with the next step."""


def _plain(exc) -> str:
    """One plain sentence for a service refusal; the service's own text never leaves here."""
    if isinstance(exc, Plain):
        return str(exc)
    if isinstance(exc, VersionClash):
        return CHANGED
    text = str(exc).lower()
    if "not found" in text:
        return NOT_FOUND
    if "priority" in text:
        return PRIORITY_OWNER_ONLY
    if "position target" in text:
        return BAD_NEIGHBOUR
    if "before or after" in text:
        return BAD_POSITION
    if "owner" in text:
        return OWNER_ONLY
    return REFUSED


def _guarded(run):
    """Turn a refusal raised inside a tool into ``Result(False, say=<plain sentence>)``."""

    @functools.wraps(run)
    def guarded(turn, args):
        try:
            return run(turn, args)
        except ToolError as exc:
            return Result(False, say=_plain(exc))

    return guarded


def _clean(text) -> str:
    return redact.redact(text or "")[0]


def _clip(text, limit) -> str:
    text = " ".join(_clean(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _status(card) -> tuple[str, str]:
    try:
        return blocks.work_status(card)
    except (KeyError, ValueError):
        return "queued", ""


def _item(turn, board, ref, *, question=False) -> dict:
    """The work item as this person sees it, or a plain "not found": another project's item and
    one the person cannot see read the same, and nothing of its text is repeated."""
    try:
        card = turn.boards().task_show(ref)
    except ToolError:
        card = None
    if card is None or card.get("board") != board:
        raise Plain(QUESTION_NOT_FOUND if question else NOT_FOUND)
    return card


def _work_block(number) -> dict:
    return {"type": "work", "card": number}


def _first_line(text) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return text.strip()


def _orchestrator(turn, board) -> dict:
    """Whether the project's orchestrator is connected and would be woken, in plain words."""
    try:
        view = front.orchestrator(turn.gateway, turn.principal(), board)
    except (ToolError, LookupError):
        view = None
    if view is None:
        return {
            "state": "none",
            "wakeable": False,
            "note": "No orchestrator is set for this project.",
        }
    if view.connection_id is None:
        return {
            "state": "disconnected",
            "wakeable": False,
            "note": "The orchestrator is not connected right now.",
        }
    if view.wakeable:
        return {
            "state": "connected",
            "wakeable": True,
            "note": "The orchestrator is connected and is woken for new messages.",
        }
    return {
        "state": "connected",
        "wakeable": False,
        "note": "The orchestrator is connected but is not woken: it sees this when it next checks.",
    }


def _items(turn, board) -> list[dict]:
    return turn.boards().task_list(board=board, all_statuses=True, top_level=True)


# Reading


@_guarded
def _project_status(turn, args):
    board = turn.board(args)
    service = turn.boards()
    entry = next((b for b in service.board_list() if b["slug"] == board), None)
    if entry is None:
        return Result(False, say=NOT_FOUND)
    counts = Counter({status: 0 for status in STATUSES})
    working = []
    for card in _items(turn, board):
        status = _status(card)[0]
        counts[status] += 1
        if status == "working" and len(working) < 5:
            working.append({"number": card["number"], "title": _clip(card["title"], 120)})
    waiting = [
        f"{note['count']} task{'' if note['count'] == 1 else 's'} {WAIT_TEXT[note['code']]}"
        for note in entry.get("waiting_notes", ())
        if note.get("code") in WAIT_TEXT
    ]
    return Result(
        True,
        {
            "project": _clip(entry["name"], 120),
            "tasks": dict(counts),
            "agents_working": entry.get("agents_working", 0),
            "working_on": working,
            "waiting": waiting,
            "orchestrator": _orchestrator(turn, board),
        },
    )


@_guarded
def _list_work(turn, args):
    board = turn.board(args)
    wanted = args.get("status")
    shown, total = [], 0
    for card in _items(turn, board):
        status = _status(card)[0]
        if (status != wanted) if wanted else (status == "done"):
            continue
        total += 1
        if len(shown) < LIST_LIMIT:
            shown.append(
                {"number": card["number"], "title": _clip(card["title"], 200), "status": status}
            )
    return Result(
        True,
        {"work": shown, "more": total > len(shown)},
        blocks=tuple(_work_block(item["number"]) for item in shown),
    )


@_guarded
def _work_detail(turn, args):
    board = turn.board(args)
    card = _item(turn, board, args["ref"])
    status, note = _status(card)
    comments = [e for e in card.get("events", ()) if e.get("kind") == "comment" and e.get("text")]
    links = [{"label": "Pull request", "url": card["pr_url"]}] if card.get("pr_url") else []
    return Result(
        True,
        {
            "number": card["number"],
            "title": _clip(card["title"], 200),
            "status": status,
            "priority": card.get("priority"),
            "note": _clip(note or card.get("status_line"), NOTE_CHARS),
            "latest_notes": [
                {"text": _clip(e["text"], NOTE_CHARS), "at": e["at"]}
                for e in comments[-NOTES_SHOWN:]
            ],
            "links": links,
        },
        blocks=(_work_block(card["number"]),),
    )


@_guarded
def _list_decisions(turn, args):
    board = turn.board(args)
    try:
        asks = turn.boards().needs_me()["asks"]
    except ToolError:
        asks = []  # a member without the needs-me tool has none waiting
    reader = blocks.Reader(turn.gateway, turn.principal())
    questions, refs = [], []
    for ask in asks:
        if ask.get("board") != board or len(questions) >= LIST_LIMIT:
            continue
        stored = {"type": "decision", "card": ask["number"]}
        view = blocks.decision(reader, stored)
        if view.get("gone") or not view["open"]:
            continue
        refs.append(stored)
        questions.append(
            {
                "ref": ask["number"],
                "question": _clip(view["question"], 300),
                "options": [_clip(option, 200) for option in view["options"]],
            }
        )
    return Result(True, {"questions": questions}, blocks=tuple(refs))


# Filing and steering


def _filing(turn, args) -> tuple[str, str]:
    """``(title, body)``: the person's own words. Without arguments, the whole message."""
    body = turn.words(args["body"]) if "body" in args else turn.message["text"]
    title = turn.words(args["title"]) if "title" in args else _first_line(body)[:TITLE_LIMIT]
    return title, body


def _create_work_label(turn, args):
    title, _ = _filing(turn, args)
    return f"Filed “{_clip(title, 80)}”"


@_guarded
def _create_work(turn, args):
    board = turn.board(args)
    title, body = _filing(turn, args)
    extra, note = {}, None
    if args.get("priority") is not None:
        try:
            priorities.owner_value(turn.principal(), args["priority"])
            extra["priority"] = args["priority"]
        except ToolError:
            note = "Filed without the priority: only the project owner can set it."
    card = turn.boards().task_add(title=title, body=body, board=board, status="todo", **extra)
    turn.store().work_add(turn.bot["id"], turn.user_id, board, card["number"], turn.message["id"])
    data = {"number": card["number"], "title": _clip(card["title"], 200)}
    data["status"] = _status(card)[0]
    if note:
        data["note"] = note
    return Result(True, data, blocks=(_work_block(card["number"]),))


@_guarded
def _prioritize_work(turn, args):
    board = turn.board(args)
    fields = {key: args[key] for key in ("priority", "before", "after") if key in args}
    if not fields:
        return Result(False, say=NOTHING_CHANGED)
    if "before" in fields and "after" in fields:
        return Result(False, say=BAD_POSITION)
    card = _item(turn, board, args["ref"])
    updated = turn.boards().task_update(args["ref"], version=card["version"], **fields)
    return Result(
        True,
        {"number": card["number"], "priority": updated.get("priority", card.get("priority"))},
        blocks=(_work_block(card["number"]),),
    )


@_guarded
def _comment_work(turn, args):
    board = turn.board(args)
    text = turn.words(args["text"]) if "text" in args else turn.message["text"]
    card = _item(turn, board, args["ref"])
    turn.boards().task_comment(card["number"], text)
    return Result(True, {"number": card["number"]}, blocks=(_work_block(card["number"]),))


@_guarded
def _tell_orchestrator(turn, args):
    board = turn.board(args)
    text = turn.words(args["text"]) if "text" in args else turn.message["text"]
    with front.skipping():  # the person already talks to this bot: no receipt, no fallback card
        posted = turn.boards().space_post(board, text)
    turn.store().forward_add(turn.bot["id"], turn.user_id, board, posted["id"], turn.message["id"])
    state = _orchestrator(turn, board)
    return Result(True, {"passed_on": True, **state})


# Answering and approving (confirm tools: the label is built, and checked, before the tap)


def _decision(turn, args, version=None):
    """``(card, choice, reply)`` for an answer that is still valid; plain refusals otherwise."""
    card = _item(turn, turn.board(args), args["ref"], question=True)
    if version is not None and card["version"] != version:
        raise Plain(CHANGED)
    if card.get("status") != "review" or card.get("review_kind") != "decision":
        raise Plain(NOT_OPEN)
    choice, reply = args.get("choice"), args.get("reply")
    if (choice is None) == (reply is None):
        raise Plain(CHOICE_OR_REPLY)
    if choice is not None and choice not in card["ask_options"]:
        raise Plain(NOT_AN_OPTION)
    return card, choice, turn.words(reply) if reply is not None else None


def _answer_decision_label(turn, args):
    card, choice, reply = _decision(turn, args)
    question = card["ask"] or card["title"]
    return f"Answer “{_clip(choice or reply, 80)}” to “{_clip(question, 100)}”"


@_guarded
def _answer_decision(turn, args):
    version = args.get("version")
    if version is None:
        raise Plain(CHANGED)
    _, choice, reply = _decision(turn, args, version)
    turn.boards().decide(args["ref"], choice=choice, reply=reply, version=version)
    return Result(True, {"answered": True}, blocks=({"type": "decision", "card": args["ref"]},))


def _approve_work_label(turn, args):
    card = _item(turn, turn.board(args), args["ref"])
    verb = "Approve" if args["approve"] else "Decline"
    return f"{verb} “{_clip(card['title'], 100)}”"


@_guarded
def _approve_work(turn, args):
    board = turn.board(args)
    card = _item(turn, board, args["ref"])
    version = args.get("version")
    if version is None or card["version"] != version:
        raise Plain(CHANGED)
    turn.boards().task_approve(card["number"], approve=args["approve"], version=version)
    return Result(True, {"approved": args["approve"]}, blocks=(_work_block(card["number"]),))


# Projects


def _slug(name) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60].strip("-")


def _create_project_label(turn, args):
    name = turn.words(args["name"])
    if not _slug(name):
        raise Plain(NAME_EMPTY)
    return f"Create a new project “{_clip(name, 80)}”"


@_guarded
def _create_project(turn, args):
    name = turn.words(args["name"])
    slug = _slug(name)
    if not slug:
        raise Plain(NAME_EMPTY)
    try:
        turn.boards().board_add(slug=slug, name=name)
    except (SpaceExists, SpaceMissing):
        raise Plain(NAME_TAKEN) from None
    except SpaceForbidden:
        raise Plain(CREATE_DENIED) from None
    except SpaceError:
        raise Plain(REFUSED) from None
    return Result(
        True,
        {"created": True, "note": "The project's bot appears in your list of bots."},
    )


# Registration

REF = {"type": "integer", "minimum": 1, "description": "The work number"}
PRIORITY = {"type": "string", "enum": list(priorities.PRIORITIES)}


def _schema(properties=None, required=()):
    return {
        "type": "object",
        "properties": properties or {},
        "required": list(required),
        "additionalProperties": False,
    }


def _static(text):
    return lambda turn, args: text


register(
    Tool(
        name="project_status",
        description=(
            "How the project stands: task counts by status, who is working, what waits and "
            "why, and whether the orchestrator is connected and will be woken."
        ),
        schema=_schema(),
        kind="read",
        run=_project_status,
        label=_static("Looked at the project status"),
        scope="project",
    )
)
register(
    Tool(
        name="list_work",
        description=(
            "List up to 20 tasks, open ones unless a status is given. Statuses: queued, "
            "working, needs_you, ready, blocked, done."
        ),
        schema=_schema({"status": {"type": "string", "enum": list(STATUSES)}}),
        kind="read",
        run=_list_work,
        label=_static("Listed the work"),
        scope="project",
    )
)
register(
    Tool(
        name="work_detail",
        description="One task: status, latest notes and links.",
        schema=_schema({"ref": REF}, ["ref"]),
        kind="read",
        run=_work_detail,
        label=lambda turn, args: f"Looked at work #{args['ref']}",
        scope="project",
    )
)
register(
    Tool(
        name="create_work",
        description=(
            "File new work as a task, in the person's own words. Only when they clearly ask for "
            "it. Without arguments the whole current message is filed. `title` and `body` must "
            "be copied verbatim from the current message; to file only one part of a message, "
            "pass that part. Filing is the only way to hand over new work: never also call "
            "tell_orchestrator with the same text. `priority` is for the project owner only."
        ),
        schema=_schema(
            {
                "title": {"type": "string", "minLength": 1, "maxLength": TITLE_LIMIT},
                "body": {"type": "string", "minLength": 1, "maxLength": 8000},
                "priority": PRIORITY,
            }
        ),
        kind="routine",
        run=_create_work,
        label=_create_work_label,
        scope="project",
    )
)
register(
    Tool(
        name="prioritize_work",
        description=(
            "Change a task's priority, or move it before or after another task in the same "
            "column (`before` / `after` name that other task). Priority is for the project "
            "owner only."
        ),
        schema=_schema(
            {"ref": REF, "priority": PRIORITY, "before": REF, "after": REF},
            ["ref"],
        ),
        kind="routine",
        run=_prioritize_work,
        label=lambda turn, args: f"Reordered work #{args['ref']}",
        scope="project",
    )
)
register(
    Tool(
        name="comment_work",
        description=(
            "Add the person's own words as a comment on a task. `text` must be copied verbatim "
            "from the current message; without it the whole message is the comment."
        ),
        schema=_schema(
            {"ref": REF, "text": {"type": "string", "minLength": 1, "maxLength": 8000}}, ["ref"]
        ),
        kind="routine",
        run=_comment_work,
        label=lambda turn, args: f"Commented on work #{args['ref']}",
        scope="project",
    )
)
register(
    Tool(
        name="tell_orchestrator",
        description=(
            "Pass the person's own words to the project's orchestrator, to steer the work. "
            "`text` must be copied verbatim from the current message; without it the whole "
            "message is passed on. Not for new work: file that with create_work only. The "
            "result says whether the orchestrator is connected and will be woken; report that "
            "honestly, as passed on, never as done."
        ),
        schema=_schema({"text": {"type": "string", "minLength": 1, "maxLength": 8000}}),
        kind="routine",
        run=_tell_orchestrator,
        label=_static("Passed your message to the orchestrator"),
        scope="project",
    )
)
register(
    Tool(
        name="list_decisions",
        description="The open questions waiting for this person in the project.",
        schema=_schema(),
        kind="read",
        run=_list_decisions,
        label=_static("Listed your open questions"),
        scope="project",
    )
)
register(
    Tool(
        name="answer_decision",
        description=(
            "Answer an open question. Give exactly one of `choice` (one of the offered options, "
            "exactly as listed) or `reply` (copied verbatim from the current message). It waits "
            "for the person's tap before anything is sent."
        ),
        schema=_schema(
            {
                "ref": REF,
                "choice": {"type": "string", "minLength": 1, "maxLength": 500},
                "reply": {"type": "string", "minLength": 1, "maxLength": 8000},
            },
            ["ref"],
        ),
        kind="confirm",
        run=_answer_decision,
        label=_answer_decision_label,
        scope="project",
    )
)
register(
    Tool(
        name="approve_work",
        description=(
            "Approve a task that waits for approval, or decline it (`approve` false). It waits "
            "for the person's tap before anything is sent."
        ),
        schema=_schema({"ref": REF, "approve": {"type": "boolean"}}, ["ref", "approve"]),
        kind="confirm",
        run=_approve_work,
        label=_approve_work_label,
        scope="project",
    )
)
register(
    Tool(
        name="create_project",
        description=(
            "Create a new project. `name` must be copied verbatim from the current message. It "
            "waits for the person's tap; the project's bot then appears in their list of bots."
        ),
        schema=_schema({"name": {"type": "string", "minLength": 1, "maxLength": 80}}, ["name"]),
        kind="confirm",
        run=_create_project,
        label=_create_project_label,
    )
)
