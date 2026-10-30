"""The handoff itself: one call from the client hook. Owner: part T07.

``run`` order (every step idempotent, see PLAN.md):
1. settings off → ``HandoffResult("compact", reason="off")``; nothing stored.
2. ``HandoffDB.begin`` by idempotency key (default ``thread:<thread_id>``);
   an existing non-failed handoff is returned as-is with ``launch=False``
   (unless this is the first launch claim, see 6).
3. parse transcript, store it (``ThreadStore.store``). Any failure →
   ``mark_failed`` + ``EVENT_FAILED`` + ``HandoffResult("compact",
   reason=...)``: no successor ever starts without a stored thread.
4. palace mirror (best effort).
5. build the doc (``writer``) with the chain (parent handoff's stored ids),
   ``mark_stored``, ``EVENT_STORED``.
6. ``claim_launch``: only the winner launches. ``client`` launcher: return
   ``launch=True`` and the prompt. Other launchers: launch here, then
   ``set_successor`` + ``EVENT_COMPLETED``, or ``mark_launch_failed`` +
   ``EVENT_LAUNCH_FAILED`` (the thread stays stored; the agent is told to
   stop anyway, since its work is safe in Memory).
"""

from __future__ import annotations

from .models import Handoff, HandoffRequest, HandoffResult, SuccessorRef


class HandoffService:
    def __init__(
        self,
        *,
        control_db,
        handoff_db,
        settings,
        store_for,
        events,
        call_tool=None,
        prompt_dir: str = "",
    ):
        raise NotImplementedError

    def run(self, principal, request: HandoffRequest) -> HandoffResult:
        raise NotImplementedError

    def register_successor(self, principal, handoff_id: str, ref: SuccessorRef) -> Handoff:
        raise NotImplementedError

    def report_launch_failure(self, principal, handoff_id: str, error: str) -> Handoff:
        """The client launcher couldn't start the successor. Same permission
        as ``register_successor``; stored/launched → ``launch_failed``,
        ``EVENT_LAUNCH_FAILED`` once (repeats are no-ops); ``error`` is cut
        to 200 chars and goes to the event reason only."""
        raise NotImplementedError

    def relaunch(self, principal, handoff_id: str) -> HandoffResult:
        raise NotImplementedError
