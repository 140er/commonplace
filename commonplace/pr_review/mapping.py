"""From a pull request to its card: which card, which board, and whether a Coworker may touch it.

The card is chosen by the pipeline's rules (``pipeline.cards``): the card's own PR repository or
its board's repository, aliases, visibility of the reader, and no guess when more than one
card fits. Reads are done with the board's own file and take an optional principal; without
one (the trigger) every board is visible. Only facts leave this module, never card text.
"""

from __future__ import annotations

import logging
from pathlib import Path

from commonplace.boards.store import BoardStore
from commonplace.http_util import log_failure
from commonplace.pipeline import cards

logger = logging.getLogger(__name__)

_CARD = (
    "SELECT t.id, t.status, t.assignee, t.branch, t.pr_repo, t.pr_state, t.pr_head, t.checks, "
    "t.ask, t.review_kind, t.pr_url, t.created_by, t.version, b.id AS board_id, b.slug AS board, "
    "b.wing AS wing FROM tasks t JOIN boards b ON b.id=t.board_id WHERE t.pr_number=? ORDER BY t.id"
)
_ONE = (
    "SELECT t.id, t.status, t.assignee, t.branch, t.pr_repo, t.pr_state, t.pr_head, t.checks, "
    "t.ask, t.review_kind, t.pr_url, t.pr_number, t.created_by, t.version, b.id AS board_id, "
    "b.slug AS board, b.wing AS wing FROM tasks t JOIN boards b ON b.id=t.board_id WHERE t.id=?"
)


def _facts(row) -> dict:
    keys = (
        "status",
        "assignee",
        "branch",
        "pr_state",
        "pr_head",
        "checks",
        "ask",
        "review_kind",
        "board",
        "wing",
        "pr_url",
        "created_by",
        "version",
    )
    return {"number": row["id"], **{k: row[k] for k in keys}}


def _open(gateway, workspace_id):
    path = Path(gateway.supervisor.workspace_dir(workspace_id)) / "boards.sqlite3"
    return BoardStore(path.parent, create=False) if path.exists() else None


def match(gateway, workspace_id: str, repo: str, pr: int, *, principal=None) -> dict:
    """``{card, reason, candidates, facts}``: ``card`` is a number or ``None`` with ``reason``
    ``no_card`` or ``ambiguous_card`` (and the candidate numbers the reader may see)."""
    family = cards.aliases(repo)
    store = _open(gateway, workspace_id)
    if store is None or not family:
        return {"card": None, "reason": "no_card", "candidates": [], "facts": None}
    try:
        with store.connection() as conn:
            rows = [
                row
                for row in conn.execute(_CARD, (pr,))
                if principal is None or principal.policy.can_read_wing(row["wing"])
            ]
            row, ambiguous = cards.pick_with_reason(conn, rows, family)
            near = [
                r["id"] for r in rows if cards._normal(r["pr_repo"]) in family or not r["pr_repo"]
            ]
    except Exception as exc:  # the board is not the review loop's to break
        log_failure(logger, "review comment card lookup failed", exc)
        return {"card": None, "reason": "lookup_failed", "candidates": [], "facts": None}
    if row is not None:
        return {"card": row["id"], "reason": None, "candidates": [row["id"]], "facts": _facts(row)}
    return {
        "card": None,
        "reason": "ambiguous_card" if ambiguous else "no_card",
        "candidates": near if ambiguous else [],
        "facts": None,
    }


def card_facts(gateway, workspace_id: str, number: int, *, principal=None) -> dict | None:
    """The facts of one card (``None`` when it does not exist or the reader may not see it)."""
    store = _open(gateway, workspace_id)
    if store is None:
        return None
    with store.connection() as conn:
        row = conn.execute(_ONE, (number,)).fetchone()
    if row is None or (principal is not None and not principal.policy.can_read_wing(row["wing"])):
        return None
    return {**_facts(row), "pr_number": row["pr_number"], "pr_repo": row["pr_repo"]}


def repo_of(facts) -> str | None:
    """``owner/name`` of a card's GitHub PR (from its PR link, else its stored PR repository)."""
    from commonplace.boards.store import pr_repository

    named = (pr_repository(facts["pr_url"]) if facts["pr_url"] else None) or facts["pr_repo"]
    normal = cards._normal(named) if named else None
    return normal[len("github.com/") :] if normal and normal.startswith("github.com/") else None


def watched(gateway, workspace_id: str, boards: dict) -> list[dict]:
    """The PRs a feeder should read: cards on enabled boards whose PR is open or not yet
    reported closed. ``[{repo, pr, card}]``, from the card's PR link; nothing else leaves."""
    store = _open(gateway, workspace_id)
    if store is None or not boards:
        return []
    out, seen = [], set()
    with store.connection() as conn:
        for row in conn.execute(
            "SELECT t.id, t.pr_url, t.pr_number, t.pr_repo, t.pr_state, b.slug AS board "
            "FROM tasks t JOIN boards b ON b.id=t.board_id WHERE t.pr_number IS NOT NULL "
            "ORDER BY t.id"
        ):
            if row["board"] not in boards or row["pr_state"] in ("merged", "closed"):
                continue
            repo = repo_of(row)
            key = (repo, row["pr_number"])
            if repo and key not in seen:
                seen.add(key)
                out.append({"repo": repo, "pr": row["pr_number"], "card": row["id"]})
    return out


def pr_problem(pr_row, facts) -> str | None:
    """Why the loop may not work this PR for this card, as a reason code: a fork, or a branch
    that is not the card's. ``None`` when the PR is the card's own."""
    if pr_row["head_repo"] and cards._normal(pr_row["head_repo"]) not in cards.aliases(
        pr_row["repo"]
    ):
        return "fork_pr"
    if pr_row["branch"] and facts["branch"] and pr_row["branch"] != facts["branch"]:
        return "branch_not_owned"
    return None


def question_event(gateway, workspace_id, number, *, version) -> int:
    """The exact question just placed, or zero if the card changed before it could be bound."""
    store = _open(gateway, workspace_id)
    if store is None:
        return 0
    with store.connection() as conn:
        row = conn.execute(
            "SELECT e.id FROM task_events e JOIN tasks t ON t.id=e.task_id "
            "WHERE t.id=? AND t.version=? AND e.to_status='review' "
            "AND e.kind IN ('ask','handoff','move') ORDER BY e.id DESC LIMIT 1",
            (number, version),
        ).fetchone()
    return row["id"] if row else 0


def last_answer(gateway, workspace_id: str, number: int, choices: list, *, pr: int) -> dict | None:
    """The answer to the card's latest question, including its exact ``question_id``.
    Never search past a newer question, even one for the same PR or offering the same words.
    """
    from .needs_me import is_ours

    store = _open(gateway, workspace_id)
    if store is None or not choices:
        return None
    with store.connection() as conn:
        row = conn.execute(
            "SELECT e.id, e.choice, e.stamp_user, a.id AS question_id, a.text AS question "
            "FROM task_events e JOIN task_events a ON a.task_id=e.task_id AND a.id < e.id "
            "WHERE e.task_id=? AND e.kind='decide' AND a.id=("
            "SELECT id FROM task_events WHERE task_id=e.task_id AND to_status='review' "
            "AND kind IN ('ask','handoff','move') ORDER BY id DESC LIMIT 1) "
            "ORDER BY e.id DESC LIMIT 1",
            (number,),
        ).fetchone()
    if row and row["choice"] in choices and is_ours(row["question"], pr):
        return {key: row[key] for key in ("id", "choice", "stamp_user", "question_id")}
    return None
