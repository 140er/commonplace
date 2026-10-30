"""Process-local, bounded token buckets for the gateway's public boundary."""

from __future__ import annotations

import ipaddress
import math
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Limit:
    name: str
    capacity: int
    seconds: int


ANONYMOUS = Limit("anonymous", 30, 60)
CREDENTIALS = Limit("credentials", 10, 60)
AUTHENTICATED = Limit("authenticated", 600, 60)
UNTRUSTED = Limit("untrusted", 60, 60)
WRITES = Limit("untrusted-writes", 120, 3600)
SHORT_LINKS = Limit("short-links", 120, 60)


def client_ip(peer: str, forwarded: str = "") -> str:
    """Only our loopback Caddy peer may supply the rightmost forwarded IP."""
    candidate = forwarded.rsplit(",", 1)[-1].strip() if peer == "127.0.0.1" else ""
    try:
        if candidate and "%" not in candidate:
            return str(ipaddress.ip_address(candidate))
    except ValueError:
        pass
    try:
        return str(ipaddress.ip_address(peer))
    except ValueError:
        return "unknown"


class TokenBuckets:
    """Charge all applicable policies together, or none. Never store credentials.

    Idle buckets expire after one full refill period. At capacity, new identities
    fail closed until space expires; evicting live identities would reset limits.
    """

    def __init__(self, *, clock=time.monotonic, max_entries=10000):
        self.clock = clock
        self.max_entries = max_entries
        self._buckets = {}
        self._lock = threading.Lock()
        self._next_cleanup = 0.0

    def check(self, policies: list[tuple[Limit, str]]) -> int:
        """Return zero on admission, otherwise Retry-After seconds (rounded up)."""
        with self._lock:
            now = self.clock()
            if now >= self._next_cleanup or len(self._buckets) >= self.max_entries:
                self._buckets = {
                    key: bucket for key, bucket in self._buckets.items() if bucket[2] > now
                }
                self._next_cleanup = now + 60
            pending = {}
            retry = 0.0
            for limit, identity in policies:
                key = (limit.name, identity)
                tokens, updated, _expires = self._buckets.get(
                    key, (limit.capacity, now, now + limit.seconds)
                )
                rate = limit.capacity / limit.seconds
                tokens = min(limit.capacity, tokens + max(0.0, now - updated) * rate)
                if tokens < 1:
                    retry = max(retry, (1 - tokens) / rate)
                pending[key] = (tokens - 1, now, now + limit.seconds)
            new_keys = pending.keys() - self._buckets.keys()
            if len(self._buckets) + len(new_keys) > self.max_entries:
                retry = max(retry, min((b[2] - now for b in self._buckets.values()), default=60))
            if retry > 0:
                return max(1, math.ceil(retry))
            self._buckets.update(pending)
            return 0
