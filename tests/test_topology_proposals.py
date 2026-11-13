"""Topology proposals through the real gateway: propose, decide in the app, apply, undo."""

import pytest

from commonplace import events, sessions
from commonplace.boards import BoardService
from commonplace.extensions import ToolContext, ToolError
from commonplace.runner import registry
from commonplace.topology import SecretRefused, TopologyError, proposals, store
from tests import (
    test_commonplace_rules_enforce as rules_tests,
    test_spaces_contract as contract_tests,
)

env = rules_tests.env
http = contract_tests.http
call = rules_tests.call
text = rules_tests.text

EVIDENCE = "uname -a: Linux web1 6.8.0 x86_64"
WEB1 = {"node": "web1", "fields": {"kind": "server", "os": "Linux 6.8"}}


@pytest.fixture
def world(env):
    BoardService(ToolContext(env.person, env.gateway)).board_add(slug="work", wing="shared")
    return env


def ctx(env, principal=None):
    return ToolContext(principal or env.principal, env.gateway)


def topo(env):
    return store.open_topology(env.gateway, env.workspace)


def board(env, principal=None):
    return BoardService(ctx(env, principal or env.person))


def seed(env, items=(WEB1,), evidence=EVIDENCE):
    """The owner's own write: applies at once, as the authoritative value."""
    return proposals.propose(ctx(env, env.person), items=list(items), evidence=evidence)


def answer(env, row, choice, principal=None):
    card = board(env).task_show(row["card"])
    return board(env, principal).decide(row["card"], choice=choice, version=card["version"])


def runner(env, wings=("shared", "topology")):
    data = registry.enroll(
        env.db,
        env.person,
        {"label": "runner-one", "projects": ["demo"], "topics": ["worker"], "wings": list(wings)},
    )
    return data["machine_id"], env.gateway.authenticate("Bearer " + data["worker_token"])


def os_change(env, value="Linux 6.9"):
    return proposals.propose(
        ctx(env),
        items=[{"node": "web1", "fields": {"os": value}}],
        evidence="os-release reports " + value,
    )["proposal"]


# ── An agent proposal is one decision with a readable before and after ────
def test_agent_proposal_is_one_decision_with_before_and_after(world):
    seed(world)
    before = events.latest_seq(world.db, world.workspace)
    result = proposals.propose(
        ctx(world),
        items=[
            {"node": "web1", "fields": {"os": "Linux 6.9", "role": "web server"}},
            {"link": ["laptop", "web1", "tailscale"], "fields": {"status": "up"}},
        ],
        evidence="os-release reports Linux 6.9",
    )
    row = result["proposal"]
    assert result["applied"] is None and row["state"] == "pending"
    assert row["observer"] == {"kind": "agent", "id": world.principal.connection_id}
    assert {(c["subject"], c["predicate"], c["before"], c["after"]) for c in row["changes"]} == {
        ("node:web1", "os", "Linux 6.8", "Linux 6.9"),
        ("node:web1", "role", None, "web server"),
        ("link:laptop>web1:tailscale", "method", None, "tailscale"),
        ("link:laptop>web1:tailscale", "status", None, "up"),
    }
    # Nothing is on the map yet.
    assert store.get_node(topo(world), "web1")["values"] == {"kind": "server", "os": "Linux 6.8"}
    assert store.get_link(topo(world), "laptop", "web1", "tailscale") is None

    waiting = [c for c in board(world).task_list(board="work") if c["status"] == "review"]
    assert [c["number"] for c in waiting] == [row["card"]]
    card = board(world).task_show(row["card"])
    assert card["ask_to"] == world.person.actor and card["ask_options"] == ["Approve", "Reject"]
    assert card["ask"].startswith("Apply this change to the map? change node web1; add link ")
    approve, reject = card["ask_blocks"]
    assert approve["preview"].splitlines() == [
        "After:",
        "node web1",
        "  os: Linux 6.9",
        "  role: web server",
        "link laptop>web1:tailscale",
        "  method: tailscale",
        "  status: up",
    ]
    assert reject["preview"].splitlines() == [
        "Before:",
        "node web1",
        "  os: Linux 6.8",
        "  role: (not set)",
        "link laptop>web1:tailscale",
        "  method: (not set)",
        "  status: (not set)",
    ]
    assert approve["detail"].startswith("Change 4 facts on the map.")
    assert "Evidence:\nos-release reports Linux 6.9" in card["body"]
    needs = board(world).needs_me()
    assert [c["number"] for c in needs["asks"]] == [row["card"]]
    kinds = [e["kind"] for e in events.after(world.db, world.workspace, before)]
    assert kinds.count("card.created") == 1


def test_a_rerun_asks_only_once(world):
    seed(world)
    first, again = os_change(world), os_change(world)
    assert again["id"] == first["id"] and again["card"] == first["card"]
    assert len(proposals.listing(ctx(world), "pending")) == 1
    other = os_change(world, "Linux 7.0")
    assert other["id"] != first["id"]


def test_a_rejected_change_is_not_asked_again_for_a_while(world, monkeypatch):
    seed(world)
    first = os_change(world)
    answer(world, first, "Reject")
    cards = len(board(world).task_list(board="work"))
    # The same change again, also from another agent, gets the owner's answer back.
    again = os_change(world)
    assert again["id"] == first["id"] and again["state"] == "rejected"
    _, other = world.agent("other")
    args = {"items": [{"node": "web1", "fields": {"os": "Linux 6.9"}}], "evidence": EVIDENCE}
    assert proposals.propose(ctx(world, other), **args)["proposal"]["id"] == first["id"]
    response = call(world, "topology_propose", **args)
    assert text(response).startswith("the owner rejected this change on ")
    assert len(board(world).task_list(board="work")) == cards
    assert store.list_conflicts(topo(world), open_only=False) == []
    # A different change is a new question, and so is the same one after the quiet time.
    assert os_change(world, "Linux 7.0")["state"] == "pending"
    monkeypatch.setattr(proposals, "REJECTED_QUIET_SECONDS", 0)
    later = os_change(world)
    assert later["id"] != first["id"] and later["state"] == "pending"


def test_no_decision_means_nothing_is_written(env):
    machine_id, worker = runner(env)
    with pytest.raises(ToolError, match="board not found"):
        proposals.propose(
            ctx(env, worker),
            items=[
                {"node": "runner-one", "fields": {"kind": "server", "runner_id": machine_id}},
                {"node": "web1", "fields": {"kind": "server"}},
            ],
            evidence=EVIDENCE,
            board="missing",
        )
    assert store.read_graph(topo(env))["nodes"] == []
    assert [p["state"] for p in proposals.listing(ctx(env))] == ["void"]


def test_approve_applies_and_keeps_history(world):
    seed(world)
    row = os_change(world)
    answer(world, row, "Approve")
    applied = proposals.get(ctx(world), row["id"])
    assert applied["state"] == "applied" and applied["decided_by"] == world.person.actor
    node = store.get_node(topo(world), "web1")
    assert node["values"]["os"] == "Linux 6.9"
    # The proposing agent stays the observer, with its own evidence.
    assert node["fields"]["os"]["provenance"][0]["observer"] == {
        "kind": "agent",
        "id": world.principal.connection_id,
    }
    assert node["fields"]["os"]["provenance"][0]["evidence"].startswith("os-release reports")
    values = [
        (h["value"], h["state"]) for h in store.history(topo(world), "node:web1", predicate="os")
    ]
    assert values == [("Linux 6.9", "current"), ("Linux 6.8", "superseded")]


def test_reject_keeps_the_old_fact(world):
    seed(world)
    row = os_change(world)
    answer(world, row, "Reject")
    assert proposals.get(ctx(world), row["id"])["state"] == "rejected"
    assert store.get_node(topo(world), "web1")["values"]["os"] == "Linux 6.8"
    assert len(store.history(topo(world), "node:web1", predicate="os")) == 1


def test_a_reply_without_a_choice_does_not_apply(world):
    seed(world)
    row = os_change(world)
    card = board(world).task_show(row["card"])
    board(world).decide(row["card"], reply="Not sure about this one", version=card["version"])
    closed = proposals.get(ctx(world), row["id"])
    assert closed["state"] == "rejected" and "without choosing Approve" in closed["note"]
    assert store.get_node(topo(world), "web1")["values"]["os"] == "Linux 6.8"


def test_approval_through_the_app_route(world, http):
    seed(world)
    row = os_change(world)
    card = board(world).task_show(row["card"])
    status, _, _ = http(
        "POST",
        f"/api/cards/{row['card']}/decide",
        {"choice": "Approve", "version": card["version"]},
        principal=world.person,
        headers={"Origin": f"http://127.0.0.1:{http.port}"},
    )
    assert status == 200
    assert store.get_node(topo(world), "web1")["values"]["os"] == "Linux 6.9"
    status, body, _ = http("GET", "/api/topology/proposals/" + row["id"])
    assert status == 200 and body["state"] == "applied"


def test_removal_is_proposed_and_applied(world):
    seed(
        world,
        [
            WEB1,
            {"node": "example.test", "fields": {"kind": "website"}},
            {"route": "example.test", "hops": ["example.test>web1:reverse_proxy"]},
        ],
    )
    result = proposals.propose(
        ctx(world),
        remove=["route:example.test", {"subject": "node:web1", "field": "os"}],
        evidence="The site moved away and the host was reinstalled.",
    )
    row = result["proposal"]
    card = board(world).task_show(row["card"])
    assert "remove route example.test" in card["ask"]
    assert "  hops: (removed)" in card["ask_blocks"][0]["preview"]
    assert "  hops: example.test>web1:reverse_proxy" in card["ask_blocks"][1]["preview"]
    assert store.get_route(topo(world), "example.test") is not None
    answer(world, row, "Approve")
    assert store.get_route(topo(world), "example.test") is None
    assert store.get_node(topo(world), "web1")["values"] == {"kind": "server"}


# ── A runner's self-report ────────────────────────────────────────────────
def test_runner_self_report_applies_without_a_question(world):
    machine_id, worker = runner(world)
    own = {
        "node": "runner-one",
        "fields": {"kind": "server", "runner_id": machine_id, "os": "Linux 6.8"},
    }
    link = {"link": ["runner-one", "hub", "direct"], "fields": {"status": "up"}}
    result = proposals.propose(ctx(world, worker), items=[own, link], evidence=EVIDENCE)
    assert result["proposal"] is None and result["applied"]["state"] == "applied"
    assert result["applied"]["origin"] == "self_report"
    node = store.get_node(topo(world), "runner-one")
    assert node["values"]["os"] == "Linux 6.8"
    assert node["fields"]["os"]["provenance"][0]["observer"] == {"kind": "runner", "id": machine_id}
    assert store.get_link(topo(world), "runner-one", "hub", "direct")["values"]["status"] == "up"
    assert not [c for c in board(world).task_list(board="work") if c["status"] == "review"]

    # A rerun changes nothing and asks nothing; it only refreshes what was seen.
    again = proposals.propose(ctx(world, worker), items=[own, link], evidence=EVIDENCE)
    assert again == {"applied": None, "proposal": None, "unchanged": 5, "observed": 0}
    assert node["fields"]["os"]["provenance"][0]["seen_count"] == 1
    seen = store.get_node(topo(world), "runner-one")["fields"]["os"]["provenance"][0]
    assert seen["seen_count"] == 2
    assert len(proposals.listing(ctx(world))) == 1


def test_runner_reports_its_own_link_status_without_a_question(world):
    machine_id, worker = runner(world)
    own = {"node": "runner-one", "fields": {"kind": "server", "runner_id": machine_id}}
    link = {"link": ["runner-one", "hub", "direct"], "fields": {"status": "up"}}
    proposals.propose(ctx(world, worker), items=[own, link], evidence=EVIDENCE)
    down = {"link": ["runner-one", "hub", "direct"], "fields": {"status": "down"}}
    result = proposals.propose(ctx(world, worker), items=[down], evidence="ping hub: timeout")
    assert result == {"applied": None, "proposal": None, "unchanged": 1, "observed": 1}
    assert store.get_link(topo(world), "runner-one", "hub", "direct")["values"]["status"] == "down"
    assert len(proposals.listing(ctx(world))) == 1
    # Another machine's link, or an agent's report of this one, is still the owner's call.
    other = {"link": ["web1", "hub", "direct"], "fields": {"status": "up"}}
    seed(world, [WEB1, other])
    other["fields"]["status"] = "down"
    assert proposals.propose(ctx(world, worker), items=[other], evidence=EVIDENCE)["proposal"]
    up = {"link": ["runner-one", "hub", "direct"], "fields": {"status": "up"}}
    assert proposals.propose(ctx(world), items=[up], evidence=EVIDENCE)["proposal"]["state"] == (
        "pending"
    )


def test_runner_change_and_facts_about_others_need_the_owner(world):
    machine_id, worker = runner(world)
    seed(world)
    proposals.propose(
        ctx(world, worker),
        items=[{"node": "runner-one", "fields": {"kind": "server", "runner_id": machine_id}}],
        evidence=EVIDENCE,
    )
    result = proposals.propose(
        ctx(world, worker),
        items=[
            # About itself, new: applies. About itself, changed: needs the owner.
            {"node": "runner-one", "fields": {"kind": "vm", "hardware": "8 cores"}},
            # About another machine, and claiming a node that already exists: the owner.
            {"node": "web1", "fields": {"runner_id": machine_id, "role": "mine now"}},
            {"route": "example.test", "hops": ["example.test>web1:dns"]},
        ],
        evidence=EVIDENCE,
    )
    assert [c["predicate"] for c in result["applied"]["changes"]] == ["hardware"]
    assert {(c["subject"], c["predicate"]) for c in result["proposal"]["changes"]} == {
        ("node:runner-one", "kind"),
        ("node:web1", "runner_id"),
        ("node:web1", "role"),
        ("route:example.test", "hops"),
    }
    values = store.get_node(topo(world), "runner-one")["values"]
    assert values["kind"] == "server" and values["hardware"] == "8 cores"
    assert "runner_id" not in store.get_node(topo(world), "web1")["values"]


def test_runner_without_the_wing_is_refused(world):
    _, worker = runner(world, wings=("shared",))
    with pytest.raises(ToolError, match="cannot write the topology wing"):
        proposals.propose(ctx(world, worker), items=[WEB1], evidence=EVIDENCE)
    listed = world.gateway.mediator.handle(
        worker, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert "topology_propose" not in {t["name"] for t in listed["result"]["tools"]}
    assert not topo(world).path.exists()


def test_worker_uses_the_tool_over_mcp_until_its_machine_is_disabled(world):
    machine_id, worker = runner(world)
    assert worker.policy.tool_visible("topology_propose")
    response = call(
        world,
        "topology_propose",
        principal=worker,
        items=[{"node": "runner-one", "fields": {"kind": "pc", "runner_id": machine_id}}],
        evidence=EVIDENCE,
    )
    assert not response["result"].get("isError"), response
    assert text(response).startswith("applied 2 facts (tp_")
    registry.patch(world.db, world.person, machine_id, {"enabled": False})
    response = call(
        world,
        "topology_propose",
        principal=worker,
        items=[{"node": "runner-one", "fields": {"os": "Linux"}}],
        evidence=EVIDENCE,
    )
    assert text(response) == "no: this runner is not active"


# ── Only the owner approves ───────────────────────────────────────────────
def test_a_non_owner_cannot_approve(world):
    seed(world)
    row = os_change(world)
    # The proposing agent, another agent and a signed-in member are all refused.
    _, other = world.agent("other")
    member_id = world.db.create_user("member@example.test")
    world.db.add_member(world.workspace, member_id, "member", actor=world.user)
    member = sessions.person_principal(world.db, member_id, world.workspace)
    for principal in (world.principal, other, member):
        with pytest.raises(ToolError):
            answer(world, row, "Approve", principal)
    response = call(world, "decide", task=row["card"], choice="Approve")
    assert response["result"]["isError"]
    assert proposals.get(ctx(world), row["id"])["state"] == "pending"
    assert store.get_node(topo(world), "web1")["values"]["os"] == "Linux 6.8"


def test_an_edited_decision_is_void_even_when_the_owner_approves(world):
    seed(world)
    row = os_change(world)
    card = board(world).task_show(row["card"])
    board(world, world.principal).task_update(
        row["card"], title="Approve the lunch order?", version=card["version"]
    )
    answer(world, row, "Approve")
    closed = proposals.get(ctx(world), row["id"])
    assert closed["state"] == "void" and "changed before the owner answered" in closed["note"]
    assert store.get_node(topo(world), "web1")["values"]["os"] == "Linux 6.8"


def test_a_map_that_moved_on_voids_the_approval(world):
    seed(world)
    row = os_change(world)
    seed(world, [{"node": "web1", "fields": {"os": "Linux 7.0"}}])
    answer(world, row, "Approve")
    closed = proposals.get(ctx(world), row["id"])
    assert closed["state"] == "void" and "map changed" in closed["note"]
    assert store.get_node(topo(world), "web1")["values"]["os"] == "Linux 7.0"


def test_a_fact_that_already_holds_the_proposed_value_does_not_void_the_rest(world):
    seed(world)
    row = proposals.propose(
        ctx(world),
        items=[{"node": "web1", "fields": {"os": "Linux 6.9", "role": "web server"}}],
        evidence="os-release reports Linux 6.9",
    )["proposal"]
    # Another observer wrote one of the two proposed values before the owner answered.
    seed(world, [{"node": "web1", "fields": {"os": "Linux 6.9"}}])
    answer(world, row, "Approve")
    closed = proposals.get(ctx(world), row["id"])
    assert closed["state"] == "applied"
    assert store.get_node(topo(world), "web1")["values"]["role"] == "web server"


def test_a_missed_event_is_settled_on_the_next_read(world):
    seed(world)
    row = os_change(world)
    events.remove_listener(world.gateway._topology_decided)
    answer(world, row, "Approve")
    assert store.get_node(topo(world), "web1")["values"]["os"] == "Linux 6.8"
    assert proposals.listing(ctx(world), "applied")[0]["id"] == row["id"]
    assert store.get_node(topo(world), "web1")["values"]["os"] == "Linux 6.9"


# ── The secret filter applies to proposals ────────────────────────────────
@pytest.mark.parametrize(
    "items, evidence",
    [
        ([WEB1], "the password is hunter2-swordfish"),
        ([{"node": "web1", "fields": {"kind": "server", "password": "x"}}], EVIDENCE),
        (
            [{"node": "web1", "fields": {"kind": "server", "role": "token = abcdef123456"}}],
            EVIDENCE,
        ),
        ([WEB1], "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\n-----END"),
    ],
)
def test_secret_looking_proposals_are_refused_and_store_nothing(world, items, evidence):
    before = events.latest_seq(world.db, world.workspace)
    for principal in (world.principal, world.person):
        with pytest.raises(SecretRefused) as refusal:
            proposals.propose(ctx(world, principal), items=items, evidence=evidence)
        assert "hunter2" not in str(refusal.value) and "abcdef" not in str(refusal.value)
    assert not topo(world).path.exists()
    assert events.latest_seq(world.db, world.workspace) == before
    response = call(world, "topology_propose", items=items, evidence=evidence)
    assert response["result"]["isError"] and "never keys, tokens" in text(response)
    assert not [c for c in board(world).task_list(board="work")]


def test_invalid_requests_are_refused_before_anything_is_stored(world):
    seed(world)
    before = proposals.listing(ctx(world))
    for args in (
        {"items": [{"node": "db1", "fields": {"os": "Linux"}}]},  # no kind on a first write
        {"items": [WEB1, WEB1]},
        {"items": []},
        {},
        {"remove": ["node:nowhere"]},
        {"remove": [{"subject": "node:web1", "field": "kind"}]},
        {"remove": ["node:web1"], "items": [{"node": "web1", "fields": {"os": "x"}}]},
        {"items": [WEB1], "ttl_seconds": 5},
        {
            "items": [
                {"node": f"n{i}", "fields": {"kind": "server", "os": "Linux", "role": "worker"}}
                for i in range(12)
            ]
        },
    ):
        with pytest.raises((TopologyError, ToolError)):
            proposals.propose(ctx(world), evidence=EVIDENCE, **args)
    assert proposals.listing(ctx(world)) == before
    assert not [c for c in board(world).task_list(board="work")]
    unchanged = proposals.propose(ctx(world), items=[WEB1], evidence=EVIDENCE)
    assert unchanged == {"applied": None, "proposal": None, "unchanged": 2, "observed": 0}


# ── Undo ──────────────────────────────────────────────────────────────────
def test_owner_undoes_an_applied_change(world):
    seed(world)
    row = os_change(world)
    answer(world, row, "Approve")
    with pytest.raises(ToolError, match="only the signed-in workspace owner"):
        proposals.undo(ctx(world), row["id"])
    undone = proposals.undo(ctx(world, world.person), row["id"])
    assert undone["state"] == "undone" and undone["skipped"] == []
    assert store.get_node(topo(world), "web1")["values"]["os"] == "Linux 6.8"
    states = [h["state"] for h in store.history(topo(world), "node:web1", predicate="os")]
    assert states == ["current", "superseded", "superseded"]
    with pytest.raises(ToolError, match="is undone, not applied"):
        proposals.undo(ctx(world, world.person), row["id"])
    assert world.db.audit_entries()[0]["action"] == "topology.undo"


def test_undo_of_a_wrong_discovery_takes_it_off_the_map(world, http):
    machine_id, worker = runner(world)
    applied = proposals.propose(
        ctx(world, worker),
        items=[
            {"node": "runner-one", "fields": {"kind": "server", "runner_id": machine_id}},
            {"link": ["runner-one", "hub", "relay"]},
        ],
        evidence=EVIDENCE,
    )["applied"]
    origin = {"Origin": f"http://127.0.0.1:{http.port}"}
    path = f"/api/topology/proposals/{applied['id']}/undo"
    assert http("POST", path, {})[0] == 403
    status, body, _ = http("POST", path, {}, principal=world.person, headers=origin)
    assert status == 200 and body["state"] == "undone"
    assert store.read_graph(topo(world))["nodes"] == []
    assert store.read_graph(topo(world))["links"] == []
    assert len(store.history(topo(world), "node:runner-one")) == 2


def test_undo_restores_a_removed_subject_and_skips_what_changed_since(world):
    seed(world)
    removed = proposals.propose(
        ctx(world, world.person), remove=["node:web1"], evidence="Decommissioned."
    )["applied"]
    assert store.get_node(topo(world), "web1") is None
    proposals.undo(ctx(world, world.person), removed["id"])
    assert store.get_node(topo(world), "web1")["values"] == {"kind": "server", "os": "Linux 6.8"}

    first = seed(world, [{"node": "web1", "fields": {"os": "Linux 6.9", "role": "web"}}])["applied"]
    seed(world, [{"node": "web1", "fields": {"os": "Linux 7.0"}}])
    undone = proposals.undo(ctx(world, world.person), first["id"])
    assert undone["skipped"] == [{"subject": "node:web1", "predicate": "os"}]
    assert store.get_node(topo(world), "web1")["values"] == {"kind": "server", "os": "Linux 7.0"}


# ── Access and transport ──────────────────────────────────────────────────
def test_wing_grant_gates_the_tool_and_the_routes(world, http):
    token, outsider = world.agent("outsider", read_wings=["shared"], write_wings=["shared"])
    listed = world.gateway.mediator.handle(
        outsider, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert "topology_propose" not in {t["name"] for t in listed["result"]["tools"]}
    with pytest.raises(ToolError, match="cannot write the topology wing"):
        proposals.propose(ctx(world, outsider), items=[WEB1], evidence=EVIDENCE)
    body = {"items": [WEB1], "evidence": EVIDENCE}
    assert http("POST", "/api/topology/proposals", body, token=token)[0] == 403
    assert http("GET", "/api/topology/proposals", token=token)[0] == 403
    assert http("GET", "/api/topology/proposals", anonymous=True)[0] == 401
    slim_token, slim = world.agent("slim", profile="slim")
    assert not slim.policy.tool_visible("topology_propose")
    assert http("POST", "/api/topology/proposals", body, token=slim_token)[0] == 403
    assert not topo(world).path.exists()


def test_rest_propose_list_and_errors(world, http):
    status, body, _ = http(
        "POST", "/api/topology/proposals", {"items": [WEB1], "evidence": EVIDENCE}
    )
    assert status == 201 and body["proposal"]["state"] == "pending"
    proposal_id = body["proposal"]["id"]
    status, listed, _ = http("GET", "/api/topology/proposals?state=pending")
    assert status == 200 and [p["id"] for p in listed["proposals"]] == [proposal_id]
    assert http("GET", "/api/topology/proposals?state=nope")[0] == 403
    assert http("GET", "/api/topology/proposals/tp_missing")[0] == 404
    assert http("POST", "/api/topology/proposals/tp_missing/undo", {})[0] == 403
    assert http("DELETE", "/api/topology/proposals/" + proposal_id)[0] == 404
    status, body, _ = http(
        "POST", "/api/topology/proposals", {"items": [WEB1], "evidence": "api_key = sk-12345678"}
    )
    assert status == 400 and "never keys, tokens" in body["error"]
    assert http("POST", "/api/topology/proposals", {"evidence": EVIDENCE, "nope": 1})[0] == 400
    assert http("POST", "/api/topology/proposals", {"evidence": EVIDENCE})[0] == 400


def test_mcp_reply_names_the_decision(world):
    response = call(world, "topology_propose", items=[WEB1], evidence=EVIDENCE)
    assert not response["result"].get("isError"), response
    first, *_ = text(response).splitlines()
    assert first.startswith("proposal tp_") and "with 2 facts waits for the owner: #1 " in first
    assert call(world, "topology_propose", evidence=EVIDENCE, nope=1)["result"]["isError"]


def test_listener_leaves_with_its_hub(world):
    listener = world.gateway._topology_decided
    assert listener in events._listeners
    world.db.close()
    assert listener not in events._listeners


def test_old_workspace_reads_empty_and_creates_nothing(world):
    assert proposals.listing(ctx(world)) == []
    proposals.settle(world.gateway, world.workspace)
    with pytest.raises(ToolError, match="proposal not found"):
        proposals.get(ctx(world), "tp_0000000000000000")
    assert not topo(world).path.exists()
    # A topology file from before proposals has no proposals table yet.
    store.upsert_node(
        topo(world),
        "web1",
        {"kind": "server"},
        observer=store.Observer("owner", world.user),
        evidence=EVIDENCE,
    )
    assert proposals.listing(ctx(world)) == []
