"""Owner read for the Compute map canvas: the graph without provenance, plus a status per end.

Interim and deliberately small: one answer holding ``store.read_graph`` without evidence and
``status.read_status`` reduced to one word per node and per link end. The agent tools add
the general graph read; the canvas then reads that and this module is removed. See "Map
read" in ``docs/topology.md``.
"""

from commonplace.http_util import require_principal, send_json

from . import model, status, store

PATH = "/api/topology/map"
UNKNOWN = "unknown"
_LABEL = ("kind", "name", "role")
_RECORD = ("subject", "wing", "key", "id", "from", "to", "method", "site", "values", "stale")


def _plain(record: dict) -> dict:
    """A store record without per-field provenance: what the canvas draws."""
    plain = {name: record[name] for name in _RECORD if name in record}
    plain["conflicted"] = bool(record.get("conflicted"))
    if "hops" in record:
        plain["hops"] = record["hops"]
    if "key" in record:
        # A faded icon needs the last known kind and label of a node whose facts expired.
        fields = record.get("fields", {})
        plain["last"] = {
            name: fields[name]["value"]
            for name in _LABEL
            if name in fields and fields[name]["stale"]
        }
    return plain


def _state(record) -> str:
    seen = record.get("status") if isinstance(record, dict) else None
    return seen if seen in model.LINK_STATUSES else UNKNOWN


def map_view(graph: dict, live: dict) -> dict:
    """``{"graph", "status"}`` for one ``read_graph`` result and its ``read_status``."""
    links = live.get("links", {})
    return {
        "graph": {
            "wing": graph["wing"],
            "nodes": [_plain(node) for node in graph["nodes"]],
            "links": [_plain(link) for link in graph["links"]],
            "routes": [_plain(route) for route in graph["routes"]],
        },
        "status": {
            "nodes": {
                node["key"]: _state(live.get("nodes", {}).get(node["key"]))
                for node in graph["nodes"]
            },
            "links": {
                link["id"]: {
                    end: _state((links.get(link["id"]) or {}).get("ends", {}).get(end))
                    for end in ("from", "to")
                }
                for link in graph["links"]
            },
        },
    }


def install(gateway):
    def handle(h):
        principal = require_principal(gateway, h)
        if principal is None:
            return
        if (
            not principal.person
            or principal.role != "owner"
            or not principal.policy.can_read_wing(model.TOPOLOGY_WING)
        ):
            send_json(h, 403, {"error": "Only the workspace owner can open the Compute map."})
            return
        graph = store.read_graph(store.open_topology(gateway, principal.workspace_id))
        # The same graph goes to the status read, so the map and its status describe one moment.
        live = status.read_status(gateway, principal.workspace_id, graph=graph)
        send_json(h, 200, map_view(graph, live))

    gateway.add_route("GET", PATH, handle)
