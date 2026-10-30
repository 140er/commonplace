"""MCP tools ``thread_recall``, ``thread_get``, ``thread_chain``. Owner: part T10.

Registered as gateway ``LocalTool``s (``ctx.gateway.mediator.local_tools``):
visible only to tokens with ``threads:read`` whose tool list allows them
(profiles ``standard`` and ``full``; explicit ``--tools`` lists may name
them). Names and arguments are new, never renamed later.

thread_recall(thread_id, query, limit=8, follow_chain=true)
thread_get(thread_id, start=0, end omitted, include_raw=false)
thread_chain(thread_id)
"""

from __future__ import annotations

import sqlite3
from functools import partial
from pathlib import Path

from commonplace.gateway import LocalTool

from .models import SCOPE_THREADS_READ
from .recall import ThreadNotFound

TOOL_NAMES = ("thread_recall", "thread_get", "thread_chain")

_SCHEMAS = {
    "thread_recall": {
        "name": "thread_recall",
        "description": "Search the stored thread of an earlier agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "follow_chain": {"type": "boolean"},
            },
            "required": ["thread_id", "query"],
            "additionalProperties": False,
        },
    },
    "thread_get": {
        "name": "thread_get",
        "description": "Read messages from the stored thread of an earlier agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "string"},
                "start": {"type": "integer", "minimum": 0},
                "end": {"type": "integer"},
                "include_raw": {"type": "boolean"},
            },
            "required": ["thread_id"],
            "additionalProperties": False,
        },
    },
    "thread_chain": {
        "name": "thread_chain",
        "description": "List the stored threads in an agent's handoff chain.",
        "inputSchema": {
            "type": "object",
            "properties": {"thread_id": {"type": "string"}},
            "required": ["thread_id"],
            "additionalProperties": False,
        },
    },
}


def _available(ctx, workspace_id: str) -> bool:
    if any(
        row.workspace_id == workspace_id and row.enabled is True
        for row in ctx.handoff_db.list_settings(workspace_id)
    ):
        return True
    path = Path(ctx.data_dir) / "workspaces" / workspace_id / "threads.sqlite3"
    if not path.is_file():
        return False
    try:
        store = ctx.store_for(workspace_id)
        with store._lock:
            return (
                store._conn.execute(
                    "SELECT 1 FROM threads WHERE workspace_id = ? LIMIT 1", (workspace_id,)
                ).fetchone()
                is not None
            )
    except (OSError, sqlite3.Error):
        return False


def _refusal(ctx, principal, *, name: str):
    policy = principal.policy
    if not policy.has_scope(SCOPE_THREADS_READ):
        return f"needs scope {SCOPE_THREADS_READ}"
    if name not in policy.tool_names():
        return "tool not allowed for this connection"
    if policy.trust == "untrusted":
        return "untrusted connections can't read stored threads"
    if not _available(ctx, principal.workspace_id):
        return "handoff is not available"
    return None


def _argument_error(args, schema):
    if not isinstance(args, dict):
        return "arguments must be an object"
    for name in schema["required"]:
        if name not in args:
            return f"{name} is required"
    properties = schema["properties"]
    kinds = {"string": str, "integer": int, "boolean": bool}
    for name, value in args.items():
        if name not in properties:
            return f"unknown argument: {name}"
        rule = properties[name]
        if type(value) is not kinds[rule["type"]]:
            return f"{name} must be {rule['type']}"
        if "minimum" in rule and value < rule["minimum"]:
            return f"{name} must be at least {rule['minimum']}"
        if "maximum" in rule and value > rule["maximum"]:
            return f"{name} must be at most {rule['maximum']}"
    return None


def register(ctx) -> None:
    """Register thread tools; Recall enforces access to each requested thread."""

    def recall(principal, args):
        hits = ctx.recall.recall(
            principal,
            args["thread_id"],
            args["query"],
            **{name: args[name] for name in ("limit", "follow_chain") if name in args},
        )
        return {
            "thread_id": args["thread_id"],
            "hits": [
                {
                    "thread_id": hit.stored_thread_id,
                    "chunk": hit.chunk_index,
                    "seq_start": hit.seq_start,
                    "seq_end": hit.seq_end,
                    "score": hit.score,
                    "text": hit.text,
                }
                for hit in hits
            ],
        }

    def get(principal, args):
        messages = ctx.recall.get(
            principal,
            args["thread_id"],
            **{name: args[name] for name in ("start", "end") if name in args},
        )
        values = []
        for message in messages:
            value = message.to_dict()
            if not args.get("include_raw", False):
                value.pop("raw", None)
            values.append(value)
        return {"thread_id": args["thread_id"], "messages": values}

    def chain(principal, args):
        threads = ctx.recall.chain(principal, args["thread_id"])
        return {
            "threads": [
                {
                    "thread_id": thread.id,
                    "created_at": thread.created_at,
                    "message_count": thread.message_count,
                }
                for thread in threads
            ]
        }

    def handle(principal, args, *, schema, action):
        error = _argument_error(args, schema["inputSchema"])
        if error is not None:
            return {"error": f"invalid arguments: {error}"}
        try:
            return action(principal, args)
        except ThreadNotFound:
            return {"error": "thread not found"}

    for name, action in zip(TOOL_NAMES, (recall, get, chain)):
        schema = _SCHEMAS[name]
        ctx.gateway.mediator.local_tools[name] = LocalTool(
            name=name,
            schema=schema,
            handler=partial(handle, schema=schema, action=action),
            refusal=partial(_refusal, ctx, name=name),
        )
