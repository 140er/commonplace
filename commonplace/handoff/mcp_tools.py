"""MCP tools ``thread_recall``, ``thread_get``, ``thread_chain``. Owner: part T10.

Registered as gateway ``LocalTool``s (``ctx.gateway.mediator.local_tools``):
visible only to tokens with ``threads:read`` whose tool list allows them
(profiles ``standard`` and ``full``; explicit ``--tools`` lists may name
them). Names and arguments are new, never renamed later.

thread_recall(thread_id, query, limit=8, follow_chain=true)
thread_get(thread_id, start=0, end=null)
thread_chain(thread_id)
"""

from __future__ import annotations

TOOL_NAMES = ("thread_recall", "thread_get", "thread_chain")


def register(ctx) -> None:
    raise NotImplementedError
