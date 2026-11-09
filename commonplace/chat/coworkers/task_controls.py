"""Controls for an existing canonical origin, never a second task/run registry.

The coworker receipt is a conservative one-attempt outbox across two stores.
Only canonical runner cancellation can fence admission. Its ledger currently
has no ongoing-worker stop/follow-up or termination-evidence protocol. Preserve
follow-ups as exact existing task comments and report unsupported application.
Never resolve uncertain starts, approve cards, or launch replacement workers.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from commonplace.boards.service import BoardService
from commonplace.boards.store import task_row
from commonplace.control_db import ControlError
from commonplace.extensions import ToolContext, ToolError
from commonplace.runner import jobs

from .store import StoreError
from .task_progress import _authority, _read


def _bound_board(conn, job, binding):
    # Reuse the service operation while fencing its exact origin in the SAME
    # boards snapshot/transaction. No control reads or caller-chosen references.
    row = conn.execute(
        "SELECT 1 FROM tasks t JOIN boards b ON b.id=t.board_id "
        "JOIN chat_task_origins o ON o.task_id=t.id WHERE t.id=? AND b.slug=? "
        "AND b.archived=0 AND o.actor=? AND o.origin_key=? AND o.request_digest=?",
        (
            binding["number"],
            binding["board"],
            "user:" + job["user_id"],
            f"coworker:{job['conversation_id']}:{job['id']}",
            binding["origin_digest"],
        ),
    ).fetchone()
    if row is None:
        raise StoreError("not_found", 404)


def _runner(authority, job, task):
    path = Path(authority.gateway.workspace_dir(authority.workspace_id)) / "runner.sqlite3"
    if not path.is_file():
        return None
    origin = f"coworker:{job['conversation_id']}:{job['id']}"
    with _read(path) as conn:
        row = conn.execute(
            "SELECT id,state,requested_by,request_key,request_hash,machine_id,binding_epoch "
            "FROM runner_jobs WHERE task_number=? AND board_slug=? AND requested_by=? "
            "AND request_key=? LIMIT 1",
            (
                task["number"],
                task["board"],
                "user:" + job["user_id"],
                "chat:" + hashlib.sha256(origin.encode()).hexdigest(),
            ),
        ).fetchone()
        return dict(row) if row is not None else None


def target(authority, job, task, user_id):
    """Internal binding from current authority, with no caller-supplied run ID."""
    try:
        path = Path(authority.gateway.workspace_dir(authority.workspace_id)) / "boards.sqlite3"
        principal = authority.user(user_id)
        if not principal.person or getattr(principal, "runner", None) is not None:
            raise StoreError("not_found", 404)
        service = BoardService(ToolContext(principal, authority.gateway), store=False)
        with _read(path) as conn:
            binding, _ = _authority(conn, authority, job, task, user_id)
            row = task_row(conn, binding["id"])
            board = service._board(conn, task["board"])
            service._write(board, row=row)
            digest = conn.execute(
                "SELECT request_digest FROM chat_task_origins WHERE actor=? AND origin_key=? AND task_id=?",
                (
                    "user:" + job["user_id"],
                    f"coworker:{job['conversation_id']}:{job['id']}",
                    binding["id"],
                ),
            ).fetchone()[0]
        task = {"board": task["board"], "number": binding["id"]}
        run = _runner(authority, job, task)
        if principal.role != "owner" and (
            run is None or run["requested_by"] != jobs.requester_ref(principal)
        ):
            raise StoreError("not_found", 404)
        result = {
            **task,
            "origin_digest": digest,
            "runner_job": run["id"] if run else None,
            **{
                key: run[key] if run else None
                for key in (
                    "requested_by",
                    "request_key",
                    "request_hash",
                    "machine_id",
                    "binding_epoch",
                )
            },
        }
        with _read(path) as conn:
            current, _ = _authority(conn, authority, job, task, user_id)
            if current != binding:
                raise StoreError("not_found", 404)
            _bound_board(conn, job, result)
        return result
    except StoreError:
        raise
    except (OSError, sqlite3.Error, ControlError, ToolError, ValueError, TypeError, KeyError):
        raise StoreError("not_found", 404) from None


def apply(authority, binding, operation, text, authorize):
    # All inputs here are from the validated durable receipt; model output and
    # conversation history never enter this path. Revalidate immediately before
    # invoking the existing canonical service, then Store fences publication.
    principal, job = authorize()
    if operation == "follow_up":
        service = BoardService(ToolContext(principal, authority.gateway))
        transaction = service.transaction

        @contextmanager
        def comment_transaction():
            service.principal = authorize()[0]
            with transaction() as conn:
                _bound_board(conn, job, binding)
                yield conn

        # Keep canonical validation, flags, append-only events and emission.
        # Only this short-lived service instance receives the extra origin fence.
        service.transaction = comment_transaction
        service.task_comment(binding["number"], text)
        return "unsupported", "comment_saved_application_unsupported"
    if binding["runner_job"] is None:
        return "unsupported", "no_originating_runner"
    path = Path(authority.gateway.workspace_dir(authority.workspace_id)) / "runner.sqlite3"
    if not path.is_file():
        raise StoreError("not_found", 404)
    store = jobs.open_store(authority.gateway, authority.workspace_id)
    transaction = store.transaction

    @contextmanager
    def cancel_transaction():
        authorize()
        with transaction() as conn:
            row = conn.execute(
                "SELECT * FROM runner_jobs WHERE id=?", (binding["runner_job"],)
            ).fetchone()
            if row is None or any(
                row[column] != binding[key]
                for column, key in (
                    ("task_number", "number"),
                    ("board_slug", "board"),
                    ("requested_by", "requested_by"),
                    ("request_hash", "request_hash"),
                    ("request_key", "request_key"),
                    ("machine_id", "machine_id"),
                    ("binding_epoch", "binding_epoch"),
                )
            ):
                raise StoreError("not_found", 404)
            board_path = (
                Path(authority.gateway.workspace_dir(authority.workspace_id)) / "boards.sqlite3"
            )
            with _read(board_path) as boards:
                _bound_board(boards, job, binding)
            yield conn

    store.transaction = cancel_transaction
    # Opening/migrating the canonical store cannot authorize a stale identity.
    principal, job = authorize()
    try:
        result = jobs.cancel(store, binding["runner_job"], principal)
    except jobs.Conflict:
        authorize()
        result = _runner(authority, job, binding)
        if result is None or result["id"] != binding["runner_job"]:
            raise StoreError("not_found", 404) from None
        if result["state"] == "started":
            return "unsupported", "worker_stop_unsupported"
        if result["state"] == "failed":
            return "unsupported", "runner_start_finished"
        if result["state"] != "cancelled":
            return "uncertain", "runner_outcome_unknown"
    if result["state"] == "cancelled":
        return "applied", "queued_start_cancelled"
    # A starting request is only intent. No existing worker termination evidence
    # is available in this ledger, including when the front turn/task is done.
    return "received", "stop_requested_termination_unconfirmed"
