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
const EMPTY = { wing: "topology", nodes: [], links: [], routes: [], status: { nodes: {}, links: {} } };
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
  for (const junk of [null, undefined, {}, { nodes: null }, { nodes: [null, { key: 7 }], links: [null, {}], routes: [null, {}] }]) {
    assert.deepEqual(ui.mapModel(junk), { nodes: [], links: [], routes: [] });
  }
});

// ---------- icons and scene ----------

test("icons: one distinct original drawing per node kind, no outside artwork", async () => {
  const kinds = ["pc", "mac", "phone", "vm", "server", "router", "cloud", "website", "container", "hub"];
  assert.deepEqual(Object.keys(KINDS).sort(), [...kinds, "unknown"].sort());
  assert.equal(new Set(Object.values(KINDS).map((kind) => kind.icon)).size, 11);
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
  assert.match(maple, /<text class="topo-name" y="45">Maple<\/text><text class="topo-role" y="60">app host<\/text>/);
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
  payload.nodes[5].values = { kind: "server", name: HOSTILE, role: HOSTILE };
  payload.links[0].method = HOSTILE;
  payload.routes[0].site = HOSTILE;
  const core = ui.createMapCore();
  core.apply(payload);
  assert.doesNotMatch(core.scene(), /<img/);
  assert.match(core.scene(), /&lt;img src=x onerror=alert\(1\)&gt;/);
});

test("scene: long names are cut for the label and kept whole for assistive text", () => {
  const payload = fixture();
  payload.nodes[5].values.name = "A".repeat(60);
  const core = ui.createMapCore();
  core.apply(payload);
  const maple = nodeTag(core.scene(), "maple");
  assert.ok(maple.includes(`<text class="topo-name" y="45">${"A".repeat(23)}…</text>`));
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
  without.routes = [];
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

test("fit shows every device inside the canvas", () => {
  const core = ui.createMapCore();
  core.apply(largeFixture(300));
  core.fit(1000, 700);
  for (const at of Object.values(core.positions)) {
    const x = at.x * core.view.k + core.view.x, y = at.y * core.view.k + core.view.y;
    assert.ok(x >= 0 && x <= 1000 && y >= 0 && y <= 700);
  }
  assert.deepEqual(layout.fitView([], 1000, 700), { x: 500, y: 350, k: 1 });
  assert.ok(layout.fitView([{ x: 5, y: 5 }], 1000, 700).k <= 1.25);
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
  const screen = ui.createTopologyMap({ mount: () => dom.host, storage: fakeStorage(), workspace: () => "ws_a", api: async (path) => { calls += 1; assert.equal(path, "/api/topology"); return payload; } });
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

test("wiring: the app mounts the map in one place and the account menu links to it for the owner", async () => {
  const app = await read("app.js");
  assert.match(app, /import \{ createTopologyMap \} from "\.\/topology-map\.js";/);
  assert.equal(app.split("createTopologyMap(").length - 1, 1);
  assert.match(app, /state\.base === "#\/compute-map"\) \{\n\s+if \(!background\) topologyScreen\(\);/);
  assert.match(await read("index.html"), /<link rel="stylesheet" href="\/app\/topology\.css">/);
  const rail = await import("../commonplace/web/static/rail.js");
  // Under Workspace in the account menu, on the desktop rail and in the phone's menu alike.
  for (const render of [rail.renderRail, rail.renderPhoneBar]) {
    assert.match(render({ can_admin: true }), /<a href="#\/console">Console<\/a><a href="#\/compute-map">Compute map<\/a><\/div>/);
    assert.doesNotMatch(render({ can_admin: false }), /compute-map/);
  }
  assert.match(app, /state\.base === "#\/compute-map" \? "map"/);
  const css = await read("topology.css");
  for (const state of ui.STATES) assert.ok(css.includes(`.topo-dot.s-${state}`));
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/, "colours come from the workbench tokens");
});

// ---------- looks for a host that draws its own graph (the Deck Compute screen) ----------

const RUNNERS = { wing: "topology", nodes: [{ key: "hub", values: { kind: "hub", name: "Commonplace", role: "Hub" } }, { key: "a", values: { kind: "server", name: "Build box", role: "Ready" } }], links: [{ id: "hub>a", from: "hub", to: "a", method: "runner" }], routes: [], status: { nodes: { hub: "none", a: "up" }, links: { "hub>a": { from: "none", to: "up" } } } };

test("look: 'none' is a status only when the host asks for it, and draws no mark", () => {
  assert.equal(ui.mapModel(RUNNERS).nodes[0].status, "unknown");
  const model = ui.mapModel(RUNNERS, { none: true });
  assert.deepEqual(model.nodes.map((node) => node.status), ["none", "up"]);
  const core = ui.createMapCore({ none: true, words: { up: "Ready", degraded: "Waiting", down: "Down", unknown: "Off" } });
  core.apply(RUNNERS);
  const scene = core.scene();
  const only = (key) => scene.split('<g class="topo-node ').find((part) => part.includes(`data-key="${key}"`));
  assert.doesNotMatch(only("hub"), /topo-dot|Status:/);
  assert.match(only("a"), /aria-label="Build box, Server, Ready\. Status: Ready\."/);
  assert.equal((linkTag(scene, "hub>a").split('<g class="topo-nodes">')[0].match(/class="topo-dot /g) || []).length, 1, "one mark, at the machine's end");
  assert.doesNotMatch(scene, /Status: Up|Hub end/);
});

test("look: a label width clips names to it and puts a plate behind every label; the classic look has none", () => {
  const payload = { ...RUNNERS, nodes: [{ key: "a", values: { kind: "server", name: "W".repeat(30), role: "Ready" } }], links: [], status: { nodes: {}, links: {} } };
  const classic = ui.createMapCore(); classic.apply(payload);
  assert.doesNotMatch(classic.scene(), /topo-label-plate/);
  assert.match(classic.scene(), new RegExp(`>${"W".repeat(23)}…<`));
  const deck = ui.createMapCore({ labels: 160 }); deck.apply(payload);
  assert.match(deck.scene(), /<g class="topo-label" transform="translate\(0 27\)"><rect class="topo-label-plate" x="-80" y="4" width="160" height="38" rx="8"\/><text class="topo-name" y="19">W{1,12}…<\/text>/);
});

test("look: a cap on a row's columns and a reach for the labels", () => {
  const nodes = Array.from({ length: 6 }, (_, i) => ({ key: `n${i}`, kind: "server" }));
  assert.equal(new Set(Object.values(layout.autoLayout(nodes, [], { columns: 2 })).map((at) => at.y)).size, 3);
  assert.equal(new Set(Object.values(layout.autoLayout(nodes, [])).map((at) => at.y)).size, 1);
  const points = [{ x: 0, y: 0 }, { x: 500, y: 0 }];
  const plain = layout.fitView(points, 600, 400), wide = layout.fitView(points, 600, 400, 24, { x: 80, top: 34, bottom: 72 });
  assert.ok(wide.k * (500 + 160) <= 600 - 48 + 1e-6);
  assert.deepEqual(layout.fitView(points, 600, 400), plain);
});

test("screen: interval 0 never reads by itself, refresh() reads again, select(key) is quiet and not a toggle", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const dom = fakeDom();
  let reads = 0;
  const screen = ui.createTopologyMap({ mount: () => dom.host, storage: fakeStorage(), interval: 0, api: async () => { reads += 1; return fixture(); } });
  screen.show();
  await settle();
  t.mock.timers.tick(60000);
  await settle();
  assert.equal(reads, 1);
  await screen.refresh();
  assert.equal(reads, 2);
  const events = [];
  dom.section().dispatchEvent = (event) => { events.push(event.detail.key); };
  assert.equal(screen.select("nope"), false);
  assert.equal(screen.select("maple"), true);
  assert.equal(screen.select("maple"), true, "choosing the chosen node keeps it chosen");
  assert.deepEqual(events, ["maple", "maple"]);
  assert.match(nodeTag(dom.part("world").innerHTML, "maple"), /aria-pressed="true"/);
  screen.stop();
  assert.equal(screen.select("maple"), false);
});

test("screen: a preselected node is chosen once, and a node that leaves the map is announced", async () => {
  const dom = fakeDom(), events = [];
  let payload = fixture();
  const screen = ui.createTopologyMap({ mount: () => dom.host, storage: fakeStorage(), interval: 0, look: { preselect: (model) => model.nodes.find((node) => node.kind === "mac" || node.kind === "pc")?.key }, api: async () => payload });
  screen.show();
  dom.section().dispatchEvent = (event) => { events.push(event.detail.key); };
  await settle();
  assert.deepEqual(events, ["studio-mac"]);
  payload = { ...fixture(), nodes: fixture().nodes.filter((node) => node.key !== "studio-mac"), links: [] };
  await screen.refresh();
  assert.deepEqual(events.slice(-2), [null, "tower"], "the vanished node is announced, then the next machine is chosen");
  screen.stop();
});
