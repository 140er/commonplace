/* Geometry of the Compute map, without a DOM: the automatic layout, pan and zoom maths
   and where a link's line and its two status dots go. Everything here is a pure function
   of its arguments, so the same topology always lands in the same place. */
export const GRID = { x: 170, y: 112 };
export const ZOOM = { min: 0.05, max: 3 };

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

// An icon plate is 54 units wide; a fit never makes it wider than 64 pixels.
export const FIT_MAX = 64 / 54;
// Names keep one size on screen whatever the zoom: 12px on a desktop, 11px on a phone (roles are one pixel less).
export const TEXT = { desktop: 12, phone: 11 };
// The plate's half size and the pixels between it and the name; the name and role lines below it, in pixels.
const PLATE = 27, GAP = 6, ROLE_LINE = 14, NAME_CLIP = 24;
// Where one device's name baseline and role baseline sit under its centre: plate units plus screen pixels
// that do not scale. The stylesheet draws exactly this with `--inv` (1 / scale).
export const labelOffsets = (size, k) => ({ name: PLATE + (GAP + size) / k, role: PLATE + (GAP + size + ROLE_LINE) / k });

// Widths in em for a bold UI font: a deliberately safe estimate, so a cut name never spills into its neighbour.
const glyph = (ch) => (/[MW@%]/.test(ch) ? 0.95 : /[A-Z]/.test(ch) ? 0.72 : /[ijl.,:;'|!\- ]/.test(ch) ? 0.32 : /[^\x20-\x7e]/.test(ch) ? 1 : 0.6);
export const textWidth = (text, size) => Array.from(String(text ?? "")).reduce((sum, ch) => sum + glyph(ch), 0) * size;

// Rows are GRID.y apart and a plate takes PLATE on each side: that is the vertical room, in map units, for the lines
// under a device. Below the scale at which a line needs more pixels than that, it would run into the row beneath.
const rowRoom = GRID.y - PLATE * 2;
export const nameShown = (k, size) => k * rowRoom >= GAP + size + 4;
export const roleShown = (k, size) => k * rowRoom >= GAP + size + ROLE_LINE + 4;

/** `text` cut with an ellipsis to fit `room` pixels at `size` pixels; whole when it already fits. */
export function labelText(text, room, size) {
  const whole = String(text ?? "");
  if (textWidth(whole, size) <= room) return whole;
  let used = textWidth("…", size), kept = "";
  for (const ch of Array.from(whole)) {
    used += glyph(ch) * size;
    if (used > room) break;
    kept += ch;
  }
  return kept ? `${kept.trimEnd()}…` : "…";
}

/** True when a line of text fits its slot: the role under a name shows only then. */
export const roleFits = (role, room, size) => !role || textWidth(role, size) <= room;

/** The room a device's name has at `k`: the horizontal distance to its nearest neighbour in the row, minus 8 pixels. */
export const slotRoom = (slot, k) => (Number.isFinite(slot) ? Math.max(0, slot * k - 8) : Infinity);

/** `{ key: slot }`: the horizontal distance in map units from each device to the nearest other device in its
 * row (about the same height); Infinity for a device alone in its row. */
export function slotWidths(positions) {
  const sorted = Object.entries(positions).map(([key, at]) => ({ key, ...at })).sort((a, b) => a.y - b.y);
  const slots = Object.fromEntries(sorted.map((item) => [item.key, Infinity]));
  for (let i = 0; i < sorted.length; i++) {
    for (let j = i + 1; j < sorted.length && sorted[j].y - sorted[i].y <= 40; j++) {
      const gap = Math.abs(sorted[j].x - sorted[i].x);
      slots[sorted[i].key] = Math.min(slots[sorted[i].key], gap);
      slots[sorted[j].key] = Math.min(slots[sorted[j].key], gap);
    }
  }
  return slots;
}

/** The view that shows the whole graph in a `width` by `height` box (the canvas without its toolbar and legend),
 * with `pad` pixels of room, scaled up as well as down (never so far that an icon passes 64px), with the
 * names and roles drawn at their fixed pixel size around each device. `items` are `{ x, y, name?, role?, slot? }`.
 * The largest scale at which every plate, mark and name line is inside the padded box wins; the graph is centred.
 * Pure: items and box in, `{ x, y, k }` out. */
export function fitView(items, width, height, { pad = 24, maxK = FIT_MAX, size = TEXT.desktop } = {}) {
  const w = width > 0 ? width : 800, h = height > 0 ? height : 600;
  if (!items.length) return { x: w / 2, y: h / 2, k: 1 };
  const extent = (k) => {
    let left = Infinity, right = -Infinity, top = Infinity, bottom = -Infinity;
    for (const item of items) {
      const room = slotRoom(item.slot ?? Infinity, k);
      const half = Math.max(PLATE * k, Math.min(textWidth(Array.from(item.name ?? "").slice(0, NAME_CLIP).join(""), size), room) / 2, roleFits(item.role, room, size - 1) ? textWidth(item.role, size - 1) / 2 : 0);
      left = Math.min(left, item.x * k - half); right = Math.max(right, item.x * k + half);
      top = Math.min(top, item.y * k - 31 * k); bottom = Math.max(bottom, item.y * k + PLATE * k + GAP + size + ROLE_LINE + 4);
    }
    return { left, right, top, bottom };
  };
  const fits = (k) => { const e = extent(k); return e.right - e.left <= w - pad * 2 && e.bottom - e.top <= h - pad * 2; };
  let k = maxK;
  if (!fits(k)) {
    let low = 0.001, high = k;
    for (let i = 0; i < 40; i++) { const mid = (low + high) / 2; if (fits(mid)) low = mid; else high = mid; }
    k = low;
  }
  const e = extent(k);
  return { k, x: w / 2 - (e.left + e.right) / 2, y: h / 2 - (e.top + e.bottom) / 2 };
}

// Where on a link its method name may go, best first: the middle, then outwards in steps.
const ALONG = [0.5, ...[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].flatMap((i) => [0.5 - i * 0.025, 0.5 + i * 0.025])];

/** Line, dot and label places per link id. Links between the same two nodes run side by side;
 * each status dot sits on its line just outside the node at that end. */
export function layoutLinks(links, positions, k = 1) {
  const groups = new Map();
  // A method name goes where it touches no device (plate, name or role) and no label placed before it.
  // Text is a fixed number of pixels, so at scale k its box is that many pixels over k in map units.
  const keepClear = Object.values(positions).flatMap((p) => [{ l: p.x - 30, r: p.x + 30, t: p.y - 30, b: p.y + 30 }, { l: p.x - 70 / k, r: p.x + 70 / k, t: p.y + PLATE + GAP / k, b: p.y + PLATE + (GAP + TEXT.desktop + ROLE_LINE + 4) / k }]);
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
      const x = round(start.x + (end.x - start.x) * along + side * (width / 2 + 8 / k)), y = round(start.y + (end.y - start.y) * along);
      const box = { l: x - width / 2, r: x + width / 2, t: y - 17 / k, b: y };
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
      const inset = Math.min(32, length * 0.3);
      const width = (Array.from(String(link.method || "")).length * 6.4 + 10) / k;
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
