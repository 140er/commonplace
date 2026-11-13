"""Chat-platform bots: a person talks to their Commonplace bots from a chat platform.

Everything is off until the workspace owner installs, tests and enables a platform team. The
foundation is the workspace file, sealed credentials, owner install routes, the adapter's test
seam, the read-only ``bridge_status`` tool and the kill switch; identity is link codes and links;
intake verifies, filters and journals a platform event, runs the linked person's own Bots-layer
turn, and an outbox delivers the reply once.
"""

from commonplace.chat_bridge import index, routes, tools, worker

tools.register_tools()


def install(gateway):
    if getattr(gateway, "chat_bridge_installed", False):
        return
    index.ensure_schema(gateway.db)
    routes.install(gateway)
    gateway.chat_bridge_installed = True
    if index.workspaces(gateway.db):  # a hub that never installed the bridge starts nothing
        worker.ensure(gateway)
