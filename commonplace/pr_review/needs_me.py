"""One Needs me item per pull request: the card's ordinary ask, raised once.

When comments end in ``needs you`` the owner gets one question on the card (not one per
comment): it names the PR and how many comments wait, never their words. The card's own ask
is the item, so it lands in the same queue as every other question, counted once however many
comments wait. A card that already has an open ask keeps it (it is already in the queue). The
person answers in the card's Review comments section (``POST /api/pr-review/cards/<n>/decision``,
which also answers the card's question) or anywhere the card's question is shown (the next
trigger pass applies an answer given there, once: ``apply_answers``).
"""

from __future__ import annotations

import logging

from commonplace.http_util import log_failure

from . import status as st

logger = logging.getLogger(__name__)

AGAIN = {1: "One more round", 2: "Two more rounds", 3: "Three more rounds"}
MINE, IGNORE = "I will do it myself", "Ignore"
# Every label an answer may carry, older ones included, mapped to the store's choice.
CHOICES = {
    **{label: "again" for label in AGAIN.values()},
    MINE: "mine",
    IGNORE: "dismiss",
    "Let the Coworker try again": "again",
    "I'll handle these myself": "mine",
    "Dismiss these": "dismiss",
}
# Where the card goes with each answer: back to its Coworker, or out of the Coworkers' way.
THEN = {"again": "return", "dismiss": "return", "mine": "backlog"}
QUESTION_START = "Review comments on PR #"


def options(rounds: int) -> list[str]:
    """The three answers, the first naming how many rounds the board gives back."""
    return [AGAIN.get(rounds, AGAIN[2]), MINE, IGNORE]


def is_ours(ask) -> bool:
    return isinstance(ask, str) and ask.startswith(QUESTION_START)


def question(pr: int, counts: dict) -> str:
    waiting = counts[st.NEEDS_YOU]
    total = sum(counts[s] for s in (st.NEW, st.WORKING, st.ADDRESSED, st.ANSWERED, st.NEEDS_YOU))
    return f"{QUESTION_START}{pr} need a decision: {waiting} of {total}"


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
            options(setting.get("rounds") or 2),
            task=facts["number"],
            context="Open the card to see each comment, its status and the reason.",
            person_reason="Review comments need a person's decision.",
        )
    except ToolError as exc:
        log_failure(logger, "review loop could not place its question", exc)
        return False
    return True


def apply_answers(gateway, workspace_id, store, now) -> int:
    """Apply answers given to the loop's question outside the Review comments section (Needs
    me, the card's own question): each answered once, by its board event id. Counts applied."""
    from . import mapping

    applied = 0
    for pr_row in store.prs_waiting_on_person():
        found = mapping.match(gateway, workspace_id, pr_row["repo"], pr_row["pr"])
        if found["card"] is None:
            continue
        event = mapping.last_answer(gateway, workspace_id, found["card"], list(CHOICES))
        if event is None or event["id"] <= pr_row["decided_event"]:
            continue
        store.decide(
            pr_row["repo"],
            pr_row["pr"],
            CHOICES[event["choice"]],
            event["stamp_user"] or "person",
            now,
            event_id=event["id"],
        )
        applied += 1
    return applied
