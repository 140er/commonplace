/* Geometry of the Compute map, without a DOM: the automatic layout, pan and zoom maths
   and where a link's line and its two status dots go. Everything here is a pure function
   of its arguments, so the same topology always lands in the same place. */
export const GRID = { x: 170, y: 130 };
export const ZOOM = { min: 0.15, max: 3 };

// Rows from top to bottom, the way traffic flows: site, CDN or DNS, edge, machine, app. The
// devices people sit at come last.
const TIER = { website: 0, cloud: 1, router: 2, hub: 2, server: 3, vm: 3, container: 4, pc: 5, mac: 5, phone: 5 };
const LAST_TIER = 5;
const byKey = (a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0);
const round = (n) => Math.round(n * 10) / 10;

/** Positions for every node: `{ key: { x, y } }`. Stable: it depends only on keys, kinds and links.
 * Inside a row a node sits near the nodes it links to in the rows above; ties go by key.
 * `columns` caps the nodes of one row (a narrow canvas); by default a row grows with the count. */
export function autoLayout(nodes, links = [], { columns = 0 } = {}) {
  const tiers = new Map();
  for (const node of nodes) {
    const tier = TIER[node.kind] ?? LAST_TIER;
    if (!tiers.has(tier)) tiers.set(tier, []);
    tiers.get(tier).push(node);
  }
  const near = new Map();
  const join = (a, b) => { if (!near.has(a)) near.set(a, []); near.get(a).push(b); };
  for (const link of links) { join(link.from, link.to); join(link.to, link.from); }
  const placed = {};
  let y = 0;
  for (const tier of [...tiers.keys()].sort((a, b) => a - b)) {
    const scored = tiers.get(tier).sort(byKey).map((node) => {
      const above = (near.get(node.key) || []).filter((key) => placed[key]).map((key) => placed[key].x);
      return { node, pull: above.length ? above.reduce((sum, x) => sum + x, 0) / above.length : null };
    });
    // Array.prototype.sort is stable, so equal pulls keep their key order.
    scored.sort((a, b) => (a.pull === null) - (b.pull === null) || (a.pull ?? 0) - (b.pull ?? 0));
    const perRow = columns > 0 ? columns : Math.max(8, Math.ceil(Math.sqrt(scored.length) * 2));
    for (let start = 0; start < scored.length; start += perRow) {
      const row = scored.slice(start, start + perRow);
      row.forEach(({ node }, index) => { placed[node.key] = { x: round((index - (row.length - 1) / 2) * GRID.x), y }; });
      y += GRID.y;
    }
  }
  return placed;
}

/** The layered layout for a host that routes its links around the nodes: the same rows as
 * `autoLayout`, but a link that spans several rows leaves a free lane in each row it passes, so
 * the router can run it between neighbours instead of through a device. Inside a row a node sits
 * near the nodes it links to in the rows above and below (a few barycentre sweeps), and a node
 * linked to another in its own row is moved next to it. A node takes `gx` of the row, a lane
 * `lane`; rows are `gy` apart. `columns` caps the nodes of a row (a tier with more continues in
 * the rows below it). Links from one device that pass a row share one lane there.
 * With `below` (a node's label height on the screen) rows are as far apart as their labels need, plus
 * `channel` clear for the links, for a view of at least `least`; without it rows are `gy` apart.
 * Stable: it depends only on keys, kinds and links. */
export function layeredLayout(nodes, links = [], { gx = 200, gy = 128, lane = 56, columns = 0, below = null, channel = 28, least = 0.85 } = {}) {
  const tiers = [...new Set(nodes.map((node) => TIER[node.kind] ?? LAST_TIER))].sort((a, b) => a - b);
  // A tier wider than `columns` becomes several rows, filled in key order.
  const rowOf = new Map();
  let layers = 0;
  for (const tier of tiers) {
    const members = nodes.filter((node) => (TIER[node.kind] ?? LAST_TIER) === tier).sort(byKey);
    const size = columns > 0 ? columns : members.length || 1;
    members.forEach((node, i) => rowOf.set(node.key, layers + Math.floor(i / size)));
    layers += Math.max(1, Math.ceil(members.length / size));
  }
  const layerOf = rowOf;
  const rows = Array.from({ length: layers }, () => []);
  for (const node of [...nodes].sort(byKey)) rows[layerOf.get(node.key)].push({ id: node.key, w: gx });
  const up = new Map(), down = new Map(), same = [];
  const edge = (parent, child) => {
    if (!up.has(child)) up.set(child, []);
    if (!down.has(parent)) down.set(parent, []);
    up.get(child).push(parent); down.get(parent).push(child);
  };
  for (const link of [...links].sort((a, b) => (a.id < b.id ? -1 : 1))) {
    if (!layerOf.has(link.from) || !layerOf.has(link.to) || link.from === link.to) continue;
    let [top, bottom] = [link.from, link.to];
    if (layerOf.get(top) === layerOf.get(bottom)) { same.push([top, bottom]); continue; }
    if (layerOf.get(top) > layerOf.get(bottom)) [top, bottom] = [bottom, top];
    let last = top;
    for (let layer = layerOf.get(top) + 1; layer < layerOf.get(bottom); layer++) {
      const id = `lane:${top}:${layer}`;
      if (!rows[layer].some((item) => item.id === id)) rows[layer].push({ id, w: lane });
      if (!(up.get(id) || []).includes(last)) edge(last, id);
      last = id;
    }
    edge(last, bottom);
  }
  // Where each item of a row sits across (rows are centred), so a node pulls towards where its neighbours are.
  const across = (row) => {
    let x = -row.reduce((sum, item) => sum + item.w, 0) / 2;
    return new Map(row.map((item) => { const at = x + item.w / 2; x += item.w; return [item.id, at]; }));
  };
  const sweep = (layer, from, neighbours) => {
    const at = across(rows[from]), own = across(rows[layer]);
    const keyed = rows[layer].map((item) => {
      const near = (neighbours.get(item.id) || []).filter((id) => at.has(id)).map((id) => at.get(id));
      return { item, pull: near.length ? near.reduce((sum, n) => sum + n, 0) / near.length : own.get(item.id) };
    });
    keyed.sort((a, b) => a.pull - b.pull);
    rows[layer] = keyed.map((entry) => entry.item);
  };
  for (let round = 0; round < 4; round++) {
    for (let layer = 1; layer < rows.length; layer++) sweep(layer, layer - 1, up);
    for (let layer = rows.length - 2; layer >= 0; layer--) sweep(layer, layer + 1, down);
  }
  for (const [a, b] of same) {
    const row = rows[layerOf.get(a)], from = row.findIndex((item) => item.id === b);
    const [moved] = row.splice(from, 1);
    row.splice(row.findIndex((item) => item.id === a) + 1, 0, moved);
  }
  const placed = {};
  // Where each row sits down the page.
  const tops = [];
  let offset = 0;
  for (const row of rows) {
    tops.push(offset);
    const label = Math.max(0, ...row.filter((item) => !item.id.startsWith("lane:")).map((item) => (4 + (below ? below(item.id) : 0)) / least));
    offset += below ? 54 + label + channel : gy;
  }
  rows.forEach((row, layer) => {
    const width = row.reduce((sum, item) => sum + item.w, 0);
    let x = -width / 2;
    for (const item of row) { if (!item.id.startsWith("lane:")) placed[item.id] = { x: round(x + item.w / 2), y: tops[layer] }; x += item.w; }
  });
  return placed;
}

export const clampZoom = (k) => Math.min(ZOOM.max, Math.max(ZOOM.min, k));

/** Zoom by `factor` keeping the screen point (px, py) over the same map point. */
export function zoomAbout(view, px, py, factor) {
  const k = clampZoom(view.k * factor);
  const ratio = k / view.k;
  return { k, x: px - (px - view.x) * ratio, y: py - (py - view.y) * ratio };
}

export const toWorld = (view, px, py) => ({ x: (px - view.x) / view.k, y: (py - view.y) / view.k });

/** The view that shows every point with room for labels; never closer than `most` (1.25). `reach` widens
 * the box the view must show by that much around the points, in map units (none by default). */
export function fitView(points, width, height, pad = 70, reach = { x: 0, top: 0, bottom: 0 }, most = 1.25) {
  const w = width > 0 ? width : 800, h = height > 0 ? height : 600;
  if (!points.length) return { x: w / 2, y: h / 2, k: 1 };
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
  const left = Math.min(...xs) - reach.x, right = Math.max(...xs) + reach.x, top = Math.min(...ys) - reach.top, bottom = Math.max(...ys) + reach.bottom;
  const k = clampZoom(Math.min(most, (w - pad * 2) / Math.max(1, right - left), (h - pad * 2) / Math.max(1, bottom - top)));
  return { k, x: w / 2 - ((left + right) / 2) * k, y: h / 2 - ((top + bottom) / 2) * k };
}

/** The view for labels drawn at a constant size on the screen. Each of `points` scales with the view and
 * carries how far it stretches beyond itself on the screen (`rx` across, `top`, `bottom`): a device centre
 * its label, a route bend next to nothing. The graph's width is fitted at the widest scale up to `most`;
 * the card is then as tall as the graph with `pad` round it (but not under `minHeight`), so the
 * page scrolls rather than the card. Only when that is more than `maxHeight` the card stops there and
 * the graph pans inside it from the top. Returns the view and the card's `height`. */
export function fitLabelled(points, width, height, { pad = 32, most = 1, minHeight = 240, maxHeight = Infinity } = {}) {
  const w = width > 0 ? width : 800;
  if (!points.length) return { x: w / 2, y: minHeight / 2, k: 1, height: minHeight };
  const extent = (k) => ({
    left: Math.min(...points.map((p) => p.x * k - p.rx)), right: Math.max(...points.map((p) => p.x * k + p.rx)),
    top: Math.min(...points.map((p) => p.y * k - p.top)), bottom: Math.max(...points.map((p) => p.y * k + p.bottom)),
  });
  let k = most;
  while (k > 0.2 && (() => { const e = extent(k); return e.right - e.left + 2 * pad > w; })()) k = Math.round((k - 0.005) * 1000) / 1000;
  const e = extent(k), tall = Math.ceil(e.bottom - e.top + 2 * pad), card = Math.min(maxHeight, Math.max(minHeight, tall));
  return { k, x: w / 2 - (e.left + e.right) / 2, y: tall >= card ? pad - e.top : (card - (e.bottom - e.top)) / 2 - e.top, height: card };
}

/** Line, dot and label places per link id. Links between the same two nodes run side by side;
 * each status dot sits on its line just outside the node at that end. */
export function layoutLinks(links, positions) {
  const groups = new Map();
  for (const link of links) {
    if (!positions[link.from] || !positions[link.to]) continue;
    const pair = link.from < link.to ? `${link.from}>${link.to}` : `${link.to}>${link.from}`;
    if (!groups.has(pair)) groups.set(pair, []);
    groups.get(pair).push(link);
  }
  const out = new Map();
  for (const group of groups.values()) {
    group.forEach((link, index) => {
      const flipped = link.from > link.to;
      // Measure from the smaller key, so two opposite links between a pair never share a line.
      const a = positions[flipped ? link.to : link.from], b = positions[flipped ? link.from : link.to];
      const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy) || 1;
      const ux = dx / length, uy = dy / length;
      const shift = (index - (group.length - 1) / 2) * 16;
      const ox = -uy * shift, oy = ux * shift;
      const inset = Math.min(46, length * 0.3);
      // The method label sits below the upper node's own label, clear of both nodes.
      const along = dy > 40 ? 0.66 : dy < -40 ? 0.34 : 0.5;
      const start = { x: round(a.x + ox), y: round(a.y + oy) }, end = { x: round(b.x + ox), y: round(b.y + oy) };
      const nearStart = { x: round(start.x + ux * inset), y: round(start.y + uy * inset) };
      const nearEnd = { x: round(end.x - ux * inset), y: round(end.y - uy * inset) };
      out.set(link.id, {
        x1: start.x, y1: start.y, x2: end.x, y2: end.y,
        from: flipped ? nearEnd : nearStart, to: flipped ? nearStart : nearEnd,
        label: { x: round(start.x + (end.x - start.x) * along), y: round(start.y + (end.y - start.y) * along) },
      });
    });
  }
  return out;
}
