"""GET /api/topology/node over real HTTP: provenance and freshness of a node, the agents of a
runner machine, and what each caller's grants let them see. Names are invented."""

# Fixtures are imported by name and used as arguments, which ruff reads as a redefinition.
# ruff: noqa: F811

from datetime import datetime, timedelta, timezone

from commonplace.topology import Observer, node_panel, store
from tests.runner_harness import (  # noqa: F401
    attest,
    card,
    cli_token,
    enroll as harness_enroll,
    http,
    member,
    owners,
    signup_hub,
)

SEEN = Observer("agent", "conn_fixture")


def _path(key):
    return f"{node_panel.PATH}?key={key}"


def _seed(hub, owner, machine_id, **write):
    topo = store.open_topology(hub.gateway, owner["workspace"])
    store.upsert(
        topo,
        [
            {
                "node": "maple",
                "fields": {
                    "kind": "server",
                    "name": "Maple",
                    "role": "app host",
                    "os": "Linux 6.8",
                    "hardware": "8 cores",
                    "resources": {"cores": 8, "memory_gb": 32},
                    "runner_id": machine_id,
                    "aliases": ["maple-ssh"],
                    "tailscale_name": "maple.example.ts.net",
                    "wings": ["shared"],
                },
            },
            {"node": "birch", "fields": {"kind": "vm", "name": "Birch"}},
            {"node": "edge", "fields": {"kind": "router", "name": "Edge"}},
            {"node": "example.test", "fields": {"kind": "website"}},
            {"link": ["example.test", "edge", "proxy"], "fields": {"status": "up"}},
            {"link": ["edge", "maple", "reverse_proxy"]},
            {
                "route": "example.test",
                "hops": [
                    "example.test>edge:proxy",
                    "edge>maple:reverse_proxy",
                    "maple>birch:hosts",
                ],
            },
        ],
        observer=SEEN,
        evidence="uname -sr\nLinux 6.8\nsecond line stays behind",
        **write,
    )


def _enrolled(hub, owner):
    # The machine's wing must exist: create it through the ordinary memory write.
    status, body = http(
        hub.port,
        "POST",
        "/api/memory",
        cookie=owner["cookie"],
        body={"text": "Synthetic workspace example.", "theme": "shared", "topic": "demo"},
    )
    assert status == 201, (status, body)
    machine = harness_enroll(hub, owner)
    attest(hub, owner, machine)
    return machine


def _start(hub, owner, machine, board="shared"):
    number = card(hub, owner, board=board)["number"]
    status, body = http(
        hub.port,
        "POST",
        "/api/card-starts",
        cookie=owner["cookie"],
        body={
            "task": number,
            "machine": machine["machine_id"],
            "project": "demo",
            "topic": "worker",
        },
    )
    assert status == 201, (status, body)
    return number, body


def _read(hub, auth, key):
    kwargs = {"cookie": auth["cookie"]} if isinstance(auth, dict) else {"token": auth}
    return http(hub.port, "GET", _path(key), **kwargs)


def test_every_field_carries_its_source_and_freshness(signup_hub, owners):
    owner = owners["A"]
    machine = _enrolled(signup_hub, owner)
    _seed(signup_hub, owner, machine["machine_id"])
    status, body = _read(signup_hub, owner, "maple")
    assert status == 200, body
    node = body["node"]
    assert node["found"] and not node["stale"] and not node["conflicted"]
    assert list(node["fields"]) == [
        "kind",
        "name",
        "role",
        "aliases",
        "tailscale_name",
        "runner_id",
        "wings",
        "hardware",
        "os",
        "resources",
    ]
    os_field = node["fields"]["os"]
    assert os_field["value"] == "Linux 6.8" and os_field["stale"] is False
    assert os_field["observed_at"] and os_field["expires_at"] > os_field["observed_at"]
    assert os_field["sources"] == [
        {
            "kind": "agent",
            "id": "conn_fixture",
            "observed_at": os_field["observed_at"],
            "seen_count": 1,
            "evidence": "uname -sr",
        }
    ]
    assert node["fields"]["resources"]["value"] == {"cores": 8, "memory_gb": 32}
    assert body["as_of"] >= os_field["observed_at"]


def test_an_expired_field_is_marked_stale_and_still_says_when_it_was_seen(signup_hub, owners):
    owner = owners["A"]
    _seed(
        signup_hub,
        owner,
        "mach_0123456789abcdef",
        now=datetime.now(timezone.utc) - timedelta(days=40),
    )
    body = _read(signup_hub, owner, "maple")[1]
    node = body["node"]
    assert node["stale"] is True
    assert all(field["stale"] for field in node["fields"].values())
    assert node["fields"]["os"]["value"] == "Linux 6.8"
    assert node["fields"]["os"]["expires_at"] < body["as_of"]


def test_an_unrecorded_node_is_found_false_and_bad_input_is_refused(signup_hub, owners):
    owner = owners["A"]
    assert _read(signup_hub, owner, "nowhere")[1]["node"] == {"key": "nowhere", "found": False}
    assert not store.open_topology(signup_hub.gateway, owner["workspace"]).path.exists()
    assert _read(signup_hub, owner, "Not%20A%20Key")[0] == 400
    assert http(signup_hub.port, "GET", node_panel.PATH, cookie=owner["cookie"])[0] == 400
    assert (
        http(signup_hub.port, "GET", _path("maple") + "&extra=1", cookie=owner["cookie"])[0] == 400
    )
    assert http(signup_hub.port, "GET", _path("maple"))[0] == 401


def test_agents_of_a_runner_machine_list_its_runs_with_their_card(signup_hub, owners):
    owner = owners["A"]
    machine = _enrolled(signup_hub, owner)
    _seed(signup_hub, owner, machine["machine_id"])
    number, _ = _start(signup_hub, owner, machine)
    body = _read(signup_hub, owner, "maple")[1]
    agents = body["agents"]
    assert agents["applicable"] is True
    assert agents["machine"]["id"] == machine["machine_id"] and agents["machine"]["enabled"]
    assert [item["kind"] for item in agents["connections"]] == ["machine", "worker"]
    [run] = agents["runs"]
    assert (run["board"], run["card"], run["state"]) == ("shared", number, "queued")
    assert agents["coordinators"] == []


def test_a_node_without_agents_and_a_node_that_is_no_runner_say_so(signup_hub, owners):
    owner = owners["A"]
    machine = _enrolled(signup_hub, owner)
    _seed(signup_hub, owner, machine["machine_id"])
    idle = _read(signup_hub, owner, "maple")[1]["agents"]
    assert idle["applicable"] is True and idle["runs"] == [] and idle["coordinators"] == []
    assert _read(signup_hub, owner, "birch")[1]["agents"] == {
        "applicable": False,
        "reason": "not_a_runner",
    }
    # A runner id nobody enrolled in this workspace is not shown as a machine.
    _seed(signup_hub, owner, "mach_ffffffffffffffff", replace=True)
    assert _read(signup_hub, owner, "maple")[1]["agents"] == {
        "applicable": False,
        "reason": "machine_not_found",
    }


def test_grants_decide_what_a_caller_sees(signup_hub, owners):
    owner = owners["A"]
    machine = _enrolled(signup_hub, owner)
    _seed(signup_hub, owner, machine["machine_id"])
    _start(signup_hub, owner, machine)
    # No topology wing grant: refused, and nothing of the node leaks.
    _, blind = cli_token(owner, "blind-reader", read_wings="shared")
    status, body = _read(signup_hub, blind, "maple")
    assert status == 403 and "maple" not in str(body)
    # Topology grant without the board's wing: the node, but no run and no connections.
    _, narrow = cli_token(owner, "topology-only", read_wings="topology")
    status, body = _read(signup_hub, narrow, "maple")
    assert status == 200 and body["node"]["found"]
    assert body["agents"]["applicable"] is True
    assert body["agents"]["runs"] == [] and body["agents"]["connections"] is None
    # Both wings: the run shows, still without the owner-only connections.
    _, wide = cli_token(owner, "topology-and-shared", read_wings="topology,shared")
    body = _read(signup_hub, wide, "maple")[1]
    assert len(body["agents"]["runs"]) == 1 and body["agents"]["connections"] is None


def test_a_member_session_reads_like_the_wing_rules_but_never_sees_connections(signup_hub, owners):
    owner = owners["A"]
    machine = _enrolled(signup_hub, owner)
    _seed(signup_hub, owner, machine["machine_id"])
    _start(signup_hub, owner, machine)
    peer = member(signup_hub, owner, owners["B"])
    status, body = _read(signup_hub, peer, "maple")
    assert status == 200 and body["node"]["found"]
    assert body["agents"]["connections"] is None
    assert [run["board"] for run in body["agents"]["runs"]] == ["shared"]


def test_another_workspace_sees_nothing_and_no_secret_is_in_the_answer(signup_hub, owners):
    owner = owners["A"]
    machine = _enrolled(signup_hub, owner)
    _seed(signup_hub, owner, machine["machine_id"])
    _start(signup_hub, owner, machine)
    status, body = http(signup_hub.port, "GET", _path("maple"), cookie=owners["B"]["cookie"])
    assert status == 200 and body["node"]["found"] is False
    status, mine = http(signup_hub.port, "GET", _path("maple"), cookie=owner["cookie"])
    text = str(mine)
    for secret in ("machine_token", "worker_token", machine["machine_token"]):
        assert secret not in text


def test_sites_say_how_each_node_takes_part_and_how_fresh_the_route_is(signup_hub, owners):
    owner = owners["A"]
    machine = _enrolled(signup_hub, owner)
    _seed(signup_hub, owner, machine["machine_id"])
    roles = {}
    for key in ("example.test", "edge", "maple", "birch"):
        [site] = _read(signup_hub, owner, key)[1]["sites"]
        assert site["site"] == "example.test" and site["hops"] == 3 and not site["stale"]
        assert site["observed_at"] and site["expires_at"] > site["observed_at"]
        assert site["sources"][0]["id"] == "conn_fixture"
        roles[key] = (site["role"], [at["index"] for at in site["at"]])
    assert roles == {
        "example.test": ("site", [0]),
        "edge": ("through", [0, 1]),
        "maple": ("hosted", [1, 2]),
        "birch": ("hosted", [2]),
    }
    [site] = _read(signup_hub, owner, "edge")[1]["sites"]
    first, second = site["at"]
    # A hop with facts says when it was seen; one without is plainly unknown, never invented.
    assert first["known"] and first["status"] == "up" and first["status_observed_at"]
    assert second["known"] is True and second["status"] == "unknown"
    assert _read(signup_hub, owner, "nowhere")[1]["sites"] == []


def test_a_route_hop_without_facts_is_reported_unknown(signup_hub, owners):
    owner = owners["A"]
    topo = store.open_topology(signup_hub.gateway, owner["workspace"])
    store.upsert(
        topo,
        [
            {"node": "shop.example", "fields": {"kind": "website"}},
            {"route": "shop.example", "hops": ["shop.example>vault:proxy"]},
        ],
        observer=SEEN,
        evidence="a route written before its link",
    )
    [site] = _read(signup_hub, owner, "shop.example")[1]["sites"]
    [hop] = site["at"]
    assert hop["known"] is False and hop["status"] == "unknown" and "observed_at" not in hop
