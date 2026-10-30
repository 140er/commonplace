"""Lossless thread store with a full-text index. Owner: part T02.

One SQLite file per workspace (``<data>/workspaces/<id>/threads.sqlite3``,
mode 0600). Messages are stored whole, verbatim, one row per
``ThreadMessage`` (``raw`` included). Chunks are views over consecutive
messages (``chunk_messages``) indexed in FTS5 for ``search``.

Idempotent: storing the same ``source_thread_id`` again returns the existing
``StoredThread`` and writes nothing; storing different content under the
same source id raises ``ThreadConflict``. A crash mid-store leaves nothing
behind (one transaction). Never deletes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import CHUNK_MAX_CHARS, RecallHit, StoredThread, ThreadMessage


class ThreadConflict(ValueError):
    """Same source thread id, different content."""


@dataclass(frozen=True)
class Chunk:
    index: int
    seq_start: int  # inclusive
    seq_end: int  # inclusive
    text: str  # "[<seq>] <role> <kind>[ <name>]: <text>" lines, verbatim text


def chunk_messages(messages: list, max_chars: int = CHUNK_MAX_CHARS) -> list[Chunk]:
    """Group whole messages up to ``max_chars``. One message longer than
    ``max_chars`` is split into consecutive pieces of that message only
    (same seq_start == seq_end), so every character is in exactly one chunk."""
    raise NotImplementedError


def canonical_sha256(messages: list) -> str:
    """SHA-256 over the messages' (seq, role, kind, name, call_id, text, raw)."""
    raise NotImplementedError


class ThreadStore:
    def __init__(self, path: str):
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def store(
        self,
        workspace_id: str,
        source_thread_id: str,
        messages: list[ThreadMessage],
        *,
        actor: str,
        parent_stored_id: str = "",
    ) -> StoredThread:
        raise NotImplementedError

    def get(self, stored_id: str) -> Optional[StoredThread]:
        raise NotImplementedError

    def by_source(self, workspace_id: str, source_thread_id: str) -> Optional[StoredThread]:
        raise NotImplementedError

    def parent_of(self, stored_id: str) -> Optional[str]:
        """The stored thread this one's agent was a successor of, if any."""
        raise NotImplementedError

    def messages(
        self, stored_id: str, start: int = 0, end: Optional[int] = None
    ) -> list[ThreadMessage]:
        """Messages with ``start <= seq <= end`` (end None: to the last)."""
        raise NotImplementedError

    def chunks(self, stored_id: str) -> list[Chunk]:
        raise NotImplementedError

    def search(self, stored_ids: list[str], query: str, limit: int = 8) -> list[RecallHit]:
        """BM25 over the chunks of ``stored_ids``. Any query text is safe
        (quoted for FTS5, never a syntax error). Best first."""
        raise NotImplementedError
