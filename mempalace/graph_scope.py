"""Bounded metadata-only graph projection protocol; no shared graph cache."""

import hashlib
import json
import sqlite3
from contextlib import closing

from .read_scope import metadata_page, valid_descriptors


def graph_descriptor(drawer_id, metadata):
    """Bind literal graph values, without overview placeholder normalization."""
    stamp = hashlib.sha256(
        json.dumps(metadata, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
    ).hexdigest()
    return {"id": drawer_id, "wing": metadata["wing"], "room": metadata["room"], "stamp": stamp}


def graph_metadata(meta, *, text_loci=False, chroma_grouped=False, cast_text=str):
    if not isinstance(meta, dict):
        return None
    projected = dict(meta)
    if text_loci:
        for key in ("wing", "room", "hall"):
            value = meta.get(key)
            if type(value) in (int, float):
                projected[key] = cast_text(value)
    if any(not isinstance(projected.get(k), str) or not projected[k] for k in ("wing", "room")):
        return None
    if any(
        projected.get(k) is not None and not isinstance(projected[k], (str, int, float, bool))
        for k in ("hall", "date")
    ):
        return None
    result = {k: str(projected.get(k) or "") for k in ("wing", "room", "hall", "date")}
    if chroma_grouped:
        # Chroma ignores the separate boolean column in grouped hall reads.
        if isinstance(meta.get("hall"), bool):
            result["hall"] = ""
        # Chroma's grouped dates read only the string column.
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
    # Use SQLite's own numeric text representation (including exponent forms)
    # once per bounded page; no palace data or schema is written.
    with closing(sqlite3.connect(":memory:")) as casts:
        for key, meta in records:
            projected = graph_metadata(
                meta,
                text_loci=not used_collection
                and backend in ("chroma", "sqlite_exact", "rust_exact"),
                chroma_grouped=backend == "chroma" and not used_collection,
                cast_text=lambda value: casts.execute(
                    "SELECT CAST(? AS TEXT)", (value,)
                ).fetchone()[0],
            )
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
            "order": (
                []
                if used_collection
                else ["room", "wing", "hall"]
                if backend == "chroma"
                else ["wing", "room", "hall"]
            ),
        }
    by_id = {r["descriptor"]["id"]: r for r in rows}
    return {"rows": [by_id[r["id"]] for r in allowed if r["id"] in by_id]}
