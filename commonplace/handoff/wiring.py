"""Plug context handoff into a gateway.

``commonplace.handoff.extension`` calls :func:`install` for every gateway
(``extensions.MODULES``). It builds one ``HandoffContext`` and hands it to each
part's ``register``. Nothing changes while every workspace leaves handoff off:
the routes exist, the thread tools stay hidden, the hook gets ``action:
compact``. Events go into the hub's event log, so the hub's webhooks deliver
them.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class HandoffContext:
    gateway: object
    control_db: object
    data_dir: Path
    handoff_db: object = None
    settings: object = None
    events: object = None
    recall: object = None
    service: object = None
    _stores: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def store_for(self, workspace_id: str):
        from .thread_store import ThreadStore

        with self._lock:
            store = self._stores.get(workspace_id)
            if store is None:
                path = self.data_dir / "workspaces" / workspace_id / "threads.sqlite3"
                os.makedirs(path.parent, exist_ok=True)
                store = self._stores[workspace_id] = ThreadStore(str(path))
            return store

    def has_threads(self, workspace_id: str) -> bool:
        path = self.data_dir / "workspaces" / workspace_id / "threads.sqlite3"
        return path.exists()

    def call_tool(self, principal, name: str, arguments: dict):
        return self.gateway.mediator.workers.call_tool(principal, name, arguments)

    def close(self) -> None:
        if self.service is not None:
            self.service.wait_for_mirrors(timeout=30)
        with self._lock:
            for store in self._stores.values():
                store.close()
            self._stores.clear()


def install(gateway, data_dir=None, **_ignored) -> HandoffContext:
    """Idempotent: a gateway gets one context. ``data_dir`` defaults to the
    gateway's data directory."""
    existing = getattr(gateway, "handoff", None)
    if existing is not None:
        return existing
    from . import api, web
    from .db import HandoffDB
    from .events import Events
    from .recall import Recall
    from .service import HandoffService
    from .settings import SettingsService

    if data_dir is None:
        data_dir = gateway.supervisor.root.parent
    ctx = HandoffContext(gateway=gateway, control_db=gateway.db, data_dir=Path(data_dir))
    ctx.handoff_db = HandoffDB(gateway.db)
    ctx.events = Events(gateway.db, ctx.handoff_db)
    ctx.settings = SettingsService(ctx.handoff_db, ctx.events)
    ctx.recall = Recall(ctx.store_for, call_tool=ctx.call_tool)
    ctx.service = HandoffService(
        control_db=gateway.db,
        handoff_db=ctx.handoff_db,
        settings=ctx.settings,
        store_for=ctx.store_for,
        events=ctx.events,
        call_tool=ctx.call_tool,
        prompt_dir=str(Path(data_dir) / "handoff-prompts"),
    )
    gateway.handoff = ctx
    api.register(ctx)
    web.register(ctx)
    # Events a crash left queued (handoff.completed) go out now.
    ctx.service.replay_pending()
    return ctx
