"""Context handoff as a gateway extension (``extensions.MODULES``).

Importing registers the thread tools; ``install(gateway)`` adds the handoff
routes and context to each gateway.
"""

from __future__ import annotations

from . import mcp_tools  # noqa: F401  (registers thread_recall, thread_get, thread_chain)
from .wiring import install as _install


def install(gateway) -> None:
    # A gateway without a data directory (test stubs) installs handoff
    # explicitly with ``wiring.install(gateway, data_dir)``.
    if getattr(getattr(gateway, "supervisor", None), "root", None) is None:
        return
    _install(gateway)
