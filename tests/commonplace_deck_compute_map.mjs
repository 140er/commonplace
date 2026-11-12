import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

const STATIC = new URL('../commonplace/web/static/', import.meta.url);
const read = name => readFile(new URL(name, STATIC), 'utf8');
const { escape: e } = await import(new URL('api.js', STATIC));
const compute = await import(new URL('deck-compute.js', STATIC));
const { fixture } = await import('./topology_fixture.mjs');

const machine = (id, label, extra = {}) => ({ id, label, enabled: true, binding_state: 'owner_attested', projects: ['atlas'], topics: ['build'], wings: ['project'], max_runtime_mode: 'approval-required', ...extra });
const machines = [machine('build', 'Build box'), machine('studio', 'Studio machine', { binding_state: 'unbound' }), machine('loose', 'Loose runner')];
// The store ties a node to a runner machine through runner_id; the fixture's nodes carry none.
function topology() {
  const data = fixture();
  for (const [key, runner] of [['maple', 'build'], ['birch', 'studio']]) data.graph.nodes.find(n => n.key === key).values.runner_id = runner;
  return data;
}
const EMPTY = { graph: { wing: 'topology', nodes: [], links: [], routes: [] }, status: { nodes: {}, links: {} } };

// A DOM just deep enough for the real canvas module: every selector yields one cached child.
function fakeRoot() {
  const handlers = new Map(), classes = new Set(), doc = { activeElement: null, adoptedStyleSheets: [], hidden: false };
  const make = () => {
    const parts = new Map(), own = new Map();
    const el = {
      isConnected: true, ownerDocument: doc, dataset: {}, style: {}, attrs: {}, hidden: false, value: '', disabled: false, textContent: '', innerHTML: '',
      clientWidth: 860, clientHeight: 620, classList: { toggle() {}, add() {}, remove() {} },
      setAttribute(name, value) { el.attrs[name] = String(value); },
      addEventListener(type, handler) { own.set(type, handler); },
      removeEventListener(type) { own.delete(type); },
      querySelector(selector) { if (!parts.has(selector)) parts.set(selector, make()); return parts.get(selector); },
      querySelectorAll: () => [], dispatchEvent() {}, setPointerCapture() {}, focus() {}, getBoundingClientRect: () => ({ left: 0, top: 0 }),
    };
    return el;
  };
  const regions = new Map();
  const root = make();
  Object.assign(root, {
    parentElement: { querySelector: () => null },
    classList: { add: x => classes.add(x), remove: x => classes.delete(x) },
    addEventListener: (type, handler) => handlers.set(type, handler),
    removeEventListener: type => handlers.delete(type),
    querySelector(selector) {
      const name = /data-compute-region="([^"]+)"/.exec(selector)?.[1];
      if (!name) return null;
      return /data-compute-region="field"/.test(this.html || '') || name !== 'field' ? (regions.get(name) || null) : null;
    },
  });
  Object.defineProperty(root, 'innerHTML', {
    get() { return this.html || ''; },
    set(html) {
      this.html = html; regions.clear();
      for (const [, name] of html.matchAll(/data-compute-region="([^"]+)"/g)) regions.set(name, make());
    },
  });
  const event = (data, key) => ({ key, prevented: false, preventDefault() { this.prevented = true; }, target: { closest(selector) { return Object.keys(data).some(k => selector === `[data-${k.replace(/[A-Z]/g, c => '-' + c.toLowerCase())}]`) ? { dataset: data } : null; } } });
  return { root, regions, handlers, classes, click: data => handlers.get('click')(event(data)), select: key => handlers.get('topology:select')({ detail: { key } }) };
}
const flush = async () => { for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve)); };
function harness({ map = topology(), rows = machines } = {}) {
  const f = fakeRoot(), calls = [], data = { map, rows };
  const api = async (path, options) => {
    calls.push(path);
    if (path === '/api/runners') return { machines: data.rows };
    if (path === '/api/bots') return { bots: [{ id: 'general', kind: 'general' }] };
    assert.equal(path, '/api/topology/map');
    if (data.map instanceof Error) throw data.map;
    return data.map;
  };
  const ui = compute.mount({ root: f.root, api, e, me: { name: 'Maya', workspace: 'Northwind' }, go() {} });
  return { ...f, calls, data, ui };
}
const world = f => f.regions.get('field').querySelector('[data-topology]').querySelector('[data-topo="world"]').innerHTML;

test('the topology store is drawn on the canvas: machines, sites and links from the route', async () => {
  const f = harness(); await flush();
  assert.ok(f.calls.includes('/api/topology/map'));
  assert.match(f.regions.get('field').innerHTML, /data-topology/);
  assert.doesNotMatch(f.regions.get('field').innerHTML, /dc-topology/);
  const scene = world(f);
  for (const word of ['Maple', 'Birch', 'orchard.example', 'Nimbus CDN']) assert.ok(scene.includes(word), word);
  assert.match(scene, /data-link="maple&gt;birch:hosts"/);
  assert.match(scene, /Status: Up/);
  assert.match(scene, /Status: Degraded/);
  f.ui.stop();
});

test('a missing route (404), an empty graph and a failing read all fall back to the runners layout', async () => {
  for (const map of [Object.assign(new Error('Not found'), { status: 404 }), EMPTY, new Error('network'), null, { graph: null }]) {
    const f = harness({ map }); await flush();
    const html = f.regions.get('field').innerHTML;
    assert.match(html, /dc-topology/, String(map));
    for (const word of ['Build box', 'Studio machine']) assert.ok(html.includes(word));
    assert.doesNotMatch(html, /data-topology/);
    assert.equal(f.regions.get('notice').textContent, '', 'no error flash');
    assert.doesNotMatch(f.root.html, /Maple|orchard\.example|role="alert"/);
    f.ui.stop();
  }
});

test('selecting a node tied to a runner opens that runner\'s detail with the existing tabs', async () => {
  const f = harness(); await flush();
  f.select('maple');
  let detail = f.regions.get('detail').innerHTML;
  assert.match(detail, /Build box/); assert.match(detail, /Ready/);
  assert.match(detail, /data-compute-tab="Config"/);
  f.click({ computeTab: 'Config' });
  detail = f.regions.get('detail').innerHTML;
  for (const word of ['atlas', 'approval-required', 'Attested by the owner, not verified']) assert.ok(detail.includes(word), word);
  f.ui.stop();
});

test('a node without a runner shows only what the store says', async () => {
  const f = harness(); await flush();
  f.select('gate');
  const detail = f.regions.get('detail').innerHTML;
  assert.match(detail, /Gate/); assert.match(detail, /edge proxy/); assert.match(detail, /Router or edge/);
  assert.match(detail, /Degraded|Up|Unknown/);
  assert.doesNotMatch(detail, /data-compute-tab|Attested|Ready|Waiting for attestation|projects/i);
  f.ui.stop();
});

test('every machine is also in a plain list with its whole name, and the list selects', async () => {
  const long = 'A very long machine name that used to truncate into nothing on a phone';
  const f = harness({ rows: [machine('build', long), ...machines.slice(1)] }); await flush();
  const list = f.regions.get('list').innerHTML;
  assert.ok(list.includes(long));
  assert.match(list, /data-compute-pick="node:maple"/);
  assert.match(list, /Loose runner/, 'a runner the store has no node for is still listed');
  f.click({ computePick: 'runner:loose' });
  assert.match(f.regions.get('detail').innerHTML, /Loose runner/);
  f.ui.stop();
});

test('the legend names the four statuses and stays on the canvas frame', async () => {
  const { renderFrame } = await import(new URL('topology-map.js', STATIC));
  const frame = renderFrame();
  for (const word of ['Up', 'Degraded', 'Down', 'Unknown']) assert.ok(frame.includes(`</svg>${word}</li>`), word);
});

test('there is exactly one Compute map: no second route, menu, rail or palette entry', async () => {
  for (const name of ['app.js', 'rail.js', 'palette.js', 'index.html', 'deck.js']) assert.doesNotMatch(await read(name), /compute-map/, name);
  assert.match(await read('index.html'), /<link rel="stylesheet" href="\/app\/topology\.css">/);
  assert.match(await read('deck-compute.js'), /createTopologyMap/);
});

test('the canvas stylesheet only restyles its own classes, in the Deck tokens', async () => {
  const css = (await read('topology.css')).replace(/\/\*[\s\S]*?\*\//g, '');
  const rules = css.split('}').map(r => r.split('{')[0].trim()).filter(Boolean).filter(s => !s.startsWith('@media'));
  for (const selector of rules.flatMap(r => r.split(','))) assert.match(selector.trim(), /^(?::root\[data-ui="deck"\] )?(?:\.topo[a-z-]*)/, selector);
  assert.doesNotMatch(css, /border(?!-radius)[^;]*\d+px solid|box-shadow|gradient|#[0-9a-fA-F]{3,8}\b/, 'no borders, shadows, gradients or raw colours');
  assert.doesNotMatch(css, /\.topo-dot\.s-degraded[^}]*var\(--wait/, 'amber means waits on you, not degraded');
});
