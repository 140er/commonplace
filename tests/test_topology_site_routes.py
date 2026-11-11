"""Site route discovery end to end: DNS answer plus proxy config become a route, and the
route reaches the map only as a proposal the owner approves. All names and addresses are
invented (example domains, documentation address ranges)."""

import json

import pytest

from commonplace.extensions import ToolError
from commonplace.topology import TopologyError, proposals, site_routes, store
from commonplace.topology.site_dns import DnsAnswer
from tests import (
    test_topology_proposals as proposal_tests,
    test_topology_site_adapters as configs,
)

env = proposal_tests.env
world = proposal_tests.world
ctx = proposal_tests.ctx
topo = proposal_tests.topo
board = proposal_tests.board
answer = proposal_tests.answer
seed = proposal_tests.seed
call = proposal_tests.call
text = proposal_tests.text
fake_resolver = configs.fake_resolver

WEB1 = {"node": "web1", "fields": {"kind": "server", "hostname": "203.0.113.10"}}
RECORDS = {
    "example.com": DnsAnswer(addresses=("203.0.113.10",)),
    "shop.example.com": DnsAnswer(addresses=("203.0.113.10",)),
    "static.example.com": DnsAnswer(addresses=("203.0.113.10",)),
    "api.example.com": DnsAnswer(addresses=("203.0.113.10",)),
    "example.net": DnsAnswer(addresses=("203.0.113.10",)),
    "example.org": DnsAnswer(addresses=("203.0.113.10", "2001:db8::10")),
    "elsewhere.example.com": DnsAnswer(addresses=("198.51.100.7",)),
    "v6.example.com": DnsAnswer(addresses=("2001:db8::7",)),
    "cdn.example.com": DnsAnswer(addresses=("198.51.100.50",), cnames=("edge.example.net",)),
}
CADDY = {"node": "web1", "kind": "caddy", "config": configs.CADDYFILE}
BLOG_ROUTE = ["example.com>web1:dns", "web1>web1-blog:reverse_proxy"]


def caddy(text, node="web1"):
    return [{"node": node, "kind": "caddy", "config": text}]


def graph(tmp_path, *items):
    """The map as a seeded store reads it; an empty map when nothing is seeded."""
    where = store.TopologyStore(tmp_path / "map")
    if items:
        store.upsert(where, list(items), observer=store.Observer("owner", "u1"), evidence="seed")
    return store.read_graph(where)


def derive(tmp_path, site, proxies=None, seeded=(WEB1,), **more):
    return site_routes.derive(
        site, proxies, graph=graph(tmp_path, *seeded), resolver=fake_resolver(RECORDS), **more
    )


def links(result):
    return [hop["link"] for hop in result["hops"]]


# ── The route is derived from DNS and each supported proxy ────────────────
def test_caddy_route_from_dns_to_the_container(tmp_path):
    result = derive(tmp_path, "Example.com", [CADDY])
    assert result["site"] == "example.com" and result["resolved"] and result["ends"] == "app"
    assert result["hops"] == [
        {
            "link": "example.com>web1:dns",
            "evidence": ["example.com A 203.0.113.10", "203.0.113.10 is recorded for web1"],
        },
        {
            "link": "web1>web1-blog:reverse_proxy",
            "evidence": [
                "Caddy config on web1, line 13: example.com, www.example.com {",
                "line 16: reverse_proxy blog:3000",
                "blog is a bare name, so it is taken as a container on web1",
            ],
        },
    ]
    assert result["items"] == [
        {"node": "example.com", "fields": {"kind": "website"}},
        {"node": "web1-blog", "fields": {"kind": "container", "name": "blog"}},
        {"link": ["example.com", "web1", "dns"]},
        {"link": ["web1", "web1-blog", "reverse_proxy"], "fields": {"port": 3000}},
        {"route": "example.com", "hops": BLOG_ROUTE},
    ]
    assert result["evidence"].splitlines() == [
        "Site route for example.com, discovered read-only: "
        "a public DNS lookup, then the proxy config that was handed in.",
        "example.com>web1:dns",
        "  example.com A 203.0.113.10",
        "  203.0.113.10 is recorded for web1",
        "web1>web1-blog:reverse_proxy",
        "  Caddy config on web1, line 13: example.com, www.example.com {",
        "  line 16: reverse_proxy blog:3000",
        "  blog is a bare name, so it is taken as a container on web1",
    ]
    assert result["notes"] == [
        "this entry has 1 more upstream target (other paths or load balancing) outside the route"
    ]
    assert result["withheld"] == 0


def test_caddy_json_route_to_another_machine_and_its_app(tmp_path):
    seeded = (
        WEB1,
        {"node": "app1", "fields": {"kind": "vm", "hostname": "198.51.100.20"}},
        {"node": "portal", "fields": {"kind": "container"}},
        {"link": ["app1", "portal", "hosts"], "fields": {"port": 8443}},
    )
    proxies = [{"node": "web1", "kind": "caddy", "config": configs.CADDY_JSON}]
    result = derive(tmp_path, "example.net", proxies, seeded)
    assert links(result) == [
        "example.net>web1:dns",
        "web1>app1:reverse_proxy",
        "app1>portal:hosts",
    ]
    assert result["ends"] == "app"
    assert result["hops"][2]["evidence"] == ["portal listens on port 8443 of app1"]
    # Nodes and links already on the map are not written again as new nodes.
    assert [item for item in result["items"] if "node" in item] == [
        {"node": "example.net", "fields": {"kind": "website"}}
    ]
    # Without a known app on that port the route ends at the machine.
    result = derive(tmp_path / "bare", "example.net", proxies, seeded[:2])
    assert links(result) == ["example.net>web1:dns", "web1>app1:reverse_proxy"]
    assert result["ends"] == "host"


@pytest.mark.parametrize("config", [json.dumps(configs.NPM_ROWS), configs.NPM_NGINX])
def test_nginx_proxy_manager_route_to_a_container_the_agent_saw(tmp_path, config):
    proxies = [{"node": "web1", "kind": "nginx_proxy_manager", "config": config}]
    apps = [{"node": "wiki", "host": "web1", "port": 8080, "names": ["Wiki", "wiki.docker"]}]
    result = derive(tmp_path, "example.org", proxies, apps=apps)
    assert links(result) == ["example.org>web1:dns", "web1>wiki:reverse_proxy"]
    assert result["ends"] == "app"
    assert result["items"] == [
        {"node": "example.org", "fields": {"kind": "website"}},
        {"node": "wiki", "fields": {"kind": "container"}},
        {"link": ["example.org", "web1", "dns"]},
        {"link": ["web1", "wiki", "reverse_proxy"], "fields": {"port": 8080}},
        {"route": "example.org", "hops": links(result)},
    ]
    assert result["hops"][0]["evidence"] == [
        "example.org AAAA 2001:db8::10",
        "example.org A 203.0.113.10",
        "203.0.113.10 is recorded for web1",
    ]
    assert "wiki" in result["hops"][1]["evidence"][1]
    # Without that sighting the bare upstream name is a container on the proxy's machine.
    result = derive(tmp_path / "bare", "example.org", proxies)
    assert links(result)[1] == "web1>web1-wiki:reverse_proxy"


def test_app_on_another_machine_is_reached_through_its_host(tmp_path):
    seeded = (WEB1, {"node": "nas", "fields": {"kind": "server", "aliases": ["storage"]}})
    apps = [{"node": "files", "host": "nas", "port": 9000, "names": ["files.docker"]}]
    config = "example.com {\n\treverse_proxy files.docker:9000\n}\n"
    result = derive(tmp_path, "example.com", caddy(config), seeded, apps=apps)
    assert links(result) == [
        "example.com>web1:dns",
        "web1>nas:reverse_proxy",
        "nas>files:hosts",
    ]
    assert result["hops"][2]["evidence"] == ["files runs on nas"]
    # An alias the owner recorded for a machine names it as an upstream too.
    config = "example.com {\n\treverse_proxy storage:9000\n}\n"
    result = derive(tmp_path / "alias", "example.com", caddy(config), seeded)
    assert links(result) == ["example.com>web1:dns", "web1>nas:reverse_proxy"]
    assert result["ends"] == "host"


def test_a_local_upstream_is_an_app_on_the_proxy_machine(tmp_path):
    result = derive(tmp_path, "shop.example.com", [CADDY])
    assert links(result) == ["shop.example.com>web1:dns", "web1>web1-port-8081:reverse_proxy"]
    assert {
        "node": "web1-port-8081",
        "fields": {"kind": "container", "name": "App on port 8081"},
    } in (result["items"])
    # A port the map already knows an app on is that app.
    seeded = (
        WEB1,
        {"node": "shop", "fields": {"kind": "container"}},
        {"link": ["web1", "shop", "hosts"], "fields": {"port": 8081}},
    )
    result = derive(tmp_path / "known", "shop.example.com", [CADDY], seeded)
    assert links(result)[1] == "web1>shop:reverse_proxy"
    for upstream in ("127.0.0.1:8081", "[::1]:8081", "host.docker.internal:8081", "web1:8081"):
        config = f"shop.example.com {{\n\treverse_proxy {upstream}\n}}\n"
        result = derive(tmp_path / upstream.replace(":", "_"), "shop.example.com", caddy(config))
        assert links(result)[1] == "web1>web1-port-8081:reverse_proxy", upstream


def test_a_site_the_machine_serves_itself_ends_at_the_machine(tmp_path):
    result = derive(tmp_path, "static.example.com", [CADDY])
    assert links(result) == ["static.example.com>web1:dns"] and result["ends"] == "host"
    assert result["notes"] == ["web1 serves static.example.com itself: its entry has no upstream"]
    result = derive(tmp_path / "unnamed", "api.example.com", [CADDY])
    assert links(result) == ["api.example.com>web1:dns"] and result["ends"] == "host"
    assert result["notes"] == ["the proxy config on web1 does not name api.example.com"]
    result = derive(tmp_path / "none", "api.example.com")
    assert result["notes"] == ["no proxy config of web1 was handed in; the route stops there"]


# ── Outside the user's machines: an explicit external / unknown hop ───────
def test_a_domain_that_resolves_elsewhere_ends_in_an_external_hop(tmp_path):
    config = "elsewhere.example.com {\n\treverse_proxy blog:3000\n}\n"
    result = derive(tmp_path, "elsewhere.example.com", caddy(config))
    assert links(result) == ["elsewhere.example.com>ext-198.51.100.7:dns"]
    assert result["ends"] == "external"
    assert result["items"] == [
        {"node": "elsewhere.example.com", "fields": {"kind": "website"}},
        {
            "node": "ext-198.51.100.7",
            "fields": {
                "kind": "cloud",
                "name": "External / unknown",
                "hostname": "198.51.100.7",
            },
        },
        {"link": ["elsewhere.example.com", "ext-198.51.100.7", "dns"]},
        {"route": "elsewhere.example.com", "hops": links(result)},
    ]
    assert result["hops"][0]["evidence"] == [
        "elsewhere.example.com A 198.51.100.7",
        "198.51.100.7 is not recorded for any node on the map",
    ]
    # The proxy config that names the site is reported, not stitched onto the route.
    assert result["notes"] == [
        "the Caddy config on web1 names elsewhere.example.com, but DNS does not point at web1"
    ]


def test_external_hops_for_an_alias_and_an_ipv6_address(tmp_path):
    result = derive(tmp_path, "cdn.example.com")
    assert links(result) == ["cdn.example.com>ext-edge.example.net:dns"]
    assert result["items"][1]["fields"]["hostname"] == "edge.example.net"
    result = derive(tmp_path / "v6", "v6.example.com")
    assert links(result) == ["v6.example.com>ext-2001-db8--7:dns"]
    assert result["items"][1] == {
        "node": "ext-2001-db8--7",
        "fields": {"kind": "cloud", "name": "External / unknown"},
    }


def test_an_upstream_nobody_knows_ends_in_an_external_hop(tmp_path):
    config = "example.com {\n\treverse_proxy https://app.example.net:8443\n}\n"
    result = derive(tmp_path, "example.com", caddy(config))
    assert links(result) == [
        "example.com>web1:dns",
        "web1>ext-app.example.net:reverse_proxy",
    ]
    assert result["ends"] == "external"
    assert result["hops"][1]["evidence"][-1] == (
        "app.example.net is not recorded for any node on the map"
    )
    for upstream, key in (("192.0.2.99:80", "ext-192.0.2.99"), ("{$BACKEND}", "ext-unknown")):
        config = f"example.com {{\n\treverse_proxy {upstream}\n}}\n"
        result = derive(tmp_path / key, "example.com", caddy(config))
        assert links(result)[1] == f"web1>{key}:reverse_proxy" and result["ends"] == "external"


def test_a_proxy_or_cdn_in_front_is_a_hop_when_the_agent_names_it(tmp_path):
    machines = [{"node": "cdn", "kind": "cloud", "addresses": ["Edge.Example.net."]}]
    config = "cdn.example.com {\n\treverse_proxy blog:3000\n}\n"
    result = derive(tmp_path, "cdn.example.com", caddy(config), machines=machines)
    assert links(result) == [
        "cdn.example.com>cdn:dns",
        "cdn>web1:proxy",
        "web1>web1-blog:reverse_proxy",
    ]
    assert result["ends"] == "app"
    assert {"node": "cdn", "fields": {"kind": "cloud"}} in result["items"]
    assert result["hops"][1]["evidence"] == [
        "Caddy config on web1 serves cdn.example.com: line 1: cdn.example.com {"
    ]
    result = derive(tmp_path / "alone", "cdn.example.com", machines=machines)
    assert links(result) == ["cdn.example.com>cdn:dns"] and result["ends"] == "edge"
    # The agent's own sighting of a machine's address matches the DNS answer as well.
    machines = [{"node": "web1", "addresses": ["198.51.100.50"]}]
    result = derive(tmp_path / "seen", "cdn.example.com", caddy(config), machines=machines)
    assert links(result) == ["cdn.example.com>web1:dns", "web1>web1-blog:reverse_proxy"]


def test_a_name_without_records_proposes_nothing(tmp_path):
    result = derive(tmp_path, "missing.example.com", [CADDY])
    assert not result["resolved"] and result["items"] is None and result["hops"] == []
    assert result["notes"] == ["missing.example.com has no DNS records; nothing is proposed"]


def test_a_route_never_loops(tmp_path):
    seeded = (WEB1, {"node": "cdn", "fields": {"kind": "cloud", "hostname": "edge.example.net"}})
    config = "cdn.example.com {\n\treverse_proxy edge.example.net:443\n}\n"
    result = derive(tmp_path, "cdn.example.com", caddy(config), seeded)
    assert links(result) == ["cdn.example.com>cdn:dns", "cdn>web1:proxy"]
    assert result["notes"] == ["web1 points back at cdn; the route stops at web1"]


def test_invalid_requests_are_refused_without_a_lookup_or_the_config(tmp_path):
    resolver = fake_resolver(RECORDS)
    secret_config = configs.CADDYFILE

    def refused(match, site="example.com", **more):
        with pytest.raises(TopologyError, match=match) as failure:
            site_routes.derive(site, graph=graph(tmp_path), resolver=resolver, **more)
        assert not [secret for secret in configs.SECRETS if secret in str(failure.value)]

    refused("public domain name", site="web1")
    refused("not on the map yet", proxies=caddy(secret_config, node="web9"))
    refused("proxy kind must be one of", proxies=[{**CADDY, "kind": "apache"}])
    refused("each entry of proxies holds node, kind, config", proxies=[{"node": "web1"}])
    refused("each entry of proxies", proxies=[{**CADDY, "path": "/etc/caddy/Caddyfile"}])
    refused("at most 8 entries", proxies=[CADDY] * 9)
    refused("proxies must be a list", proxies=secret_config)
    refused("give its kind under machines", machines=[{"node": "cdn", "addresses": []}])
    refused("machines kind must be one of", machines=[{"node": "c", "addresses": [], "kind": "x"}])
    refused("hostnames or addresses", machines=[{"node": "c", "kind": "vm", "addresses": ["a b"]}])
    refused("not on the map yet", apps=[{"node": "blog", "host": "web1"}])
    refused("two different nodes", apps=[{"node": "blog", "host": "blog"}])
    refused("port number", apps=[{"node": "blog", "host": "web1", "port": 0}])
    assert resolver.calls == []


# ── Through the hub: discovery proposes, the owner decides ────────────────
@pytest.fixture
def site_world(world):
    seed(world, [WEB1])
    world.gateway.topology_resolver = fake_resolver(RECORDS)
    return world


def discover(env, site="example.com", proxies=(CADDY,), principal=None, **more):
    return site_routes.discover(ctx(env, principal), site=site, proxies=list(proxies), **more)


def test_discovery_is_one_proposal_and_approve_puts_the_route_on_the_map(site_world):
    result = discover(site_world)
    row = result["proposal"]
    assert result["applied"] is None and row["state"] == "pending" and result["unchanged"] == 0
    assert result["ends"] == "app" and links(result) == BLOG_ROUTE
    assert "items" not in result and "evidence" not in result
    assert row["observer"] == {"kind": "agent", "id": site_world.principal.connection_id}
    assert store.get_route(topo(site_world), "example.com") is None
    assert store.get_node(topo(site_world), "web1-blog") is None

    card = board(site_world).task_show(row["card"])
    assert card["ask_options"] == ["Approve", "Reject"]
    assert card["ask_blocks"][0]["preview"].splitlines() == [
        "After:",
        "node example.com",
        "  kind: website",
        "node web1-blog",
        "  kind: container",
        "  name: blog",
        "link example.com>web1:dns",
        "  method: dns",
        "link web1>web1-blog:reverse_proxy",
        "  method: reverse_proxy",
        "  port: 3000",
        "route example.com",
        "  hops: example.com>web1:dns, web1>web1-blog:reverse_proxy",
    ]
    assert "line 16: reverse_proxy blog:3000" in card["body"]

    answer(site_world, row, "Approve")
    route = store.get_route(topo(site_world), "example.com")
    assert [(hop["link"], hop["known"]) for hop in route["hops"]] == [
        ("example.com>web1:dns", True),
        ("web1>web1-blog:reverse_proxy", True),
    ]
    link = store.get_link(topo(site_world), "web1", "web1-blog", "reverse_proxy")
    assert link["values"] == {"method": "reverse_proxy", "port": 3000}
    provenance = link["fields"]["port"]["provenance"][0]
    assert provenance["observer"] == {"kind": "agent", "id": site_world.principal.connection_id}
    assert "line 16: reverse_proxy blog:3000" in provenance["evidence"]
    assert store.get_node(topo(site_world), "example.com")["values"] == {"kind": "website"}


def test_a_changed_upstream_is_proposed_as_a_change_not_rewritten(site_world):
    answer(site_world, discover(site_world)["proposal"], "Approve")
    moved = caddy(configs.CADDYFILE.replace("reverse_proxy blog:3000", "reverse_proxy ghost:2368"))
    result = discover(site_world, proxies=moved)
    row = result["proposal"]
    assert row["state"] == "pending" and result["applied"] is None
    changes = {(c["subject"], c["predicate"]): (c["before"], c["after"]) for c in row["changes"]}
    new_route = ["example.com>web1:dns", "web1>web1-ghost:reverse_proxy"]
    assert changes[("route:example.com", "hops")] == (BLOG_ROUTE, new_route)
    assert changes[("link:web1>web1-ghost:reverse_proxy", "port")] == (None, 2368)
    card = board(site_world).task_show(row["card"])
    assert "  hops: " + ", ".join(BLOG_ROUTE) in card["ask_blocks"][1]["preview"]
    assert "  hops: " + ", ".join(new_route) in card["ask_blocks"][0]["preview"]
    # Until the owner answers, and after a rejection, the map keeps the old route.
    assert store.get_route(topo(site_world), "example.com")["values"]["hops"] == BLOG_ROUTE
    answer(site_world, row, "Reject")
    assert store.get_route(topo(site_world), "example.com")["values"]["hops"] == BLOG_ROUTE
    assert store.get_node(topo(site_world), "web1-ghost") is None

    # The same container on another port is a change of that one link.
    ported = caddy(configs.CADDYFILE.replace("blog:3000", "blog:3001"))
    row = discover(site_world, proxies=ported)["proposal"]
    assert [(c["subject"], c["predicate"], c["before"], c["after"]) for c in row["changes"]] == [
        ("link:web1>web1-blog:reverse_proxy", "port", 3000, 3001)
    ]
    answer(site_world, row, "Approve")
    link = store.get_link(topo(site_world), "web1", "web1-blog", "reverse_proxy")
    assert link["values"]["port"] == 3001
    assert [
        h["value"] for h in store.history(topo(site_world), link["subject"], predicate="port")
    ] == [
        3001,
        3000,
    ]


def test_a_rerun_of_an_unchanged_route_asks_nothing(site_world):
    first = discover(site_world)["proposal"]
    assert discover(site_world)["proposal"]["id"] == first["id"]
    answer(site_world, first, "Approve")
    result = discover(site_world)
    assert result["proposal"] is None and result["applied"] is None and result["unchanged"] == 4
    assert len(proposals.listing(ctx(site_world))) == 2  # the seed and the route


def test_an_external_route_is_proposed_like_any_other(site_world):
    row = discover(site_world, site="elsewhere.example.com", proxies=())["proposal"]
    answer(site_world, row, "Approve")
    route = store.get_route(topo(site_world), "elsewhere.example.com")
    assert [hop["to"] for hop in route["hops"]] == ["ext-198.51.100.7"]
    node = store.get_node(topo(site_world), "ext-198.51.100.7")
    assert node["values"]["name"] == "External / unknown"


def test_no_secret_from_a_proxy_config_is_stored_anywhere(site_world, tmp_path):
    npm = "nginx_proxy_manager"
    runs = [
        ("example.com", "caddy", configs.CADDYFILE),
        ("example.net", "caddy", configs.CADDY_JSON),
        ("example.org", npm, json.dumps(configs.NPM_ROWS)),
        ("example.org", npm, configs.NPM_NGINX.replace('"wiki"', '"wiki2"')),
        (
            "shop.example.com",
            "caddy",
            f"shop.example.com {{\n\treverse_proxy https://u:{configs.PASSWORD}@pay.example.net\n"
            f"\treverse_proxy /hook* https://hooks.example.net/{configs.TOKEN}\n}}\n",
        ),
    ]
    shown = []
    for site, kind, config in runs:
        assert any(secret in config for secret in configs.SECRETS)
        result = discover(
            site_world, site=site, proxies=[{"node": "web1", "kind": kind, "config": config}]
        )
        answer(site_world, result["proposal"], "Approve")
        shown.append(json.dumps(result))
        shown.append(
            json.dumps(board(site_world).task_show(result["proposal"]["card"]), default=str)
        )
        response = call(
            site_world,
            "topology_discover_site",
            site=site,
            proxies=[{"node": "web1", "kind": kind, "config": config}],
        )
        shown.append(text(response))
    assert result["withheld"] == 1 and "1 config lines were left out" in shown[-1]
    shown.append(json.dumps(store.read_graph(topo(site_world))))
    shown.append(json.dumps(proposals.listing(ctx(site_world))))
    assert len(store.read_graph(topo(site_world))["routes"]) == 4
    files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert topo(site_world).path in files
    stored = b"\n".join(path.read_bytes() for path in files) + "\n".join(shown).encode()
    assert b"reverse_proxy blog:3000" in stored  # the evidence itself is there
    for secret in configs.SECRETS:
        assert secret.encode() not in stored, secret[:6]
    assert b"alice" not in stored and b"Authorization" not in stored


# ── Access, the tool and the owner's own run ──────────────────────────────
def test_the_tool_over_mcp_names_the_route_and_the_decision(site_world):
    response = call(site_world, "topology_discover_site", site="example.com", proxies=[CADDY])
    assert not response["result"].get("isError"), response
    first, second, note = text(response).splitlines()
    assert first == (
        "route example.com: example.com>web1:dns, web1>web1-blog:reverse_proxy "
        "(ends at the container or app)"
    )
    assert second.startswith("proposal tp_") and "with 7 facts waits for the owner: #" in second
    assert note.startswith("note: this entry has 1 more upstream target")
    response = call(site_world, "topology_discover_site", site="elsewhere.example.com")
    assert "(ends at an external / unknown hop)" in text(response)
    response = call(site_world, "topology_discover_site", site="missing.example.com")
    assert text(response) == "note: missing.example.com has no DNS records; nothing is proposed"
    for bad in ({"site": "web1"}, {"site": "example.com", "nope": 1}, {"proxies": [CADDY]}):
        assert call(site_world, "topology_discover_site", **bad)["result"]["isError"], bad
    response = call(
        site_world, "topology_discover_site", site="example.com", proxies=[{**CADDY, "kind": "x"}]
    )
    assert text(response) == "no: proxy kind must be one of caddy, nginx_proxy_manager"


def test_the_wing_grant_gates_the_tool_before_any_lookup(site_world):
    resolver = site_world.gateway.topology_resolver
    _, outsider = site_world.agent("outsider", read_wings=["shared"], write_wings=["shared"])
    listed = site_world.gateway.mediator.handle(
        outsider, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert "topology_discover_site" not in {t["name"] for t in listed["result"]["tools"]}
    with pytest.raises(ToolError, match="cannot write the topology wing"):
        discover(site_world, principal=outsider)
    assert resolver.calls == []
    _, slim = site_world.agent("slim", profile="slim")
    assert not slim.policy.tool_visible("topology_discover_site")
    _, worker = proposal_tests.runner(site_world)
    assert worker.policy.tool_visible("topology_discover_site")
    # A route is never a runner's fact about itself: its discovery is a proposal too.
    result = discover(site_world, principal=worker, board="work")
    assert result["applied"] is None and result["proposal"]["state"] == "pending"
    assert result["proposal"]["observer"]["kind"] == "runner"


def test_the_owner_running_discovery_writes_the_route_directly(site_world):
    result = discover(site_world, principal=site_world.person)
    assert result["proposal"] is None and result["applied"]["origin"] == "owner"
    assert store.get_route(topo(site_world), "example.com")["values"]["hops"] == BLOG_ROUTE


def test_an_unresolved_name_or_a_refusal_stores_nothing(world):
    world.gateway.topology_resolver = fake_resolver(RECORDS)
    result = discover(world, site="missing.example.com", proxies=())
    assert result["proposal"] is None and result["applied"] is None and not result["resolved"]
    with pytest.raises(TopologyError, match="not on the map yet"):
        discover(world)
    assert not topo(world).path.exists()
