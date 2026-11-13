"""Map discovery: one button starts a read-only collector on every runner machine.

Two fake runner machines run through the real gateway: the start goes through the normal
card-start path, each machine's agent runs the product's collector (on invented system
facts) and hands the result to ``topology_propose``. Nothing here is a real host.
"""

import ast
import json
import re
from pathlib import Path

import pytest

from commonplace import sessions
from commonplace.boards import BoardService
from commonplace.extensions import ToolContext
from commonplace.runner import jobs, registry
from commonplace.topology import collector, discovery, model, node_agents, proposals, store
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.runner_harness import http

PACKAGE = Path(collector.__file__).parent

SSH_CONFIG = """\
Host *
    ServerAliveInterval 30

Host web1
    HostName 192.0.2.10
    User deploy
    IdentityFile ~/.ssh/id_ed25519
    Port 2222

Host inner
    HostName 10.0.0.7
    ProxyJump web1
"""
TAILSCALE = json.dumps(
    {
        "Self": {"HostName": "alpha", "DNSName": "alpha.tail-example.ts.net.", "OS": "linux"},
        "Peer": {
            "nodekey:1": {
                "HostName": "beta",
                "DNSName": "beta.tail-example.ts.net.",
                "OS": "macOS",
                "Online": True,
            },
            "nodekey:2": {
                "HostName": "phone",
                "DNSName": "phone.tail-example.ts.net.",
                "OS": "iOS",
                "Online": False,
            },
        },
    }
)


class FakeProbe(collector.Probe):
    """A machine made of dictionaries; every access is recorded."""

    def __init__(self, *, system, node, files=None, commands=None, up=(), release="6.8.0", cores=8):
        self.system, self.node, self.release, self.machine = system, node, release, "x86_64"
        self.files, self.commands, self.up = files or {}, commands or {}, set(up)
        self.cores, self.reads, self.runs, self.reached = cores, [], [], []

    def _read(self, path):
        self.reads.append(path)
        return self.files.get(path)

    def _run(self, argv):
        self.runs.append(argv)
        return self.commands.get(argv)

    def reach(self, host, port):
        self.reached.append((host, port))
        return host in self.up

    def cpu_count(self):
        return self.cores

    def disk_gb(self):
        return 500.0


def alpha(**changes):
    files = {
        "/etc/os-release": 'NAME="Debian GNU/Linux"\nVERSION_ID="12"\nPRETTY_NAME="Debian GNU/Linux 12"\n',
        "/proc/cpuinfo": "processor\t: 0\nmodel name\t: Example CPU 3000\n",
        "/proc/meminfo": "MemTotal:       32768000 kB\nMemFree: 1 kB\n",
        "/sys/class/dmi/id/product_name": "Example Tower\n",
        "/sys/class/dmi/id/sys_vendor": "Example Corp\n",
        "~/.ssh/config": SSH_CONFIG,
    }
    base = dict(
        system="Linux",
        node="alpha.example.test",
        files=files,
        commands={("tailscale", "status", "--json"): TAILSCALE},
        up={"192.0.2.10", "beta.tail-example.ts.net", "hub.example.test"},
    )
    base.update(changes)
    return FakeProbe(**base)


def beta(os_name="14.5"):
    return FakeProbe(
        system="Darwin",
        node="beta.local",
        files={},
        commands={
            ("sysctl", "-n", "hw.model"): "Mac14,2\n",
            ("sysctl", "-n", "hw.memsize"): str(16 * 1024**3) + "\n",
            ("sysctl", "-n", "machdep.cpu.brand_string"): "Example Silicon M9\n",
        },
        release=os_name,
        cores=10,
        up={"hub.example.test"},
    )


HUB_URL = "https://hub.example.test"


class World:
    """One workspace with two live runner machines allowed to work in the topology wing."""

    def __init__(self, hub):
        self.hub, self.gw, self.db, self.ws = hub, hub.gateway, hub.db, hub.workspace
        self.owner = sessions.person_principal(self.db, hub.admin, self.ws)
        self.machines = {name: self.machine(name) for name in ("alpha-runner", "beta-runner")}

    def ctx(self, principal=None):
        return ToolContext(principal=principal or self.owner, gateway=self.gw)

    def machine(self, label, wings=("shared", "topology"), live=True):
        enrolled = registry.enroll(
            self.db,
            self.owner,
            {"label": label, "projects": ["demo"], "topics": ["worker"], "wings": list(wings)},
        )
        registry.attest(
            self.db,
            self.owner,
            enrolled["machine_id"],
            {
                "topics": ["worker"],
                "provider_instance_id": "worker",
                "worker_connection_id": enrolled["worker_connection_id"],
            },
        )
        if live:
            self.beat(enrolled["machine_id"])
        return enrolled["machine_id"]

    def beat(self, machine_id):
        machine = registry.get_machine(self.db, self.ws, machine_id)
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE connections SET last_used_at=? WHERE id=?",
                (model.stamp(model.clock(None)), machine["machine_connection_id"]),
            )
            self.db._conn.commit()

    def worker(self, machine_id):
        return registry.worker_principal(
            self.db, registry.get_machine(self.db, self.ws, machine_id)
        )

    def start(self):
        return discovery.start(self.ctx(), hub_url=HUB_URL)

    def run_agent(self, label, probe, **changes):
        """What the started agent does: run the collector, pass its result on."""
        out = collector.collect(probe, runner_id=self.machines[label], hub_url=HUB_URL)
        ctx = self.ctx(self.worker(self.machines[label]))
        own = proposals.propose(ctx, **out["propose"])
        peers = out["propose_peers"] and proposals.propose(ctx, **out["propose_peers"])
        return out, own, peers

    def topo(self):
        return store.open_topology(self.gw, self.ws)

    def approve_all(self):
        service = BoardService(self.ctx())
        for row in proposals.listing(self.ctx(), "pending"):
            card = service.task_show(row["card"])
            service.decide(row["card"], choice="Approve", version=card["version"])
        proposals.settle(self.gw, self.ws)


@pytest.fixture
def world(hub):
    return World(hub)


def outcomes(result):
    return {m["label"]: m["outcome"] for m in result["machines"]}


# ── The button starts one normal run per machine ─────────────────────────
def test_start_creates_one_card_and_one_normal_start_per_machine(world):
    result = world.start()
    assert outcomes(result) == {"alpha-runner": "started", "beta-runner": "started"}
    store_ = jobs.open_store(world.gw, world.ws)
    service = BoardService(world.ctx())
    for entry in result["machines"]:
        runs = jobs.status(store_, entry["card"], world.owner)
        assert [(r["job"], r["state"]) for r in runs] == [(entry["job"], "queued")]
        card = service.task_show(entry["card"])
        assert card["status"] == "todo" and card["board"] == discovery.BOARD
        assert card["body"].startswith(discovery.PROMPT.split("{")[0])
        assert f"--runner-id {entry['machine']}" in card["body"]
        assert f"--hub-url {HUB_URL}" in card["body"]
    assert len({m["card"] for m in result["machines"]}) == 2


def test_the_run_is_visible_on_the_machine_with_card_and_log(world, hub):
    result = world.start()
    machine = world.machines["alpha-runner"]
    world.run_agent("alpha-runner", alpha())
    panel = node_agents.read(world.gw, world.owner, machine, now=model.clock(None))
    [run] = panel["runs"]
    entry = next(m for m in result["machines"] if m["machine"] == machine)
    assert run["job"] == entry["job"] and run["card"] == entry["card"]
    assert run["state"] == "queued" and "log_ref" in run


def test_a_second_press_while_a_run_is_active_starts_nothing_new(world):
    first = world.start()
    again = world.start()
    assert outcomes(again) == {"alpha-runner": "running", "beta-runner": "running"}
    assert [m["job"] for m in again["machines"]] == [m["job"] for m in first["machines"]]
    store_ = jobs.open_store(world.gw, world.ws)
    with store_.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runner_jobs").fetchone()[0] == 2


# ── Two fake runners produce the expected map without manual entry ────────
def test_two_runners_build_the_expected_nodes_and_links(world):
    world.start()
    _, own_a, peers_a = world.run_agent("alpha-runner", alpha())
    # Its own facts and links apply at once; the machines it only saw wait for the owner.
    assert own_a["applied"] and own_a["proposal"] is None
    assert peers_a["applied"] is None and peers_a["proposal"]["state"] == "pending"
    now = model.clock(None)
    assert {n["key"] for n in store.read_graph(world.topo(), now=now)["nodes"]} == {"alpha"}
    keys = {c["subject"] for c in peers_a["proposal"]["changes"]}
    assert {"node:hub", "node:phone", "node:web1", "node:inner", "node:beta"} <= keys
    _, own_b, peers_b = world.run_agent("beta-runner", beta())
    assert own_b["applied"] and own_b["proposal"] is None
    assert {n["key"] for n in store.read_graph(world.topo(), now=now)["nodes"]} == {"alpha", "beta"}
    # Both machines proposed the hub and saw each other; the owner's answers settle it all.
    world.approve_all()
    graph = store.read_graph(world.topo(), now=now)
    nodes = {n["key"]: n["values"] for n in graph["nodes"]}
    assert set(nodes) == {"alpha", "beta", "hub", "phone", "web1", "inner"}
    assert nodes["alpha"]["kind"] == "server"
    assert nodes["alpha"]["runner_id"] == world.machines["alpha-runner"]
    assert nodes["alpha"]["os"] == "Debian GNU/Linux 12"
    assert nodes["alpha"]["hardware"] == "Example Corp Example Tower"
    assert nodes["alpha"]["resources"] == {
        "cpu": "Example CPU 3000",
        "cores": 8,
        "memory_gb": 31.25,
        "storage_gb": 500,
    }
    assert nodes["alpha"]["tailscale_name"] == "alpha.tail-example.ts.net"
    assert nodes["beta"]["kind"] == "mac" and nodes["beta"]["hardware"] == "Mac14,2"
    assert nodes["beta"]["runner_id"] == world.machines["beta-runner"]
    assert nodes["beta"]["resources"]["memory_gb"] == 16
    assert nodes["phone"]["kind"] == "phone" and nodes["hub"]["kind"] == "server"
    links = {link["subject"].removeprefix("link:"): link["values"] for link in graph["links"]}
    assert links["alpha>beta:tailscale"] == {
        "method": "tailscale",
        "status": "up",
        "alias": "beta.tail-example.ts.net",
    }
    assert links["alpha>phone:tailscale"]["status"] == "down"
    assert links["alpha>web1:direct"]["port"] == 2222
    assert links["alpha>web1:direct"]["status"] == "up"
    assert links["alpha>inner:jump_host"]["via"] == "web1"
    assert links["alpha>hub:direct"]["status"] == "up"
    assert links["beta>hub:direct"]["status"] == "up"


def test_a_rerun_refreshes_and_proposes_only_real_changes(world):
    world.start()
    world.run_agent("alpha-runner", alpha())
    world.run_agent("beta-runner", beta())
    world.approve_all()

    def seen():
        return store.get_node(world.topo(), "beta")["fields"]["os"]["provenance"][0]["seen_count"]

    before = seen()
    _, own, peers = world.run_agent("beta-runner", beta())
    assert own["applied"] is None and own["proposal"] is None and own["unchanged"] > 0
    assert peers["applied"] is None and peers["proposal"] is None
    assert seen() == before + 1
    # One real change (an OS upgrade) is the only thing asked.
    _, changed, _ = world.run_agent("beta-runner", beta(os_name="15.0"))
    [change] = changed["proposal"]["changes"]
    assert (change["subject"], change["predicate"]) == ("node:beta", "os")
    assert change["before"] == "macOS 14.5" and change["after"] == "macOS 15.0"
    assert changed["applied"] is None


def test_an_unreachable_peer_and_hub_are_reported_not_hidden(world):
    probe = alpha(up={"beta.tail-example.ts.net"})
    out = collector.collect(probe, runner_id=world.machines["alpha-runner"], hub_url=HUB_URL)
    assert {u["target"] for u in out["unreachable"]} == {"hub", "web1"}
    assert all(u["reason"] for u in out["unreachable"])
    world.run_agent("alpha-runner", probe)
    links = {
        item["subject"]: item["values"]
        for item in store.read_graph(world.topo(), now=model.clock(None))["links"]
    }
    assert links["link:alpha>hub:direct"]["status"] == "down"
    assert links["link:alpha>web1:direct"]["status"] == "down"
    assert "unreachable: hub" in out["summary"] and "unreachable: web1" in out["summary"]


def test_a_machine_that_cannot_be_reached_is_reported_with_its_reason(world):
    gone = world.machine("gone-runner", live=False)
    noperm = world.machine("limited-runner", wings=("shared",))
    result = world.start()
    by = {m["label"]: m for m in result["machines"]}
    assert (
        by["gone-runner"]["outcome"] == "unreachable"
        and by["gone-runner"]["reason"] == "never_reported"
    )
    assert (
        by["limited-runner"]["outcome"] == "refused"
        and by["limited-runner"]["reason"] == "no_topology_wing"
    )
    assert by["gone-runner"]["job"] is None and by["alpha-runner"]["outcome"] == "started"
    with jobs.open_store(world.gw, world.ws).connection() as conn:
        machines = {r["machine_id"] for r in conn.execute("SELECT machine_id FROM runner_jobs")}
    assert gone not in machines and noperm not in machines
    latest = discovery.latest(world.ctx())
    assert {m["label"]: m["outcome"] for m in latest["machines"]}["gone-runner"] == "unreachable"


def test_no_runner_machine_is_an_answer_not_an_error(hub):
    empty = World.__new__(World)
    empty.hub, empty.gw, empty.db, empty.ws = hub, hub.gateway, hub.db, hub.workspace
    empty.owner = sessions.person_principal(empty.db, hub.admin, empty.ws)
    result = discovery.start(empty.ctx(), hub_url=HUB_URL)
    assert result["machines"] == [] and result["note"] == "no_runner_machines"


# ── The route ────────────────────────────────────────────────────────────
def test_the_button_route_is_the_owner_s_and_returns_the_outcomes(world, hub):
    link = sessions.create_login_link(world.db, "owner@example.com", world.ws)
    cookie = f"{sessions.COOKIE}={sessions.redeem_login_link(world.db, link)}"
    status, body = http(hub.port, "POST", "/api/topology/discovery", cookie=cookie, body={})
    assert status == 200, body
    assert {m["outcome"] for m in body["machines"]} == {"started"}
    status, latest = http(hub.port, "GET", "/api/topology/discovery", cookie=cookie)
    assert status == 200 and [m["job"] for m in latest["machines"]] == [
        m["job"] for m in body["machines"]
    ]
    member = world.db.create_user("member@example.com")
    world.db.add_member(world.ws, member, "member", actor=hub.admin)
    link = sessions.create_login_link(world.db, "member@example.com", world.ws)
    other = f"{sessions.COOKIE}={sessions.redeem_login_link(world.db, link)}"
    status, body = http(hub.port, "POST", "/api/topology/discovery", cookie=other, body={})
    assert status == 403, body
    status, _ = http(hub.port, "POST", "/api/topology/discovery", body={})
    assert status == 401


# ── Read-only, and no secrets ────────────────────────────────────────────
WRITE_WORDS = re.compile(
    r"\b(sudo|rm|mv|cp|chmod|chown|tee|dd|mkfs|kill|systemctl|service|apt|apt-get|yum|brew|pip|"
    r"npm|curl|wget|scp|rsync|ssh-keygen|ssh-add|ssh-copy-id|crontab|useradd|passwd|reboot|"
    r"shutdown|iptables|ufw|tailscale up|tailscale set|tailscale down|sed -i)\b"
)
SECRET_WORDS = re.compile(
    r"id_(rsa|ed25519|ecdsa|dsa)|\.pem|\.key\b|shadow|\.netrc|\.env\b|credentials|authorized_keys|"
    r"known_hosts|\.aws|\.gnupg|keychain|\.git-credentials|\.npmrc|\.pgpass|token"
)


def test_the_collector_has_no_way_to_write_or_to_change_anything():
    source = (PACKAGE / "collector.py").read_text()
    tree = ast.parse(source)
    imported = {
        a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    } | {
        n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    }
    assert not imported & {"shutil", "tempfile", "requests", "urllib", "http", "ftplib", "smtplib"}
    called = {
        n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    assert not called & {
        "remove",
        "unlink",
        "rmdir",
        "rename",
        "chmod",
        "chown",
        "mkdir",
        "makedirs",
        "write",
        "write_text",
        "write_bytes",
        "truncate",
        "popen",
        "kill",
        "setenv",
        "putenv",
        "symlink",
        "touch",
        "sendall",
        "send",
        "bind",
        "listen",
    }, called
    assert "os.system" not in source and "os.replace" not in source
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open":
            mode = node.args[1] if len(node.args) > 1 else None
            assert mode is None or (isinstance(mode, ast.Constant) and set(mode.value) <= set("rb"))
    # The only process it can start is a fixed read-only command.
    assert collector.ALLOWED_COMMANDS and all(
        not WRITE_WORDS.search(" ".join(argv)) for argv in collector.ALLOWED_COMMANDS
    )
    assert {argv[0] for argv in collector.ALLOWED_COMMANDS} <= {"tailscale", "sysctl", "nvidia-smi"}
    assert not any(SECRET_WORDS.search(path) for path in collector.ALLOWED_READS)


def test_the_default_probe_refuses_anything_outside_its_lists():
    probe = collector.Probe()
    for path in ("~/.ssh/id_ed25519", "/etc/shadow", "~/.aws/credentials", "/etc/passwd"):
        with pytest.raises(collector.NotAllowed):
            probe.read(path)
    for argv in (
        ("rm", "-rf", "/"),
        ("tailscale", "up"),
        ("sh", "-c", "id"),
        ("sysctl", "-w", "a=1"),
    ):
        with pytest.raises(collector.NotAllowed):
            probe.run(argv)


def test_a_collection_reads_only_listed_paths_and_never_a_key_file():
    probe = alpha()
    collector.collect(probe, runner_id="mach_0000000000000001", hub_url=HUB_URL)
    assert probe.reads and set(probe.reads) <= set(collector.ALLOWED_READS)
    assert set(probe.runs) <= set(collector.ALLOWED_COMMANDS)
    assert not any(SECRET_WORDS.search(p) for p in probe.reads)
    # Network: only the hub and the hosts the user's own ssh and Tailscale setup names.
    assert set(probe.reached) <= {("hub.example.test", 443), ("192.0.2.10", 2222)}


def test_a_key_file_is_named_at_most_by_alias_and_nothing_secret_is_emitted():
    probe = alpha()
    probe.files["~/.ssh/config"] += "    Password hunter2\n    IdentityFile ~/.ssh/work_key.pem\n"
    out = collector.collect(probe, runner_id="mach_0000000000000001", hub_url=HUB_URL)
    blob = json.dumps(out)
    assert "IdentityFile" not in blob and "id_ed25519" not in blob and "hunter2" not in blob
    assert ".pem" not in blob
    # The store's secret filter accepts the whole output as is.
    model.facts_for(out["propose"]["items"])
    model.evidence_text(out["propose"]["evidence"])


def test_the_prompt_asks_for_read_only_work_and_names_no_write_or_secret_command():
    body = discovery.card_body("mach_0000000000000001", HUB_URL)
    for forbidden in (WRITE_WORDS, SECRET_WORDS):
        assert not forbidden.search(body), forbidden.search(body)
    assert "read-only" in body and "topology_propose" in body
    assert "python3 -m commonplace.topology.collector" in body
    assert "mach_0000000000000001" in body and HUB_URL in body
    # Nothing about any one setup is built in.
    for word in ("jeffrey", "simrig", "edge1", "tailscale.net"):
        assert word not in (body + (PACKAGE / "collector.py").read_text()).lower()
