"""Answers read from the topology graph: ``topology_get`` and ``topology_route``.

Pure functions over ``store.read_graph``: no storage, no authorization. An answer
always says how fresh each hop is, and says plainly when something is unknown or
stale instead of filling the gap. Callers check the ``topology`` wing first.
"""

from __future__ import annotations

import heapq
import itertools

from . import model

# A way to reach a machine. ``dns``, ``proxy`` and ``reverse_proxy`` only appear in site routes.
CONNECT_METHODS = ("direct", "jump_host", "tailscale", "relay", "hosts")
NODE_FIELDS = (
    "kind",
    "name",
    "role",
    "hostname",
    "aliases",
    "tailscale_name",
    "runner_id",
    "wings",
)
MAX_ROUTES = 3
MAX_DEPTH = 8
MAX_PATHS = 500
# Added to a hop's cost so a fresh, working way is ranked before a doubtful one.
_PENALTY = {"up": 0.0, "degraded": 0.5, "unknown": 1.0, "down": 3.0}


def _index(graph):
    return (
        {record["key"]: record for record in graph["nodes"]},
        {record["id"]: record for record in graph["links"]},
        {record["site"]: record for record in graph["routes"]},
    )


def node_view(record, key=None) -> dict:
    """What an agent needs to reach a node, and which of it has gone stale."""
    if record is None:
        return {"key": key, "known": False}
    values = record["values"]
    view = {"key": record["key"], "known": True, "stale": record["stale"]}
    view.update({name: values[name] for name in NODE_FIELDS if name in values})
    stale = sorted(name for name in NODE_FIELDS if name in record["fields"] and name not in values)
    if stale:
        view["stale_fields"] = stale
    if record["conflicted"]:
        view["conflicted"] = True
    return view


def _brief(nodes, key) -> dict:
    record = nodes.get(key)
    if record is None:
        return {"key": key, "known": False}
    values = record["values"]
    brief = {"key": key, "known": True}
    brief.update({name: values[name] for name in ("kind", "name") if name in values})
    return brief


def hop_view(link_id, links, nodes) -> dict:
    """One hop with its method, its alias and port, and how fresh it is."""
    source, target, method = model.parse_link_id(link_id)
    link = links.get(link_id)
    hop = {
        "link": link_id,
        "from": _brief(nodes, source),
        "to": _brief(nodes, target),
        "method": method,
        "known": link is not None,
    }
    if link is None:
        hop.update(stale=True, status="unknown", note="no facts are recorded for this link")
        return hop
    values, fields = link["values"], link["fields"]
    hop["stale"] = link["stale"]
    hop["status"] = values.get("status", "unknown")
    for name in ("via", "alias", "port"):
        if name in values:
            hop[name] = values[name]
    if "status" in fields:
        hop["status_observed_at"] = fields["status"]["observed_at"]
        hop["status_expires_at"] = fields["status"]["expires_at"]
        if fields["status"]["stale"]:
            hop["status_note"] = "the last status is stale and not shown as current"
    stale_fields = sorted(
        name for name in ("via", "alias", "port") if name in fields and name not in values
    )
    if stale_fields:
        hop["stale_fields"] = stale_fields
    identity = fields.get("method")
    if identity is not None:
        hop["observed_at"] = identity["observed_at"]
        hop["expires_at"] = identity["expires_at"]
    if link["conflicted"]:
        hop["conflicted"] = True
    return hop


def _path_status(hops) -> str:
    statuses = {hop["status"] for hop in hops}
    if "down" in statuses:
        return "down"
    if "unknown" in statuses or any(hop["stale"] for hop in hops):
        return "unknown"
    return "degraded" if "degraded" in statuses else "up"


def _cost(link) -> float:
    status = link["values"].get("status", "unknown")
    return 1.0 + _PENALTY.get(status, 1.0) + (1.0 if link["stale"] else 0.0)


# ── topology_get ──────────────────────────────────────────────────────────
def get_answer(graph, *, node=None, site=None, now: str) -> dict:
    """A node (with its links), a site's route, or the whole graph."""
    if node is not None and site is not None:
        raise model.TopologyError("no: ask for a node or a site, not both")
    nodes, links, routes = _index(graph)
    if node is not None:
        key = model.node_key(node, "node")
        record = nodes.get(key)
        if record is None:
            return {
                "scope": "node",
                "key": key,
                "found": False,
                "as_of": now,
                "answer": f"unknown: nothing is recorded for node {key!r}",
            }
        touching = [link for link in graph["links"] if key in (link["from"], link["to"])]
        return {
            "scope": "node",
            "key": key,
            "found": True,
            "as_of": now,
            "node": record,
            "links": touching,
            "routes": [route for route in graph["routes"] if route["site"] == key],
        }
    if site is not None:
        key = model.node_key(site, "site")
        route = routes.get(key)
        if route is None:
            return {
                "scope": "site",
                "key": key,
                "found": False,
                "as_of": now,
                "answer": f"unknown: no route is recorded for site {key!r}",
            }
        return {
            "scope": "site",
            "key": key,
            "found": True,
            "as_of": now,
            "route": route,
            "node": nodes.get(key),
        }
    return {
        "scope": "graph",
        "found": True,
        "as_of": now,
        "counts": {
            "nodes": len(graph["nodes"]),
            "links": len(graph["links"]),
            "routes": len(graph["routes"]),
            "conflicts": len(graph["conflicts"]),
        },
        **{name: graph[name] for name in ("nodes", "links", "routes", "conflicts")},
    }


# ── topology_route ────────────────────────────────────────────────────────
def route_answer(graph, target, origin=None, *, now: str) -> dict:
    """How to connect to a machine, or where a site is hosted."""
    target = model.node_key(target, "target")
    if origin is not None:
        origin = model.node_key(origin, "from")
    nodes, links, routes = _index(graph)
    node = nodes.get(target)
    if target in routes or (node is not None and node["values"].get("kind") == "website"):
        return _hosting(target, routes.get(target), nodes, links, now)
    return _connect(target, origin, node, nodes, links, now)


def _hosting(site, route, nodes, links, now) -> dict:
    base = {"mode": "hosting", "target": site, "as_of": now}
    if route is None:
        return {
            **base,
            "found": False,
            "status": "unknown",
            "route": None,
            "hosted_on": None,
            "answer": f"unknown: site {site!r} is known but no route is recorded for it",
        }
    hops = [hop_view(hop["link"], links, nodes) for hop in route["hops"]]
    unknown = [hop["link"] for hop in hops if not hop["known"]]
    stale = [hop["link"] for hop in hops if hop["known"] and hop["stale"]]
    down = [hop["link"] for hop in hops if hop["status"] == "down"]
    complete = not unknown and not stale and not route["stale"]
    last = hops[-1]["to"]["key"] if hops else None
    problems = []
    if route["stale"]:
        problems.append("the route itself has not been confirmed recently")
    if unknown:
        problems.append("unknown hops: " + ", ".join(unknown))
    if stale:
        problems.append("stale hops: " + ", ".join(stale))
    if down:
        problems.append("hops reported down: " + ", ".join(down))
    chain = " -> ".join([hops[0]["from"]["key"]] + [hop["to"]["key"] for hop in hops])
    answer = f"site {site!r} is routed {chain}" + (
        "." if not problems else "; " + "; ".join(problems) + "."
    )
    result = {
        **base,
        "found": True,
        "status": "complete" if complete else "partial",
        "route": {"site": site, "stale": route["stale"], "hops": hops},
        "hosted_on": node_view(nodes.get(last), last) if last else None,
        "answer": answer,
    }
    for name, value in (("unknown_hops", unknown), ("stale_hops", stale), ("down_hops", down)):
        if value:
            result[name] = value
    return result


def _connect(target, origin, node, nodes, links, now) -> dict:
    base = {"mode": "connect", "target": target, "as_of": now}
    if node is None:
        return {
            **base,
            "found": False,
            "status": "unknown",
            "node": None,
            "routes": [],
            "answer": f"unknown: nothing is recorded for {target!r}; it is not a known machine or site",
        }
    base.update(found=True, node=node_view(node, target))
    usable = [link for link in links.values() if link["method"] in CONNECT_METHODS]
    if origin is None:
        inbound = sorted(
            (link for link in usable if link["to"] == target), key=lambda link: _cost(link)
        )
        options = [[hop_view(link["id"], links, nodes)] for link in inbound]
        return {
            **base,
            "status": "partial" if options else "unknown",
            "routes": [_route(hops) for hops in options[:MAX_ROUTES]],
            "answer": (
                f"{len(options)} recorded way(s) in to {target!r}; pass 'from' for the full path "
                "from one machine"
                if options
                else f"unknown: no recorded way to reach {target!r}"
            ),
        }
    if origin not in nodes:
        return {
            **base,
            "status": "unknown",
            "routes": [],
            "answer": f"unknown: the starting machine {origin!r} is not recorded",
        }
    if origin == target:
        return {
            **base,
            "status": "up",
            "routes": [],
            "answer": f"{target!r} is the starting machine",
        }
    paths = _paths(usable, origin, target)
    if not paths:
        return {
            **base,
            "status": "unknown",
            "routes": [],
            "answer": f"unknown: no recorded path from {origin!r} to {target!r}",
        }
    routes = [_route([hop_view(link["id"], links, nodes) for link in path]) for path in paths]
    best = routes[0]
    note = "" if best["status"] == "up" else f" (status {best['status']}: see each hop)"
    return {
        **base,
        "status": best["status"],
        "routes": routes,
        "answer": f"{origin!r} reaches {target!r} in {len(best['hops'])} hop(s){note}",
    }


def _route(hops) -> dict:
    return {
        "status": _path_status(hops),
        "stale": any(hop["stale"] for hop in hops),
        "hops": hops,
    }


def _paths(usable, origin, target) -> list[list[dict]]:
    """Up to ``MAX_ROUTES`` loop-free paths from ``origin`` to ``target``, best first."""
    outgoing = {}
    for link in usable:
        outgoing.setdefault(link["from"], []).append(link)
    ticket = itertools.count()
    queue = [(0.0, next(ticket), origin, [])]
    found = []
    seen = 0
    while queue and len(found) < MAX_ROUTES and seen < MAX_PATHS:
        cost, _, here, path = heapq.heappop(queue)
        seen += 1
        if here == target:
            found.append(path)
            continue
        if len(path) >= MAX_DEPTH:
            continue
        visited = {origin} | {link["to"] for link in path}
        for link in outgoing.get(here, ()):
            if link["to"] not in visited:
                heapq.heappush(queue, (cost + _cost(link), next(ticket), link["to"], path + [link]))
    return found
