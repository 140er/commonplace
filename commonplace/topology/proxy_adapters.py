"""Reverse proxy adapters: which upstream a proxy sends a site to, read from its config.

An adapter gets configuration text and returns the sites it names and, per site, the
upstream targets with the config line as evidence. That is all an adapter does:

- it reads only the text handed to it: no file, no ``import`` or ``include`` target, no
  environment variable, no network, and it runs nothing;
- it keeps only site names and upstream targets. Everything else in a proxy config
  (basic-auth users and hashes, API tokens, TLS keys, headers) is never copied, and a
  site or upstream line that still looks like a secret is withheld and counted.

Two adapters ship: ``caddy`` (a Caddyfile or Caddy's JSON config) and
``nginx_proxy_manager`` (its proxy-host rows as JSON, or the nginx files it generates).
Add another by subclassing ``ProxyAdapter`` and calling ``register``. The contract is in
``docs/topology.md``.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass

from . import model
from .model import TopologyError

MAX_CONFIG = 512 * 1024
MAX_SITES = 500
MAX_UPSTREAMS = 16
MAX_DEPTH = 8
EVIDENCE_LINE = 300
ANY_SITE = "*"

_USERINFO = re.compile(r"//[^/@\s]*@")
_SITE_NAME = re.compile(r"(?:\*\.)?[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")


@dataclass(frozen=True)
class Upstream:
    """One target a proxy forwards to. ``host`` is empty when the config does not name one
    (a placeholder); ``path`` is empty when the target serves the whole site."""

    host: str
    port: int | None
    scheme: str | None
    path: str
    evidence: str


@dataclass(frozen=True)
class ProxySite:
    """The names one proxy entry serves (``*`` is any name) and where it forwards them."""

    names: tuple
    upstreams: tuple
    evidence: str


@dataclass(frozen=True)
class Parsed:
    """An adapter's result. ``withheld`` counts lines left out because they looked secret."""

    sites: tuple
    withheld: int = 0


class ProxyAdapter:
    """Subclass, set ``kind`` and ``label``, implement ``read``, then ``register``."""

    kind = ""
    label = ""

    def read(self, text: str, found: "Found") -> None:
        raise NotImplementedError

    def parse(self, text) -> Parsed:
        if not isinstance(text, str) or not text.strip():
            raise TopologyError(f"no: the {self.label} config must be text")
        if len(text) > MAX_CONFIG:
            raise TopologyError(f"no: the {self.label} config is larger than {MAX_CONFIG} bytes")
        found = Found()
        self.read(text, found)
        return found.parsed()


ADAPTERS: dict = {}


def register(adapter: ProxyAdapter) -> ProxyAdapter:
    ADAPTERS[adapter.kind] = adapter
    return adapter


def parse(kind, text) -> Parsed:
    adapter = ADAPTERS.get(kind) if isinstance(kind, str) else None
    if adapter is None:
        raise TopologyError(f"no: proxy kind must be one of {', '.join(sorted(ADAPTERS))}")
    return adapter.parse(text)


def serving(parsed: Parsed, site: str) -> ProxySite | None:
    """The entry that serves ``site``: its own name first, then a wildcard, then any-name."""
    parent = "*." + site.partition(".")[2]
    for wanted in (site, parent, ANY_SITE):
        for entry in parsed.sites:
            if wanted in entry.names:
                return entry
    return None


def primary(entry: ProxySite) -> Upstream | None:
    """The upstream for the whole site, or the first one when every target has a path."""
    whole = [upstream for upstream in entry.upstreams if not upstream.path]
    return (whole or list(entry.upstreams) or [None])[0]


# ── What leaves an adapter ────────────────────────────────────────────────
def _evidence(text: str) -> str | None:
    """One evidence line, or ``None`` when it looks like it holds a secret."""
    line = " ".join(_USERINFO.sub("//", text).split())[:EVIDENCE_LINE]
    return None if model.secret_kinds(line) else line


class Found:
    """Collects sites and upstreams; the only way anything leaves an adapter."""

    def __init__(self):
        self._sites: dict = {}
        self.withheld = 0

    def site(self, names, evidence: str):
        """Open the entry for these names; returns its handle, or ``None`` to skip it."""
        names = tuple(dict.fromkeys(name for name in map(site_name, names) if name))
        if not names:
            return None
        if names not in self._sites:
            if len(self._sites) >= MAX_SITES:
                return None
            line = _evidence(evidence)
            if line is None:
                self.withheld += 1
                line = f"{', '.join(names)} (line withheld: it looked like a secret)"
            self._sites[names] = (line, [])
        return names

    def upstream(self, handle, target: str, evidence: str, path: str = "", scheme=None) -> None:
        if handle is None or not isinstance(target, str):
            return
        upstreams = self._sites[handle][1]
        if len(upstreams) >= MAX_UPSTREAMS:
            return
        found_scheme, host, port = split_target(target)
        line = _evidence(evidence)
        if host and model.secret_kinds(host):
            host, line = "", None
        if line is None:
            self.withheld += 1
            line = "upstream line withheld: it looked like a secret"
        path = path if isinstance(path, str) and path.startswith(("/", "@")) else ""
        if path == "/" or model.secret_kinds(path):
            path = ""
        upstreams.append(Upstream(host, port, found_scheme or scheme, path[:120], line))

    def parsed(self) -> Parsed:
        sites = tuple(
            ProxySite(names, tuple(upstreams), line)
            for names, (line, upstreams) in self._sites.items()
        )
        return Parsed(sites, self.withheld)


def site_name(value) -> str:
    """A served name from a site address: no scheme, port or path; ``*`` for any name."""
    if not isinstance(value, str):
        return ""
    text = value.strip().strip(",").lower()
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0]
    if text.startswith("["):
        return ""
    if not text:
        return ""
    host = text.rsplit(":", 1)[0] if ":" in text else text
    if host in ("", "_", ANY_SITE):
        return ANY_SITE
    return host if _SITE_NAME.fullmatch(host) and not model.secret_kinds(host) else ""


def split_target(value: str) -> tuple:
    """``(scheme, host, port)`` of an upstream as written; host is empty when unknown."""
    text, scheme = value.strip(), None
    if text.startswith("unix") and "/" in text:
        return None, "localhost", None
    if "://" in text:
        scheme, text = text.split("://", 1)
        scheme = scheme.rpartition("+")[2].lower()
        scheme = scheme if scheme in ("http", "https", "h2c", "grpc", "grpcs") else None
    text = text.split("/", 1)[0].rpartition("@")[2]
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        port = rest.lstrip(":")
    elif text.count(":") == 1:
        host, port = text.split(":")
    else:
        host, port = text, ""
    port = port.split("-", 1)[0]
    number = int(port) if port.isascii() and port.isdecimal() and 0 < int(port) < 65536 else None
    host = host.lower().rstrip(".") or ("localhost" if number else "")
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        if not model.HOSTNAME.fullmatch(host or "-"):
            host = ""
    return scheme, host, number


# ── Tokens: Caddyfile and nginx share the shape "words, blocks in braces" ──
def _statements(text: str, *, nginx: bool) -> list:
    """Nested ``(line, words, block or None)``. Quotes, comments and (Caddyfile) line
    continuations are handled; a brace is a block only as a word of its own."""
    root: list = []
    stack, words, word, quote, line, started = [root], [], None, "", 1, 1

    def end_word():
        nonlocal word
        if word is not None:
            words.append(word)
            word = None

    def end_statement(block=False):
        nonlocal words
        end_word()
        if words or block:
            entry = (started, words, [] if block else None)
            stack[-1].append(entry)
            if block:
                stack.append(entry[2])
        words = []

    index, size = 0, len(text)
    while index < size:
        char = text[index]
        following = text[index + 1] if index + 1 < size else ""
        index += 1
        if quote:
            if char == "\\" and following in (quote, "\\") and quote != "`":
                word += following
                index += 1
            elif char == quote:
                quote = ""
            else:
                line += char == "\n"
                word += char
            continue
        if word is None and not words:
            started = line
        if char == "\n":
            line += 1
            if nginx:
                end_word()
            else:
                end_statement()
        elif char == "\\" and following == "\n" and not nginx:
            end_word()
            line += 1
            index += 1
        elif char in " \t\r":
            end_word()
        elif char == "#" and word is None:
            while index < size and text[index] != "\n":
                index += 1
        elif (
            char in "\"`'"
            and word is None
            and (char != "'" or nginx)
            and (char != "`" or not nginx)
        ):
            quote, word = char, ""
        elif char == ";" and nginx:
            end_statement()
        elif char == "{" and (
            not (word or "").endswith("$")
            if nginx
            else word is None and following in ("\n", "\r", " ", "\t", "")
        ):
            end_statement(block=True)
        elif char == "}" and word is None:
            end_statement()
            if len(stack) > 1:
                stack.pop()
        else:
            word = (word or "") + char
    end_statement()
    return root


def _loads(text: str):
    if text.lstrip()[:1] not in ("{", "["):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _bracketed(host: str) -> str:
    """An IPv6 address in the brackets a URL needs."""
    try:
        return f"[{host}]" if ipaddress.ip_address(host).version == 6 else host
    except ValueError:
        return host


# ── Caddy ─────────────────────────────────────────────────────────────────
class CaddyAdapter(ProxyAdapter):
    """A Caddyfile, or Caddy's JSON config (what its admin API and autosave file hold)."""

    kind = "caddy"
    label = "Caddy"
    blocks = ("handle", "handle_path", "route")

    def read(self, text, found):
        data = _loads(text)
        if isinstance(data, dict):
            servers = _dict(_dict(_dict(data.get("apps")).get("http")).get("servers"))
            for server in servers.values():
                self._routes(_dict(server).get("routes"), (ANY_SITE,), "", found, 0)
            return
        entries = _statements(text, nginx=False)
        snippets = {
            words[0][1:-1]: block
            for _, words, block in entries
            if block is not None and len(words) == 1 and re.fullmatch(r"\(.+\)", words[0])
        }
        sites = [
            entry
            for entry in entries
            if entry[1] and not re.fullmatch(r"\(.+\)", entry[1][0]) and entry[1][0] != "import"
        ]
        if sites and any(block is None for _, _, block in sites):
            # A Caddyfile for one site: the address line, then its directives.
            line, words, _ = sites[0]
            sites = [(line, words, sites[1:])]
        for line, words, block in sites:
            names = [name for word in words for name in word.split(",")]
            handle = found.site(names, f"line {line}: {' '.join(words)} {{")
            self._directives(block, snippets, "", handle, found, 0)

    def _directives(self, entries, snippets, path, handle, found, depth):
        for line, words, block in entries or ():
            if not words:
                continue
            name, args = words[0], list(words[1:])
            if name == "import" and args and args[0] in snippets and depth < MAX_DEPTH:
                self._directives(snippets[args[0]], snippets, path, handle, found, depth + 1)
            elif name == "reverse_proxy":
                matcher = path
                if args and (args[0].startswith(("/", "@")) or args[0] == "*"):
                    first = args.pop(0)
                    matcher = path if first == "*" else first
                shown = f"reverse_proxy {matcher} ".replace("  ", " ")
                for target in args:
                    found.upstream(handle, target, f"line {line}: {shown}{target}", matcher)
                for inner, inner_words, _ in block or ():
                    if inner_words[:1] == ["to"]:
                        for target in inner_words[1:]:
                            evidence = f"line {inner}: {shown}{{ to {target} }}"
                            found.upstream(handle, target, evidence, matcher)
            elif name in self.blocks and block is not None and depth < MAX_DEPTH:
                inner = args[0] if args and args[0].startswith(("/", "@")) else path
                self._directives(block, snippets, inner, handle, found, depth + 1)

    def _routes(self, routes, hosts, path, found, depth):
        for route in _list(routes):
            if not isinstance(route, dict) or depth > MAX_DEPTH:
                continue
            matched, inner = [], path
            for match in _list(route.get("match")):
                if isinstance(match, dict):
                    matched += [host for host in _list(match.get("host")) if isinstance(host, str)]
                    paths = [entry for entry in _list(match.get("path")) if isinstance(entry, str)]
                    inner = paths[0] if paths else inner
            names = tuple(matched) or hosts
            for handler in _list(route.get("handle")):
                if not isinstance(handler, dict):
                    continue
                if handler.get("handler") == "subroute":
                    self._routes(handler.get("routes"), names, inner, found, depth + 1)
                elif handler.get("handler") == "reverse_proxy":
                    handle = found.site(names, f"route match host: {', '.join(names)}")
                    transport = handler.get("transport")
                    scheme = "https" if isinstance(transport, dict) and "tls" in transport else None
                    for upstream in _list(handler.get("upstreams")):
                        dial = upstream.get("dial") if isinstance(upstream, dict) else None
                        if isinstance(dial, str):
                            evidence = f"reverse_proxy upstream dial: {dial}"
                            found.upstream(handle, dial, evidence, inner, scheme)


# ── Nginx Proxy Manager ───────────────────────────────────────────────────
class NginxProxyManagerAdapter(ProxyAdapter):
    """Nginx Proxy Manager data: its proxy-host rows as JSON (the API's list or the
    ``proxy_host`` table), or the nginx server files it generates."""

    kind = "nginx_proxy_manager"
    label = "Nginx Proxy Manager"
    lists = ("proxy_hosts", "proxy_host", "hosts", "data")
    variables = ("$forward_scheme", "$server", "$port")

    def read(self, text, found):
        data = _loads(text)
        if data is None:
            self._servers(_statements(text, nginx=True), found, 0)
            return
        if isinstance(data, dict):
            data = next(
                (data[key] for key in self.lists if isinstance(data.get(key), list)), [data]
            )
        for row in _list(data):
            if isinstance(row, dict):
                self._row(row, found)

    @staticmethod
    def _decoded(value):
        """The ``proxy_host`` table keeps lists as JSON text."""
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return []
        return _list(value)

    @staticmethod
    def _forward(row):
        host, port = row.get("forward_host"), row.get("forward_port")
        if not isinstance(host, str) or not host.strip() or isinstance(port, bool):
            return None
        scheme = row.get("forward_scheme")
        scheme = scheme if scheme in ("http", "https") else "http"
        port = f":{port}" if isinstance(port, int) or (isinstance(port, str) and port) else ""
        return f"{scheme}://{_bracketed(host.strip())}{port}"

    def _row(self, row, found):
        if row.get("enabled") in (0, False) or row.get("is_deleted") in (1, True):
            return
        names = [name for name in self._decoded(row.get("domain_names")) if isinstance(name, str)]
        number = row.get("id")
        label = f"proxy host {number}" if type(number) is int else "proxy host"
        handle = found.site(names, f"{label}: domain_names {', '.join(names)}")
        target = self._forward(row)
        if target:
            found.upstream(handle, target, f"{label}: forward {target}")
        for location in self._decoded(row.get("locations")):
            target = self._forward(location) if isinstance(location, dict) else None
            path = location.get("path") if target else None
            if isinstance(path, str):
                found.upstream(handle, target, f"{label}: location {path} forward {target}", path)

    def _servers(self, entries, found, depth):
        for line, words, block in entries:
            if block is None or depth > MAX_DEPTH:
                continue
            if words[:1] == ["server"]:
                self._server(line, block, found)
            else:
                self._servers(block, found, depth + 1)

    def _server(self, line, block, found):
        block = [entry for entry in block if entry[1]]
        names = [name for _, words, _ in block if words[0] == "server_name" for name in words[1:]]
        names = [name for name in names if not name.startswith("~")]
        handle = found.site(names, f"line {line}: server_name {' '.join(names)}")
        values = self._set(block, {})
        if "$server" in values:
            target = "{}://{}:{}".format(
                values.get("$forward_scheme", ("http",))[0],
                _bracketed(values["$server"][0]),
                values.get("$port", ("",))[0],
            ).rstrip(":")
            shown = "; ".join(f"set {name} {value[0]}" for name, value in values.items())
            found.upstream(handle, target, f"line {values['$server'][1]}: {shown}")
        self._passes(block, values, "", handle, found, 0)

    def _set(self, block, inherited) -> dict:
        values = dict(inherited)
        for line, words, _ in block:
            if words[:1] == ["set"] and len(words) == 3 and words[1] in self.variables:
                values[words[1]] = (words[2], line)
        return values

    def _passes(self, block, values, path, handle, found, depth):
        for line, words, inner in block:
            if words[:1] == ["proxy_pass"] and len(words) == 2:
                target = words[1]
                for name in self.variables:
                    if name in target:
                        target = target.replace(name, values[name][0]) if name in values else ""
                target = target.split("$", 1)[0]
                shown = f"location {path} " if path else ""
                found.upstream(handle, target, f"line {line}: {shown}proxy_pass {words[1]}", path)
            elif words[:1] == ["location"] and inner is not None and depth < MAX_DEPTH:
                self._passes(inner, self._set(inner, values), words[-1], handle, found, depth + 1)


register(CaddyAdapter())
register(NginxProxyManagerAdapter())
