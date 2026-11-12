"""Meeting-join bots, foundation: the store, sealed credentials, switches and the adapter seam.

Modules: ``adapter`` (the provider interface, the registry and the in-memory fake), ``seal``
(credentials and links sealed with a per-workspace key), ``store`` (``meetings.sqlite3``: owner
switches, connections, people, meetings), ``routes`` (``/api/meetings``), ``tools`` (the
read-only ``meeting_status`` tool) and ``cli`` (``commonplace meetings status|disable``).
Design: ``docs/design/meeting-join-bots.md``. Everything is off until the owner, then the
person, opt in; no worker, join or capture exists yet, and no provider is registered by default.
"""

from commonplace.meetings import tools  # noqa: F401  (registers meeting_status)


def install(gateway) -> None:
    from commonplace.meetings import routes

    if getattr(gateway, "meetings_installed", False):
        return
    routes.install(gateway)
    gateway.meetings_installed = True
