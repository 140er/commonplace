"""The Pipeline view's data: events per pull request, and the numbers computed from them.

Modules: ``compute`` (one pure function from events to steps, waits and lead time), ``store``
(validation and the append-only ``pipeline.sqlite3`` workspace file), ``demo`` (a fixed example
for the empty state and tests) and ``routes`` (``/api/pipeline``). The browser view is
``deck-pipeline.js``. Contract: ``docs/system/pr-pipeline-view.md``.
"""


def install(gateway) -> None:
    from . import routes

    routes.install(gateway)
