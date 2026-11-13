"""Chat-platform bots: a person talks to their Commonplace bots from a chat platform.

Everything is off until the workspace owner installs, tests and enables a platform team. This
slice holds the foundation: the workspace file, sealed credentials, owner install routes, the
adapter's test seam, the read-only ``bridge_status`` tool and the kill switch.
"""

from commonplace.chat_bridge import index, routes, tools

tools.register_tools()


def install(gateway):
    if getattr(gateway, "chat_bridge_installed", False):
        return
    index.ensure_schema(gateway.db)
    routes.install(gateway)
    gateway.chat_bridge_installed = True
