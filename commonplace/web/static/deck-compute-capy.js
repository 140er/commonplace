// Capy presentation for the existing topology canvas. No enrollment or discovery writes live here.
import { escape as e } from './api.js';
import { panelHTML, panelCloseHTML } from './topology-detail.js';
import { stateHTML, effectiveState } from './deck-compute-state.js';
import { linkName } from './deck-compute-scene.js';

const PHONE_CLOSE = '<button type="button" class="dc-back" data-td="close" aria-label="Back to machines">Back</button>';

export const ENROLL_ROUTE = '#/admin';
export const COMPUTE_COPY = {
  add: 'Add a machine',
  intro: 'Your machines and how your bots reach them. Drag to arrange.',
  setup: 'Run one command on the machine, then accept the connection. It connects out, so you open no ports.',
};
export const HEALTH_WORDS = { up: 'Healthy', degraded: 'Degraded', down: 'Unreachable', unknown: 'Not reported' };
const number = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
const words = value => typeof value === 'string' ? value : '';
const amount = value => new Intl.NumberFormat('en', { maximumFractionDigits: 1 }).format(value);
const plain = value => ({ ok: 'Healthy', healthy: 'Healthy', running: 'Running', stopped: 'Stopped', idle: 'Idle', busy: 'Busy' })[String(value).toLowerCase()] || words(value);

/** Values are explicitly recorded facts only. A capacity is not a usage measurement; absent fields hide. */
export function inspectorModel({ record = {}, detail = {}, node = {} } = {}) {
  const values = { ...(record.stale ? {} : record.values || {}) };
  for (const [name, entry] of Object.entries(detail.fields || {})) if (!entry.stale) values[name] = entry.value;
  const resources = values.resources || {};
  const metrics = [];
  const capacity = (label, used, total) => {
    used = number(used); total = number(total);
    if (total === null && used === null) return;
    metrics.push({ label, text: used !== null && total !== null ? `${amount(used)} of ${amount(total)} GB` : `${amount(total ?? used)} GB${total === null ? ' used' : ' capacity'}`, percent: used !== null && total > 0 ? Math.min(100, used / total * 100) : null });
  };
  capacity('Memory', resources.memory_used_gb, resources.memory_gb);
  const cpu = number(resources.cpu_percent);
  if (cpu !== null && cpu <= 100) metrics.push({ label: 'CPU', text: `${amount(cpu)}%`, percent: cpu });
  capacity('Disk', resources.storage_used_gb, resources.storage_gb);
  const fields = [];
  const add = (label, value) => { if (value !== '' && value !== null && value !== undefined) fields.push({ label, value: String(value) }); };
  add('Service', plain(values.service));
  add('Health', values.health ? plain(values.health) : HEALTH_WORDS[node.status]);
  add('Memory kills', number(values.oom_kill));
  add('Version', words(values.version));
  if (Array.isArray(values.bots)) add('Bots', values.bots.filter(v => typeof v === 'string').join(', '));
  const healthState = ({ healthy:'up', ok:'up', up:'up', degraded:'degraded', down:'down', unreachable:'down' })[String(values.health || '').toLowerCase()] || effectiveState(node);
  return { healthState, name: words(node.name) || words(values.name), role: words(values.role), metrics, fields, stale: !!record.stale || !!detail.stale };
}

export function inspectorHTML(model) {
  return `<div class="dc-inspector">${model.stale ? '<p class="dc-observed">Some observations are out of date.</p>' : ''}${model.metrics.map(metric => `<div class="dc-metric"><div><span>${e(metric.label)}</span><strong>${e(metric.text)}</strong></div>${metric.percent === null ? '' : `<meter min="0" max="100" value="${metric.percent}" aria-label="${e(metric.label)}">${e(metric.text)}</meter>`}</div>`).join('')}<dl class="dc-facts">${model.fields.map(field => `<div><dt>${e(field.label)}</dt><dd${field.label === 'Version' ? ' class="dc-version"' : ''}>${field.label === 'Health' ? stateHTML(model.healthState,field.value) : e(field.value)}</dd></div>`).join('')}</dl></div>`;
}

export function computePanel(options, record, phone = false) {
  const summary = options.failure ? '' : inspectorHTML(inspectorModel({ record, detail: options.detail, node: options.node }));
  return decorateComputePanel(panelHTML(options), summary, phone);
}

export function decorateComputePanel(html, summary, phone = false) {
  const header = '</header>', foot = '<p class="td-foot">', readOnly = 'Read-only. Changes go through agent proposals.';
  const headEnd = html.indexOf(header), footStart = html.indexOf(foot), copyStart = html.indexOf(readOnly, footStart);
  // Preserve the original panel if its upstream structure or copy has changed.
  if (headEnd < 0 || footStart < headEnd + header.length || copyStart < footStart) return html;
  const start = html.slice(0, headEnd + header.length);
  const body = html.slice(headEnd + header.length, footStart);
  const footer = html.slice(footStart).replace(readOnly, 'Bots propose changes here. You approve them in Today.');
  let result = `${start}${summary}<details class="dc-more"><summary>Device details</summary>${body}</details>${footer}`;
  if (phone) result = result.replace(/<button[^>]*data-td="close"[^>]*>[\s\S]*?<\/button>/, PHONE_CLOSE);
  return result;
}

export function addMachineHTML() {
  return `<section class="dc-enroll"><h2>${COMPUTE_COPY.add}</h2><p>${COMPUTE_COPY.setup}</p><a class="dc-action" href="${ENROLL_ROUTE}">Add a machine</a><p class="dc-observed">Then choose which bots may use it.</p></section>`;
}

/** The media query is observable without a browser. CSS is a second, immediate fallback. */
export function watchComputeMotion(root, media = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)')) {
  const sync = () => { root.dataset.computeMotion = media?.matches ? 'reduced' : 'full'; };
  sync(); media?.addEventListener?.('change', sync);
  return () => { media?.removeEventListener?.('change', sync); delete root.dataset.computeMotion; };
}


/** Follow the recorded directed links on the phone. Unconnected nodes and cycles stay visible. */
export function phoneTree(nodes, links) {
  const keys = new Set(nodes.map(node => node.key));
  const edges = new Map(nodes.map(node => [node.key, []])), incoming = new Set();
  for (const link of links) if (keys.has(link.from) && keys.has(link.to) && link.from !== link.to) {
    edges.get(link.from).push(link.to); incoming.add(link.to);
  }
  const depths = new Map();
  const visit = root => {
    const queue = [[root, 0]];
    for (let i = 0; i < queue.length; i++) {
      const [key, depth] = queue[i];
      if (depths.has(key)) continue;
      depths.set(key, depth);
      for (const next of edges.get(key).sort()) if (!depths.has(next)) queue.push([next, depth + 1]);
    }
  };
  const sorted = [...keys].sort();
  for (const key of sorted) if (!incoming.has(key)) visit(key);
  for (const key of sorted) if (!depths.has(key)) visit(key);
  const positions = {}; let row = 0;
  for (const depth of [...new Set(depths.values())].sort((a,b) => a-b)) {
    const members = sorted.filter(key => depths.get(key) === depth);
    for (const key of members) { positions[key] = { x: 0, y: row * 112 }; row++; }
  }
  return positions;
}

export function computeStyles(doc) {
  if (!doc?.head || doc.querySelector('link[data-compute-capy]')) return;
  const link = doc.createElement('link');
  link.rel = 'stylesheet'; link.href = new URL('./deck-compute-capy.css', import.meta.url).href;
  link.setAttribute('data-compute-capy', ''); doc.head.append(link);
}


export function summaryStatesHTML(nodes) {
  const rank = { down:0, degraded:1, unknown:2 };
  return ' '+nodes.filter(n => n.status !== 'up' || n.stale || n.missing).filter(n=>n.status !== 'none').sort((a,b)=>(rank[effectiveState(a)] ?? 3)-(rank[effectiveState(b)] ?? 3)).map(n => `<span class="dc-summary-state">${stateHTML(effectiveState(n),`${n.name} ${effectiveState(n)}`)}</span>`).join(' ');
}

export function computeLinkPanel({ link, names, compact = false, phone = false }) {
  const states = [link.status.from,link.status.to];
  const state = link.stale || link.missing ? 'unknown' : states.includes('down') ? 'down' : states.includes('degraded') ? 'degraded' : states.every(s=>s === 'up') ? 'up' : 'unknown';
  const close = phone ? PHONE_CLOSE : panelCloseHTML(compact);
  const observed = link.observedAt;
  const seen = observed && Number.isFinite(Date.parse(observed)) ? `<time datetime="${e(observed)}">${e(new Date(observed).toLocaleString('en',{dateStyle:'medium',timeStyle:'short',timeZone:'UTC'}))} UTC</time>` : 'Unknown';
  return `<header class="td-head"><h2 class="td-title">${e(names.get(link.from) || link.from)} to ${e(names.get(link.to) || link.to)}</h2>${close}</header><dl class="dc-facts dc-link-facts"><div><dt>Route</dt><dd>${e(linkName(link.method) || 'Not reported')}</dd></div><div><dt>State</dt><dd>${stateHTML(state)}</dd></div><div><dt>Last seen</dt><dd>${seen}</dd></div></dl>`;
}


export function mountComputeTopAction(shell, go) {
  const bar = shell?.querySelector('#phone-bar');
  if (!bar || !globalThis.MutationObserver) return { stop() {} };
  const button = bar.ownerDocument.createElement('button');
  button.type = 'button'; button.className = 'iconbtn dc-top-add';
  button.setAttribute('aria-label','Add a machine'); button.title = 'Add a machine';
  button.innerHTML = '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M9 3v12M3 9h12"/></svg>';
  button.addEventListener('click',()=>go(ENROLL_ROUTE));
  const place = () => { const top = bar.querySelector('.m-top'); if (top && button.parentNode !== top) top.append(button); };
  const observer = new MutationObserver(place); observer.observe(bar,{childList:true,subtree:true}); place();
  return { stop() { observer.disconnect(); button.remove(); } };
}
