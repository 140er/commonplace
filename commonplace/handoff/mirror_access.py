"""Apply thread read boundaries to every outgoing mirror drawer."""

from commonplace.policy import canonical_wing
from commonplace.rules import checks

from .models import MIRROR_WING
from .recall import Recall

_DROP = object()
_WORDS = ("text", "content", "document", "snippet")


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

    def scrub(node):
        if isinstance(node, dict):
            wing = node.get("wing")
            if isinstance(wing, str) and canonical_wing(wing) == MIRROR_WING:
                room = node.get("room")
                if isinstance(room, str) and room:
                    if not can_read(room):
                        return _DROP
                elif any(isinstance(node.get(k), str) and node[k] for k in _WORDS):
                    return _DROP
            out = {}
            for key, value in node.items():
                cleaned = scrub(value)
                if cleaned is _DROP:
                    return _DROP
                out[key] = cleaned
            return out
        if isinstance(node, list):
            return [cleaned for item in node if (cleaned := scrub(item)) is not _DROP]
        return node

    def missing(node):
        drawer_id = node.get("drawer_id") if isinstance(node, dict) else None
        return {"error": f"Drawer not found: {drawer_id}"}

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
        return {**result, "results": items, "count": len(items), "errors": errors}
    cleaned = scrub(result)
    if cleaned is _DROP:
        return missing(result) if rule == "drawer_read" else {"error": "not found", "denied": True}
    return cleaned
