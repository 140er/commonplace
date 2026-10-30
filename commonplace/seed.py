"""Import a reviewed card list through the boards service.

The CLI passes an owner person's ``ToolContext`` to ``seed_file(ctx, path)``.
Titles and bodies are preserved exactly. An existing (board, title) is skipped,
so rerunning a seed never overwrites a person's subsequent edits.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from .extensions import ToolError

STATUSES = ("backlog", "todo", "doing", "review", "done")
_FIELDS = frozenset({"board", "title", "body", "status", "labels", "repo", "branch", "pr_url"})
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]*")


def load_seed(path: str | Path) -> list[dict]:
    """Read and validate the whole file before opening or changing a board."""
    with Path(path).expanduser().open(encoding="utf-8") as source:
        data = json.load(source)
    if not isinstance(data, list):
        raise ValueError("seed must be a JSON array of cards")
    cards = []
    for index, row in enumerate(data, 1):
        prefix = f"seed card {index}"
        if not isinstance(row, dict):
            raise ValueError(f"{prefix} must be an object")
        unknown = row.keys() - _FIELDS
        if unknown:
            raise ValueError(f"{prefix}: unknown fields: {', '.join(sorted(unknown))}")
        for field in ("board", "title"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"{prefix}: {field} must be a nonempty string")
        if not _SLUG.fullmatch(row["board"]):
            raise ValueError(f"{prefix}: board must be a lowercase slug")
        for field in ("body", "repo", "branch", "pr_url"):
            if field in row and not isinstance(row[field], str):
                raise ValueError(f"{prefix}: {field} must be a string")
        status = row.get("status", "todo")
        if not isinstance(status, str) or status not in STATUSES:
            raise ValueError(f"{prefix}: status must be one of {', '.join(STATUSES)}")
        labels = row.get("labels", [])
        if not isinstance(labels, list) or any(
            not isinstance(label, str) or not label.strip() for label in labels
        ):
            raise ValueError(f"{prefix}: labels must be an array of nonempty strings")
        pr_url = row.get("pr_url", "")
        if pr_url:
            try:
                parsed = urlsplit(pr_url)
                valid = parsed.scheme in ("http", "https") and bool(parsed.hostname)
                _ = parsed.port
            except ValueError:
                valid = False
            if not valid or any(char.isspace() for char in pr_url):
                raise ValueError(f"{prefix}: pr_url must be an HTTP(S) URL")
        cards.append({**row, "status": status, "labels": list(labels)})
    return cards


def _require_owner(ctx) -> None:
    if not ctx.principal.person or ctx.principal.role != "owner":
        raise ToolError("no: seeding requires a workspace owner person")


def seed_workspace(db, data_dir, workspace_ref, path, user_ref) -> dict:
    """Build an owner context offline, without starting gateway extensions."""
    from .control_db import ControlError
    from .extensions import ToolContext
    from .sessions import person_principal
    from .workers import WorkerSupervisor

    workspace = db.workspace(workspace_ref)
    user = db.user(user_ref)
    principal = person_principal(db, user["id"], workspace["id"])
    if principal is None or principal.role != "owner":
        raise ControlError("seeding requires an active workspace owner")
    supervisor = WorkerSupervisor(str(data_dir))
    gateway = SimpleNamespace(
        db=db,
        supervisor=supervisor,
        workspace_dir=lambda ws: str(supervisor.workspace_dir(ws)),
    )
    try:
        return seed_file(ToolContext(principal, gateway), path)
    except ModuleNotFoundError as exc:
        if exc.name not in {"commonplace.boards", "commonplace.boards.service"}:
            raise
        raise ToolError("no: boards service is not installed") from exc


def seed_file(ctx, path: str | Path) -> dict:
    """Import cards using an owner person's context; return created/skipped counts.

    ``BoardService.seed_card`` creates the board as needed, stamps the actor,
    emits events and atomically enforces exact (board, title) idempotency. The
    counts compare against the initial snapshot plus this invocation's cards;
    simultaneous imports still deduplicate in the service.
    """
    _require_owner(ctx)
    cards = load_seed(path)
    if not cards:
        return {"created": 0, "skipped": 0}

    # Keep seed-file validation usable without importing the boards extension.
    from .boards.service import BoardService

    service = BoardService(ctx)
    requested = {card["board"] for card in cards}
    existing_boards = {board["slug"] for board in service.board_list()}
    existing = {
        (board, card["title"])
        for board in sorted(requested & existing_boards)
        for card in service.task_list(board=board, all_statuses=True)
    }
    created = skipped = 0
    for card in cards:
        identity = (card["board"], card["title"])
        if identity in existing:
            skipped += 1
            continue
        service.seed_card(**card)
        existing.add(identity)
        created += 1
    return {"created": created, "skipped": skipped}
