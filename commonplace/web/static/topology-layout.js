/* Geometry of the Compute map, without a DOM: the automatic layout, pan and zoom maths
   and where a link's line and its two status dots go. Everything here is a pure function
   of its arguments, so the same topology always lands in the same place. */
export const GRID = { x: 170, y: 112 };
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

// What a device draws around its centre, in map units: the plate and status mark above, the name and role
// below, and a name's width to each side.
export const REACH = { x: 80, top: 31, bottom: 72 };
// An icon plate is 54 units wide; a fit never makes it wider than 64 pixels.
// Where on a link its method name may go, best first: the middle, then outwards in steps.
const ALONG = [0.5, ...[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].flatMap((i) => [0.5 - i * 0.025, 0.5 + i * 0.025])];
export const FIT_MAX = 64 / 54;

/** The view that fills a `width` by `height` box with the points (and what is drawn around them), with `pad`
 * pixels of room, scaling up as well as down (never past `maxK`). Names keep a readable size: when the whole
 * graph cannot fit at `minK` the view stays at `minK`, centred across and starting at the top, and the rest is
 * left to panning. Pure: points and box in, `{ x, y, k }` out. */
export function fitView(points, width, height, { pad = 24, minK = 1, maxK = FIT_MAX } = {}) {
  const w = width > 0 ? width : 800, h = height > 0 ? height : 600;
  if (!points.length) return { x: w / 2, y: h / 2, k: 1 };
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
  const left = Math.min(...xs) - REACH.x, right = Math.max(...xs) + REACH.x, top = Math.min(...ys) - REACH.top, bottom = Math.max(...ys) + REACH.bottom;
  const byWidth = Math.max(1, w - pad * 2) / (right - left), byHeight = Math.max(1, h - pad * 2) / (bottom - top);
  const whole = Math.min(maxK, byWidth, byHeight);
  if (whole >= minK) return { k: whole, x: w / 2 - ((left + right) / 2) * whole, y: h / 2 - ((top + bottom) / 2) * whole };
  const k = clampZoom(minK);
  return { k, x: w / 2 - ((left + right) / 2) * k, y: pad - top * k };
}

/** Line, dot and label places per link id. Links between the same two nodes run side by side;
 * each status dot sits on its line just outside the node at that end. */
export function layoutLinks(links, positions) {
  const groups = new Map();
  // A method name goes where it touches no device (plate, name or role) and no label placed before it.
  const keepClear = Object.values(positions).flatMap((p) => [{ l: p.x - 30, r: p.x + 30, t: p.y - 30, b: p.y + 30 }, { l: p.x - 78, r: p.x + 78, t: p.y + 36, b: p.y + 76 }]);
  const taken = [];
  const free = (box) => ![...keepClear, ...taken].some((o) => box.l < o.r && box.r > o.l && box.t < o.b && box.b > o.t);
  for (const link of links) {
    if (!positions[link.from] || !positions[link.to]) continue;
    const pair = link.from < link.to ? `${link.from}>${link.to}` : `${link.to}>${link.from}`;
    if (!groups.has(pair)) groups.set(pair, []);
    groups.get(pair).push(link);
  }
  const labelAt = (start, end, width) => {
    let first = null;
    // On the line first; when no stretch of it is free (devices one row apart), beside it.
    for (const side of [0, 1, -1]) for (const along of ALONG) {
      const x = round(start.x + (end.x - start.x) * along + side * (width / 2 + 8)), y = round(start.y + (end.y - start.y) * along);
      const box = { l: x - width / 2, r: x + width / 2, t: y - 17, b: y };
      first ??= { x, y };
      if (free(box)) { taken.push(box); return { x, y }; }
    }
    return first;
  };
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
      const inset = Math.min(34, length * 0.3);
      const width = Array.from(String(link.method || "")).length * 6.4 + 10;
      const start = { x: round(a.x + ox), y: round(a.y + oy) }, end = { x: round(b.x + ox), y: round(b.y + oy) };
      const nearStart = { x: round(start.x + ux * inset), y: round(start.y + uy * inset) };
      const nearEnd = { x: round(end.x - ux * inset), y: round(end.y - uy * inset) };
      out.set(link.id, {
        x1: start.x, y1: start.y, x2: end.x, y2: end.y,
        from: flipped ? nearEnd : nearStart, to: flipped ? nearStart : nearEnd,
        label: labelAt(start, end, width),
      });
    });
  }
  return out;
}
