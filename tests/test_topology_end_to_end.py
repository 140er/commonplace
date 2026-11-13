"""The Compute map end to end on a clean workspace, with nothing about any real setup.

Two synthetic runner machines (invented system facts), one synthetic reverse proxy config
and a fake DNS resolver stand in for the user's world. The whole path runs through the real
hub: the owner presses "Start agent work on map", the started agents report, the owner
approves what they propose, the map and a device's four tabs are read through the routes the
screens call, and a second agent connection, that wrote nothing, answers "how do I connect
to X" and "where is site Y hosted" from the two topology tools alone.
"""

import json
import re
from pathlib import Path

import pytest

from commonplace import sessions
from commonplace.boards import BoardService
from commonplace.topology.site_dns import DnsAnswer
from tests import test_topology_discovery as discovery_tests
from tests._commonplace_helpers import hub_fixture  # noqa: F401  (fixture)
from tests.runner_harness import http
from tests.test_topology_site_adapters import fake_resolver

world = discovery_tests.world
alpha, beta, HUB_URL = discovery_tests.alpha, discovery_tests.beta, discovery_tests.HUB_URL

STATIC = Path(__file__).resolve().parent.parent / "commonplace" / "web" / "static"
SITE = "app.example.com"
CADDYFILE = f"{SITE} {{\n\treverse_proxy blog:3000\n}}\n"
RECORDS = {SITE: DnsAnswer(addresses=("192.0.2.10",))}
STATUSES = {"up", "degraded", "down", "unknown"}


def screen_path(constant):
    """A path exactly as the screens' code spells it, so the test reads what the app reads."""
    for name in ("topology-map.js", "topology-detail.js", "map-discovery.js"):
        found = re.search(rf'export const {constant} = "([^"]+)"', (STATIC / name).read_text())
        if found:
            return found.group(1)
    raise AssertionError(constant)


class Owner:
    """The signed-in workspace owner's reads and decisions over HTTP."""

    def __init__(self, world):
        self.world = world
        link = sessions.create_login_link(world.db, "owner@example.com", world.ws)
        self.cookie = f"{sessions.COOKIE}={sessions.redeem_login_link(world.db, link)}"

    def call(self, method, path, body=None):
        return http(self.world.hub.port, method, path, cookie=self.cookie, body=body)

    def get(self, path):
        status, body = self.call("GET", path)
        assert status == 200, (path, status, body)
        return body

    def press(self):
        status, body = self.call("POST", screen_path("DISCOVERY_PATH"), {})
        assert status == 200, body
        return body

    def pending(self):
        return self.get("/api/topology/proposals?state=pending")["proposals"]

    def decide(self, choice="Approve"):
        """Answer every open decision the way the card's own screen does."""
        service = BoardService(self.world.ctx())
        rows = self.pending()
        for row in rows:
            version = service.task_show(row["card"])["version"]
            status, body = self.call(
                "POST", f"/api/cards/{row['card']}/decide", {"choice": choice, "version": version}
            )
            assert status == 200, body
        return rows


class SecondAgent:
    """A connection that only reads the topology: it wrote nothing and sees two tools."""

    def __init__(self, world):
        self.hub = world.hub
        self.token = world.hub.token(
            "second-agent",
            profile="slim",
            scopes=["memory:read"],
            read_wings=["topology"],
            write_wings=[],
            trust="standard",
        )

    def tools(self):
        return self.hub.tool_names(self.token)

    def ask(self, tool, **arguments):
        answer, raw = self.hub.call(self.token, tool, **arguments)
        assert answer is not None, raw
        return answer

    def connect(self, target, start):
        return self.ask("topology_route", target=target, **{"from": start})

    def hosted(self, site):
        return self.ask("topology_route", target=site)


def site_agent(world):
    return world.hub.token(
        "site-agent",
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["topology"],
        write_wings=["topology"],
        trust="standard",
    )


def discover_site(world, board="compute-map"):
    """What an agent does for one website: pass in the proxy config it read, nothing else."""
    world.gw.topology_resolver = fake_resolver(RECORDS)
    response = world.hub.rpc(
        site_agent(world),
        "tools/call",
        {
            "name": "topology_discover_site",
            "arguments": {
                "site": SITE,
                "proxies": [{"node": "web1", "kind": "caddy", "config": CADDYFILE}],
                "machines": [{"node": "web1", "addresses": ["192.0.2.10"], "kind": "server"}],
                "board": board,
            },
        },
    )
    assert not response["result"].get("isError"), response
    return response["result"]["content"][0]["text"]


def run_discovery(world, owner):
    """Press the button, let both agents report, show what is waiting for the owner."""
    pressed = owner.press()
    assert {m["outcome"] for m in pressed["machines"]} == {"started"}
    world.run_agent("alpha-runner", alpha())
    world.run_agent("beta-runner", beta())
    return pressed


def keys(graph):
    return {node["key"] for node in graph["nodes"]}


# ── The whole path ────────────────────────────────────────────────────────
def test_from_a_clean_workspace_to_a_second_agent_answering_from_the_map(world):
    owner, second = Owner(world), SecondAgent(world)
    map_path = screen_path("MAP_PATH")

    # A clean workspace draws an empty map, and the second agent is told nothing is known.
    empty = owner.get(map_path)
    assert empty["counts"] == {"nodes": 0, "links": 0, "routes": 0, "conflicts": 0}
    assert second.hosted(SITE)["found"] is False
    assert second.connect("web1", "alpha")["answer"].startswith("unknown:")

    # Start agent work on map: one run per runner machine; the agents report.
    pressed = run_discovery(world, owner)
    assert {m["label"] for m in pressed["machines"]} == {"alpha-runner", "beta-runner"}
    # A machine's facts about itself are on the map at once; the machines it only saw wait.
    assert keys(owner.get(map_path)) == {"alpha", "beta"}
    waiting = owner.pending()
    assert waiting and all(row["state"] == "pending" for row in waiting)

    # The owner approves, over the same route the decision card's screen uses.
    decided = owner.decide("Approve")
    assert {row["id"] for row in decided} == {row["id"] for row in waiting}
    assert owner.pending() == []
    assert keys(owner.get(map_path)) == {"alpha", "beta", "hub", "phone", "web1", "inner"}

    # A site: an agent hands in the proxy config it read, the owner approves the route.
    reply = discover_site(world)
    assert "waits for the owner" in reply and "app.example.com>web1:dns" in reply
    assert len(owner.decide("Approve")) == 1

    # Read the map.
    graph = owner.get(map_path)
    assert (graph["counts"]["nodes"], graph["counts"]["routes"], graph["counts"]["conflicts"]) == (
        8,
        1,
        0,
    )
    assert keys(graph) == {"alpha", "beta", "hub", "phone", "web1", "inner", SITE, "web1-blog"}
    assert [route["site"] for route in graph["routes"]] == [SITE]
    assert set(graph["status"]["nodes"].values()) <= STATUSES
    # Only a runner with a live heartbeat is drawn up; nothing else is guessed green.
    assert graph["status"]["nodes"]["alpha"] == "up"
    assert graph["status"]["nodes"]["web1"] == graph["status"]["nodes"][SITE] == "unknown"

    # The device panel: Physical, Config, Agents, Sites, each through its read route.
    node = screen_path("NODE_PATH")
    machine = world.machines["alpha-runner"]
    panel = owner.get(f"{node}?key=alpha")
    fields = panel["node"]["fields"]
    # Physical
    assert fields["os"]["value"] == "Debian GNU/Linux 12"
    assert fields["hardware"]["value"] == "Example Corp Example Tower"
    assert fields["resources"]["value"]["cores"] == 8
    assert fields["os"]["sources"][0]["kind"] == "runner"
    # Config: the node's own fields and the route answer exactly as an agent gets it.
    assert fields["tailscale_name"]["value"] == "alpha.tail-example.ts.net"
    assert fields["runner_id"]["value"] == machine
    config = owner.get(f"{screen_path('ROUTE_PATH')}?target=beta&from=alpha")
    assert config["routes"][0]["hops"][0]["method"] == "tailscale"
    # Agents: the machine and the discovery run the button started, with its card.
    assert panel["agents"]["applicable"] and panel["agents"]["machine"]["id"] == machine
    [run] = panel["agents"]["runs"]
    started = next(m for m in pressed["machines"] if m["machine"] == machine)
    assert (run["job"], run["card"]) == (started["job"], started["card"])
    # Sites: the route passes through the proxy machine and ends at the container.
    web1 = owner.get(f"{node}?key=web1")
    assert [(s["site"], s["role"]) for s in web1["sites"]] in (
        [(SITE, "through")],
        [(SITE, "hosted")],
    )
    blog = owner.get(f"{node}?key=web1-blog")
    assert [(s["site"], s["role"]) for s in blog["sites"]] == [(SITE, "hosted")]
    assert web1["agents"] == {"applicable": False, "reason": "not_a_runner"}

    # A second agent connection answers both questions from the topology tools alone.
    assert {"topology_get", "topology_route"} <= second.tools()
    assert not second.tools() & {"topology_propose", "topology_discover_site"}
    to_beta = second.connect("beta", "alpha")
    [way] = to_beta["routes"]
    assert [(h["method"], h["alias"]) for h in way["hops"]] == [
        ("tailscale", "beta.tail-example.ts.net")
    ]
    assert (
        to_beta["status"] == "up" and to_beta["node"]["runner_id"] == world.machines["beta-runner"]
    )
    to_web1 = second.connect("web1", "alpha")
    assert (to_web1["routes"][0]["hops"][0]["method"], to_web1["routes"][0]["hops"][0]["port"]) == (
        "direct",
        2222,
    )
    to_inner = second.connect("inner", "alpha")
    [hop] = to_inner["routes"][0]["hops"]
    assert (hop["method"], hop["via"]) == ("jump_host", "web1")
    hosted = second.hosted(SITE)
    assert hosted["mode"] == "hosting" and hosted["hosted_on"]["key"] == "web1-blog"
    assert [(h["method"], h["from"]["key"], h["to"]["key"]) for h in hosted["route"]["hops"]] == [
        ("dns", SITE, "web1"),
        ("reverse_proxy", "web1", "web1-blog"),
    ]
    assert hosted["status"] == "complete" and "unknown_hops" not in hosted
    site = second.ask("topology_get", site=SITE)
    assert site["found"] and site["route"]["site"] == SITE
    # What it learned came from the runners and the site agent, never from itself, and no
    # key file or credential from the machines' configuration reached the answers.
    inner = second.ask("topology_get", node="inner")
    [source] = inner["node"]["fields"]["kind"]["provenance"]
    assert source["observer"] == {"kind": "runner", "id": machine}
    answers = json.dumps([to_beta, to_web1, to_inner, hosted, site, inner])
    assert "id_ed25519" not in answers and "IdentityFile" not in answers


def test_the_screens_read_the_routes_this_test_reads():
    assert screen_path("MAP_PATH") == "/api/topology"
    assert screen_path("NODE_PATH") == "/api/topology/node"
    assert screen_path("ROUTE_PATH") == "/api/topology/route"
    assert screen_path("DISCOVERY_PATH") == "/api/topology/discovery"


# ── The path needs its steps ──────────────────────────────────────────────
@pytest.mark.parametrize("choice", ["Reject", None])
def test_without_the_owner_s_approval_the_second_agent_learns_nothing_more(world, choice):
    owner, second = Owner(world), SecondAgent(world)
    run_discovery(world, owner)
    discover_site(world)
    if choice:
        owner.decide(choice)
    graph = owner.get(screen_path("MAP_PATH"))
    # Only what each machine said about itself is on the map.
    assert keys(graph) == {"alpha", "beta"} and graph["counts"]["routes"] == 0
    assert second.connect("inner", "alpha")["answer"].startswith("unknown:")
    assert second.hosted(SITE)["found"] is False
    assert second.hosted(SITE)["status"] == "unknown"


def test_without_pressing_the_button_there_is_nothing_to_approve(world):
    owner, second = Owner(world), SecondAgent(world)
    assert owner.pending() == []
    assert second.connect("beta", "alpha")["answer"].startswith("unknown:")
    assert owner.get(screen_path("MAP_PATH"))["counts"]["nodes"] == 0
