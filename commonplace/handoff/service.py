"""The handoff itself: one call from the client hook. Owner: part T10.

Hook contract: ``action=handoff`` means the thread is safely stored, so the
agent may stop. ``action=compact`` means it must keep working or compact;
in particular, another request's pending handoff is not safe to stop for.

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

The current DB contract does not persist settings or launch metadata. Keep
the begin-time settings, metadata and doc for retries in this service's
lifetime. A different instance or restarted process with settings now off
returns compact/off even for an existing stored handoff: it cannot look up
the saved setting before the off gate. When settings remain on, retries
reconstruct the doc and use the current launcher; explicit owner relaunch
also reconstructs it regardless of the toggle. Durable snapshots and a
read-only lookup by workspace/key are required for correct retry behavior.

Successor registration uses the control DB's shared lock around read,
set_successor and event emission. This deduplicates completion events for
services sharing that ControlDB, but does not guarantee delivery: if emit
fails after set_successor, an idempotent repeat does not retry the event.
An atomic transition indicator prevents duplicates across processes;
transactional audit/outbox writes are needed for durable delivery.

Launch transitions use the same shared lock to preserve a concurrently
completed handoff. The DB stubs cannot enforce that guard across processes:
mark_launched and mark_launch_failed need conditional updates checking the
expected state, claim identity and absence of a successor.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from . import launchers, palace_mirror, thread_store, transcripts, writer
from .db import HandoffDBError
from .models import (
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_LAUNCH_FAILED,
    EVENT_STARTED,
    EVENT_STORED,
    Handoff,
    HandoffDoc,
    HandoffRequest,
    HandoffResult,
    HandoffSettings,
    SuccessorRef,
    ThreadMeta,
)


@dataclass(frozen=True)
class _Snapshot:
    settings: HandoffSettings
    meta: ThreadMeta
    doc: Optional[HandoffDoc] = None


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
        self.control_db = control_db
        self.db = handoff_db
        self.settings = settings
        self.store_for = store_for
        self.events = events
        self.call_tool = call_tool
        self.prompt_dir = prompt_dir
        self._snapshots: dict[tuple[str, str], _Snapshot] = {}

    def run(self, principal, request: HandoffRequest) -> HandoffResult:
        meta = request.meta
        if not meta.thread_id or not meta.thread_id.strip():
            raise ValueError("thread_id must not be empty")
        workspace_id = principal.workspace_id
        key = request.idempotency_key or f"thread:{meta.thread_id}"
        current = self.settings.effective(
            workspace_id, meta.agent or principal.connection, meta.task_ref
        )
        with self.control_db._lock:
            snapshot = self._snapshots.get((workspace_id, key))
        settings = snapshot.settings if snapshot else current
        if not settings.enabled:
            # Turning handoff off later never strands a handoff already under way:
            # a stored thread still gets its one successor.
            existing = self.db.by_key(workspace_id, key)
            if existing is None or existing.state in ("failed", "pending"):
                return HandoffResult("compact", reason="off")
            settings = replace(current, enabled=True)

        try:
            handoff, created = self.db.begin(
                workspace_id, key, meta, connection_id=principal.connection_id
            )
        except Exception:
            reason = "thread storage failed"
            self._emit(EVENT_FAILED, principal, None, decision="error", reason=reason)
            return HandoffResult("compact", reason=reason)
        if not created:
            if handoff.state in ("launched", "done", "launch_failed"):
                return HandoffResult("handoff", handoff)
            if handoff.state != "stored":
                return HandoffResult("compact", handoff, reason="in progress")
            with self.control_db._lock:
                snapshot = self._snapshots.get((workspace_id, key))
            if snapshot is None:
                snapshot = _Snapshot(settings, self._meta(handoff))
            return self._launch(principal, handoff, snapshot)

        snapshot = _Snapshot(current, meta)
        with self.control_db._lock:
            self._snapshots[(workspace_id, key)] = snapshot
        self._emit(EVENT_STARTED, principal, handoff)
        try:
            messages = transcripts.parse(request.transcript, request.transcript_format)
            store = self.store_for(workspace_id)
            parent_id = ""
            if meta.parent_handoff_id:
                parent = self.db.get(meta.parent_handoff_id)
            else:
                # A successor that didn't inherit COMMONPLACE_HANDOFF_ID is still
                # linked when its own thread id was registered as the successor.
                parent = self.db.by_successor_thread(workspace_id, meta.thread_id)
            if parent and parent.workspace_id == workspace_id:
                parent_id = parent.stored_thread_id
            stored = store.store(
                workspace_id,
                meta.thread_id,
                messages,
                actor=principal.actor,
                parent_stored_id=parent_id,
            )

            if self.call_tool is not None:
                try:
                    palace_mirror.mirror(
                        self.call_tool,
                        principal,
                        stored,
                        thread_store.chunk_messages(messages),
                    )
                except Exception:
                    # The full thread is already safe in the lossless store.
                    pass

            doc = writer.build_doc(
                messages,
                meta,
                note=request.note,
                stored_thread_id=stored.id,
                handoff_id=handoff.id,
                chain=self._chain(store, parent_id, workspace_id, exclude=stored.id),
            )
            markdown = writer.render_markdown(doc)
            snapshot = replace(snapshot, doc=doc)
            with self.control_db._lock:
                self._snapshots[(workspace_id, key)] = snapshot
            handoff = self.db.mark_stored(handoff.id, stored.id, markdown)
        except Exception:
            reason = "thread storage failed"
            try:
                handoff = self.db.mark_failed(handoff.id, reason)
            except Exception:
                # A broken DB must still allow the calling agent to compact.
                pass
            with self.control_db._lock:
                self._snapshots.pop((workspace_id, key), None)
            self._emit(EVENT_FAILED, principal, handoff, decision="error", reason=reason)
            return HandoffResult("compact", handoff, reason=reason)

        self._emit(EVENT_STORED, principal, handoff)
        return self._launch(principal, handoff, snapshot)

    def register_successor(self, principal, handoff_id: str, ref: SuccessorRef) -> Handoff:
        with self.control_db._lock:
            handoff = self._authorized(principal, handoff_id)
            if handoff.state == "done" and handoff.successor == ref:
                return handoff
            if handoff.state not in ("stored", "launched"):
                raise HandoffDBError("handoff is not ready for a successor")
            handoff = self.db.set_successor(handoff_id, ref)
            self._emit(EVENT_COMPLETED, principal, handoff)
            return handoff

    def report_launch_failure(self, principal, handoff_id: str, error: str) -> Handoff:
        """The client launcher couldn't start the successor. Same permission
        as ``register_successor``; stored/launched → ``launch_failed``,
        ``EVENT_LAUNCH_FAILED`` once (repeats are no-ops); ``error`` is cut
        to 200 chars and goes to the event reason only."""
        with self.control_db._lock:
            handoff = self._authorized(principal, handoff_id)
            if handoff.state == "launch_failed":
                return handoff
            if handoff.state not in ("stored", "launched"):
                raise HandoffDBError("handoff is not ready to report a launch failure")
            handoff = self.db.mark_launch_failed(handoff_id, "successor launch failed")
            self._emit(
                EVENT_LAUNCH_FAILED, principal, handoff, decision="error", reason=error[:200]
            )
            return handoff

    def relaunch(self, principal, handoff_id: str) -> HandoffResult:
        with self.control_db._lock:
            handoff = self._authorized(principal, handoff_id, owner_only=True)
            if handoff.state != "launch_failed":
                raise HandoffDBError("handoff is not ready to relaunch")
            snapshot = self._snapshots.get((handoff.workspace_id, handoff.idempotency_key))
            if snapshot is None:
                snapshot = _Snapshot(
                    self.settings.effective(
                        handoff.workspace_id,
                        handoff.agent or principal.connection,
                        handoff.task_ref,
                    ),
                    self._meta(handoff),
                )
            handoff = self.db.release_launch(handoff.id, actor=principal.actor)
        return self._launch(principal, handoff, snapshot)

    def _launch(self, principal, handoff: Handoff, snapshot: _Snapshot) -> HandoffResult:
        claimed = False
        try:
            if not self.db.claim_launch(handoff.id):
                return HandoffResult("handoff", handoff)
            claimed = True
            doc = snapshot.doc
            if doc is None:
                store = self.store_for(handoff.workspace_id)
                doc = writer.build_doc(
                    store.messages(handoff.stored_thread_id),
                    snapshot.meta,
                    stored_thread_id=handoff.stored_thread_id,
                    handoff_id=handoff.id,
                    chain=self._chain(
                        store,
                        store.parent_of(handoff.stored_thread_id) or "",
                        handoff.workspace_id,
                        exclude=handoff.stored_thread_id,
                    ),
                )
            prompt = writer.successor_prompt(doc)
            with self.control_db._lock:
                current = self.db.get(handoff.id)
                if current is None or current.state != "stored" or current.successor is not None:
                    return HandoffResult("handoff", current or handoff)
                handoff = self.db.mark_launched(handoff.id)
            successor = launchers.launch(
                snapshot.settings.launcher, handoff, prompt, prompt_dir=self.prompt_dir
            )
            if successor is not None:
                handoff = self.register_successor(principal, handoff.id, successor)
            else:
                with self.control_db._lock:
                    handoff = self.db.get(handoff.id) or handoff
            client_launch = (
                snapshot.settings.launcher.kind == "client"
                and handoff.state == "launched"
                and handoff.successor is None
            )
            return HandoffResult(
                "handoff",
                handoff,
                launch=client_launch,
                prompt=prompt if client_launch else "",
            )
        except Exception:
            return self._launch_failure(principal, handoff, claimed=claimed)

    def _launch_failure(self, principal, handoff: Handoff, *, claimed: bool) -> HandoffResult:
        reason = "successor launch failed"
        try:
            with self.control_db._lock:
                current = self.db.get(handoff.id)
                if current is not None:
                    handoff = current
                if (
                    not claimed
                    or current is None
                    or current.state not in ("stored", "launched")
                    or current.successor is not None
                ):
                    return HandoffResult(
                        "handoff", handoff, reason="" if handoff.state == "done" else reason
                    )
                handoff = self.db.mark_launch_failed(handoff.id, reason)
                self._emit(EVENT_LAUNCH_FAILED, principal, handoff, decision="error", reason=reason)
        except Exception:
            pass
        return HandoffResult("handoff", handoff, reason=reason)

    def _authorized(self, principal, handoff_id: str, *, owner_only: bool = False) -> Handoff:
        handoff = self.db.get(handoff_id)
        if (
            handoff is None
            or handoff.workspace_id != principal.workspace_id
            or (
                principal.role != "owner"
                and (owner_only or handoff.connection_id != principal.connection_id)
            )
        ):
            raise LookupError("handoff not found")
        return handoff

    @staticmethod
    def _meta(handoff: Handoff) -> ThreadMeta:
        return ThreadMeta(
            thread_id=handoff.source_thread_id,
            agent=handoff.agent,
            task_ref=handoff.task_ref,
            branch=handoff.branch,
            pr_url=handoff.pr_url,
            cwd=handoff.cwd,
            parent_handoff_id=handoff.parent_handoff_id,
        )

    @staticmethod
    def _chain(store, parent_id: str, workspace_id: str, *, exclude: str) -> tuple:
        chain = []
        seen = {exclude}
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = store.get(parent_id)
            if parent is None or parent.workspace_id != workspace_id:
                break
            chain.append(parent_id)
            parent_id = store.parent_of(parent_id)
        return tuple(chain)

    def _emit(self, event: str, principal, handoff: Optional[Handoff], **details) -> None:
        try:
            self.events.emit(
                event,
                workspace_id=handoff.workspace_id if handoff else principal.workspace_id,
                handoff=handoff,
                principal=principal,
                **details,
            )
        except Exception:
            # Event delivery must not jeopardize storage or stop compaction.
            pass
