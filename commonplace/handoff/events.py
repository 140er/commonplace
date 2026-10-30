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
        contain verbatim user data.
        """
        try:
            if event not in EVENTS:
                _LOG.warning("Unknown handoff event ignored")
                return
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
        except Exception:
            _LOG.error("Handoff event preparation failed")
            return

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
            hub_events.emit(
                self.control_db,
                workspace_id,
                event,
                subject=subject,
                actor=getattr(principal, "actor", "") or "",
                data=data,
            )
        except Exception:
            _LOG.error("Handoff event write failed")
