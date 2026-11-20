"""The review comments a started Coworker reads: the card's PR, its waiting comments, the rules.

``for_card`` answers what the start context shows (``runner.start_context``): the comments in
``new`` or ``working`` of the card's pull request, redacted, as the starting connection may read
them, with the reply mode and the rounds. A card the connection cannot see, a card without a PR
or a PR with nothing waiting answers ``None`` and adds nothing.
"""

from __future__ import annotations

from . import mapping, status as st
from .store import RUN_COMMENTS, open_review, reply_mode


def for_card(ctx, number: int) -> dict | None:
    gateway, workspace = ctx.gateway, ctx.principal.workspace_id
    facts = mapping.card_facts(gateway, workspace, number, principal=ctx.principal)
    repo = mapping.repo_of(facts) if facts else None
    pr = facts.get("pr_number") if facts else None
    if not repo or not pr:
        return None
    store = open_review(gateway, workspace)
    waiting = [
        c for c in store.comments(repo, pr, limit=500) if c["status"] in (st.NEW, st.WORKING)
    ][:RUN_COMMENTS]
    if not waiting:
        return None
    row, board = store.pr(repo, pr), store.board(facts["board"])
    return {
        "repo": repo,
        "pr": pr,
        "branch": facts.get("branch"),
        "mode": reply_mode(row, board),
        "rounds_used": row["rounds_used"] if row else 0,
        "rounds": board["rounds"],
        "comments": waiting,
    }
