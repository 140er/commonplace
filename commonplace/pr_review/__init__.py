"""Auto-addressed PR review comments, slice 1: intake, mapping, statuses, API, tools, trigger.

Modules: ``intake`` (validation of the feeder's batches), ``store`` (the ``pr_review.sqlite3``
workspace file: comments, statuses, history, rounds), ``status`` (statuses, reasons and the words
people read), ``mapping`` (PR to card, branch and fork rules), ``trigger`` (hands a card back to
its Coworker when comments wait), ``needs_me`` (one question per PR), ``routes``
(``/api/pr-review``) and ``tools`` (``pr_review_list``, ``pr_review_take``, ``pr_review_report``). The hub never calls
GitHub: a feeder reads it and posts here. Design: ``docs/system/pr-review-loop.md``.

As a gateway extension, ``install(gateway)`` adds the routes.
"""

from . import tools  # noqa: F401  (registers the tools)


def install(gateway) -> None:
    from . import routes, store

    if getattr(gateway, "pr_review_installed", False):
        return
    routes.install(gateway)
    # The stores' connections close before the control database does (hub shutdown).
    gateway.db.add_pre_close_callback(lambda: store.close_all(gateway))
    gateway.pr_review_installed = True
