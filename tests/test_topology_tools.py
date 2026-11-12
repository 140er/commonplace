"""topology_get and topology_route: answers, freshness, unknowns and the wing grant.

Every name is synthetic. The fixture map is seeded through the store; time moves by
patching the extension's clock, nothing sleeps.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from commonplace.policy import Policy
from commonplace.topology import Observer, extension, store
from tests import test_commonplace_rules_enforce as rules_tests
from tests.test_spaces_contract import http  # noqa: F401  (fixture)

env = rules_tests.env
call = rules_tests.call
text = rules_tests.text

OWNER = Observer("owner", "usr_owner")
EVIDENCE = "ssh -G web1: hostname web1.example.test"

NODES = {
    "laptop": {"kind": "pc", "name": "Laptop", "role": "workstation"},
    "jump1": {"kind": "server", "name": "Jump host", "aliases": ["jump"]},
    "web1": {
        "kind": "server",
        "name": "Web one",
        "role": "app host",
        "hostname": "web1.example.test",
        "aliases": ["web1"],
        "tailscale_name": "web1-ts",
        "runner_id": "mach_0123456789abcdef",
        "wings": ["shared"],
    },
    "app1": {"kind": "container", "name": "App"},
    "dns1": {"kind": "cloud", "name": "DNS provider"},
    "cdn": {"kind": "cloud", "name": "CDN"},
    "example.test": {"kind": "website", "name": "Example"},
    "lonely": {"kind": "vm", "name": "Unlinked VM"},
}
LINKS = [
    ("laptop", "jump1", "direct", {"status": "up", "alias": "jump"}),
    ("jump1", "web1", "jump_host", {"status": "up", "via": "jump1", "alias": "web1"}),
    ("laptop", "web1", "tailscale", {"status": "up", "alias": "web1-ts"}),
    ("web1", "app1", "hosts", {"status": "up"}),
    ("example.test", "dns1", "dns", {"status": "up"}),
    ("dns1", "cdn", "proxy", {"status": "up"}),
    ("cdn", "web1", "reverse_proxy", {"status": "up", "port": 443}),
]
ROUTE = [
    "example.test>dns1:dns",
    "dns1>cdn:proxy",
    "cdn>web1:reverse_proxy",
    "web1>app1:hosts",
]


@pytest.fixture
def topo(env, tmp_path):
    env.gateway.supervisor.workspace_dir = lambda workspace_id: (
        tmp_path / "workspaces" / workspace_id
    )
    return store.open_topology(env.gateway, env.workspace)


def seed(topo, *, links=LINKS, route=ROUTE):
    write = {"observer": OWNER, "evidence": EVIDENCE}
    for key, fields in NODES.items():
        store.upsert_node(topo, key, fields, **write)
    for source, target, method, fields in links:
        store.upsert_link(topo, source, target, method, fields, **write)
    if route:
        store.upsert_route(topo, "example.test", route, **write)


@pytest.fixture
def seeded(topo):
    seed(topo)
    return topo


def later(monkeypatch, **delta):
    moved = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(**delta)
    monkeypatch.setattr(extension, "_now", lambda: moved)


def answer(env, tool, principal=None, **args):
    response = call(env, tool, principal=principal, **args)
    assert not response["result"].get("isError"), response
    return json.loads(text(response))


def refused(env, tool, principal=None, **args):
    response = call(env, tool, principal=principal, **args)
    if "error" in response:
        return response["error"]["message"]
    assert response["result"].get("isError"), response
    return text(response)


def route_methods(route):
    return [hop["method"] for hop in route["hops"]]


# ── topology_get ──────────────────────────────────────────────────────────
def test_get_returns_the_whole_graph(env, seeded):
    result = answer(env, "topology_get")
    assert result["scope"] == "graph" and result["found"] is True
    assert result["counts"] == {"nodes": 8, "links": 7, "routes": 1, "conflicts": 0}
    assert {node["key"] for node in result["nodes"]} == set(NODES)
    assert result["as_of"].endswith("Z")


def test_get_a_node_has_provenance_freshness_and_its_links(env, seeded):
    result = answer(env, "topology_get", node="web1")
    assert result["found"] and result["node"]["values"]["hostname"] == "web1.example.test"
    field = result["node"]["fields"]["tailscale_name"]
    assert field["stale"] is False and field["expires_at"] and field["observed_at"]
    assert field["provenance"][0]["observer"] == {"kind": "owner", "id": "usr_owner"}
    assert field["provenance"][0]["evidence"] == EVIDENCE
    assert {link["id"] for link in result["links"]} == {
        "jump1>web1:jump_host",
        "laptop>web1:tailscale",
        "cdn>web1:reverse_proxy",
        "web1>app1:hosts",
    }


def test_get_a_site_returns_its_resolved_route(env, seeded):
    result = answer(env, "topology_get", site="example.test")
    assert result["found"] and result["route"]["site"] == "example.test"
    assert [hop["link"] for hop in result["route"]["hops"]] == ROUTE
    assert all(hop["known"] and hop["status"] == "up" for hop in result["route"]["hops"])


def test_get_says_plainly_when_nothing_is_recorded(env, seeded):
    node = answer(env, "topology_get", node="ghost")
    assert node["found"] is False and node["answer"].startswith("unknown:")
    site = answer(env, "topology_get", site="lonely")
    assert site["found"] is False and "no route is recorded" in site["answer"]


def test_get_on_an_empty_workspace_is_an_empty_map(env, topo):
    result = answer(env, "topology_get")
    assert result["counts"] == {"nodes": 0, "links": 0, "routes": 0, "conflicts": 0}
    assert not topo.path.exists()  # reading does not create the file


def test_get_shows_a_stale_status_as_stale(env, seeded, monkeypatch):
    later(monkeypatch, hours=1)  # a link status lives 15 minutes
    link = answer(env, "topology_get", node="laptop")["links"][0]
    assert "status" not in link["values"]
    assert link["fields"]["status"]["stale"] is True
    assert link["fields"]["status"]["value"] == "up"


# ── topology_route: how do I connect to X ─────────────────────────────────
def test_route_connects_with_the_best_way_first(env, seeded):
    result = answer(env, "topology_route", target="web1", **{"from": "laptop"})
    assert result["mode"] == "connect" and result["found"] and result["status"] == "up"
    best, second = result["routes"][:2]
    assert route_methods(best) == ["tailscale"]
    assert best["hops"][0]["alias"] == "web1-ts" and best["hops"][0]["status"] == "up"
    assert route_methods(second) == ["direct", "jump_host"]
    assert second["hops"][1]["via"] == "jump1" and second["hops"][1]["alias"] == "web1"
    assert result["node"]["runner_id"] == "mach_0123456789abcdef"
    assert result["node"]["wings"] == ["shared"]
    assert result["node"]["aliases"] == ["web1"]


def test_route_follows_several_hops_to_a_container(env, seeded):
    result = answer(env, "topology_route", target="app1", **{"from": "laptop"})
    assert result["status"] == "up"
    assert [hop["to"]["key"] for hop in result["routes"][0]["hops"]] == ["web1", "app1"]
    assert result["routes"][0]["hops"][0]["to"]["kind"] == "server"


def test_route_prefers_a_working_way_over_a_down_one(env, topo):
    seed(topo, links=[*LINKS[:2], ("laptop", "web1", "tailscale", {"status": "down"}), *LINKS[3:]])
    result = answer(env, "topology_route", target="web1", **{"from": "laptop"})
    best = result["routes"][0]
    assert route_methods(best) == ["direct", "jump_host"] and best["status"] == "up"
    assert result["routes"][1]["status"] == "down"


def test_route_reports_a_down_only_way_as_down(env, topo):
    seed(topo, links=[("laptop", "web1", "tailscale", {"status": "down"})], route=None)
    result = answer(env, "topology_route", target="web1", **{"from": "laptop"})
    assert result["status"] == "down" and result["routes"][0]["hops"][0]["status"] == "down"


def test_route_marks_a_stale_hop_instead_of_trusting_it(env, seeded, monkeypatch):
    later(monkeypatch, hours=1)
    result = answer(env, "topology_route", target="web1", **{"from": "laptop"})
    hop = result["routes"][0]["hops"][0]
    assert hop["status"] == "unknown" and hop["status_note"].startswith("the last status is stale")
    assert hop["status_observed_at"] and hop["alias"] == "web1-ts"
    assert result["status"] == "unknown" and result["routes"][0]["stale"] is False


def test_route_marks_a_fully_expired_link_stale(env, seeded, monkeypatch):
    later(monkeypatch, days=40)
    result = answer(env, "topology_route", target="web1", **{"from": "laptop"})
    hop = result["routes"][0]["hops"][0]
    assert hop["stale"] is True and result["routes"][0]["stale"] is True
    assert result["status"] == "unknown" and "alias" not in hop and "alias" in hop["stale_fields"]


def test_route_without_a_start_lists_the_recorded_ways_in(env, seeded):
    result = answer(env, "topology_route", target="web1")
    assert result["status"] == "partial" and "pass 'from'" in result["answer"]
    firsts = {route["hops"][0]["link"] for route in result["routes"]}
    assert firsts == {"laptop>web1:tailscale", "jump1>web1:jump_host"}
    assert all(len(route["hops"]) == 1 for route in result["routes"])


def test_route_to_a_machine_nothing_reaches_is_unknown(env, seeded):
    result = answer(env, "topology_route", target="lonely", **{"from": "laptop"})
    assert result["found"] is True and result["status"] == "unknown" and result["routes"] == []
    assert "no recorded path" in result["answer"]
    alone = answer(env, "topology_route", target="lonely")
    assert alone["status"] == "unknown" and alone["routes"] == []


def test_route_to_an_unrecorded_target_or_start_is_unknown(env, seeded):
    target = answer(env, "topology_route", target="ghost", **{"from": "laptop"})
    assert target["found"] is False and target["status"] == "unknown"
    assert target["routes"] == [] and "not a known machine or site" in target["answer"]
    start = answer(env, "topology_route", target="web1", **{"from": "ghost"})
    assert (
        start["status"] == "unknown"
        and "starting machine 'ghost' is not recorded" in start["answer"]
    )


def test_route_to_the_starting_machine_is_trivial(env, seeded):
    result = answer(env, "topology_route", target="web1", **{"from": "web1"})
    assert result["status"] == "up" and result["routes"] == []


def test_route_never_uses_site_routing_links_to_connect(env, seeded):
    # example.test reaches the CDN by dns/proxy, which are not ways to log in.
    result = answer(env, "topology_route", target="cdn", **{"from": "example.test"})
    assert result["routes"] == [] and result["status"] == "unknown"


def test_route_survives_a_loop_in_the_links(env, topo):
    seed(
        topo,
        links=[
            ("laptop", "jump1", "direct", {"status": "up"}),
            ("jump1", "laptop", "direct", {"status": "up"}),
            ("jump1", "web1", "jump_host", {"status": "up"}),
        ],
        route=None,
    )
    result = answer(env, "topology_route", target="web1", **{"from": "laptop"})
    assert len(result["routes"]) == 1 and len(result["routes"][0]["hops"]) == 2


# ── topology_route: where is site Y hosted ────────────────────────────────
def test_route_answers_where_a_site_is_hosted(env, seeded):
    result = answer(env, "topology_route", target="example.test")
    assert result["mode"] == "hosting" and result["found"] and result["status"] == "complete"
    hops = result["route"]["hops"]
    assert [hop["method"] for hop in hops] == ["dns", "proxy", "reverse_proxy", "hosts"]
    assert [hop["to"]["key"] for hop in hops] == ["dns1", "cdn", "web1", "app1"]
    assert hops[2]["port"] == 443 and hops[0]["to"]["kind"] == "cloud"
    assert result["hosted_on"]["key"] == "app1" and result["hosted_on"]["kind"] == "container"
    assert "example.test" in result["answer"] and "dns1 -> cdn -> web1 -> app1" in result["answer"]
    assert "unknown_hops" not in result and "stale_hops" not in result


def test_route_says_which_hop_is_unknown_and_does_not_invent_it(env, topo):
    seed(topo, links=[link for link in LINKS if link[2] != "reverse_proxy"])
    result = answer(env, "topology_route", target="example.test")
    assert result["status"] == "partial" and result["unknown_hops"] == ["cdn>web1:reverse_proxy"]
    hop = result["route"]["hops"][2]
    assert hop["known"] is False and hop["status"] == "unknown" and "no facts" in hop["note"]
    assert "unknown hops: cdn>web1:reverse_proxy" in result["answer"]


def test_route_says_which_hop_is_stale(env, topo, monkeypatch):
    write = {"observer": OWNER, "evidence": EVIDENCE}
    seed(topo, links=[link for link in LINKS if link[2] != "proxy"])
    store.upsert_link(topo, "dns1", "cdn", "proxy", {"status": "up"}, ttl_seconds=60, **write)
    later(monkeypatch, hours=1)
    result = answer(env, "topology_route", target="example.test")
    assert result["status"] == "partial" and result["stale_hops"] == ["dns1>cdn:proxy"]
    assert result["route"]["hops"][1]["stale"] is True
    assert result["route"]["hops"][1]["status"] == "unknown"


def test_route_reports_a_down_hop_without_calling_the_route_incomplete(env, topo):
    seed(topo, links=[*LINKS[:6], ("cdn", "web1", "reverse_proxy", {"status": "down"})])
    result = answer(env, "topology_route", target="example.test")
    assert result["status"] == "complete" and result["down_hops"] == ["cdn>web1:reverse_proxy"]
    assert "hops reported down" in result["answer"]


def test_route_for_a_site_without_a_route_is_unknown(env, topo):
    seed(topo, route=None)
    result = answer(env, "topology_route", target="example.test")
    assert result["mode"] == "hosting" and result["found"] is False
    assert result["status"] == "unknown" and result["route"] is None and result["hosted_on"] is None
    assert "no route is recorded" in result["answer"]


def test_route_marks_a_route_that_has_not_been_confirmed(env, seeded, monkeypatch):
    later(monkeypatch, days=40)
    result = answer(env, "topology_route", target="example.test")
    assert result["status"] == "partial" and result["route"]["stale"] is True
    assert "route itself has not been confirmed" in result["answer"]


# ── Arguments ─────────────────────────────────────────────────────────────
def test_arguments_are_validated(env, seeded):
    assert "not both" in refused(env, "topology_get", node="web1", site="example.test")
    assert "node must be" in refused(env, "topology_get", node="Web One!")
    assert "invalid arguments" in refused(env, "topology_get", extra=1)
    assert "target is required" in refused(env, "topology_route")
    assert "from must be" in refused(env, "topology_route", target="web1", **{"from": "A B"})
    assert "invalid arguments" in refused(env, "topology_route", target="web1", mode="x")


# ── Authorization ─────────────────────────────────────────────────────────
def test_a_connection_without_the_topology_grant_is_refused(env, seeded):
    _, outsider = env.agent("outsider", read_wings=["shared"])
    for tool, args in (("topology_get", {}), ("topology_route", {"target": "web1"})):
        message = refused(env, tool, principal=outsider, **args)
        assert "cannot read the topology wing" in message
        assert "web1.example.test" not in message


def test_a_connection_without_read_scope_does_not_see_the_tools(env, seeded):
    _, writer = env.agent("writer", scopes=["memory:write"], write_wings=["inbox"])
    visible = writer.policy.visible_tools()
    assert "topology_get" not in visible and "topology_route" not in visible
    assert "unknown tool" in refused(env, "topology_get", principal=writer).lower()
    assert writer.policy.refusal("topology_get") == "needs scope memory:read"


def test_an_agent_with_only_these_tools_answers_both_questions(env, seeded):
    _, agent = env.agent(
        "mapper",
        profile="slim",
        scopes=["memory:read"],
        read_wings=["topology"],
        write_wings=[],
        trust="standard",
    )
    assert {"topology_get", "topology_route"} <= set(agent.policy.visible_tools())
    connect = answer(env, "topology_route", principal=agent, target="web1", **{"from": "laptop"})
    assert route_methods(connect["routes"][0]) == ["tailscale"]
    hosted = answer(env, "topology_route", principal=agent, target="example.test")
    assert hosted["hosted_on"]["key"] == "app1"
    assert answer(env, "topology_get", principal=agent, node="web1")["found"]


def test_a_wing_pattern_that_covers_topology_is_enough(env, seeded):
    _, agent = env.agent("wide", read_wings=["top*"])
    assert answer(env, "topology_get", principal=agent)["counts"]["nodes"] == 8


def test_another_workspace_never_sees_these_facts(env, seeded):
    other = env.db.create_workspace("other-workspace", env.user)
    _, token = env.db.create_token(
        workspace_ref=other,
        user_ref=env.user,
        name="elsewhere",
        client="test",
        policy=Policy.build(profile="full", scopes=["memory:read"], read_wings=["*"]),
        actor=env.user,
    )
    principal = env.gateway.authenticate("Bearer " + token)
    assert principal.workspace_id != env.workspace
    assert answer(env, "topology_get", principal=principal)["counts"]["nodes"] == 0
    assert answer(env, "topology_get", principal=principal, node="web1")["found"] is False
    assert (
        answer(env, "topology_route", principal=principal, target="example.test")["found"] is False
    )
    assert answer(env, "topology_get")["counts"]["nodes"] == 8


# ── REST ──────────────────────────────────────────────────────────────────
def test_rest_matches_the_tools(env, seeded, http):  # noqa: F811
    status, body, _ = http("GET", "/api/topology")
    assert status == 200 and body["counts"]["nodes"] == 8
    status, body, _ = http("GET", "/api/topology?node=web1")
    assert status == 200 and body["node"]["key"] == "web1"
    status, body, _ = http("GET", "/api/topology?site=example.test")
    assert status == 200 and body["route"]["site"] == "example.test"
    status, body, _ = http("GET", "/api/topology/route?target=web1&from=laptop")
    assert status == 200 and route_methods(body["routes"][0]) == ["tailscale"]
    status, body, _ = http("GET", "/api/topology/route?target=example.test")
    assert status == 200 and body["status"] == "complete"
    status, body, _ = http("GET", "/api/topology/route?target=ghost")
    assert status == 200 and body["found"] is False


def test_rest_refuses_without_the_grant_and_bad_input(env, seeded, http):  # noqa: F811
    _, outsider_token = env.db.create_token(
        workspace_ref=env.workspace,
        user_ref=env.user,
        name="rest-outsider",
        client="test",
        policy=Policy.build(profile="full", scopes=["memory:read"], read_wings=["shared"]),
        actor=env.user,
    )
    for path in ("/api/topology", "/api/topology/route?target=web1"):
        status, body, _ = http("GET", path, token=outsider_token)
        assert status == 403 and "topology" in body["error"]
        assert "web1.example.test" not in json.dumps(body)
    assert http("GET", "/api/topology/route")[0] == 400
    assert http("GET", "/api/topology?node=web1&site=example.test")[0] == 400
    assert http("GET", "/api/topology?bogus=1")[0] == 400
    assert http("GET", "/api/topology", anonymous=True)[0] == 401
    assert http("GET", "/api/topology/route?target=web1", anonymous=True)[0] == 401


def test_the_owner_reads_the_topology_by_default(env, seeded, http):  # noqa: F811
    status, body, _ = http("GET", "/api/topology/route?target=example.test", principal=env.person)
    assert status == 200 and body["hosted_on"]["key"] == "app1"


# ── Loading ───────────────────────────────────────────────────────────────
def test_a_module_whose_package_is_missing_is_skipped_but_a_broken_import_is_not(
    monkeypatch, tmp_path
):
    from commonplace import extensions

    monkeypatch.setattr(extensions, "MODULES", ("commonplace.no_such_package.extension",))
    assert extensions.load_modules() == []
    monkeypatch.setattr(extensions, "MODULES", ("commonplace.topology.no_such_module",))
    assert extensions.load_modules() == []
    # An import that fails inside a module that exists is still an error.
    (tmp_path / "broken_extension_probe.py").write_text("import a_dependency_that_is_not_there\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(extensions, "MODULES", ("broken_extension_probe",))
    with pytest.raises(ModuleNotFoundError):
        extensions.load_modules()
