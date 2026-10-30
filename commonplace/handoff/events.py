"""Audit entries and hub events for handoffs.

``emit`` writes one audit entry (``action`` = the event name, ids only) and
appends one event to the hub's event log (``commonplace.events``), where the
web app and the hub's signed webhooks pick it up (subscribe with
``handoff.*`` kinds or the ``handoff`` preset). Never thread text. Never
blocks a request on HTTP.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .models import EVENT_FAILED, EVENT_LAUNCH_FAILED, EVENTS

_LOG = logging.getLogger(__name__)
_FAILURE_REASONS = {
    EVENT_FAILED: "thread storage failed",
    EVENT_LAUNCH_FAILED: "successor launch failed",
}
_SAFE_REASONS = frozenset(
    (*_FAILURE_REASONS.values(), "handoff failed", "not found", "off", "invalid settings")
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_reason(event: str, reason: str) -> str:
    # Callers may pass exception messages containing the original transcript.
    # Only known diagnostics may leave the machine or enter the audit log.
    if reason in _SAFE_REASONS:
        return reason[:200]
    if reason or event in _FAILURE_REASONS:
        return _FAILURE_REASONS.get(event, "handoff failed")
    return ""


class Events:
    def __init__(self, control_db, handoff_db):
        self.control_db = control_db
        self.handoff_db = handoff_db

    def emit(
        self,
        event: str,
        *,
        workspace_id: str,
        handoff=None,
        principal=None,
        decision: str = "ok",
        reason: str = "",
        **ids,
    ) -> None:
        """Record ids only, independently attempting the audit entry and the event.

        Unknown reasons become controlled diagnostics, since exception text can
        contain verbatim user data. Never raises.
        """
        try:
            self._write(
                event,
                workspace_id=workspace_id,
                handoff=handoff,
                principal=principal,
                decision=decision,
                reason=reason,
                strict=False,
                **ids,
            )
        except Exception:
            _LOG.error("Handoff event preparation failed")

    def deliver_pending(self, *, handoff_id: str = "", workspace_id: str = "") -> int:
        """Deliver queued events (``HandoffDB.set_successor`` queues
        ``handoff.completed``): the audit entry,
        the hub event and the delivered mark commit together, so each one is
        recorded exactly once, also across gateways on one control database
        and across restarts. A failure rolls back and leaves the row for the
        next try. Returns how many were delivered. Never raises."""
        from commonplace import events as hub_events

        delivered = 0
        try:
            # executescript commits: never inside the delivery transaction.
            hub_events.ensure_schema(self.control_db)
            rows = self.handoff_db.pending_events(handoff_id=handoff_id, workspace_id=workspace_id)
        except Exception:
            _LOG.error("Handoff outbox read failed")
            return 0
        for row in rows:
            try:
                with self.control_db._lock, self.control_db._tx() as conn:
                    fresh = conn.execute(
                        "SELECT delivered_at FROM handoff_outbox WHERE id = ?", (row["id"],)
                    ).fetchone()
                    if fresh is None or fresh["delivered_at"]:
                        continue
                    handoff = self.handoff_db.get(row["handoff_id"])
                    stored = self._write(
                        row["event"],
                        workspace_id=row["workspace_id"],
                        handoff=handoff,
                        principal=_Recorded(row),
                        strict=True,
                    )
                    conn.execute(
                        "UPDATE handoff_outbox SET delivered_at = ?, attempts = attempts + 1 "
                        "WHERE id = ?",
                        (_iso(_now()), row["id"]),
                    )
                delivered += 1
            except Exception:
                _LOG.error("Handoff event delivery failed; it stays queued")
                try:
                    self.handoff_db.event_attempted(row["id"])
                except Exception:
                    pass
                continue
            # Listeners (webhooks) run after the commit, never inside it. A
            # missed notice is caught up from the event log by the dispatcher.
            if stored is not None:
                try:
                    hub_events.notify(stored)
                except Exception:
                    _LOG.error("Handoff event listener failed")
        return delivered

    def _write(
        self,
        event: str,
        *,
        workspace_id: str,
        handoff=None,
        principal=None,
        decision: str = "ok",
        reason: str = "",
        strict: bool,
        **ids,
    ):
        """``strict``: raise on failure and record the hub event without
        telling its listeners (the caller's transaction is open); return the
        stored hub event for ``events.notify`` after commit."""
        if event not in EVENTS:
            _LOG.warning("Unknown handoff event ignored")
            return None
        successor = getattr(handoff, "successor", None)
        payload = {
            "event": event,
            "workspace": workspace_id,
            "handoff_id": getattr(handoff, "id", "") or ids.get("handoff_id", ""),
            "stored_thread_id": getattr(handoff, "stored_thread_id", "")
            or ids.get("stored_thread_id", ""),
            "task_ref": getattr(handoff, "task_ref", "") or ids.get("task_ref", ""),
            "successor_agent_ref": getattr(successor, "agent_ref", "")
            or ids.get("successor_agent_ref", ""),
            "at": _iso(_now()),
        }
        safe_reason = _safe_reason(event, reason)
        if event in _FAILURE_REASONS or decision != "ok":
            payload.update(decision=decision, reason=safe_reason)

        try:
            self.control_db.audit(
                action=event,
                decision=decision,
                workspace_id=workspace_id,
                user_id=getattr(principal, "user_id", None),
                connection_id=getattr(principal, "connection_id", None)
                or getattr(handoff, "connection_id", None),
                auth=getattr(principal, "auth", None),
                tool=None,
                reason=safe_reason,
                target_ids=[
                    payload["handoff_id"],
                    payload["stored_thread_id"],
                    payload["successor_agent_ref"],
                ],
            )
        except Exception:
            if strict:
                raise
            _LOG.error("Handoff audit write failed")

        try:
            from commonplace import events as hub_events

            subject = payload["task_ref"] or payload["handoff_id"]
            data = {
                key: payload[key]
                for key in (
                    "handoff_id",
                    "stored_thread_id",
                    "task_ref",
                    "successor_agent_ref",
                    "decision",
                    "reason",
                )
                if payload.get(key)
            }
            if payload["task_ref"].lstrip("#").isdigit():
                data["number"] = int(payload["task_ref"].lstrip("#"))
            write = hub_events.record if strict else hub_events.emit
            return write(
                self.control_db,
                workspace_id,
                event,
                subject=subject,
                actor=getattr(principal, "actor", "") or "",
                data=data,
            )
        except Exception:
            if strict:
                raise
            _LOG.error("Handoff event write failed")
            return None


class _Recorded:
    """The caller of a queued event, as recorded when it was queued."""

    def __init__(self, row):
        self.user_id = row["user_id"] or None
        self.connection_id = row["connection_id"] or None
        self.auth = row["auth"] or None
        self.actor = row["actor"] or ""
