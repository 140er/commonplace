"""Bot tools for the learning memory: sharing one of the person's learned items with a project.

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
