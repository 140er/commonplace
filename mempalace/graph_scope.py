"""Bounded metadata-only graph projection protocol; no shared graph cache."""

from .metadata_types import is_bookkeeping
from .read_scope import descriptor, metadata_page, valid_descriptors


def graph_metadata(meta):
    if not isinstance(meta, dict):
        return None
    if any(not isinstance(meta.get(k), str) or not meta[k] for k in ("wing", "room")):
        return None
    if any(
        meta.get(k) is not None and not isinstance(meta[k], (str, int, float, bool))
        for k in ("hall", "date")
    ):
        return None
    return {k: str(meta.get(k) or "") for k in ("wing", "room", "hall", "date")}


def scoped_rows(scope, config, backend, get_collection):
    if not isinstance(scope, dict):
        raise ValueError("invalid graph scope")
    probe = scope.get("probe") is True
    if set(scope) != ({"probe", "after", "timeout"} if probe else {"allow", "timeout"}):
        raise ValueError("invalid graph scope")
    allowed = scope.get("allow")
    if not probe and not valid_descriptors(allowed):
        raise ValueError("invalid graph descriptors")
    records, cursor = metadata_page(
        config,
        backend,
        get_collection,
        after=scope.get("after"),
        ids=None if probe else [r["id"] for r in allowed],
        timeout=scope.get("timeout"),
    )
    rows = []
    for key, meta in records:
        projected = graph_metadata(meta)
        if projected is None or is_bookkeeping(meta, key):
            continue
        # Bind every graph field, while unrelated stored fields stay untouched.
        item = descriptor(key, projected)
        if item is not None:
            if probe:
                rows.append(item)
            elif item in allowed:
                rows.append({"descriptor": item, "metadata": projected})
    if probe:
        return {
            "rows": rows,
            "next": cursor,
            "grouped": backend in ("chroma", "compact", "sqlite_exact", "rust_exact"),
        }
    by_id = {r["descriptor"]["id"]: r for r in rows}
    return {"rows": [by_id[r["id"]] for r in allowed if r["id"] in by_id]}
