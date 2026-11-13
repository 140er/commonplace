"""The map's "Add machine" and "Add site": one button each, a run when an agent can take it,
a ready prompt when not, and never a map change before the owner approves.

Runner machines are fakes behind the real gateway (the discovery tests' world); every name
here is invented.
"""

import re

import pytest

from commonplace import sessions
from commonplace.boards import BoardService
from commonplace.extensions import ToolError
from commonplace.runner import jobs, registry
from commonplace.topology import add_node, collector, discovery, site_routes, store
from commonplace.topology.site_dns import DnsAnswer
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.runner_harness import http
from tests.test_topology_discovery import HUB_URL, World, alpha, beta

DOMAIN = "example.com"


@pytest.fixture
def world(hub):
    return World(hub)


def nodes(world):
    return {n["key"]: n["values"] for n in store.read_graph(world.topo())["nodes"]}


def cards(world, title_start):
    try:
        listed = BoardService(world.ctx()).task_list(board=discovery.BOARD, all_statuses=True)
    except ToolError:  # no discovery board yet: nothing was ever started
        return []
    return [c for c in listed if c["title"].startswith(title_start)]


def cookie_for(world, email):
    link = sessions.create_login_link(world.db, email, world.ws)
    return f"{sessions.COOKIE}={sessions.redeem_login_link(world.db, link)}"


# ── Add machine ──────────────────────────────────────────────────────────
def test_add_machine_starts_a_normal_run_on_a_runner_that_is_not_on_the_map(world):
    result = add_node.add_machine(world.ctx(), hub_url=HUB_URL)
    assert result["mode"] == "started" and result["prompt"] is None
    assert {m["outcome"] for m in result["machines"]} == {"started"}
    assert len(result["machines"]) == 2
    store_ = jobs.open_store(world.gw, world.ws)
    service = BoardService(world.ctx())
    for entry in result["machines"]:
        [run] = jobs.status(store_, entry["card"], world.owner)
        assert (run["job"], run["state"]) == (entry["job"], "queued")
        card = service.task_show(entry["card"])
        assert card["board"] == discovery.BOARD and card["status"] == "todo"
        assert card["title"].startswith("Add machine ")
        assert f"--runner-id {entry['machine']}" in card["body"]


def test_the_machine_appears_on_the_map_after_approval_only(world):
    add_node.add_machine(world.ctx(), hub_url=HUB_URL)
    world.run_agent("alpha-runner", alpha())
    world.run_agent("beta-runner", beta())
    # Their own facts apply as a runner's additions about itself; the machines they only
    # saw (the hub, a phone, ssh hosts) wait for the owner.
    assert set(nodes(world)) == {"alpha", "beta"}
    world.approve_all()
    assert {"alpha", "beta", "hub", "phone", "web1", "inner"} <= set(nodes(world))


def test_a_second_press_starts_nothing_new(world):
    first = add_node.add_machine(world.ctx(), hub_url=HUB_URL)
    again = add_node.add_machine(world.ctx(), hub_url=HUB_URL)
    assert {m["outcome"] for m in again["machines"]} == {"running"}
    assert again["mode"] == "started"
    assert [m["job"] for m in again["machines"]] == [m["job"] for m in first["machines"]]


def test_a_machine_already_on_the_map_is_not_started_again(world):
    add_node.add_machine(world.ctx(), hub_url=HUB_URL)
    world.run_agent("alpha-runner", alpha())
    result = add_node.add_machine(world.ctx(), hub_url=HUB_URL)
    assert [m["label"] for m in result["machines"]] == ["beta-runner"]


def test_without_an_agent_the_button_hands_out_the_prompt_and_the_command(world):
    world.run_agent("alpha-runner", alpha())
    world.run_agent("beta-runner", beta())
    before = len(cards(world, ""))
    result = add_node.add_machine(world.ctx(), hub_url=HUB_URL)
    assert result["mode"] == "prompt" and result["machines"] == []
    assert result["command"] == f"{add_node.COMMAND} --hub-url {HUB_URL}"
    assert result["command"] in result["prompt"]
    assert "topology_propose" in result["prompt"]
    assert len(cards(world, "")) == before


def test_no_runner_machine_at_all_also_falls_back(hub):
    empty = World.__new__(World)
    empty.hub, empty.gw, empty.db, empty.ws = hub, hub.gateway, hub.db, hub.workspace
    empty.owner = sessions.person_principal(empty.db, hub.admin, empty.ws)
    result = add_node.add_machine(empty.ctx(), hub_url="")
    assert result["mode"] == "prompt" and result["command"] == add_node.COMMAND
    assert "--hub-url" not in result["prompt"]


def test_machines_that_cannot_take_the_work_fall_back_with_their_reasons(hub):
    only = World.__new__(World)
    only.hub, only.gw, only.db, only.ws = hub, hub.gateway, hub.db, hub.workspace
    only.owner = sessions.person_principal(only.db, hub.admin, only.ws)
    only.machines = {}
    only.machine("gone-runner", live=False)
    only.machine("limited-runner", wings=("shared",))
    result = add_node.add_machine(only.ctx(), hub_url=HUB_URL)
    assert result["mode"] == "prompt"
    assert {m["label"]: m["reason"] for m in result["machines"]} == {
        "gone-runner": "never_reported",
        "limited-runner": "no_topology_wing",
    }


def test_the_collector_works_for_a_machine_that_is_not_a_runner():
    out = collector.collect(alpha(), hub_url=HUB_URL)
    [own] = [i for i in out["propose"]["items"] if "node" in i and "runner_id" not in i["fields"]]
    assert own["node"] == "alpha" and "runner_id" not in own["fields"]


# ── Add site ─────────────────────────────────────────────────────────────
def test_add_site_starts_one_run_with_the_domain_as_the_only_input(world):
    result = add_node.add_site(world.ctx(), DOMAIN, hub_url=HUB_URL)
    assert result["mode"] == "started" and result["prompt"] is None
    [taken] = [m for m in result["machines"] if m["outcome"] == "started"]
    card = BoardService(world.ctx()).task_show(taken["card"])
    assert card["board"] == discovery.BOARD and card["status"] == "todo"
    assert DOMAIN in card["title"] and DOMAIN in card["body"]
    assert "topology_discover_site" in card["body"]
    assert len(cards(world, "Trace the route")) == 1


def test_the_site_appears_on_the_map_after_approval_only(world):
    result = add_node.add_site(world.ctx(), DOMAIN)
    [taken] = [m for m in result["machines"] if m["outcome"] == "started"]
    ctx = world.ctx(world.worker(taken["machine"]))
    answer = site_routes.discover(
        ctx, site=DOMAIN, resolver=lambda name: DnsAnswer(addresses=("203.0.113.10",))
    )
    assert answer["proposal"] and answer["proposal"]["state"] == "pending"
    assert DOMAIN not in nodes(world)
    world.approve_all()
    assert DOMAIN in nodes(world)
    assert store.get_route(world.topo(), DOMAIN)["hops"]


def test_a_second_press_for_the_same_site_runs_once_and_another_site_starts(world):
    first = add_node.add_site(world.ctx(), DOMAIN)
    again = add_node.add_site(world.ctx(), DOMAIN)
    assert [m["outcome"] for m in again["machines"]] == ["running"]
    assert again["machines"][0]["job"] == next(
        m["job"] for m in first["machines"] if m["outcome"] == "started"
    )
    other = add_node.add_site(world.ctx(), "example.org")
    assert [m["outcome"] for m in other["machines"]] == ["started"]


def test_add_site_without_an_agent_hands_out_the_prompt(world):
    for machine in world.machines.values():
        registry.revoke(world.db, world.owner, machine)
    result = add_node.add_site(world.ctx(), "Example.COM.")
    assert result["mode"] == "prompt" and result["command"] is None
    assert DOMAIN in result["prompt"] and "topology_discover_site" in result["prompt"]


@pytest.mark.parametrize("bad", [None, "", "localhost", "10.0.0.1", "intranet", "a b.example.com"])
def test_a_bad_domain_is_refused_and_nothing_starts(world, bad):
    with pytest.raises(add_node.InvalidSite):
        add_node.add_site(world.ctx(), bad)
    assert not cards(world, "Trace the route")


# ── Rights and the route ─────────────────────────────────────────────────
def test_only_the_signed_in_owner_may_add(world, hub):
    member = world.db.create_user("member@example.com")
    world.db.add_member(world.ws, member, "member", actor=hub.admin)
    principal = sessions.person_principal(world.db, member, world.ws)
    for call in (
        lambda: add_node.add_machine(world.ctx(principal)),
        lambda: add_node.add_site(world.ctx(principal), DOMAIN),
    ):
        with pytest.raises(ToolError, match="owner"):
            call()
    assert not cards(world, "Add machine") and not cards(world, "Trace the route")
    worker = world.worker(world.machines["alpha-runner"])
    with pytest.raises(ToolError):
        add_node.add_machine(world.ctx(worker))


def test_the_routes_are_the_owner_s_and_answer_in_json(world, hub):
    owner = cookie_for(world, "owner@example.com")
    status, body = http(hub.port, "POST", "/api/topology/add-machine", cookie=owner, body={})
    assert status == 200, body
    assert body["kind"] == "machine" and body["mode"] == "started"
    status, body = http(
        hub.port, "POST", "/api/topology/add-site", cookie=owner, body={"site": DOMAIN}
    )
    assert status == 200 and body["kind"] == "site" and body["mode"] == "started", body
    status, body = http(
        hub.port, "POST", "/api/topology/add-site", cookie=owner, body={"site": "intranet"}
    )
    assert status == 400 and "domain" in body["error"]
    status, body = http(
        hub.port, "POST", "/api/topology/add-site", cookie=owner, body={"site": DOMAIN, "x": 1}
    )
    assert status == 400
    member = world.db.create_user("member@example.com")
    world.db.add_member(world.ws, member, "member", actor=hub.admin)
    other = cookie_for(world, "member@example.com")
    for path, payload in (("add-machine", {}), ("add-site", {"site": DOMAIN})):
        status, body = http(hub.port, "POST", f"/api/topology/{path}", cookie=other, body=payload)
        assert status == 403, body
        status, _ = http(hub.port, "POST", f"/api/topology/{path}", body=payload)
        assert status == 401


def test_the_discovery_button_still_sees_only_its_own_runs(world):
    add_node.add_machine(world.ctx(), hub_url=HUB_URL)
    assert discovery.latest(world.ctx())["machines"] == []
    mapped = world.start()
    assert {m["outcome"] for m in mapped["machines"]} == {"started"}
    assert [m["job"] for m in discovery.latest(world.ctx())["machines"]] == [
        m["job"] for m in mapped["machines"]
    ]


# ── Nothing specific to anyone's setup ───────────────────────────────────
GENERIC = {
    "address": r"\b\d{1,3}(?:\.\d{1,3}){3}\b|\b[0-9a-f]{1,4}(?::[0-9a-f]{0,4}){3,}\b",
    "url": r"https?://|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|dev|app|ts\.net|local|lan|internal)\b",
    "mail": r"\S+@\S+",
    "path": r"/(?:home|Users|root|etc/(?!os-release))\S*|~/",
    "secret": r"\b(?:ghp_|sk-|mach_|Bearer )\S*",
}


@pytest.mark.parametrize("name", ["MACHINE_PROMPT", "SITE_PROMPT"])
def test_the_prompts_contain_nothing_specific_to_one_setup(name, world):
    text = getattr(add_node, name)
    for what, pattern in GENERIC.items():
        assert not re.search(pattern, text, re.I), (name, what)
    # Whatever the invented workspace holds never leaks into the template either.
    for value in (*world.machines, HUB_URL, "owner@example.com"):
        assert value not in text
    assert "{" not in text.format(command="x", site="y")


def test_what_the_user_sees_is_the_template_plus_only_the_hub_address_and_the_domain(world):
    machine = add_node.machine_prompt(HUB_URL)
    assert machine == add_node.MACHINE_PROMPT.format(
        command=f"{add_node.COMMAND} --hub-url {HUB_URL}"
    )
    site = add_node.site_prompt(DOMAIN)
    assert site == add_node.SITE_PROMPT.format(site=DOMAIN)
    stripped = site.replace(DOMAIN, "")
    for what, pattern in GENERIC.items():
        assert not re.search(pattern, stripped, re.I), what
