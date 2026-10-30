"""Mirror stored threads into the workspace palace. Owner: part T03.

Each chunk becomes one drawer in wing ``THREADS_WING`` (``threads``), room
``<stored thread id>``, verbatim chunk text, so normal Memory search finds
old threads too. Written through the gateway's own worker client
(``call_tool(principal, "mempalace_add_drawer", args)``), so authorship is
server-stamped. Idempotent: a retry adds no duplicate drawers.

Best effort: the lossless copy is ``thread_store``; a mirror failure is
logged and reported, it does not fail the handoff.
"""

from __future__ import annotations

from typing import Callable

from .models import RecallHit, StoredThread

CallTool = Callable  # (principal, tool_name, arguments) -> parsed result dict


def mirror(call_tool: CallTool, principal, stored: StoredThread, chunks: list) -> list[str]:
    """Write the chunks as drawers; return their drawer ids."""
    raise NotImplementedError


def palace_search(
    call_tool: CallTool, principal, stored_ids: list[str], query: str, limit: int = 8
) -> list[RecallHit]:
    """Semantic search over mirrored chunks of ``stored_ids`` only."""
    raise NotImplementedError
