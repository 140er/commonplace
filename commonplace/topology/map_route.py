"""Owner read for the Compute map canvas: the graph without provenance, plus a status per end.

Interim and deliberately small. The agent tools add the general graph read and the live
status read adds heartbeat-derived status; the canvas then reads those two and this module
is removed. Until then a node's status is ``unknown`` and both ends of a link carry the
link's own observed ``status`` fact. See "Map read" in ``docs/topology.md``.
"""

from commonplace.http_util import require_principal, send_json

from . import model, store

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
        plain["last"] = {
            name: record["fields"][name]["value"]
            for name in _LABEL
            if name in record["fields"] and record["fields"][name]["stale"]
        }
    return plain


def map_view(graph: dict) -> dict:
    """``{"graph", "status"}`` for one ``read_graph`` result."""
    status = {"nodes": {}, "links": {}}
    for node in graph["nodes"]:
        status["nodes"][node["key"]] = UNKNOWN
    for link in graph["links"]:
        seen = link["values"].get("status", UNKNOWN)
        status["links"][link["id"]] = {"from": seen, "to": seen}
    return {
        "graph": {
            "wing": graph["wing"],
            "nodes": [_plain(node) for node in graph["nodes"]],
            "links": [_plain(link) for link in graph["links"]],
            "routes": [_plain(route) for route in graph["routes"]],
        },
        "status": status,
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
        topo = store.open_topology(gateway, principal.workspace_id)
        send_json(h, 200, map_view(store.read_graph(topo)))

    gateway.add_route("GET", PATH, handle)
