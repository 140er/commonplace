"""Authorize graph inputs before aggregation, traversal, ranking and counts."""

from commonplace.rules import checks
from mempalace import palace_graph
from mempalace.config import sanitize_name
from mempalace.read_scope import PAGE_SIZE, descriptor, valid_descriptors

from .mirror_access import reader, unreadable_room
from .overview import _valid_cursor
from .tunnels import project as project_tunnels


def _result(name, args, rows, tunnels):
    graph = palace_graph._nodes_edges_from_grouped_rows(
        (m["room"], m["wing"], m["hall"], 1, m["date"]) for m in rows
    )
    if name == "mempalace_traverse":
        return palace_graph.traverse(
            args["start_room"], max_hops=args["max_hops"], graph_data=graph
        )
    if name == "mempalace_find_tunnels":
        return palace_graph.find_tunnels(args.get("wing_a"), args.get("wing_b"), graph_data=graph)
    return palace_graph.graph_stats(graph_data=graph, explicit_tunnels=tunnels)


def _arguments(name, args):
    args = dict(args)
    if name == "mempalace_traverse":
        if not isinstance(args.get("start_room"), str):
            raise ValueError("start_room must be a string")
        hops = args.get("max_hops", 2)
        if type(hops) is not int:
            raise ValueError("max_hops must be an integer")
        args["max_hops"] = max(1, min(hops, 10))
    elif name == "mempalace_find_tunnels":
        for key in ("wing_a", "wing_b"):
            value = args.get(key)
            if isinstance(value, str) and not value.strip():
                args[key] = None
            elif value is not None:
                args[key] = sanitize_name(value, key)
    return args


def project(gateway, principal, workers, name, args, *, timeout=660.0):
    try:
        args = _arguments(name, args)
    except ValueError as exc:
        return {"error": str(exc)}
    empty = _result(name, args, [], [])
    if not getattr(principal, "workspace_id", None):
        return empty
    try:
        gateway.handoff.store_for(principal.workspace_id)
        with checks.deadline(timeout):

            def visible(access, row):
                return principal.policy.can_read_wing(row["wing"]) and not unreadable_room(
                    access, row["wing"], row["room"]
                )

            access = reader(gateway, principal)
            after, allowed, seen = None, [], set()
            grouped = None
            while True:
                page = workers.call_tool(
                    principal,
                    name,
                    {
                        **args,
                        "thread_scope": {
                            "probe": True,
                            "after": after,
                            "timeout": checks.remaining(timeout),
                        },
                    },
                )
                if (
                    not isinstance(page, dict)
                    or set(page) != {"rows", "next", "grouped"}
                    or type(page["grouped"]) is not bool
                    or grouped is not None
                    and page["grouped"] != grouped
                    or not valid_descriptors(page["rows"])
                    or not _valid_cursor(page["next"], after, page["rows"])
                ):
                    return empty
                grouped = page["grouped"]
                for row in page["rows"]:
                    checks.remaining(0)
                    if row["id"] in seen:
                        return empty
                    seen.add(row["id"])
                    if visible(access, row):
                        allowed.append(row)
                after = page["next"]
                if after is None:
                    break
            access = reader(gateway, principal)
            allowed = [row for row in allowed if visible(access, row)]
            rows = []
            for start in range(0, max(1, len(allowed)), PAGE_SIZE):
                batch = allowed[start : start + PAGE_SIZE]
                page = workers.call_tool(
                    principal,
                    name,
                    {
                        **args,
                        "thread_scope": {"allow": batch, "timeout": checks.remaining(timeout)},
                    },
                )
                checks.remaining(0)
                if (
                    not isinstance(page, dict)
                    or set(page) != {"rows"}
                    or not isinstance(page["rows"], list)
                ):
                    return empty
                returned = []
                for item in page["rows"]:
                    if (
                        not isinstance(item, dict)
                        or set(item) != {"descriptor", "metadata"}
                        or item["descriptor"] not in batch
                        or item["descriptor"] in returned
                        or not isinstance(item["metadata"], dict)
                        or set(item["metadata"]) != {"wing", "room", "hall", "date"}
                        or any(not isinstance(v, str) for v in item["metadata"].values())
                        or any(
                            item["metadata"][k] != item["descriptor"][k] for k in ("wing", "room")
                        )
                    ):
                        return empty
                    if descriptor(item["descriptor"]["id"], item["metadata"]) != item["descriptor"]:
                        return empty
                    returned.append(item["descriptor"])
                    rows.append(item["metadata"])
            tunnels = []
            if name == "mempalace_graph_stats":
                tunnels = project_tunnels(
                    gateway,
                    principal,
                    workers,
                    "mempalace_list_tunnels",
                    {},
                    timeout=checks.remaining(timeout),
                    raise_on_failure=True,
                )
                if not isinstance(tunnels, list):
                    return empty
            checks.remaining(0)
            access = reader(gateway, principal)
            rows = [row for row in rows if visible(access, row)]
            if grouped:
                rows.sort(key=lambda m: (m["room"], m["wing"], m["hall"]))
            result = _result(name, args, rows, tunnels)
            checks.remaining(0)
            return result
    except Exception:
        return empty
