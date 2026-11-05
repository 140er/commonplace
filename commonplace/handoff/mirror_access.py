"""Apply thread read boundaries to every outgoing mirror drawer."""

import re

from commonplace.policy import canonical_wing
from commonplace.rules import checks

from .models import MIRROR_WING
from .recall import Recall

_DROP = object()
_THREAD_ROOM = re.compile(r"thr_[0-9a-f]+\Z")


def gate(gateway, principal, rule, result):
    """Scrub mirror words, caching access decisions only within this response."""
    allowed = {}

    def can_read(room):
        if room not in allowed:
            allowed[room] = False
            try:
                ctx = gateway.handoff
                recall = ctx.recall
                if checks._deadline.get() is not None:
                    checks.remaining(0)
                    store = ctx._stores.get(principal.workspace_id)
                    if store is None:
                        return False
                    recall = Recall(lambda _workspace: store, private=recall._private)
                allowed[room] = recall.can_read(principal, room)
            except Exception:
                # Missing context, store errors and expired deadlines hide words.
                pass
        return allowed[room]

    def unreadable(wing, room):
        mirror = isinstance(wing, str) and canonical_wing(wing) == MIRROR_WING
        thread_room = isinstance(room, str) and _THREAD_ROOM.fullmatch(room)
        return (mirror or thread_room) and (not isinstance(room, str) or not can_read(room))

    def scrub(node):
        if isinstance(node, dict):
            if unreadable(node.get("wing"), node.get("room")):
                return _DROP
            if "connected_wing" in node and unreadable(
                node.get("connected_wing"), node.get("connected_room")
            ):
                return _DROP
            # Preview metadata belongs to the drawer, not to the endpoint.
            if (
                "drawer_preview" in node or (node.get("drawer_id") and "connected_wing" in node)
            ) and (
                not isinstance(node.get("drawer_wing"), str)
                or not node["drawer_wing"].strip()
                or not isinstance(node.get("drawer_room"), str)
                or not node["drawer_room"].strip()
                or unreadable(node["drawer_wing"], node["drawer_room"])
            ):
                return _DROP
            out = {}
            for key, value in node.items():
                cleaned = scrub(value)
                if cleaned is _DROP:
                    return _DROP
                out[key] = cleaned
            if all(out[k] is v for k, v in node.items()):
                return node
            _recount(node, out)
            return out
        if isinstance(node, list):
            out = [cleaned for item in node if (cleaned := scrub(item)) is not _DROP]
            return node if len(out) == len(node) and all(a is b for a, b in zip(out, node)) else out
        return node

    def missing(node):
        drawer_id = node.get("drawer_id") if isinstance(node, dict) else None
        return {"error": f"Drawer not found: {drawer_id}"}

    if rule in ("drawer_read", "search", "read_wing") and not isinstance(result, (dict, list)):
        return missing(result) if rule == "drawer_read" else {"error": "not found", "denied": True}
    if (
        rule == "drawer_read"
        and isinstance(result, dict)
        and isinstance(result.get("results"), list)
    ):
        items = []
        errors = 0
        for item in result["results"]:
            cleaned = scrub(item)
            if cleaned is _DROP:
                cleaned = {"drawer_id": item.get("drawer_id"), **missing(item)}
            errors += bool(isinstance(cleaned, dict) and cleaned.get("error"))
            items.append(cleaned)
        if all(a is b for a, b in zip(items, result["results"])):
            return result
        out = {**result, "results": items, "count": len(items), "errors": errors}
        out.pop("total", None)
        return out
    cleaned = scrub(result)
    if cleaned is _DROP:
        return missing(result) if rule == "drawer_read" else {"error": "not found", "denied": True}
    return cleaned


def _recount(original, cleaned):
    for key in ("drawers", "results"):
        if isinstance(cleaned.get(key), list) and cleaned[key] is not original.get(key):
            cleaned.pop("total", None)
            if "count" in cleaned:
                cleaned["count"] = len(cleaned[key])
