"""Apply thread read boundaries to every outgoing mirror drawer."""

import re
from urllib.parse import unquote, urlsplit

from commonplace.policy import canonical_wing
from commonplace.rules import checks

from .models import MIRROR_WING
from .recall import Recall

_DROP = object()
_THREAD_ROOM = re.compile(r"thr_[0-9a-f]+\Z")


class _ResponseAccess:
    """Live authority cached for one outgoing response, including startup clones."""

    def __init__(self, gateway, principal):
        self.gateway, self.principal = gateway, principal
        self.rooms, self.records = {}, {}

    def recall(self):
        ctx = self.gateway.handoff
        recall = ctx.recall
        if checks._deadline.get() is not None:
            checks.remaining(0)
            store = ctx._stores.get(self.principal.workspace_id)
            if store is None:
                return None
            recall = Recall(
                lambda _: store, private=recall._private, import_eligible=recall._import_eligible
            )
        return recall

    def can_read(self, room):
        if room not in self.rooms:
            self.rooms[room] = False
            try:
                recall = self.recall()
                self.rooms[room] = recall is not None and recall.can_read(self.principal, room)
            except Exception:
                pass
        return self.rooms[room]

    def ordinary_record(self, node):
        try:
            recall = self.recall()
            return recall is not None and recall.ordinary_record(self.principal, node.get("room"))
        except Exception:
            return False

    def import_allowed(self, node):
        key = id(node)
        if key not in self.records:
            self.records[key] = False
            try:
                recall = self.recall()
                self.records[key] = recall is not None and recall.import_record_allowed(
                    self.principal, node
                )
            except Exception:
                pass
        return self.records[key]


def gate(gateway, principal, rule, result):
    """Scrub mirror words, caching access decisions only within this response."""
    access = _ResponseAccess(gateway, principal)
    can_read = access.can_read
    import_allowed = access.import_allowed
    reader_recall = access.recall
    reader_scope = principal.policy.has_scope("memory:import:read")

    def unreadable(wing, room):
        mirror = isinstance(wing, str) and canonical_wing(wing) == MIRROR_WING
        thread_room = isinstance(room, str) and _THREAD_ROOM.fullmatch(room)
        return (mirror or thread_room) and (not isinstance(room, str) or not can_read(room))

    def scrub(node):
        if isinstance(node, dict):
            fact = _is_fact(node)
            import_row = False
            if (
                reader_scope
                and not fact
                and not access.ordinary_record(node)
                and any(key in node for key in ("drawer_id", "text", "content"))
                and (
                    node.get("wing") in ("private_threads", "private_chats")
                    or _thread_id(node.get("room"))
                )
            ):
                import_row = import_allowed(node)
                if not import_row:
                    return _DROP
                # Return only proved words and structural provenance. Arbitrary
                # labels, previews and links cannot piggyback on a safe range.
                node = _proved_record(node)
            if fact:
                proved = _reader_fact(node, reader_recall, principal) if reader_scope else set()
                if proved is None:
                    return _DROP
                node = _scrub_fact(node, lambda source: source in proved or can_read(source))
                if node is _DROP:
                    return _DROP
            node = _scrub_thread_links(node, can_read)
            if not import_row and unreadable(None if fact else node.get("wing"), node.get("room")):
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
            out = _clean_related(node, out)
            if len(out) == len(node) and all(out[k] is v for k, v in node.items()):
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
            return _filter_fact_links(result)
        out = {**result, "results": items, "count": len(items), "errors": errors}
        out.pop("total", None)
        return _filter_fact_links(out)
    cleaned = scrub(result)
    if cleaned is _DROP:
        return _dropped_result(rule, result)
    return _filter_fact_links(cleaned)


def _proved_record(node):
    out = {k: node[k] for k in ("drawer_id", "wing", "room", "text", "content") if k in node}
    for key in ("score", "similarity", "distance"):
        if type(node.get(key)) in (int, float):
            out[key] = node[key]
    metadata = node.get("metadata") or {}
    if str(node.get("drawer_id", "")).startswith("imported:"):
        out["metadata"] = {
            k: metadata[k] for k in ("source_thread", "import_view_sha256") if k in metadata
        }
    else:
        source = node.get("source_path") or node.get("source_file") or metadata.get("source_file")
        out["source_path"] = source
        out["metadata"] = {"source_file": source, "source_thread": node["room"]}
    return out


def _reader_fact(node, reader_recall, principal):
    """Only view-bearing sources require every-source proof; ordinary facts retain their gate."""
    try:
        recall = reader_recall()
        if recall is None:
            return None
        proved = set()
        for source, row in _fact_sources(node):
            if recall.ordinary_record(principal, source):
                continue
            if not recall.proves_import(
                principal,
                source,
                row.get("source_seq_start", row.get("seq_start")),
                row.get("source_seq_end", row.get("seq_end")),
            ):
                return None
            proved.add(source)
        return proved
    except Exception:
        return None


def _recount(original, cleaned):
    for key in ("drawers", "results"):
        if isinstance(cleaned.get(key), list) and cleaned[key] is not original.get(key):
            cleaned.pop("total", None)
            if "count" in cleaned:
                cleaned["count"] = len(cleaned[key])


def _is_fact(node):
    return bool({"predicate", "object", "fact", "triple_id"}.intersection(node))


def _thread_id(value):
    return isinstance(value, str) and _THREAD_ROOM.fullmatch(value) is not None


def _fact_sources(node):
    entries = []
    source = node.get("source")
    if isinstance(source, dict):
        entries.append((source.get("thread"), source))
    sources = node.get("sources")
    if isinstance(sources, list):
        entries.extend((row.get("source_thread"), row) for row in sources if isinstance(row, dict))
    if "source_thread" in node:
        entries.append((node["source_thread"], node))
    return entries


def _scrub_fact(node, can_read):
    sources = _fact_sources(node)
    private = any(
        isinstance(node.get(key), str) and canonical_wing(node[key]) == MIRROR_WING
        for key in ("wing", "theme")
    )
    if not private and not any(_thread_id(thread) for thread, _ in sources):
        return node
    readable = {thread for thread, _ in sources if _thread_id(thread) and can_read(thread)}
    if not readable:
        return _DROP
    out = dict(node)
    source = node.get("source")
    if "source" in node and (
        not isinstance(source, dict)
        or not _thread_id(source.get("thread"))
        or source["thread"] not in readable
    ):
        out.pop("source")
    if isinstance(node.get("sources"), list):
        kept = [
            row
            for row in node["sources"]
            if isinstance(row, dict)
            and _thread_id(row.get("source_thread"))
            and row["source_thread"] in readable
        ]
        if len(kept) != len(node["sources"]):
            out["sources"] = kept
    # Flat provenance describes the primary source; do not retain a hidden
    # primary thread's ids/links just because another source is readable.
    primary = node.get("source_thread")
    if primary is None and _thread_id(node.get("origin_id")):
        primary = node["origin_id"]
    if primary is not None and (not _thread_id(primary) or primary not in readable):
        for key in (
            "source_thread",
            "source_seq_start",
            "source_seq_end",
            "source_url",
            "source_link",
            "source_drawer_id",
            "source_file",
            "origin_id",
            "origin_link",
        ):
            out.pop(key, None)
    return node if out == node else out


def _scrub_thread_links(node, can_read):
    out = node
    for key in ("approval_link", "origin_link", "source_link", "source_url"):
        value = node.get(key)
        if not isinstance(value, str):
            continue
        try:
            match = re.search(r"/app/threads/(thr_[0-9a-f]+)(?:/|$)", unquote(urlsplit(value).path))
        except ValueError:
            match = None
        if match and not can_read(match[1]):
            if out is node:
                out = dict(node)
            out.pop(key)
    return out


def _clean_related(original, out):
    related = original.get("related_facts")
    if not isinstance(related, list) or len(out.get("related_facts", [])) == len(related):
        return out
    kept = {
        row.get("id") or row.get("triple_id")
        for row in out["related_facts"]
        if isinstance(row, dict)
    }
    superseded = original.get("superseded")
    if isinstance(superseded, list):
        out["superseded"] = [key for key in superseded if key in kept]
        previous = original.get("previous")
        if isinstance(previous, list) and len(previous) == len(superseded):
            out["previous"] = [value for key, value in zip(superseded, previous) if key in kept]
        else:
            out.pop("previous", None)
    else:
        out.pop("previous", None)
    if original.get("current") not in kept:
        out.pop("current", None)
    return out


def _dicts(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _dicts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _dicts(value)


def _filter_fact_links(node):
    kept = {
        value
        for row in _dicts(node)
        if {"object", "predicate", "fact"}.intersection(row)
        and isinstance(value := row.get("id") or row.get("triple_id"), str)
    }

    def prune(value):
        if isinstance(value, dict):
            out = {
                key: prune(child)
                for key, child in value.items()
                if key != "superseded_by" or isinstance(child, str) and child in kept
            }
            return (
                value
                if len(out) == len(value) and all(out[key] is child for key, child in value.items())
                else out
            )
        if isinstance(value, list):
            out = [prune(child) for child in value]
            return value if all(a is b for a, b in zip(out, value)) else out
        return value

    return prune(node)


def _dropped_result(rule, result):
    if (
        rule == "kg_write"
        and isinstance(result, dict)
        and result.get("success") is True
        and not result.get("error")
    ):
        return {key: result[key] for key in ("success", "triple_id", "action") if key in result}
    if rule == "drawer_read":
        drawer_id = result.get("drawer_id") if isinstance(result, dict) else None
        return {"error": f"Drawer not found: {drawer_id}"}
    return {"error": "not found", "denied": True}
