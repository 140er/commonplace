"""Card priority: a small named scale the workspace owner sets, ordered before position.

Stored as the rank (0 is most urgent) so SQL can order by it; every public card
and tool reply carries the name. Every card has one: the default is ``normal``.

``priority_by`` records who last set it: ``""`` (nobody: the default, still to be ranked),
``person`` (the signed-in workspace owner) or ``coworker`` (the board's Coworker, with a
one-line reason in the card history). A person's priority is never replaced by a
Coworker's.
"""

from commonplace.extensions import ToolError

UNSET = object()
PRIORITIES = ("urgent", "high", "normal", "low")
DEFAULT = "normal"
DEFAULT_RANK = PRIORITIES.index(DEFAULT)


def rank(name) -> int:
    return PRIORITIES.index(name)


def name(value) -> str:
    """Public name for a stored rank; anything unreadable falls back to the default."""
    if type(value) is int and 0 <= value < len(PRIORITIES):
        return PRIORITIES[value]
    return DEFAULT


def validate(value) -> str:
    if not isinstance(value, str) or value not in PRIORITIES:
        raise ToolError("no: priority must be one of " + ", ".join(PRIORITIES))
    return value


def owner_value(principal, value) -> str:
    if not principal.person or principal.role != "owner":
        raise ToolError("no: only a signed-in workspace owner can set a priority")
    return validate(value)


BY_DEFAULT, BY_PERSON, BY_COWORKER = "", "person", "coworker"
SOURCES = {BY_DEFAULT: "default", BY_PERSON: "person", BY_COWORKER: "coworker"}
MAX_REASON = 200


def source(value) -> str:
    """Public name for a stored ``priority_by``; anything unreadable counts as the default."""
    return (
        SOURCES.get(value, SOURCES[BY_DEFAULT]) if isinstance(value, str) else SOURCES[BY_DEFAULT]
    )


def setter(principal, coordinates) -> str:
    """Who is setting a priority: the owner person, or the board's Coworker; nobody else."""
    if principal.person and principal.role == "owner":
        return BY_PERSON
    if coordinates:
        return BY_COWORKER
    raise ToolError(
        "no: only a signed-in workspace owner or the project's Coworker can set a priority"
    )


def reason(value) -> str:
    """The one-line reason a Coworker gives for a priority it sets."""
    if not isinstance(value, str) or not value.strip() or "\n" in value.strip():
        raise ToolError("no: priority_reason must be one line of text")
    text = value.strip()
    if len(text) > MAX_REASON:
        raise ToolError(f"no: priority_reason must be at most {MAX_REASON} characters")
    return text
