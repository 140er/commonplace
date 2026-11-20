"""One Needs me item per pull request: the card's ordinary ask, raised once.

When comments end in ``needs you`` the owner gets one question on the card (not one per
comment): it names the PR and how many comments wait, never their words. The card's own ask
is the item, so it lands in the same queue as every other question, counted once however many
comments wait. A card that already has an open ask keeps it (it is already in the queue). The
person's answer arrives through ``POST /api/pr-review/cards/<n>/decision``.
"""

from __future__ import annotations

import logging

from commonplace.http_util import log_failure

from . import status as st

logger = logging.getLogger(__name__)

OPTIONS = ["Let the Coworker try again", "I'll handle these myself", "Dismiss these"]
CHOICES = dict(zip(OPTIONS, ("again", "mine", "dismiss"), strict=True))


def question(pr: int, counts: dict) -> str:
    waiting = counts[st.NEEDS_YOU]
    total = sum(counts[s] for s in (st.NEW, st.WORKING, st.ADDRESSED, st.ANSWERED, st.NEEDS_YOU))
    return f"Review comments on PR #{pr} need a decision: {waiting} of {total}"


def raise_ask(gateway, workspace_id, store, pr_row, facts, setting, now) -> bool:
    """Ask the owner once on the card. True when an ask was placed; a card that cannot take one
    (done, held elsewhere) keeps the comments visible as ``needs you`` and the reason on them."""
    if facts.get("ask"):
        return False
    from commonplace import sessions
    from commonplace.boards.service import BoardService
    from commonplace.extensions import ToolContext, ToolError

    person = (
        sessions.person_principal(gateway.db, setting["enabled_by"], workspace_id)
        if setting.get("enabled_by")
        else None
    )
    if person is None:
        return False
    counts = store.counts(pr_row["repo"], pr_row["pr"])
    try:
        BoardService(ToolContext(principal=person, gateway=gateway)).ask(
            question(pr_row["pr"], counts),
            OPTIONS,
            task=facts["number"],
            context="Open the card to see each comment, its status and the reason.",
            person_reason="Review comments need a person's decision.",
        )
    except ToolError as exc:
        log_failure(logger, "review loop could not place its question", exc)
        return False
    return True
