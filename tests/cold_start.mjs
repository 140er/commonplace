import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { createNeedLedger } from '../commonplace/web/static/deck-home-today.js';

// Run the shipped request and refresh code. The clock models a 30 s dependency,
// including a server/body that ignores cancellation, without a 30 s test sleep.
const read = name => readFileSync(`commonplace/web/static/${name}`, 'utf8');
const app = read('app.js');
const slice = (start, end) => app.slice(app.indexOf(start), app.indexOf(end, app.indexOf(start)));
const [slow, route = '#/boards'] = process.argv.slice(2);
let now = 0, serial = 0, painted = null;
const timers = new Map(), requests = [], aborted = [];
const tick = () => new Promise(resolve => setImmediate(resolve));
const content = { children: [], innerHTML: '', querySelector: () => null };
const values = {
  '/api/boards': { boards: [{ slug: 'project', name: 'Project' }] },
  '/api/boards?view=shell': { boards: [{ slug: 'project', name: 'Project' }] },
  '/api/needs-me': { count: 1 }, '/api/rules/count': { waiting: 2 },
  '/api/bots': { bots: [] }, '/api/boards/project/cards': { cards: [], linked: [] },
};
const paint = () => { painted ??= now; content.children = [{}]; content.innerHTML = 'Main data'; };
const context = {
  enc: encodeURIComponent, createNeedLedger, deckNeedsLedger: createNeedLedger(), Event, AbortController,
  document: { dispatchEvent() {} },
  setTimeout(fn, ms) { const id = ++serial; timers.set(id, { fn, at: now + ms }); return id; },
  clearTimeout(id) { timers.delete(id); },
  fetch(path, options) {
    requests.push({ path, at: now });
    const value = values[path];
    assert.ok(value, `Unexpected path ${path}`);
    const response = { status: 200, ok: true, json: async () => value };
    if (path !== slow) return Promise.resolve(response);
    aborted.push(options.signal);
    return new Promise(resolve => context.setTimeout(() => resolve(response), 30000));
  },
  state: { me: { actor: 'user:reader', workspace: 'workspace', can_admin: true }, base: route, route, screen: 0, refreshRequest: 0, boards: [], needs: {} },
  root: { querySelector: () => content },
  remember: () => ({}), restore() {}, capture: () => () => {}, navigation() {},
  boardsView: paint, boardView: paint, memoryView: paint, settingsView: paint,
  botsScreen: paint, mountWorkbenchChat() {}, rememberDeckBots() {},
  showError() {}, message: text => text, waitingTotal: () => 0,
};
runInNewContext(read('api.js').replace(/export /g, '') + '\n' + slice('async function refresh(', 'function decideStart('), context);
const load = context.refresh();
await tick();
for (const at of [1900, 3000, 30000]) {
  now = at;
  for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.fn(); }
  await tick();
}
await load;
console.log(JSON.stringify({ slow, route, main_ms: painted, deadline_ms: 3000 }));
assert.notEqual(painted, null, `${slow} held ${route} behind a 30 s dependency`);
assert.ok(painted < 2000, `${slow} delayed main data until ${painted} ms`);
assert.ok(requests.some(row => row.path === '/api/boards?view=shell'), 'Bootstrap uses the cheap board directory');
assert.ok(aborted.every(signal => signal?.aborted), 'Optional reads have a 3 s deadline');
assert.equal(content.innerHTML, 'Main data', 'Late metadata cannot replace the selected screen');
