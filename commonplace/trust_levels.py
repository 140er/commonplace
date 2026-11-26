"""What each connection trust level allows, in plain words, and who may change it.

One source for the server (Admin projection, refusal texts) and the web (it renders what the
server sends). The behaviour itself lives in :mod:`commonplace.policy`; these lines describe it.
An owner moves a connection between ``untrusted`` and ``standard`` only. ``trusted`` is set up
from the command line, so the browser never grants it and never edits a connection that holds it.
"""

from __future__ import annotations

from commonplace.policy import TRUST_LEVELS, Policy, PolicyError

TEXT = {
    "untrusted": (
        "Adds notes to its own inbox for you to review; it cannot change or delete existing "
        "memories, write to private wings, or read agent threads."
    ),
    "standard": (
        "Reads and writes the wings you gave it and can change existing memories; it cannot "
        "delete memories or use admin tools."
    ),
    "trusted": (
        "Full access within what it was granted, including deleting memories and admin tools "
        "when granted."
    ),
}
SETTABLE = ("untrusted", "standard")

OWNER_ONLY_TEXT = "Only the workspace owner can change a connection's trust level."
INACTIVE_TEXT = "Only an active connection can change; this one is revoked or expired."
TRUSTED_TEXT = (
    "A trusted connection is managed from the command line; its level can't be changed here."
)
OAUTH_TEXT = "Manage this connection in its sign-in client."
MACHINERY_TEXT = "Runner and project bot connections get their level from their own setup."
CHOICE_TEXT = "Choose untrusted or standard."


def levels() -> list:
    """Every level with its summary, in the order the policy lists them."""
    return [{"level": level, "summary": TEXT[level]} for level in TRUST_LEVELS]


def target_refusal(raw_policy, trust) -> str | None:
    """Why this stored policy cannot take ``trust``, or ``None``.

    Runs the policy's own construction checks, so a lowering that the policy would refuse at
    creation (an untrusted writer needs an inbox wing, and may not write to a private wing)
    is refused here for the same reason.
    """
    if trust not in SETTABLE:
        return CHOICE_TEXT
    try:
        current = Policy.from_json(raw_policy)
        if not current.scopes and not current.tools:
            return "This connection's stored policy can't be read."
        Policy.build(
            tools=None if isinstance(current.tools, str) else current.tools,
            profile=current.tools if isinstance(current.tools, str) else "slim",
            scopes=current.scopes,
            read_wings=current.read_wings,
            write_wings=current.write_wings,
            write_wing=current.write_wing,
            trust=trust,
        )
    except PolicyError as exc:
        return str(exc)
    return None


def change_view(row, *, active, machinery) -> dict:
    """What the Admin page needs to offer (or explain) a change: ``can_change``, targets, reason."""
    reason = ""
    options = []
    current = Policy.from_json(row["policy"]).trust
    if row["auth"] != "pat":
        reason = OAUTH_TEXT
    elif machinery:
        reason = MACHINERY_TEXT
    elif not active:
        reason = INACTIVE_TEXT
    elif current not in SETTABLE:
        reason = TRUSTED_TEXT
    else:
        refusals = {}
        for level in SETTABLE:
            if level == current:
                continue
            refusal = target_refusal(row["policy"], level)
            if refusal:
                refusals[level] = refusal
            else:
                options.append(level)
        if not options:
            reason = next(iter(refusals.values()), "No other level is available.")
    return {"can_change": bool(options), "options": options, "reason": reason}
