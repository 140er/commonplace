"""GET /api/topology/map over real HTTP: the canvas shape, owner-only access and isolation."""

from datetime import datetime, timedelta, timezone

from commonplace.topology import Observer, map_route, store
from tests._bots_helpers import bots_hub_fixture  # noqa: F401

RUNNER = Observer("runner", "mach_0123456789abcdef")
WRITE = {"observer": RUNNER, "evidence": "seen in a fixture"}


def _seed(hub, workspace=None, **write):
    topo = store.open_topology(hub.gateway, workspace or hub.workspace)
    store.upsert(
        topo,
        [
            {"node": "laptop", "fields": {"kind": "mac", "name": "Laptop", "role": "desk"}},
            {"node": "web1", "fields": {"kind": "server", "name": "Web 1"}},
            {"node": "example.test", "fields": {"kind": "website"}},
            {"link": ["laptop", "web1", "tailscale"], "fields": {"status": "up"}},
            {"link": ["example.test", "web1", "reverse_proxy"]},
            {"route": "example.test", "hops": ["example.test>web1:reverse_proxy"]},
        ],
        **{**WRITE, **write},
    )
    return topo


def test_owner_reads_graph_and_status_without_provenance(bots_hub):
    _seed(bots_hub)
    status, body, raw = bots_hub.get(bots_hub.owner, map_route.PATH)
    assert status == 200, body
    assert set(body) == {"graph", "status"}
    graph = body["graph"]
    assert graph["wing"] == "topology"
    nodes = {node["key"]: node for node in graph["nodes"]}
    assert nodes["laptop"] == {
        "subject": "node:laptop",
        "wing": "topology",
        "key": "laptop",
        "values": {"kind": "mac", "name": "Laptop", "role": "desk"},
        "stale": False,
        "conflicted": False,
        "last": {},
    }
    links = {link["id"]: link for link in graph["links"]}
    assert links["laptop>web1:tailscale"]["from"] == "laptop"
    assert links["laptop>web1:tailscale"]["to"] == "web1"
    assert links["laptop>web1:tailscale"]["method"] == "tailscale"
    assert graph["routes"][0]["site"] == "example.test"
    assert graph["routes"][0]["hops"][0]["link"] == "example.test>web1:reverse_proxy"
    # One status per node and per link end; a link without a status fact is unknown, never up.
    assert body["status"]["nodes"] == {
        "laptop": "unknown",
        "web1": "unknown",
        "example.test": "unknown",
    }
    assert body["status"]["links"] == {
        "laptop>web1:tailscale": {"from": "up", "to": "up"},
        "example.test>web1:reverse_proxy": {"from": "unknown", "to": "unknown"},
    }
    # Evidence and observers stay behind: the canvas draws, the detail read explains.
    assert b"seen in a fixture" not in raw and b"mach_" not in raw and b'"fields"' not in raw


def test_live_status_reaches_the_canvas_as_one_word_per_node_and_link_end():
    graph = {
        "wing": "topology",
        "nodes": [
            {"key": key, "subject": f"node:{key}", "wing": "topology", "values": {}, "fields": {}}
            for key in ("laptop", "web1", "nas")
        ],
        "links": [
            {"id": link, "subject": f"link:{link}", "wing": "topology", "values": {}, "fields": {}}
            for link in ("laptop>web1:tailscale", "web1>nas:direct")
        ],
        "routes": [],
    }
    live = {
        "nodes": {"laptop": {"status": "up", "color": "green"}, "web1": {"status": "degraded"}},
        "links": {
            "laptop>web1:tailscale": {
                "status": "down",
                "ends": {"from": {"status": "up"}, "to": {"status": "down"}},
            },
            "web1>nas:direct": {"status": "green", "ends": {"from": {"status": "green"}}},
        },
        "machines": {"mach_0123456789abcdef": {"status": "up"}},
    }
    assert map_route.map_view(graph, live)["status"] == {
        # A node the status read does not know, and anything that is not a status, is unknown.
        "nodes": {"laptop": "up", "web1": "degraded", "nas": "unknown"},
        "links": {
            "laptop>web1:tailscale": {"from": "up", "to": "down"},
            "web1>nas:direct": {"from": "unknown", "to": "unknown"},
        },
    }


def test_expired_status_is_unknown_and_a_stale_node_keeps_its_last_label(bots_hub):
    long_ago = datetime.now(timezone.utc) - timedelta(days=40)
    _seed(bots_hub, now=long_ago)
    view = bots_hub.get(bots_hub.owner, map_route.PATH)[1]
    node = next(node for node in view["graph"]["nodes"] if node["key"] == "laptop")
    assert node["stale"] is True and node["values"] == {}
    assert node["last"] == {"kind": "mac", "name": "Laptop", "role": "desk"}
    assert view["status"]["links"]["laptop>web1:tailscale"] == {"from": "unknown", "to": "unknown"}


def test_empty_workspace_reads_as_an_empty_map_and_creates_no_file(bots_hub):
    status, body, _ = bots_hub.get(bots_hub.owner, map_route.PATH)
    assert status == 200
    assert body == {
        "graph": {"wing": "topology", "nodes": [], "links": [], "routes": []},
        "status": {"nodes": {}, "links": {}},
    }
    assert not store.open_topology(bots_hub.gateway, bots_hub.workspace).path.exists()


def test_only_the_signed_in_owner_of_the_workspace_reads_it(bots_hub):
    _seed(bots_hub)
    assert bots_hub.get(None, map_route.PATH)[0] == 401
    for auth in (bots_hub.member, bots_hub.t1):
        status, body, raw = bots_hub.get(auth, map_route.PATH)
        assert status == 403, body
        assert body == {"error": "Only the workspace owner can open the Compute map."}
        assert b"web1" not in raw
    # Another workspace's owner reads their own, empty, map.
    status, body, raw = bots_hub.get(bots_hub.other, map_route.PATH)
    assert status == 200 and body["graph"]["nodes"] == [] and b"web1" not in raw
