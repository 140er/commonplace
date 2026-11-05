"""Bounded, best-effort search backfill on the handoff mirror executor."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from commonplace import continuity_read, plans, sessions

from . import palace_mirror

MAX_THREADS = 20
MAX_UNITS = 2000
PAUSE_SECONDS = 1
STORAGE_MARGIN = 16 * 1024 * 1024
logger = logging.getLogger(__name__)


def enabled():
    return os.environ.get("COMMONPLACE_SEARCH_INDEX") != "off"


@dataclass
class State:
    state: str = "idle"
    units_written: int = 0
    skipped: int = 0
    after_id: str = ""
    dirty: bool = False
    # Successful batches of an unfinished thread, keyed by content digest.
    # Replaying these results lets a single large thread span bounded jobs.
    batches: dict = field(default_factory=dict)
    enrolled: bool = False


class _JobFull(Exception):
    pass


def import_groups(db, workspace_id):
    """Read only control metadata; never mutate continuity rows."""
    with db._lock:
        rows = db._conn.execute(
            "SELECT g.holder_stored_id, s.fork_stored_id FROM sync_streams s "
            "JOIN sync_groups g ON g.workspace_id=s.workspace_id AND g.group_id=s.group_id "
            "WHERE s.workspace_id=? AND s.kind='import'",
            (workspace_id,),
        ).fetchall()
    return {value for row in rows for value in row if value}


def principal_for(db, stored):
    filer = continuity_read.filing_user(db, stored)
    if stored.source_thread_id.startswith("import-private:"):
        filer = stored.source_thread_id.split(":", 2)[1]
    if filer:
        try:
            principal = sessions.person_principal(db, filer, stored.workspace_id)
            if principal is not None:
                return principal
        except Exception:
            pass
    with db._lock:
        owners = db._conn.execute(
            "SELECT user_id FROM memberships WHERE workspace_id=? AND role='owner' ORDER BY user_id",
            (stored.workspace_id,),
        ).fetchall()
    for owner in owners:
        try:
            principal = sessions.person_principal(db, owner[0], stored.workspace_id)
            if principal is not None:
                return principal
        except Exception:
            pass
    return None


def quota_ok(service, workspace_id):
    if not plans.enabled():
        return True
    account = plans.workspace_account(service.control_db, workspace_id)
    plan = plans.get_plan(service.control_db, account) if account else None
    if plan is None or plan.storage_bytes is None:
        return True
    from commonplace.workspace_store import workspace_path

    # store_for is the bound HandoffContext method in the gateway.
    context = getattr(service.store_for, "__self__", None)
    root = getattr(context, "data_dir", Path(service.control_db.path).parent)
    return plans.StorageMeter._walk(workspace_path(root, workspace_id)) < (
        plan.storage_bytes - STORAGE_MARGIN
    )


def sweep(service, workspace_id, state):
    """Return whether another bounded job is needed. Never log user content."""
    written = 0
    visited = 0
    deferred = False
    if state.dirty:
        state.after_id = ""
        state.dirty = False
    try:
        if not enabled():
            state.state = "off"
            return False
        store = service.store_for(workspace_id)
        if not state.enrolled:
            for stored_id in import_groups(service.control_db, workspace_id):
                stored = store.get(stored_id)
                if stored is not None and stored.workspace_id == workspace_id:
                    store.begin_mirror(stored_id, -1, bootstrap=-1)
            state.enrolled = True
        threads = store.index_backlog(workspace_id, after_id=state.after_id, limit=MAX_THREADS)
        for stored in threads:
            if not enabled():
                state.state = "off"
                return False
            principal = principal_for(service.control_db, stored)
            if principal is None:
                state.skipped += 1
                state.after_id = stored.id
                visited += 1
                continue
            if stored.id not in state.batches and len(state.batches) >= 4:
                state.batches.pop(next(iter(state.batches)))
            cached = state.batches.setdefault(stored.id, {})

            def allowed():
                nonlocal deferred
                if not quota_ok(service, workspace_id):
                    deferred = True
                    return False
                return True

            def call(principal, name, arguments, cached=cached):
                nonlocal written
                if name != "write_mirror_units":
                    return service.call_tool(principal, name, arguments)
                key = hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).digest()
                if key in cached:
                    return cached[key]
                count = len(arguments["units"])
                if written + count > MAX_UNITS:
                    raise _JobFull()
                result = service.call_tool(principal, name, arguments)
                ids = result.get("drawer_ids") if isinstance(result, dict) else None
                if (
                    isinstance(ids, list)
                    and len(ids) == count
                    and all(isinstance(i, str) and i for i in ids)
                    and not result.get("error")
                    and result.get("success") is not False
                    and not result.get("isError")
                ):
                    cached[key] = result
                    written += count
                    state.units_written += count
                return result

            try:
                palace_mirror.mirror_pending(
                    call, principal, stored, store, skip_empty=True, should_continue=allowed
                )
            except palace_mirror.MirrorError as exc:
                if not isinstance(exc.__cause__, _JobFull):
                    state.batches.pop(stored.id, None)
                if deferred:
                    state.state = "deferred_storage"
                    return False
                if isinstance(exc.__cause__, _JobFull):
                    return True
                # A failed thread remains pending for the next external trigger.
            else:
                state.batches.pop(stored.id, None)
            state.after_id = stored.id
            visited += 1
        if len(threads) == MAX_THREADS or state.dirty:
            return True
        state.after_id = ""
        state.state = "idle"
        return False
    except Exception:
        state.batches.clear()
        state.state = "idle"
        return False
    finally:
        logger.info("search index job threads=%d units=%d state=%s", visited, written, state.state)
