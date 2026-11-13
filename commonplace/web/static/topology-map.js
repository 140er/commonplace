/* The Compute map canvas: machines, sites and the links between them, drawn as SVG from the
   topology store's graph and a status per node and per link end.

   mapModel      turns the read into what is drawn (the one place that knows the read's shape)
   renderScene   the SVG of one model at given positions
   createMapCore positions, view, route and what is remembered per workspace; no DOM
   createTopologyMap  the screen: mounts, pans, zooms, drags, polls and stops itself

   A status is always a shape and a text as well as a colour. Nothing here loads from
   another origin. */
import { escape as esc } from "./api.js";
import { KINDS, kindOf } from "./topology-icons.js";
import { createNodePanel } from "./topology-detail.js";
import { autoLayout, fitLabelled, fitView, layeredLayout, layoutLinks, toWorld, zoomAbout } from "./topology-layout.js";
import { DISC, GAP, plateOf, routeLinks, straightClear } from "./topology-route.js";
import { labelBox } from "./topology-text.js";

export const MAP_PATH = "/api/topology";
export const STATES = ["up", "degraded", "down", "unknown"];
export const STATUS_TEXT = { up: "Up", degraded: "Degraded", down: "Down", unknown: "Unknown" };
export const MAP_COPY = {
  loading: "Loading the map…",
  emptyTitle: "No machines on the map yet",
  emptyBody: "An agent can find your machines and how they reach each other. Nothing is typed in by hand.",
  start: "Start agent work on map",
  startOff: "Discovery is not connected in this version yet.",
  hint: "Drag the background to pan, scroll to zoom, drag a device to place it.",
  stage: "Map canvas. Arrow keys pan, plus and minus zoom, 0 fits everything. On a device, Enter selects it and Shift with an arrow key moves it.",
  failed: "The map could not be loaded.",
  notLive: "Not updating right now. Showing the last map that loaded.",
};
// Circle, triangle, square, ring: four outlines that differ without any colour.
const SHAPES = {
  up: '<circle r="5.5"/>',
  degraded: '<path d="M0 -6.5 6.6 5h-13.2z"/>',
  down: '<rect x="-5" y="-5" width="10" height="10" rx="1"/>',
  unknown: '<circle r="4.5"/>',
};

// "none" draws no mark: a node or link end whose state nobody claims (a caller opts in with `none`).
const stateOf = (value, none = false) => { const s = value && typeof value === "object" ? value.state : value; return STATES.includes(s) ? s : none && s === "none" ? "none" : "unknown"; };
const clip = (text, max) => { const chars = Array.from(String(text)); return chars.length > max ? `${chars.slice(0, max - 1).join("")}…` : chars.join(""); };
const text = (value) => (typeof value === "string" ? value : "");
const finite = (...values) => values.every((n) => typeof n === "number" && Number.isFinite(n));
/** The last known kind, name and role of a node whose facts expired: a faded icon needs them. */
const lastKnown = (record) => Object.fromEntries(["kind", "name", "role"].filter((name) => record.fields?.[name]?.stale && typeof record.fields[name].value === "string").map((name) => [name, record.fields[name].value]));

/** The drawable model of one read (`GET /api/topology`): `{ nodes, links, routes }`.
 * A link or route hop may name a node or link that has no facts; it is drawn as "not recorded"
 * instead of being dropped or invented. */
export function mapModel(payload, { none = false } = {}) {
  const graph = payload || {}, status = payload?.status || {};
  const nodes = [], seen = new Set(), links = [], linkIds = new Set();
  for (const record of graph.nodes || []) {
    if (typeof record?.key !== "string" || seen.has(record.key)) continue;
    seen.add(record.key);
    const values = { ...lastKnown(record), ...(record.values || {}) };
    nodes.push({ key: record.key, kind: kindOf(values.kind), name: text(values.name) || record.key, role: text(values.role), stale: !!record.stale, conflicted: !!record.conflicted, missing: false, status: stateOf(status.nodes?.[record.key], none) });
  }
  const ghost = (key) => { if (typeof key !== "string" || seen.has(key)) return; seen.add(key); nodes.push({ key, kind: "unknown", name: key, role: "", stale: false, conflicted: false, missing: true, status: "unknown" }); };
  const link = (id, from, to, method, extra) => {
    if (![id, from, to].every((part) => typeof part === "string") || linkIds.has(id)) return;
    linkIds.add(id); ghost(from); ghost(to);
    links.push({ id, from, to, method: text(method), ...extra });
  };
  for (const record of graph.links || []) {
    const ends = status.links?.[record?.id];
    link(record?.id, record?.from, record?.to, record?.method, { via: text(record?.values?.via), stale: !!record?.stale, conflicted: !!record?.conflicted, missing: false, status: { from: stateOf(ends?.from ?? ends, none), to: stateOf(ends?.to ?? ends, none) } });
  }
  const routes = [];
  for (const record of graph.routes || []) {
    if (typeof record?.site !== "string") continue;
    const hops = (record.hops || []).filter((hop) => typeof hop?.link === "string").map((hop) => ({ link: hop.link, from: hop.from, to: hop.to, method: text(hop.method), known: hop.known !== false, stale: !!hop.stale, status: stateOf(hop.status) }));
    for (const hop of hops) link(hop.link, hop.from, hop.to, hop.method, { via: "", stale: false, conflicted: false, missing: true, status: { from: "unknown", to: "unknown" } });
    routes.push({ site: record.site, stale: !!record.stale, hops });
  }
  return { nodes, links, routes };
}

export function statusDot(state, at, title, end = "") {
  if (state === "none") return "";
  return `<g class="topo-dot s-${state}" data-state="${state}"${end ? ` data-end="${end}"` : ""} transform="translate(${at.x} ${at.y})"><title>${esc(title)}</title>${SHAPES[state]}</g>`;
}

export const nodeLabel = (node, words = STATUS_TEXT) => `${node.name}, ${KINDS[node.kind].label}${node.role && node.role !== KINDS[node.kind].label ? `, ${node.role}` : ""}.${node.status === "none" ? "" : ` Status: ${words[node.status]}.`}${node.missing ? " Not recorded." : ""}${node.stale ? " Stale." : ""}${node.conflicted ? " Has a conflict." : ""}`;

const subOf = (node) => [node.role, node.missing && "not recorded", node.stale && "stale", node.conflicted && "conflict"].filter(Boolean).join(" · ");

// Counts keep an 11px screen size. Reserve their full box at the smallest fitted
// scale, beside the mark and away from the disc, labels and every link stroke.
function bundlePosition(key, count, model, positions, places, labels, selectedLink) {
  const origin = positions[key], width = String(count).length * 8 / K_LEAST, half = 8 / K_LEAST;
  const overlaps = (a, b) => a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
  const nodes = model.nodes.filter(node => positions[node.key]);
  const plates = labels ? nodes.map(node => plateOf({ ...node, height: labelBox(node.name, subOf(node), labels).height }, positions[node.key], labels, K_LEAST)) : [];
  const strokes = [...places.values()].flatMap(place => {
    const points = place.points || [{ x: place.x1, y: place.y1 }, { x: place.x2, y: place.y2 }];
    return points.slice(1).map((p, i) => [points[i], p]);
  });
  const clear = box => !plates.some(plate => overlaps(box, plate)) && !nodes.some(node => {
    const at = positions[node.key], x = Math.max(box.x, Math.min(at.x, box.x + box.width)), y = Math.max(box.y, Math.min(at.y, box.y + box.height));
    return Math.hypot(x - at.x, y - at.y) <= DISC + 2;
  });
  const crosses = (box, [a, b]) => {
    // Clip the stroke against the count box, including the selected stroke's width.
    let low = 0, high = 1;
    const dx = b.x - a.x, dy = b.y - a.y;
    for (const [p, q] of [[-dx, a.x - box.x + 2], [dx, box.x + box.width - a.x + 2], [-dy, a.y - box.y + 2], [dy, box.y + box.height - a.y + 2]]) {
      if (!p) { if (q < 0) return false; } else { const t = q / p; if (p < 0) low = Math.max(low, t); else high = Math.min(high, t); if (low > high) return false; }
    }
    return true;
  };
  const dots = nodes.map(node => ({ x: positions[node.key].x + 17, y: positions[node.key].y - 31, width: 14, height: 14 }));
  const selected = places.get(selectedLink);
  if (selected) for (const end of ["from", "to"]) dots.push({ x: selected[end].x - 7, y: selected[end].y - 7, width: 14, height: 14 });
  // A finite scene always leaves a free side farther out; prefer a nearby badge.
  for (let distance = 40; ; distance += 16) for (const side of [1, -1]) for (const y of [0, -20, 20]) {
    const at = { x: side * distance, y }, x = origin.x + at.x, cy = origin.y + y;
    const mark = { x: x - 7, y: cy - 7, width: 14, height: 14 };
    const box = { x: x + side * 12 - (side < 0 ? width : 0), y: cy - half, width, height: 2 * half };
    if (clear(mark) && clear(box) && !dots.some(dot => overlaps(mark, dot) || overlaps(box, dot)) && !strokes.some(stroke => crosses(box, stroke))) return { ...at, side };
  }
}

function nodeMarkup(node, at, { on, dim, sel, words, labels, bundle }) {
  const classes = ["topo-node", `k-${node.kind}`, `s-${node.status}`, node.stale && "stale", node.missing && "missing", on && "on", dim && "dim", sel && "sel"].filter(Boolean).join(" ");
  const sub = subOf(node);
  const bundleTitle = bundle ? `${bundle.count} links, worst ${node.name} end: ${words[bundle.state]}.` : "";
  const aria = esc(nodeLabel(node, words) + (bundleTitle ? ` ${bundleTitle}` : ""));
  const badge = bundle ? `<g class="topo-bundle" data-bundle-node="${esc(node.key)}" data-count="${bundle.count}" data-state="${bundle.state}" transform="translate(${bundle.x} ${bundle.y})">${statusDot(bundle.state, { x: 0, y: 0 }, bundleTitle)}<g transform="translate(${bundle.side * 12} 0)"><text class="topo-bundle-count" x="0" y="4" text-anchor="${bundle.side > 0 ? "start" : "end"}">${bundle.count}</text></g></g>` : "";
  // A label is drawn at a constant size on the screen: its group is scaled by the inverse of the view's scale
  // (the stage's --inv), so the type is the same size at every zoom. The plate hides a line that passes under it.
  // The hub's label sits above it: every line leaves the hub downwards.
  const hub = node.kind === "hub", box = labels ? labelBox(node.name, sub, labels) : null;
  const top = box ? (hub ? -GAP - box.height : GAP) : 0;
  const lines = (list, y, name) => (list.length ? `<text class="${name}" y="${y}">${list.map((line, i) => `<tspan x="0"${i ? ' dy="15"' : ""}>${esc(line)}</tspan>`).join("")}</text>` : "");
  const tag = box ? `<g class="topo-label${hub ? " above" : ""}" transform="translate(0 ${hub ? -DISC : DISC})"><rect class="topo-label-plate" x="${-labels / 2}" y="${top}" width="${labels}" height="${box.height}" rx="8"/>${lines(box.nameLines, top + 15, "topo-name")}${lines(box.roleLines, top + 15 + 15 * box.nameLines.length, "topo-role")}</g>` : "";
  const texts = box ? "" : `<text class="topo-name" y="45">${esc(clip(node.name, 24))}</text>${sub ? `<text class="topo-role" y="60">${esc(clip(sub, 36))}</text>` : ""}`;
  return `<g class="${classes}" data-key="${esc(node.key)}" transform="translate(${at.x} ${at.y})" tabindex="0" role="button" aria-pressed="${sel ? "true" : "false"}" aria-label="${aria}"><title>${aria}</title>${tag}<rect class="topo-plate" x="-27" y="-27" width="54" height="54" rx="10"/><g class="topo-icon">${KINDS[node.kind].icon}</g>${statusDot(node.status, { x: 24, y: -24 }, `Status: ${words[node.status]}`)}${texts}${badge}</g>`;
}

function linkMarkup(link, at, names, { on, dim, words, interactive, sel, bundles }) {
  const classes = ["topo-link", link.stale && "stale", link.missing && "missing", on && "on", dim && "dim"].filter(Boolean).join(" ");
  const method = link.method.replace(/_/g, " ");
  const from = names.get(link.from), to = names.get(link.to);
  const ends = [["from", from], ["to", to]].filter(([end]) => link.status[end] !== "none").map(([end, name]) => ` ${name} end: ${words[link.status[end]]}.`).join("");
  const label = `${from} to ${to}, ${method}${link.via ? ` via ${link.via}` : ""}.${ends}${link.missing ? " Not recorded." : ""}${link.stale ? " Stale." : ""}`;
  return `<g class="${classes}" data-link="${esc(link.id)}"${interactive ? ` tabindex="0" role="button" aria-pressed="${sel ? "true" : "false"}" aria-label="${esc(label)}"` : ""}><title>${esc(label)}</title>${at.points ? `<path class="topo-route" d="M${at.points.map((p) => `${p.x} ${p.y}`).join("L")}"/>` : `<line x1="${at.x1}" y1="${at.y1}" x2="${at.x2}" y2="${at.y2}"/>`}<text class="topo-method" x="${at.label.x}" y="${at.label.y}" dy="-5">${esc(method)}</text>${!sel && bundles.has(link.from) ? "" : statusDot(link.status.from, at.from, `${from} end: ${words[link.status.from]}`, "from")}${!sel && bundles.has(link.to) ? "" : statusDot(link.status.to, at.to, `${to} end: ${words[link.status.to]}`, "to")}</g>`;
}

/** The whole map as SVG markup. `route` is one of `model.routes`: its links and nodes are
 * marked `on` and everything else `dim`. `selected` is a node key. */
export function renderScene(model, positions, { route = null, selected = null, selectedLink = null, interactiveLinks = false, words = STATUS_TEXT, labels = 0, places: given = null } = {}) {
  const onLinks = new Set(route?.hops.map((hop) => hop.link));
  const onNodes = new Set(route ? [route.site, ...route.hops.flatMap((hop) => [hop.from, hop.to])] : []);
  const names = new Map(model.nodes.map((node) => [node.key, node.name]));
  const places = given || layoutLinks(model.links, positions);
  // Count incident ends before removing the selected link: a four-link bundle stays
  // collapsed while its selected link shows its own two marks. Classic keeps every mark.
  const bundles = new Map();
  if (interactiveLinks) {
    for (const link of model.links) if (places.has(link.id)) for (const end of ["from", "to"]) {
      const key = link[end];
      if (!bundles.has(key)) bundles.set(key, []);
      bundles.get(key).push({ id: link.id, state: link.status[end] });
    }
    for (const [key, ends] of bundles) {
      if (ends.length <= 3) { bundles.delete(key); continue; }
      const remaining = ends.filter(end => end.id !== selectedLink);
      // Match the status reader: worst known state; unknown only with no known state.
      const state = ["down", "degraded", "up", "unknown"].find(state => remaining.some(end => end.state === state)) || "none";
      bundles.set(key, { count: remaining.length, state, ...(state === "none" ? {} : bundlePosition(key, remaining.length, model, positions, places, labels, selectedLink)) });
    }
  }
  const links = model.links.filter((link) => places.has(link.id)).map((link) => linkMarkup(link, places.get(link.id), names, { on: onLinks.has(link.id) || selectedLink === link.id, dim: !!route && !onLinks.has(link.id) && selectedLink !== link.id, words, interactive: interactiveLinks, sel: selectedLink === link.id, bundles }));
  const nodes = model.nodes.filter((node) => positions[node.key]).map((node) => nodeMarkup(node, positions[node.key], { on: onNodes.has(node.key), dim: !!route && !onNodes.has(node.key), sel: node.key === selected, words, labels, bundle: bundles.get(node.key)?.state !== "none" ? bundles.get(node.key) : null }));
  return `<g class="topo-links">${links.join("")}</g><g class="topo-nodes">${nodes.join("")}</g>`;
}

/** A highlighted route in words: every hop with its method and status, unknown hops said plainly. */
export function routeText(route, model) {
  const names = new Map(model.nodes.map((node) => [node.key, node.name]));
  const name = (key) => names.get(key) || key;
  if (!route.hops.length) return `${name(route.site)}: no hops recorded.`;
  return `${name(route.hops[0].from)}${route.hops.map((hop) => ` → ${name(hop.to)} (${[hop.method.replace(/_/g, " "), STATUS_TEXT[hop.status], !hop.known && "not recorded", hop.stale && "stale"].filter(Boolean).join(", ")})`).join("")}`;
}

export function emptyState(canStart) {
  return `<div class="topo-empty"><h2>${esc(MAP_COPY.emptyTitle)}</h2><p>${esc(MAP_COPY.emptyBody)}</p><button type="button" class="btn primary" data-topo="start"${canStart ? "" : " disabled"}>${esc(MAP_COPY.start)}</button>${canStart ? "" : `<p class="topo-off">${esc(MAP_COPY.startOff)}</p>`}</div>`;
}

export function statusMark(state) {
  const value = STATES.includes(state) ? state : "unknown";
  return `<svg viewBox="-8 -8 16 16" aria-hidden="true"><g class="topo-dot s-${value}">${SHAPES[value]}</g></svg>`;
}

export function renderFrame({ words = STATUS_TEXT, legend: shown = STATES } = {}) {
  const legend = shown.map((state) => `<li>${statusMark(state)}${esc(words[state])}</li>`).join("");
  return `<section class="topo" data-topology aria-label="Compute map">
    <div class="topo-bar"><label>Route <select data-topo="route" aria-label="Highlight a site route"><option value="">None</option></select></label><span class="topo-spacer"></span><div class="topo-zoom" role="group" aria-label="Zoom"><button type="button" class="iconbtn bordered" data-topo="zoom-out" aria-label="Zoom out">−</button><button type="button" class="iconbtn bordered" data-topo="zoom-in" aria-label="Zoom in">+</button><button type="button" class="btn sm" data-topo="fit">Fit</button></div><button type="button" class="btn sm" data-topo="reset">Reset layout</button></div>
    <div class="topo-stage" data-topo="stage" tabindex="0" role="group" aria-label="${esc(MAP_COPY.stage)}"><svg class="topo-svg" aria-label="Topology"><g data-topo="world"></g></svg><div class="topo-note" data-topo="note"><p class="topo-msg" role="status">${esc(MAP_COPY.loading)}</p></div></div>
    <p class="topo-path" data-topo="path" role="status" hidden></p>
    <div class="topo-foot"><ul class="topo-legend" aria-label="Status legend">${legend}</ul><span class="topo-live" data-topo="live" role="status"></span><span class="topo-hint">${esc(MAP_COPY.hint)}</span></div>
  </section>`;
}

/** What one browser remembers for one workspace: the places devices were dragged to and the view. */
export function mapMemory(storage, workspace) {
  const key = `commonplace.topology.${workspace || "default"}`;
  return {
    load() {
      let data = null;
      try { data = JSON.parse(storage?.getItem(key) || "null"); } catch { /* Unreadable storage is an unplaced map. */ }
      const placed = {};
      if (data?.placed && typeof data.placed === "object") for (const [node, at] of Object.entries(data.placed)) if (finite(at?.x, at?.y)) placed[node] = { x: at.x, y: at.y };
      const view = finite(data?.view?.x, data?.view?.y, data?.view?.k) && data.view.k > 0 ? zoomAbout({ x: data.view.x, y: data.view.y, k: data.view.k }, 0, 0, 1) : null;
      return { placed, view };
    },
    save(data) { try { storage?.setItem(key, JSON.stringify(data)); } catch { /* The map still works for this visit. */ } },
  };
}

/** The map without a DOM. Dragged places and the view survive a reload through `storage`. */
export function createMapCore({ storage = null, workspace = "", words = STATUS_TEXT, none = false, labels = 0, columns = 0, layout = "auto", metrics = {}, route: routed = false } = {}) {
  let boxes = [];
  const nodeInfo = () => boxes;
  const memory = mapMemory(storage, workspace);
  let { placed, view } = memory.load();
  // A host that sizes the card to the graph (labels) always opens at the fitted view; only dragged places are kept.
  if (labels) view = null;
  let model = mapModel(null), raw = null, positions = {}, site = "", selected = null, selectedLink = null;
  const save = () => memory.save({ placed, view });
  let routes = null;
  const place = () => {
    // What each label needs: its height on the screen sets how far apart the rows are.
    boxes = model.nodes.map((node) => ({ key: node.key, kind: node.kind, sub: !!subOf(node), height: labels ? labelBox(node.name, subOf(node), labels).height : 0 }));
    const heights = new Map(boxes.map((box) => [box.key, box.height]));
    const auto = layout === "layered" ? layeredLayout(model.nodes, model.links, labels ? { ...metrics, below: (key) => heights.get(key) ?? 38, least: K_LEAST } : metrics) : autoLayout(model.nodes, model.links, { columns });
    positions = Object.fromEntries(model.nodes.map((node) => [node.key, placed[node.key] ? { ...placed[node.key] } : auto[node.key]]));
    routes = null;
  };
  // Where each link runs: a straight line when it touches no other device and no label, else routed around them
  // when the host asks. A link nobody could route keeps its straight line. Routes and plates are made for a
  // view no smaller than K_LEAST, which the first view never goes below.
  const linkPlaces = () => {
    const straight = layoutLinks(model.links, positions);
    if (!routed) return straight;
    routes ??= routeLinks(model.links, nodeInfo(), positions, labels || 160, K_LEAST) || new Map();
    const info = nodeInfo();
    return new Map(model.links.filter((link) => straight.has(link.id)).map((link) => [link.id, routes.has(link.id) && (routes.get(link.id).parallel || !straightClear(link.from, link.to, info, positions, labels || 160, K_LEAST)) ? routes.get(link.id) : straight.get(link.id)]));
  };
  const route = () => model.routes.find((item) => item.site === site) || null;
  return {
    get model() { return model; },
    get positions() { return positions; },
    get view() { return view; },
    get loaded() { return raw !== null; },
    get route() { return route(); },
    get selected() { return selected; },
    /** Take a read; false when it is the read already shown. */
    apply(payload) {
      const next = JSON.stringify(payload ?? null);
      if (next === raw) return false;
      raw = next; model = mapModel(payload, { none }); place();
      if (!route()) site = "";
      if (selected !== null && !positions[selected]) selected = null;
      if (!model.links.some((link) => link.id === selectedLink)) selectedLink = null;
      return true;
    },
    /** Move a node while it is being dragged; `moveNode` is what remembers the place. */
    dragNode(key, x, y) { if (positions[key] && finite(x, y)) { positions[key] = { x: Math.round(x), y: Math.round(y) }; routes = null; } },
    moveNode(key, x, y) {
      if (!positions[key] || !finite(x, y)) return false;
      placed[key] = { x: Math.round(x), y: Math.round(y) }; positions[key] = { ...placed[key] }; routes = null; save();
      return true;
    },
    setView(next, { remember = true } = {}) { view = { x: next.x, y: next.y, k: next.k }; if (remember) save(); },
    fit(width, height, options, { pad = 70, reach } = {}) {
      if (!labels) { this.setView(fitView(Object.values(positions), width, height, pad, reach), options); return null; }
      // Labels keep their size on the screen: fit the graph's width and let a taller graph pan.
      const heights = new Map(boxes.map((box) => [box.key, box]));
      const bends = routed ? [...linkPlaces().values()].flatMap((place) => place.points || []) : [];
      // A device reaches its disc one way and its label the other (the hub's label is above it).
      const points = [...Object.entries(positions).map(([key, at]) => { const box = heights.get(key), label = DISC + GAP + (box?.height || 38); return { ...at, rx: labels / 2, top: box?.kind === "hub" ? label : DISC, bottom: box?.kind === "hub" ? DISC : label }; }), ...bends.map((at) => ({ ...at, rx: 6, top: 6, bottom: 6 }))];
      const view = fitLabelled(points, width, height, { pad, most: 1, maxHeight: (globalThis.innerHeight || 0) * 2 || Infinity });
      this.setView(view, options);
      return view;
    },
    /** Forget every dragged place: back to the automatic layout. */
    reset() { placed = {}; place(); save(); },
    setRoute(next) { site = model.routes.some((item) => item.site === next) ? next : ""; },
    selectLink(id) { selectedLink = model.links.some((link) => link.id === id) && selectedLink !== id ? id : null; },
    select(key) { selected = positions[key] && key !== selected ? key : null; return selected; },
    /** Make `key` the selected node (not a toggle); false when the map has no such node. */
    choose(key) { if (!positions[key]) return false; selected = key; return true; },
    clear() { selected = null; },
    scene() { return renderScene(model, positions, { route: route(), selected, selectedLink, interactiveLinks: routed, words, labels, places: routed ? linkPlaces() : null }); },
    links() { return linkPlaces(); },
    get routed() { return routed; },
    /** What each device's label needs, for a host or a test that reasons about plates. */
    get boxes() { return boxes; },
  };
}

// Private browsing may refuse storage; the map then forgets places when the page closes.
function browserStorage() { try { return globalThis.localStorage || null; } catch { return null; } }

// The smallest scale the first view uses with constant-size labels: plates and routes are laid out for it.
export const K_LEAST = 0.85;
const ARROWS = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };

/** The Compute map screen. `show()` mounts it into `mount()`; it reads `path` again every
 * `interval` ms while the page is visible (never when `interval` is 0: the owner calls `refresh()`)
 * and stops by itself once its markup is replaced.
 * `onStartWork` is the action of the empty state's button; without one the button is off.
 * `look` is for a host that draws a different graph in its own words:
 *   words, legend  the text of each status and the statuses the legend lists
 *   none           lets a node or link end say "none": no mark
 *   labels         the width of a label in screen pixels: it is drawn at a constant size whatever the
 *                  zoom, names clip to it, and a plate in the card colour sits behind it (it hides
 *                  a line passing under it). The first view fits the graph's width (a taller graph
 *                  pans) and never shrinks below 0.85; 0 is the classic look
 *   pad            the space the first view keeps round the graph (32)
 *   grow           the card takes the height of the fitted graph (two screens at most) instead of a fixed one
 *   columns        most nodes in one row
 *   layout, metrics, route  "layered" rows with free lanes (metrics: gx, gy, lane) and links routed around
 *                  every device and label
 *   compact        the detail view says where a value came from once, in one quiet line
 *   own            `(key) => { physical, config, agents, sites }` tab bodies for nodes the host describes itself
 *   preselect      `(model) => key` chosen once, on the first read, when nobody chose yet. */
export function createTopologyMap({ mount, api, storage = browserStorage(), workspace = () => "", onStartWork = null, interval = 5000, path = MAP_PATH, look = {} }) {
  const { words = STATUS_TEXT, legend = STATES, none = false, labels = 0, columns = 0, own = null, preselect = null, layout = "auto", metrics = {}, route: routed = false, compact = false } = look;
  const reach = undefined, fitPad = look.pad ?? (labels ? 32 : 70);
  let section = null, stage = null, world = null, core = null, timer = null, run = 0, refused = false, error = "", stalled = false;
  let listeners = null, gesture = null, redraw = false, sites = null, sizes = null, panel = null, chosen = false;
  // True while the view is the automatic fit: it then follows the canvas when its size changes.
  let fitted = false;
  let nodeEls = new Map(), linkEls = new Map();
  const pointers = new Map();
  const part = (name) => section.querySelector(`[data-topo="${name}"]`);
  const size = () => [stage.clientWidth, stage.clientHeight];

  // The card is as tall as the fitted graph, so the page scrolls instead of the card (up to a cap).
  function grow(view) {
    if (look.grow && view?.height && Math.abs(stage.clientHeight - view.height) > 1) stage.style.height = `${view.height}px`;
  }
  function paintView() {
    const { x, y, k } = core.view;
    world.setAttribute("transform", `translate(${x} ${y}) scale(${k})`);
    // Constant-size labels: their groups are scaled by this, so the type does not follow the zoom.
    stage.style.setProperty?.("--inv", String(1 / k));
    stage.classList.toggle("far", k < 0.55);
    stage.classList.toggle("near", k >= 0.9);
    const step = 24 * k * (k < 0.4 ? 4 : 1);
    stage.style.backgroundSize = `${step}px ${step}px`;
    stage.style.backgroundPosition = `${x}px ${y}px`;
  }
  function paintNode(key) {
    const at = core.positions[key];
    nodeEls.get(key)?.setAttribute("transform", `translate(${at.x} ${at.y})`);
    // A routed link may take another way once a device has moved: draw the scene again.
    if (core.routed) { draw(); return; }
    const places = core.links();
    for (const link of core.model.links) {
      if (link.from !== key && link.to !== key) continue;
      const el = linkEls.get(link.id), g = places.get(link.id);
      if (!el || !g) continue;
      const line = el.querySelector("line"), label = el.querySelector("text");
      for (const name of ["x1", "y1", "x2", "y2"]) line.setAttribute(name, g[name]);
      label.setAttribute("x", g.label.x); label.setAttribute("y", g.label.y);
      for (const end of ["from", "to"]) el.querySelector(`[data-end="${end}"]`).setAttribute("transform", `translate(${g[end].x} ${g[end].y})`);
    }
  }
  function draw() {
    redraw = false;
    const focused = section.ownerDocument.activeElement?.closest?.("[data-key]")?.dataset.key;
    const focusedLink = section.ownerDocument.activeElement?.closest?.("[data-link]")?.dataset.link;
    world.innerHTML = core.scene();
    nodeEls = new Map([...world.querySelectorAll("[data-key]")].map((el) => [el.dataset.key, el]));
    linkEls = new Map([...world.querySelectorAll("[data-link]")].map((el) => [el.dataset.link, el]));
    // The first fit is automatic, so it is not remembered: only a view the person chose is.
    if (core.loaded && core.model.nodes.length && !core.view) { grow(core.fit(...size(), { remember: false }, { pad: fitPad, reach })); fitted = true; }
    if (core.view) paintView();
    if (focused) nodeEls.get(focused)?.focus({ preventScroll: true });
    if (focusedLink) linkEls.get(focusedLink)?.focus({ preventScroll: true });
    const list = core.model.routes.map((route) => route.site);
    if (JSON.stringify(list) !== sites) {
      sites = JSON.stringify(list);
      part("route").innerHTML = `<option value="">None</option>${list.map((site) => `<option value="${esc(site)}">${esc(site)}</option>`).join("")}`;
    }
    part("route").value = core.route?.site || "";
    part("route").disabled = !list.length;
    const route = core.route, trail = part("path");
    trail.hidden = !route;
    trail.textContent = route ? `Route of ${route.site}: ${routeText(route, core.model)}` : "";
    const note = part("note");
    const next = error ? `<div class="topo-empty" role="alert"><p>${esc(error)}</p>${refused ? "" : '<button type="button" class="btn" data-topo="retry">Retry</button>'}</div>`
      : !core.loaded ? `<p class="topo-msg" role="status">${esc(MAP_COPY.loading)}</p>`
        : core.model.nodes.length ? "" : emptyState(typeof onStartWork === "function");
    if (note.innerHTML !== next) note.innerHTML = next;
    part("live").textContent = stalled ? MAP_COPY.notLive : "";
  }
  const later = () => { if (gesture) redraw = true; else draw(); };

  async function load() {
    const mine = run;
    try {
      const payload = await api(path);
      if (mine !== run) return;
      error = ""; stalled = false;
      const before = core.selected;
      const changed = core.apply(payload);
      // A device that left the map takes its details with it.
      if (before !== null && core.selected === null) { chosen = false; announce(null); }
      if (!chosen && !core.selected && core.model.nodes.length && preselect) {
        const first = preselect(core.model);
        if (first && core.choose(first)) { chosen = true; announce(first); }
      }
      if (changed || part("note").innerHTML || part("live").textContent) later();
    } catch (failure) {
      if (mine !== run) return;
      refused = failure?.status === 403;
      if (core.loaded && !refused) stalled = true;
      else error = failure?.message || MAP_COPY.failed;
      later();
    }
  }
  async function tick() {
    timer = null;
    const mine = run;
    if (!section?.isConnected) { stop(); return; }
    if (!section.ownerDocument.hidden) await load();
    if (mine === run && !refused && interval > 0) timer = setTimeout(tick, interval);
  }
  function zoom(factor, px, py) {
    if (!core.view) return;
    const [width, height] = size();
    core.setView(zoomAbout(core.view, px ?? width / 2, py ?? height / 2, factor));
    fitted = false;
    paintView();
  }
  function fit(options) { grow(core.fit(...size(), options, { pad: fitPad, reach })); fitted = true; paintView(); }
  function announce(key) { section.dispatchEvent(new CustomEvent("topology:select", { bubbles: true, detail: { key } })); }
  function select(key) {
    const selected = core.select(key);
    chosen = true;
    draw();
    nodeEls.get(key)?.focus({ preventScroll: true });
    announce(selected);
  }
  function selectLink(id) {
    core.selectLink(id);
    draw();
    linkEls.get(id)?.focus({ preventScroll: true });
  }
  /** Select `key` from outside (not a toggle, no focus move); false when the map has no such node. */
  function choose(key) {
    if (!section || !core.choose(key)) return false;
    chosen = true;
    draw(); announce(key);
    return true;
  }
  const pinch = () => {
    const [a, b] = [...pointers.values()];
    return { distance: Math.hypot(a.x - b.x, a.y - b.y) || 1, x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  };

  function down(event) {
    // A press on the note (empty state, retry) belongs to its button, not to the canvas.
    if (event.button > 0 || !core.view || event.target.closest?.(".topo-note > *")) return;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    try { stage.setPointerCapture(event.pointerId); } catch { /* A pointer that already ended cannot be captured. */ }
    if (pointers.size === 2) { gesture = { kind: "pinch", last: pinch() }; return; }
    if (pointers.size > 2) return;
    const key = event.target.closest?.("[data-key]")?.dataset.key;
    const link = routed && event.target.closest?.("[data-link]")?.dataset.link;
    gesture = key && core.positions[key]
      ? { kind: "node", key, x: event.clientX, y: event.clientY, origin: { ...core.positions[key] }, moved: false }
      : link ? { kind: "link", id: link, moved: false } : { kind: "pan", moved: false };
  }
  function move(event) {
    const point = pointers.get(event.pointerId);
    if (!point || !gesture) return;
    const dx = event.clientX - point.x, dy = event.clientY - point.y;
    point.x = event.clientX; point.y = event.clientY;
    if (gesture.kind === "pinch") {
      if (pointers.size < 2) return;
      const now = pinch(), box = stage.getBoundingClientRect();
      const next = zoomAbout(core.view, now.x - box.left, now.y - box.top, now.distance / gesture.last.distance);
      core.setView({ ...next, x: next.x + now.x - gesture.last.x, y: next.y + now.y - gesture.last.y }, { remember: false });
      gesture.last = now; fitted = false;
      paintView();
    } else if (gesture.kind === "node") {
      const fx = event.clientX - gesture.x, fy = event.clientY - gesture.y;
      if (!gesture.moved && Math.hypot(fx, fy) < 4) return;
      gesture.moved = true;
      core.dragNode(gesture.key, gesture.origin.x + fx / core.view.k, gesture.origin.y + fy / core.view.k);
      paintNode(gesture.key);
    } else {
      gesture.moved = true; fitted = false;
      stage.classList.add("is-panning");
      core.setView({ ...core.view, x: core.view.x + dx, y: core.view.y + dy }, { remember: false });
      paintView();
    }
  }
  function up(event) {
    if (!pointers.delete(event.pointerId) || !gesture) return;
    const done = gesture;
    if (done.kind === "pinch" && pointers.size) { gesture = { kind: "pan", moved: true }; return; }
    gesture = null;
    stage.classList.remove("is-panning");
    if (done.kind === "node" && done.moved) {
      if (event.type === "pointercancel") { core.dragNode(done.key, done.origin.x, done.origin.y); paintNode(done.key); }
      else { const at = core.positions[done.key]; core.moveNode(done.key, at.x, at.y); }
    } else if (done.kind === "node") {
      if (event.type !== "pointercancel") select(done.key);
    } else if (done.kind === "link" && !done.moved && event.type !== "pointercancel") selectLink(done.id);
    else core.setView(core.view);
    if (redraw) draw();
  }
  function wheel(event) {
    event.preventDefault();
    const box = stage.getBoundingClientRect();
    const delta = event.deltaY * (event.deltaMode === 1 ? 16 : 1);
    zoom(Math.exp(-delta * 0.0015), event.clientX - box.left, event.clientY - box.top);
  }
  function keys(event) {
    if (event.ctrlKey || event.metaKey || event.altKey || !core.view) return;
    const key = event.target.closest?.("[data-key]")?.dataset.key;
    if (key && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); select(key); return; }
    const link = routed && event.target.closest?.("[data-link]")?.dataset.link;
    if (link && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); selectLink(link); return; }
    const arrow = ARROWS[event.key];
    if (arrow && key && event.shiftKey) {
      event.preventDefault();
      const at = core.positions[key];
      core.moveNode(key, at.x + arrow[0] * 20, at.y + arrow[1] * 20);
      paintNode(key);
    } else if (arrow) {
      event.preventDefault();
      core.setView({ ...core.view, x: core.view.x - arrow[0] * 60, y: core.view.y - arrow[1] * 60 });
      fitted = false;
      paintView();
    } else if (event.key === "+" || event.key === "=") { event.preventDefault(); zoom(1.25); }
    else if (event.key === "-" || event.key === "_") { event.preventDefault(); zoom(0.8); }
    else if (event.key === "0") { event.preventDefault(); fit(); }
  }
  function click(event) {
    const action = event.target.closest?.("[data-topo]")?.dataset.topo;
    if (action === "zoom-in") zoom(1.25);
    else if (action === "zoom-out") zoom(0.8);
    else if (action === "fit" && core.view) fit();
    else if (action === "reset") { core.reset(); draw(); if (core.view) fit(); }
    else if (action === "retry") { error = ""; draw(); void load(); }
    else if (action === "start" && typeof onStartWork === "function") {
      section.dispatchEvent(new CustomEvent("topology:start-work", { bubbles: true }));
      onStartWork();
    }
  }

  function stop() {
    run += 1;
    clearTimeout(timer); timer = null;
    listeners?.abort(); listeners = null;
    sizes?.disconnect(); sizes = null;
    pointers.clear(); gesture = null;
    section = null;
  }
  function show() {
    stop();
    const host = mount();
    if (!host) return;
    host.innerHTML = renderFrame({ words, legend });
    section = host.querySelector("[data-topology]");
    stage = part("stage"); world = part("world");
    core = createMapCore({ storage, workspace: workspace(), words, none, labels, columns, layout, metrics, route: routed });
    refused = false; error = ""; stalled = false; redraw = false; sites = null; fitted = false; chosen = false;
    listeners = new AbortController();
    const on = (target, kind, handler, options = {}) => target.addEventListener(kind, handler, { ...options, signal: listeners.signal });
    on(stage, "pointerdown", down); on(stage, "pointermove", move); on(stage, "pointerup", up); on(stage, "pointercancel", up);
    on(stage, "wheel", wheel, { passive: false });
    on(stage, "keydown", keys);
    on(section, "click", click);
    panel = createNodePanel({ section, api, model: () => core.model, interval, own, compact });
    on(part("route"), "change", (event) => { core.setRoute(event.target.value); draw(); });
    if (typeof ResizeObserver === "function") {
      sizes = new ResizeObserver(() => { if (fitted && core.view && !gesture) fit({ remember: false }); });
      sizes.observe(stage);
    }
    void tick();
  }
  return { show, stop, select: choose, refresh: () => { if (!section?.isConnected) return undefined; panel?.refresh(); return load(); } };
}
