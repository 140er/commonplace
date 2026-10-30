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

import hashlib
import hmac
import http.client
import json
import logging
import math
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from .models import EVENT_FAILED, EVENT_LAUNCH_FAILED, EVENTS

_LOG = logging.getLogger(__name__)
_BACKOFF = (1, 5, 30, 120, 600)
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


def _webhook_url(url: str):
    if not isinstance(url, str) or any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise ValueError("invalid webhook URL")
    parts = urlsplit(url)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or (parts.port is not None and not 1 <= parts.port <= 65535)
    ):
        raise ValueError("invalid webhook URL")
    return parts


def _post(url: str, body_bytes: bytes, headers: dict) -> int:
    """One request, with a five-second socket timeout and no redirects."""
    parts = _webhook_url(url)
    connection_type = (
        http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    )
    connection = connection_type(parts.hostname, parts.port, timeout=5.0)
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    try:
        connection.request("POST", target, body=body_bytes, headers=headers)
        response = connection.getresponse()
        try:
            return response.status
        finally:
            response.close()
    finally:
        connection.close()


class Events:
    def __init__(self, control_db, handoff_db):
        self.control_db = control_db
        self.handoff_db = handoff_db
        self.outbox = None

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
        """Record ids only, independently attempting audit and enqueue.

        Unknown reasons become controlled diagnostics, since exception text can
        contain verbatim user data. Assign ``outbox`` to wake its delivery loop.
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
            enqueued = self.handoff_db.enqueue(workspace_id, event, payload)
        except Exception:
            _LOG.error("Handoff webhook enqueue failed")
            return
        if enqueued and self.outbox is not None:
            try:
                self.outbox.notify()
            except Exception:
                _LOG.error("Handoff outbox notification failed")


class Outbox:
    def __init__(self, handoff_db, *, poll_s: float = 1.0, post=None):
        """``post(url, body_bytes, headers) -> status``; default uses http.client.

        ``clock`` can be assigned a callable returning a UTC datetime for tests.
        Use one Outbox per database: the database contract has no delivery claim.
        """
        if not math.isfinite(poll_s) or poll_s <= 0:
            raise ValueError("poll_s must be positive and finite")
        self.handoff_db = handoff_db
        self.poll_s = poll_s
        self.post = post if post is not None else _post
        self.clock = _now
        self._stopping = threading.Event()
        self._wake = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._delivery_lock = threading.Lock()
        self._thread = None

    def deliver_once(self) -> int:
        """Deliver due rows; return how many were attempted."""
        if not self._delivery_lock.acquire(blocking=False):
            return 0
        try:
            try:
                rows = self.handoff_db.due(_iso(self.clock()), limit=50)
            except Exception:
                _LOG.error("Handoff outbox query failed")
                return 0
            attempted = 0
            for row in rows:
                if self._stopping.is_set():
                    break
                attempted += 1
                try:
                    self._deliver(row)
                except Exception:
                    # A broken row or a failed acknowledgement must not prevent
                    # delivery of the rest. Never log URLs, payloads or errors.
                    _LOG.error("Handoff outbox row processing failed")
            return attempted
        finally:
            self._delivery_lock.release()

    def _deliver(self, row: dict) -> None:
        try:
            _webhook_url(row["url"])
        except (TypeError, ValueError):
            self._retry(row, "invalid webhook URL", give_up=True)
            return
        try:
            secret = self.handoff_db.webhook_secret(row["webhook_id"])
        except Exception:
            self._retry(row, "webhook delivery failed")
            return
        if not secret:
            self._retry(row, "webhook secret unavailable", give_up=True)
            return
        try:
            body = json.dumps(row["payload"], sort_keys=True, separators=(",", ":")).encode("utf-8")
            signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            headers = {
                "Content-Type": "application/json",
                "X-Commonplace-Event": row["event"],
                "X-Commonplace-Delivery": str(row["id"]),
                "X-Commonplace-Signature": "sha256=" + signature,
            }
            status = self.post(row["url"], body, headers)
            delivered = 200 <= status < 300
        except Exception:
            self._retry(row, "webhook delivery failed")
            return
        if delivered:
            self.handoff_db.mark_delivered(row["id"])
        else:
            self._retry(row, f"HTTP {status}", give_up=status == 410)

    def _retry(self, row: dict, error: str, *, give_up: bool = False) -> None:
        attempts = row["attempts"]  # Failed attempts before this delivery, initially zero.
        give_up = give_up or attempts >= len(_BACKOFF)
        delay = 0 if give_up else _BACKOFF[attempts]
        self.handoff_db.mark_retry(
            row["id"], error, _iso(self.clock() + timedelta(seconds=delay)), give_up=give_up
        )

    def notify(self) -> None:
        """Wake the polling loop after new deliveries have been committed."""
        self._wake.set()

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopping.clear()
            self._thread = threading.Thread(target=self._run, name="handoff-outbox", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while not self._stopping.is_set():
            self._wake.clear()
            try:
                self.deliver_once()
            except Exception:
                _LOG.error("Handoff outbox polling failed")
            if not self._stopping.is_set():
                self._wake.wait(self.poll_s)

    def stop(self) -> None:
        """Return within two seconds; an in-flight daemon request may finish later.

        A subsequent start cannot create a second worker until that one exits.
        Injected post callables should enforce their own request timeout.
        """
        with self._lifecycle_lock:
            self._stopping.set()
            self._wake.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
