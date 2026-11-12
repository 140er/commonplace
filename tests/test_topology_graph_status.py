"""The whole-graph read (GET /api/topology) carries live status as one word per node and link end."""

from datetime import datetime, timedelta, timezone

from commonplace.topology import Observer, answers, store
from tests._bots_helpers import bots_hub_fixture  # noqa: F401

PATH = "/api/topology"
RUNNER = Observer("runner", "mach_0123456789abcdef")
WRITE = {"observer": RUNNER, "evidence": "seen in a fixture"}


def _seed(hub, **write):
    topo = store.open_topology(hub.gateway, hub.workspace)
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


def test_owner_reads_graph_and_one_status_word_per_node_and_link_end(bots_hub):
    _seed(bots_hub)
    status, body, _ = bots_hub.get(bots_hub.owner, PATH)
    assert status == 200, body
    assert body["scope"] == "graph"
    nodes = {node["key"]: node for node in body["nodes"]}
    assert nodes["laptop"]["values"] == {"kind": "mac", "name": "Laptop", "role": "desk"}
    links = {link["id"]: link for link in body["links"]}
    assert links["laptop>web1:tailscale"]["from"] == "laptop"
    assert body["routes"][0]["hops"][0]["link"] == "example.test>web1:reverse_proxy"
    # A node no runner reports on is unknown, never up; a link end takes the path report.
    assert body["status"]["nodes"] == {
        "laptop": "unknown",
        "web1": "unknown",
        "example.test": "unknown",
    }
    assert body["status"]["links"] == {
        "laptop>web1:tailscale": {"from": "up", "to": "up"},
        "example.test>web1:reverse_proxy": {"from": "unknown", "to": "unknown"},
    }


def test_a_node_or_site_read_has_no_status_block(bots_hub):
    _seed(bots_hub)
    assert "status" not in bots_hub.get(bots_hub.owner, PATH + "?node=web1")[1]
    assert "status" not in bots_hub.get(bots_hub.owner, PATH + "?site=example.test")[1]


def test_live_status_is_reduced_to_one_word_per_node_and_link_end():
    graph = {
        "nodes": [{"key": key} for key in ("laptop", "web1", "nas")],
        "links": [{"id": link} for link in ("laptop>web1:tailscale", "web1>nas:direct")],
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
    assert answers.status_words(graph, live) == {
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
    body = bots_hub.get(bots_hub.owner, PATH)[1]
    node = next(node for node in body["nodes"] if node["key"] == "laptop")
    assert node["stale"] is True and node["values"] == {}
    # The canvas draws a faded icon from the stale fields' last values.
    assert {name: node["fields"][name]["value"] for name in ("kind", "name", "role")} == {
        "kind": "mac",
        "name": "Laptop",
        "role": "desk",
    }
    assert all(node["fields"][name]["stale"] for name in ("kind", "name", "role"))
    assert body["status"]["links"]["laptop>web1:tailscale"] == {"from": "unknown", "to": "unknown"}


def test_empty_workspace_reads_as_an_empty_map_and_creates_no_file(bots_hub):
    status, body, _ = bots_hub.get(bots_hub.owner, PATH)
    assert status == 200
    assert (body["nodes"], body["links"], body["routes"]) == ([], [], [])
    assert body["status"] == {"nodes": {}, "links": {}}
    assert not store.open_topology(bots_hub.gateway, bots_hub.workspace).path.exists()


def test_another_workspaces_map_and_status_stay_out_of_the_read(bots_hub):
    _seed(bots_hub)
    status, body, raw = bots_hub.get(bots_hub.other, PATH)
    assert status == 200 and body["nodes"] == [] and body["status"] == {"nodes": {}, "links": {}}
    assert b"web1" not in raw
    assert bots_hub.get(None, PATH)[0] == 401
