"""Required integration contract, deliberately without a production implementation.

An application integration must serialize *all* listed writers against admission,
advance the workspace epoch on authority changes, and fence gateway admission
with the same epoch. A Python lock, a caller boolean, or naming these domains is
not such an integration. This package cannot certify external writers. Until
they participate, do not supply an AuthorityFence or enable the executor.

Guards cover local checks and durable admission only. They must never be held
over network I/O. Gateway freshness/revocation ordering is the integration's
responsibility; SSH callbacks alone cannot close that distributed boundary.
"""

from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass

from .protocol import identifier, positive_integer

REQUIRED_DOMAINS = frozenset(
    {"control", "boards", "compute", "custody", "enrollment", "gateway_admission"}
)


class FenceDenied(ValueError):
    """No participating application-wide authority fence is available."""


@dataclass(frozen=True)
class AuthorityLease:
    workspace: str
    epoch: int
    domains: frozenset[str]


class AuthorityFence(ABC):
    """Trusted application seam; no process-local fallback is provided.

    ``hold(workspace)`` yields an AuthorityLease while relevant local authority
    writers are excluded. Mixed old/unparticipating writers must deny the lease.
    Its epoch must also be enforced by the protected gateway admission service.
    Implementations must use a single documented lock order and bounded waits.
    """

    @abstractmethod
    def hold(self, workspace):
        """Return a context manager yielding a current participating lease."""


@contextmanager
def _held(fence, workspace):
    if not isinstance(fence, AuthorityFence):
        raise FenceDenied("authority_fence_unavailable")
    with fence.hold(workspace) as lease:
        if (
            type(lease) is not AuthorityLease
            or lease.workspace != workspace
            or type(lease.domains) is not frozenset
            or not REQUIRED_DOMAINS <= lease.domains
        ):
            raise FenceDenied("authority_fence_unavailable")
        identifier(lease.workspace)
        positive_integer(lease.epoch)
        yield lease
