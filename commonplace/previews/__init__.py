"""Persistent previews: a Coworker publishes a built bundle for a card, and people who can
open that card reopen it after the work session has ended.

Modules: ``bundle`` (checks a bundle before anything is stored), ``store`` (control-database
tables, limits, reasons, files on disk), ``service`` (publish, stop, reopen, delete, the stop
rule), ``routes`` (``/api/previews`` for people) and ``tools`` (``preview_publish``,
``preview_list``, ``preview_stop`` for Coworkers). The design note is
``docs/system/previews.md``.

As a gateway extension, ``install(gateway)`` creates the tables and adds the routes.
"""

from . import tools  # noqa: F401  (registers the tools)


def install(gateway) -> None:
    from . import routes, store

    if getattr(gateway, "previews_installed", False):
        return
    store.ensure_schema(gateway.db)
    routes.install(gateway)
    gateway.previews_installed = True
