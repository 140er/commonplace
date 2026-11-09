"""Compute management metadata; remote transport stays disabled by default."""


def install(gateway):
    """Register owner-session management routes without keys or remote effects."""
    if getattr(gateway, "compute_installed", False):
        return
    from . import routes

    routes.install(gateway)
    gateway.compute_installed = True
