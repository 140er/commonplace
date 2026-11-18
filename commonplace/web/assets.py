"""Parallel module discovery and revalidation for the unbundled web app."""

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

IMPORT = re.compile(r"^import .*? from ['\"]\./([^'\"]+)['\"]", re.M)
ROUTES = {
    "settings": ["projects.js", "agent-schedules.js", "tool-policy.js", "plan.js", "agents.js"],
    "admin": ["admin.js", "adminrunners.js", "invites.js"],
    "instance-admin": ["instance-admin.js"],
    "console": ["console.js"],
    "compute-map": ["topology-map.js", "map-discovery.js"],
    "rules": ["rules.js"],
    "memory": ["memory.js", "board.js", "chatdock.js"],
    "board": ["board.js", "children.js", "projectpanel.js", "chatdock.js", "cardmove.js"],
    "space": [
        "board.js",
        "children.js",
        "projectpanel.js",
        "chatdock.js",
        "cardmove.js",
        "decide.js",
    ],
    "chat": ["board.js", "children.js", "projectpanel.js", "chatdock.js", "chat.js"],
    "card": ["details.js", "board.js", "children.js", "deck-board.js", "lifecycle.js", "decide.js"],
    "needs": ["needs.js", "details.js", "board.js", "children.js", "chatdock.js"],
    "decide": ["decide.js", "decide-swipe.js"],
    "bots": ["bots.js"],
    "home": ["deck-home.js"],
    "today": ["deck-today-capy.js"],
    "compute": ["deck-workspace.js"],
    "coworkers": ["deck-workspace.js"],
}


@lru_cache(maxsize=2)
def _graph(signature):
    return {name: IMPORT.findall(Path(path).read_text()) for name, path, _mtime, _size in signature}


def graph(static):
    # Only the small shipped asset directory is inspected, never workspace data.
    return _graph(
        tuple(
            (p.name, str(p), p.stat().st_mtime_ns, p.stat().st_size)
            for p in sorted(static.glob("*.js"))
        )
    )


def closure(graph, entries):
    found = set()

    def visit(name):
        if name in found:
            return
        found.add(name)
        for dependency in graph.get(name, []):
            visit(dependency)

    for name in entries:
        visit(name)
    return sorted(found)


def index(body, static):
    links = "".join(
        f'<link rel="modulepreload" href="/app/{name}">\n'
        for name in closure(graph(static), ["app.js"])
    )
    # Keep the blocking look script in its original position before the module entry.
    return body.replace(
        b'<script type="module" src="/app/app.js">',
        links.encode()
        + b'<script async src="/app/route-preload.js"></script>\n<script type="module" src="/app/app.js">',
    )


def route_preload(static):
    modules = graph(static)
    routes = {name: closure(modules, entries) for name, entries in ROUTES.items()}
    return (
        """// Discover only this route, beside the shell graph. No identity or data requests here.
const modules = """
        + json.dumps(routes, separators=(",", ":"))
        + """;
let deck = true;
try { deck = localStorage.getItem('cmmn.ui') !== 'classic'; } catch {}
const look = new URLSearchParams(location.search).get('ui');
if (look === 'deck' || look === 'classic') deck = look === 'deck';
const parts = location.hash.split('?')[0].replace(/^#\\//, '').split('/');
let route = parts[0] || (deck ? 'home' : '');
if (route === 'bots' && ['coworkers', 'compute', 'management', 'conversations'].includes(parts[2])) route = 'coworkers';
void import('/app/route-styles.js').then(m => m.loadRouteStyles(location.hash)).catch(() => {});
for (const name of Object.hasOwn(modules, route) ? modules[route] : []) {
  const link = document.createElement('link');
  link.rel = 'modulepreload'; link.href = '/app/' + name;
  document.head.append(link);
}
"""
    ).encode()


def etag(body):
    return '"' + hashlib.sha256(body).hexdigest() + '"'
