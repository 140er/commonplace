"""The wake pass: end every snooze that is due and tell the board's listeners once.

Waking is already correct without this worker (a card whose time has passed is no longer
snoozed anywhere it is read); the worker does the bookkeeping for boards nobody reads: it
logs the wake on the card and emits ``card.woke``, the event webhooks and the app watch.
It reads each workspace's board file read-only and takes the write lock only when
something is due. A snooze row is deleted by exactly one transaction, so a wake is logged
and emitted at most once, also across threads, processes and restarts; a crash between the
commit and the event leaves the card woken and logged but without the event.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone

from commonplace import events, http_util
from commonplace.boards.store import BoardStore
from commonplace.projects import orchestration as orch

from . import store

logger = logging.getLogger(__name__)
POLL_SECONDS = 30


def moment(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, timezone.utc)


class SnoozeWorker:
    def __init__(self, gateway, *, start=True, now=time.time):
        self.gateway = gateway
        self.now = now
        self._stop = threading.Event()
        self.thread = None
        if start:
            self.thread = threading.Thread(target=self._run, name="card-snooze", daemon=True)
            self.thread.start()

    def stop(self):
        self._stop.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=5)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:
                http_util.log_failure(logger, "Card snooze pass failed", exc)
            self._stop.wait(timeout=POLL_SECONDS)

    def run_once(self, now: float | None = None) -> int:
        """Wake every snoozed card that is due at ``now``; returns how many woke."""
        when = moment(self.now() if now is None else now)
        woken = 0
        for workspace in self.gateway.db.workspaces():
            try:
                woken += self._workspace(workspace["id"], when)
            except Exception as exc:
                http_util.log_failure(logger, "Card snooze pass failed for a workspace", exc)
        return woken

    def _workspace(self, workspace_id: str, when: datetime) -> int:
        # Not gateway.workspace_dir: that creates the directory, and a pass must not.
        directory = str(self.gateway.supervisor.workspace_dir(workspace_id))
        if not os.path.exists(os.path.join(directory, "boards.sqlite3")):
            return 0
        with orch.board_read(directory) as conn:
            if not store.has_due(conn, when):
                return 0
        with BoardStore(directory, create=False).transaction() as conn:
            cards = store.wake_due(conn, when)
        for card in cards:
            events.emit(
                self.gateway.db,
                workspace_id,
                "card.woke",
                subject=f"#{card['id']}",
                actor="system",
                data=store.event_data(card),
            )
        return len(cards)
