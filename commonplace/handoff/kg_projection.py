"""Original-source authorization before knowledge graph read aggregation."""

from commonplace.rules import checks
from mempalace.knowledge_graph import valid_fact_entries

from .mirror_access import gate, reader


def project(gateway, principal, workers, name, args):
    suffix = name.removeprefix("mempalace_kg_")
    empty = {
        "query": {
            "entity": args.get("entity"),
            "as_of": args.get("as_of"),
            "facts": [],
            "active_facts": [],
            "historical_facts": [],
            "future_facts": [],
            "count": 0,
        },
        "timeline": {
            "entity": args.get("entity") or "all",
            "timeline": [],
            "count": 0,
            "total": 0,
            "offset": max(0, args.get("offset", 0)),
            "limit": max(1, min(100, args.get("limit", 100))),
        },
        "stats": {
            "entities": 0,
            "triples": 0,
            "current_facts": 0,
            "expired_facts": 0,
            "relationship_types": [],
            "wings": [],
        },
        "list_wings": {"wings": []},
    }[suffix]
    if not principal.workspace_id:
        return empty
    try:
        gateway.handoff.store_for(principal.workspace_id)
        with checks.deadline(660):
            access, after, allowed = reader(gateway, principal), None, []
            policy = principal.policy
            while True:
                result = workers.call_tool(
                    principal, name, {**args, "thread_scope": {"probe": True, "after": after}}
                )
                page = result.get("probe") if isinstance(result, dict) else None
                if not isinstance(page, dict) or set(page) != {"facts", "next"}:
                    return empty
                rows = page["facts"]
                if not isinstance(rows, list) or len(rows) > 500:
                    return empty
                stripped = [
                    {k: v for k, v in r.items() if k in {"id", "wing", "threads"}} for r in rows
                ]
                if not valid_fact_entries(stripped) or any(
                    set(r)
                    != {
                        "id",
                        "wing",
                        "threads",
                        "sensitivity",
                        "source_thread",
                        "source_seq_start",
                        "source_seq_end",
                        "sources",
                    }
                    for r in rows
                ):
                    return empty
                ids = [r["id"] for r in rows]
                if ids != sorted(set(ids)) or (after is not None and any(i <= after for i in ids)):
                    return empty
                cursor = page["next"]
                if cursor is not None and (not ids or cursor != ids[-1]):
                    return empty
                for row in rows:
                    checks.remaining(0)
                    if (
                        (
                            row["wing"] is None
                            and policy.sees_all_wings
                            or isinstance(row["wing"], str)
                            and policy.can_read_wing(row["wing"])
                        )
                        and (row["sensitivity"] or "internal") in policy.fact_sensitivities()
                        and gate(
                            gateway,
                            principal,
                            "kg_read",
                            {"facts": [{**row, "fact": ""}]},
                            access=access,
                        ).get("facts")
                    ):
                        allowed.append(row)
                if cursor is None:
                    break
                after = cursor
            checks.remaining(0)
            result = workers.call_tool(
                principal, name, {**args, "thread_scope": {"allow": allowed}}
            )
            if not isinstance(result, dict) or "error" in result or not set(empty) <= set(result):
                return empty
            cleaned = gate(gateway, principal, "kg_read", result, access=access)
            # The final citation scrub may not drop a fact after it contributed
            # to counts or pages. A failed proof discards the entire projection.
            for key in ("facts", "active_facts", "historical_facts", "future_facts", "timeline"):
                if key in result and (
                    not isinstance(result[key], list)
                    or len(cleaned.get(key, [])) != len(result[key])
                ):
                    return empty
            return cleaned
    except Exception:
        return empty
