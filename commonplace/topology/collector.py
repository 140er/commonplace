"""The map discovery collector: what one machine can tell about itself, read-only.

An agent started by "Start agent work on map" runs ``python3 -m commonplace.topology.collector``
on its machine and hands the ``propose`` part of the output to ``topology_propose``. The
collector reads a short, fixed list of files and runs a short, fixed list of commands, opens
connections only to the hub and to the hosts the user's own ssh and Tailscale setup names,
and writes nothing, anywhere. It never reads a key file or a credential: a key file is at
most an alias the user's ssh config already names, and even that is not emitted.

``Probe`` is the only door to the machine. Its lists (``ALLOWED_READS``,
``ALLOWED_COMMANDS``) are enforced in ``read`` and ``run``, so a test or a reviewer reads the
whole footprint here. Everything else is a pure function of what the probe returned, which
is why the same machine produces the same output (a rerun changes nothing unless the
machine changed).
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import platform
import re
import socket
import subprocess

# Files that may be read. Never a key, token, password or history file.
ALLOWED_READS = (
    "/etc/os-release",
    "/proc/cpuinfo",
    "/proc/meminfo",
    "/sys/class/dmi/id/product_name",
    "/sys/class/dmi/id/sys_vendor",
    "~/.ssh/config",
)
# Commands that may run, argument for argument. Each only prints facts.
ALLOWED_COMMANDS = (
    ("tailscale", "status", "--json"),
    ("sysctl", "-n", "hw.model"),
    ("sysctl", "-n", "hw.memsize"),
    ("sysctl", "-n", "machdep.cpu.brand_string"),
    ("nvidia-smi", "-L"),
)
DEFAULT_PEERS = 10
MAX_PEERS = 15  # keeps each topology_propose call inside its 32 items
READ_LIMIT = 262144
COMMAND_SECONDS = 5
CONNECT_SECONDS = 2

_VIRTUAL = re.compile(r"kvm|qemu|vmware|virtualbox|virtual machine|hvm|bochs|droplet", re.I)
_OS_KIND = {"macos": "mac", "darwin": "mac", "windows": "pc", "ios": "phone", "android": "phone"}


class NotAllowed(Exception):
    """A read or a command outside the collector's lists."""


class Probe:
    """The machine as the collector may see it."""

    def __init__(self):
        self.system = platform.system()
        self.release = (
            platform.mac_ver()[0] if self.system == "Darwin" else platform.release()
        ) or platform.release()
        self.machine = platform.machine()
        self.node = platform.node()

    def read(self, path):
        if path not in ALLOWED_READS:
            raise NotAllowed(f"not a listed file: {path}")
        return self._read(path)

    def run(self, argv):
        argv = tuple(argv)
        if argv not in ALLOWED_COMMANDS:
            raise NotAllowed(f"not a listed command: {' '.join(argv)}")
        return self._run(argv)

    def _read(self, path):
        try:
            with open(os.path.expanduser(path), "r", errors="replace") as handle:
                return handle.read(READ_LIMIT)
        except OSError:
            return None

    def _run(self, argv):
        try:
            done = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=COMMAND_SECONDS,
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None

    def reach(self, host, port):
        try:
            socket.create_connection((host, port), timeout=CONNECT_SECONDS).close()
        except OSError:
            return False
        return True

    def cpu_count(self):
        return os.cpu_count()

    def disk_gb(self):
        try:
            stat = os.statvfs("/")
        except (AttributeError, OSError):
            return None
        return stat.f_blocks * stat.f_frsize / 1024**3


# ── Small parsers ─────────────────────────────────────────────────────────
def key_of(name) -> str:
    """A node key from a host name: its first label, lowercase, key characters only."""
    label = re.sub(r"[^a-z0-9_-]+", "-", str(name or "").strip().split(".")[0].lower())
    label = label.strip("-_")[:63].strip("-_")
    return label or "machine"


def _line(text, limit=200):
    return " ".join(str(text).split())[:limit]


def _first(text, pattern):
    match = re.search(pattern, text or "", re.M)
    return match.group(1).strip() if match else None


def _hostname(value):
    value = str(value or "").strip().rstrip(".")
    return (
        value if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", value) else None
    )


def _number(value):
    return int(value) if value == int(value) else round(value, 2)


def ssh_hosts(config):
    """Concrete host aliases of an ssh config: ``{alias: {hostname, port, jump}}``.

    Only the three options that say how to reach a machine are kept; user names, key file
    names and everything else in the file are dropped here and never leave this function."""
    hosts, current = {}, []
    for raw in (config or "").splitlines():
        parts = re.sub(r"=", " ", raw.split("#", 1)[0].strip(), count=1).split(None, 1)
        if len(parts) != 2:
            continue
        option, value = parts[0].lower(), parts[1].strip()
        if option == "host":
            current = [a for a in value.split() if not re.search(r"[*?!]", a) and _hostname(a)]
            for alias in current:
                hosts.setdefault(alias, {})
        elif option in ("hostname", "port", "proxyjump"):
            for alias in current:
                hosts[alias].setdefault(option, value.split()[0] if value.split() else "")
    result = {}
    for alias, options in hosts.items():
        jump = options.get("proxyjump", "")
        jump = re.sub(r"^.*@", "", jump.split(",")[0]).rsplit(":", 1)[0] if jump else ""
        port = options.get("port", "")
        result[alias] = {
            "hostname": _hostname(options.get("hostname")) or alias,
            "port": int(port) if port.isdigit() and 0 < int(port) < 65536 else None,
            "jump": jump if jump and jump.lower() != "none" else None,
        }
    return result


def _hub(url):
    match = re.fullmatch(r"(https?)://([^/:?#@]+)(?::(\d+))?(?:[/?#].*)?", (url or "").strip())
    if not match:
        return None
    scheme, host, port = match.groups()
    return host, int(port) if port else (443 if scheme == "https" else 80)


def _method_to(host) -> str:
    """Tailscale when the address is in the Tailscale ranges, otherwise direct."""
    if host.lower().endswith(".ts.net"):
        return "tailscale"
    try:
        return (
            "tailscale"
            if ipaddress.ip_address(host) in ipaddress.ip_network("100.64.0.0/10")
            else "direct"
        )
    except ValueError:
        return "direct"


def _tailscale(text):
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        return {}, None
    self_name = (data.get("Self") or {}).get("DNSName")
    peers = {}
    for peer in (data.get("Peer") or {}).values():
        name = _hostname(peer.get("HostName"))
        if name:
            peers[key_of(name)] = {
                "name": name,
                "dns": _hostname(peer.get("DNSName")),
                "os": str(peer.get("OS") or ""),
                "online": bool(peer.get("Online")),
            }
    return peers, _hostname(self_name)


# ── The machine itself ────────────────────────────────────────────────────
def own_node(probe, runner_id, tailscale_name):
    system = probe.system
    if system == "Darwin":
        os_text, kind = f"macOS {probe.release}", "mac"
        hardware = (probe.run(("sysctl", "-n", "hw.model")) or "").strip()
        cpu = (probe.run(("sysctl", "-n", "machdep.cpu.brand_string")) or "").strip()
        mem = probe.run(("sysctl", "-n", "hw.memsize")) or ""
        memory = int(mem) / 1024**3 if mem.strip().isdigit() else None
    elif system == "Windows":
        os_text, kind, hardware, cpu, memory = f"Windows {probe.release}", "pc", "", "", None
    else:
        release = probe.read("/etc/os-release")
        os_text = _first(release, r'^PRETTY_NAME="?([^"\n]+)"?') or f"{system} {probe.release}"
        product = (probe.read("/sys/class/dmi/id/product_name") or "").strip()
        vendor = (probe.read("/sys/class/dmi/id/sys_vendor") or "").strip()
        kind = "vm" if _VIRTUAL.search(product + " " + vendor) else "server"
        hardware = " ".join(x for x in (vendor, product) if x)
        cpu = _first(probe.read("/proc/cpuinfo"), r"^model name\s*:\s*(.+)$") or ""
        kib = _first(probe.read("/proc/meminfo"), r"^MemTotal:\s*(\d+) kB")
        memory = int(kib) / 1024**2 if kib else None
    gpu = _first(probe.run(("nvidia-smi", "-L")), r"^GPU \d+: (.*?)(?: \(UUID.*)?$")
    resources = {}
    for name, value in (
        ("cpu", _line(cpu)),
        ("gpu", _line(gpu or "")),
        ("cores", probe.cpu_count()),
        ("memory_gb", None if memory is None else _number(memory)),
        ("storage_gb", None if probe.disk_gb() is None else _number(probe.disk_gb())),
    ):
        if value not in (None, ""):
            resources[name] = value
    key = key_of(probe.node)
    fields = {"kind": kind, "name": key, "runner_id": runner_id, "os": _line(os_text)}
    if hostname := _hostname(probe.node):
        fields["hostname"] = hostname
    if hardware:
        fields["hardware"] = _line(hardware or probe.machine)
    if resources:
        fields["resources"] = resources
    if tailscale_name:
        fields["tailscale_name"] = tailscale_name
    return key, fields


# ── Collect ───────────────────────────────────────────────────────────────
def collect(probe, runner_id, hub_url=None, max_peers=DEFAULT_PEERS) -> dict:
    """Everything this machine can say about itself and its ways to others.

    Returns ``propose`` (this machine and its own links; arguments of ``topology_propose``),
    ``propose_peers`` (the other machines it saw, which the owner decides on, or ``None``),
    ``unreachable`` (what was tried and did not answer, with the reason), ``notes`` and a
    one-line ``summary``. Two calls, so a peer that reports itself first cannot void the
    machine's own facts."""
    tailscale, own_dns = _tailscale(probe.run(("tailscale", "status", "--json")))
    me, own = own_node(probe, runner_id, own_dns)
    evidence = [
        f"host {me}: {own['os']} on {own.get('hardware', probe.machine)}",
        "resources: " + json.dumps(own.get("resources", {}), sort_keys=True),
    ]
    own_items, peer_items, unreachable, notes = [{"node": me, "fields": own}], [], [], []

    hub = _hub(hub_url)
    if hub and key_of("hub") != me:
        host, port = hub
        up = probe.reach(host, port)
        hub_node = {"kind": "server", "name": "Hub"}
        if _hostname(host):
            hub_node["hostname"] = _hostname(host)
        peer_items.append({"node": "hub", "fields": hub_node})
        own_items.append(
            {
                "link": [me, "hub", _method_to(host)],
                "fields": {"status": "up" if up else "down", "port": port},
            }
        )
        evidence.append(f"hub {host}:{port} {'reachable' if up else 'not reachable'}")
        if not up:
            unreachable.append({"target": "hub", "reason": f"no connection to {host}:{port}"})

    peers = {}
    for peer_key, peer in tailscale.items():
        if peer_key == me:
            continue
        fields = {"kind": _OS_KIND.get(peer["os"].lower(), "server"), "name": peer["name"]}
        if peer["dns"]:
            fields["tailscale_name"] = peer["dns"]
        link = {"status": "up" if peer["online"] else "down"}
        if peer["dns"]:
            link["alias"] = peer["dns"]
        peers[peer_key] = {"fields": fields, "links": [("tailscale", link)]}
        evidence.append(f"tailscale peer {peer_key}: {'online' if peer['online'] else 'offline'}")
    for alias, host in sorted(ssh_hosts(probe.read("~/.ssh/config")).items()):
        peer_key = key_of(alias)
        if peer_key == me:
            continue
        entry = peers.setdefault(peer_key, {"fields": {"kind": "server"}, "links": []})
        entry["fields"].setdefault("name", alias)
        entry["fields"]["aliases"] = [alias]
        if _hostname(host["hostname"]) and not entry["fields"].get("hostname"):
            entry["fields"]["hostname"] = _hostname(host["hostname"])
        link = {"alias": alias}
        if host["port"]:
            link["port"] = host["port"]
        if host["jump"]:
            link["via"] = key_of(host["jump"])
            entry["links"].append(("jump_host", link))
            evidence.append(f"ssh host {alias} via {host['jump']}")
            continue
        target = (host["hostname"], host["port"] or 22)
        up = probe.reach(*target)
        link["status"] = "up" if up else "down"
        entry["links"].append(("direct", link))
        evidence.append(
            f"ssh host {alias}: {target[0]}:{target[1]} {'reachable' if up else 'not reachable'}"
        )
        if not up:
            unreachable.append(
                {"target": peer_key, "reason": f"no connection to {target[0]}:{target[1]}"}
            )

    for index, peer_key in enumerate(sorted(peers)):
        entry = peers[peer_key]
        if index >= max(0, min(max_peers, MAX_PEERS)):
            notes.append(f"{peer_key} and later peers were left out (limit {max_peers})")
            break
        peer_items.append({"node": peer_key, "fields": entry["fields"]})
        own_items.extend(
            {"link": [me, peer_key, method], "fields": fields} for method, fields in entry["links"]
        )
    listed = {item["node"] for item in peer_items}
    unreachable = [u for u in unreachable if u["target"] in listed]
    summary = f"{me}: {len(own_items) + len(peer_items)} facts collected"
    for entry in unreachable:
        summary += f"; unreachable: {entry['target']} ({entry['reason']})"
    text = "\n".join(evidence)
    return {
        "propose": {"items": own_items, "evidence": text},
        "propose_peers": {"items": peer_items, "evidence": text} if peer_items else None,
        "unreachable": unreachable,
        "notes": notes,
        "summary": summary,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m commonplace.topology.collector",
        description="Collect this machine's facts for the Compute map, read-only.",
    )
    parser.add_argument("--runner-id", required=True, help="this machine's runner id (mach_...)")
    parser.add_argument("--hub-url", default=None, help="the hub's address, to check the way to it")
    parser.add_argument("--max-peers", type=int, default=DEFAULT_PEERS)
    args = parser.parse_args(argv)
    print(json.dumps(collect(Probe(), args.runner_id, args.hub_url, args.max_peers), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
