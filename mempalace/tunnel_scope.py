"""Private metadata-only protocol for explicit tunnel reads."""

from time import monotonic

from .read_scope import PAGE_SIZE, descriptor, metadata_page, valid_descriptors

# Two optional drawer associations per tunnel fit in one metadata batch.
TUNNEL_PAGE = PAGE_SIZE // 2


def valid_tunnel(row):
    if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
        return False
    for key in ("source", "target"):
        pair = row.get(key)
        if not isinstance(pair, dict) or any(
            not isinstance(pair.get(k), str) or not pair[k].strip() for k in ("wing", "room")
        ):
            return False
        if pair.get("drawer_id") is not None and (
            not isinstance(pair["drawer_id"], str) or not pair["drawer_id"].strip()
        ):
            return False
    return True


def drawer_ids(rows):
    return list(
        dict.fromkeys(
            r[k]["drawer_id"] for r in rows for k in ("source", "target") if r[k].get("drawer_id")
        )
    )


def associations(rows, config, backend, get_collection, end):
    ids = drawer_ids(rows)
    found = {}
    for start in range(0, len(ids), PAGE_SIZE):
        batch = ids[start : start + PAGE_SIZE]
        records, _ = metadata_page(
            config, backend, get_collection, ids=batch, timeout=end - monotonic()
        )
        for key, meta in records:
            if key not in batch or key in found:
                raise ValueError("invalid drawer metadata")
            if not isinstance(meta, dict) or any(
                not isinstance(meta.get(k), str) or not meta[k].strip() or meta[k] == "?"
                for k in ("wing", "room")
            ):
                continue
            item = descriptor(key, meta)
            if item is not None:
                found[key] = item
    return found


def scoped_tunnels(scope, rows, config, backend, get_collection):
    """Probe contains no content; allow binds full tunnel and drawer metadata.

    Re-read stored endpoints and current drawer metadata before returning any
    tunnel for preview hydration or traversal recording. No allow means zero.
    """
    if not isinstance(scope, dict):
        raise ValueError("invalid tunnel scope")
    timeout = scope.get("timeout")
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 660:
        raise ValueError("invalid tunnel deadline")
    end = monotonic() + timeout
    if set(scope) == {"probe", "after", "timeout"} and scope["probe"] is True:
        after = scope["after"]
        if type(after) is not int or after < 0:
            raise ValueError("invalid tunnel cursor")
        page = rows[after : after + TUNNEL_PAGE]
        valid = [r for r in page if valid_tunnel(r)]
        found = associations(valid, config, backend, get_collection, end)
        entries = [
            {"tunnel": r, "drawers": [found[i] for i in drawer_ids([r])]}
            for r in valid
            if all(i in found for i in drawer_ids([r]))
        ]
        return {
            "probe": {
                "rows": entries,
                "next": after + len(page) if after + len(page) < len(rows) else None,
            }
        }
    if set(scope) != {"allow", "timeout"} or not isinstance(scope["allow"], list):
        raise ValueError("invalid tunnel allow")
    allowed = scope["allow"]
    if len(allowed) > TUNNEL_PAGE:
        raise ValueError("oversized tunnel allow")
    for item in allowed:
        if (
            not isinstance(item, dict)
            or set(item) != {"tunnel", "drawers"}
            or not valid_tunnel(item["tunnel"])
            or not valid_descriptors(item["drawers"])
            or set(drawer_ids([item["tunnel"]])) != {r["id"] for r in item["drawers"]}
        ):
            raise ValueError("invalid tunnel descriptor")
    current = [r for r in rows if valid_tunnel(r)]
    selected = [item for item in allowed if item["tunnel"] in current]
    found = associations([i["tunnel"] for i in selected], config, backend, get_collection, end)
    if monotonic() >= end:
        raise TimeoutError("tunnel deadline expired")
    return [i["tunnel"] for i in selected if all(found.get(d["id"]) == d for d in i["drawers"])]
