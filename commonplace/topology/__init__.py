"""The topology store: machines, links and site routes as structured facts.

``model`` names what may be stored and refuses secrets; ``store`` keeps the facts in the
control database with provenance, expiry and conflict records; ``status`` derives live status
from runner signals. See ``docs/topology.md``.
"""

from . import (
    model,
    site_route_api,  # noqa: F401  registers topology_discover_site
    status,
    store,
)
from .model import TOPOLOGY_WING, Observer, SecretRefused, TopologyError
from .proposal_api import install  # also registers topology_propose

__all__ = [
    "TOPOLOGY_WING",
    "Observer",
    "SecretRefused",
    "TopologyError",
    "install",
    "model",
    "status",
    "store",
]
