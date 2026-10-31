"""Sample old-store reads after a verified switch; never log payloads or queries."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import secrets

logger = logging.getLogger(__name__)


def digest(value):
    def encode(item):
        if dataclasses.is_dataclass(item):
            return dataclasses.asdict(item)
        if hasattr(item, "tolist"):
            return item.tolist()
        raise TypeError("Unsupported comparison value")

    return hashlib.sha256(
        json.dumps(value, default=encode, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def compare(current, previous):
    """Only cryptographic digests reach logs, even when records differ."""
    left, right = digest(current), digest(previous)
    logger.info("store_dual_read current_hash=%s previous_hash=%s", left, right)


class SampledReads:
    """Delegate writes untouched and observe a small sample of live reads."""

    def __init__(self, current, previous, *, reads, sample_every=100):
        self._current = current
        self._previous = previous
        self._reads = frozenset(reads)
        self._sample_every = max(1, sample_every)

    def __getattr__(self, name):
        method = getattr(self._current, name)
        if name not in self._reads:
            return method

        def read(*args, **kwargs):
            result = method(*args, **kwargs)
            if secrets.randbelow(self._sample_every) == 0:
                try:
                    compare(result, getattr(self._previous, name)(*args, **kwargs))
                except Exception:
                    # Backend errors often contain document text or filesystem
                    # paths. Never log their messages, arguments or traceback.
                    logger.info("store_dual_read comparison_unavailable")
            return result

        return read
