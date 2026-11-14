"""Install the machine runner's routes once per gateway."""

from . import (
    budgets_api,
    chat_relay,
    chat_routes,
    costs_api,
    enrollment_api,
    install_receipts,
    ledger_api,
    owner_api,
    registry,
    requester_api,
    runner_api,
)


def install(gateway):
    if getattr(gateway, "runner_installed", False):
        return
    from . import trust

    trust.configure(gateway)
    registry.ensure(gateway.db)
    enrollment_api.install(gateway)
    install_receipts.ensure(gateway.db)
    chat_relay.ensure_schema(gateway.db)
    owner_api.register(gateway)
    runner_api.register(gateway)
    ledger_api.register(gateway)
    costs_api.install(gateway)
    budgets_api.install(gateway)
    requester_api.register(gateway)
    chat_routes.register(gateway)
    gateway.runner_installed = True
