"""Parallel module discovery and revalidation for the unbundled web app."""

import base64
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
    manifest_path = static / "build" / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_bytes())
        body = re.sub(rb'<link rel="stylesheet"[^>]+>\n?', b"", body)
        styles = f'<link rel="stylesheet" href="/app/build/{manifest["css"]}">\n'
        styles += "".join(
            f'<link rel="preload" href="/app/fonts/{name}" as="font" type="font/woff2" crossorigin>\n'
            for name in manifest["fonts"]
        )
        body = body.replace(b"</title>", b"</title>\n" + styles.encode())
        body = body.replace(
            b'<script type="module" src="/app/app.js"></script>',
            b'<script type="module" data-bundle-entry>' + bundle_entry(static) + b"</script>",
        )
        return body.replace(
            b'<script src="/app/deck-prepaint.js">',
            b"<script data-boot-guard>"
            + (static / "boot-guard.js").read_bytes()
            + b'</script>\n<script src="/app/deck-prepaint.js">',
        )
    links = "".join(
        f'<link rel="modulepreload" href="/app/{name}">\n'
        for name in closure(graph(static), ["app.js"])
    )
    # Keep the blocking look script in its original position before the module entry.
    body = body.replace(
        b'<script src="/app/deck-prepaint.js">',
        b"<script data-boot-guard>"
        + (static / "boot-guard.js").read_bytes()
        + b'</script>\n<script src="/app/deck-prepaint.js">',
    )
    return body.replace(
        b'<script type="module" src="/app/app.js">',
        links.encode()
        + b'<script async src="/app/route-preload.js"></script>\n<script type="module" src="/app/app.js">',
    )


def boot_policy(static, policy):
    digest = base64.b64encode(
        hashlib.sha256((static / "boot-guard.js").read_bytes()).digest()
    ).decode()
    hashes = f"'sha256-{digest}'"
    if (static / "build" / "manifest.json").is_file():
        entry_digest = base64.b64encode(hashlib.sha256(bundle_entry(static)).digest()).decode()
        hashes += f" 'sha256-{entry_digest}'"
    return policy.replace("script-src 'self'", f"script-src 'self' {hashes}")


def bundle_entry(static):
    """The URL fragment is client-only; select its route bundle without another fetch."""
    manifest = json.loads((static / "build" / "manifest.json").read_bytes())
    return (
        "globalThis.commonplaceAssetManifest="
        + json.dumps(manifest, separators=(",", ":"))
        + ";\nconst parts=location.hash.replace(/^#\\//,'').split(/[/?]/);"
        "let route=parts[0]||'home';"
        "if(route==='bots'&&['coworkers','compute','management','conversations'].includes(parts[2]))route='coworkers';"
        "if(route==='board'&&parts[2]==='chat')route='chat';"
        "const routes=globalThis.commonplaceAssetManifest.routes;"
        "try{await import('/app/build/'+(Object.hasOwn(routes,route)?routes[route]:routes.shell));}"
        "catch{globalThis.commonplaceBootGuard?.failed();}\n"
    ).encode()


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
