"""Tool policy: the workspace owner sets every tool action to Allowed, Ask first or Off.

Modules: ``store`` (control-database rows, the argument digest, single-use closing), ``gate``
(the check at the gateway's dispatch point and the Ask-first request), ``routes``
(``/api/tool-policy``, owner only).

Allowed is the default for every action and stores nothing, so a workspace that never opens
the setting behaves as before. The policy narrows what a connection's scopes already allow
and never widens it. It covers agent connections; people in the app are not gated.
"""

from .gate import check_call, rest_refusal

__all__ = ["check_call", "install", "rest_refusal"]


def install(gateway) -> None:
    from . import routes, store

    store.ensure_schema(gateway.db)
    routes.install(gateway)
