"""Read stored threads: recall, ranges and the chain. Owner: part T07b.

Access: a principal reads a stored thread only if it has ``threads:read``,
the thread is in its workspace, and its policy can read the thread's wing
(``THREADS_WING`` unless the store says otherwise). Anything else looks
exactly like a missing thread (``ThreadNotFound``), never "forbidden".

``recall`` merges FTS (``ThreadStore.search``) with the palace mirror's
semantic hits when a ``call_tool`` is given, dedupes by (thread, chunk), and
with ``follow_chain`` also searches the older threads of the chain.
"""

from __future__ import annotations

from typing import Callable, Optional

from .models import RecallHit, StoredThread, ThreadMessage

MAX_RANGE = 200  # messages per thread_get


class ThreadNotFound(LookupError):
    pass


class Recall:
    def __init__(self, store_for: Callable, *, call_tool: Optional[Callable] = None):
        """``store_for(workspace_id) -> ThreadStore``."""
        raise NotImplementedError

    def thread(self, principal, stored_thread_id: str) -> StoredThread:
        raise NotImplementedError

    def chain(self, principal, stored_thread_id: str) -> list[StoredThread]:
        """This thread then its predecessors, newest first; stops at the
        first one the principal can't read; cycle-safe."""
        raise NotImplementedError

    def recall(
        self,
        principal,
        stored_thread_id: str,
        query: str,
        *,
        limit: int = 8,
        follow_chain: bool = True,
    ) -> list[RecallHit]:
        raise NotImplementedError

    def get(
        self, principal, stored_thread_id: str, start: int = 0, end: Optional[int] = None
    ) -> list[ThreadMessage]:
        """At most ``MAX_RANGE`` messages."""
        raise NotImplementedError
