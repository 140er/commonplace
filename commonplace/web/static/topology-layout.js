/* Geometry of the Compute map, without a DOM: the automatic layout, pan and zoom maths
   and where a link's line and its two status dots go. Everything here is a pure function
   of its arguments, so the same topology always lands in the same place. */
export const GRID = { x: 170, y: 130 };
export const ZOOM = { min: 0.15, max: 3 };

// Rows from top to bottom, the way traffic flows: site, CDN or DNS, edge, machine, app. The
// devices people sit at come last.
const TIER = { website: 0, cloud: 1, router: 2, server: 3, vm: 3, container: 4, pc: 5, mac: 5, phone: 5 };
const LAST_TIER = 5;
const byKey = (a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0);
const round = (n) => Math.round(n * 10) / 10;

/** Positions for every node: `{ key: { x, y } }`. Stable: it depends only on keys, kinds and links.
 * Inside a row a node sits near the nodes it links to in the rows above; ties go by key. */
export function autoLayout(nodes, links = []) {
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
    const perRow = Math.max(8, Math.ceil(Math.sqrt(scored.length) * 2));
    for (let start = 0; start < scored.length; start += perRow) {
      const row = scored.slice(start, start + perRow);
      row.forEach(({ node }, index) => { placed[node.key] = { x: round((index - (row.length - 1) / 2) * GRID.x), y }; });
      y += GRID.y;
    }
  }
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

/** The view that shows every point with room for labels; never closer than 1.25. */
export function fitView(points, width, height, pad = 70) {
  const w = width > 0 ? width : 800, h = height > 0 ? height : 600;
  if (!points.length) return { x: w / 2, y: h / 2, k: 1 };
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
  const left = Math.min(...xs), right = Math.max(...xs), top = Math.min(...ys), bottom = Math.max(...ys);
  const k = clampZoom(Math.min(1.25, (w - pad * 2) / Math.max(1, right - left), (h - pad * 2) / Math.max(1, bottom - top)));
  return { k, x: w / 2 - ((left + right) / 2) * k, y: h / 2 - ((top + bottom) / 2) * k };
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
