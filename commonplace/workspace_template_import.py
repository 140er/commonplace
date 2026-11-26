"""Apply a previewed workspace template to a workspace the actor owns.

``apply`` is the only write path of a template import. It refuses unless the actor owns the
target workspace, scans the package for credentials on its own (a second line behind the
preview), and recomputes the preview against the target as it is now: the caller's
``preview_id`` must equal it, so a changed package or a changed target is never applied
blindly. Then it writes, in this order:

1. goals (``boards.sqlite3``, one transaction),
2. routines and seats (control database, one transaction),
3. one orchestrator config *proposal* (``runner.sqlite3``): new roles and skill references,
   never accepted, never a runner, trigger or MCP grant.

The stores are separate files, so a failure in a later step removes only the rows this
import created in the earlier ones (compensating cleanup); an earlier step that fails rolls
its own transaction back. Existing rows are never updated or deleted: a name that already
exists is skipped, and imported goals whose parent was skipped attach to the existing one.
Memory has no writer: ``include_memory`` is refused with ``memory_unavailable``.
"""

from __future__ import annotations

import logging
import time

from commonplace import routines, workspace_template as wt
from commonplace.boards import goals as board_goals
from commonplace.boards.store import BoardStore, now as board_now
from commonplace.control_db import ControlError
from commonplace.projects import seats

logger = logging.getLogger(__name__)
TemplateError = wt.TemplateError


def _codes(result: dict) -> str:
    return ", ".join(sorted({r["code"] for r in result["refused"]}))


def _owner(db, workspace_id: str, actor: str):
    try:
        user = db.user(actor)
        db.require_owner(workspace_id, user["id"])
    except (ControlError, LookupError, KeyError, TypeError):
        raise TemplateError(
            "owner_only", "only an owner of the target workspace can import"
        ) from None
    return user


def _skipped(collisions: list[dict]) -> list[dict]:
    return [dict(c) for c in collisions]


def _goal_paths(rows: list[dict]) -> dict[str, int]:
    return {path.casefold(): ident for ident, path in wt._goal_paths(rows, "title", "parent", "id")}


def _write_goals(directory, content: dict, actor: str, created: list[int]) -> int:
    entries = content.get("goals", [])
    if not entries:
        return 0
    with BoardStore(directory).transaction() as conn:
        existing = [
            {"id": r["id"], "parent": r["parent_id"], "title": r["title"]}
            for r in conn.execute("SELECT id, parent_id, title FROM goals ORDER BY id")
        ]
        known = _goal_paths(existing)
        ids: dict[int, int | None] = {}
        paths = {
            ref: path.casefold() for ref, path in wt._goal_paths(entries, "title", "parent", "ref")
        }
        by_ref = {e["ref"]: e for e in entries}
        done = 0
        for entry in sorted(entries, key=lambda e: _depth(e, by_ref)):
            path = paths[entry["ref"]]
            if path in known:
                ids[entry["ref"]] = known[path]
                continue
            parent = ids.get(entry["parent"]) if entry.get("parent") is not None else None
            view = board_goals.put(
                conn,
                f"user:{actor}",
                board_now(),
                title=entry["title"],
                detail=entry.get("detail", ""),
                parent=parent or 0,
                archived=bool(entry.get("archived")),
            )
            created.append(view["id"])
            ids[entry["ref"]] = known[path] = view["id"]
            done += 1
    return done


def _depth(entry: dict, by_ref: dict) -> int:
    depth, seen, cursor = 0, set(), entry
    while cursor is not None and cursor["ref"] not in seen:
        seen.add(cursor["ref"])
        depth += 1
        cursor = by_ref.get(cursor.get("parent"))
    return depth


def _write_control(db, workspace_id, board, content, user, target, created) -> dict[str, int]:
    """Routines and seats in one control-db transaction; ``created`` gets the new ids."""
    now = time.time()
    have = {str(r["name"]).casefold() for r in target.routines}
    prepared = []
    for entry in content.get("routines", []):
        name = routines.check_name(entry["name"])
        if name.casefold() in have:
            continue
        have.add(name.casefold())
        template = routines.template_fields(entry["template"])
        template["board"] = wt.routine_board(template, target)
        tz = entry.get("timezone", "UTC")
        prepared.append(
            (
                name,
                template,
                routines.check_schedule(entry["schedule"], tz, now),
                tz,
                entry.get("state", "active")
                if entry.get("state") in ("active", "paused")
                else "active",
            )
        )
    have_roles = {s.get("role") for s in target.seats}
    stamp = routines.stamp(now)
    counts = {"routines": 0, "seats": 0}
    with db._tx() as conn:
        for name, template, schedule, tz, state in prepared:
            created["routines"].append(
                routines.insert_row(
                    db,
                    workspace_id,
                    owner=user["id"],
                    created_by=user["id"],
                    name=name,
                    template=template,
                    schedule=schedule,
                    tz=tz,
                    now=now,
                    state=state,
                    conn=conn,
                )
            )
            counts["routines"] += 1
        for entry in content.get("seats", []):
            role = entry["role"]
            if role in have_roles:
                continue
            have_roles.add(role)
            cursor = conn.execute(
                "INSERT OR IGNORE INTO project_orchestrators "
                "(workspace_id, board, role, name, connection_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?)",
                (workspace_id, board, role, entry["name"], stamp, stamp),
            )
            if cursor.rowcount:
                created["seats"].append((board, role))
                counts["seats"] += 1
    return {k: v for k, v in counts.items() if v}


def _compensate(db, directory, workspace_id, created: dict) -> list[str]:
    """Remove only what this import created; returns the steps that could not be undone."""
    failed = []
    try:
        if created["goals"]:
            with BoardStore(directory).transaction() as conn:
                for ident in sorted(created["goals"], reverse=True):
                    conn.execute("DELETE FROM goals WHERE id=?", (ident,))
    except Exception:
        failed.append("goals")
    try:
        with db._tx() as conn:
            for ident in created["routines"]:
                conn.execute(
                    "DELETE FROM routines WHERE id=? AND workspace_id=?", (ident, workspace_id)
                )
            for board, role in created["seats"]:
                conn.execute(
                    "DELETE FROM project_orchestrators WHERE workspace_id=? AND board=? AND role=? "
                    "AND connection_id IS NULL",
                    (workspace_id, board, role),
                )
    except Exception:
        failed.append("routines_and_seats")
    return failed


def apply(
    db,
    workspace_id: str,
    directory,
    package,
    *,
    board: str | None,
    preview_id: str,
    actor: str,
    include_memory: bool = False,
) -> dict:
    """Write ``package`` into the workspace; see the module docstring for the guarantees."""
    from commonplace.orch import cli as orch_cli, store as orch_store

    user = _owner(db, workspace_id, actor)
    content = package.get("content") if isinstance(package, dict) else None
    if not isinstance(content, dict):
        raise TemplateError("package_invalid", "not a workspace template package")
    found = wt.scan(content) + wt.scan(package.get("redactions", []), "redactions")
    if found:
        paths = ", ".join(sorted({f["path"] for f in found})[:10])
        raise TemplateError("secret_found", f"the package holds credential-shaped values: {paths}")
    if "memory" in content and include_memory:
        raise TemplateError("memory_unavailable", "this workspace has no memory writer for imports")
    target = wt.read_source(db, workspace_id, directory, board)
    result = wt.preview(package, target, include_memory=include_memory)
    if not result["ok"]:
        raise TemplateError("preview_refused", _codes(result))
    if not isinstance(preview_id, str) or preview_id != result["preview_id"]:
        raise TemplateError(
            "preview_stale",
            "the package or the workspace changed since this preview; preview again",
        )
    routines.ensure_schema(db)  # idempotent: the stores a write needs exist before it starts
    seats.ensure_schema(db)
    plan = wt.plan_proposal(content, target)
    if plan is not None and plan["sha256"] in target.proposed_shas:
        plan = None

    created: dict = {"goals": [], "routines": [], "seats": []}
    applied: dict[str, int] = {}
    version = None
    try:
        count = _write_goals(directory, content, user["id"], created["goals"])
        if count:
            applied["goals"] = count
        applied.update(_write_control(db, workspace_id, board, content, user, target, created))
        if plan is not None:
            principal = orch_cli.local_principal(user, workspace_id)
            names = [r["name"] for r in db.connections(workspace_id) if not r["revoked_at"]]
            version = orch_store.Store(directory).propose(
                principal,
                board,
                plan["text"],
                connections=names,
                sha256=plan["sha256"],
                source_ref=f"template:{package['digest'][:16]}",
            )
            applied["roles"] = len(plan["roles"])
    except BaseException:
        failed = _compensate(db, directory, workspace_id, created)
        if failed:
            logger.error("template import rollback incomplete: %s", ", ".join(failed))
        raise
    db.audit(
        action="template.import",
        decision="ok",
        workspace_id=workspace_id,
        user_id=user["id"],
        reason=f"digest:{package['digest'][:16]} "
        + " ".join(f"{k}:{v}" for k, v in applied.items()),
    )
    return {
        "ok": True,
        "applied": applied,
        "skipped": _skipped(result["collisions"]),
        "dropped": result["dropped"],
        "proposal": (
            {
                "board": version.board,
                "ref": version.ref,
                "status": version.status,
                "sha256": version.sha256,
                "note": "proposal only: an owner accepts it by its sha256; no runner or trigger grant was created",
            }
            if version is not None
            else None
        ),
        "preview_id": preview_id,
    }
