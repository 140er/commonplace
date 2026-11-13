import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';

const STATIC = new URL('../commonplace/web/static/', import.meta.url);
const { fixture } = await import('./topology_fixture.mjs');
const { createMapCore, mapModel, MAP_PATH } = await import(new URL('topology-map.js', STATIC));
const layout = await import(new URL('topology-layout.js', STATIC));

// ---------- links and labels: the fallback graphs and the topology case, desktop and phone ----------

const { createTopologyMap, K_LEAST } = await import(new URL('topology-map.js', STATIC));
const { DISC, plateOf, routeLinks } = await import(new URL('topology-route.js', STATIC));
const { largeFixture } = await import('./topology_fixture.mjs');
const FIELD = { desktop: { width: 860, height: 600 }, phone: { width: 358, height: 494 } };
const WORDS = { up: 'Ready', degraded: 'Waiting for attestation', down: 'Down', unknown: 'Off' };
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
const segmentDistance = (p, a, b) => {
  const dx = b.x - a.x, dy = b.y - a.y, span = dx * dx + dy * dy;
  const t = span ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / span)) : 0;
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
};
const fallbackRows = (count, long) => Array.from({ length: count }, (_, i) => ({ id: `m${i}`, label: long ? 'A very long machine name WWWWWW 🧰 '.repeat(6) : `Machine ${i}`, enabled: i % 3 !== 2, binding_state: i % 3 === 1 ? 'unbound' : 'owner_attested' }));

/** What the Deck screen builds: the same options as its canvas, a row as wide as the card holds. */
function deckCore(compute, payload, phone, runners, machines = null) {
  const base = phone ? compute.METRICS.phone : compute.METRICS.desktop, field = phone ? FIELD.phone : FIELD.desktop;
  const metrics = { ...base, columns: compute.layoutColumns(field.width, base, machines) };
  const core = createMapCore({ labels: metrics.labels, layout: 'layered', metrics, route: true, ...(runners ? { words: WORDS, none: true } : {}) });
  core.apply(payload);
  const view = core.fit(field.width, field.height, { remember: false }, { pad: metrics.pad });
  return { core, metrics, field, view };
}

/** Every link is one continuous line between its two devices that touches no other device and no label; labels
 * do not meet; no disc or label is cut by the card's edge; the card is as tall as the graph with its padding. */
function assertGeometry({ core, metrics, field, view }) {
  const { model, positions } = core, places = core.links(), boxes = new Map(core.boxes.map(box => [box.key, box]));
  const plates = model.nodes.map(node => ({ key: node.key, ...plateOf(boxes.get(node.key), positions[node.key], metrics.labels, K_LEAST) }));
  assert.equal(places.size, model.links.length);
  for (const link of model.links) {
    const place = places.get(link.id);
    const points = place.points || [{ x: positions[link.from].x, y: positions[link.from].y }, { x: positions[link.to].x, y: positions[link.to].y }];
    if (place.points) for (const [point, key] of [[points[0], link.from], [points.at(-1), link.to]]) assert.ok(Math.abs(Math.hypot(point.x - positions[key].x, point.y - positions[key].y) - DISC) < 1, `${link.id} meets ${key}`);
    for (let i = 1; i < points.length; i++) {
      const [a, b] = [points[i - 1], points[i]];
      assert.ok(Math.hypot(b.x - a.x, b.y - a.y) > 0, 'no empty segment');
      for (const node of model.nodes) if (place.points || (node.key !== link.from && node.key !== link.to)) assert.ok(segmentDistance(positions[node.key], a, b) >= DISC - 0.5, `${link.id} passes through ${node.key}`);
      for (const plate of plates) assert.ok(!touches({ x1: a.x, y1: a.y, x2: b.x, y2: b.y }, plate), `${link.id} crosses the label of ${plate.key}`);
    }
    if (place.from) for (const dot of [place.from, place.to]) for (const plate of plates) assert.ok(!(dot.x + 7 > plate.x && dot.x - 7 < plate.x + plate.width && dot.y + 7 > plate.y && dot.y - 7 < plate.y + plate.height), `a status mark sits in the label of ${plate.key}`);
  }
  for (let i = 0; i < plates.length; i++) for (const other of plates.slice(i + 1)) assert.ok(!overlaps(plates[i], other), `labels of ${plates[i].key} and ${other.key} overlap`);
  const scene = core.scene();
  assert.equal((scene.match(/class="topo-label-plate"/g) || []).length, model.nodes.length, 'a plate behind every label');
  assert.ok(scene.indexOf('class="topo-label-plate"') > scene.indexOf('<g class="topo-nodes">'), 'plates are drawn after the links');
  // The card: never a scale below what the plates were laid out for, nothing cut by its edge, tall enough for the whole graph.
  assert.ok(view.k >= K_LEAST - 1e-9 && view.k <= 1, `scale ${view.k}`);
  let lowest = 0;
  for (const node of model.nodes) {
    const at = positions[node.key], screen = plateOf(boxes.get(node.key), at, metrics.labels, view.k);
    const plate = { left: screen.x * view.k + view.x, right: (screen.x + screen.width) * view.k + view.x, top: screen.y * view.k + view.y, bottom: (screen.y + screen.height) * view.k + view.y };
    const disc = { left: at.x * view.k + view.x - DISC * view.k, right: at.x * view.k + view.x + DISC * view.k, top: at.y * view.k + view.y - DISC * view.k, bottom: at.y * view.k + view.y + DISC * view.k };
    for (const [what, box] of [['label', plate], ['disc', disc]]) {
      assert.ok(box.left >= metrics.pad - 1 && box.right <= field.width - metrics.pad + 1, `the ${what} of ${node.key} is cut by the card's side (${Math.round(box.left)}..${Math.round(box.right)} of ${field.width})`);
      assert.ok(box.top >= metrics.pad - 1 && box.bottom <= view.height - metrics.pad + 1, `the ${what} of ${node.key} is cut by the card's top or bottom (${Math.round(box.top)}..${Math.round(box.bottom)} of ${view.height})`);
    }
    lowest = Math.max(lowest, plate.bottom, disc.bottom);
  }
  assert.ok(view.height >= lowest + metrics.pad - 1, `card ${view.height} is shorter than the graph ${lowest} + ${metrics.pad}`);
}
const tspans = (scene, name) => [...scene.matchAll(new RegExp(`<text class="${name}"[^>]*>(.*?)</text>`, 'g'))].map(m => [...m[1].matchAll(/<tspan[^>]*>([^<]*)<\/tspan>/g)].map(t => t[1]));

for (const count of [1, 2, 5, 8, 12]) for (const long of [false, true]) for (const phone of [false, true]) {
  test(`fallback links and labels: hub with ${count} machines, ${long ? 'very long' : 'short'} names, ${phone ? 'phone' : 'desktop'} field`, async () => {
    const compute = await import(new URL('deck-compute.js', STATIC));
    const rows = fallbackRows(count, long), made = deckCore(compute, compute.runnerGraph(rows), phone, true, count);
    assertGeometry(made);
    const scene = made.core.scene();
    assert.equal(made.core.model.nodes.length, count + 1);
    if (long) assert.ok(tspans(scene, 'topo-name').slice(1).every(lines => lines.at(-1).endsWith('…') && lines.length <= 2));
    assert.equal(scene, deckCore(compute, compute.runnerGraph(rows), phone, true, count).core.scene(), 'deterministic');
    // A row holds as many devices as the card fits: five on a desktop, two on a phone; a short last row stays centred.
    const perRow = phone ? 2 : count <= 5 ? 5 : 4, ys = made.core.model.nodes.filter(n => n.kind !== 'hub').map(n => made.core.positions[n.key].y);
    assert.equal(Math.max(...[...new Set(ys)].map(y => ys.filter(v => v === y).length)), Math.min(count, perRow));
  });
}

for (const phone of [false, true]) test(`topology links and labels: ${phone ? 'phone' : 'desktop'} field, the fixture's links run between devices, around labels, and are whole`, async () => {
  const compute = await import(new URL('deck-compute.js', STATIC));
  const made = deckCore(compute, fixture(), phone, false);
  assertGeometry(made);
  const scene = made.core.scene();
  assert.equal((scene.match(/<path class="topo-route" d="M[^"]*"\/><text class="topo-method"/g) || []).length + (scene.match(/<line /g) || []).length, made.core.model.links.length, 'every link is one continuous line');
  // Every device of the fixture is in the card, and none of its texts is cut with an ellipsis when it wraps.
  assert.equal(made.core.model.nodes.length, 14);
  if (phone) for (const lines of [...tspans(scene, 'topo-name'), ...tspans(scene, 'topo-role')]) assert.ok(lines.every(line => !line.endsWith('…')), lines.join(' '));
  if (phone) {
    assert.deepEqual(tspans(scene, 'topo-name').find(lines => lines.join('').startsWith('shop.orchard')), ['shop.orchard.', 'example']);
    assert.ok(tspans(scene, 'topo-role').some(lines => lines.join(' ').startsWith('edge proxy') && lines.length === 2));
  }
});

// The router is also used independently of the canvas's clear-centreline shortcut.
const adjacentNodes = [{ key: 'a', kind: 'cloud', sub: true }, { key: 'b', kind: 'cloud', sub: true }];
const adjacentPositions = { a: { x: 0, y: 0 }, b: { x: 186, y: 0 } };
const pairLinks = [{ id: 'first', from: 'a', to: 'b' }, { id: 'second', from: 'b', to: 'a' }];

for (const reverse of [false, true]) for (const k of [0.85, 1]) test(`adjacent duplicate links use short parallel lanes at scale ${k}, reverse ${reverse}`, () => {
  const links = reverse ? pairLinks : pairLinks.map(link => ({ ...link, from: 'a', to: 'b' }));
  const routes = routeLinks(links, adjacentNodes, adjacentPositions, 160, k);
  const lanes = links.map(link => routes.get(link.id));
  for (const i of [1, 0]) {
    const lane = lanes[i];
    assert.equal(lane.points.length, 2, 'a free adjacent lane has no detour');
    const [a, b] = lane.points;
    assert.equal(a.y, b.y, 'constant offset along the entire stroke');
    assert.ok(Math.hypot(b.x - a.x, b.y - a.y) <= 186, 'no longer than the straight distance');
    const link = links[i];
    for (const [point, key] of [[a, link.from], [b, link.to]]) assert.ok(Math.abs(Math.hypot(point.x - adjacentPositions[key].x, point.y) - DISC) < 0.1, 'ends meet the disc');
    for (const node of adjacentNodes) {
      const plate = plateOf(node, adjacentPositions[node.key], 160, k);
      assert.ok(!touches({ x1: a.x, y1: a.y, x2: b.x, y2: b.y }, plate), 'stroke clears labels');
      for (const dot of [lane.from, lane.to]) assert.ok(!overlaps({ x: dot.x - 7, y: dot.y - 7, width: 14, height: 14 }, plate), 'status marks clear labels');
    }
  }
  assert.equal(Math.abs(lanes[1].points[0].y - lanes[0].points[0].y), 16, 'fixed separation, including reverse-direction links');
  assert.deepEqual(routeLinks(links, adjacentNodes, adjacentPositions, 160, k), routes, 'deterministic');
});

test('single links and non-adjacent duplicate routes retain their geometry', () => {
  const single = routeLinks(pairLinks.slice(0, 1), adjacentNodes, adjacentPositions, 160);
  assert.deepEqual(single.get('first').points, [{ x: 27, y: -1.4 }, { x: 38, y: -2 }, { x: 142, y: -2 }, { x: 159, y: -1.2 }]);
  const nodes = [...adjacentNodes, { key: 'middle', kind: 'cloud', sub: true }];
  const positions = { ...adjacentPositions, b: { x: 400, y: 0 }, middle: { x: 200, y: 0 } };
  const routes = routeLinks(pairLinks, nodes, positions, 160);
  assert.deepEqual([...routes].map(([id, route]) => [id, route.points]), [
    ['first', [{ x: 18.1, y: -20 }, { x: 38, y: -42 }, { x: 358, y: -42 }, { x: 380.9, y: -19.1 }]],
    ['second', [{ x: 380.9, y: -19.1 }, { x: 358, y: -42 }, { x: 342, y: -58 }, { x: 30, y: -58 }, { x: 14, y: -42 }, { x: 8.5, y: -25.6 }]],
  ]);
});

for (const phone of [false, true]) test(`two-link topology: parallel, separate targets with readable labels, ${phone ? 'phone' : 'desktop'}`, async () => {
  const compute = await import(new URL('deck-compute.js', STATIC)), payload = fixture();
  payload.links.push({ id: 'second', from: 'nimbus-cdn', to: 'nimbus-dns', method: 'direct', stale: true });
  const made = deckCore(compute, payload, phone, false);
  assertGeometry(made);
  const links = made.core.links(), first = links.get('nimbus-dns>nimbus-cdn:proxy'), second = links.get('second');
  assert.equal(first.points?.length, 2);
  assert.equal(second.points?.length, 2);
  assert.equal(Math.abs(first.y1 - second.y1), 16);
  for (const id of ['nimbus-dns>nimbus-cdn:proxy', 'second']) assert.ok(made.core.scene().includes(`data-link="${e(id)}" tabindex="0" role="button" aria-pressed="false"`), 'each stroke has its own focusable target');
});

for (const count of [2, 4, 6, 8]) test(`${count} parallel strokes can each be selected by pointer and keyboard without opening a node`, async () => {
  const payload = count === 2 ? { nodes: adjacentNodes.map(node => ({ key: node.key, values: { kind: 'cloud', name: node.key } })), links: pairLinks, routes: [] } : bundlePayload(count);
  const host = stageDom(), ui = createTopologyMap({ mount: () => host, storage: null, interval: 0, api: async () => payload, look: { labels: 160, layout: 'layered', route: true } });
  ui.show();
  for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve));
  const stage = host.querySelector('[data-topology]').querySelector('[data-topo="stage"]');
  const world = host.querySelector('[data-topology]').querySelector('[data-topo="world"]');
  const target = id => ({ closest: selector => selector === '[data-link]' ? { dataset: { link: id } } : null });
  const selected = id => world.innerHTML.includes(`data-link="${e(id)}" tabindex="0" role="button" aria-pressed="true"`);
  for (const { id } of payload.links) {
    stage.fire('pointerdown', { pointerId: 1, clientX: 90, clientY: 8, target: target(id) });
    stage.fire('pointerup', { pointerId: 1, target: target(id) });
    assert.ok(selected(id), `${id}: pointer selects its own stroke`);
    stage.fire('keydown', { key: 'Enter', target: target(id) });
    assert.ok(!selected(id), 'Enter toggles it off');
    stage.fire('keydown', { key: ' ', target: target(id) });
    assert.ok(selected(id), 'Space selects it');
  }
  assert.ok(!selected(payload.links[0].id), 'selecting the other stroke clears the first');
  assert.doesNotMatch(world.innerHTML, /data-key="[^"]+"[^>]*aria-pressed="true"/, 'link selection leaves node selection alone');
  ui.stop();
});

// At a 390px viewport the card is 358px wide. A dense lane must not consume
// more than 32 map units, regardless of the number of links using it.
const bundlePayload = count => ({
  nodes: [
    { key: 'a', values: { kind: 'cloud', name: 'Source', role: 'Upstream' } },
    { key: 'middle', values: { kind: 'router', name: 'Middle', role: 'Transit' } },
    { key: 'b', values: { kind: 'server', name: 'Destination', role: 'Downstream' } },
  ],
  links: Array.from({ length: count }, (_, i) => ({ id: `bundle-${i}`, from: i % 2 ? 'b' : 'a', to: i % 2 ? 'a' : 'b', method: 'direct' })),
  routes: [],
});

const bundleMarks = scene => [...scene.matchAll(/<g class="topo-bundle" data-bundle-node="([^"]+)" data-count="(\d+)" data-state="([^"]+)" transform="translate\((-?[\d.]+) (-?[\d.]+)\)">([\s\S]*?)<\/text><\/g><\/g>/g)];
const endMarks = scene => [...scene.matchAll(/class="topo-dot s-([^"]+)" data-state="[^"]+" data-end="(from|to)" transform="translate\((-?[\d.]+) (-?[\d.]+)\)"/g)];
function markedBundle(count, states = ['up', 'unknown', 'degraded', 'down']) {
  const payload = bundlePayload(count);
  payload.status = { links: Object.fromEntries(payload.links.map((link, i) => [link.id, { from: states[i % states.length], to: 'unknown' }])) };
  return payload;
}

for (const count of [4, 6, 8]) for (const phone of [false, true]) test(`collapsed ends: ${count} links, ${phone ? 'phone' : 'desktop'}`, async () => {
  const compute = await import(new URL('deck-compute.js', STATIC));
  const made = deckCore(compute, markedBundle(count), phone, false), scene = made.core.scene();
  const marks = bundleMarks(scene);
  assert.equal(marks.length, 2, 'one aggregate at each device');
  assert.deepEqual(marks.map(m => m[1]).sort(), ['a', 'b']);
  for (const mark of marks) {
    assert.equal(Number(mark[2]), count);
    assert.equal(mark[3], mark[1] === 'a' ? 'degraded' : 'down', 'direction-aware worst known end state');
    assert.match(mark[6], new RegExp(`class="topo-dot s-${mark[3]}"`));
    assert.match(mark[6], new RegExp(`<text class="topo-bundle-count"[^>]*>${count}$`));
    assert.match(mark[6], /end: (Degraded|Down)/, 'state has words as well as a shape');
    const at = made.core.positions[mark[1]], x = at.x + Number(mark[4]), y = at.y + Number(mark[5]);
    const side = mark[6].includes('translate(-12 0)') ? -1 : 1, width = String(count).length * 8 / K_LEAST;
    const badge = { x: x + side * 12 - (side < 0 ? width : 0), y: y - 8 / K_LEAST, width, height: 16 / K_LEAST };
    for (const at of Object.values(made.core.positions)) {
      const cx = Math.max(badge.x, Math.min(at.x, badge.x + badge.width)), cy = Math.max(badge.y, Math.min(at.y, badge.y + badge.height));
      assert.ok(Math.hypot(cx - at.x, cy - at.y) > DISC, 'count box clears every device disc');
    }
    for (const route of made.core.links().values()) {
      const points = route.points || [{ x: route.x1, y: route.y1 }, { x: route.x2, y: route.y2 }];
      for (let i = 1; i < points.length; i++) assert.ok(!touches({ x1: points[i-1].x, y1: points[i-1].y, x2: points[i].x, y2: points[i].y }, { x: badge.x - 2, y: badge.y - 2, width: badge.width + 4, height: badge.height + 4 }), 'count box clears every link stroke');
    }
    for (const node of made.core.boxes) assert.ok(!overlaps(badge, plateOf(node, made.core.positions[node.key], made.metrics.labels, K_LEAST)), 'badge and count clear every label');
    assert.ok(x * made.view.k + made.view.x >= 7 && y * made.view.k + made.view.y >= 7, 'badge fits in the canvas');
  }
  assert.equal(endMarks(scene).length, 0, 'no individual marks remain to overlap');
  assertGeometry(made);
  for (const { id } of made.core.model.links) {
    made.core.selectLink(id);
    const selected = made.core.scene(), own = endMarks(selected), others = bundleMarks(selected);
    assert.equal(own.length, 2, 'only the selected link has individual end marks');
    assert.deepEqual(own.map(m => m[1]), Object.values(made.core.model.links.find(l => l.id === id).status));
    assert.equal(others.length, 2, 'remaining links stay collapsed even when only three remain');
    assert.ok(others.every(m => Number(m[2]) === count - 1));
    for (const mark of others) {
      const at = made.core.positions[mark[1]], x = at.x + Number(mark[4]), y = at.y + Number(mark[5]);
      for (const dot of own) assert.ok(!overlaps({ x: x - 7, y: y - 7, width: 14, height: 14 }, { x: Number(dot[3]) - 7, y: Number(dot[4]) - 7, width: 14, height: 14 }), 'selected marks clear the badge and its count');
    }
    made.core.selectLink(id);
    assert.equal(made.core.scene(), scene, 'deselect restores the aggregate');
  }
});

test('collapsed ends: classic keeps its marks and unclaimed states stay unmarked', async () => {
  const compute = await import(new URL('deck-compute.js', STATIC));
  const classic = createMapCore();
  classic.apply(markedBundle(6));
  assert.equal(bundleMarks(classic.scene()).length, 0);
  assert.equal(endMarks(classic.scene()).length, 12);
  const payload = markedBundle(6, ['none']);
  for (const ends of Object.values(payload.status.links)) ends.to = 'none';
  const unclaimed = deckCore(compute, payload, false, true).core;
  assert.equal(bundleMarks(unclaimed.scene()).length, 0);
  assert.equal(endMarks(unclaimed.scene()).length, 0);
});

test('collapsed ends: device degree counts incoming and outgoing link ends', async () => {
  const compute = await import(new URL('deck-compute.js', STATIC)), payload = markedBundle(6);
  payload.nodes.push({ key: 'c', values: { kind: 'pc', name: 'Client' } });
  for (const link of payload.links.slice(0, 3)) { if (link.from === 'b') link.from = 'c'; else link.to = 'c'; }
  const made = deckCore(compute, payload, false, false);
  const marks = bundleMarks(made.core.scene());
  assert.deepEqual(marks.map(m => [m[1], Number(m[2])]), [['a', 6]]);
  made.core.selectLink('bundle-2');
  const badge = bundleMarks(made.core.scene())[0];
  assert.equal(badge[3], 'up', 'selected worst end is excluded from the remaining aggregate');
});

for (const count of [1, 2, 3]) test(`sparse ends: ${count} links retain every original mark and position`, async () => {
  const compute = await import(new URL('deck-compute.js', STATIC));
  const made = deckCore(compute, markedBundle(count), false, false), scene = made.core.scene();
  assert.equal(bundleMarks(scene).length, 0);
  assert.equal(endMarks(scene).length, count * 2);
  for (const link of made.core.model.links) for (const end of ['from', 'to']) {
    const at = made.core.links().get(link.id)[end];
    assert.ok(scene.includes(`data-end="${end}" transform="translate(${at.x} ${at.y})"`));
  }
});

for (const [states, worst, shape] of [
  [['unknown'], 'unknown', '<circle r="4.5"/>'],
  [['unknown', 'up'], 'up', '<circle r="5.5"/>'],
  [['up', 'degraded'], 'degraded', '<path d="M0 -6.5 6.6 5h-13.2z"/>'],
  [['degraded', 'down'], 'down', '<rect x="-5" y="-5" width="10" height="10" rx="1"/>'],
]) test(`collapsed ends: ${worst} retains its status shape`, async () => {
  const compute = await import(new URL('deck-compute.js', STATIC));
  const payload = markedBundle(6, states);
  payload.links.forEach(link => { link.from = 'a'; link.to = 'b'; });
  const marks = bundleMarks(deckCore(compute, payload, false, false).core.scene());
  const mark = marks.find(m => m[1] === 'a');
  assert.ok(mark, 'one aggregate at the source');
  assert.equal(mark[3], worst);
  assert.ok(mark[6].includes(shape));
});

for (const count of [2, 4, 6, 8]) for (const phone of [false, true]) test(`bounded bundle: ${count} links, ${phone ? '390px phone' : 'desktop'}`, async () => {
  const compute = await import(new URL('deck-compute.js', STATIC));
  const payload = bundlePayload(count), made = deckCore(compute, payload, phone, false);
  assertGeometry(made);
  const routes = made.core.links(), y = made.core.positions.middle.y;
  const xs = [...routes.values()].map(route => {
    const points = route.points;
    const i = points.findIndex((p, i) => i && Math.min(p.y, points[i - 1].y) < y && Math.max(p.y, points[i - 1].y) > y);
    assert.ok(i > 0, 'every route crosses the shared lane');
    const a = points[i - 1], b = points[i];
    return a.x + (b.x - a.x) * (y - a.y) / (b.y - a.y);
  });
  // Two links may use opposite sides of the device, as before. Dense bundles
  // use a single bounded corridor, with one distinct stroke per link.
  if (count > 2) assert.ok(Math.max(...xs) - Math.min(...xs) <= 32.1, `bundle width ${Math.max(...xs) - Math.min(...xs)}`);
  assert.equal(new Set(xs).size, count, 'no coincident strokes');
  for (const id of routes.keys()) assert.ok(made.core.scene().includes(`data-link="${id}" tabindex="0" role="button"`));
  assert.deepEqual(made.core.links(), deckCore(compute, { ...payload, links: [...payload.links].reverse() }, phone, false).core.links(), 'input order does not change routes');
});

for (const phone of [false, true]) test(`bounded bundle: mixed destinations, ${phone ? '390px phone' : 'desktop'}`, async () => {
  const compute = await import(new URL('deck-compute.js', STATIC)), payload = bundlePayload(6);
  payload.nodes.push({ key: 'c', values: { kind: 'pc', name: 'Client', role: 'Workstation' } });
  for (const [i, link] of payload.links.entries()) { link.from = 'a'; link.to = i < 3 ? 'b' : 'c'; }
  const made = deckCore(compute, payload, phone, false);
  // Dragged nodes can leave several destinations on the same side corridor.
  for (const node of made.core.model.nodes) made.core.dragNode(node.key, 0, made.core.positions[node.key].y);
  made.view = made.core.fit(made.field.width, made.field.height, { remember: false }, { pad: made.metrics.pad });
  assertGeometry(made);
  const lanes = [...made.core.links().values()];
  assert.ok(lanes.every(lane => lane.parallel), 'different destinations share the side corridor');
  const xs = lanes.map(lane => lane.points[1].x);
  assert.ok(Math.max(...xs) - Math.min(...xs) <= 32.1);
  assert.equal(new Set(xs).size, 6);
});

test('bounded bundle: congestion elsewhere leaves a separate sparse pair unchanged', () => {
  const nodes = ['a', 'middle', 'b', 'c', 'other', 'd'].map(key => ({ key, kind: 'server', sub: true }));
  const positions = Object.fromEntries(nodes.map((node, i) => [node.key, { x: 0, y: i * 150 }]));
  const links = [
    ...Array.from({ length: 4 }, (_, i) => ({ id: `dense-${i}`, from: 'a', to: 'b' })),
    ...Array.from({ length: 2 }, (_, i) => ({ id: `sparse-${i}`, from: 'c', to: 'd' })),
  ];
  const routes = routeLinks(links, nodes, positions, 124, K_LEAST);
  assert.ok(links.slice(0, 4).every(link => routes.get(link.id).parallel));
  assert.ok(links.slice(4).every(link => !routes.get(link.id).parallel));
});

test('bounded bundle: blocked side exits keep the grid routes', () => {
  const nodes = ['a', 'b', 'left', 'right'].map(key => ({ key, kind: 'server', sub: true }));
  const positions = { a: { x: 0, y: 0 }, b: { x: 0, y: 300 }, left: { x: -90, y: 0 }, right: { x: 90, y: 0 } };
  const links = bundlePayload(6).links;
  const routes = routeLinks(links, nodes, positions, 124, K_LEAST);
  assert.equal(routes.size, 6);
  assert.ok([...routes.values()].every(route => !route.parallel), 'a blocked exit never replaces a route');
  for (const route of routes.values()) for (let i = 1; i < route.points.length; i++) for (const node of nodes) {
    const a = route.points[i - 1], b = route.points[i];
    assert.ok(!touches({ x1: a.x, y1: a.y, x2: b.x, y2: b.y }, plateOf(node, positions[node.key], 124, K_LEAST)));
  }
});

for (const count of [2, 4, 6, 8]) for (const phone of [false, true]) test(`bounded bundle: ${count} same-row links, ${phone ? '390px phone' : 'desktop'}`, async () => {
  const links = Array.from({ length: count }, (_, i) => ({ id: `bundle-${i}`, from: 'a', to: 'b' }));
  const lanes = [...routeLinks(links, adjacentNodes, adjacentPositions, 160).values()];
  assert.ok(lanes.every(lane => lane.points.length === 2), 'no detours');
  const ys = lanes.map(lane => lane.y1);
  assert.ok(Math.max(...ys) - Math.min(...ys) <= 32);
  assert.equal(new Set(ys).size, count);
  const compute = await import(new URL('deck-compute.js', STATIC));
  const payload = { nodes: adjacentNodes.map(node => ({ key: node.key, values: { kind: 'cloud', name: node.key, role: 'Cloud' } })), links: links.map(link => ({ ...link, method: 'direct' })), routes: [] };
  assertGeometry(deckCore(compute, payload, phone, false));
});

test('the card holds as many devices in a row as fit its width at the plate width', async () => {
  const compute = await import(new URL('deck-compute.js', STATIC));
  assert.equal(compute.columnsFor(860, compute.METRICS.desktop), 5);
  assert.equal(compute.columnsFor(358, compute.METRICS.phone), 2);
  assert.equal(compute.columnsFor(1200, compute.METRICS.desktop), 7);
  assert.equal(compute.columnsFor(100, compute.METRICS.phone), 1);
  // Five machines fit one row at the desktop card; more than fit take rows with room for the lanes of passing links.
  assert.equal(compute.layoutColumns(860, compute.METRICS.desktop, 5), 5);
  assert.equal(compute.layoutColumns(860, compute.METRICS.desktop, 8), 4);
  assert.equal(compute.layoutColumns(358, compute.METRICS.phone, 5), 2);
});

test('a map too big to route keeps straight links and still draws', async () => {
  const compute = await import(new URL('deck-compute.js', STATIC));
  const core = createMapCore({ labels: 160, layout: 'layered', metrics: compute.METRICS.desktop, route: true });
  core.apply(largeFixture(300));
  const scene = core.scene();
  assert.match(scene, /<line /);
  assert.ok(scene.includes('data-key='));
});

// ---------- labels keep their size on the screen at every zoom ----------

function stageDom() {
  const make = () => {
    const handlers = {}, parts = new Map(), classes = new Set(), props = {};
    const el = {
      isConnected: true, dataset: {}, attrs: {}, hidden: false, value: '', disabled: false, textContent: '', innerHTML: '', props, clientWidth: 860, clientHeight: 600,
      style: { setProperty(name, value) { props[name] = value; } }, classList: { toggle: (n, on) => (on ? classes.add(n) : classes.delete(n)), add: n => classes.add(n), remove: n => classes.delete(n) },
      setAttribute(name, value) { el.attrs[name] = String(value); }, addEventListener(kind, handler) { handlers[kind] = handler; }, dispatchEvent() {},
      fire: (kind, event = {}) => handlers[kind]?.({ type: kind, preventDefault() {}, target: { closest: () => null }, ctrlKey: false, metaKey: false, altKey: false, ...event }),
      querySelector(selector) { if (!parts.has(selector)) parts.set(selector, make()); return parts.get(selector); }, querySelectorAll: () => [], setPointerCapture() {}, focus() {}, getBoundingClientRect: () => ({ left: 0, top: 0 }),
    };
    el.ownerDocument = { hidden: false, activeElement: null };
    return el;
  };
  return make();
}

for (const phone of [false, true]) test(`label type is constant on screen: ${phone ? 'phone' : 'desktop'}, fitted view, zoomed out and zoomed in`, async () => {
  const compute = await import(new URL('deck-compute.js', STATIC));
  const metrics = phone ? compute.METRICS.phone : compute.METRICS.desktop, host = stageDom();
  const ui = createTopologyMap({ mount: () => host, storage: null, interval: 0, api: async () => fixture(), look: { labels: metrics.labels, layout: 'layered', metrics, route: true, pad: metrics.pad } });
  ui.show();
  for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve));
  const stage = host.querySelector('[data-topology]').querySelector('[data-topo="stage"]');
  const css = await readFile(new URL('topology.css', STATIC), 'utf8'), deck = await readFile(new URL('deck-compute.js', STATIC), 'utf8');
  const px = (source, name) => Number(new RegExp(`\\.${name}\\s*\\{[^}]*?(\\d+)px`).exec(source)?.[1]);
  const nameSize = Math.max(px(deck, 'deck-compute \\.topo-name'), 0) || px(css, 'topo-name'), roleSize = Math.max(px(deck, 'deck-compute \\.topo-role'), 0) || px(css, 'topo-role');
  assert.ok(nameSize >= 12 && roleSize >= 11, `declared sizes ${nameSize}/${roleSize}`);
  assert.match(deck, /\.topo-label \{ transform: translate\(0, 27px\) scale\(var\(--inv, 1\)\); \}/);
  const world = host.querySelector('[data-topology]').querySelector('[data-topo="world"]');
  assert.match(world.innerHTML, /<g class="topo-label"[^>]*><rect class="topo-label-plate"[\s\S]*?<text class="topo-name"/);
  const scale = () => Number(/scale\(([\d.]+)\)/.exec(world.attrs.transform)[1]);
  const seen = [];
  const check = (when) => {
    const k = scale(), inv = Number(stage.props['--inv']);
    seen.push([when, k]);
    // on screen = declared size x label scale (--inv) x zoom (k)
    assert.ok(Math.abs(inv * k - 1) < 1e-6, `${when}: --inv ${inv} at zoom ${k}`);
    assert.ok(nameSize * inv * k >= 12 - 1e-6 && roleSize * inv * k >= 11 - 1e-6, `${when}: type ${nameSize * inv * k}/${roleSize * inv * k}px`);
  };
  check('fitted');
  for (let i = 0; i < 3; i++) stage.fire('keydown', { key: '-', target: { closest: () => null } });
  check('zoomed out');
  for (let i = 0; i < 9; i++) stage.fire('keydown', { key: '+', target: { closest: () => null } });
  check('zoomed in');
  assert.ok(new Set(seen.map(([, k]) => k.toFixed(2))).size === 3, `three different zooms: ${JSON.stringify(seen)}`);
  ui.stop();
});

test('the Deck tab bar is one row of equal columns for two, three and four sections', async () => {
  const css = await readFile(new URL('deck.css', STATIC), 'utf8');
  assert.match(css, /\.m-tabs\[data-count="4"\] \{ grid-template-columns: repeat\(4, 1fr\); \}/);
  assert.match(css, /\.m-tabs\[data-count="3"\] \{ grid-template-columns: repeat\(3, 1fr\); \}/);
  assert.match(css, /\.m-tabs\[data-count="2"\] \{ grid-template-columns: repeat\(2, 1fr\); \}/);
});

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

test('empty workspace keeps just the hub, and an unavailable general bot hides only the bot handoff', async () => {
  const f = harness({ rows: [], bots: [] }); await flush();
  assert.deepEqual(f.nodes(), ['commonplace-hub']);
  assert.match(f.root.html, /No machines yet/);
  assert.equal(f.regions.get('empty').hidden, false);
  assert.equal(f.regions.get('summary').textContent, '0 of 0 machines ready.');
  assert.match(f.regions.get('actions').innerHTML, /data-compute-action="add-machine">Add machine/);
  assert.match(f.regions.get('actions').innerHTML, /data-compute-action="add-site">Add site/);
  assert.doesNotMatch(f.regions.get('actions').innerHTML, /Let a bot|href=/);
  f.ui.stop();
});

test('one-click handoffs store the draft and navigate; never send a message', async () => {
  const previous = globalThis.sessionStorage, saved = [];
  globalThis.sessionStorage = { setItem: (key, value) => saved.push([key, JSON.parse(value)]) };
  try {
    const f = harness(); await flush();
    f.click({ computeAction: 'map' });
    assert.deepEqual(saved, [['cmmn.deck.draft', { bot: 'general', text: 'Map my machines and sites.' }]]);
    assert.deepEqual(f.nav, ['#/bots/general']);
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
  assert.doesNotMatch(f.regions.get('actions').innerHTML, /Let a bot/);
  assert.match(f.regions.get('actions').innerHTML, /Add machine/); assert.match(f.regions.get('actions').innerHTML, /Add site/); ui.stop();
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
  assert.deepEqual(files.filter(name => /^(?:deck-)?topology.*\.(?:js|css)$/.test(name)).sort(), ['topology-detail.css', 'topology-detail.js', 'topology-icons.js', 'topology-layout.js', 'topology-map.js', 'topology-route.js', 'topology-text.js', 'topology.css']);
  const users = [];
  for (const name of files.filter(name => name.endsWith('.js'))) if (/import\s*\{[^}]*\bcreateTopologyMap\b[^}]*\}\s*from ['"]\.\/topology-map\.js['"]/.test(await readFile(new URL(name, STATIC), 'utf8'))) users.push(name);
  assert.deepEqual(users.sort(), ['app.js', 'deck-compute.js', 'topology-detail.js'].filter(name => name !== 'topology-detail.js'));
  assert.match(await readFile(new URL('deck-compute.js', STATIC), 'utf8'), /createTopologyMap/);
  assert.ok(MAP_PATH && mapModel(EMPTY));
});

test('Add machine and Add site are one button each: one request, the answer in the notice, no form', async () => {
  const f = fakeRoot(), seen = [];
  const answer = { kind: 'machine', mode: 'started', machines: [{ machine: 'mach_1', label: 'Build box', outcome: 'started', card: 7 }] };
  const api = async (path, options) => {
    if (path === '/api/bots') return { bots: [] };
    if (path === '/api/topology') return EMPTY;
    if (path === '/api/runners') return { machines };
    seen.push([path, options]); return answer;
  };
  const ui = compute.mount({ root: f.root, api, e });
  await flush();
  assert.doesNotMatch(f.root.html + f.regions.get('actions').innerHTML, /<form|<input/);
  const button = { disabled: false, dataset: { computeAction: 'add-machine' } };
  f.handlers.get('click')({ target: { closest: () => button } });
  await flush();
  assert.deepEqual(seen, [['/api/topology/add-machine', { method: 'POST', body: {} }]]);
  assert.match(f.regions.get('notice').textContent, /Started on Build box \(card #7\)/);
  assert.equal(button.disabled, false);
  ui.stop();
});

test('a member without rights is shown no add buttons at all', async () => {
  const f = harness({ error: Object.assign(new Error('forbidden'), { status: 403 }) }); await flush();
  assert.match(f.root.html, /Only workspace owners/);
  assert.doesNotMatch(f.root.html + (f.regions.get('actions')?.innerHTML || ''), /add-machine|add-site|Add machine|Add site/);
  f.ui.stop();
});
