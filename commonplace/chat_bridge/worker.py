"""The bridge's background worker: delivers the outbox and resolves what a restart left behind.

It exists only while some install exists on the hub: nothing starts it on a hub that never
installed the bridge, and the first accepted intake (or a hub start with installs) starts it. A
reply is also delivered at once on its own short thread; this loop is the safety net for retries,
cut-off posts and interrupted turns.
"""

import logging
import threading
from datetime import datetime, timezone

from commonplace.chat_bridge import index, outbox, store, turns
from commonplace.http_util import log_failure

logger = logging.getLogger(__name__)

INTERVAL_S = 5
GIVE_UP = 5  # consecutive failed passes before the loop ends (it restarts with the next intake)
_lock = threading.Lock()


def sweep(gateway, workspace_id, now=None) -> None:
    """One pass over a workspace: resolve interrupted events, settle cut-off posts, deliver."""
    now = now or datetime.now(timezone.utc)
    bridge = store.open_bridge(gateway, workspace_id)
    for install in bridge.installs():
        if not install["enabled"]:
            continue
        for step in (turns.resolve_events, outbox.settle_stuck):
            try:
                step(gateway, workspace_id, install["id"], now)
            except Exception as exc:  # noqa: BLE001
                log_failure(logger, "Chat bridge sweep step failed", exc)
        outbox.deliver(gateway, workspace_id, install["id"], now)


def _loop(gateway, stop) -> None:
    failed = 0
    while not stop.wait(INTERVAL_S):
        try:
            for workspace_id in index.workspaces(gateway.db):
                sweep(gateway, workspace_id)
            failed = 0
        except Exception as exc:  # noqa: BLE001
            log_failure(logger, "Chat bridge pass failed", exc)
            failed += 1
            if failed >= GIVE_UP:
                break
    with _lock:
        if getattr(gateway, "chat_bridge_worker", None) is stop:
            gateway.chat_bridge_worker = None


def ensure(gateway) -> None:
    """Start the worker once for this gateway."""
    with _lock:
        if getattr(gateway, "chat_bridge_worker", None) is not None:
            return
        stop = threading.Event()
        gateway.chat_bridge_worker = stop
        threading.Thread(
            target=_loop, args=(gateway, stop), name="bridge-worker", daemon=True
        ).start()


def stop(gateway) -> None:
    with _lock:
        event = getattr(gateway, "chat_bridge_worker", None)
        gateway.chat_bridge_worker = None
    if event is not None:
        event.set()


def kick(gateway, workspace_id, install_id) -> None:
    """Deliver what is queued for an install now, off the caller's thread."""

    def run():
        try:
            outbox.deliver(gateway, workspace_id, install_id)
        except Exception as exc:  # noqa: BLE001 - the loop retries
            log_failure(logger, "Chat reply not delivered", exc)

    threading.Thread(target=run, name="bridge-deliver", daemon=True).start()
