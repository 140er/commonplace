import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { createNeedLedger } from '../commonplace/web/static/deck-home-today.js';

const read = name => readFileSync(new URL(`../commonplace/web/static/${name}`, import.meta.url), 'utf8');
const app = read('app.js');
const slice = (start, end) => app.slice(app.indexOf(start), app.indexOf(end, app.indexOf(start)));
const [role, failure = 'success', look = 'deck'] = process.argv.slice(2);
let error = '', rendered = 0, expired = 0, blocked = failure !== 'success';
const requests = [];
const queue = { count: 1, asks: [{ number: 8, title: 'Review request' }] };
const content = { children: [], innerHTML: '', querySelector: () => null };
const values = { '/api/boards': { boards: [{ slug: 'project' }] }, '/api/needs-me': queue,
  '/api/rules/count': { waiting: 0 }, '/api/bots': { bots: [] } };
const context = {
  deckNeedsLedger: createNeedLedger(),
  state: { me: { actor: 'user:reader', can_admin: role === 'owner' }, base: '#/boards', route: '#/boards', screen: 0, refreshRequest: 0, needs: { count: 99 } },
  root: { querySelector: selector => selector === '#content' ? content : null },
  document: { dispatchEvent: () => expired++ }, Event, AbortController, setTimeout, clearTimeout,
  fetch: async (path, options) => {
    requests.push(path);
    assert.equal(options.optional, undefined, 'Internal options must not be sent to fetch');
    let status = 200;
    if (path === '/api/needs-me' && role === 'member') status = 403;
    if (blocked && path === '/api/needs-me' && ['401', '403', '503'].includes(failure)) status = Number(failure);
    if (blocked && path === '/api/boards' && failure === 'boards') status = 503;
    return { status, ok: status === 200, json: async () => status === 200 ? values[path] : { error: 'Read refused or unavailable' } };
  },
  remember: () => ({}), restore() {}, capture: () => () => {}, navigation() {},
  boardsView() { rendered++; content.children = [{}]; content.innerHTML = 'Projects'; },
  showError(text) { error = text; }, message: text => `${text} Retry`, waitingTotal: () => 0,
};
if (look === 'deck') context.state.deck = { owns: () => false };
runInNewContext(read('api.js').replace(/export /g, '') + '\n' + slice('function rememberDeckBots(', 'function setBotsWaiting(') + slice('async function refresh(', 'function decideStart('), context);
const retry = app.match(/else if \(action === "retry"\) ([^\n]+)/);
assert.ok(retry, 'Production Retry handler exists');
runInNewContext('async function clickRetry() { ' + retry[1] + ' }', context);
const ok = await context.refresh();
if (['503', 'boards'].includes(failure)) {
  assert.equal(ok, false, 'A transient read error offers Retry');
  assert.match(content.innerHTML, /Retry/);
  blocked = false;
  await context.clickRetry();
} else {
  assert.equal(ok, true, 'An owner-only queue refusal must not fail workspace bootstrap');
}
assert.equal(error, '', 'Successful bootstrap or Retry clears the error');
assert.equal(expired, 0, 'An optional queue refusal must not expire the session');
assert.equal(rendered, 1, 'Workspace content renders');
assert.equal(context.state.boards[0].slug, 'project');
if (role === 'member') {
  assert.equal(requests.includes('/api/needs-me'), false, 'Members never request the owner queue');
  assert.equal(context.state.needs.count, 0, 'No stale owner queue survives');
} else {
  assert.ok(requests.includes('/api/needs-me'), 'Owners retain the decision queue');
  assert.equal(context.state.needs.count, ['401', '403'].includes(failure) ? 0 : 1);
}
// Required authentication errors still expire the session, including bounded reads.
context.fetch = async () => ({ status: 401, ok: false, json: async () => ({ error: 'Sign in' }) });
await assert.rejects(() => context.api('/api/me', { timeoutMs: 100 }), { status: 401 });
assert.equal(expired, 1);
