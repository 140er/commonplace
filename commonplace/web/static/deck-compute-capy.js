// Capy presentation for the existing topology canvas. No enrollment or discovery writes live here.
import { escape as e } from './api.js';
import { panelHTML } from './topology-detail.js';

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
  return { name: words(node.name) || words(values.name), role: words(values.role), metrics, fields, stale: !!record.stale || !!detail.stale };
}

export function inspectorHTML(model) {
  return `<div class="dc-inspector">${model.stale ? '<p class="dc-observed">Some observations are out of date.</p>' : ''}${model.metrics.map(metric => `<div class="dc-metric"><div><span>${e(metric.label)}</span><strong>${e(metric.text)}</strong></div>${metric.percent === null ? '' : `<meter min="0" max="100" value="${metric.percent}" aria-label="${e(metric.label)}">${e(metric.text)}</meter>`}</div>`).join('')}<dl class="dc-facts">${model.fields.map(field => `<div><dt>${e(field.label)}</dt><dd${field.label === 'Version' ? ' class="dc-version"' : ''}>${e(field.value)}</dd></div>`).join('')}</dl></div>`;
}

export function computePanel(options, record, phone = false) {
  const summary = options.failure ? '' : inspectorHTML(inspectorModel({ record, detail: options.detail, node: options.node }));
  // Keep the existing physical/config/agents/sites controllers available behind one disclosure.
  let html = panelHTML(options);
  if (phone) html = html.replace(/<button[^>]*data-td="close"[^>]*>[\s\S]*?<\/button>/, '<button type="button" class="dc-back" data-td="close" aria-label="Back to machines">Back</button>');
  return html.replace('</header>', `</header>${summary}<details class="dc-more"><summary>Device details</summary>`).replace('<p class="td-foot">', '</details><p class="td-foot">');
}

export function addMachineHTML() {
  return `<section class="dc-enroll"><h2>${COMPUTE_COPY.add}</h2><p>${COMPUTE_COPY.setup}</p><a class="dc-action" href="${ENROLL_ROUTE}">Add runner</a><p class="dc-observed">Choose which bots may use it in workspace administration.</p></section>`;
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
    for (let start = 0; start < members.length; start += 2) {
      const group = members.slice(start, start + 2);
      group.forEach((key, i) => { positions[key] = { x: (i - (group.length - 1) / 2) * 165, y: row * 150 }; });
      row++;
    }
  }
  return positions;
}

export function computeStyles(doc) {
  if (!doc?.head || doc.querySelector('link[data-compute-capy]')) return;
  const link = doc.createElement('link');
  link.rel = 'stylesheet'; link.href = new URL('./deck-compute-capy.css', import.meta.url).href;
  link.setAttribute('data-compute-capy', ''); doc.head.append(link);
}
