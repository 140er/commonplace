"""The Compute map is generic: nothing in it belongs to any one person's setup.

Product code, the discovery prompt and collector, the map's web files, the topology docs and
every topology test and fixture are scanned for the shapes a real setup leaves behind:
addresses outside the documentation and private ranges, public-looking host names, e-mail
domains, home-directory paths, ssh user names, MAC addresses, key and token shapes. The
patterns are derived from structure (address classes, host-name syntax, secret shapes), so no
personal or setup token is written down here; the only literals are the invented names the
fixtures use, listed in ``SYNTHETIC_*`` below. Anything else must be a documentation address
or a reserved example name.

The self-test plants one violation of each kind, built from parts at run time so this file
stays clean, and shows that the scan fires on each.
"""

import ipaddress
import re
from pathlib import Path

import pytest

from commonplace import privacy

ROOT = Path(__file__).resolve().parent.parent

SCANNED = (
    "commonplace/topology/*.py",
    "commonplace/web/static/topology*",
    "commonplace/web/static/map-*",
    "commonplace/web/static/deck-compute.js",
    "commonplace/web/static/compute.js",
    "docs/topology.md",
    "docs/modules/topology.md",
    "docs/modules/topology.d/*.md",
    "tests/test_topology_*.py",
    "tests/topology_*",
)

# Addresses that name no real machine: RFC 5737 documentation ranges, RFC 1918 private
# ranges, RFC 3849 documentation, loopback and unspecified, and the carrier-grade range base
# the collector compares against (a range, not a peer).
OK_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "192.0.2.0/24",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "0.0.0.0/32",
        "2001:db8::/32",
        "::1/128",
        "::/128",
    )
)
OK_ADDRESSES = {ipaddress.ip_address("100.64.0.0")}

# Reserved example names (RFC 2606 and 6761) and the invented names the fixtures use.
RESERVED_SUFFIXES = {"example.com", "example.net", "example.org"}
RESERVED_TLDS = {"test", "example", "invalid", "localhost", "local", "internal", "lan"}
SYNTHETIC_SUFFIXES = {"tail-example.ts.net", "example.ts.net"}
# Top-level domains that are never an attribute or file name in this code base.
PUBLIC_TLDS = {
    "com", "net", "org", "io", "dev", "app", "co", "cloud", "xyz", "biz",
    "nl", "de", "uk", "fr", "eu", "us", "ca", "au",
}  # fmt: skip
# Invented ssh user names the fixtures use.
SYNTHETIC_USERS = {"deploy", "alice", "u", "user", "you"}
# The one place per kind where a fixture plants a secret to prove it is refused.
SECRET_FIXTURES = {
    "tests/test_topology_store.py",
    "tests/test_topology_proposals.py",
    "tests/test_topology_site_adapters.py",
    "tests/test_topology_site_routes.py",
}

IPV4 = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?![\w.]*\w)")
IPV6 = re.compile(r"(?<![\w:.])[0-9A-Fa-f:.]*:[0-9A-Fa-f:.]*:[0-9A-Fa-f:.]*(?![\w:])")
HOST = re.compile(r"(?<![\w@/.-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,24}\b")
EMAIL = re.compile(r"[\w.+-]+@((?:[\w-]+\.)+[A-Za-z]{2,24})")
HOME = re.compile(r"(?:/(?:home|Users)|[A-Za-z]:\\Users)[/\\]([^/\\\s'\"`<>*$|]+)")
SSH_USER = re.compile(r"^\s*User\s+(\S+)\s*$", re.MULTILINE)
MAC = re.compile(r"(?<![\w:-])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![\w:-])")
SSH_KEY = re.compile(r"\b(?:ssh-(?:rsa|ed25519|dss)|ecdsa-sha2-\S+)\s+[A-Za-z0-9+/=]{20,}")
KEY_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----([^-]*)-----END", re.DOTALL)
REAL_KEY_BODY = 100  # characters: a real key body is far longer than any fixture's stub
HOME_PLACEHOLDERS = {"user", "you", "example", "name", "me", "username", "<user>"}


def listed():
    return sorted({path for pattern in SCANNED for path in ROOT.glob(pattern) if path.is_file()})


def address_ok(text):
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return True  # not an address at all (a version, a refusal fixture)
    return address in OK_ADDRESSES or any(address in net for net in OK_NETWORKS)


def name_ok(host):
    host = host.lower().rstrip(".")
    labels = host.split(".")
    if labels[-1] in RESERVED_TLDS or labels[-1] not in PUBLIC_TLDS:
        return True
    return any(host == s or host.endswith("." + s) for s in RESERVED_SUFFIXES | SYNTHETIC_SUFFIXES)


def violations(name, text, *, secret_fixture=False):
    """Everything in one file's text that belongs to a real setup, as ``(kind, what)``."""
    found = []
    for match in IPV4.finditer(text):
        if not address_ok(match.group()):
            found.append(("address", match.group()))
    for match in IPV6.finditer(text):
        if not address_ok(match.group()):
            found.append(("address", match.group()))
    for match in HOST.finditer(text):
        if not name_ok(match.group()):
            found.append(("host name", match.group()))
    for match in EMAIL.finditer(text):
        if not name_ok(match.group(1)):
            found.append(("e-mail domain", match.group(1)))
    for match in HOME.finditer(text):
        if match.group(1).lower() not in HOME_PLACEHOLDERS:
            found.append(("home directory", match.group()))
    for match in SSH_USER.finditer(text):
        if match.group(1) not in SYNTHETIC_USERS:
            found.append(("ssh user", match.group(1)))
    for match in MAC.finditer(text):
        found.append(("hardware address", match.group()))
    for match in SSH_KEY.finditer(text):
        found.append(("public key", match.group()[:12]))
    for match in KEY_BLOCK.finditer(text):
        if len(match.group(1).strip()) > REAL_KEY_BODY:
            found.append(("private key", "a key body of real length"))
    if not secret_fixture:
        for line in text.splitlines():
            flags = privacy.scan(line)
            if privacy.has_secret(flags):
                found.append(("secret", ", ".join(flags)))
    return [(name, kind, what) for kind, what in found]


def scan():
    out = []
    for path in listed():
        name = path.relative_to(ROOT).as_posix()
        text = path.read_text(errors="replace")
        out += violations(name, text, secret_fixture=name in SECRET_FIXTURES)
    return out


# ── The scan covers what it should ───────────────────────────────────────
def test_the_scan_reaches_product_prompt_web_docs_and_tests():
    names = {path.relative_to(ROOT).as_posix() for path in listed()}
    for expected in (
        "commonplace/topology/discovery.py",  # the discovery prompt
        "commonplace/topology/collector.py",
        "commonplace/topology/proxy_adapters.py",
        "commonplace/web/static/topology-map.js",
        "commonplace/web/static/topology-detail.js",
        "commonplace/web/static/map-discovery.js",
        "docs/topology.md",
        "docs/modules/topology.md",
        "tests/test_topology_discovery.py",
        "tests/test_topology_end_to_end.py",
        "tests/topology_fixture.mjs",
    ):
        assert expected in names, expected
    assert len(names) > 40


# ── The guard ────────────────────────────────────────────────────────────
def test_nothing_in_the_map_is_specific_to_one_person_s_setup():
    assert scan() == []


# ── The self-test: the scan fires on a planted violation of each kind ─────
def dotted(*parts):
    return ".".join(str(part) for part in parts)


PLANTED = {
    "address": lambda: f"connect to {dotted(8, 8, 4, 4)} now",
    "address v6": lambda: "route via " + ":".join(["2a01", "4f8", "1", "2", "3", "4", "5", "6"]),
    "host name": lambda: "ssh to " + dotted("build-box", "internal-corp", "io"),
    "e-mail domain": lambda: "owner@" + dotted("mail", "somecorp", "com"),
    "home directory": lambda: "/".join(["", "home", "realperson", ".ssh", "config"]),
    "ssh user": lambda: "Host x\n    User " + "realperson" + "\n",
    "hardware address": lambda: ":".join(["3c", "22", "fb", "0a", "1b", "2c"]),
    "public key": lambda: "ssh-" + "ed25519 " + "AAAAC3NzaC1lZDI1NTE5AAAAI" + "x" * 20,
    "private key": lambda: (
        "-----BEGIN " + "OPENSSH PRIVATE KEY-----\n" + "QUJD" * 60 + "\n-----END PRIVATE KEY"
    ),
    "secret": lambda: "key id " + "AK" + "IA" + "ABCDEFGHIJKLMNOP",
}


@pytest.mark.parametrize("kind", sorted(PLANTED))
def test_the_scan_fires_on_a_planted_violation(kind):
    hits = violations("planted.txt", PLANTED[kind]())
    assert [k for _, k, _ in hits if k.startswith(kind.split()[0])], (kind, hits)


def test_a_planted_violation_in_a_scanned_file_fails_the_guard(tmp_path, monkeypatch):
    clean = tmp_path / "docs" / "topology.md"
    clean.parent.mkdir()
    clean.write_text("A route from example.com via " + dotted(203, 0, 113, 7) + ".\n")
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    assert scan() == []
    clean.write_text(clean.read_text() + "And a real one at " + dotted(8, 8, 4, 4) + ".\n")
    assert [kind for _, kind, _ in scan()] == ["address"]


def test_documentation_ranges_and_example_names_are_not_violations():
    text = "\n".join(
        [
            dotted(192, 0, 2, 10),
            dotted(198, 51, 100, 7),
            dotted(203, 0, 113, 10),
            dotted(10, 0, 0, 7),
            dotted(172, 16, 4, 4),
            dotted(192, 168, 1, 20),
            "2001:db8::10",
            "web1.example.test, app.example.com, cdn.example.net, host.example.org",
            "beta.local, nas.internal, alpha.tail-example.ts.net",
            "owner@example.com",
            "created 2026-01-01T10:00:00Z, version 6.8.0, mach_0123456789abcdef",
            "Host web1\n    User deploy\n",
        ]
    )
    assert violations("fixture.txt", text) == []


def test_a_secret_fixture_may_plant_secrets_but_not_a_real_key():
    planted = PLANTED["secret"]()
    assert violations("x.py", planted, secret_fixture=True) == []
    assert violations("x.py", PLANTED["private key"](), secret_fixture=True)


def test_the_guard_itself_names_no_real_setup_token():
    # It is part of the scan, so it cannot hide one; the only host names and users it spells
    # out are the reserved or invented ones above.
    me = Path(__file__).resolve().relative_to(ROOT).as_posix()
    assert me in {path.relative_to(ROOT).as_posix() for path in listed()}
