"""The board card behind a pull request: when it was first claimed and when its PR was linked.

The Pipeline's ``card`` step is not sent by the deploy script; the board already knows it.
``cards_for`` reads the workspace's board file (never creating it) and answers, for each
(repository, PR number), the card whose PR link it is, or nothing when no card the reader may
see fits. Only numbers and times leave this module: no title, body or board name.

A card stores the repository of its board when the PR was linked, which can be an older name
of the repository the events carry (a renamed repository). So a card is chosen like this,
among the cards the reader may see that have this PR number:

1. a unique card whose normalized PR repository is the event's repository or an alias;
2. else a unique card missing its PR repository, on a board whose normalized repository
   is the event's repository or an alias.

A populated, different PR repository rules a card out, even if its board matches.
Ambiguous matches and cards with no repository information are left unlinked. PR numbers
alone never establish repository identity; renamed repositories need an explicit alias
or a migration of the card's stored PR repository.

Aliases come from ``COMMONPLACE_REPO_ALIASES``, ``old/name=new/name`` pairs separated by
commas, read both ways. Nothing is matched across workspaces: a reader only sees its own
workspace's board file.
"""

from __future__ import annotations

import os
from pathlib import Path

from commonplace.boards.service import BoardService, normalize_repo
from commonplace.extensions import ToolContext, ToolError

ALIASES_ENV = "COMMONPLACE_REPO_ALIASES"


def _normal(repo):
    try:
        return normalize_repo(repo)
    except ToolError:
        return None


def aliases(repo: str) -> set:
    """The normalized repository and every alias of it named in ``COMMONPLACE_REPO_ALIASES``."""
    family = {n for n in [_normal(repo)] if n}
    for pair in os.environ.get(ALIASES_ENV, "").split(","):
        left, _, right = pair.partition("=")
        names = {_normal(left.strip()), _normal(right.strip())} if right.strip() else set()
        names.discard(None)
        if names & family:
            family |= names
    return family


def _boards_of(conn, board_id) -> set:
    return {
        _normal(row["repo"])
        for row in conn.execute("SELECT repo FROM board_repos WHERE board_id=?", (board_id,))
    }


def pick_with_reason(conn, candidates, family):
    """``(row, ambiguous)``: the card for one PR among visible candidates (see the module note),
    or ``(None, True)`` when more than one fits and ``(None, False)`` when none does."""
    matches = [row for row in candidates if _normal(row["pr_repo"]) in family]
    if matches:
        return (matches[0], False) if len(matches) == 1 else (None, True)
    matches = [
        row
        for row in candidates
        if not row["pr_repo"] and _boards_of(conn, row["board_id"]) & family
    ]
    return (matches[0], False) if len(matches) == 1 else (None, len(matches) > 1)


def _pick(conn, candidates, family):
    """The card for one PR among visible candidates, or ``None`` (see the module note)."""
    return pick_with_reason(conn, candidates, family)[0]


def cards_for(gateway, principal, keys) -> dict:
    """``{(repo, pr): {number, claimed_at, linked_at}}`` for the pairs that have a visible card."""
    keys = list(keys)
    path = Path(gateway.supervisor.workspace_dir(principal.workspace_id)) / "boards.sqlite3"
    if not keys or not path.exists():
        return {}
    found = {}
    store = BoardService(ToolContext(principal=principal, gateway=gateway)).store
    with store.connection() as conn:
        for repo, pr in keys:
            family = aliases(repo)
            if not family:
                continue
            candidates = [
                row
                for row in conn.execute(
                    "SELECT t.id, t.created_at, t.pr_reported_at, t.pr_repo, b.id AS board_id, "
                    "b.wing AS wing FROM tasks t JOIN boards b ON b.id=t.board_id "
                    "WHERE t.pr_number=? ORDER BY t.id",
                    (pr,),
                )
                if principal.policy.can_read_wing(row["wing"])
            ]
            row = _pick(conn, candidates, family)
            if row is None:
                continue
            claimed = conn.execute(
                "SELECT created_at FROM task_events WHERE task_id=? AND to_status='doing' "
                "ORDER BY id LIMIT 1",
                (row["id"],),
            ).fetchone()
            found[(repo, pr)] = {
                "number": row["id"],
                "claimed_at": claimed["created_at"] if claimed else row["created_at"],
                "linked_at": row["pr_reported_at"],
            }
    return found
