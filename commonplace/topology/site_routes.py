"""Site route discovery: from a domain name to the app that serves it.

``discover`` derives one site's route, read-only, in three steps:

1. the DNS step (``site_dns``): where the public name points;
2. the reverse proxy step (``proxy_adapters``): which upstream the proxy on that machine
   sends the site to, from configuration text the caller read there and handed in;
3. the mapping: each step becomes a link between nodes of the map, the links in order
   are the site route, and every hop keeps the lines it was derived from.

A name that points outside the machines on the map, or an upstream nobody knows, ends
the route in an explicit "External / unknown" node instead of a guess. Nothing here
writes the store: the result goes through ``proposals.propose``, so a new route and a
changed upstream are both a decision for the owner. The contract is in
``docs/topology.md``.
"""

from __future__ import annotations

import ipaddress
import re

from . import model, proposals, proxy_adapters, site_dns, store
from .model import TopologyError

EDGE_KINDS = ("cloud", "router")
EXTERNAL_NAME = "External / unknown"
EXTERNAL_KEY = "ext-unknown"
LOCAL_NAMES = ("localhost", "host.docker.internal", "host.containers.internal")
MAX_PROXIES = 8
MAX_MACHINES = 32
MAX_APPS = 32
MAX_NAMES = 16
DNS_EVIDENCE_LINES = 8


def _norm(value) -> str:
    """A hostname or address as it is compared: lowercase, no trailing dot."""
    text = value.strip().lower().rstrip(".") if isinstance(value, str) else ""
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return text if model.HOSTNAME.fullmatch(text or "-") else ""


def _listed(value, what, limit) -> list:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or len(value) > limit:
        raise TopologyError(f"no: {what} must be a list of at most {limit} entries")
    return list(value)


def _entry(value, what, required, optional=()) -> dict:
    if (
        not isinstance(value, dict)
        or set(required) - value.keys()
        or value.keys() - set(required) - set(optional)
    ):
        fields = ", ".join(required) + "".join(f", {name} (optional)" for name in optional)
        raise TopologyError(f"no: each entry of {what} holds {fields}")
    return value


def _names(value, what) -> list:
    found = []
    for name in _listed(value, what, MAX_NAMES):
        clean = _norm(name)
        if not clean:
            raise TopologyError(f"no: {what} must hold hostnames or addresses")
        model.check_secret(what, clean)
        found.append(clean)
    return found


def _proxies(value) -> list:
    """``[(node, adapter, parsed)]``. A refusal never repeats any config text."""
    found = []
    for raw in _listed(value, "proxies", MAX_PROXIES):
        entry = _entry(raw, "proxies", ("node", "kind", "config"))
        node = model.node_key(entry["node"], "proxies node")
        parsed = proxy_adapters.parse(entry["kind"], entry["config"])
        found.append((node, proxy_adapters.ADAPTERS[entry["kind"]], parsed))
    return found


def _machines(value) -> list:
    found = []
    for raw in _listed(value, "machines", MAX_MACHINES):
        entry = _entry(raw, "machines", ("node", "addresses"), ("kind",))
        kind = entry.get("kind")
        if kind is not None and kind not in model.NODE_KINDS:
            raise TopologyError(f"no: machines kind must be one of {', '.join(model.NODE_KINDS)}")
        node = model.node_key(entry["node"], "machines node")
        found.append((node, kind, _names(entry["addresses"], "machines addresses")))
    return found


def _apps(value) -> list:
    found = []
    for raw in _listed(value, "apps", MAX_APPS):
        entry = _entry(raw, "apps", ("node", "host"), ("port", "names"))
        port = entry.get("port")
        if port is not None:
            port = model.LINK_FIELDS["port"]("apps port", port)
        node = model.node_key(entry["node"], "apps node")
        host = model.node_key(entry["host"], "apps host")
        if node == host:
            raise TopologyError("no: an app and its host are two different nodes")
        found.append((node, host, port, _names(entry.get("names"), "apps names")))
    return found


def _key(prefix: str, text: str) -> str:
    """A node key derived from a name or address the config or the DNS answer holds."""
    text = re.sub(r"[^a-z0-9._-]", "-", text.lower()).strip("._-")
    return f"{prefix}-{text}"[:63].rstrip("._-") if text else EXTERNAL_KEY


def _local(host: str) -> bool:
    if host in LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class _Map:
    """The map as it is now plus what the caller says it saw, indexed for matching."""

    def __init__(self, graph, machines, apps):
        self.kinds, self.declared = {}, {}
        self.public, self.internal = {}, {}
        self.hosted, self.host_of = {}, {}
        for record in graph["nodes"]:
            fields = {name: field["value"] for name, field in record["fields"].items()}
            key = record["key"]
            self.kinds[key] = fields.get("kind")
            self.internal.setdefault(key, key)
            self._name(self.public, fields.get("hostname"), key)
            for name in (fields.get("tailscale_name"), *(fields.get("aliases") or ())):
                self._name(self.internal, name, key)
        for record in graph["links"]:
            if record["method"] == "hosts":
                self.host_of.setdefault(record["to"], record["from"])
                port = record["fields"].get("port", {}).get("value")
                if port is not None:
                    self.hosted.setdefault((record["from"], port), record["to"])
        self.existing = set(self.kinds)
        for node, kind, addresses in machines:
            if node not in self.kinds:
                if kind is None:
                    raise TopologyError(
                        f"no: node {node} is not on the map yet; give its kind under machines"
                    )
                self.kinds[node] = self.declared[node] = kind
                self.internal.setdefault(node, node)
            for address in addresses:
                self._name(self.public, address, node)
        for node, host, port, names in apps:
            self.need(host, "apps host")
            if node not in self.kinds:
                self.kinds[node] = self.declared[node] = "container"
            self.host_of[node] = host
            if port is not None:
                self.hosted[(host, port)] = node
            for name in (node, *names):
                self.internal[name] = node
        # A public name also works from inside; an internal one never answers for DNS.
        for name, key in self.public.items():
            self.internal.setdefault(name, key)

    @staticmethod
    def _name(index, value, key) -> None:
        name = _norm(value)
        if name:
            index.setdefault(name, key)

    def need(self, node, what) -> None:
        if node not in self.kinds:
            raise TopologyError(
                f"no: {what} {node} is not on the map yet; list it under machines with its kind"
            )


class _Route:
    def __init__(self, site):
        self.site, self.at = site, site
        self.hops, self.nodes, self.notes = [], {}, []
        self.ends = "host"

    def hop(self, target, method, evidence, port=None) -> bool:
        """Add a hop from where the route is now; refused when it would loop back."""
        if target == self.at or target == self.site or any(target == h[0] for h in self.hops):
            self.notes.append(f"{self.at} points back at {target}; the route stops at {self.at}")
            return False
        fields = {"port": port} if port else {}
        self.hops.append((self.at, target, method, fields, list(evidence)))
        self.at = target
        return True

    def external(self, target: str, method, evidence, port=None) -> None:
        key = _key("ext", target)
        fields = {"kind": "cloud", "name": EXTERNAL_NAME}
        if target and model.HOSTNAME.fullmatch(target):
            fields["hostname"] = target
        self.nodes[key] = fields
        if self.hop(key, method, evidence, port):
            self.ends = "external"


def _landing(route, answer, known) -> str | None:
    """The DNS hop: to the node the name points at, else to an external node."""
    seen = [*answer["addresses"], *answer["cnames"], route.site]
    matches = list(dict.fromkeys(known.public[name] for name in seen if name in known.public))
    matches = [node for node in matches if node != route.site]
    evidence = answer["evidence"][:DNS_EVIDENCE_LINES]
    if len(answer["evidence"]) > DNS_EVIDENCE_LINES:
        evidence.append(f"({len(answer['evidence']) - DNS_EVIDENCE_LINES} more records)")
    if not matches:
        target = (answer["cnames"][-1:] or answer["addresses"][:1])[0]
        evidence.append(f"{target} is not recorded for any node on the map")
        route.external(target, "dns", evidence)
        return None
    node = matches[0]
    name = next(name for name in seen if known.public.get(name) == node)
    evidence.append(f"{name} is recorded for {node}")
    if len(matches) > 1:
        route.notes.append(
            f"the DNS answer also matches {', '.join(matches[1:])}; the route follows {node}"
        )
    route.hop(node, "dns", evidence)
    return node


def _off_route(route, proxies) -> None:
    """A config that names the site on a machine DNS does not lead to is reported, never
    stitched onto the route."""
    for at, adapter, parsed in proxies:
        if proxy_adapters.serving(parsed, route.site) is not None:
            route.notes.append(
                f"the {adapter.label} config on {at} names {route.site}, "
                f"but DNS does not point at {at}"
            )


def _proxy(route, node, proxies, known):
    """The proxy entry that serves the site on the way from ``node``, or ``None``."""
    site = route.site
    serving = [
        (at, adapter, entry)
        for at, adapter, parsed in proxies
        if (entry := proxy_adapters.serving(parsed, site)) is not None
    ]
    if known.kinds.get(node) in EDGE_KINDS:
        # A proxy or CDN in front: it forwards to the machine whose proxy serves the site.
        if not serving:
            route.ends = "edge"
            route.notes.append(f"no proxy config handed in names {site}; the route stops at {node}")
            return None
        at, adapter, entry = serving[0]
        if at != node:
            evidence = [f"{adapter.label} config on {at} serves {site}: {entry.evidence}"]
            if not route.hop(at, "proxy", evidence):
                return None
        return at, adapter, entry
    here = [found for found in serving if found[0] == node]
    if here:
        return here[0]
    if any(at == node for at, _, _ in proxies):
        route.notes.append(f"the proxy config on {node} does not name {site}")
    else:
        route.notes.append(f"no proxy config of {node} was handed in; the route stops there")
    _off_route(route, proxies)
    return None


def _upstream(route, proxy, known) -> None:
    """The reverse proxy hop: to a container or app, another machine, or an external node."""
    node, adapter, entry = proxy
    upstream = proxy_adapters.primary(entry)
    if upstream is None:
        route.notes.append(f"{node} serves {route.site} itself: its entry has no upstream")
        return
    others = len(entry.upstreams) - 1
    if others:
        route.notes.append(
            f"this entry has {others} more upstream target{'' if others == 1 else 's'} "
            "(other paths or load balancing) outside the route"
        )
    evidence = [f"{adapter.label} config on {node}, {entry.evidence}", upstream.evidence]
    host, port = upstream.host, upstream.port
    target = known.internal.get(host)
    if not host:
        evidence.append("the upstream is not a name or address this config spells out")
        route.external("", "reverse_proxy", evidence, port)
    elif _local(host) or target == node:
        app = known.hosted.get((node, port))
        if app is None:
            app = _key(node, f"port-{port}" if port else "app")
            route.nodes[app] = {
                "kind": "container",
                "name": f"App on port {port}" if port else "App behind the proxy",
            }
        if route.hop(app, "reverse_proxy", evidence, port):
            route.ends = "app"
    elif target is not None and known.kinds.get(target) == "container":
        owner = known.host_of.get(target)
        if owner and owner != node and route.hop(owner, "reverse_proxy", evidence, port):
            evidence = [f"{target} runs on {owner}"]
            if route.hop(target, "hosts", evidence, port):
                route.ends = "app"
        elif (not owner or owner == node) and route.hop(target, "reverse_proxy", evidence, port):
            route.ends = "app"
    elif target is not None:
        if route.hop(target, "reverse_proxy", evidence, port):
            app = known.hosted.get((target, port))
            if app and route.hop(app, "hosts", [f"{app} listens on port {port} of {target}"], port):
                route.ends = "app"
    elif "." not in host and not site_dns.is_address(host):
        # A bare name resolves only next to the proxy: a container on the same machine.
        app = _key(node, host)
        route.nodes[app] = {"kind": "container", "name": host}
        evidence.append(f"{host} is a bare name, so it is taken as a container on {node}")
        if route.hop(app, "reverse_proxy", evidence, port):
            route.ends = "app"
    else:
        evidence.append(f"{host} is not recorded for any node on the map")
        route.external(host, "reverse_proxy", evidence, port)


def _evidence(route) -> str:
    lines = [
        f"Site route for {route.site}, discovered read-only: "
        "a public DNS lookup, then the proxy config that was handed in."
    ]
    for source, target, method, _, evidence in route.hops:
        lines.append(model.link_id(source, target, method))
        lines += ["  " + line for line in evidence]
    return "\n".join(lines)[: model.MAX_EVIDENCE]


def derive(site, proxies=None, machines=None, apps=None, *, graph, resolver=None) -> dict:
    """Derive one site's route without proposing it.

    ``graph`` is ``store.read_graph``'s result. Returns ``{"site", "resolved", "ends",
    "hops", "notes", "withheld", "items", "evidence"}``; ``items`` and ``evidence`` are
    what ``proposals.propose`` takes, and are ``None`` for a name without DNS records.
    """
    site = site_dns.public_name(site)
    proxies = _proxies(proxies)
    known = _Map(graph, _machines(machines), _apps(apps))
    for node, _, _ in proxies:
        known.need(node, "proxies node")
    answer = site_dns.lookup(site, resolver)
    route = _Route(site)
    result = {
        "site": site,
        "resolved": bool(answer["addresses"] or answer["cnames"]),
        "ends": None,
        "hops": [],
        "notes": route.notes,
        "withheld": sum(parsed.withheld for _, _, parsed in proxies),
        "items": None,
        "evidence": None,
    }
    if not result["resolved"]:
        route.notes.append(f"{site} has no DNS records; nothing is proposed")
        return result
    node = _landing(route, answer, known)
    proxy = _proxy(route, node, proxies, known) if node else None
    if proxy:
        _upstream(route, proxy, known)
    if node is None:
        _off_route(route, proxies)
    on_route = {site, *(target for _, target, _, _, _ in route.hops)}
    items = [
        {"node": key, "fields": fields}
        for key, fields in {site: {"kind": "website"}, **route.nodes}.items()
        if key in on_route and key not in known.existing
    ]
    items += [
        {"node": key, "fields": {"kind": kind}}
        for key, kind in known.declared.items()
        if key in on_route and key not in route.nodes
    ]
    for source, target, method, fields, _ in route.hops:
        items.append({"link": [source, target, method], **({"fields": fields} if fields else {})})
    hops = [model.link_id(source, target, method) for source, target, method, _, _ in route.hops]
    items.append({"route": site, "hops": hops})
    result.update(
        ends=route.ends,
        hops=[{"link": link, "evidence": hop[4]} for link, hop in zip(hops, route.hops)],
        items=items,
        evidence=_evidence(route),
    )
    return result


def discover(
    ctx,
    site=None,
    proxies=None,
    machines=None,
    apps=None,
    ttl_seconds=None,
    board=None,
    *,
    resolver=None,
) -> dict:
    """Derive one site's route and hand it to the owner through the proposal path.

    Returns ``derive``'s summary (without ``items`` and ``evidence``) plus ``applied``,
    ``proposal`` and ``unchanged`` from ``proposals.propose``. The caller needs write
    access to the ``topology`` wing; that is checked before any lookup.
    """
    proposals._caller(ctx)
    workspace_id = ctx.principal.workspace_id
    proposals.settle(ctx.gateway, workspace_id)
    graph = store.read_graph(store.open_topology(ctx.gateway, workspace_id))
    resolver = resolver or getattr(ctx.gateway, "topology_resolver", None)
    result = derive(site, proxies, machines, apps, graph=graph, resolver=resolver)
    items, evidence = result.pop("items"), result.pop("evidence")
    outcome = {"applied": None, "proposal": None, "unchanged": 0}
    if items:
        outcome = proposals.propose(
            ctx, items=items, evidence=evidence, ttl_seconds=ttl_seconds, board=board
        )
    return {**result, **outcome}
