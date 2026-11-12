/* Where the links of the Compute map run when a host wants none of them to touch a device or a
   label: each link is routed on a coarse grid around the discs and the label plates, in eight
   directions, preferring few bends and lanes no other link uses. Pure: the same nodes at the same
   places always give the same routes. A link that cannot be routed (or a map too big to route)
   keeps the straight line of `layoutLinks`, and the caller can tell which (`routed`). */
export const DISC = 27;
const CELL = 8, MARGIN = 8, TURN = 0.6, SHARED = 2, NEAR = 0.8, OUTSIDE = 12, MAX_CELLS = 160000, MAX_NODES = 80;
const DIRS = [[1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1]];
const round = (n) => Math.round(n * 10) / 10;

export const GAP = 4;
/** The label plate of a node in map units. A label is drawn at a constant size on the screen, so its
 * plate is `labels` wide and `node.height` (38 for two lines) screen pixels, GAP below the disc (above it for the
 * hub): in map units that is divided by the view's scale `k`. */
export const plateOf = (node, at, labels, k = 1) => {
  const height = (node.height ?? (node.sub ? 38 : 22)) / k, gap = GAP / k;
  return { x: at.x - labels / 2 / k, y: node.kind === "hub" ? at.y - DISC - gap - height : at.y + DISC + gap, width: labels / k, height };
};

const touchesRect = (a, b, r) => {
  let low = 0, high = 1;
  const dx = b.x - a.x, dy = b.y - a.y;
  for (const [p, q] of [[-dx, a.x - r.x], [dx, r.x + r.width - a.x], [-dy, a.y - r.y], [dy, r.y + r.height - a.y]]) {
    if (p === 0) { if (q < 0) return false; } else { const t = q / p; if (p < 0) low = Math.max(low, t); else high = Math.min(high, t); if (low > high) return false; }
  }
  return true;
};
const toSegment = (p, a, b) => {
  const dx = b.x - a.x, dy = b.y - a.y, span = dx * dx + dy * dy;
  const t = span ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / span)) : 0;
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
};
/** True when the straight line between two devices touches no other device and no label. */
export function straightClear(a, b, nodes, positions, labels, k = 1) {
  const from = positions[a], to = positions[b];
  for (const node of nodes) {
    const at = positions[node.key];
    if (!at) continue;
    if (node.key !== a && node.key !== b && toSegment(at, from, to) < DISC + MARGIN) return false;
    const plate = plateOf(node, at, labels, k);
    if (touchesRect(from, to, { x: plate.x - 2, y: plate.y - 2, width: plate.width + 4, height: plate.height + 4 })) return false;
  }
  return true;
}

class Heap {
  constructor() { this.items = []; }
  get size() { return this.items.length; }
  push(cost, value) {
    const items = this.items;
    items.push([cost, value]);
    for (let i = items.length - 1; i > 0;) { const p = (i - 1) >> 1; if (items[p][0] <= items[i][0]) break; [items[p], items[i]] = [items[i], items[p]]; i = p; }
  }
  pop() {
    const items = this.items, top = items[0], last = items.pop();
    if (items.length) {
      items[0] = last;
      for (let i = 0; ;) {
        const l = 2 * i + 1, r = l + 1; let m = i;
        if (l < items.length && items[l][0] < items[m][0]) m = l;
        if (r < items.length && items[r][0] < items[m][0]) m = r;
        if (m === i) break;
        [items[m], items[i]] = [items[i], items[m]]; i = m;
      }
    }
    return top[1];
  }
}

const length = (points) => points.slice(1).reduce((sum, p, i) => sum + Math.hypot(p.x - points[i].x, p.y - points[i].y), 0);
/** The point `at` map units along a polyline from its first point. */
export function pointAlong(points, at) {
  let left = at;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i], span = Math.hypot(b.x - a.x, b.y - a.y);
    if (left <= span || i === points.length - 1) { const t = span ? Math.min(1, left / span) : 0; return { x: round(a.x + (b.x - a.x) * t), y: round(a.y + (b.y - a.y) * t) }; }
    left -= span;
  }
  return points[0];
}

/** Routes for `links` between `nodes` (`{ key, kind, sub }`) placed at `positions`, with label
 * plates `labels` wide. Returns a Map of link id to `{ points, from, to, label, x1, y1, x2, y2 }`
 * for every link that found a way; links without one are absent. `null` when the map is too big. */
export function routeLinks(links, nodes, positions, labels, k = 1) {
  const placed = nodes.filter((node) => positions[node.key]);
  if (!placed.length || placed.length > MAX_NODES) return null;
  const reach = labels / 2 / k + 70;
  const xs = placed.map((node) => positions[node.key].x), ys = placed.map((node) => positions[node.key].y);
  const ox = Math.min(...xs) - reach, oy = Math.min(...ys) - reach - 40;
  const W = Math.ceil((Math.max(...xs) + reach - ox) / CELL), H = Math.ceil((Math.max(...ys) + reach + 80 - oy) / CELL);
  if (W * H > MAX_CELLS) return null;
  const blocked = new Uint8Array(W * H), used = new Uint8Array(W * H);
  const cx = (c) => ox + (c % W + 0.5) * CELL, cy = (c) => oy + (Math.floor(c / W) + 0.5) * CELL;
  const cellAt = (x, y) => { const i = Math.floor((x - ox) / CELL), j = Math.floor((y - oy) / CELL); return i < 0 || j < 0 || i >= W || j >= H ? -1 : j * W + i; };
  const mark = (x0, y0, x1, y1, grid = blocked) => {
    for (let j = Math.max(0, Math.floor((y0 - oy) / CELL)); j <= Math.min(H - 1, Math.floor((y1 - oy) / CELL)); j++) for (let i = Math.max(0, Math.floor((x0 - ox) / CELL)); i <= Math.min(W - 1, Math.floor((x1 - ox) / CELL)); i++) {
      const x = ox + (i + 0.5) * CELL, y = oy + (j + 0.5) * CELL;
      if (x >= x0 && x <= x1 && y >= y0 && y <= y1) grid[j * W + i] = 1;
    }
  };
  const half = DISC + MARGIN;
  // Past the devices' own extent a route is dear: links bundle in the lanes between devices instead of circling them.
  const plates = placed.map((node) => plateOf(node, positions[node.key], labels, k));
  const inside = { left: Math.min(...plates.map((p) => p.x)) - MARGIN, right: Math.max(...plates.map((p) => p.x + p.width)) + MARGIN };
  for (const node of placed) {
    const at = positions[node.key], plate = plateOf(node, at, labels, k);
    mark(at.x - half, at.y - half, at.x + half, at.y + half);
    mark(plate.x - MARGIN, plate.y - MARGIN, plate.x + plate.width + MARGIN, plate.y + plate.height + MARGIN);
  }
  const ring = (at) => {
    const cells = [], r = half + CELL;
    for (let y = at.y - r; y <= at.y + r; y += CELL) for (let x = at.x - r; x <= at.x + r; x += CELL) {
      if (Math.max(Math.abs(x - at.x), Math.abs(y - at.y)) < half + 1) continue;
      const c = cellAt(x, y);
      if (c >= 0 && !blocked[c]) cells.push(c);
    }
    return [...new Set(cells)];
  };
  const out = new Map();
  const best = new Float32Array(W * H * 9).fill(Infinity), from = new Int32Array(W * H * 9).fill(-1);
  const order = [...links].filter((link) => positions[link.from] && positions[link.to] && link.from !== link.to)
    .sort((a, b) => Math.hypot(positions[a.from].x - positions[a.to].x, positions[a.from].y - positions[a.to].y) - Math.hypot(positions[b.from].x - positions[b.to].x, positions[b.from].y - positions[b.to].y) || (a.id < b.id ? -1 : 1));
  for (const link of order) {
    const a = positions[link.from], b = positions[link.to];
    const sources = ring(a), targets = new Set(ring(b));
    if (!sources.length || !targets.size) continue;
    const heap = new Heap(), touched = [];
    const h = (c) => { const dx = Math.abs(cx(c) - b.x) / CELL, dy = Math.abs(cy(c) - b.y) / CELL; return Math.max(0, Math.max(dx, dy) + 0.414 * Math.min(dx, dy) - half / CELL); };
    for (const c of sources) { best[c * 9 + 8] = 0; touched.push(c * 9 + 8); heap.push(h(c), c * 9 + 8); }
    let end = -1;
    while (heap.size) {
      const state = heap.pop(), c = Math.floor(state / 9), dir = state % 9;
      if (targets.has(c)) { end = state; break; }
      const g = best[state], i = c % W, j = Math.floor(c / W);
      for (let d = 0; d < 8; d++) {
        const ni = i + DIRS[d][0], nj = j + DIRS[d][1];
        if (ni < 0 || nj < 0 || ni >= W || nj >= H) continue;
        const n = nj * W + ni;
        if (blocked[n]) continue;
        // No cutting a corner of a device or a label.
        if (d % 2 && (blocked[j * W + ni] || blocked[nj * W + i])) continue;
        const cost = g + (d % 2 ? 1.414 : 1) + (dir !== 8 && dir !== d ? TURN : 0) + (used[n] === 2 ? SHARED : used[n] === 1 ? NEAR : 0) + (cx(n) < inside.left || cx(n) > inside.right ? OUTSIDE : 0);
        const next = n * 9 + d;
        if (cost < best[next]) { if (best[next] === Infinity) touched.push(next); best[next] = cost; from[next] = state; heap.push(cost + h(n), next); }
      }
    }
    const cells = [];
    if (end >= 0) for (let state = end; state >= 0; state = from[state]) cells.push(Math.floor(state / 9));
    for (const state of touched) { best[state] = Infinity; from[state] = -1; }
    if (end < 0) continue;
    cells.reverse();
    // A lane another link runs in is dear, and the lane beside it a little: parallel links keep apart.
    for (const c of cells) {
      for (let d = 0; d < 8; d++) { const n = c + DIRS[d][0] + DIRS[d][1] * W; if (n >= 0 && n < W * H && !used[n]) used[n] = 1; }
      used[c] = 2;
    }
    // Corner points only; the ends sit on the discs' edges.
    let points = cells.map((c) => ({ x: round(cx(c)), y: round(cy(c)) }));
    points = points.filter((p, i) => i === 0 || i === points.length - 1 || (p.x - points[i - 1].x) * (points[i + 1].y - p.y) !== (p.y - points[i - 1].y) * (points[i + 1].x - p.x));
    const edge = (at, to) => { const d = Math.hypot(to.x - at.x, to.y - at.y) || 1; return { x: round(at.x + (to.x - at.x) / d * DISC), y: round(at.y + (to.y - at.y) / d * DISC) }; };
    points = [edge(a, points[0]), ...points, edge(b, points.at(-1))];
    // The dot of each end sits on the line just outside its device.
    const total = length(points), inset = Math.min(18, total * 0.3);
    let widest = 0, label = points[0];
    for (let i = 1; i < points.length; i++) {
      const span = Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
      if (span > widest) { widest = span; label = { x: round((points[i].x + points[i - 1].x) / 2), y: round((points[i].y + points[i - 1].y) / 2) }; }
    }
    out.set(link.id, { points, from: pointAlong(points, inset), to: pointAlong(points, total - inset), label, x1: points[0].x, y1: points[0].y, x2: points.at(-1).x, y2: points.at(-1).y });
  }
  return out;
}
