import { escape as e } from './api.js';
import { MAP_PATH, createTopologyMap, mapModel } from './topology-map.js';
import { discoveryAction } from './map-discovery.js';
import { addAction } from './map-add.js';

export const TITLE = 'Compute';
const HUB = 'commonplace-hub';
const STATES = { ready: 'Ready', waiting: 'Waiting for attestation', off: 'Off' };
// The runners fallback speaks in enrollment states, never in liveness: Ready is "owner attested and enabled".
const MARK = { ready: 'up', waiting: 'degraded', off: 'unknown' };
const RUNNER_WORDS = { up: STATES.ready, degraded: STATES.waiting, down: 'Down', unknown: STATES.off };
const MACHINE_KINDS = new Set(['pc', 'mac', 'phone', 'vm', 'server', 'container']);
// Map units: a label's plate and the gaps of the layered layout (a link runs in the gap between neighbours).
// `labels` is the width of a label's plate in screen pixels (labels keep their size at every zoom); gx is
// the room of a device in a row, laid out for a view of 0.85 or more, so plates never meet; `lane` the width
// kept free in a row for links that pass it.
export const METRICS = { desktop: { labels: 140, gx: 186, lane: 56, pad: 32 }, phone: { labels: 124, gx: 165, lane: 36, pad: 16 } };
const GENERIC_FAILURE = 'Machines could not be loaded. Try again.';
/** How many devices a row holds at `width` (the card's): as many as fit at the plate width, down to a scale of
 * 0.85, with room for `lanes` lanes of passing links in the row. */
export const columnsFor = (width, m, lanes = 0) => Math.max(1, Math.floor((width - 2 * m.pad - m.labels - lanes * m.lane * 0.85) / (m.gx * 0.85)) + 1);
/** A star of `machines` (a single row, no lane needed, while they all fit) or the topology (`null`: lanes for passing links). */
export const layoutColumns = (width, m, machines = null) => (machines !== null && machines <= columnsFor(width, m) ? columnsFor(width, m) : columnsFor(width, m, 2));
let stylesheet;
function styles(doc) {
  if (!doc || !globalThis.CSSStyleSheet) return;
  if (!stylesheet) {
    stylesheet = new CSSStyleSheet();
    stylesheet.replaceSync(`
.deck-compute#content { gap: 0; overflow: auto; padding: 24px max(24px, calc((100% - 1200px) / 2)) 24px; color: var(--text); }
.deck-compute .dc-title { display: flex; align-items: center; flex-wrap: wrap; gap: 12px 16px; margin-bottom: 20px; }
.deck-compute h1 { font-size: 24px; line-height: 32px; font-weight: 600; letter-spacing: -.02em; }
.deck-compute .dc-summary { margin: 0; color: var(--text-2); font-size: 14px; }
.deck-compute .dc-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-left: auto; }
.deck-compute .dc-action { display: inline-flex; align-items: center; justify-content: center; min-height: 40px; padding: 0 18px; border: 0; border-radius: 999px; background: var(--accent-weak); color: var(--text); text-decoration: none; font-size: 14px; font-weight: 500; }
.deck-compute .dc-primary { background: var(--text); color: var(--bg); }
.deck-compute .dc-action:hover { background: var(--well); }
.deck-compute .dc-primary:hover { background: var(--text-2); }
.deck-compute .dc-action:focus-visible { outline: 2px solid var(--text); outline-offset: 3px; }
.deck-compute .dc-notice:empty { display: none; }
.deck-compute .dc-notice { margin: 12px 0; font-size: 14px; color: var(--text-2); }
.deck-compute .dc-empty { margin: 0 0 16px; color: var(--text-2); }
.deck-compute .dc-map { min-width: 0; }
/* The one map: the shared topology canvas in Deck tokens. At 1280: 860px canvas + 14px gap + 326px details. */
.deck-compute .topo { flex: none; position: static; display: grid; grid-template-columns: minmax(0, 1fr) 326px; column-gap: 14px; align-items: start; background: none; color: var(--text); }
.deck-compute .topo:not(:has(.topo-detail:not([hidden]))) { grid-template-columns: minmax(0, 1fr); }
.deck-compute .topo-stage { grid-area: 1 / 1; flex: none; min-height: 0; height: max(440px, calc(100dvh - 196px)); border-radius: 20px; background: var(--surface); background-image: none; }
.deck-compute .topo-stage:focus-visible { outline: 2px solid var(--text); outline-offset: 3px; }
.deck-compute .topo-path { grid-area: 2 / 1; padding: 12px 16px 0; border: 0; color: var(--text-2); }
.deck-compute .topo-foot { grid-area: 3 / 1; padding: 12px 16px 0; border: 0; color: var(--text-3); font-size: 12px; }
.deck-compute .topo-hint { display: none; }
.deck-compute .topo-legend svg { width: 12px; height: 12px; }
.deck-compute .topo-live { color: var(--text-2); }
.deck-compute .topo-bar { grid-area: 3 / 1; z-index: 1; justify-self: end; padding: 6px 0 0; border: 0; gap: 8px; }
.deck-compute .topo-bar label { color: var(--text-3); font-size: 12px; }
.deck-compute .topo-bar select { min-height: 36px; border: 0; border-radius: 18px; background: var(--well); color: var(--text); }
.deck-compute .topo-bar .btn, .deck-compute .topo-bar .iconbtn { min-height: 36px; background: var(--well); color: var(--text); border: 0; }
.deck-compute .topo-bar .iconbtn { width: 36px; border-radius: 18px; }
.deck-compute .topo-bar .btn:focus-visible, .deck-compute .topo-bar .iconbtn:focus-visible, .deck-compute .topo-bar select:focus-visible { outline: 2px solid var(--text); outline-offset: 2px; }
/* Discs sit one lightness step above the card; the chosen node is inverted. Status stays a shape. */
.deck-compute .topo-plate { fill: var(--well); stroke: none; rx: 27px; ry: 27px; }
.deck-compute .topo-node:hover .topo-plate { fill: var(--accent-weak); stroke: none; }
.deck-compute .topo-node.on .topo-plate { stroke: var(--text); stroke-width: 2; }
.deck-compute .topo-node.sel .topo-plate { fill: var(--text); stroke: none; }
.deck-compute .topo-node.sel { color: var(--bg); }
.deck-compute .topo-node.stale .topo-plate, .deck-compute .topo-node.missing .topo-plate { stroke: var(--line-strong); stroke-width: 1.5; stroke-dasharray: 4 3; }
.deck-compute .topo-node:focus-visible .topo-plate { stroke: var(--text-2); stroke-width: 3; stroke-dasharray: 4 3; }
.deck-compute .topo-node.sel:focus-visible .topo-plate { stroke: var(--bg); }
.deck-compute .topo-label-plate { fill: var(--surface); stroke: none; }
/* A label keeps its size on the screen at every zoom: its group is scaled by the stage's --inv (1 / zoom). */
.deck-compute .topo-label { transform: translate(0, 27px) scale(var(--inv, 1)); }
.deck-compute .topo-label.above { transform: translate(0, -27px) scale(var(--inv, 1)); }
.deck-compute .topo-name { font-size: 12px; }
.deck-compute .topo-role { font-size: 11px; }
.deck-compute .topo-stage.far .topo-role { display: inline; }
.deck-compute .topo-name, .deck-compute .topo-role, .deck-compute .topo-method { stroke: var(--surface); }
.deck-compute .topo-name { fill: var(--text); font-weight: 500; }
.deck-compute .topo-role { fill: var(--text-3); }
.deck-compute .topo-method { fill: var(--text-3); }
.deck-compute .topo-link line, .deck-compute .topo-route { stroke: var(--line-strong); }
.deck-compute .topo-route { fill: none; stroke-width: 1.5; stroke-linejoin: round; stroke-linecap: round; }
.deck-compute .topo-link.stale .topo-route, .deck-compute .topo-link.missing .topo-route { stroke-dasharray: 5 4; }
.deck-compute .topo-link.on line, .deck-compute .topo-link.on .topo-route { stroke: var(--text); stroke-width: 3; }
.deck-compute .topo-dot { stroke: var(--surface); }
.deck-compute .topo-dot.s-up { fill: var(--ok); }
.deck-compute .topo-dot.s-degraded { fill: var(--text-2); }
.deck-compute .topo-dot.s-down { fill: var(--block); }
.deck-compute .topo-dot.s-unknown { fill: var(--surface); stroke: var(--text-3); stroke-width: 2; }
/* Amber means one thing: a machine waiting on the person to attest it. */
.deck-compute.dc-runners .topo-dot.s-degraded { fill: var(--wait); }
/* The method of a link shows only along a highlighted route: at full size it would sit over other links. */
.deck-compute .topo-stage .topo-method { display: none; }
.deck-compute .topo-link.on .topo-method { display: inline; }
.deck-compute .topo-note .topo-empty { border: 0; border-radius: 20px; background: var(--well); }
/* The details card: Physical, Config, Agents, Sites for the node chosen on the canvas. */
.deck-compute .topo-detail { grid-area: 1 / 2 / 4 / 3; position: static; z-index: auto; width: auto; height: auto; max-height: max(440px, calc(100dvh - 196px)); border: 0; border-radius: 20px; background: var(--surface); box-shadow: none; padding: 10px 8px 6px; }
.deck-compute .td-head { padding: 12px 14px 8px; }
.deck-compute .td-title { font-weight: 600; }
.deck-compute .td-tabs { gap: 14px; padding: 0 14px; }
.deck-compute .td-tabs [role="tab"] { min-height: 44px; min-width: 44px; padding: 0; color: var(--text-3); font-size: 14px; }
.deck-compute .td-tabs [role="tab"][aria-selected="true"] { color: var(--text); border-bottom-color: var(--text); font-weight: 500; }
.deck-compute .td-tabs [role="tab"]:focus-visible, .deck-compute .td-body:focus-visible { outline: 2px solid var(--text); outline-offset: -2px; }
.deck-compute .topo-detail .iconbtn { flex: none; width: 36px; height: 36px; min-width: 36px; padding: 0; display: inline-grid; place-items: center; border-radius: 50%; }
.deck-compute .topo-detail .iconbtn:focus-visible { outline: 2px solid var(--text); outline-offset: 2px; }
.deck-compute .td-row dd { min-width: 0; gap: 2px; }
.deck-compute .td-row dt { color: var(--text-2); }
/* Where a value came from is one quiet line under it, never louder than the value or its label. */
.deck-compute .td-row .td-meta { display: block; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; font-size: 11.5px; line-height: 16px; color: var(--text-3); }
.deck-compute .td-copy { border: 0; background: var(--bg); }
.deck-compute .td-foot { border-top-color: var(--line); }
@media (max-width: 999px) {
.deck-compute .topo { grid-template-columns: minmax(0, 1fr); row-gap: 0; }
.deck-compute .topo-bar { grid-area: 4 / 1; justify-self: start; padding-top: 12px; }
.deck-compute .topo-detail { grid-area: 5 / 1; max-height: none; margin-top: 18px; }
}
@media (max-width: 699px) {
/* Room under the page for the tab bar (56px and its inset), so the legend, the controls and the details can be scrolled to. */
.deck-compute#content { padding: 18px 16px calc(96px + env(safe-area-inset-bottom, 0px)); }
.deck-compute .dc-title { flex-direction: column; align-items: stretch; gap: 10px; margin-bottom: 18px; }
.deck-compute .dc-summary { width: 100%; }
.deck-compute .dc-actions { width: 100%; margin-left: 0; gap: 8px; }
.deck-compute .dc-action { flex: 0 0 auto; min-height: 44px; padding: 0 14px; }
.deck-compute .dc-primary { flex: 1 1 auto; }
.deck-compute .topo-stage { height: clamp(360px, calc(100dvh - 350px), 520px); }
.deck-compute .topo-foot { padding-left: 4px; padding-right: 4px; }
.deck-compute .topo-bar .btn, .deck-compute .topo-bar .iconbtn, .deck-compute .topo-bar select { min-height: 44px; }
.deck-compute .topo-bar .iconbtn { width: 44px; border-radius: 22px; }
.deck-compute .topo-bar [data-topo="reset"] { display: none; }
}
`);
  }
  if (!doc.adoptedStyleSheets.includes(stylesheet)) doc.adoptedStyleSheets = [...doc.adoptedStyleSheets, stylesheet];
}

// Readiness is an enrollment state, not a claim of liveness or independent verification.
export function machineState(machine) {
  return !machine.enabled ? 'off' : machine.binding_state === 'owner_attested' ? 'ready' : 'waiting';
}

/** The hub and the enrolled runner machines in the shape of `GET /api/topology`, so the one canvas can
 * draw them when the topology read is empty or fails. The hub carries no status; a machine carries its
 * enrollment state (Ready, Waiting for attestation, Off) once, on the node, and nothing is said about sites or routes. */
export function runnerGraph(rows) {
  const nodes = [{ key: HUB, values: { kind: 'hub', name: 'Commonplace', role: 'Hub' } }];
  const links = [], status = { nodes: { [HUB]: 'none' }, links: {} };
  for (const machine of rows) {
    const state = machineState(machine), id = `${HUB}>${machine.id}`;
    nodes.push({ key: machine.id, values: { kind: 'server', name: machine.label, role: STATES[state] } });
    links.push({ id, from: HUB, to: machine.id, method: 'runner' });
    status.nodes[machine.id] = MARK[state];
    status.links[id] = { from: 'none', to: 'none' };
  }
  return { nodes, links, routes: [], status };
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
/** One sentence about what the read holds. */
export function summaryText({ mode, rows, payload }) {
  if (mode === 'runners') return `${rows.filter(m => machineState(m) === 'ready').length} of ${plural(rows.length, 'machine')} ready.`;
  const nodes = mapModel(payload).nodes, machines = nodes.filter(n => MACHINE_KINDS.has(n.kind)), sites = nodes.filter(n => n.kind === 'website');
  const count = status => machines.filter(n => n.status === status).length;
  const notes = [[count('degraded'), 'degraded'], [count('down'), 'down']].filter(([n]) => n).map(([n, word]) => `${n} ${word}`);
  const text = machines.length ? `${count('up')} of ${plural(machines.length, 'machine')} up${notes.length ? ` (${notes.join(', ')})` : ''}.` : '';
  return sites.length ? `${text ? `${text} ` : ''}${plural(sites.length, 'site')}.` : text || `${plural(nodes.length, 'device')} on the map.`;
}

export function mount(context) {
  let ctx = context, rows = [], general = null, mode = '', latest = null, map = null, built = '';
  let stopped = false, epoch = 0, request = null, framed = false, phone = false, builtColumns = 0, observer = null;
  const root = context.root, doc = root.ownerDocument || globalThis.document;
  styles(doc);
  root.classList.add('deck-compute');
  // The screen owns its title row; restore the shared header on departure.
  const shellHeaders = ['.head', '#phone-bar'].map(selector => root.parentElement?.querySelector(selector)).filter(Boolean).map(node => ({ node, hidden: node.hidden }));
  for (const { node } of shellHeaders) node.hidden = true;
  // Keep the shell's phone destinations reachable while the long map/details page scrolls.
  const phoneTabs = root.parentElement?.querySelector('#phone-tabs');
  const navStyle = phoneTabs?.style;
  const previousNav = navStyle && ['position', 'bottom', 'left', 'right', 'zIndex'].map(key => [key, navStyle[key]]);
  if (navStyle) Object.assign(navStyle, { position: 'fixed', bottom: '0', left: '0', right: '0', zIndex: '10' });
  const region = name => root.querySelector(`[data-compute-region="${name}"]`);
  const narrow = () => globalThis.matchMedia?.('(max-width:699px)').matches === true;
  const note = text => { const host = region('notice'); if (host) host.textContent = text; return Boolean(host); };
  const add = addAction({ api: (path, options) => ctx.api(path, options), note });

  // What the details card shows for a node this screen describes itself: the runners fallback has no topology facts.
  function own(key) {
    if (mode !== 'runners') return null;
    const machine = rows.find(m => m.id === key), plain = text => `<p class="td-empty">${e(text)}</p>`;
    let config;
    if (key === HUB) config = plain('No machine configuration is recorded for the hub.');
    else if (machine) {
      const list = value => Array.isArray(value) ? value.length ? value.join(', ') : 'None granted' : 'Not recorded';
      const facts = [['Projects', list(machine.projects)], ['Topics', list(machine.topics)], ['Wings', list(machine.wings)], ['Longest runtime mode', machine.max_runtime_mode || 'Not recorded'], ['Trust', machine.binding_state === 'owner_attested' ? 'Attested by the owner, not verified' : 'Not attested, not verified']];
      config = `<dl class="td-list">${facts.map(([name, value]) => `<div class="td-row"><dt>${e(name)}</dt><dd><span class="td-val">${e(value)}</span></dd></div>`).join('')}</dl>`;
    } else return null;
    return { physical: plain('No physical details are recorded yet.'), config, agents: plain('Running work is not reported for this machine.'), sites: plain('No sites are recorded yet.') };
  }
  // A machine to start on: the first computer, else whatever is first.
  const preselect = model => (model.nodes.find(n => MACHINE_KINDS.has(n.kind)) || model.nodes[0])?.key;
  // The width of the map card: the screen's column, less the details card beside it on a wide screen.
  function cardWidth() {
    const host = region('map')?.clientWidth || 0, wide = globalThis.matchMedia?.('(min-width:1000px)').matches === true;
    return host ? (wide ? host - 340 : host) : narrow() ? 358 : 860;
  }
  const resized = () => {
    if (stopped || !map) return;
    const metrics = narrow() ? METRICS.phone : METRICS.desktop;
    if (layoutColumns(cardWidth(), metrics, mode === 'runners' ? rows.length : null) !== builtColumns) build();
  };
  function build() {
    map?.stop();
    phone = narrow();
    const metrics = { ...(phone ? METRICS.phone : METRICS.desktop) };
    metrics.columns = layoutColumns(cardWidth(), metrics, mode === 'runners' ? rows.length : null);
    builtColumns = metrics.columns;
    const runners = mode === 'runners';
    built = mode;
    map = createTopologyMap({
      mount: () => region('map'),
      // The screen reads the topology itself (refresh drives it); the canvas asks for what was read.
      api: (path, options) => path === MAP_PATH ? Promise.resolve(latest) : ctx.api(path, options),
      workspace: () => `deck:${runners ? 'runners:' : ''}${ctx.me?.workspace || ''}`,
      onStartWork: discoveryAction({ api: (path, options) => ctx.api(path, options), root: () => region('map') }),
      interval: 0,
      look: { labels: metrics.labels, pad: metrics.pad, grow: true, preselect, compact: true, layout: 'layered', metrics, route: true, ...(runners ? { words: RUNNER_WORDS, legend: ['up', 'degraded', 'unknown'], none: true, own } : {}) },
    });
    map.show();
    if (globalThis.ResizeObserver && !observer && region('map')) { observer = new ResizeObserver(resized); observer.observe(region('map')); }
  }
  function actions() {
    const host = region('actions');
    if (host) host.innerHTML = '<button class="dc-action" type="button" data-compute-action="add-machine">Add machine</button><button class="dc-action" type="button" data-compute-action="add-site">Add site</button>' + (general ? '<button class="dc-action dc-primary" type="button" data-compute-action="map">Let a bot map it</button>' : '');
  }
  function frame() {
    map?.stop(); map = null; built = ''; observer?.disconnect(); observer = null;
    root.innerHTML = '<header class="dc-title"><h1>Compute</h1><p class="dc-summary" data-compute-region="summary"></p><div class="dc-actions" data-compute-region="actions"></div></header><p class="dc-empty" data-compute-region="empty" hidden>No machines yet. Add a machine to begin.</p><p class="dc-notice" data-compute-region="notice" role="status"></p><div class="dc-map" data-compute-region="map"></div>';
    framed = true; actions();
  }
  function apply(read) {
    mode = read.mode; rows = read.rows; latest = read.payload;
    root.classList.toggle('dc-runners', mode === 'runners');
    if (!framed) frame();
    region('summary').textContent = summaryText({ mode, rows, payload: latest });
    region('empty').hidden = !(mode === 'runners' && !rows.length);
    note('');
    if (!map || built !== mode || phone !== narrow()) build(); else void map.refresh();
  }
  /** The topology when it holds any node; otherwise the enrolled runners, which also covers a failed topology read. */
  async function read(options) {
    try {
      const topology = await ctx.api(MAP_PATH, options);
      if (Array.isArray(topology?.nodes) && topology.nodes.length) return { mode: 'topology', rows: [], payload: topology };
    } catch (error) { if (error?.status === 403 || options.signal.aborted) throw error; }
    const result = await ctx.api('/api/runners', options);
    const machines = (Array.isArray(result?.machines) ? result.machines : []).filter(m => !m.revoked_at).map(m => ({ ...m, id: String(m.id ?? m.machine_id) }));
    return { mode: 'runners', rows: machines, payload: runnerGraph(machines) };
  }
  async function refresh(next = {}) {
    ctx = { ...ctx, ...next }; const mine = ++epoch;
    request?.abort(); request = new AbortController();
    const options = { signal: request.signal, timeoutMs: 15000 };
    try {
      const result = await read(options);
      if (stopped || mine !== epoch) return;
      apply(result);
      try {
        const bots = await ctx.api('/api/bots', options);
        if (stopped || mine !== epoch) return;
        general = (Array.isArray(bots?.bots) ? bots.bots : []).find(b => b.kind === 'general') || null;
      } catch { if (stopped || mine !== epoch) return; general = null; }
      actions();
    } catch (error) {
      if (stopped || mine !== epoch) return;
      if (error?.status === 403) {
        map?.stop(); map = null; built = ''; framed = false; rows = []; general = null; latest = null;
        root.innerHTML = '<h1>Compute</h1><p class="dc-notice">Only workspace owners can see the machines.</p>';
      } else if (!note(GENERIC_FAILURE)) root.innerHTML = `<h1>Compute</h1><p class="dc-notice" role="status">${GENERIC_FAILURE}</p>`;
    }
  }
  function click(event) {
    const target = event.target.closest?.('[data-compute-action]'), action = target?.dataset.computeAction;
    if (action === 'add-machine' || action === 'add-site') { void add(action, target); return; }
    if (!action || !general) return;
    try {
      globalThis.sessionStorage.setItem('cmmn.deck.draft', JSON.stringify({ bot: general.id, text: 'Map my machines and sites.' }));
      ctx.go(`#/bots/${encodeURIComponent(general.id)}`);
    } catch { note('The message could not be prepared. Try again.'); }
  }
  root.addEventListener('click', click);
  root.innerHTML = '<h1>Compute</h1><p class="dc-notice" role="status">Loading machines…</p>';
  void refresh();
  return { refresh, stop() {
    if (stopped) return;
    stopped = true; epoch++; request?.abort(); map?.stop(); map = null; observer?.disconnect();
    root.removeEventListener('click', click);
    root.classList.remove('deck-compute', 'dc-runners'); root.innerHTML = '';
    for (const { node, hidden } of shellHeaders) node.hidden = hidden;
    if (navStyle) for (const [key, value] of previousNav) navStyle[key] = value;
  } };
}
