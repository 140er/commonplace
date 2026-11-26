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
        # The early script goes first: a script placed after a pending stylesheet waits for it.
        body = body.replace(
            b"</title>",
            b"</title>\n<script data-early-hints>"
            + early_script(static)
            + b"</script>\n"
            + styles.encode(),
        )
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
        early_digest = base64.b64encode(hashlib.sha256(early_script(static)).digest()).decode()
        hashes += f" 'sha256-{entry_digest}' 'sha256-{early_digest}'"
    return policy.replace("script-src 'self'", f"script-src 'self' {hashes}")


# The URL fragment is client-only: both scripts pick this route's bundle from it.
ROUTE_FROM_HASH = (
    "const parts=location.hash.replace(/^#\\//,'').split(/[/?]/);"
    "let route=parts[0]||'home';"
    "if(route==='bots'&&['coworkers','compute','management','conversations'].includes(parts[2]))route='coworkers';"
    "if(route==='board'&&parts[2]==='chat')route='chat';"
)


# Plain GET reads the route will make first start now, beside the bundle. api.js takes each one
# once (the matching request only, within 15 s); anything else, and every write, reads afresh.
EARLY_READS = (
    "const early=globalThis.commonplaceEarly={};"
    "const read=path=>{try{const response=fetch(path,{credentials:'same-origin',cache:'no-store',"
    "headers:{Accept:'application/json'}});response.catch(()=>{});early[path]={at:Date.now(),response};}catch{}};"
    "read('/api/me');"
    "if(route==='board'){try{read('/api/boards?view=shell');"
    "const cards=slug=>{if(slug)read('/api/boards/'+encodeURIComponent(slug)+'/cards');};"
    "if(parts[1])cards(decodeURIComponent(parts[1]));else{"
    # A bare #/board opens the saved project else the first readable one. The saved key is per
    # workspace, so only an unambiguous one is guessed; the directory read settles the rest.
    "let saved='';const keys=[];for(let i=0;i<localStorage.length;i++){const key=localStorage.key(i);"
    "if(key&&key.startsWith('cmmn.board.last.'))keys.push(key);}"
    "if(keys.length===1)saved=localStorage.getItem(keys[0])||'';cards(saved);"
    "early['/api/boards?view=shell'].response.then(r=>r.clone().json()).then(d=>{const boards=d.boards||[];"
    "const board=boards.find(b=>b.slug===saved)||boards[0];if(board&&board.slug!==saved)cards(board.slug);}).catch(()=>{});}"
    "}catch{}}"
)


def early_script(static):
    """Runs before the stylesheet: start fetching this route's bundle while styles load."""
    routes = json.loads((static / "build" / "manifest.json").read_bytes())["routes"]
    return (
        "(()=>{const routes="
        + json.dumps(routes, separators=(",", ":"))
        + ";"
        + ROUTE_FROM_HASH
        + "const link=document.createElement('link');link.rel='modulepreload';"
        "link.href='/app/build/'+(Object.hasOwn(routes,route)?routes[route]:routes.shell);"
        "document.head.append(link);" + EARLY_READS + "})();"
    ).encode()


def bundle_entry(static):
    """The URL fragment is client-only; select its route bundle without another fetch."""
    manifest = json.loads((static / "build" / "manifest.json").read_bytes())
    return (
        "globalThis.commonplaceAssetManifest="
        + json.dumps(manifest, separators=(",", ":"))
        + ";\n"
        + ROUTE_FROM_HASH
        + "const routes=globalThis.commonplaceAssetManifest.routes;"
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
