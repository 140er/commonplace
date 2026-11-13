"""Pure, actor-independent reasons a card is not moving, with one plain sentence each.

The card view, the board summary, the claim refusals and the app all read these, so
the sentences exist only here."""

from .display import _waiting_on_person

CODES = (
    "snoozed",
    "blocked_by",
    "waits_on_person",
    "claim_expired",
    "plan_review",
    "not_released",
    "no_agent",
    "project_limit",
    "checks_pending",
)

# Who can release a restricted-origin card, by the board's release authority.
RELEASE_DETAIL = {
    "coordinator": "the board coordinator releases it",
    "owner": "no connected orchestrator can release it, so the workspace owner does",
    "blocked": "the board coordinator cannot release it; the workspace owner must change the coordinator first",
}

# Reasons that stop an explicit claim or start; the others only explain what the board shows.
REFUSING = ("snoozed", "blocked_by", "waits_on_person", "plan_review", "not_released")


def text(reason: dict) -> str:
    code = reason["code"]
    if code == "snoozed":
        return "Snoozed until " + reason["label"]
    if code == "blocked_by":
        return "Waiting for " + ", ".join(
            f"#{b['number']}: {b['title']}" for b in reason["blockers"]
        )
    if code == "waits_on_person":
        return "Waiting on a person"
    if code == "claim_expired":
        return "Claim expired: the agent stopped reporting; its next update renews it, or hand the card off"
    if code == "plan_review":
        return "Waiting for plan review"
    if code == "not_released":
        detail = ": split stuck" if reason.get("split_stuck") else ""
        if reason.get("release") and not reason.get("split_stuck"):
            detail = ": " + RELEASE_DETAIL[reason["release"]]
        return "Waiting for release" + detail
    if code == "no_agent":
        return "No agent is connected to this project yet"
    if code == "project_limit":
        return f"Waiting for a free slot: {reason['used']} of {reason['cap']} agents busy"
    if code == "checks_pending":
        return "Waiting for checks to finish"
    raise ValueError("unknown waiting reason")


def waiting(
    row,
    *,
    blockers,
    released: bool,
    snoozed: dict | None = None,
    usage,
    claim_expired: bool = False,
    plan_review: bool = False,
    agents: bool | None = None,
) -> dict | None:
    """The one reason ``row`` is not moving, or None.

    ``agents`` is whether any agent connection can work on the card's project; None means
    unknown, which never produces a reason."""
    row = dict(row)
    status = row["status"]
    if status == "done":
        return None
    open_work = status in ("todo", "backlog")
    if snoozed and open_work:
        reason = {
            "code": "snoozed",
            "until": snoozed["until"],
            "after_card": snoozed["after_card"],
            "label": snoozed["text"].removeprefix("Snoozed until "),
        }
    elif blockers:
        reason = {"code": "blocked_by", "blockers": list(blockers)}
    elif _waiting_on_person(row):
        reason = {"code": "waits_on_person"}
    elif status == "doing" and claim_expired:
        reason = {"code": "claim_expired"}
    elif plan_review and not released and open_work:
        reason = {"code": "plan_review"}
    elif not released and open_work:
        reason = {"code": "not_released"}
        if row.get("release_by"):
            reason["release"] = row["release_by"]
        if row.get("split_stuck"):
            reason["split_stuck"] = True
    elif status == "todo" and agents is False:
        reason = {"code": "no_agent"}
    elif open_work and usage is not None and usage.cap is not None and usage.used >= usage.cap:
        reason = {"code": "project_limit", "used": usage.used, "cap": usage.cap}
    elif status in ("doing", "review") and row.get("checks") == "pending":
        reason = {"code": "checks_pending"}
    else:
        return None
    return {**reason, "text": text(reason)}


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def summary_line(summary: dict, *, used: int, cap: int | None, tasks: bool = False) -> str:
    """One compact status line, using the same reason counts as the card views."""
    busy = _plural(used, "agent", "agents") + " busy"
    if cap is not None:
        busy += f" (limit {cap})"
    phrases = {
        "project_limit": "for a free agent" if tasks else "for a free slot",
        "blocked_by": "for another task" if tasks else "for another card",
        "waits_on_person": "for a person",
        "snoozed": "until its snooze ends",
        "claim_expired": "for an update",
        "plan_review": "for plan review",
        "not_released": "for release",
        "no_agent": "for an agent to connect",
        "checks_pending": "for checks",
    }
    waits = []
    for code, phrase in phrases.items():
        if not (n := summary.get(code, 0)):
            continue
        one, many = ("task waits", "tasks wait") if tasks else ("card waits", "cards wait")
        if tasks and waits:
            one, many = "waits", "wait"
        waits.append(f"{_plural(n, one, many)} {phrase}")
    return busy + "." + (" " + ", ".join(waits) + "." if waits else "")


def notes(summary: dict, *, used: int | None = None, cap: int | None = None) -> list[dict]:
    """Board-level sentences from a ``{code: count}`` summary: why cards wait and why few
    agents are working. Only codes with cards behind them appear, in ``CODES`` order."""
    lines = []
    for code in CODES:
        n = summary.get(code, 0)
        if not n:
            continue
        cards = _plural(n, "card", "cards")
        if code == "snoozed":
            line = f"{cards} snoozed: they come back by themselves."
        elif code == "blocked_by":
            line = f"{cards} wait for other cards to be Done."
        elif code == "waits_on_person":
            line = f"{cards} wait for a person to answer."
        elif code == "claim_expired":
            line = f"{cards} have an expired claim: the agent stopped reporting."
        elif code == "plan_review":
            line = f"{cards} wait for plan review."
        elif code == "not_released":
            line = f"{cards} wait for release."
        elif code == "no_agent":
            line = f"No agent is connected, so {cards} cannot start."
        elif code == "project_limit":
            line = f"Agent limit reached ({used} of {cap} working): {cards} wait for a free slot."
        else:
            line = f"{cards} wait for checks to finish."
        lines.append({"code": code, "count": n, "text": line})
    return lines
