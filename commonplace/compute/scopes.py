"""Pure validation for explicitly approved, finite gateway discovery scopes.

Callers must obtain scopes from authenticated server storage. Constructing one of
these values is not enrollment, approval, or an execution grant. No network I/O
or credential handling occurs in this module.
"""

from __future__ import annotations

import ipaddress
import math
import re
from dataclasses import dataclass

_PRIVATE = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)
_INTERFACE = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")


class ScopeDenied(ValueError):
    """A scope cannot authorize the requested observation."""


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScopeDenied(f"invalid {name}")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ScopeDenied(f"invalid {name}")
    return value


@dataclass(frozen=True)
class DiscoveryLimits:
    """Approved ceilings; dispatch must account for aggregate consumption."""

    hosts: int
    concurrency: int
    rate_per_second: int
    duration_seconds: int
    results: int

    def __post_init__(self):
        for name, ceiling in (
            ("hosts", 4096),
            ("concurrency", 64),
            ("rate_per_second", 100),
            ("duration_seconds", 300),
            ("results", 4096),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ScopeDenied(f"invalid {name} limit")
        if self.concurrency > self.hosts:
            raise ScopeDenied("concurrency exceeds host limit")


@dataclass(frozen=True)
class DiscoveryContext:
    workspace: str
    project: str
    gateway: str
    user: str
    agent: str
    task: str

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ScopeDenied("incomplete discovery context")


@dataclass(frozen=True)
class NetworkScope:
    context: DiscoveryContext
    cidrs: tuple[str, ...]
    interfaces: tuple[str, ...]
    methods: frozenset[str]
    approved_by: str
    expires_at: float
    limits: DiscoveryLimits
    revoked: bool = False

    def __post_init__(self):
        if not isinstance(self.context, DiscoveryContext) or not isinstance(
            self.limits, DiscoveryLimits
        ):
            raise ScopeDenied("invalid scope contract")
        if not isinstance(self.approved_by, str) or not self.approved_by.strip():
            raise ScopeDenied("human approval required")
        if (
            not isinstance(self.methods, (set, frozenset))
            or not self.methods
            or not self.methods <= {"passive", "active"}
        ):
            raise ScopeDenied("unsupported discovery method")
        if (
            not isinstance(self.cidrs, (tuple, list))
            or not isinstance(self.interfaces, (tuple, list))
            or not 1 <= len(self.cidrs) <= 16
            or not 1 <= len(self.interfaces) <= 16
        ):
            raise ScopeDenied("invalid scope size")
        if any(not isinstance(x, str) or not _INTERFACE.fullmatch(x) for x in self.interfaces):
            raise ScopeDenied("invalid interface")
        if type(self.revoked) is not bool:
            raise ScopeDenied("invalid revocation state")
        _finite(self.expires_at, "expiry")
        networks = []
        for cidr in self.cidrs:
            try:
                if not isinstance(cidr, str) or "%" in cidr:
                    raise ValueError
                network = ipaddress.ip_network(cidr, strict=True)
            except ValueError:
                raise ScopeDenied("invalid canonical network") from None
            if not any(
                network.version == allowed.version and network.subnet_of(allowed)
                for allowed in _PRIVATE
            ):
                raise ScopeDenied("network is outside private discovery ranges")
            networks.append(str(network))
        # Freeze caller-provided collections so mutation cannot widen a grant.
        object.__setattr__(self, "cidrs", tuple(dict.fromkeys(networks)))
        object.__setattr__(self, "interfaces", tuple(dict.fromkeys(self.interfaces)))
        object.__setattr__(self, "methods", frozenset(self.methods))

    def authorize(self, *, context, address, interface, method, now):
        """Check exact actor/task scope at dispatch; never resolve a hostname."""
        _finite(now, "time")
        if self.revoked or now >= self.expires_at:
            raise ScopeDenied("scope is revoked or expired")
        if context != self.context:
            raise ScopeDenied("scope context does not match")
        if method not in self.methods or interface not in self.interfaces:
            raise ScopeDenied("method or interface is not approved")
        try:
            if not isinstance(address, str) or "%" in address:
                raise ValueError
            destination = ipaddress.ip_address(address)
        except ValueError:
            raise ScopeDenied("destination must be an IP literal") from None
        if not any(destination in ipaddress.ip_network(cidr) for cidr in self.cidrs):
            raise ScopeDenied("destination is outside approved scope")
        return str(destination)
