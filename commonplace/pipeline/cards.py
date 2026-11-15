"""The board card behind a pull request: when it was first claimed and when its PR was linked.

The Pipeline's ``card`` step is not sent by the deploy script; the board already knows it.
``card_for`` reads the workspace's board file (never creating it) and answers the card whose
PR link is this repository and number, or ``None`` when no card the reader may see is linked.
Only numbers and times leave this module: no title, body or board name.
"""

from __future__ import annotations

from pathlib import Path

from commonplace.boards.service import BoardService, normalize_repo
from commonplace.extensions import ToolContext, ToolError


def _normal(repo: str):
    try:
        return normalize_repo(repo)
    except ToolError:
        return None


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
            normal = _normal(repo)
            if normal is None:
                continue
            for row in conn.execute(
                "SELECT t.id, t.created_at, t.pr_reported_at, b.wing AS wing FROM tasks t "
                "JOIN boards b ON b.id=t.board_id WHERE t.pr_repo=? AND t.pr_number=? "
                "ORDER BY t.id",
                (normal, pr),
            ):
                if not principal.policy.can_read_wing(row["wing"]):
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
                break
    return found
