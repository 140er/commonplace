"""Read behind the Compute map's node detail panel: ``GET /api/topology/node?key=<node key>``.

One node with the provenance and freshness of every field it holds (the map read leaves both
out) and, for a node that is an enrolled runner machine, the agents working there. Read-only.

Access is the topology wing grant, like every other topology read; what the agents part adds
is decided per card and per connection in ``node_agents``. A key no node holds is tried as an
enrolled runner machine's id (the Compute fallback without map nodes). See "Node detail read" in
``docs/topology.md``.
"""

import logging

from commonplace.http_util import log_failure, query, require_principal, send_json

from . import model, node_agents, store

PATH = "/api/topology/node"
LOG = logging.getLogger(__name__)
EVIDENCE_LINE = 200
# Fields the panel may show, in the order of the node's record in docs/topology.md.
SHOWN = (
    "kind",
    "name",
    "role",
    "hostname",
    "aliases",
    "tailscale_name",
    "runner_id",
    "wings",
    "hardware",
    "os",
    "resources",
)


def _source(entry: dict) -> dict:
    """One observation as the panel shows it: who saw it, when, and the first line of what they saw."""
    line = (entry["evidence"].strip().splitlines() or [""])[0]
    return {
        "kind": entry["observer"]["kind"],
        "id": entry["observer"]["id"],
        "observed_at": entry["observed_at"],
        "seen_count": entry["seen_count"],
        "evidence": line if len(line) <= EVIDENCE_LINE else line[: EVIDENCE_LINE - 1] + "…",
    }


def _field(field: dict) -> dict:
    sources = sorted(field["provenance"], key=lambda entry: entry["observed_at"], reverse=True)
    return {
        "value": field["value"],
        "stale": field["stale"],
        "observed_at": field["observed_at"],
        "expires_at": field["expires_at"],
        "sources": [_source(entry) for entry in sources],
        "contenders": len(field["conflicts"]),
    }


def node_view(record: dict | None, key: str) -> dict:
    """``record`` (from ``store.get_node``) as the panel reads it; an unrecorded key says so."""
    if record is None:
        return {"key": key, "found": False}
    return {
        "key": key,
        "found": True,
        "stale": record["stale"],
        "conflicted": record["conflicted"],
        "fields": {
            name: _field(record["fields"][name]) for name in SHOWN if name in record["fields"]
        },
    }


def _hop(hop: dict, links: dict) -> dict:
    link = links.get(hop["link"])
    view = {
        name: hop[name] for name in ("link", "from", "to", "method", "known", "stale", "status")
    }
    if link is not None:
        view["observed_at"] = link["fields"]["method"]["observed_at"]
        if "status" in link["fields"]:
            view["status_observed_at"] = link["fields"]["status"]["observed_at"]
    return view


def sites_view(graph: dict, key: str) -> list[dict]:
    """The site routes that start at, end at or pass through ``key``, each with its freshness.

    ``role`` is ``site`` for the website itself, ``hosted`` where the node is the end of the
    route or the machine that hosts what ends it, and ``through`` for every other hop."""
    links = {link["id"]: link for link in graph["links"]}
    found = []
    for route in graph["routes"]:
        hops = [_hop(hop, links) for hop in route["hops"]]
        mine = [index for index, hop in enumerate(hops) if key in (hop["from"], hop["to"])]
        if route["site"] != key and not mine:
            continue
        if route["site"] == key:
            role = "site"
        elif any(
            hops[i]["to"] == key
            and i == len(hops) - 1
            or hops[i]["from"] == key
            and hops[i]["method"] == "hosts"
            for i in mine
        ):
            role = "hosted"
        else:
            role = "through"
        found.append(
            {
                "site": route["site"],
                "role": role,
                "stale": route["stale"],
                "observed_at": route["fields"]["hops"]["observed_at"],
                "expires_at": route["fields"]["hops"]["expires_at"],
                "sources": [_source(entry) for entry in route["fields"]["hops"]["provenance"]][:3],
                "hops": len(hops),
                "at": [{"index": index, **hops[index]} for index in mine],
            }
        )
    return found


def install(gateway):
    def handle(h):
        principal = require_principal(gateway, h)
        if principal is None:
            return
        if not principal.policy.can_read_wing(model.TOPOLOGY_WING):
            send_json(h, 403, {"error": "no: this connection cannot read the topology wing"})
            return
        try:
            params = query(h)
            if params.keys() - {"key"}:
                raise model.TopologyError("no: invalid arguments")
            key = model.node_key(params.get("key"), "key")
        except model.TopologyError as exc:
            send_json(h, 400, {"error": str(exc)})
            return
        try:
            now = model.clock(None)
            topo = store.open_topology(gateway, principal.workspace_id)
            graph = store.read_graph(topo, now=now)
            record = next((node for node in graph["nodes"] if node["key"] == key), None)
            view = node_view(record, key)
            # A key the map does not hold may still be an enrolled runner machine's id: the
            # Compute screen's fallback lists machines without map nodes.
            runner = (
                key if record is None else view.get("fields", {}).get("runner_id", {}).get("value")
            )
            result = {
                "as_of": model.stamp(now),
                "node": view,
                "sites": sites_view(graph, key),
                "agents": node_agents.read(
                    gateway, principal, runner, now=now, by_key=record is None
                ),
            }
        except Exception as exc:
            log_failure(LOG, "topology node read failed", exc)
            send_json(h, 500, {"error": "topology node read failed"})
            return
        send_json(h, 200, result)

    gateway.add_route("GET", PATH, handle)
