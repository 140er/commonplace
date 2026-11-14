"""Bot tools for the learning memory: listing, forgetting, pausing and sharing learned items.

Every tool runs as the person (the user id comes from the turn, never from an argument) and none
turns learning on, widens a mode or edits text. ``learned_list`` only reads, ``pause_learning``
only pauses, and ``forget_learned``, ``forget_all_learned`` and ``share_learned`` are confirm
tools: the model can only ask, nothing is deleted or copied until the person taps the chip, and
the chip label is built here from the stored item.

``share_learned`` is a confirm tool: the model can only ask; nothing is copied until the person
taps the chip. The chip label is built here from the stored item, never from model text, and
states exactly what will happen. Everything is checked again at the tap (``run``): the item, the
project and both owner switches. The same chip is made by ``POST /api/learned/<id>/share``
(``build_pending``), so the person can share from the review list without a model.
"""

import uuid

from commonplace import redact
from commonplace.bots import learned, store, tools
from commonplace.extensions import ToolError

SENTENCES = {
    "workspace_off": "Learning is switched off for this workspace.",
    "shared_off": "Sharing learned items is switched off for this workspace.",
    "project_off": "Sharing learned items is switched off for that project.",
    "capped": "That project already has the most shared items it can hold.",
    "conflict": "That is already shared with this project.",
    "missing": "There is no such learned item.",
    "unshareable": "That item cannot be shared.",
    "refused": "That cannot be shared.",
    "project": "Say which project to share it with.",
}


def _items(turn):
    return learned.open_learned(turn.gateway, turn.workspace_id)


def _enabled(turn) -> bool:
    """Offered only to the person's own bot, and only while sharing could work at all."""
    if turn.bot.get("shared"):
        return False
    policy = _items(turn).policy()
    return policy["enabled"] and policy["shared_enabled"]


def target(turn, args, item) -> str:
    """The project the item goes to: the bot's own, else the named one, else the item's own tag."""
    own = turn.bot.get("board")
    if own:
        if args.get("project") not in (None, own):
            raise ToolError(SENTENCES["unshareable"])
        return own
    slug = args.get("project") or item["board"]
    if not isinstance(slug, str):
        raise ToolError(SENTENCES["project"])
    return slug


def _plain(exc) -> ToolError:
    if isinstance(exc, learned.Disabled):
        return ToolError(SENTENCES.get(exc.reason, SENTENCES["refused"]))
    if isinstance(exc, learned.Capped):
        return ToolError(SENTENCES["capped"])
    if isinstance(exc, store.Conflict):
        return ToolError(SENTENCES["conflict"])
    if isinstance(exc, store.Missing):
        return ToolError(SENTENCES["missing"])
    if isinstance(exc, store.Invalid):
        return ToolError(SENTENCES["unshareable"])
    return ToolError(SENTENCES["refused"])


def _checked(turn, args) -> tuple[dict, str]:
    """The item and the project, as shared right now, or a plain refusal."""
    items = _items(turn)
    try:
        item = items.get(turn.user_id, args["id"])
    except store.Missing:
        raise ToolError(SENTENCES["missing"]) from None
    slug = target(turn, args, item)
    if not turn.effective().can_read(slug):
        raise tools.Gone()
    try:
        items.share_check(turn.user_id, args["id"], slug, turn.effective().can_read)
    except learned.Disabled:
        raise  # switches are told apart by the caller (creation refuses, the tap closes)
    except (store.Missing, store.Invalid) as exc:
        raise _plain(exc) from None
    return item, slug


def label_for(item, name) -> str:
    """The chip: the exact words, the project and who will see them. Built from stored data."""
    words = redact.redact(item["text"])[0]
    return (
        f"Share with everyone who can read {name}, with your name: “{words}”. "
        "It stays visible there until you or the workspace owner delete it."
    )


def _name(boards, slug) -> str:
    return next((b["name"] for b in boards if b["slug"] == slug), slug)


def _check(turn, args):
    # The item and the project must be the person's and readable; the switches are the tap's.
    try:
        _checked(turn, args)
    except learned.Disabled:
        pass


def _label(turn, args) -> str:
    # Built before a chip exists, so a switch that is off refuses here, early and plainly.
    try:
        item, slug = _checked(turn, args)
    except learned.Disabled as exc:
        raise _plain(exc) from None
    return label_for(item, _name(turn.boards().board_list(), slug))


def _run(turn, args):
    items = _items(turn)
    try:
        item = items.get(turn.user_id, args["id"])
        slug = target(turn, args, item)
        copy = items.share(turn.user_id, args["id"], slug, turn.effective().can_read)
    except ToolError:
        raise
    except (
        learned.Disabled,
        learned.Capped,
        learned.Refused,
        store.Conflict,
        store.Missing,
        store.Invalid,
    ) as exc:
        return tools.Result(False, say=str(_plain(exc)))
    name = _name(turn.boards().board_list(), slug)
    return tools.Result(True, {"id": copy["id"]}, say=f"Shared with everyone who can read {name}.")


def build_pending(gateway, principal, item_id, slug):
    """A chip for sharing item ``item_id`` into project ``slug`` (the review list's Share button):
    the pending row, built from the stored item, for the bot the item was learned in.

    Raises :class:`ToolError` with a plain sentence for a refusal, or :class:`store.Missing`.
    """
    bots = store.open_bots(gateway, principal.workspace_id)
    items = learned.open_learned(gateway, principal.workspace_id)
    item = items.get(principal.user_id, item_id)
    bot = bots.get(item["bot_id"], principal.user_id)
    turn = tools.Turn(
        gateway,
        principal.workspace_id,
        principal.user_id,
        bot,
        {"id": item["source_message_id"] or 0, "text": ""},
        "share-" + uuid.uuid4().hex,
    )
    args = {"id": item_id, "project": slug}
    try:
        label = _label(turn, args)
    except tools.Gone:
        raise store.Missing() from None  # a project the person cannot read looks missing
    frozen = {
        "args": args,
        "version": None,
        "message": item["source_message_id"],
        "request_id": turn.request_id,
        "step": 0,
    }
    return bots.pending_create(bot["id"], principal.user_id, "share_learned", frozen, label)


tools.register(
    tools.Tool(
        name="share_learned",
        description=(
            "Offer to share one of the person's learned items with a project, by its `id`. "
            "Only when the person asks to share it. Waits for the person's tap; nothing is "
            "shared before that. `project` only when this bot has no project of its own."
        ),
        schema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1, "maxLength": 64},
                "project": {"type": "string", "minLength": 1, "maxLength": 200},
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        kind="confirm",
        reach="none",
        available=_enabled,
        check=_check,
        run=_run,
        label=_label,
    )
)


# Listing, forgetting and pausing

LIST_MAX = 20
FORGET_SAYS = {
    "missing": "There is no such learned item.",
    "gone": "That was already forgotten.",
}


def _learning_on(turn) -> bool:
    """Offered only to the person's own bot (a shared bot has no learning setting of its own) and
    only while the workspace switch is on, so a workspace that never turned learning on sends
    exactly the tool list it always sent."""
    return not turn.bot.get("shared") and _items(turn).policy()["enabled"]


def _list(turn, args):
    page = _items(turn).list(
        turn.user_id,
        scope=args.get("scope"),
        state=args.get("state"),
        limit=min(args.get("limit", LIST_MAX), LIST_MAX),
    )
    return tools.Result(
        True,
        {
            "items": [
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "scope": item["scope"],
                    "state": item["state"],
                    "said": item["source_at"][:10],
                    "text": redact.redact(item["text"])[0],
                }
                for item in page
            ]
        },
        keep=tuple(item["id"] for item in page),
    )


def _quote(item) -> str:
    return redact.redact(item["text"])[0]


def _stored(turn, args) -> dict:
    try:
        return _items(turn).get(turn.user_id, args["id"])
    except store.Missing:
        raise ToolError(FORGET_SAYS["missing"]) from None


def _forget_check(turn, args):
    _stored(turn, args)


def _forget_label(turn, args) -> str:
    return f"Forget this for good: “{_quote(_stored(turn, args))}”. It cannot be undone."


def _forget_run(turn, args):
    try:
        _items(turn).forget(turn.user_id, args["id"])
    except store.Missing:
        return tools.Result(False, say=FORGET_SAYS["gone"])
    return tools.Result(True, {"id": args["id"]}, say="Forgotten.")


def _forget_all_label(turn, args) -> str:
    counts = _items(turn).counts(turn.user_id, turn.bot["id"])
    items = counts["person"] + counts["shared"] + counts["proposed"]
    return (
        f"Forget everything this bot learned about you: {items} learned items and "
        f"{counts['notes']} notes, including what you shared with a project. "
        "It will also ignore your messages so far. It cannot be undone."
    )


def _forget_all_run(turn, args):
    try:
        done = _items(turn).forget_all(turn.bot["id"], turn.user_id)
    except store.Missing:
        return tools.Result(False, say=FORGET_SAYS["missing"])
    return tools.Result(True, done, say="Forgot everything this bot learned.")


def _pause_run(turn, args):
    days = args["days"]
    try:
        _items(turn).configure(turn.bot["id"], turn.user_id, pause=days)
    except (store.Missing, store.Invalid):
        return tools.Result(False, say="I cannot pause learning here.")
    return tools.Result(True, {"days": days}, say=f"Paused learning for {days} days.")


tools.register(
    tools.Tool(
        name="learned_list",
        description=(
            "List what this person's bots learned about them (their own words, newest first), "
            "optionally only one `scope` (person or shared) or `state` (active or proposed). "
            "Only when the person asks what you remember or learned."
        ),
        schema={
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["person", "shared"]},
                "state": {"type": "string", "enum": ["active", "proposed"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": LIST_MAX},
            },
            "required": [],
            "additionalProperties": False,
        },
        kind="read",
        reach="none",
        available=_learning_on,
        run=_list,
        label=lambda turn, args: "Looked at what I learned",
    )
)
tools.register(
    tools.Tool(
        name="forget_learned",
        description=(
            "Offer to forget one learned item for good, by its `id`. Only when the person asks. "
            "Waits for the person's tap; nothing is deleted before that."
        ),
        schema={
            "type": "object",
            "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 64}},
            "required": ["id"],
            "additionalProperties": False,
        },
        kind="confirm",
        reach="none",
        available=_learning_on,
        check=_forget_check,
        run=_forget_run,
        label=_forget_label,
    )
)
tools.register(
    tools.Tool(
        name="forget_all_learned",
        description=(
            "Offer to forget everything this bot learned about the person. Only when the "
            "person asks. Waits for the person's tap; nothing is deleted before that."
        ),
        schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        kind="confirm",
        reach="none",
        available=_learning_on,
        run=_forget_all_run,
        label=_forget_all_label,
    )
)
tools.register(
    tools.Tool(
        name="pause_learning",
        description=(
            "Pause learning from this conversation for a number of days (1 to 30), when the "
            "person asks you to stop learning. It never turns learning on or changes a mode."
        ),
        schema={
            "type": "object",
            "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 30}},
            "required": ["days"],
            "additionalProperties": False,
        },
        kind="routine",
        reach="none",
        available=_learning_on,
        run=_pause_run,
        label=lambda turn, args: f"Paused learning for {args['days']} days",
    )
)
