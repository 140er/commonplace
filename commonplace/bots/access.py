"""What one bot turn may reach: the bot's access list, the talker and the owner (design section 5).

``effective`` is the single function every path that reaches data for a turn goes through::

    effective reach for a shared bot =
          what the bot's access list grants        (bot_access rows, default none)
       ∩  what the talker may do right now          (their fresh person principal)
       ∩  what the owner may do right now           (owner's fresh person principal)

A bot that is not shared does not consult its list: the reach is the person's own, as before.
Machines and tools use the same shape; the machine term is applied where machines are listed
(:meth:`commonplace.bots.tools.Turn.machines`). The functions here never raise for a person who
lost access: a missing principal reaches nothing, and an unreadable list fails closed.
"""

from dataclasses import dataclass

from commonplace import sessions
from commonplace.boards.service import BoardService
from commonplace.bots import machines
from commonplace.bots.errors import Invalid
from commonplace.control_db import ControlError
from commonplace.extensions import ToolContext

REACHES = ("none", "project:read", "project:write", "machine", "wing:read", "wing:write")

# Fixed refusal text. It never names a project, so it cannot leak what the asker may not read.
SENTENCES = {
    "outside_bot_project": "This bot is not allowed to use that project. Its owner sets that.",
    "outside_bot_other": "This bot is not allowed to use that. Its owner sets that.",
    "bot_read_only": "This bot may only read that project.",
}


class OutsideBot(Exception):
    """The asker may use this, but the bot's access list does not grant it
    (``outside_bot_access``). ``what`` is ``project`` or ``other`` (a tool or a machine)."""

    code = "outside_bot_access"

    def __init__(self, what="other"):
        super().__init__(what)
        self.what = what

    @property
    def text(self) -> str:
        return SENTENCES["outside_bot_project" if self.what == "project" else "outside_bot_other"]


class ReadOnly(Exception):
    """A write on a project the bot may only read (``bot_read_only``)."""

    code = "bot_read_only"
    text = SENTENCES["bot_read_only"]


class OutsideScope(Exception):
    """A grant names something the bot's owner could not reach themselves
    (``outside_your_scope``)."""


@dataclass(frozen=True)
class Access:
    """The effective reach of one turn, resolved from fresh principals.

    ``projects``: slugs this turn may read, in the asker's board order (an unshared bot: all the
    person's readable projects). ``writable``: the subset it may also write. ``asker``: every
    slug the talker may read: the projects a model may name, and the choice between the two refusals. ``machines`` and
    ``tools``: the bot's own grants (empty for an unshared bot, which is never restricted).
    """

    shared: bool
    projects: tuple = ()
    writable: frozenset = frozenset()
    asker: tuple = ()
    machines: frozenset = frozenset()
    tools: frozenset = frozenset()
    grants: tuple = ()  # (kind, ref, mode) of the bot's own list, for the prompt and my_access

    def can_read(self, slug) -> bool:
        return slug in self.projects

    def can_write(self, slug) -> bool:
        return not self.shared or slug in self.writable

    def tool_granted(self, name) -> bool:
        return not self.shared or name in self.tools

    def machine_granted(self, ref) -> bool:
        return not self.shared or ref in self.machines


def _views(gateway, principal) -> dict:
    """``{slug: may_write}`` in board order for what ``principal`` reads (nothing for None)."""
    if principal is None:
        return {}
    service = BoardService(ToolContext(principal=principal, gateway=gateway))
    return {
        board["slug"]: principal.policy.can_write_wing(board["wing"])
        for board in service.board_list()
    }


def person(gateway, user_id, workspace_id):
    """The person's fresh principal, or None once they lost access to the workspace."""
    try:
        return sessions.person_principal(gateway.db, user_id, workspace_id)
    except (ControlError, LookupError):
        return None


def check_grantable(gateway, owner, wanted, stored, known_tools) -> None:
    """Raise :class:`OutsideScope` (or :class:`Invalid` for an unknown tool) when a new or changed
    entry names something ``owner`` cannot reach now: the list never lends what the owner lacks.

    ``wanted``: ``{(kind, ref): mode}``; ``stored``: the current ``(kind, ref, mode)`` rows. An
    entry already stored with the same mode is kept as it is, so an owner who lost something
    can still save the rest of the list; it just stays dormant (the owner term of ``effective``).
    """
    held = {(kind, ref): mode for kind, ref, mode in stored}
    reach = _views(gateway, owner)
    usable = None
    for (kind, ref), mode in wanted.items():
        if held.get((kind, ref)) == mode:
            continue
        if kind == "project":
            if ref not in reach or (mode == "write" and not reach[ref]):
                raise OutsideScope()
        elif kind == "machine":
            if usable is None:
                usable = {m["id"] for m in machines.list_machines(gateway, owner)}
            if ref not in usable:
                raise OutsideScope()
        elif kind == "tool" and ref not in known_tools:
            raise Invalid("unknown tool")


def effective(gateway, bot, grants, talker, owner) -> Access:
    """The reach of a turn of ``bot`` asked by ``talker`` (a principal, or None once gone).

    ``grants``: the bot's access entries (``kind``, ``ref``, ``mode``), or None when they could
    not be read, which fails closed. ``owner``: the bot's owner's fresh principal.
    """
    asker = _views(gateway, talker)
    if not bot.get("shared"):
        return Access(
            False, tuple(asker), frozenset(s for s, w in asker.items() if w), tuple(asker)
        )
    entries = tuple((e["kind"], e["ref"], e["mode"]) for e in grants or ())
    if grants is None or talker is None or owner is None:
        return Access(True, asker=tuple(asker), grants=entries)
    holder = asker if owner.user_id == talker.user_id else _views(gateway, owner)
    mode = {ref: m for kind, ref, m in entries if kind == "project"}
    reads = tuple(slug for slug in asker if slug in mode and slug in holder)
    writes = frozenset(s for s in reads if mode[s] == "write" and asker[s] and holder[s])
    return Access(
        True,
        reads,
        writes,
        tuple(asker),
        frozenset(ref for kind, ref, _m in entries if kind == "machine"),
        frozenset(ref for kind, ref, _m in entries if kind == "tool"),
        entries,
    )


def check_tool(access, tool, project=None) -> None:
    """Raise :class:`OutsideBot` or :class:`ReadOnly` when ``access`` does not cover ``tool``.

    ``project`` is the slug a project tool acts on (already resolved by the turn). A tool with
    reach ``none`` is always allowed; the rest need their own name in the list and a grant that
    covers their reach. Nothing is checked for a bot that is not shared.
    """
    if not access.shared or tool.reach == "none":
        return
    if not access.tool_granted(tool.name):
        raise OutsideBot("other")
    if tool.reach.startswith("wing:"):
        raise OutsideBot("other")  # no wing grant is accepted until a wing tool exists
    if tool.reach == "machine" and not access.machines:
        raise OutsideBot("other")
    if tool.reach.startswith("project:") or tool.scope == "project":
        if project is None or not access.can_read(project):
            raise OutsideBot("project")
        # Starting or stopping an agent changes the project, so it needs a write grant too.
        if tool.reach != "project:read" and not access.can_write(project):
            raise ReadOnly()
