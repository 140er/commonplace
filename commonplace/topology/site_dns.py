"""The DNS step of site route discovery: where a public domain name points.

One lookup of one public name through an injected resolver. The step never connects to
what it finds and never asks for a name that is not public (an address, a single label,
``localhost`` or a special-use suffix such as ``.local``). Tests pass a fake resolver;
the hub's default is the system resolver. The contract is in ``docs/topology.md``.
"""

from __future__ import annotations

import ipaddress
import queue
import socket
import threading
from dataclasses import dataclass
from typing import Callable

from . import model
from .model import TopologyError

LOOKUP_SECONDS = 5.0
MAX_ANSWERS = 16
# Names under these suffixes are never public (RFC 6761, RFC 6762, RFC 8375 and the
# usual private zones), so they are not looked up.
PRIVATE_SUFFIXES = (
    "localhost",
    "local",
    "internal",
    "intranet",
    "lan",
    "home",
    "corp",
    "private",
    "home.arpa",
    "in-addr.arpa",
    "ip6.arpa",
    "invalid",
    "test",
    "example",
    "onion",
    "alt",
)


@dataclass(frozen=True)
class DnsAnswer:
    """What a resolver saw for one name: its alias chain, in order, and its addresses."""

    addresses: tuple = ()
    cnames: tuple = ()


Resolver = Callable[[str], DnsAnswer]


class DnsUnavailable(TopologyError):
    """The lookup itself failed (no resolver, a timeout); nothing is known about the name."""


def is_address(value) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def public_name(value) -> str:
    """A public domain name in the form the topology stores it, or a refusal."""
    if not isinstance(value, str):
        raise TopologyError("no: site must be a domain name")
    name = value.strip().lower().removesuffix(".")
    labels = name.split(".")
    if (
        not model.KEY.fullmatch(name)
        or "_" in name
        or len(labels) < 2
        or is_address(name)
        or labels[-1].isdigit()
        or any(not label or label.startswith("-") or label.endswith("-") for label in labels)
    ):
        raise TopologyError(
            "no: site must be a public domain name such as example.com, up to 63 characters"
        )
    if any(name == suffix or name.endswith("." + suffix) for suffix in PRIVATE_SUFFIXES):
        raise TopologyError("no: only public domain names are looked up, and this one is not")
    return model.node_key(name, "site")


def system_resolver(name: str) -> DnsAnswer:
    """The system resolver: addresses and, when it reports one, the canonical name."""
    try:
        found = socket.getaddrinfo(name, None, type=socket.SOCK_STREAM, flags=socket.AI_CANONNAME)
    except socket.gaierror as exc:
        if exc.errno in (socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME)):
            return DnsAnswer()
        raise DnsUnavailable("no: the DNS lookup failed; try again later") from None
    canonical = next((entry[3] for entry in found if entry[3]), "")
    return DnsAnswer(
        addresses=tuple(entry[4][0] for entry in found),
        cnames=(canonical,) if canonical and canonical.lower().rstrip(".") != name else (),
    )


def _within(resolver, name, seconds) -> DnsAnswer:
    """Run one lookup on its own thread, so a hanging resolver cannot hold the caller."""
    done: queue.Queue = queue.Queue(maxsize=1)

    def run():
        try:
            done.put((resolver(name), None))
        except BaseException as exc:  # handed to the caller below
            done.put((None, exc))

    threading.Thread(target=run, name="topology-dns", daemon=True).start()
    try:
        answer, failure = done.get(timeout=seconds)
    except queue.Empty:
        raise DnsUnavailable("no: the DNS lookup timed out; try again later") from None
    if isinstance(failure, TopologyError):
        raise failure
    if failure is not None or not isinstance(answer, DnsAnswer):
        raise DnsUnavailable("no: the DNS lookup failed; try again later")
    return answer


def _unique(values) -> list:
    return list(dict.fromkeys(values))


def lookup(site, resolver: Resolver | None = None, *, seconds: float = LOOKUP_SECONDS) -> dict:
    """Look one public name up.

    Returns ``{"name", "cnames", "addresses", "evidence"}``: the alias chain in order,
    the addresses sorted, and one evidence line per record. A name without records has
    empty lists and says so in its evidence. Entries a resolver returns that are not a
    hostname or an address are left out.
    """
    name = public_name(site)
    answer = _within(resolver or system_resolver, name, seconds)
    cnames = _unique(
        entry.lower().rstrip(".")
        for entry in tuple(answer.cnames)[:MAX_ANSWERS]
        if isinstance(entry, str) and model.HOSTNAME.fullmatch(entry.rstrip(".") or "-")
    )
    cnames = [entry for entry in cnames if entry != name and not is_address(entry)]
    addresses = sorted(
        _unique(
            str(ipaddress.ip_address(entry))
            for entry in tuple(answer.addresses)[:MAX_ANSWERS]
            if isinstance(entry, str) and is_address(entry)
        )
    )
    evidence, at = [], name
    for target in cnames:
        evidence.append(f"{at} CNAME {target}")
        at = target
    for address in addresses:
        record = "AAAA" if ":" in address else "A"
        evidence.append(f"{at} {record} {address}")
    if not evidence:
        evidence.append(f"{name} has no address records")
    return {"name": name, "cnames": cnames, "addresses": addresses, "evidence": evidence}
