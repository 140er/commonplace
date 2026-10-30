"""Handoff tables in the control database. Owner: part T04.

Uses the ``ControlDB``'s connection, lock and ``_tx()``; tables are created
on construction (``CREATE TABLE IF NOT EXISTS``), so old control DBs upgrade
in place. Ids and settings only, never thread text (that is in
``thread_store``).

Tables: ``handoff_settings`` (unique workspace_id+scope+ref),
``handoffs`` (unique workspace_id+idempotency_key; ``launch_claimed`` flag),
``handoff_webhooks``, ``handoff_outbox``.
"""

from __future__ import annotations

from typing import Optional

from .models import Handoff, SettingRow, SuccessorRef, ThreadMeta


class HandoffDBError(ValueError):
    pass


class HandoffDB:
    def __init__(self, control_db):
        raise NotImplementedError

    # ── settings ──
    def setting_rows(
        self, workspace_id: str, agent: str = "", task_ref: str = ""
    ) -> list[SettingRow]:
        """The workspace row plus the agent and task rows that exist, in that order."""
        raise NotImplementedError

    def list_settings(self, workspace_id: str) -> list[SettingRow]:
        raise NotImplementedError

    def put_setting(self, row: SettingRow, *, actor: str) -> SettingRow:
        """Insert or replace one row; ``updated_by``/``updated_at`` stamped here."""
        raise NotImplementedError

    def delete_setting(self, workspace_id: str, scope: str, ref: str, *, actor: str) -> bool:
        raise NotImplementedError

    # ── handoffs ──
    def begin(
        self, workspace_id: str, idempotency_key: str, meta: ThreadMeta, *, connection_id: str
    ) -> tuple[Handoff, bool]:
        """Create the ``pending`` row, or return the existing one. ``(handoff, created)``.
        Two concurrent calls with one key: exactly one gets ``created=True``.
        A row in state ``failed`` is reused (reset to pending, created=True) so
        a retry after a store failure can try again."""
        raise NotImplementedError

    def mark_stored(self, handoff_id: str, stored_thread_id: str, doc_markdown: str) -> Handoff:
        raise NotImplementedError

    def mark_failed(self, handoff_id: str, error: str) -> Handoff:
        raise NotImplementedError

    def claim_launch(self, handoff_id: str) -> bool:
        """True exactly once per handoff, ever (atomic compare-and-set)."""
        raise NotImplementedError

    def mark_launched(self, handoff_id: str) -> Handoff:
        raise NotImplementedError

    def mark_launch_failed(self, handoff_id: str, error: str) -> Handoff:
        raise NotImplementedError

    def release_launch(self, handoff_id: str, *, actor: str) -> Handoff:
        """For ``relaunch``: only from ``launch_failed``; clears the claim."""
        raise NotImplementedError

    def set_successor(self, handoff_id: str, ref: SuccessorRef) -> Handoff:
        """Record the successor, state ``done``. Same ref again: no-op.
        A different ref when one is set: ``HandoffDBError``."""
        raise NotImplementedError

    def get(self, handoff_id: str) -> Optional[Handoff]:
        raise NotImplementedError

    def by_stored_thread(self, stored_thread_id: str) -> Optional[Handoff]:
        raise NotImplementedError

    def by_successor_thread(self, workspace_id: str, thread_id: str) -> Optional[Handoff]:
        raise NotImplementedError

    def list(self, workspace_id: str, *, task_ref: str = "", limit: int = 50) -> list[Handoff]:
        raise NotImplementedError

    # ── webhooks ──
    def add_webhook(
        self, workspace_id: str, url: str, secret: str, events: tuple, *, actor: str
    ) -> str:
        raise NotImplementedError

    def webhooks(self, workspace_id: str) -> list[dict]:
        """``{id, url, events, created_at}``; never the secret."""
        raise NotImplementedError

    def webhook_secret(self, webhook_id: str) -> str:
        raise NotImplementedError

    def remove_webhook(self, webhook_id: str, *, actor: str) -> bool:
        raise NotImplementedError

    # ── outbox ──
    def enqueue(self, workspace_id: str, event: str, payload: dict) -> list[int]:
        """One outbox row per matching webhook. Payload: ids only."""
        raise NotImplementedError

    def due(self, now_iso: str, limit: int = 50) -> list[dict]:
        raise NotImplementedError

    def mark_delivered(self, outbox_id: int) -> None:
        raise NotImplementedError

    def mark_retry(
        self, outbox_id: int, error: str, next_at_iso: str, *, give_up: bool = False
    ) -> None:
        raise NotImplementedError
