"""MCP tools ``thread_recall``, ``thread_get``, ``thread_chain``.

Gateway extension tools (``commonplace.extensions``): served to tokens with
``threads:read`` whose tool list allows them (profiles ``standard`` and
``full``; explicit ``--tools`` lists may name them), never to untrusted
tokens, and only while the workspace uses handoff (a setting turned it on
somewhere, or it already has stored threads). Otherwise they are missing
from ``tools/list`` and answer ``Unknown tool``. Names and arguments are new,
never renamed later.

thread_recall(thread_id, query, limit=8, follow_chain=true)
thread_get(thread_id, start=0, end omitted, include_raw=false)
thread_chain(thread_id)
"""

from __future__ import annotations

import sqlite3

from commonplace.extensions import WORK_PROFILES, LocalTool, ToolError, register

from .models import SCOPE_THREADS_READ
from .recall import ThreadNotFound, thread_provenance

TOOL_NAMES = ("thread_recall", "thread_get", "thread_chain")

_SCHEMAS = {
    "thread_recall": {
        "name": "thread_recall",
        "description": "Search the stored thread of an earlier agent. Results include source and proposal/unknown authority; transcript roles do not prove user approval.",
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
        "description": "Read exact messages with source and approval status. Transcript roles do not verify who decided a rule.",
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


def _available(principal, gateway) -> bool:
    ctx = getattr(gateway, "handoff", None)
    if ctx is None:
        return False
    workspace_id = principal.workspace_id
    if any(row.enabled is True for row in ctx.handoff_db.list_settings(workspace_id)):
        return True
    if not ctx.has_threads(workspace_id):
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


def _provenance(ctx, principal, thread_id):
    stored = ctx.recall.thread(principal, thread_id)
    return thread_provenance(stored)


def _recall(ctx, principal, args):
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
                "provenance": _provenance(ctx, principal, hit.stored_thread_id),
            }
            for hit in hits
        ],
    }


def _get(ctx, principal, args):
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
    return {
        "thread_id": args["thread_id"],
        "messages": values,
        "provenance": _provenance(ctx, principal, args["thread_id"]),
    }


def _chain(ctx, principal, args):
    threads = ctx.recall.chain(principal, args["thread_id"])
    return {
        "threads": [
            {
                "thread_id": thread.id,
                "created_at": thread.created_at,
                "message_count": thread.message_count,
                "provenance": _provenance(ctx, principal, thread.id),
            }
            for thread in threads
        ]
    }


def _handler(name, action):
    schema = _SCHEMAS[name]["inputSchema"]

    def handle(tool_ctx, args):
        ctx = getattr(tool_ctx.gateway, "handoff", None)
        if ctx is None:
            raise ToolError("thread not found")
        error = _argument_error(args, schema)
        if error is not None:
            raise ToolError(f"invalid arguments: {error}")
        try:
            return action(ctx, tool_ctx.principal, args)
        except ThreadNotFound:
            raise ToolError("thread not found") from None

    return handle


for _name, _action in zip(TOOL_NAMES, (_recall, _get, _chain)):
    register(
        LocalTool(
            name=_name,
            description=_SCHEMAS[_name]["description"],
            input_schema=_SCHEMAS[_name]["inputSchema"],
            handler=_handler(_name, _action),
            scope=SCOPE_THREADS_READ,
            kind="read",
            profiles=WORK_PROFILES,
            trusted_only=True,
            available=_available,
        )
    )
