// Compact Compute cards and direct links, hosted by the shared topology canvas.
import { escape as e } from './api.js';
import { KINDS } from './topology-icons.js';
import { textWidth, wrapLines } from './topology-text.js';
import { fitLabelled } from './topology-layout.js';

export const linkName = method => ({ tailscale: 'Tailscale', direct: 'Direct', lan: 'LAN' })[method] || String(method || '').replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
export function cardSize(node, phone = false) {
  const padding = phone ? 42 : 86;
  return { width: Math.min(phone ? 262 : 280, Math.max(phone ? 160 : 186, Math.ceil(Math.max(textWidth(node.name, 13), textWidth(node.role || '', 12)) + padding))), height: 48 };
}
const pointAt = (a, b, control, t) => control ? { x: (1-t)**2*a.x + 2*(1-t)*t*control.x + t*t*b.x, y: (1-t)**2*a.y + 2*(1-t)*t*control.y + t*t*b.y } : { x: a.x+(b.x-a.x)*t, y: a.y+(b.y-a.y)*t };
const inside = (p, box) => p.x > box.x && p.x < box.x+box.width && p.y > box.y && p.y < box.y+box.height;

/** Straight centre-to-centre by default. Only an obstructed pair gets a local quadratic curve. */
export function directLinks(model, positions, phone = false, k = 1) {
  const boxes = model.nodes.filter(n => positions[n.key]).map(node => {
    const { width, height } = cardSize(node, phone), p = positions[node.key];
    return { key: node.key, x: p.x-width/(2*k)-6, y: p.y-height/(2*k)-6, width: width/k+12, height: height/k+12 };
  });
  return new Map((model.links || []).filter(link => positions[link.from] && positions[link.to]).map(link => {
    const a = positions[link.from], b = positions[link.to], obstacles = boxes.filter(box => box.key !== link.from && box.key !== link.to);
    const hits = control => {
      let count = 0;
      for (let i = 1; i < 40; i++) if (obstacles.some(box => inside(pointAt(a,b,control,i/40),box))) count++;
      return count;
    };
    let control = null, best = hits(null);
    const distance = Math.hypot(b.x-a.x,b.y-a.y) || 1;
    if (best) for (let offset = 32; offset <= 320 && best; offset += 24) for (const sign of [1,-1]) {
      const trial = { x: (a.x+b.x)/2 - (b.y-a.y)/distance*offset*sign, y: (a.y+b.y)/2 + (b.x-a.x)/distance*offset*sign };
      const score = hits(trial);
      if (score < best) { control = trial; best = score; }
      if (!best) break;
    }
    const midpoint = pointAt(a,b,control,.5);
    const half = (textWidth(linkName(link.method),11)+10)/(2*k);
    const candidates = [[0,-12],[0,-28],[0,28],[-36,-12],[36,-12],[-72,0],[72,0]];
    const label = candidates.map(([x,y]) => ({ x: midpoint.x+x/k, y: midpoint.y+y/k })).find(p => !boxes.some(box => p.x+half > box.x && p.x-half < box.x+box.width && p.y+9/k > box.y && p.y-9/k < box.y+box.height)) || { x: midpoint.x, y: midpoint.y-12/k };
    return [link.id, { d: `M${a.x} ${a.y}${control ? `Q${control.x} ${control.y} ` : 'L'}${b.x} ${b.y}`, control, label, points: control ? [a,control,b] : [a,b] }];
  }));
}

function statusMark(node, x) {
  if (node.status === 'none') return '';
  const shape = node.stale || node.missing ? 'ring' : ({ up: 'disc', degraded: 'triangle', down: 'diamond' })[node.status] || 'ring';
  const geometry = {
    disc: '<circle r="3"/>',
    ring: '<circle r="3.5" fill="none" stroke="currentColor" stroke-width="1.5"/>',
    triangle: '<path d="M0 -4L4 3H-4Z"/>',
    diamond: '<path d="M0 -4L4 0L0 4L-4 0Z"/>',
  }[shape];
  return `<g class="dc-node-status" data-shape="${shape}" transform="translate(${x} 0)">${geometry}</g>`;
}

function nodeHTML(node, at, { sel, on, dim, words }, phone) {
  const { width, height } = cardSize(node, phone), left = -width/2, textX = left+(phone ? 12 : 50), room = width-(phone ? 40 : 78);
  const name = wrapLines(node.name,room,13,1)[0], role = wrapLines(node.role || '',room,12,1)[0];
  const kind = KINDS[node.kind] || KINDS.unknown;
  const label = `${node.name}${node.role ? `, ${node.role}` : ''}${node.status === 'none' ? '' : `. ${words[node.status] || 'Not reported'}`}${node.stale ? '. Stale' : ''}${node.missing ? '. Not recorded' : ''}`;
  return `<g class="topo-node dc-node s-${node.status}${sel ? ' sel' : ''}${on ? ' on' : ''}${dim ? ' dim' : ''}${node.stale || node.missing ? ' stale' : ''}" data-key="${e(node.key)}" transform="translate(${at.x} ${at.y})" tabindex="0" role="button" aria-pressed="${sel}" aria-label="${e(label)}"><title>${e(label)}</title><g class="dc-node-card"><rect class="dc-node-surface" x="${left}" y="-24" width="${width}" height="${height}" rx="10"/>${phone ? '' : `<g class="dc-node-tile" transform="translate(${left+24} 0)"><rect x="-14" y="-14" width="28" height="28" rx="7"/><g class="dc-node-icon" transform="scale(.38)">${kind.icon}</g></g>`}<text class="dc-node-name" x="${textX}" y="${role ? -4 : 4}">${e(name)}</text>${role ? `<text class="dc-node-role" x="${textX}" y="14">${e(role)}</text>` : ''}${statusMark(node, width/2-12)}</g></g>`;
}
function linkHTML(link, at, names, { on, dim, sel, words }) {
  const states = [...new Set([words[link.status.from],words[link.status.to]].filter(Boolean))].join('; ');
  const label = `${names.get(link.from)} to ${names.get(link.to)}, ${linkName(link.method)}${states ? `. ${states}` : ''}`;
  const live = !link.stale && !link.missing && link.status.from === 'up' && link.status.to === 'up';
  const caption = linkName(link.method), width = textWidth(caption,11)+10;
  return `<g class="topo-link dc-link${on ? ' on' : ''}${dim ? ' dim' : ''}${link.stale || link.missing ? ' stale' : ''}" data-link="${e(link.id)}" data-method="${e(link.method)}" data-active="${live}" tabindex="0" role="button" aria-pressed="${sel}" aria-label="${e(label)}"><title>${e(label)}</title><path class="dc-link-hit" d="${at.d}"/><path class="topo-route" d="${at.d}"/><g class="dc-link-label" transform="translate(${at.label.x} ${at.label.y})"><g class="dc-link-caption"><rect x="${-width/2}" y="-9" width="${width}" height="18" rx="3"/><text class="topo-method" x="0" y="4">${e(caption)}</text></g></g></g>`;
}

export function computePresentation(phone = false) {
  return {
    node: (node, at, options) => nodeHTML(node, at, options, phone),
    link: linkHTML,
    links: (model, positions, k) => directLinks(model,positions,phone,k),
    fit(model, positions, width, height) {
      const points = model.nodes.filter(n => positions[n.key]).map(node => ({ ...positions[node.key], rx: cardSize(node,phone).width/2, top: 24, bottom: 24 }));
      const curves = [...directLinks(model,positions,phone).values()].filter(link => link.control).map(link => ({ ...pointAt(link.points[0],link.points[2],link.control,.5), rx: 4, top: 20, bottom: 4 }));
      return fitLabelled([...points,...curves],width,height,{ pad: 48, most: 1, minHeight: phone ? 240 : 360 });
    },
  };
}
const fitIcon = '<path d="M6 2H2v4m8-4h4v4M2 10v4h4m8-4v4h-4"/>';
const resetIcon = '<path d="M3 6a5.5 5.5 0 1 1 0 4M3 2v4h4"/>';
export function computeFrame() {
  const control = (action,label,icon) => `<button type="button" class="iconbtn dc-map-control" data-topo="${action}" aria-label="${label}" title="${label}"><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4">${icon}</svg></button>`;
  return `<section class="topo" data-topology aria-label="Compute map"><div class="topo-stage" data-topo="stage" tabindex="0" role="group" aria-label="Map canvas. Drag nodes to arrange. Arrow keys pan; plus and minus zoom; 0 fits everything."><svg class="topo-svg" aria-label="Topology"><g data-topo="world"></g></svg><div class="topo-note" data-topo="note"></div><div class="dc-map-controls" role="group" aria-label="Map controls">${control('fit','Fit',fitIcon)}${control('reset','Reset layout',resetIcon)}</div></div><select data-topo="route" hidden aria-label="Highlighted route"></select><p data-topo="path" class="topo-path" role="status" hidden></p><span data-topo="live" class="topo-live" role="status"></span></section>`;
}

/** The route picker lives in Management; the map reads only its remembered site name. */
export const routePreference = value => {
  try { if (value !== undefined) sessionStorage.setItem('cmmn.compute.route',value); return sessionStorage.getItem('cmmn.compute.route') || ''; } catch { return ''; }
};
export function mountComputeRoutes({ root, api, go }) {
  if (!root) return { stop() {} };
  const controller = new AbortController();
  root.innerHTML = '<p role="status">Loading routes…</p>';
  api('/api/topology',{ signal: controller.signal }).then(payload => {
    if (controller.signal.aborted || !root.isConnected) return;
    const routes = Array.isArray(payload?.routes) ? payload.routes : [];
    root.innerHTML = `<section class="dc-route-management"><h2>Route</h2><label>Highlight a site route<select data-compute-route><option value="">None</option>${routes.map(route => `<option value="${e(route.site)}">${e(route.site)}</option>`).join('')}</select></label><button type="button" class="btn" data-compute-show-route>Show on map</button></section>`;
    root.querySelector('[data-compute-route]').value = routePreference();
  }).catch(() => { if (!controller.signal.aborted) root.textContent = 'Routes could not be loaded.'; });
  root.addEventListener('click',event => { if (event.target.closest?.('[data-compute-show-route]')) { routePreference(root.querySelector('[data-compute-route]').value); go('#/compute'); } },{ signal: controller.signal });
  return { stop() { controller.abort(); } };
}

export function placeManagementTabs(content, tabs) {
  if (!content || !tabs || !globalThis.MutationObserver) return { stop() {} };
  const place = () => {
    const intro = content.querySelector('.compute-intro');
    if (intro && intro.nextElementSibling !== tabs) intro.after(tabs);
  };
  const observer = new MutationObserver(place);
  observer.observe(content,{ childList:true,subtree:true }); place();
  return { stop() { observer.disconnect(); } };
}
