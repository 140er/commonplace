"""Private workspace coworkers; runtime is installed when its module is present."""

import importlib
import os

from .store import Store, StoreError, open_store

__all__ = ["Store", "StoreError", "open_store", "install"]


def install(gateway):
    if getattr(gateway, "coworkers_installed", False):
        return
    from . import routes

    routes.install(gateway)
    try:
        runtime = importlib.import_module("commonplace.chat.coworkers.runtime")
    except ModuleNotFoundError as exc:
        if exc.name != "commonplace.chat.coworkers.runtime":
            raise
    else:
        gateway.coworker_worker = runtime.CoworkerWorker(
            gateway, start=os.environ.get("COMMONPLACE_CHAT_WORKER", "on") != "off"
        )
        gateway.db.add_pre_close_callback(gateway.coworker_worker.stop)
    gateway.coworkers_installed = True
