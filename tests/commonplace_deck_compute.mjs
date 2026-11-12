import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';

const STATIC = new URL('../commonplace/web/static/', import.meta.url);
const { fixture } = await import('./topology_fixture.mjs');
const { createMapCore, mapModel, MAP_PATH } = await import(new URL('topology-map.js', STATIC));
const layout = await import(new URL('topology-layout.js', STATIC));

// ---------- the runners fallback keeps every label readable ----------

const LABEL = 160, FIELD = { desktop: { width: 860, height: 620, columns: 6 }, phone: { width: 358, height: 440, columns: 2 } };
const touches = (s, r) => {
  // Liang-Barsky clipping; touching an edge counts as an intersection too.
  let low = 0, high = 1;
  const dx = s.x2 - s.x1, dy = s.y2 - s.y1;
  for (const [p, q] of [[-dx, s.x1 - r.x], [dx, r.x + r.width - s.x1], [-dy, s.y1 - r.y], [dy, r.y + r.height - s.y1]]) {
    if (p === 0) { if (q < 0) return false; } else { const t = q / p; if (p < 0) low = Math.max(low, t); else high = Math.min(high, t); if (low > high) return false; }
  }
  return true;
};
const overlaps = (a, b) => a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;

function fallbackScene(count, long, phone, compute) {
  const field = phone ? FIELD.phone : FIELD.desktop;
  const rows = Array.from({ length: count }, (_, i) => ({ id: `m${i}`, label: long ? 'A very long machine name WWWWWW 🧰 '.repeat(6) : `Machine ${i}`, enabled: i % 3 !== 2, binding_state: i % 3 === 1 ? 'unbound' : 'owner_attested' }));
  const core = createMapCore({ words: { up: 'Ready', degraded: 'Waiting for attestation', down: 'Down', unknown: 'Off' }, none: true, labels: LABEL, columns: field.columns });
  core.apply(compute.runnerGraph(rows));
  core.fit(field.width, field.height, { remember: false }, { pad: 24, reach: { x: LABEL / 2, top: 72, bottom: 72 } });
  return { core, field, scene: core.scene() };
}

for (const count of [1, 2, 5, 8, 12]) for (const long of [false, true]) for (const phone of [false, true]) {
  test(`fallback label geometry: hub with ${count} machines, ${long ? 'very long' : 'short'} names, ${phone ? 'phone' : 'desktop'} field`, async () => {
    const compute = await import(new URL('deck-compute.js', STATIC));
    const { core, field, scene } = fallbackScene(count, long, phone, compute);
    const { view, positions } = core, nodes = core.model.nodes;
    assert.equal(nodes.length, count + 1);
    // Each label has a plate in the card colour, drawn above every link, so no line or mark runs through a label.
    const plates = [...scene.matchAll(/data-key="([^"]+)"[^>]*transform="translate\(([-\d.]+) ([-\d.]+)\)"[\s\S]*?<rect class="topo-label-plate" x="([-\d.]+)" y="(-?\d+)" width="(\d+)" height="(\d+)"/g)].map(([, key, x, y, px, py, width, height]) => ({ key, x: +x + +px, y: +y + +py, width: +width, height: +height }));
    assert.equal(plates.length, count + 1, 'one plate per node');
    assert.ok(scene.indexOf('class="topo-label-plate"') > scene.indexOf('</g><g class="topo-nodes">'), 'plates are drawn after the links');
    for (const plate of plates) {
      const screen = { x: plate.x * view.k + view.x, y: plate.y * view.k + view.y, width: plate.width * view.k, height: plate.height * view.k };
      assert.ok(screen.x >= 0 && screen.y >= 0 && screen.x + screen.width <= field.width && screen.y + screen.height <= field.height, `${plate.key} inside the ${field.width}x${field.height} field`);
    }
    for (let i = 0; i < plates.length; i++) for (const other of plates.slice(i + 1)) assert.ok(!overlaps(plates[i], other), 'labels never overlap');
    if (long) assert.ok([...scene.matchAll(/<text class="topo-name"[^>]*>([^<]*)<\/text>/g)].slice(1).every(([, name]) => name.endsWith('…')));
    // Marks sit outside every label; a node's disc never covers another node.
    const places = core.links();
    // Only the marks that are drawn: one on each machine (top right of its disc), none on the hub and none on a link end.
    const marks = nodes.filter(node => node.status !== 'none').map(node => ({ x: positions[node.key].x + 24, y: positions[node.key].y - 24 }));
    assert.equal(marks.length, count);
    for (const link of core.model.links) assert.deepEqual(link.status, { from: 'none', to: 'none' });
    for (const mark of marks) for (const plate of plates) assert.ok(!(mark.x + 7 > plate.x && mark.x - 7 < plate.x + plate.width && mark.y + 7 > plate.y && mark.y - 7 < plate.y + plate.height), 'a status mark is never inside a label');
    for (const [key, at] of Object.entries(positions)) for (const [other, there] of Object.entries(positions)) if (key !== other) assert.ok(Math.hypot(at.x - there.x, at.y - there.y) > 60, 'discs apart');
    // A hub with a single row of machines needs no plate at all: no line even touches a label.
    if (count <= field.columns) for (const place of places.values()) for (const plate of plates) assert.ok(!touches(place, plate), `line touches ${plate.key}`);
    assert.deepEqual(scene, fallbackScene(count, long, phone, compute).scene, 'deterministic');
  });
}

const { escape: e } = await import(new URL('api.js', STATIC));
const compute = await import(new URL('deck-compute.js', STATIC));
const machines = [
  { id: 'build', label: 'Build box', enabled: true, binding_state: 'owner_attested', projects: ['atlas'], topics: ['build'], wings: ['project'], max_runtime_mode: 'approval-required', verified: false },
  { id: 'studio', label: 'Studio machine', enabled: true, binding_state: 'unbound' },
  { id: 'old', label: 'Old tower', enabled: false, binding_state: 'owner_attested' },
  { id: 'gone', label: 'Revoked machine', enabled: true, revoked_at: '2026-01-01' },
];
const EMPTY = { wing: 'topology', nodes: [], links: [], routes: [], status: { nodes: {}, links: {} } };
const flush = async () => { for (let i = 0; i < 24; i++) await new Promise(resolve => setImmediate(resolve)); };

// A page that records what the real canvas draws; markup is not parsed, regions are found by their attribute.
function element(doc) {
  const handlers = {}, parts = new Map(), classes = new Set();
  const node = {
    isConnected: true, ownerDocument: doc, dataset: {}, style: {}, attrs: {}, hidden: false, value: '', disabled: false, textContent: '', innerHTML: '', children: [],
    clientWidth: 860, clientHeight: 620, classes, handlers,
    classList: { toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)), add: name => classes.add(name), remove: name => classes.delete(name) },
    setAttribute(name, value) { node.attrs[name] = String(value); },
    addEventListener(kind, handler, options) { handlers[kind] = handler; options?.signal?.addEventListener('abort', () => { delete handlers[kind]; }); },
    removeEventListener(kind) { delete handlers[kind]; },
    dispatchEvent(event) { handlers[event.type]?.(event); },
    fire: (kind, event = {}) => handlers[kind]?.({ type: kind, preventDefault() {}, stopPropagation() {}, target: { closest: () => null }, ...event }),
    querySelector(selector) { if (!parts.has(selector)) parts.set(selector, element(doc)); return parts.get(selector); },
    querySelectorAll: () => [], append(child) { node.children.push(child); }, contains: () => false, remove() { node.removed = true; },
    setPointerCapture() {}, focus() {}, getBoundingClientRect: () => ({ left: 0, top: 0 }),
  };
  return node;
}
function fakeRoot() {
  const regions = new Map(), handlers = new Map(), classes = new Set(), panels = [];
  const doc = { activeElement: null, hidden: false, adoptedStyleSheets: [], createElement: () => { const panel = element(doc); panels.push(panel); return panel; } };
  const header = { hidden: false }, phoneBar = { hidden: false }, phoneTabs = { style: { position: '', bottom: '', left: '', right: '', zIndex: '' } };
  const root = {
    ownerDocument: doc, parentElement: { querySelector: selector => ({ '.head': header, '#phone-bar': phoneBar, '#phone-tabs': phoneTabs })[selector] },
    classList: { add: x => classes.add(x), remove: (...names) => names.forEach(x => classes.delete(x)), toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)) },
    addEventListener: (type, handler) => handlers.set(type, handler),
    removeEventListener: type => handlers.delete(type),
    querySelector(selector) { return regions.get(/data-compute-region="([^"]+)"/.exec(selector)?.[1]) || null; },
    set innerHTML(html) {
      this.html = html; regions.clear();
      for (const [, name] of html.matchAll(/data-compute-region="([^"]+)"/g)) regions.set(name, name === 'map' ? element(doc) : { innerHTML: '', textContent: '', hidden: /data-compute-region="empty"[^>]*hidden/.test(html) && name === 'empty' });
    },
  };
  const event = data => ({ target: { closest(selector) { return Object.keys(data).some(k => selector === `[data-${k.replace(/[A-Z]/g, c => '-' + c.toLowerCase())}]`) ? { dataset: data } : null; } } });
  return { root, doc, regions, handlers, classes, panels, header, phoneBar, phoneTabs, click: data => handlers.get('click')(event(data)) };
}
function harness({ bots = [{ id: 'general', kind: 'general' }], rows = machines, topology = EMPTY, error = null } = {}) {
  const f = fakeRoot(), calls = [], nav = [], data = { rows, bots, error, topology };
  const api = async (path, options) => {
    calls.push([path, options]);
    if (path === '/api/topology') { if (data.topology instanceof Error) throw data.topology; return data.topology; }
    if (path.startsWith('/api/topology/')) throw Object.assign(new Error('not wired'), { status: 404 });
    assert.ok(['/api/runners', '/api/bots'].includes(path), path);
    if (path === '/api/runners') { if (data.error) throw data.error; return { machines: data.rows }; }
    return { bots: data.bots };
  };
  const ui = compute.mount({ root: f.root, api, e, me: { name: 'Maya', workspace: 'Northwind' }, go: hash => nav.push(hash) });
  const map = () => f.regions.get('map').querySelector('[data-topology]');
  const part = name => map().querySelector(`[data-topo="${name}"]`);
  const nodes = () => [...(part('world').innerHTML.matchAll(/data-key="([^"]+)"/g))].map(m => m[1]);
  const chosen = () => /data-key="([^"]+)"[^>]*aria-pressed="true"/.exec(part('world').innerHTML)?.[1];
  // Choosing a node on the canvas with the keyboard, as the canvas's own keys do.
  const choose = key => part('stage').fire('keydown', { key: 'Enter', target: { closest: selector => selector === '[data-key]' ? { dataset: { key } } : null } });
  const detail = () => f.panels.at(-1)?.innerHTML || '';
  return { ...f, calls, nav, data, ui, map, part, nodes, chosen, choose, detail };
}

test('readiness is exactly enabled plus owner_attested, never verified or last-used time', () => {
  assert.equal(compute.machineState({ enabled: true, binding_state: 'owner_attested', verified: false }), 'ready');
  assert.equal(compute.machineState({ enabled: true, binding_state: 'unbound', verified: true, last_seen: 'now' }), 'waiting');
  assert.equal(compute.machineState({ enabled: false, binding_state: 'owner_attested' }), 'off');
});

test('topology read: the Deck screen draws the shared canvas, one sentence from the data, one detail view', async () => {
  const f = harness({ topology: fixture() }); await flush();
  assert.deepEqual(f.calls.filter(c => c[0] === '/api/topology').length, 1);
  assert.ok(!f.calls.some(c => c[0] === '/api/runners'), 'a topology with nodes needs no runner read');
  for (const key of ['maple', 'birch', 'studio-mac', 'orchard.example', 'nimbus-cdn']) assert.ok(f.nodes().includes(key), key);
  assert.match(f.regions.get('summary').textContent, /machines up/);
  assert.match(f.regions.get('summary').textContent, /2 sites/);
  assert.equal(f.regions.get('empty').hidden, true);
  assert.equal(f.part('stage').fire('keydown', {}), undefined);
  // The first machine is chosen, and its details are the topology panel with its four tabs.
  assert.equal(f.chosen(), 'maple');
  assert.equal(f.panels.length, 1);
  for (const tab of ['Physical', 'Config', 'Agents', 'Sites']) assert.match(f.detail(), new RegExp(`data-td-tab="${tab.toLowerCase()}"[^>]*>${tab}<`));
  assert.ok(f.calls.some(c => c[0].startsWith('/api/topology/node?key=maple')), 'the node read goes through the shell api');
  f.choose('birch'); await flush();
  assert.equal(f.chosen(), 'birch');
  assert.equal(f.panels.length, 1, 'still one detail view');
  assert.match(f.detail(), /Birch/);
  assert.doesNotMatch(f.regions.get('map').innerHTML, /dc-detail/);
  f.ui.stop();
});

test('the canvas options: no timers of its own, and refresh reads again through the screen', async () => {
  const f = harness({ topology: fixture() }); await flush();
  const before = f.calls.length;
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(f.calls.length, before, 'interval 0: nothing reads by itself');
  await f.ui.refresh(); await flush();
  assert.equal(f.calls.filter(c => c[0] === '/api/topology').length, 2);
  f.ui.stop();
});

test('empty or failed topology: the runners through the same canvas, in honest states only', async () => {
  for (const topology of [EMPTY, Object.assign(new Error('Request failed (502)'), { status: 502 })]) {
    const f = harness({ topology }); await flush();
    assert.deepEqual(f.nodes().sort(), ['build', 'commonplace-hub', 'old', 'studio']);
    const scene = f.part('world').innerHTML;
    // What a person can read: titles and labels, not class names.
    const words = [...scene.matchAll(/<(?:title|text)[^>]*>([^<]*)</g)].map(m => m[1]).join(' | ');
    for (const word of ['Commonplace', 'Build box', 'Studio machine', 'Old tower', 'Ready', 'Waiting for attestation', 'Off']) assert.ok(words.includes(word), word);
    assert.doesNotMatch(words, /Revoked machine|online|\bUp\b|Degraded|Unknown|\bDown\b|agents working|proxy|CDN|website|route/i);
    assert.match(f.map().innerHTML + f.regions.get('map').innerHTML, /Ready<\/li><li>[^]*Waiting for attestation<\/li><li>[^]*Off<\/li>/);
    assert.doesNotMatch(f.regions.get('map').innerHTML, /Up<\/li>|Degraded<\/li>/);
    assert.equal(f.regions.get('summary').textContent, '1 of 3 machines ready.');
    assert.equal((scene.match(/class="topo-dot /g) || []).length, 3, 'one mark per machine, none at the hub or on a link');
    f.ui.stop();
  }
});

test('selecting a runner node opens exactly one detail view with its Config from the runner row', async () => {
  const f = harness(); await flush();
  assert.equal(f.chosen(), 'build');
  assert.equal(f.panels.length, 1);
  const panel = f.panels[0];
  assert.match(f.detail(), /Build box/);
  assert.match(f.detail(), /Physical/);
  const pick = tab => panel.handlers.click({ target: { closest: () => ({ dataset: { tdTab: tab } }) } });
  pick('config');
  for (const word of ['atlas', 'project', 'approval-required', 'Attested by the owner, not verified']) assert.ok(f.detail().includes(word), word);
  pick('agents'); assert.match(f.detail(), /Running work is not reported/);
  pick('sites'); assert.match(f.detail(), /No sites are recorded/);
  assert.ok(!f.calls.some(c => c[0].startsWith('/api/topology/node')), 'a runner is described by its row, not read from the topology');
  f.choose('studio'); await flush();
  assert.equal(f.panels.length, 1);
  assert.match(f.detail(), /Studio machine/);
  pick('config'); assert.match(f.detail(), /Not attested, not verified/);
  f.choose('commonplace-hub'); await flush();
  pick('config'); assert.match(f.detail(), /No machine configuration is recorded for the hub/);
  f.ui.stop();
});

test('the choice survives a refresh, and a node that left takes its details with it', async () => {
  const f = harness(); await flush();
  f.choose('studio'); await flush();
  f.data.rows = [...machines, { id: 'new', label: 'New box', enabled: true, binding_state: 'owner_attested' }];
  await f.ui.refresh({ me: { name: 'Maya' } }); await flush();
  assert.equal(f.chosen(), 'studio');
  assert.ok(f.nodes().includes('new'));
  assert.match(f.detail(), /Studio machine/);
  f.data.rows = machines.filter(m => m.id !== 'studio');
  await f.ui.refresh(); await flush();
  assert.equal(f.chosen(), 'build', 'the default machine is chosen again');
  assert.match(f.detail(), /Build box/);
  f.ui.stop();
});

test('when the topology appears later the screen switches to it', async () => {
  const f = harness(); await flush();
  assert.ok(f.nodes().includes('commonplace-hub'));
  f.data.topology = fixture(); await f.ui.refresh(); await flush();
  assert.ok(f.nodes().includes('maple') && !f.nodes().includes('commonplace-hub'));
  assert.match(f.regions.get('summary').textContent, /machines up/);
  assert.equal(f.chosen(), 'maple');
  f.ui.stop();
});

test('real screen: title, shell header handling, cleanup', async () => {
  const f = harness(); await flush();
  assert.match(f.root.html, /<h1>Compute<\/h1>/);
  assert.equal(f.header.hidden, true); assert.equal(f.phoneBar.hidden, true); assert.equal(f.phoneTabs.style.position, 'fixed');
  f.ui.stop(); f.ui.stop();
  assert.equal(f.handlers.size, 0); assert.equal(f.header.hidden, false); assert.equal(f.classes.size, 0);
  assert.ok(f.calls.at(-1)[1].signal.aborted); assert.equal(f.phoneBar.hidden, false); assert.equal(f.phoneTabs.style.position, '');
  assert.equal(f.root.html, '');
});

test('a refusal is a plain owner-only sentence with no map or raw error, and no second read', async () => {
  const f = harness({ topology: Object.assign(new Error('internal detail'), { status: 403 }) }); await flush();
  assert.match(f.root.html, /Only workspace owners can see the machines/);
  assert.doesNotMatch(f.root.html, /<svg|internal detail|role="alert"/);
  assert.deepEqual(f.calls.map(c => c[0]), ['/api/topology']); f.ui.stop();
  const g = harness({ error: { status: 403, message: 'internal detail' } }); await flush();
  assert.match(g.root.html, /Only workspace owners can see the machines/);
  assert.doesNotMatch(g.root.html, /<svg|internal detail|role="alert"/);
  g.ui.stop();
});

test('empty workspace keeps just the hub, and unavailable general bot hides both handoffs', async () => {
  const f = harness({ rows: [], bots: [] }); await flush();
  assert.deepEqual(f.nodes(), ['commonplace-hub']);
  assert.match(f.root.html, /No machines yet/);
  assert.equal(f.regions.get('empty').hidden, false);
  assert.equal(f.regions.get('summary').textContent, '0 of 0 machines ready.');
  assert.match(f.regions.get('actions').innerHTML, /href="#\/admin">Add machine/);
  assert.doesNotMatch(f.regions.get('actions').innerHTML, /Add site|Let a bot/);
  f.ui.stop();
});

test('one-click handoffs store the draft and navigate; never send a message', async () => {
  const previous = globalThis.sessionStorage, saved = [];
  globalThis.sessionStorage = { setItem: (key, value) => saved.push([key, JSON.parse(value)]) };
  try {
    const f = harness(); await flush();
    f.click({ computeAction: 'site' }); f.click({ computeAction: 'map' });
    assert.deepEqual(saved, [['cmmn.deck.draft', { bot: 'general', text: 'Add a site to my map.' }], ['cmmn.deck.draft', { bot: 'general', text: 'Map my machines and sites.' }]]);
    assert.deepEqual(f.nav, ['#/bots/general', '#/bots/general']);
    assert.deepEqual(f.calls.map(c => c[0]), ['/api/topology', '/api/runners', '/api/bots']);
    f.ui.stop();
  } finally { globalThis.sessionStorage = previous; }
});

test('escaped server strings stay inert in the canvas and in the configuration', async () => {
  const attack = '<img src=x onerror="alert(1)">';
  const f = harness({ rows: [{ ...machines[0], id: attack, label: attack, projects: [attack], topics: [attack], wings: [attack], max_runtime_mode: attack }] }); await flush();
  f.panels[0].handlers.click({ target: { closest: () => ({ dataset: { tdTab: 'config' } }) } });
  for (const html of [f.part('world').innerHTML, f.detail()]) {
    assert.doesNotMatch(html, /<img|aria-label="<|<style|style="/);
    assert.match(html, /&lt;img/);
  }
  f.ui.stop();
});

test('late refresh and late stopped responses cannot replace the current screen', async () => {
  const f = fakeRoot(), pending = [];
  const api = (path, options) => path === '/api/bots' ? Promise.resolve({ bots: [] }) : path === '/api/topology' ? Promise.resolve(EMPTY) : new Promise(resolve => pending.push(resolve));
  const ui = compute.mount({ root: f.root, api, e });
  await flush();
  const next = ui.refresh(); await flush();
  pending[1]({ machines: [machines[1]] }); await next;
  pending[0]({ machines }); await flush();
  assert.match(f.regions.get('summary').textContent, /^0 of 1 machine ready\.$/);
  const last = ui.refresh(); await flush(); ui.stop(); pending[2]({ machines }); await last;
  assert.equal(f.root.html, '');
});

test('transient failures keep the map, while a later refusal clears machine data', async () => {
  const f = harness(); await flush();
  f.data.error = new Error('network'); await f.ui.refresh(); await flush();
  assert.ok(f.nodes().includes('build'));
  assert.match(f.regions.get('notice').textContent, /could not be loaded/);
  assert.doesNotMatch(f.root.html + f.regions.get('notice').textContent, /network/);
  f.data.error = { status: 403 }; await f.ui.refresh(); await flush();
  assert.doesNotMatch(f.root.html, /Build box|data-compute-region="map"/);
  f.data.error = null; await f.ui.refresh(); await flush();
  assert.ok(f.nodes().includes('build'));
  assert.equal(f.regions.get('summary').textContent, '1 of 3 machines ready.'); f.ui.stop();
});

test('one constructed stylesheet, all selectors scoped to the mounted root', async () => {
  const previous = globalThis.CSSStyleSheet, sheets = [];
  globalThis.CSSStyleSheet = class { replaceSync(css) { this.css = css; sheets.push(this); } };
  try {
    const a = harness(), b = harness(); await flush();
    assert.equal(sheets.length, 1);
    assert.equal(a.doc.adoptedStyleSheets[0], b.doc.adoptedStyleSheets[0]);
    const css = sheets[0].css.replace(/\/\*[\s\S]*?\*\//g, '');
    const rules = css.split('\n').filter(line => line.includes('{') && !line.startsWith('@media'));
    for (const rule of rules) for (const selector of rule.split('{')[0].split(',')) assert.match(selector.trim(), /^\.deck-compute(?:[ #.]|$)/);
    // Deck tokens only: the one place a hex colour may appear is a status mark, and there is none here.
    assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(/);
    a.ui.stop(); b.ui.stop();
  } finally { globalThis.CSSStyleSheet = previous; }
});

test('failed bot discovery leaves the real map and Add machine available', async () => {
  const f = fakeRoot();
  const ui = compute.mount({ root: f.root, e, api: async path => { if (path === '/api/bots') throw new Error('network'); if (path === '/api/topology') return EMPTY; return { machines }; } });
  await flush(); assert.match(f.regions.get('map').querySelector('[data-topology]').querySelector('[data-topo="world"]').innerHTML, /Build box/);
  assert.doesNotMatch(f.regions.get('actions').innerHTML, /Add site|Let a bot/);
  assert.match(f.regions.get('actions').innerHTML, /Add machine/); ui.stop();
});

test('one map: Deck resolves the old map address to Compute, the account menu has one entry, no duplicate files', async () => {
  const { resolveRoute } = await import(new URL('routes.js', STATIC));
  assert.equal(resolveRoute('#/compute-map', { boards: [], deck: true }), '#/compute');
  assert.equal(resolveRoute('#/compute-map', { boards: [] }), '#/compute-map', 'classic look unchanged');
  const { renderDeckBar, renderDeckTabs } = await import(new URL('deck.js', STATIC));
  const screens = { today: () => 0, compute: () => 0 };
  for (const markup of [renderDeckBar({ can_admin: true, screens, hash: '#/compute' }), renderDeckTabs({ screens })]) assert.doesNotMatch(markup, /compute-map|Compute map/);
  const { readdir } = await import('node:fs/promises');
  const files = await readdir(STATIC);
  assert.ok(!files.includes('deck-topology.js'), 'the old hub map is gone');
  assert.deepEqual(files.filter(name => /^(?:deck-)?topology.*\.(?:js|css)$/.test(name)).sort(), ['topology-detail.css', 'topology-detail.js', 'topology-icons.js', 'topology-layout.js', 'topology-map.js', 'topology.css']);
  const users = [];
  for (const name of files.filter(name => name.endsWith('.js'))) if ((await readFile(new URL(name, STATIC), 'utf8')).includes('topology-map.js')) users.push(name);
  assert.deepEqual(users.sort(), ['app.js', 'deck-compute.js', 'topology-detail.js'].filter(name => name !== 'topology-detail.js'));
  assert.match(await readFile(new URL('deck-compute.js', STATIC), 'utf8'), /createTopologyMap/);
  assert.ok(MAP_PATH && mapModel(EMPTY));
});
