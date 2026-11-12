"""The meeting worker: a beat that recovers orphaned meetings and starts the due ones.

Nothing runs until a meeting exists: the thread is started by an invite (or, after a restart, by
a workspace that already holds unfinished meetings) and ends by itself when no meeting is
requested or running, so a workspace that never used meetings has no worker and no file. Each
meeting runs on its own daemon thread; the beat renews nothing itself (a meeting renews its own
lease) and a meeting whose lease lapsed is ended by ``engine.recover`` (``interrupted``, or
``discarded`` if a stop was requested), never resumed.
"""

from __future__ import annotations

import logging
import threading
import time

from commonplace.meetings import engine, store

logger = logging.getLogger("commonplace.meetings")

BEAT_S = 15
AUTOSTART = True  # tests turn it off and call ``beat`` themselves


class Worker:
    def __init__(self, gateway, *, clock=time.time, sink_factory=None):
        self.gateway, self.clock = gateway, clock
        self.sink_factory = sink_factory or (lambda workspace_id, meeting_id: engine.Sink())
        self._lock = threading.Lock()
        self._beating: set[str] = set()
        self._runs: dict[str, threading.Thread] = {}

    def beat(self, workspace_id, *, inline=False) -> list[str]:
        """Recover orphaned meetings, then start the due ones. Returns the ids started. With
        ``inline`` each meeting runs to its end on the calling thread (tests, the CLI)."""
        meetings = store.open_meetings(self.gateway, workspace_id)
        if not meetings.exists():
            return []
        now = self.clock()
        for meeting_id in meetings.expired(now, engine.LEASE_S):
            engine.recover(self.gateway, workspace_id, meeting_id, clock=self.clock)
        if not meetings.enabled():
            return []
        started = []
        # only as many as may start now: a capped meeting waits without a thread per beat
        for meeting_id in meetings.due(now)[: meetings.free_slots(now, engine.LEASE_S)]:
            run = engine.Run(
                self.gateway,
                workspace_id,
                meeting_id,
                clock=self.clock,
                sink=self.sink_factory(workspace_id, meeting_id),
            )
            if inline:
                run.run()
            else:
                thread = threading.Thread(target=run.run, name="meeting-run", daemon=True)
                self._runs[meeting_id] = thread
                thread.start()
            started.append(meeting_id)
        return started

    def kick(self, workspace_id) -> bool:
        """Make sure a beat thread is running for this workspace (only if there is work)."""
        if not AUTOSTART:
            return False
        with self._lock:
            if workspace_id in self._beating:
                return False
            self._beating.add(workspace_id)
        threading.Thread(
            target=self._loop, args=(workspace_id,), name="meeting-beat", daemon=True
        ).start()
        return True

    def _loop(self, workspace_id):
        meetings = store.open_meetings(self.gateway, workspace_id)
        while True:
            try:
                self.beat(workspace_id)
                busy = meetings.pending()
            except Exception as exc:  # a failing beat must not end the worker
                logger.warning("meeting beat failed type=%s", type(exc).__name__)
                busy = True
            with self._lock:
                # decided under the lock ``kick`` takes: an invite that committed before this
                # look keeps the loop alive, one that commits after it starts a new loop
                if not busy and not meetings.pending():
                    self._beating.discard(workspace_id)
                    return
            time.sleep(BEAT_S)


def of(gateway) -> Worker:
    worker = getattr(gateway, "meetings_worker", None)
    if worker is None:
        worker = gateway.meetings_worker = Worker(gateway)
    return worker


def resume(gateway) -> None:
    """After a restart: a workspace that already has unfinished meetings gets its beat back, which
    ends the orphaned ones. A workspace without a meetings file is not touched."""
    try:
        for row in gateway.db.workspaces():
            meetings = store.open_meetings(gateway, row["id"])
            if meetings.exists() and meetings.pending():
                of(gateway).kick(row["id"])
    except Exception as exc:
        logger.warning("meeting resume failed type=%s", type(exc).__name__)
