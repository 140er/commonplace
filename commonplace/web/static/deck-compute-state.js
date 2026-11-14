// One status vocabulary for node marks, summaries and the inspector.
import { escape as e } from './api.js';

export const STATE_WORDS = { up: 'Up', degraded: 'Degraded', down: 'Down', unknown: 'Unknown' };
const shapes = { up: 'disc', degraded: 'triangle', down: 'diamond', unknown: 'ring' };
export const effectiveState = node => node.stale || node.missing ? 'unknown' : Object.hasOwn(shapes,node.status) ? node.status : 'unknown';

// A one-pixel tangent at each corner rounds triangles and diamonds without a stroke enlarging them.
function rounded(points) {
  const corners = points.map((p,i) => {
    const toward = q => { const distance = Math.hypot(q[0]-p[0],q[1]-p[1]); return [p[0]+(q[0]-p[0])/distance,p[1]+(q[1]-p[1])/distance]; };
    return { p, before: toward(points[(i+points.length-1)%points.length]), after: toward(points[(i+1)%points.length]) };
  });
  return corners.map((c,i) => `${i ? 'L' : 'M'}${c.before.join(' ')}Q${c.p.join(' ')} ${c.after.join(' ')}`).join('')+'Z';
}
export function stateGeometry(state, phone = false) {
  if (state === 'up') return `<circle r="${phone ? 5 : 4}"/>`;
  if (state === 'unknown') return `<circle r="${phone ? 4.25 : 3.25}" fill="none" stroke="currentColor" stroke-width="1.5"/>`;
  const w = state === 'degraded' ? (phone ? 12 : 10) : (phone ? 11 : 9), h = state === 'degraded' ? (phone ? 11 : 9) : w;
  const points = state === 'degraded' ? [[0,-h/2],[w/2,h/2],[-w/2,h/2]] : [[0,-h/2],[w/2,0],[0,h/2],[-w/2,0]];
  return `<path d="${rounded(points)}"/>`;
}
export function nodeStatusMark(node, x = 0, phone = false) {
  if (node.status === 'none') return '';
  const state = effectiveState(node);
  return `<g class="dc-node-status" data-state="${state}" data-shape="${shapes[state]}" transform="translate(${x} 0)" aria-hidden="true">${stateGeometry(state,phone)}</g>`;
}
export function stateHTML(state, word = STATE_WORDS[state] || STATE_WORDS.unknown) {
  return `<span class="dc-state-word"><svg class="dc-state" width="12" height="12" viewBox="-6 -6 12 12" aria-hidden="true">${nodeStatusMark({status:state})}</svg>${e(word)}</span>`;
}
