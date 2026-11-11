"""The owner-granted ``cards:decide`` scope: an owner's own app answers that owner's asks."""

from __future__ import annotations

DECIDE_SCOPE = "cards:decide"
SCOPE_TEXT = (
    "Allow this app to answer your Needs-me questions as you. It can answer only questions "
    "addressed to you, never another person's, and every answer is recorded with this "
    "connection. You can stop this at any time under Agents."
)
OWNER_ONLY_TEXT = "Only the workspace owner can let an app answer Needs-me questions."
TOP_LEVEL_TEXT = "Only a top-level connection of the workspace owner can answer Needs-me questions."
AUDIT_ACTION = "card.decide.delegated"


def eligible(db, user_id, workspace_id) -> bool:
    """Whether this person may grant the scope now: a current workspace owner."""
    with db._lock:
        return db.role(workspace_id, user_id) == "owner"


def answers_for(principal, row) -> bool:
    """Whether this connection may answer this ask on behalf of its own person.

    Fails closed: only a live owner's top-level PAT or OAuth connection holding
    the scope, and only for an ask addressed to that same person.
    """
    return bool(
        not principal.person
        and principal.auth in ("pat", "oauth")
        and principal.role == "owner"
        and principal.parent_connection_id is None
        and principal.runner is None
        and principal.policy.has_scope(DECIDE_SCOPE)
        and row["ask_to"] == f"user:{principal.user_id}"
    )
