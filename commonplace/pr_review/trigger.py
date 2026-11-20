"""The work trigger: when comments wait, hand the card back to its Coworker, once, within limits.

It runs after every runner sweep (``runner.jobs._sweep``) and after every intake. For each PR
with comments in ``new`` it decides, in this order, and each stop has a coded reason:

1. the board's review loop is on, the PR is open and the loop was not stopped for it;
2. the PR comes from the card's own repository and branch (no fork, no other branch);
3. rounds are left (default two per PR, set per board 1 to 3);
4. the board has an accepted Coworker setup, so a start can run by itself.

A card waiting for a Coworker's finished work (a completion review) is returned for more work
as the person who switched the loop on; that person's answer is the delegation, recorded on the
card. The return is an ordinary board event, so the existing todo trigger starts the Coworker
with its fences, caps and bounded retries; nothing here starts a run on its own. A card already
in ``todo`` or ``doing`` is left: the comments wait and join the run. A card waiting for a
person's own answer keeps its comments waiting. Reasons that no start can fix become
``needs you`` comments (one Needs me item per card, ``needs_me.py``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from commonplace.http_util import log_failure

from . import mapping, needs_me, status as st
from .store import ROUNDS_DEFAULT, open_review

logger = logging.getLogger(__name__)

RETURN_CHOICE = "Return for more work"
# One pass looks at this many PRs, longest-waiting first; the rest wait for the next pass.
MAX_PRS_PER_PASS = 20


def _now():
    return datetime.now(timezone.utc)


def run(gateway, workspace_id: str, now: datetime | None = None) -> list[dict]:
    """One pass over the workspace; ``[{repo, pr, card, outcome}]`` for every PR it acted on."""
    store = open_review(gateway, workspace_id)
    now = now or _now()
    store.settle(now)
    boards = store.enabled_boards()
    if not boards:
        return []
    results = []
    for pr_row in store.waiting_prs(MAX_PRS_PER_PASS):
        try:
            outcome = _consider(gateway, workspace_id, store, boards, pr_row, now)
        except Exception as exc:  # one PR's trouble must not hide the others
            log_failure(logger, "review comment trigger failed for a PR", exc)
            outcome = None
        if outcome is not None:
            results.append({"repo": pr_row["repo"], "pr": pr_row["pr"], **outcome})
    return results


def _hold(gateway, workspace_id, store, pr_row, facts, reason, now, setting):
    store.hold(pr_row["repo"], pr_row["pr"], reason, "system", now)
    needs_me.raise_ask(gateway, workspace_id, store, pr_row, facts, setting, now)
    return {"card": facts["number"], "outcome": "held", "reason": reason}


def _consider(gateway, workspace_id, store, boards, pr_row, now):
    found = mapping.match(gateway, workspace_id, pr_row["repo"], pr_row["pr"])
    facts = found["facts"]
    if facts is None:
        return None  # unmapped: shown on the PR, mapped by itself once a card links it
    setting = boards.get(facts["board"])
    if setting is None or pr_row["loop_stopped"] or pr_row["pr_state"] in ("closed", "merged"):
        return None
    hold = lambda reason: _hold(  # noqa: E731
        gateway, workspace_id, store, pr_row, facts, reason, now, setting
    )
    problem = mapping.pr_problem(pr_row, facts)
    if problem:
        return hold(problem)
    rounds = setting["rounds"] or ROUNDS_DEFAULT
    if pr_row["rounds_used"] >= rounds:
        return hold("rounds_exhausted")
    from commonplace.orch import runner_bridge

    if not runner_bridge.configured(gateway, workspace_id, facts["board"]):
        return hold("no_coworker_config")
    status = facts["status"]
    if status == "done":
        return hold("card_done")
    if status in ("todo", "doing"):
        return {"card": facts["number"], "outcome": "waiting", "reason": status}
    if status == "review" and facts["review_kind"] == "completion":
        return _return_card(gateway, workspace_id, store, pr_row, facts, setting, now)
    return {"card": facts["number"], "outcome": "waiting", "reason": "waiting_on_person"}


def _return_card(gateway, workspace_id, store, pr_row, facts, setting, now):
    """Hand a finished card back as the person who switched the loop on (their delegation)."""
    from commonplace import sessions
    from commonplace.boards.service import BoardService
    from commonplace.extensions import ToolContext, ToolError

    person = (
        sessions.person_principal(gateway.db, setting["enabled_by"], workspace_id)
        if setting["enabled_by"]
        else None
    )
    if person is None or person.role != "owner":
        store.hold(pr_row["repo"], pr_row["pr"], "loop_owner_unavailable", "system", now)
        return {"card": facts["number"], "outcome": "held", "reason": "loop_owner_unavailable"}
    counts = store.counts(pr_row["repo"], pr_row["pr"])
    text = (
        f"Review comments arrived on PR #{pr_row['pr']} ({counts[st.NEW]} waiting). "
        "Handed back to the Coworker by the review loop that this workspace owner switched on."
    )
    try:
        BoardService(ToolContext(principal=person, gateway=gateway)).decide(
            facts["number"], choice=RETURN_CHOICE, reply=text, then="return"
        )
    except ToolError as exc:
        # The board's own refusal (not the comment's words) is the reason; log the type only.
        log_failure(logger, "review loop could not return a card", exc)
        store.hold(pr_row["repo"], pr_row["pr"], "card_not_startable", "system", now)
        return {"card": facts["number"], "outcome": "held", "reason": "card_not_startable"}
    return {"card": facts["number"], "outcome": "returned", "reason": None}
