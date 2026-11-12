"""Snooze a card until a time or until another card is done.

Modules: ``store`` (the ``card_snoozes`` table in each board database: validation, set,
clear, wake), ``worker`` (the due-time pass that logs and announces wakes), ``routes``
(``/api/card-snooze``). The card operations themselves are ``BoardService.task_snooze`` and
``task_unsnooze``, so the MCP tools and the REST routes share one set of rules. Time
parsing comes from the shared ``commonplace.schedule_time`` module.

As a gateway extension, ``install(gateway)`` starts the worker (set
``COMMONPLACE_SNOOZE_WORKER=off`` to run it by hand with ``run_once``) and adds the routes.
"""

import os


def install(gateway) -> None:
    from . import routes, worker

    if getattr(gateway, "card_snooze", None) is not None:
        return
    gateway.card_snooze = worker.SnoozeWorker(
        gateway, start=os.environ.get("COMMONPLACE_SNOOZE_WORKER", "on") != "off"
    )
    gateway.db.add_pre_close_callback(gateway.card_snooze.stop)
    routes.install(gateway)
