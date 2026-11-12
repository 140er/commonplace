import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const { KINDS, kindOf } = await import("../commonplace/web/static/topology-icons.js");
const layout = await import("../commonplace/web/static/topology-layout.js");
const ui = await import("../commonplace/web/static/topology-map.js");
const { fixture, largeFixture } = await import("./topology_fixture.mjs");
const read = (name) => readFile(new URL(`../commonplace/web/static/${name}`, import.meta.url), "utf8");
const settle = async () => { for (let i = 0; i < 12; i++) await new Promise((resolve) => setImmediate(resolve)); };
const HOSTILE = '<img src=x onerror=alert(1)>"';
const EMPTY = { graph: { wing: "topology", nodes: [], links: [], routes: [] }, status: { nodes: {}, links: {} } };
const nodeTag = (scene, key) => new RegExp(`<g class="[^"]*" data-key="${key.replace(/\./g, "\\.")}"[\\s\\S]*?</g></g>`).exec(scene)?.[0] ?? assert.fail(`no node ${key}`);
const linkTag = (scene, id) => scene.split('<g class="topo-link').find((part) => part.includes(`data-link="${id.replace(/>/g, "&gt;")}"`)) ?? assert.fail(`no link ${id}`);
const placeOf = (scene, key) => /transform="translate\((-?[\d.]+) (-?[\d.]+)\)"/.exec(nodeTag(scene, key)).slice(1).map(Number);
function fakeStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return { getItem: (key) => (data.has(key) ? data.get(key) : null), setItem: (key, value) => { data.set(key, String(value)); }, data };
}

// ---------- model ----------

test("model: kinds, labels and one status per node and per link end", () => {
  const model = ui.mapModel(fixture());
  const nodes = Object.fromEntries(model.nodes.map((node) => [node.key, node]));
  assert.deepEqual([nodes.maple.kind, nodes.maple.name, nodes.maple.role, nodes.maple.status], ["server", "Maple", "app host", "up"]);
  assert.equal(nodes.birch.status, "degraded");
  assert.equal(nodes["shop-api"].status, "down");
  assert.equal(nodes.pocket.status, "unknown");
  const links = Object.fromEntries(model.links.map((link) => [link.id, link]));
  assert.deepEqual(links["nimbus-cdn>gate:reverse_proxy"].status, { from: "up", to: "degraded" });
  assert.deepEqual(links["pocket>maple:relay"].status, { from: "unknown", to: "up" });
  assert.equal(links["tower>birch:jump_host"].via, "maple");
  assert.deepEqual(model.routes.map((route) => route.site), ["orchard.example", "shop.orchard.example"]);
});

test("model: a stale node keeps its last icon and label, an unrecorded hop is drawn as not recorded", () => {
  const model = ui.mapModel(fixture());
  const nas = model.nodes.find((node) => node.key === "old-nas");
  assert.deepEqual([nas.kind, nas.name, nas.role, nas.stale], ["server", "Old NAS", "archive", true]);
  const ghost = model.nodes.find((node) => node.key === "shop-edge");
  assert.deepEqual([ghost.kind, ghost.missing, ghost.status], ["unknown", true, "unknown"]);
  const hop = model.links.find((link) => link.id === "nimbus-dns>shop-edge:proxy");
  assert.deepEqual([hop.missing, hop.status], [true, { from: "unknown", to: "unknown" }]);
});

test("model: anything that is not a known status is unknown, never up", () => {
  const payload = fixture();
  payload.status.nodes.maple = "green";
  payload.status.links["gate>maple:reverse_proxy"] = { from: "UP", to: null };
  delete payload.status.links["maple>orchard-web:hosts"];
  payload.status.nodes.birch = { state: "down" };
  payload.status.links["maple>birch:hosts"] = "degraded";
  const model = ui.mapModel(payload);
  assert.equal(model.nodes.find((node) => node.key === "maple").status, "unknown");
  assert.equal(model.nodes.find((node) => node.key === "birch").status, "down");
  const ends = (id) => model.links.find((link) => link.id === id).status;
  assert.deepEqual(ends("gate>maple:reverse_proxy"), { from: "unknown", to: "unknown" });
  assert.deepEqual(ends("maple>orchard-web:hosts"), { from: "unknown", to: "unknown" });
  assert.deepEqual(ends("maple>birch:hosts"), { from: "degraded", to: "degraded" });
  for (const junk of [null, undefined, {}, { graph: null }, { graph: { nodes: [null, { key: 7 }], links: [null, {}], routes: [null, {}] } }]) {
    assert.deepEqual(ui.mapModel(junk), { nodes: [], links: [], routes: [] });
  }
});

// ---------- icons and scene ----------

test("icons: one distinct original drawing per node kind, no outside artwork", async () => {
  const kinds = ["pc", "mac", "phone", "vm", "server", "router", "cloud", "website", "container"];
  assert.deepEqual(Object.keys(KINDS).sort(), [...kinds, "unknown"].sort());
  assert.equal(new Set(Object.values(KINDS).map((kind) => kind.icon)).size, 10);
  for (const kind of Object.values(KINDS)) { assert.ok(kind.label); assert.doesNotMatch(kind.icon, /<image|<use|href|url\(|style=/); }
  assert.equal(kindOf("toaster"), "unknown");
  assert.equal(kindOf("constructor"), "unknown");
  for (const name of ["topology-map.js", "topology-layout.js", "topology-icons.js", "topology.css"]) {
    const source = await read(name);
    assert.doesNotMatch(source, /https?:\/\/|\/\/cdn|@import|url\(/, `${name} must not load anything from elsewhere`);
  }
});

test("scene: the fixture renders the right icon, label and dot for every device", () => {
  const core = ui.createMapCore();
  core.apply(fixture());
  const scene = core.scene();
  const expect = { "orchard.example": "website", "nimbus-cdn": "cloud", gate: "router", maple: "server", birch: "vm", "orchard-web": "container", "studio-mac": "mac", tower: "pc", pocket: "phone", "shop-edge": "unknown" };
  for (const [key, kind] of Object.entries(expect)) {
    const tag = nodeTag(scene, key);
    assert.ok(tag.includes(`<g class="topo-icon">${KINDS[kind].icon}</g>`), `${key} draws the ${kind} icon`);
    assert.ok(tag.includes(` k-${kind} `));
  }
  const maple = nodeTag(scene, "maple");
  assert.match(maple, /<text class="topo-name" data-full="Maple">Maple<\/text><text class="topo-role" data-full="app host">app host<\/text>/);
  assert.match(maple, /role="button" aria-pressed="false" aria-label="Maple, Server, app host\. Status: Up\."/);
  assert.match(maple, /<g class="topo-dot s-up" data-state="up" transform="translate\(24 -24\)"><title>Status: Up<\/title><circle r="5.5"\/><\/g>/);
  assert.match(nodeTag(scene, "birch"), /class="topo-dot s-degraded" data-state="degraded"[^>]*><title>Status: Degraded<\/title><path /);
  assert.match(nodeTag(scene, "shop-api"), /class="topo-dot s-down" data-state="down"[^>]*><title>Status: Down<\/title><rect /);
  assert.match(nodeTag(scene, "pocket"), /class="topo-dot s-unknown" data-state="unknown"[^>]*><title>Status: Unknown<\/title><circle r="4.5"/);
  assert.match(nodeTag(scene, "old-nas"), /class="topo-node k-server s-unknown stale"[\s\S]*>archive · stale</);
  assert.match(nodeTag(scene, "gate"), />edge proxy · conflict</);
  assert.match(nodeTag(scene, "shop-edge"), /aria-label="shop-edge, Unknown device\. Status: Unknown\. Not recorded\."[\s\S]*>not recorded</);
});

test("scene: a link has a labelled dot at each end with that end's own status", () => {
  const core = ui.createMapCore();
  core.apply(fixture());
  const link = linkTag(core.scene(), "nimbus-cdn>gate:reverse_proxy");
  assert.match(link, /<title>Nimbus CDN to Gate, reverse proxy\. Nimbus CDN end: Up\. Gate end: Degraded\.<\/title>/);
  assert.match(link, /<text class="topo-method"[^>]*>reverse proxy<\/text>/);
  assert.match(link, /class="topo-dot s-up" data-state="up" data-end="from"[^>]*><title>Nimbus CDN end: Up<\/title><circle r="5.5"/);
  assert.match(link, /class="topo-dot s-degraded" data-state="degraded" data-end="to"[^>]*><title>Gate end: Degraded<\/title><path /);
  assert.match(linkTag(core.scene(), "tower>birch:jump_host"), /Tower to Birch, jump host via maple\./);
  assert.match(linkTag(core.scene(), "nimbus-dns>shop-edge:proxy"), /^ missing"[\s\S]*Not recorded\./);
});

test("status is never colour alone: four different shapes, each with its word", () => {
  const shapes = ui.STATES.map((state) => ui.statusDot(state, { x: 0, y: 0 }, "t").replace(/^<g[^>]*><title>t<\/title>|<\/g>$/g, ""));
  assert.deepEqual(ui.STATES, ["up", "degraded", "down", "unknown"]);
  assert.equal(new Set(shapes).size, 4);
  assert.deepEqual(shapes.map((shape) => /^<(\w+)/.exec(shape)[1]), ["circle", "path", "rect", "circle"]);
  const frame = ui.renderFrame();
  const legend = /<ul class="topo-legend"[\s\S]*?<\/ul>/.exec(frame)[0];
  for (const state of ui.STATES) assert.ok(legend.includes(`<g class="topo-dot s-${state}">`) && legend.includes(`</svg>${ui.STATUS_TEXT[state]}</li>`));
  assert.doesNotMatch(frame, /style=/);
});

test("scene: text from the store is escaped everywhere it is drawn", () => {
  const payload = fixture();
  payload.graph.nodes[5].values = { kind: "server", name: HOSTILE, role: HOSTILE };
  payload.graph.links[0].method = HOSTILE;
  payload.graph.routes[0].site = HOSTILE;
  const core = ui.createMapCore();
  core.apply(payload);
  assert.doesNotMatch(core.scene(), /<img/);
  assert.match(core.scene(), /&lt;img src=x onerror=alert\(1\)&gt;/);
});

test("scene: long names are cut for the label and kept whole for assistive text", () => {
  const payload = fixture();
  payload.graph.nodes[5].values.name = "A".repeat(60);
  const core = ui.createMapCore();
  core.apply(payload);
  const maple = nodeTag(core.scene(), "maple");
  assert.ok(maple.includes(`<text class="topo-name" data-full="${"A".repeat(60)}">${"A".repeat(23)}…</text>`));
  assert.ok(maple.includes(`aria-label="${"A".repeat(60)}, Server`));
});

// ---------- routes ----------

test("route: the chosen site's path is marked on, the rest dimmed, and said in words", () => {
  const core = ui.createMapCore();
  core.apply(fixture());
  assert.doesNotMatch(core.scene(), / on"| dim"/);
  core.setRoute("orchard.example");
  const scene = core.scene();
  for (const key of ["orchard.example", "nimbus-dns", "nimbus-cdn", "gate", "maple", "orchard-web"]) assert.match(nodeTag(scene, key), /^<g class="[^"]* on"/, key);
  for (const key of ["tower", "birch", "shop-api", "pocket"]) assert.match(nodeTag(scene, key), /^<g class="[^"]* dim"/, key);
  assert.match(linkTag(scene, "gate>maple:reverse_proxy"), /^ on"/);
  assert.match(linkTag(scene, "studio-mac>maple:tailscale"), /^ dim"/);
  assert.equal(ui.routeText(core.route, core.model), "orchard.example → Nimbus DNS (dns, Up) → Nimbus CDN (proxy, Up) → Gate (reverse proxy, Degraded) → Maple (reverse proxy, Up) → orchard-web (hosts, Up)");
  core.setRoute("shop.orchard.example");
  assert.equal(ui.routeText(core.route, core.model), "shop.orchard.example → Nimbus DNS (dns, Unknown) → shop-edge (proxy, Unknown, not recorded)");
  core.setRoute("nowhere.example");
  assert.equal(core.route, null);
  // A route that disappears from the store stops being highlighted.
  core.setRoute("orchard.example");
  const without = fixture();
  without.graph.routes = [];
  core.apply(without);
  assert.equal(core.route, null);
});

// ---------- layout, pan and zoom ----------

test("layout: the same topology always lands in the same places, whatever the order", () => {
  const model = ui.mapModel(fixture());
  const first = layout.autoLayout(model.nodes, model.links);
  assert.deepEqual(layout.autoLayout(model.nodes, model.links), first);
  assert.deepEqual(layout.autoLayout([...model.nodes].reverse(), [...model.links].reverse()), first);
  assert.equal(new Set(Object.values(first).map((at) => `${at.x},${at.y}`)).size, model.nodes.length);
  // Traffic flows down the page: site, DNS or CDN, edge, machine, app; desks last.
  const y = (key) => first[key].y;
  assert.ok(y("orchard.example") < y("nimbus-cdn") && y("nimbus-cdn") < y("gate") && y("gate") < y("maple") && y("maple") < y("orchard-web") && y("orchard-web") < y("tower"));
  assert.equal(y("maple"), y("birch"));
});

test("layout: a few hundred nodes stay apart and are placed quickly", () => {
  const core = ui.createMapCore();
  const started = performance.now();
  core.apply(largeFixture(300));
  const scene = core.scene();
  const elapsed = performance.now() - started;
  const places = Object.values(core.positions);
  assert.equal(places.length, 300);
  assert.equal(new Set(places.map((at) => `${at.x},${at.y}`)).size, 300);
  assert.equal(scene.split('data-key="').length - 1, 300);
  assert.equal(scene.split('data-link="').length - 1, core.model.links.length);
  assert.ok(elapsed < 1500, `300 nodes took ${elapsed} ms`);
});

test("links: two methods between the same machines run side by side, each dot at its own end", () => {
  const model = ui.mapModel(fixture());
  const positions = layout.autoLayout(model.nodes, model.links);
  const places = layout.layoutLinks(model.links, positions);
  const a = places.get("studio-mac>maple:tailscale"), b = places.get("studio-mac>maple:direct");
  assert.notDeepEqual([a.x1, a.y1, a.x2, a.y2], [b.x1, b.y1, b.x2, b.y2]);
  const distance = (p, q) => Math.hypot(p.x - q.x, p.y - q.y);
  for (const link of model.links) {
    const at = places.get(link.id);
    assert.ok(distance(at.from, positions[link.from]) < distance(at.from, positions[link.to]), link.id);
    assert.ok(distance(at.to, positions[link.to]) < distance(at.to, positions[link.from]), link.id);
  }
  // Opposite directions between one pair do not collapse onto one line either.
  const pair = [{ id: "a>b:direct", from: "a", to: "b" }, { id: "b>a:direct", from: "b", to: "a" }];
  const both = layout.layoutLinks(pair, { a: { x: 0, y: 0 }, b: { x: 200, y: 0 } });
  assert.notEqual(both.get("a>b:direct").y1, both.get("b>a:direct").y1);
  assert.ok(both.get("b>a:direct").from.x > both.get("b>a:direct").to.x);
  assert.equal(layout.layoutLinks([{ id: "a>gone:direct", from: "a", to: "gone" }], { a: { x: 0, y: 0 } }).size, 0);
});

test("zoom keeps the point under the pointer still and stays inside its limits", () => {
  const view = { x: 40, y: -20, k: 1 };
  const before = layout.toWorld(view, 300, 200);
  const zoomed = layout.zoomAbout(view, 300, 200, 1.7);
  const after = layout.toWorld(zoomed, 300, 200);
  assert.ok(Math.abs(before.x - after.x) < 1e-9 && Math.abs(before.y - after.y) < 1e-9);
  assert.equal(zoomed.k, 1.7);
  assert.equal(layout.zoomAbout(view, 0, 0, 1000).k, layout.ZOOM.max);
  assert.equal(layout.zoomAbout(view, 0, 0, 0.0001).k, layout.ZOOM.min);
});

// What one fit has to keep inside the padded box: every plate, mark, name and role line, at the fixed text size.
function extents(items, view, size = layout.TEXT.desktop) {
  let left = Infinity, right = -Infinity, top = Infinity, bottom = -Infinity;
  for (const item of items) {
    const room = layout.slotRoom(item.slot ?? Infinity, view.k);
    const half = Math.max(27 * view.k, Math.min(layout.textWidth(item.name ?? "", size), room) / 2);
    left = Math.min(left, item.x * view.k + view.x - half); right = Math.max(right, item.x * view.k + view.x + half);
    top = Math.min(top, item.y * view.k + view.y - 31 * view.k); bottom = Math.max(bottom, item.y * view.k + view.y + 27 * view.k + 6 + size + 14 + 4);
  }
  return { left, right, top, bottom };
}

test("fit: the whole graph, tall into a wide box and into a narrow box, always inside the padded box", () => {
  const tall = [{ x: 0, y: 0, name: "Gate" }, { x: 340, y: 0, name: "Nimbus CDN" }, { x: 0, y: 672, name: "Studio Mac" }, { x: 340, y: 672, name: "Tower" }];
  for (const [w, h, size] of [[1100, 460, 12], [358, 330, 11], [240, 900, 11], [900, 620, 12]]) {
    const view = layout.fitView(tall, w, h, { size });
    const at = extents(tall, view, size);
    assert.ok(at.left >= 24 - 1e-6 && at.right <= w - 24 + 1e-6 && at.top >= 24 - 1e-6 && at.bottom <= h - 24 + 1e-6, `${w}x${h}: ${JSON.stringify(at)}`);
    assert.ok(Math.abs((at.left + at.right) / 2 - w / 2) < 1e-6 && Math.abs((at.top + at.bottom) / 2 - h / 2) < 1e-6, `${w}x${h} centred`);
    assert.ok(view.k > 0 && view.k <= layout.FIT_MAX);
  }
  // The box a caller passes is the stage without the toolbar and the legend: a smaller box gives a smaller scale.
  const withBars = layout.fitView(tall, 1100, 460 - 90, {}), without = layout.fitView(tall, 1100, 460, {});
  assert.ok(withBars.k < without.k);
  // Scaled up as well as down, never past 64px icons.
  const few = [{ x: 0, y: 0 }, { x: 170, y: 0 }];
  assert.equal(layout.fitView(few, 1400, 800).k, layout.FIT_MAX);
  assert.ok(54 * layout.fitView(few, 1400, 800).k <= 64.0001);
  assert.deepEqual(layout.fitView([], 1000, 700), { x: 500, y: 350, k: 1 });
});

test("fit shows the whole graph however many devices there are", () => {
  const core = ui.createMapCore();
  core.apply(largeFixture(300));
  core.fit(1000, 700);
  assert.ok(core.view.k > 0 && core.view.k < 1);
  for (const at of Object.values(core.positions)) {
    const x = at.x * core.view.k + core.view.x, y = at.y * core.view.k + core.view.y;
    assert.ok(x >= 24 && x <= 976 && y >= 24 && y <= 676, `${x},${y}`);
  }
  core.apply(fixture());
  core.fit(1000, 700, { size: 12 });
  const items = core.model.nodes.map((node) => ({ ...core.positions[node.key], name: node.name, slot: core.slots()[node.key] }));
  const at = extents(items, core.view);
  assert.ok(at.left >= 24 - 1e-6 && at.right <= 976 + 1e-6 && at.top >= 24 - 1e-6 && at.bottom <= 676 + 1e-6, "every device of the fixture is inside");
});

test("names: cut to the slot with an ellipsis, whole when they fit, the slot is the row gap minus 8px", () => {
  const size = 12;
  assert.equal(layout.labelText("Maple", 200, size), "Maple");
  const cut = layout.labelText("shop.orchard.example", 100, size);
  assert.ok(cut.endsWith("…") && cut.length < "shop.orchard.example".length);
  assert.ok(layout.textWidth(cut, size) <= 100 + 1e-9, "the cut name fits its room");
  assert.equal(layout.labelText("Maple", 2, size), "…");
  assert.ok(layout.textWidth(layout.labelText("W".repeat(40), 150, size), size) <= 150 + 1e-9);
  // A slot is the horizontal distance to the nearest neighbour in the row; a device alone in its row has none.
  const slots = layout.slotWidths({ a: { x: 0, y: 0 }, b: { x: 170, y: 10 }, c: { x: 500, y: 0 }, d: { x: 0, y: 200 } });
  assert.deepEqual([slots.a, slots.b, slots.c, slots.d], [170, 170, 330, Infinity]);
  assert.equal(layout.slotRoom(170, 0.5), 77);
  assert.equal(layout.slotRoom(170, 1), 162);
  assert.equal(layout.slotRoom(Infinity, 0.1), Infinity);
});

test("roles: shown only when the scale gives them room", () => {
  const slot = 170, size = 11;
  const role = "build runner";
  const needed = layout.textWidth(role, size);
  const k = (needed + 8) / slot;
  assert.equal(layout.roleFits(role, layout.slotRoom(slot, k + 0.01), size), true);
  assert.equal(layout.roleFits(role, layout.slotRoom(slot, k - 0.01), size), false);
  assert.equal(layout.roleFits("", 0, size), true, "no role, nothing to hide");
  assert.equal(layout.roleFits(role, Infinity, size), true);
});

test("lines under a device show only when the row below leaves room for them", () => {
  assert.equal(layout.nameShown(1, 12), true);
  assert.equal(layout.nameShown(0.16, 12), false, "an overview of hundreds of devices names none of them");
  assert.equal(layout.roleShown(1, 12), true);
  const k = (6 + 12 + 14 + 4) / (layout.GRID.y - 54);
  assert.equal(layout.roleShown(k + 0.01, 12), true);
  assert.equal(layout.roleShown(k - 0.01, 12), false);
  assert.equal(layout.nameShown(k - 0.01, 12), true, "the name needs less room than the role");
});

test("text keeps its size: the stylesheet draws names at 1 / scale, the same arithmetic as labelOffsets", async () => {
  const css = await read("topology.css");
  assert.match(css, /\.topo-name \{[^}]*font-size: calc\(var\(--tsize\) \* var\(--inv, 1\)\);[^}]*transform: translateY\(calc\(27px \+ \(6px \+ var\(--tsize\)\) \* var\(--inv, 1\)\)\)/);
  assert.match(css, /\.topo \{ --tsize: 12px;/);
  assert.match(css, /@media \(max-width: 699px\) \{[\s\S]*\.topo \{ --tsize: 11px; \}/);
  for (const k of [0.25, 1, 1.18]) {
    const at = layout.labelOffsets(12, k);
    assert.ok(Math.abs((at.name - 27) * k - 18) < 1e-9, "the name baseline is 18 screen pixels under the plate edge");
    assert.ok(Math.abs((at.role - at.name) * k - 14) < 1e-9);
  }
  const source = await read("topology-map.js");
  assert.match(source, /sort\(\(a, b\) => \(a\.key === selected\) - \(b\.key === selected\)\)/, "the selected device is drawn last, on top");
  assert.match(source, /chosen = el\.classList\?\.contains\("sel"\);\n\s+const room = chosen \? Infinity/, "the selected device keeps its whole name and role");
});

test("link names show for the links of the selected device only, plus a route, hover and focus", async () => {
  const model = ui.mapModel(fixture());
  const places = layout.autoLayout(model.nodes, model.links);
  const none = ui.renderScene(model, places);
  assert.doesNotMatch(none, /class="topo-link[^"]* sel/);
  const picked = ui.renderScene(model, places, { selected: "maple" });
  const sel = [...picked.matchAll(/<g class="topo-link([^"]*)" data-link="([^"]+)"/g)].filter((m) => / sel/.test(m[1])).map((m) => m[2].replace(/&gt;/g, ">"));
  assert.deepEqual(sel.sort(), model.links.filter((l) => l.from === "maple" || l.to === "maple").map((l) => l.id).sort());
  assert.match(picked, /data-link="[^"]+" tabindex="0" role="img" aria-label="/, "a link can take keyboard focus and has its text");
  const css = await read("topology.css");
  assert.match(css, /\.topo-stage\.near \.topo-link\.sel \.topo-method, \.topo-link\.on \.topo-method, \.topo-link:hover \.topo-method, \.topo-link:focus \.topo-method \{ display: inline; \}/);
  assert.match(css, /\.topo-method \{ display: none; \}/);
  assert.match(css, /\.topo-name, \.topo-role \{ stroke: var\(--surface\); stroke-width: calc\(8px \* var\(--inv, 1\)\);[^}]*paint-order: stroke;/, "names sit above lines on a ground-coloured backing");
});

test("links: each end's status mark sits clear of the label box under its device", () => {
  const model = ui.mapModel(fixture());
  const positions = layout.autoLayout(model.nodes, model.links);
  const places = layout.layoutLinks(model.links, positions);
  for (const link of model.links) for (const [end, key] of [["from", link.from], ["to", link.to]]) {
    const dot = places.get(link.id)[end], node = positions[key];
    // The name starts about 43 units below the centre; a mark (radius up to 6.5) stays above that.
    assert.ok(Math.hypot(dot.x - node.x, dot.y - node.y) <= 36.0001, `${link.id} ${end}`);
    assert.ok(dot.y - node.y + 6.5 < 43, `${link.id} ${end} clear of the name`);
  }
});

// ---------- memory ----------

test("memory: a dragged place and the view survive a reload, per workspace", () => {
  const storage = fakeStorage();
  const first = ui.createMapCore({ storage, workspace: "ws_a" });
  first.apply(fixture());
  const auto = { ...first.positions.gate };
  assert.equal(first.moveNode("maple", 412.4, -96.2), true);
  first.setView({ x: 12, y: 34, k: 1.5 });
  const again = ui.createMapCore({ storage, workspace: "ws_a" });
  assert.deepEqual(again.view, { x: 12, y: 34, k: 1.5 });
  again.apply(fixture());
  assert.deepEqual(again.positions.maple, { x: 412, y: -96 });
  assert.deepEqual(again.positions.gate, auto);
  assert.deepEqual(placeOf(again.scene(), "maple"), [412, -96]);
  // Another workspace in the same browser has its own places.
  const other = ui.createMapCore({ storage, workspace: "ws_b" });
  other.apply(fixture());
  assert.equal(other.view, null);
  assert.notDeepEqual(other.positions.maple, { x: 412, y: -96 });
  // A drag in progress is not remembered until it is dropped.
  again.dragNode("gate", 900, 900);
  assert.deepEqual(again.positions.gate, { x: 900, y: 900 });
  assert.deepEqual(ui.createMapCore({ storage, workspace: "ws_a" }).apply(fixture()) && ui.mapMemory(storage, "ws_a").load().placed, { maple: { x: 412, y: -96 } });
  again.reset();
  assert.deepEqual(ui.mapMemory(storage, "ws_a").load().placed, {});
  assert.deepEqual(again.positions.gate, auto);
  assert.equal(again.moveNode("nobody", 1, 1), false);
  assert.equal(again.moveNode("maple", NaN, 1), false);
});

test("memory: unreadable or refused storage is an unplaced map, not an error", () => {
  for (const stored of ["{", "null", "[]", '{"placed":{"maple":{"x":"1","y":2},"gate":null},"view":{"x":1,"y":2,"k":0}}', '{"placed":7,"view":"wide"}']) {
    assert.deepEqual(ui.mapMemory(fakeStorage({ "commonplace.topology.ws": stored }), "ws").load(), { placed: {}, view: null });
  }
  const refusing = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("full"); } };
  const core = ui.createMapCore({ storage: refusing, workspace: "ws" });
  core.apply(fixture());
  assert.equal(core.moveNode("maple", 5, 5), true);
  assert.deepEqual(core.positions.maple, { x: 5, y: 5 });
  // A remembered zoom outside the limits comes back inside them.
  assert.equal(ui.mapMemory(fakeStorage({ "commonplace.topology.ws": '{"view":{"x":0,"y":0,"k":99}}' }), "ws").load().view.k, layout.ZOOM.max);
});

test("core: an unchanged read is not redrawn, a changed status is", () => {
  const core = ui.createMapCore();
  assert.equal(core.loaded, false);
  assert.equal(core.apply(fixture()), true);
  assert.equal(core.apply(fixture()), false);
  assert.match(linkTag(core.scene(), "nimbus-cdn>gate:reverse_proxy"), /s-degraded" data-state="degraded" data-end="to"/);
  assert.equal(core.apply(fixture({ gate: "down" })), true);
  assert.match(linkTag(core.scene(), "nimbus-cdn>gate:reverse_proxy"), /s-down" data-state="down" data-end="to"[^>]*><title>Gate end: Down/);
  assert.equal(core.select("maple"), "maple");
  assert.match(nodeTag(core.scene(), "maple"), /^<g class="[^"]* sel"[^>]*aria-pressed="true"/);
  assert.equal(core.select("maple"), null);
});

// ---------- the screen ----------

test("empty state: one button, off until discovery is connected", () => {
  assert.match(ui.emptyState(true), /<h2>No machines on the map yet<\/h2>[\s\S]*<button type="button" class="btn primary" data-topo="start">Start agent work on map<\/button><\/div>$/);
  assert.match(ui.emptyState(false), /data-topo="start" disabled>Start agent work on map<\/button><p class="topo-off">/);
});

function fakeDom() {
  const doc = { hidden: false, activeElement: null };
  const make = () => {
    const handlers = {}, parts = new Map(), classes = new Set();
    const el = {
      isConnected: true, ownerDocument: doc, dataset: {}, style: {}, attrs: {}, hidden: false, value: "", disabled: false, textContent: "", innerHTML: "", events: [],
      clientWidth: 1000, clientHeight: 700, classes,
      classList: { toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)), add: (name) => classes.add(name), remove: (name) => classes.delete(name) },
      setAttribute(name, value) { el.attrs[name] = String(value); },
      addEventListener(kind, handler, options) { handlers[kind] = handler; options?.signal?.addEventListener("abort", () => { delete handlers[kind]; }); },
      fire: (kind, event = {}) => handlers[kind]?.({ type: kind, preventDefault() {}, target: { closest: () => null }, ...event }),
      listening: () => Object.keys(handlers),
      querySelector(selector) { if (!parts.has(selector)) parts.set(selector, make()); return parts.get(selector); },
      querySelectorAll: () => [],
      dispatchEvent(event) { el.events.push(event); },
      setPointerCapture() {}, focus() {}, getBoundingClientRect: () => ({ left: 0, top: 0 }),
    };
    return el;
  };
  const host = make();
  const section = () => host.querySelector("[data-topology]");
  const part = (name) => section().querySelector(`[data-topo="${name}"]`);
  return { host, doc, section, part };
}
const on = (key) => ({ closest: (selector) => (selector === "[data-key]" ? { dataset: { key } } : null) });
const view = (dom) => { const [, x, y, k] = /translate\((\S+) (\S+)\) scale\((\S+)\)/.exec(dom.part("world").attrs.transform).map(Number); return { x, y, k }; };

test("screen: loads the map, reads again every few seconds and shows a status change", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const dom = fakeDom();
  let payload = fixture(), calls = 0;
  const screen = ui.createTopologyMap({ mount: () => dom.host, storage: fakeStorage(), workspace: () => "ws_a", api: async (path) => { calls += 1; assert.equal(path, "/api/topology/map"); return payload; } });
  screen.show();
  assert.match(dom.host.innerHTML, /Loading the map…/);
  await settle();
  assert.equal(calls, 1);
  const first = dom.part("world").innerHTML;
  assert.match(first, /data-key="maple"/);
  assert.equal(dom.part("note").innerHTML, "");
  assert.match(dom.part("route").innerHTML, /<option value="orchard.example">orchard.example<\/option>/);
  assert.ok(view(dom).k > 0);
  t.mock.timers.tick(4999);
  await settle();
  assert.equal(calls, 1);
  t.mock.timers.tick(1);
  await settle();
  assert.equal(calls, 2);
  assert.equal(dom.part("world").innerHTML, first);
  payload = fixture({ gate: "down" });
  t.mock.timers.tick(5000);
  await settle();
  assert.equal(calls, 3);
  assert.match(linkTag(dom.part("world").innerHTML, "nimbus-cdn>gate:reverse_proxy"), /<title>Gate end: Down<\/title><rect /);
  // A hidden tab waits; the map reads again once the tab is back.
  dom.doc.hidden = true;
  t.mock.timers.tick(5000);
  await settle();
  assert.equal(calls, 3);
  dom.doc.hidden = false;
  t.mock.timers.tick(5000);
  await settle();
  assert.equal(calls, 4);
  // Another screen replaced the markup: the map stops by itself and lets go of its listeners.
  const stage = dom.part("stage");
  assert.ok(stage.listening().includes("wheel"));
  dom.section().isConnected = false;
  t.mock.timers.tick(5000);
  await settle();
  t.mock.timers.tick(20000);
  await settle();
  assert.equal(calls, 4);
  assert.deepEqual(stage.listening(), []);
});

test("screen: wheel, drag, pinch and keys pan and zoom, and the view is remembered", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const dom = fakeDom(), storage = fakeStorage();
  const remembered = () => JSON.parse(storage.getItem("commonplace.topology.ws_a"));
  const screen = ui.createTopologyMap({ mount: () => dom.host, storage, workspace: () => "ws_a", api: async () => fixture() });
  screen.show();
  await settle();
  const stage = dom.part("stage");
  const start = view(dom);
  let prevented = false;
  stage.fire("wheel", { deltaY: -300, clientX: 400, clientY: 300, preventDefault() { prevented = true; } });
  const zoomed = view(dom);
  assert.ok(prevented && zoomed.k > start.k);
  assert.ok(Math.abs((400 - start.x) / start.k - (400 - zoomed.x) / zoomed.k) < 1e-6, "the point under the pointer stays put");
  assert.deepEqual(remembered().view, zoomed);
  stage.fire("pointerdown", { pointerId: 1, button: 0, clientX: 200, clientY: 200 });
  stage.fire("pointermove", { pointerId: 1, clientX: 260, clientY: 170 });
  assert.ok(stage.classes.has("is-panning"));
  stage.fire("pointerup", { pointerId: 1, clientX: 260, clientY: 170 });
  assert.ok(!stage.classes.has("is-panning"));
  assert.deepEqual(view(dom), { x: zoomed.x + 60, y: zoomed.y - 30, k: zoomed.k });
  assert.deepEqual(remembered().view, view(dom));
  const panned = view(dom);
  stage.fire("keydown", { key: "ArrowRight" });
  assert.equal(view(dom).x, panned.x - 60);
  stage.fire("keydown", { key: "ArrowUp" });
  assert.equal(view(dom).y, panned.y + 60);
  stage.fire("keydown", { key: "+" });
  assert.ok(view(dom).k > panned.k);
  stage.fire("keydown", { key: "-" });
  stage.fire("keydown", { key: "-" });
  assert.ok(view(dom).k < panned.k);
  stage.fire("keydown", { key: "0" });
  assert.deepEqual(view(dom), start);
  stage.fire("keydown", { key: "ArrowRight", ctrlKey: true });
  assert.deepEqual(view(dom), start);
  // Two fingers moving apart zoom in around the point between them.
  stage.fire("pointerdown", { pointerId: 1, clientX: 400, clientY: 300 });
  stage.fire("pointerdown", { pointerId: 2, clientX: 500, clientY: 300 });
  stage.fire("pointermove", { pointerId: 2, clientX: 600, clientY: 300 });
  assert.ok(view(dom).k > start.k * 1.9);
  stage.fire("pointerup", { pointerId: 2 });
  stage.fire("pointerup", { pointerId: 1 });
  assert.deepEqual(remembered().view, view(dom));
  dom.section().fire("click", { target: { closest: () => ({ dataset: { topo: "fit" } }) } });
  assert.deepEqual(view(dom), start);
  dom.section().fire("click", { target: { closest: () => ({ dataset: { topo: "zoom-in" } }) } });
  assert.equal(view(dom).k, Math.min(layout.ZOOM.max, start.k * 1.25));
  screen.stop();
});

test("screen: dragging a device places it and the place persists; a tap selects it", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const dom = fakeDom(), storage = fakeStorage();
  const api = async () => fixture();
  const screen = ui.createTopologyMap({ mount: () => dom.host, storage, workspace: () => "ws_a", api });
  screen.show();
  await settle();
  const stage = dom.part("stage"), { k } = view(dom);
  const [x, y] = placeOf(dom.part("world").innerHTML, "maple");
  stage.fire("pointerdown", { pointerId: 1, button: 0, clientX: 300, clientY: 300, target: on("maple") });
  stage.fire("pointermove", { pointerId: 1, clientX: 302, clientY: 301, target: on("maple") });
  assert.equal(storage.getItem("commonplace.topology.ws_a"), null, "a wobble is not a drag");
  stage.fire("pointermove", { pointerId: 1, clientX: 380, clientY: 250, target: on("maple") });
  assert.equal(storage.getItem("commonplace.topology.ws_a"), null, "not remembered before the drop");
  stage.fire("pointerup", { pointerId: 1, clientX: 380, clientY: 250 });
  const dropped = { x: Math.round(x + 80 / k), y: Math.round(y - 50 / k) };
  assert.deepEqual(JSON.parse(storage.getItem("commonplace.topology.ws_a")).placed, { maple: dropped });
  assert.equal(dom.section().events.length, 0, "a drag does not select");
  // A reload (a new screen over the same storage) draws the device where it was dropped.
  const reload = fakeDom();
  const second = ui.createTopologyMap({ mount: () => reload.host, storage, workspace: () => "ws_a", api });
  second.show();
  await settle();
  assert.deepEqual(placeOf(reload.part("world").innerHTML, "maple"), [dropped.x, dropped.y]);
  // The keyboard places a device too.
  reload.part("stage").fire("keydown", { key: "ArrowDown", shiftKey: true, target: on("maple") });
  assert.deepEqual(JSON.parse(storage.getItem("commonplace.topology.ws_a")).placed.maple, { x: dropped.x, y: dropped.y + 20 });
  // A cancelled drag puts the device back.
  stage.fire("pointerdown", { pointerId: 3, button: 0, clientX: 10, clientY: 10, target: on("gate") });
  stage.fire("pointermove", { pointerId: 3, clientX: 90, clientY: 90, target: on("gate") });
  stage.fire("pointercancel", { pointerId: 3 });
  assert.equal(JSON.parse(storage.getItem("commonplace.topology.ws_a")).placed.gate, undefined);
  // A tap selects and tells the rest of the app which device; Enter does the same.
  stage.fire("pointerdown", { pointerId: 4, button: 0, clientX: 10, clientY: 10, target: on("gate") });
  stage.fire("pointerup", { pointerId: 4, clientX: 10, clientY: 10 });
  assert.deepEqual(dom.section().events.map((event) => [event.type, event.detail.key]), [["topology:select", "gate"]]);
  assert.match(nodeTag(dom.part("world").innerHTML, "gate"), /aria-pressed="true"/);
  stage.fire("keydown", { key: "Enter", target: on("gate") });
  assert.equal(dom.section().events[1].detail.key, null);
  // Reset layout forgets the places.
  dom.section().fire("click", { target: { closest: () => ({ dataset: { topo: "reset" } }) } });
  assert.deepEqual(JSON.parse(storage.getItem("commonplace.topology.ws_a")).placed, {});
  assert.deepEqual(placeOf(dom.part("world").innerHTML, "maple"), [x, y]);
  screen.stop(); second.stop();
});

test("screen: a read that arrives during a drag waits for the drop", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const dom = fakeDom();
  let payload = fixture();
  const screen = ui.createTopologyMap({ mount: () => dom.host, storage: fakeStorage(), api: async () => payload });
  screen.show();
  await settle();
  const stage = dom.part("stage"), before = dom.part("world").innerHTML;
  stage.fire("pointerdown", { pointerId: 1, button: 0, clientX: 300, clientY: 300, target: on("maple") });
  stage.fire("pointermove", { pointerId: 1, clientX: 360, clientY: 300, target: on("maple") });
  payload = fixture({ gate: "down" });
  t.mock.timers.tick(5000);
  await settle();
  assert.equal(dom.part("world").innerHTML, before);
  stage.fire("pointerup", { pointerId: 1, clientX: 360, clientY: 300 });
  assert.match(linkTag(dom.part("world").innerHTML, "nimbus-cdn>gate:reverse_proxy"), /Gate end: Down/);
  screen.stop();
});

test("screen: an empty workspace shows the empty state and its button starts the work", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const dom = fakeDom();
  let started = 0;
  const screen = ui.createTopologyMap({ mount: () => dom.host, storage: fakeStorage(), api: async () => EMPTY, onStartWork: () => { started += 1; } });
  screen.show();
  await settle();
  assert.equal(dom.part("note").innerHTML, ui.emptyState(true));
  assert.equal(dom.part("route").disabled, true);
  dom.section().fire("click", { target: { closest: () => ({ dataset: { topo: "start" } }) } });
  assert.equal(started, 1);
  assert.deepEqual(dom.section().events.map((event) => event.type), ["topology:start-work"]);
  // Nothing to pan on an empty canvas, and a press on the note is left to its button.
  dom.part("stage").fire("pointerdown", { pointerId: 1, button: 0, clientX: 1, clientY: 1 });
  dom.part("stage").fire("pointermove", { pointerId: 1, clientX: 50, clientY: 50 });
  assert.equal(dom.part("world").attrs.transform, undefined);
  screen.stop();
  const plain = fakeDom();
  const off = ui.createTopologyMap({ mount: () => plain.host, storage: fakeStorage(), api: async () => EMPTY });
  off.show();
  await settle();
  assert.equal(plain.part("note").innerHTML, ui.emptyState(false));
  off.stop();
});

test("screen: a refusal is explained and not retried; a failed refresh keeps the last map", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const refused = fakeDom();
  let calls = 0;
  const denied = ui.createTopologyMap({ mount: () => refused.host, storage: fakeStorage(), api: async () => { calls += 1; throw Object.assign(new Error("Only the workspace owner can open the Compute map."), { status: 403 }); } });
  denied.show();
  await settle();
  assert.match(refused.part("note").innerHTML, /role="alert"><p>Only the workspace owner can open the Compute map\.<\/p><\/div>$/);
  t.mock.timers.tick(30000);
  await settle();
  assert.equal(calls, 1);

  const dom = fakeDom();
  let fail = true, reads = 0;
  const screen = ui.createTopologyMap({ mount: () => dom.host, storage: fakeStorage(), api: async () => { reads += 1; if (fail) throw new Error("Request failed (502)"); return fixture(); } });
  screen.show();
  await settle();
  assert.match(dom.part("note").innerHTML, /<p>Request failed \(502\)<\/p><button type="button" class="btn" data-topo="retry">Retry<\/button>/);
  fail = false;
  dom.section().fire("click", { target: { closest: () => ({ dataset: { topo: "retry" } }) } });
  await settle();
  assert.equal(dom.part("note").innerHTML, "");
  const shown = dom.part("world").innerHTML;
  assert.match(shown, /data-key="maple"/);
  fail = true;
  t.mock.timers.tick(5000);
  await settle();
  assert.equal(dom.part("world").innerHTML, shown);
  assert.equal(dom.part("note").innerHTML, "");
  assert.equal(dom.part("live").textContent, ui.MAP_COPY.notLive);
  fail = false;
  t.mock.timers.tick(5000);
  await settle();
  assert.equal(dom.part("live").textContent, "");
  assert.ok(reads >= 4);
  screen.stop();
});

test("screen: show() again starts clean and an old answer cannot draw into the new mount", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const dom = fakeDom();
  const waiting = [];
  const screen = ui.createTopologyMap({ mount: () => dom.host, storage: fakeStorage(), api: () => new Promise((resolve) => waiting.push(resolve)) });
  screen.show();
  screen.show();
  await settle();
  assert.equal(waiting.length, 2);
  waiting[0](fixture({ gate: "down" }));
  await settle();
  assert.equal(dom.part("world").innerHTML, "");
  waiting[1](fixture());
  await settle();
  assert.match(linkTag(dom.part("world").innerHTML, "nimbus-cdn>gate:reverse_proxy"), /Gate end: Degraded/);
  t.mock.timers.tick(5000);
  await settle();
  assert.equal(waiting.length, 3, "one poll loop, not two");
  screen.stop();
});

test("wiring: the canvas is mounted by the Deck Compute screen only, and its stylesheet is loaded", async () => {
  assert.match(await read("deck-compute.js"), /import \{[^}]*createTopologyMap[^}]*\} from '\.\/topology-map\.js';/);
  for (const name of ["app.js", "rail.js", "palette.js"]) assert.doesNotMatch(await read(name), /createTopologyMap|compute-map/, name);
  assert.match(await read("index.html"), /<link rel="stylesheet" href="\/app\/topology\.css">/);
  const css = await read("topology.css");
  for (const state of ui.STATES) assert.ok(css.includes(`.topo-dot.s-${state}`));
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/, "colours come from the Deck tokens");
});

test("screen: with no interval it never reads on its own, a host read arrives through refresh, select marks a device quietly", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const dom = fakeDom();
  let reads = 0;
  const screen = ui.createTopologyMap({ mount: () => dom.host, api: async () => { reads += 1; return fixture(); }, storage: fakeStorage(), interval: 0 });
  screen.show();
  await settle();
  assert.equal(reads, 1);
  t.mock.timers.tick(60000);
  await settle();
  assert.equal(reads, 1, "no poll loop");
  await screen.refresh();
  assert.equal(reads, 2);
  screen.select("maple");
  assert.match(dom.part("world").innerHTML, /class="topo-node[^"]* sel[^"]*" data-key="maple"/);
  assert.deepEqual(dom.section().events, [], "a host selection is not announced back");
  screen.select("maple");
  assert.match(dom.part("world").innerHTML, / sel[^"]*" data-key="maple"/, "selecting the open device again keeps it open");
  screen.stop();
});

test("link names avoid devices and each other, at the scale they are drawn", () => {
  const model = ui.mapModel(fixture());
  const positions = layout.autoLayout(model.nodes, model.links);
  const hits = (a, b) => a.l < b.r && a.r > b.l && a.t < b.b && a.b > b.t;
  for (const k of [1, 0.8]) {
    const places = layout.layoutLinks(model.links, positions, k);
    const boxes = model.links.map((l) => { const at = places.get(l.id).label, w = (l.method.length * 6.4 + 10) / k; return { id: l.id, l: at.x - w / 2, r: at.x + w / 2, t: at.y - 17 / k, b: at.y }; });
    let crowded = 0;
    for (const box of boxes) for (const p of Object.values(positions)) {
      const under = { l: p.x - 70 / k, r: p.x + 70 / k, t: p.y + 27 + 6 / k, b: p.y + 27 + 36 / k };
      if (hits(box, under) || hits(box, { l: p.x - 30, r: p.x + 30, t: p.y - 30, b: p.y + 30 })) crowded += 1;
    }
    assert.ok(crowded <= 5, `${crowded} link names still touch a device at ${k}`);
    for (let i = 0; i < boxes.length; i++) for (const other of boxes.slice(i + 1)) assert.ok(!hits(boxes[i], other) || (boxes[i].id.split(":")[0] === other.id.split(":")[0]), `${boxes[i].id} over ${other.id} at ${k}`);
  }
});
