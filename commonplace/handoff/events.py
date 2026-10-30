"""Audit entries and wake webhooks for handoffs. Owner: part T13.

``emit`` writes one audit entry (``action`` = the event name, ids only) and
enqueues one outbox row per webhook subscribed to the event. ``Outbox``
delivers in a background thread: POST JSON ``{event, workspace, handoff_id,
stored_thread_id, task_ref, successor_agent_ref, at}`` with
``X-Commonplace-Event`` and ``X-Commonplace-Signature: sha256=<hmac of
body>``; retries with backoff (1 s, 5 s, 30 s, 2 min, 10 min) then gives up.
Never sends thread text. Never blocks a request.
"""

from __future__ import annotations


class Events:
    def __init__(self, control_db, handoff_db):
        raise NotImplementedError

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
        raise NotImplementedError


class Outbox:
    def __init__(self, handoff_db, *, poll_s: float = 1.0, post=None):
        """``post(url, body_bytes, headers) -> status``; default uses http.client."""
        raise NotImplementedError

    def deliver_once(self) -> int:
        """Deliver due rows; return how many were attempted."""
        raise NotImplementedError

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError
