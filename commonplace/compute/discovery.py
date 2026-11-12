"""Bounded observations from existing caches; never discovery by network traffic.

Suppliers are trusted installation capabilities, not wire arguments. They must
return promptly and call ``checkpoint`` during reads; Python cannot preempt an
arbitrary blocking supplier. Collection is serial and budgets span all selected
sources. No durable quota, enrollment, identity verification or execution is
provided here. A timestamp means the cache was read then, not host liveness.
"""

from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import stat
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Callable, Protocol

from .scopes import DiscoveryContext, DiscoveryLimits, NetworkScope, ScopeDenied

MAX_SOURCE_BYTES = 65536
MAX_SOURCE_LINES = 1024
MAX_LINE_BYTES = 1024
MAX_OUTPUT_BYTES = 8192
MAX_CACHE_AGE_SECONDS = 300
PASSIVE_SOURCES = frozenset({"linux_arp", "ip_neigh"})
_PRIVATE = tuple(
    ipaddress.ip_network(x) for x in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)
_INTERFACE = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")
_MAC = re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\Z")
_STATES = frozenset({"REACHABLE", "STALE", "DELAY", "PROBE", "PERMANENT", "NOARP"})
_FLAGS = frozenset({"router", "managed", "extern_learn", "offload"})


class DiscoveryDenied(ScopeDenied):
    """Fixed local reason only; cache content is never included in errors."""


def _time(value):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise DiscoveryDenied("invalid_clock")
    return value


def validate_sources(sources):
    if (
        type(sources) not in (tuple, list)
        or not 1 <= len(sources) <= len(PASSIVE_SOURCES)
        or any(type(x) is not str or x not in PASSIVE_SOURCES for x in sources)
        or len(set(sources)) != len(sources)
    ):
        raise DiscoveryDenied("unsupported_passive_source")
    return tuple(sources)


def _address(value):
    try:
        if "%" in value:
            return None
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if not any(address.version == net.version and address in net for net in _PRIVATE):
        return None
    return str(address)


def _mac(value):
    if not _MAC.fullmatch(value):
        return None
    value = value.lower()
    if value == "00:00:00:00:00:00" or int(value[:2], 16) & 1:
        return None
    return value


@dataclass(frozen=True)
class CacheEntry:
    address: str
    interface: str
    hardware_address: str
    state: str


@dataclass(frozen=True)
class CacheSnapshot:
    source: str
    data: bytes
    observed_at: float


class CacheSupplier(Protocol):
    """Trusted source reader; source names never mean paths or commands."""

    sources: frozenset[str]

    def read(
        self, source: str, *, max_bytes: int, max_lines: int, checkpoint: Callable[[], None]
    ) -> CacheSnapshot: ...


def _lines(data, *, max_bytes=MAX_SOURCE_BYTES, max_lines=MAX_SOURCE_LINES):
    if type(data) is not bytes or len(data) > max_bytes:
        raise DiscoveryDenied("source_byte_limit")
    # Only LF delimits records. CR is allowed solely as a CRLF terminator.
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    if len(lines) > max_lines:
        raise DiscoveryDenied("source_line_limit")
    if any(len(line) > MAX_LINE_BYTES for line in lines):
        raise DiscoveryDenied("source_line_limit")
    return lines


def _arp(line):
    fields = line.split()
    if len(fields) != 6:
        return None
    address, hwtype, flags, hardware, mask, interface = fields
    if (
        hwtype != "0x1"
        or not re.fullmatch(r"0x[0-9a-fA-F]{1,4}", flags)
        or not int(flags, 16) & 2
        or mask != "*"
        or not _INTERFACE.fullmatch(interface)
    ):
        return None
    address, hardware = _address(address), _mac(hardware)
    if address is None or ":" in address or hardware is None:
        return None
    return CacheEntry(address, interface, hardware, "complete")


def _neigh(line):
    fields = line.split()
    # Fixed ip-neigh cache rendering only. No names, arbitrary key/value tails,
    # INCOMPLETE/FAILED entries or metadata that might be interpreted as actions.
    if not 6 <= len(fields) <= 10 or fields[1] != "dev" or fields[3] != "lladdr":
        return None
    address, interface, hardware = fields[0], fields[2], fields[4]
    tail = fields[5:]
    states = [x for x in tail if x in _STATES]
    if (
        len(states) != 1
        or len(set(tail)) != len(tail)
        or any(x not in _STATES | _FLAGS for x in tail)
        or not _INTERFACE.fullmatch(interface)
    ):
        return None
    address, hardware = _address(address), _mac(hardware)
    if address is None or hardware is None:
        return None
    return CacheEntry(address, interface, hardware, states[0])


def _parse_line(source, line):
    line = line.removesuffix(b"\r")
    # Drop non-ASCII, control characters (except column tabs), and escape codes.
    if any(x < 32 and x != 9 or x > 126 for x in line):
        return None
    text = line.decode("ascii")
    return _arp(text) if source == "linux_arp" else _neigh(text)


def parse_cache(source: str, data: bytes) -> tuple[CacheEntry, ...]:
    """Pure bounded parser for proc ARP or trusted captured ip-neigh text.

    No command is run to obtain ip-neigh text. Unsupported formats/records are
    discarded; an unsupported source or exceeded source bound denies the input.
    """
    validate_sources((source,))
    return tuple(entry for line in _lines(data) if (entry := _parse_line(source, line)))


class OSCacheSupplier:
    """Optional Linux cache reader; not installed in the helper by default.

    Only /proc/net/arp is approved here. IPv6/neigh snapshots require a separate
    trusted supplier; there is no command, netlink, mDNS, DNS or active fallback.
    No public API accepts a path. Tests substitute synthetic file descriptors.
    """

    sources = frozenset({"linux_arp"})

    def __init__(self, *, wall=time.time):
        self.wall = wall

    def read(self, source, *, max_bytes, max_lines, checkpoint):
        if source not in self.sources:
            raise DiscoveryDenied("unsupported_passive_source")
        if (
            type(max_bytes) is not int
            or not 1 <= max_bytes <= MAX_SOURCE_BYTES
            or type(max_lines) is not int
            or not 1 <= max_lines <= MAX_SOURCE_LINES
        ):
            raise DiscoveryDenied("invalid_source_limit")
        checkpoint()
        fd = None
        try:
            fd = os.open(
                "/proc/net/arp", os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
            )
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise DiscoveryDenied("unsafe_cache_source")
            data = bytearray()
            while True:
                checkpoint()
                part = os.read(fd, min(4096, max_bytes - len(data) + 1))
                checkpoint()
                data.extend(part)
                if len(data) > max_bytes:
                    raise DiscoveryDenied("source_byte_limit")
                _lines(bytes(data), max_bytes=max_bytes, max_lines=max_lines)
                if not part:
                    break
            observed_at = _time(self.wall())
        except OSError:
            raise DiscoveryDenied("passive_source_unavailable") from None
        finally:
            if fd is not None:
                os.close(fd)
        return CacheSnapshot(source, bytes(data), observed_at)


@dataclass(frozen=True)
class PassiveObservation:
    gateway: str
    interface: str
    source: str
    observed_at: float
    address: str
    hardware_address: str
    state: str

    def document(self):
        return asdict(self)


@dataclass(frozen=True)
class PassiveResult:
    observations: tuple[PassiveObservation, ...]
    reason: str | None
    source_bytes: int
    source_lines: int

    def document(self):
        return {
            "observations": [x.document() for x in self.observations],
            "truncated": self.reason is not None,
            "reason": self.reason,
            "source_bytes": self.source_bytes,
            "source_lines": self.source_lines,
        }


class _Stopped(Exception):
    pass


def _size(document):
    return len(
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    )


def _check_rate(admissions, stamp, ceiling):
    while admissions and stamp - admissions[0] >= 1:
        admissions.popleft()
    if len(admissions) >= ceiling:
        raise _Stopped("rate_limit")


def _fresh(observed_at, now):
    observed_at = _time(observed_at)
    if observed_at > now or now - observed_at > MAX_CACHE_AGE_SECONDS:
        raise DiscoveryDenied("stale_cache_snapshot")
    return observed_at


def _read_snapshot(supplier, source, max_bytes, max_lines, checkpoint):
    try:
        snapshot = supplier.read(
            source, max_bytes=max_bytes, max_lines=max_lines, checkpoint=checkpoint
        )
    except (_Stopped, DiscoveryDenied):
        raise
    except Exception:
        raise DiscoveryDenied("passive_source_unavailable") from None
    checkpoint()
    if type(snapshot) is not CacheSnapshot or snapshot.source != source:
        raise DiscoveryDenied("invalid_cache_snapshot")
    return snapshot, _lines(snapshot.data, max_bytes=max_bytes, max_lines=max_lines)


def _scope(scope, context, limits, now):
    if type(scope) is not NetworkScope or type(context) is not DiscoveryContext:
        raise DiscoveryDenied("invalid_scope_contract")
    if scope.context != context:
        raise DiscoveryDenied("scope_context_mismatch")
    if scope.revoked or now >= scope.expires_at:
        raise DiscoveryDenied("scope_revoked_or_expired")
    if "passive" not in scope.methods:
        raise DiscoveryDenied("passive_method_denied")
    if any(
        getattr(limits, name) > getattr(scope.limits, name) for name in limits.__dataclass_fields__
    ):
        raise DiscoveryDenied("discovery_limit_exceeded")
    return scope


def _in_scope(scope, context, entry, now):
    try:
        scope.authorize(
            context=context,
            address=entry.address,
            interface=entry.interface,
            method="passive",
            now=now,
        )
    except ScopeDenied:
        return False
    address = ipaddress.ip_address(entry.address)
    # Do not treat IPv4 subnet/broadcast addresses as hosts; /31 and /32 are
    # point-to-point/host scopes and have no excluded broadcast endpoints.
    return any(
        address in net
        and (
            net.version != 4
            or net.prefixlen >= 31
            or address not in (net.network_address, net.broadcast_address)
        )
        for net in map(ipaddress.ip_network, scope.cidrs)
    )


class PassiveCollector:
    """Serial bounded collection with fresh trusted authority per observation.

    ``current_scope`` must resolve authenticated server state, never a grant from
    a request. Both it and the independent protected ceiling apply. One instance
    admits one collection at a time. Durable cross-job quotas belong to the
    controller; no stronger cross-process/restart guarantee is made here.
    """

    def __init__(self, supplier: CacheSupplier, *, wall=time.time, monotonic=time.monotonic):
        sources = getattr(supplier, "sources", None)
        if (
            type(sources) is not frozenset
            or not sources
            or not sources <= PASSIVE_SOURCES
            or not callable(getattr(supplier, "read", None))
        ):
            raise DiscoveryDenied("invalid_cache_supplier")
        self.supplier, self.sources = supplier, sources
        self.wall, self.monotonic = wall, monotonic
        self._lock = threading.Lock()

    def collect(
        self,
        *,
        context: DiscoveryContext,
        limits: DiscoveryLimits,
        sources: tuple[str, ...],
        ceiling: NetworkScope,
        current_scope: Callable[[], NetworkScope],
        output_bytes: int = MAX_OUTPUT_BYTES,
        envelope_bytes: int = 0,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> PassiveResult:
        sources = validate_sources(sources)
        if not set(sources) <= self.sources:
            raise DiscoveryDenied("passive_source_unavailable")
        if (
            type(limits) is not DiscoveryLimits
            or type(output_bytes) is not int
            or not 1 <= output_bytes <= MAX_OUTPUT_BYTES
            or type(envelope_bytes) is not int
            or not 0 <= envelope_bytes < output_bytes
            or not callable(current_scope)
            or not callable(cancelled)
        ):
            raise DiscoveryDenied("invalid_collection_contract")
        if not self._lock.acquire(blocking=False):
            raise DiscoveryDenied("collection_capacity_exceeded")
        try:
            return self._collect(
                context,
                limits,
                sources,
                ceiling,
                current_scope,
                output_bytes - envelope_bytes,
                cancelled,
            )
        finally:
            self._lock.release()

    def _collect(self, context, limits, sources, ceiling, current_scope, output_bytes, cancelled):
        start = _time(self.monotonic())
        previous = start
        previous_wall = _time(self.wall())
        observations, hosts, seen, admissions = [], set(), set(), deque()
        source_bytes = source_lines = 0
        reason = None

        def checkpoint():
            nonlocal previous
            stamp = _time(self.monotonic())
            if stamp < previous:
                raise DiscoveryDenied("clock_regressed")
            previous = stamp
            stop = cancelled()
            if type(stop) is not bool:
                raise DiscoveryDenied("invalid_cancellation")
            if stop:
                raise _Stopped("cancelled")
            if stamp - start >= limits.duration_seconds:
                raise _Stopped("duration_limit")
            return stamp

        def authority():
            nonlocal previous_wall
            _scope(ceiling, context, limits, previous_wall)
            try:
                current = current_scope()
            except Exception:
                raise DiscoveryDenied("scope_authority_unavailable") from None
            # A trusted lookup can block. Never authorize using the timestamp
            # captured before it, or let a backwards UTC jump extend authority.
            now = _time(self.wall())
            if now < previous_wall:
                raise DiscoveryDenied("clock_regressed")
            previous_wall = now
            protected = _scope(ceiling, context, limits, now)
            return protected, _scope(current, context, limits, now), now

        def fits(items):
            # Reserve the largest fixed summary even on a successful run. Include
            # encoded provenance and the helper's exact envelope, not just text.
            worst = PassiveResult(
                tuple(items), "passive_source_unavailable", MAX_SOURCE_BYTES, MAX_SOURCE_LINES
            )
            return _size(worst.document()) <= output_bytes

        authority()
        if not fits([]):
            raise DiscoveryDenied("output_limit")
        try:
            checkpoint()
            for source in sources:
                checkpoint()
                authority()
                remaining_bytes = MAX_SOURCE_BYTES - source_bytes
                remaining_lines = MAX_SOURCE_LINES - source_lines
                if remaining_bytes <= 0 or remaining_lines <= 0:
                    raise _Stopped("source_limit")
                snapshot, lines = _read_snapshot(
                    self.supplier, source, remaining_bytes, remaining_lines, checkpoint
                )
                source_bytes += len(snapshot.data)
                source_lines += len(lines)
                observed_at = _fresh(snapshot.observed_at, authority()[2])
                for line in lines:
                    stamp = checkpoint()
                    entry = _parse_line(source, line)
                    if entry is None:
                        continue
                    protected, current, now = authority()
                    _fresh(observed_at, now)
                    if not all(_in_scope(s, context, entry, now) for s in (protected, current)):
                        continue
                    observation = PassiveObservation(
                        context.gateway,
                        entry.interface,
                        source,
                        observed_at,
                        entry.address,
                        entry.hardware_address,
                        entry.state,
                    )
                    if observation in seen:
                        continue
                    host = (entry.interface, entry.address)
                    if host not in hosts and len(hosts) >= limits.hosts:
                        raise _Stopped("host_limit")
                    if len(observations) >= limits.results:
                        raise _Stopped("result_limit")
                    _check_rate(admissions, stamp, limits.rate_per_second)
                    if not fits([*observations, observation]):
                        raise _Stopped("output_limit")
                    # The scope callback may itself take time or trigger cancel.
                    checkpoint()
                    protected, current, now = authority()
                    if not all(_in_scope(s, context, entry, now) for s in (protected, current)):
                        continue
                    stamp = checkpoint()
                    # Account at actual admission, after potentially slow trusted
                    # authority work, using a sliding one-second window.
                    _check_rate(admissions, stamp, limits.rate_per_second)
                    admissions.append(stamp)
                    hosts.add(host)
                    seen.add(observation)
                    observations.append(observation)
        except _Stopped as exc:
            reason = str(exc)
        # Withhold all collected observations if authority was lost meanwhile.
        protected, current, now = authority()
        for observation in observations:
            _fresh(observation.observed_at, now)
            if not all(_in_scope(s, context, observation, now) for s in (protected, current)):
                raise DiscoveryDenied("scope_changed")
        try:
            checkpoint()
        except _Stopped as exc:
            reason = str(exc)
        return PassiveResult(tuple(observations), reason, source_bytes, source_lines)
