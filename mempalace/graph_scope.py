"""Bounded metadata-only graph projection protocol; no shared graph cache."""

import hashlib
import json

from .read_scope import metadata_page, valid_descriptors


def graph_descriptor(drawer_id, metadata):
    """Bind literal graph values, without overview placeholder normalization."""
    stamp = hashlib.sha256(
        json.dumps(metadata, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
    ).hexdigest()
    return {"id": drawer_id, "wing": metadata["wing"], "room": metadata["room"], "stamp": stamp}


def graph_metadata(meta, *, chroma_grouped=False):
    if not isinstance(meta, dict):
        return None
    if any(not isinstance(meta.get(k), str) or not meta[k] for k in ("wing", "room")):
        return None
    if any(
        meta.get(k) is not None and not isinstance(meta[k], (str, int, float, bool))
        for k in ("hall", "date")
    ):
        return None
    result = {k: str(meta.get(k) or "") for k in ("wing", "room", "hall", "date")}
    if chroma_grouped:
        # The Chroma grouped reader casts numeric halls, but MAX(date) reads
        # only the string column. Preserve those backend-specific conventions.
        hall = meta.get("hall")
        result["hall"] = "" if hall is None or isinstance(hall, bool) else str(hall)
        result["date"] = meta.get("date") if isinstance(meta.get("date"), str) else ""
    return result


def scoped_rows(scope, config, backend, get_collection):
    if not isinstance(scope, dict):
        raise ValueError("invalid graph scope")
    probe = scope.get("probe") is True
    if set(scope) != ({"probe", "after", "timeout"} if probe else {"allow", "timeout"}):
        raise ValueError("invalid graph scope")
    allowed = scope.get("allow")
    if not probe and not valid_descriptors(allowed):
        raise ValueError("invalid graph descriptors")
    used_collection = False

    def collection():
        nonlocal used_collection
        used_collection = True
        return get_collection()

    records, cursor = metadata_page(
        config,
        backend,
        collection,
        after=scope.get("after"),
        ids=None if probe else [r["id"] for r in allowed],
        timeout=scope.get("timeout"),
    )
    rows = []
    for key, meta in records:
        projected = graph_metadata(meta, chroma_grouped=backend == "chroma" and not used_collection)
        if projected is None:
            continue
        # Bind every graph field, while unrelated stored fields stay untouched.
        item = graph_descriptor(key, projected)
        if item is not None:
            if probe:
                rows.append(item)
            elif item in allowed:
                rows.append({"descriptor": item, "metadata": projected})
    if probe:
        return {
            "rows": rows,
            "next": cursor,
            "grouped": not used_collection,
        }
    by_id = {r["descriptor"]["id"]: r for r in rows}
    return {"rows": [by_id[r["id"]] for r in allowed if r["id"] in by_id]}
