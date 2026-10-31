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
    """Expose only equality, without a reusable content fingerprint."""
    left, right = digest(current), digest(previous)
    logger.info("store_dual_read match=%s", left == right)


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


class CollectionReads(SampledReads):
    def __init__(self, current, previous_backend, previous_path, collection_name):
        from .backend_copy import open_collection

        previous = open_collection(previous_backend, previous_path, collection_name)
        super().__init__(current, previous, reads=("get", "query", "lexical_search"))

    def close(self):
        self._current.close()


def observe_collection(collection, palace_path, collection_name):
    """The marker is written only by a successfully verified workspace cutover."""
    from pathlib import Path

    from .backends import get_backend

    marker = Path(palace_path) / ".store-comparison.json"
    try:
        config = json.loads(marker.read_text(encoding="utf-8"))
        # Chroma may mutate storage even through a read-only API open.
        if config["backend"] == "chroma":
            return collection
        current = Path(palace_path).resolve()
        from commonplace.workspace_store import generation_path

        previous = (
            generation_path(current.parent.parent / config["workspace"], config["previous"])
            / "palace"
        )
        if not previous.is_dir():
            return collection
        backend = get_backend(config["backend"])
        try:
            return CollectionReads(collection, backend, previous, collection_name)
        except Exception:
            pass
    except (OSError, ValueError, KeyError):
        pass
    return collection
