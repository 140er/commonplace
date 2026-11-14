import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// Node has Event but no KeyboardEvent; the panel only needs its `key`.
globalThis.KeyboardEvent ??= class extends Event { constructor(type, init = {}) { super(type, init); this.key = init.key; } };
const detail = await import("../commonplace/web/static/topology-detail.js");
const map = await import("../commonplace/web/static/topology-map.js");
const { fixture } = await import("./topology_fixture.mjs");
const { nodeRead, routeRead, scriptedApi, ago } = await import("./topology_detail_fixture.mjs");
const read = (name) => readFile(new URL(`../commonplace/web/static/${name}`, import.meta.url), "utf8");
const settle = async () => { for (let i = 0; i < 12; i++) await new Promise((resolve) => setImmediate(resolve)); };
const HOSTILE = '<img src=x onerror=alert(1)>"';
const unescape = (html) => html.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&");

const payload = fixture();
const model = map.mapModel(payload);
const nodeOf = (key) => model.nodes.find((node) => node.key === key);
const tabs = (key, overrides = {}) => {
  const read_ = nodeRead(key, payload), answer = routeRead(key, payload), d = detail.detailModel(read_);
  const names = new Map(model.nodes.map((node) => [node.key, node.name]));
  return { d, physical: detail.physicalHTML(d), config: detail.configHTML(d, { links: model.links.filter((link) => !link.missing), key, answer, ...overrides }), agents: detail.agentsHTML(d), sites: detail.sitesHTML(d, names) };
};

// ---------- the read's shape ----------

test("model: anything that is not a node read is a device with nothing recorded", () => {
  for (const junk of [null, undefined, {}, { node: null }, { node: { found: false } }, { node: { found: true, fields: "x" }, sites: "no", agents: 7 }]) {
    const d = detail.detailModel(junk);
    assert.deepEqual([d.found && Object.keys(d.fields).length > 0, d.sites], [false, []]);
  }
  const d = detail.detailModel(nodeRead("maple", payload));
  assert.equal(d.found, true);
  assert.equal(d.fields.os.value, "Linux 6.8");
  assert.equal(d.sites.length, 1);
});

// ---------- Physical ----------

test("physical: a server shows hardware, OS and every resource with its source and age", () => {
  const html = tabs("maple").physical;
  assert.match(html, /Last observed 20m ago/);
  for (const [name, label, value] of [["hardware", "Hardware", "8-core workstation board"], ["os", "OS", "Linux 6.8"], ["resources.cpu", "CPU", "8-core x86"], ["resources.cores", "Cores", "8"], ["resources.memory_gb", "Memory", "32 GB"], ["resources.storage_gb", "Storage", "960 GB"]]) {
    const row = new RegExp(`data-field="${name.replace(".", "\\.")}"><dt>${label}</dt><dd><span class="td-val">${value}</span><span class="td-meta"[^>]*>Agent conn_scout · observed 20m ago`).exec(html);
    assert.ok(row, `row ${name}`);
  }
  assert.match(html, /“uname -sr”/);
  assert.doesNotMatch(html, /class="td-flag"/);
});

test("physical: a device with no hardware facts and an unrecorded device say so plainly", () => {
  assert.match(tabs("orchard-web").physical, /<p class="td-empty" data-empty>No hardware, OS or resources are recorded for this device\.<\/p>/);
  assert.match(tabs("shop-edge").physical, /Nothing is recorded for this device yet\. An agent can add it\./);
});

test("physical: expired values are marked stale and still say when they were last seen", () => {
  const html = tabs("old-nas").physical;
  assert.match(html, /td-row stale/);
  assert.match(html, /<span class="td-flag">stale<\/span>/);
  assert.match(html, /stale: last observed 40d ago/);
  assert.match(html, /Linux 5\.10/);
});

test("physical: a field another observer contests is flagged, and more observers are counted", () => {
  const read_ = nodeRead("maple", payload);
  read_.node.fields.os.contenders = 1;
  read_.node.fields.os.sources.push({ kind: "owner", id: "user_maya", observed_at: ago(5), seen_count: 1, evidence: "" });
  const html = detail.physicalHTML(detail.detailModel(read_));
  assert.match(html, /Agent conn_scout \+1 more/);
  assert.match(html, /1 disagreeing/);
});

// ---------- Config ----------

test("config: the facts that say how to connect, as rows and as copyable text", () => {
  const html = tabs("maple").config;
  for (const [label, value] of [["Tailscale name", "maple.example.ts.net"], ["ssh alias", "maple-ssh"], ["Runner id", "mach_0123456789abcdef"], ["Wings", "atlas, shared"]]) {
    assert.match(html, new RegExp(`<dt>${label}</dt><dd><span class="td-val">${value}</span>`));
  }
  assert.match(html, /Runner mach_0123456789abcdef · observed 3m ago/);
  const copy = unescape(/<pre class="td-copy" data-copy-text="facts" hidden>([\s\S]*?)<\/pre>/.exec(html)[1]);
  assert.match(copy, /^Tailscale name: maple\.example\.ts\.net$/m);
  assert.match(copy, /^ssh alias: maple-ssh$/m);
  assert.match(copy, /^Tailscale: from studio-mac$/m);
  assert.match(copy, /^relay: from pocket$/m);
  assert.match(copy, /^direct: from studio-mac$/m);
});

test("config: the route answer is topology_route's own words, shown and copyable unchanged", () => {
  const answer = routeRead("maple", payload);
  const html = tabs("maple").config;
  assert.ok(html.includes(`<p class="td-answer" data-answer>${answer.answer.replace(/'/g, "&#39;")}</p>`), "the answer sentence is verbatim");
  assert.match(html, /answer as of just now/);
  assert.match(html, /data-hop="studio-mac&gt;maple:tailscale"[^>]*>Studio Mac → Maple: Tailscale, up/);
  const text = detail.routeText(answer);
  assert.ok(text.startsWith(answer.answer));
  assert.match(text, /Way 1 \(up\):\n {2}Studio Mac → Maple: Tailscale, up/);
  const copied = unescape(/data-copy-text="route" hidden>([\s\S]*?)<\/pre>/.exec(html)[1]);
  assert.equal(copied, text);
});

test("config: a site's route is hosting, with an unknown hop said as unknown", () => {
  const html = tabs("shop.orchard.example").config;
  assert.match(html, /site &#39;shop\.orchard\.example&#39; is routed shop\.orchard\.example -&gt; nimbus-dns -&gt; shop-edge/);
  assert.match(html, /Nimbus DNS → shop-edge: proxy, unknown, not recorded/);
});

test("config: nothing recorded, no route answer and a refused answer each read as plain words", () => {
  assert.match(tabs("pocket").config, /No way to connect to this device is recorded\./);
  assert.match(tabs("shop-edge").config, /Nothing is recorded for this device yet/);
  assert.match(tabs("maple", { answer: null, routeFailure: detail.DETAIL_COPY.routeOff }).config, /The route answer is not available right now\./);
  assert.match(tabs("maple", { answer: null }).config, /Loading…/);
  assert.doesNotMatch(tabs("maple", { answer: null, routeFailure: "x" }).config, /data-copy="route"/);
});

// ---------- Agents ----------

test("agents: a runner machine lists connections, runs with their card and log, and the coordinator", () => {
  const html = tabs("maple").agents;
  assert.match(html, /maple-runner · enabled/);
  assert.match(html, /Poll connection: maple-runner \(machine\)[^<]*<span class="td-meta"> ?last used 1m ago · source: connection/);
  assert.match(html, /Worker connection: maple-runner \(worker\)/);
  assert.match(html, /data-run="job_a1"><a href="#\/card\/442">atlas #442<\/a> · started · thread thr_orchard_1/);
  assert.match(html, /updated 2m ago · source: start job/);
  assert.match(html, /log: <a href="https:\/\/logs\.example\.test\/run\/a1" target="_blank" rel="noopener noreferrer">/);
  assert.match(html, /data-run="job_a2"><a href="#\/card\/443">atlas #443<\/a> · queued/);
  assert.match(html, /data-coordinator="atlas"><a href="#\/board\/atlas">atlas<\/a> · lease held by conn_lead · 2 open steps/);
  assert.match(html, /data-thread="thr_builder_9"><a href="#\/card\/444">atlas #444<\/a> · thread thr_builder_9 · started/);
  assert.match(html, /source: checkpoint/);
  assert.doesNotMatch(html, /No agents are working/);
});

test("agents: a machine with nothing running, a device that is no runner and a gone machine say so", () => {
  const idle = tabs("birch").agents;
  assert.match(idle, /No runs are queued or running on this machine\./);
  assert.match(idle, /No agents are working on this device right now\./);
  assert.match(idle, /birch-runner \(worker\)[^<]*<span class="td-meta"> ?never used/);
  assert.match(tabs("studio-mac").agents, /^<p class="td-empty" data-empty>This device is not a runner machine, so no agents are recorded on it\.<\/p>$/);
  const gone = nodeRead("maple", payload); gone.agents = { applicable: false, reason: "machine_not_found" };
  assert.match(detail.agentsHTML(detail.detailModel(gone)), /not enrolled in this workspace/);
});

test("agents: a caller who may not see connections is told, and sees only the runs the read let through", () => {
  const read_ = nodeRead("maple", payload);
  read_.agents.connections = null; read_.agents.runs = [];
  const html = detail.agentsHTML(detail.detailModel(read_));
  assert.match(html, /<p class="td-empty" data-connections>Connections are shown to workspace owners\.<\/p>/);
  assert.doesNotMatch(html, /maple-runner \(machine\)|job_a1/);
});

test("agents: a log that is not an http link is shown as text, never as a link", () => {
  const read_ = nodeRead("maple", payload);
  read_.agents.runs[0].log_ref = "javascript:alert(1)";
  const html = detail.agentsHTML(detail.detailModel(read_));
  assert.match(html, /log: <code>javascript:alert\(1\)<\/code>/);
  assert.doesNotMatch(html, /href="javascript/);
});

// ---------- Sites ----------

test("sites: each way a node takes part in a site's route, with the hop and its freshness", () => {
  const through = tabs("nimbus-cdn").sites;
  assert.match(through, /data-site="orchard\.example"><div><strong>orchard\.example<\/strong> · Routed through this device/);
  assert.match(through, /hop 2 of 5: Nimbus DNS → Nimbus CDN \(proxy, up\)/);
  assert.match(through, /hop 3 of 5: Nimbus CDN → Gate \(reverse proxy, degraded\)/);
  assert.match(through, /Agent conn_scout · observed 15m ago/);
  assert.match(tabs("maple").sites, /· Hosted on this device/);
  assert.match(tabs("orchard-web").sites, /· Hosted on this device/);
  assert.match(tabs("orchard.example").sites, /<strong>orchard\.example<\/strong> · This website/);
});

test("sites: a hop with no facts says not recorded, and each site offers its route on the map", () => {
  const html = tabs("shop-edge").sites;
  assert.match(html, /hop 2 of 2: Nimbus DNS → shop-edge \(proxy, unknown, not recorded\)/);
  assert.match(html, /<button type="button" class="btn sm" data-td="route" data-site="shop\.orchard\.example">Show route on map<\/button>/);
});

test("sites: a device without any site route has a plain empty state", () => {
  assert.match(tabs("pocket").sites, /^<p class="td-empty" data-empty>No website is routed through or hosted on this device\.<\/p>$/);
});

// ---------- every kind, safety ----------

test("every node kind renders all four tabs from its own facts", () => {
  const seen = new Set();
  for (const node of model.nodes.filter((item) => !item.missing)) {
    seen.add(node.kind);
    const html = detail.panelHTML({ key: node.key, node, tab: "physical", detail: detail.detailModel(nodeRead(node.key, payload)), links: model.links, answer: routeRead(node.key, payload), routeFailure: "", names: new Map(), loading: false, failure: "" });
    assert.match(html, new RegExp(`<h2 class="td-title">${node.name.replace(/\./g, "\\.")}</h2>`));
    for (const { label } of detail.TABS) assert.match(html, new RegExp(`role="tab"[^>]*>${label}</button>`));
  }
  assert.deepEqual([...seen].sort(), ["cloud", "container", "mac", "pc", "phone", "router", "server", "vm", "website"]);
});

test("the panel is read-only and shows only the fields the store allows", () => {
  const read_ = nodeRead("maple", payload);
  read_.node.fields.password = { value: "hunter2-secret", stale: false, observed_at: ago(1), sources: [], contenders: 0 };
  read_.node.fields.api_key = { value: "sk-live-secret", stale: false, observed_at: ago(1), sources: [], contenders: 0 };
  const d = detail.detailModel(read_);
  const everything = [detail.physicalHTML(d), detail.configHTML(d, { links: [], key: "maple" }), detail.agentsHTML(d), detail.sitesHTML(d)].join("");
  assert.doesNotMatch(everything, /hunter2|sk-live/);
  const panel = detail.panelHTML({ key: "maple", node: nodeOf("maple"), tab: "config", detail: d, links: [], answer: null, routeFailure: "", names: new Map(), loading: false, failure: "" });
  assert.doesNotMatch(panel, /<input|<textarea|<select|contenteditable|<form/);
  assert.match(panel, /Read-only\. Changes go through agent proposals\./);
});

test("every value is escaped, whatever an observer wrote", () => {
  const read_ = nodeRead("maple", payload);
  read_.node.fields.os.value = HOSTILE;
  read_.node.fields.os.sources[0].id = HOSTILE;
  read_.node.fields.hardware.sources[0].evidence = HOSTILE;
  read_.node.fields.aliases.value = [HOSTILE];
  read_.agents.runs[0].board = HOSTILE; read_.agents.runs[0].thread_id = HOSTILE;
  read_.agents.connections[0].name = HOSTILE;
  read_.sites[0].site = HOSTILE;
  const answer = routeRead("maple", payload); answer.answer = HOSTILE;
  const d = detail.detailModel(read_);
  const html = [detail.physicalHTML(d), detail.configHTML(d, { links: [], key: "maple", answer }), detail.agentsHTML(d), detail.sitesHTML(d)].join("");
  assert.doesNotMatch(html, /<img/);
  assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt;&quot;"));
});

test("the panel loads nothing from another origin and holds no editing controls in its code", async () => {
  for (const name of ["topology-detail.js", "topology-detail.css"]) assert.doesNotMatch(await read(name), /https?:\/\/(?!logs\.example)|\/\/cdn|@import|url\(/, name);
  assert.doesNotMatch(await read("topology-detail.js"), /method: "(POST|PUT|PATCH|DELETE)"/);
});

test("the canvas mounts the panel with one import and one call", async () => {
  const canvas = await read("topology-map.js");
  assert.equal(canvas.split("createNodePanel").length - 1, 2);
  assert.match(canvas, /import \{ createNodePanel \} from "\.\/topology-detail\.js";/);
  assert.match(await read("index.html"), /<link rel="stylesheet" href="\/app\/topology-detail\.css">/);
});

// ---------- the screen ----------

function fakeScreen() {
  const doc = { hidden: false, activeElement: null };
  const select = { value: "", events: [], dispatchEvent(event) { this.events.push(event); } };
  const keyed = new Map();
  const handlers = {};
  const panels = [];
  doc.createElement = () => {
    const el = { hidden: true, innerHTML: "", attrs: {}, className: "", removed: false, handlers: {}, setAttribute(name, value) { el.attrs[name] = value; }, addEventListener(kind, fn, options) { el.handlers[kind] = fn; options?.signal?.addEventListener("abort", () => { delete el.handlers[kind]; }); }, remove() { el.removed = true; }, contains: () => false, after() {}, querySelector(selector) { const m = /data-copy-text="(\w+)"/.exec(selector); if (m) { const found = new RegExp(`data-copy-text="${m[1]}" hidden>([\\s\\S]*?)</pre>`).exec(el.innerHTML); return found ? { textContent: unescape(found[1]), hidden: true } : null; } if (selector === "[data-td-status]") return null; return { scrollTop: 0, focus() {} }; } };
    panels.push(el);
    return el;
  };
  const section = {
    isConnected: true, ownerDocument: doc, children: [],
    append(el) { this.children.push(el); },
    addEventListener(kind, fn, options) { handlers[kind] = fn; options?.signal?.addEventListener("abort", () => { delete handlers[kind]; }); },
    querySelector: (selector) => (selector === '[data-topo="route"]' ? select : null),
    querySelectorAll: () => [...keyed.values()],
  };
  const node = (key) => { const el = { dataset: { key }, events: [], dispatchEvent(event) { el.events.push(event); } }; keyed.set(key, el); return el; };
  const choose = (key) => handlers["topology:select"]?.({ detail: { key } });
  const panel = () => panels[0];
  const html = () => panel()?.innerHTML || "";
  const click = (dataset) => panel().handlers.click({ target: { closest: () => ({ dataset, after() {} }) } });
  const press = (key, target = null) => { let stopped = false, prevented = false; panel().handlers.keydown({ key, stopPropagation() { stopped = true; }, preventDefault() { prevented = true; }, target: { closest: () => target } }); return { stopped, prevented }; };
  return { doc, section, select, node, choose, panel, panels, html, click, press, handlers };
}
const open = (extra = {}, api = scriptedApi(payload)) => {
  const screen = fakeScreen();
  const copied = [];
  const view = detail.createNodePanel({ section: screen.section, api, model: () => model, interval: 5000, copy: async (text) => { copied.push(text); }, ...extra });
  return { ...screen, view, api, copied };
};

test("screen: nothing is mounted until a device is selected, then both reads run for it", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const s = open();
  assert.equal(s.panels.length, 0);
  s.choose("maple");
  assert.match(s.html(), /Loading…/);
  assert.equal(s.panel().hidden, false);
  t.mock.timers.tick(0);
  await settle();
  assert.deepEqual(s.api.calls, ["/api/topology/node?key=maple", "/api/topology/route?target=maple"]);
  assert.match(s.html(), /<h2 class="td-title">Maple<\/h2><p class="td-sub">Server · app host<\/p>/);
  assert.match(s.html(), /role="tab" id="td-tab-physical"[^>]*aria-selected="true"/);
  assert.match(s.html(), /Linux 6\.8/);
});

test("screen: tabs switch by click and by arrow keys, and the chosen tab stays for the next device", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const s = open();
  s.choose("maple"); t.mock.timers.tick(0); await settle();
  s.click({ tdTab: "config" });
  assert.match(s.html(), /id="td-tab-config"[^>]*aria-selected="true"/);
  assert.match(s.html(), /How to connect/);
  const tabTarget = (id) => ({ dataset: { tdTab: id } });
  assert.equal(s.press("ArrowRight", tabTarget("config")).prevented, true);
  assert.match(s.html(), /id="td-tab-agents"[^>]*aria-selected="true"/);
  s.press("End", tabTarget("agents"));
  assert.match(s.html(), /id="td-tab-sites"[^>]*aria-selected="true"/);
  s.press("ArrowRight", tabTarget("sites"));
  assert.match(s.html(), /id="td-tab-physical"[^>]*aria-selected="true"/);
  s.press("ArrowLeft", tabTarget("physical"));
  assert.equal(s.view.tab, "sites");
  s.click({ tdTab: "agents" });
  s.choose("birch"); t.mock.timers.tick(0); await settle();
  assert.equal(s.view.tab, "agents");
  assert.match(s.html(), /birch-runner/);
});

test("screen: Copy hands the facts, or the route answer, to the clipboard", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const s = open();
  s.choose("maple"); t.mock.timers.tick(0); await settle();
  s.click({ tdTab: "config" });
  s.click({ td: "copy", copy: "facts" });
  await settle();
  assert.match(s.copied[0], /^Tailscale name: maple\.example\.ts\.net$/m);
  s.click({ td: "copy", copy: "route" });
  await settle();
  assert.equal(s.copied[1], detail.routeText(routeRead("maple", payload)));
});

test("screen: a clipboard that refuses leaves the text open to select", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const s = open({ copy: async () => { throw new Error("denied"); } });
  s.choose("maple"); t.mock.timers.tick(0); await settle();
  s.click({ tdTab: "config" });
  s.click({ td: "copy", copy: "facts" });
  await settle();
  assert.equal(s.copied.length, 0);
});

test("screen: Show route on map sets the canvas's own route picker and fires its change", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const s = open();
  s.choose("nimbus-cdn"); t.mock.timers.tick(0); await settle();
  s.click({ tdTab: "sites" });
  s.click({ td: "route", site: "orchard.example" });
  assert.equal(s.select.value, "orchard.example");
  assert.deepEqual(s.select.events.map((event) => [event.type, event.bubbles]), [["change", true]]);
});

test("screen: close and Escape deselect the device on the canvas the way Enter on it does", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const s = open();
  const maple = s.node("maple");
  s.choose("maple"); t.mock.timers.tick(0); await settle();
  s.click({ td: "close" });
  assert.deepEqual(maple.events.map((event) => [event.type, event.key]), [["keydown", "Enter"]]);
  assert.equal(s.press("Escape").stopped, true);
  assert.equal(maple.events.length, 2);
  s.choose(null);
  assert.equal(s.panel().hidden, true);
  assert.equal(s.html(), "");
  assert.equal(s.view.key, null);
});

test("screen: reads again while the device stays selected, shows a change and stops with the screen", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const s = open();
  s.choose("maple"); t.mock.timers.tick(0); await settle();
  assert.equal(s.api.calls.length, 2);
  const first = s.html();
  t.mock.timers.tick(5000); await settle();
  assert.equal(s.api.calls.length, 4);
  assert.equal(s.html(), first);
  s.doc.hidden = true;
  t.mock.timers.tick(5000); await settle();
  assert.equal(s.api.calls.length, 4);
  s.doc.hidden = false;
  t.mock.timers.tick(5000); await settle();
  assert.equal(s.api.calls.length, 6);
  s.section.isConnected = false;
  t.mock.timers.tick(5000); await settle();
  t.mock.timers.tick(20000); await settle();
  assert.equal(s.api.calls.length, 6);
  assert.equal(s.panel().removed, true);
  assert.deepEqual(Object.keys(s.handlers), []);
});

test("screen: a late answer for a device that is no longer selected is dropped", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const waiting = [];
  const api = (path) => new Promise((resolve) => waiting.push({ path, resolve }));
  const s = open({}, api);
  s.choose("maple"); t.mock.timers.tick(0); await settle();
  assert.equal(waiting.length, 2);
  s.choose("birch"); t.mock.timers.tick(0); await settle();
  for (const item of waiting.slice(0, 2)) item.resolve(item.path.includes("/node") ? nodeRead("maple", payload) : routeRead("maple", payload));
  await settle();
  assert.doesNotMatch(s.html(), /Linux 6\.8/);
  assert.match(s.html(), /Loading…/);
});

test("screen: a refused read and a missing route answer each say so in words", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const refused = open({}, async () => { throw Object.assign(new Error("no"), { status: 403 }); });
  refused.choose("maple"); t.mock.timers.tick(0); await settle();
  assert.match(refused.html(), /You may not read the details of this device\./);
  const missing = open({}, async (path) => { if (path.includes("/route")) throw Object.assign(new Error("not found"), { status: 404 }); return nodeRead("maple", payload); });
  missing.choose("maple"); t.mock.timers.tick(0); await settle();
  missing.click({ tdTab: "config" });
  assert.match(missing.html(), /Tailscale name/);
  assert.match(missing.html(), /The route answer is not available right now\./);
  const broken = open({}, async () => { throw new Error("Request failed (502)"); });
  broken.choose("maple"); t.mock.timers.tick(0); await settle();
  assert.match(broken.html(), /The details could not be loaded\./);
});

test("screen: a device the map draws but the store does not know reads as not recorded", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const s = open();
  s.choose("shop-edge"); t.mock.timers.tick(0); await settle();
  assert.match(s.html(), /<h2 class="td-title">shop-edge<\/h2><p class="td-sub">Unknown device · not recorded<\/p>/);
  assert.match(s.html(), /Nothing is recorded for this device yet\./);
});

test("screen: the real canvas still selects and the panel follows its select event", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const s = fakeScreen();
  const frame = { isConnected: true, ownerDocument: s.doc };
  void frame;
  // The canvas fires `topology:select` on its root with detail.key; this is the whole contract.
  const view = detail.createNodePanel({ section: s.section, api: scriptedApi(payload), model: () => model, copy: async () => {} });
  s.choose("tower"); t.mock.timers.tick(0); await settle();
  assert.equal(view.key, "tower");
  assert.match(s.html(), /Windows 11/);
  s.choose(undefined);
  assert.equal(view.key, null);
});
