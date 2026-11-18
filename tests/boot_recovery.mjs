import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const mode = process.argv[2];
const app = readFileSync('commonplace/web/static/app.js', 'utf8');
const admin = readFileSync('commonplace/web/static/admin.js', 'utf8');
const api = readFileSync('commonplace/web/static/api.js', 'utf8');
let now = 100000, sequence = 0, signedIn = 0, shells = 0, routes = 0;
const timers = new Map(), requests = [], buttons = {};
const tick = () => new Promise(resolve => setImmediate(resolve));
const root = { innerHTML: 'Loading Commonplace…', querySelector: selector => {
  if (selector === '[data-boot-retry]') return buttons.retry ||= {};
  return { innerHTML: '' };
} };
const response = status => ({ status, ok: status === 200, json: async () => status === 200 ? { actor: 'user:reader', workspace: 'workspace', people: {} } : {} });
const context = {
  state: { me: null }, root, AbortController, Event, URLSearchParams,
  signinCode: null, setPeople() {}, showError() {}, schedule() {},
  location: { search: '', hash: '#/bots/project' }, history: {},
  document: { dispatchEvent() {} }, voice: { load() {} },
  signin() { signedIn++; root.innerHTML = 'Sign in | Email a sign-in link'; },
  shell() { shells++; root.innerHTML = 'App'; }, route() { routes++; },
  setTimeout(fn, ms) { const id = ++sequence; timers.set(id, { fn, at: now + ms }); return id; },
  clearTimeout(id) { timers.delete(id); },
  fetch(path, options) {
    assert.equal(path, '/api/me');
    requests.push({ at: now, signal: options.signal });
    if (mode === 'slow-success') return new Promise(resolve => context.setTimeout(() => resolve(response(200)), 5000));
    if (requests.length > 1 && !['exhaustion', 'manual'].includes(mode)) return Promise.resolve(response(200));
    if (mode === 'timeout') return new Promise(() => {});
    if (mode === 'abort') return Promise.reject(Object.assign(new Error('request aborted'), { name: 'AbortError' }));
    if (mode === 'network') return Promise.reject(new TypeError('Failed to fetch'));
    if (mode === 'null') return Promise.resolve({ status: 200, ok: true, json: async () => null });
    return Promise.resolve(response(/^\d+$/.test(mode) ? Number(mode) : 500));
  },
};
runInNewContext(api.replace(/export /g, '') + '\n' +
  admin.slice(admin.indexOf('export async function boundedRequest'), admin.indexOf('export function createAdmin')).replace('export ', '') + '\n' +
  app.slice(app.indexOf('async function boot()')).replace(/boot\(\);\s*$/, 'globalThis.ready = boot();'), context);
async function advance(ms) {
  now += ms;
  for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.fn(); }
  await tick();
}
await tick();
if (mode === '401') {
  assert.equal(signedIn, 1);
  assert.match(root.innerHTML, /Sign in/);
  assert.equal(requests.length, 1);
  assert.equal(timers.size, 0);
} else if (mode === 'slow-success') {
  await advance(3000);
  assert.equal(signedIn, 0, 'The first request allows ten seconds after module evaluation');
  await advance(2000);
  await context.ready;
  assert.equal(root.innerHTML, 'App');
  assert.equal(routes, 1);
} else {
  if (mode === 'timeout') await advance(10000);
  console.log(JSON.stringify({ mode, first_failure: root.innerHTML }));
  assert.equal(signedIn, 0, 'A non-401 identity failure must never show sign-in');
  assert.match(root.innerHTML, /Could not reach Commonplace, retrying/);
  assert.match(root.innerHTML, /Retry/);
  assert.doesNotMatch(root.innerHTML, /Sign in|Email a sign-in link|No bots yet/);
  assert.equal(shells, 0, 'Unknown identity must not render an empty shell');
  if (mode === 'manual') {
    await buttons.retry.onclick();
    await tick();
    assert.equal(requests.length, 2, 'Retry advances the scheduled attempt without a duplicate read');
    await advance(2000); await advance(4000);
    await context.ready;
    context.fetch = () => Promise.resolve(response(200));
    await buttons.retry.onclick();
    await tick();
    assert.equal(root.innerHTML, 'App', 'Retry also recovers after the automatic attempts are exhausted');
  } else if (mode === 'exhaustion') {
    for (const delay of [1000, 2000, 4000]) await advance(delay);
    await context.ready;
    assert.deepEqual(requests.map(row => row.at - 100000), [0, 1000, 3000, 7000]);
    assert.equal(shells, 0);
    assert.equal(signedIn, 0);
    assert.match(root.innerHTML, /Could not reach Commonplace/);
    assert.match(root.innerHTML, /Retry/);
    assert.equal(timers.size, 0);
  } else {
    await advance(1000);
    await context.ready;
    assert.equal(root.innerHTML, 'App');
    assert.equal(shells, 1);
    assert.equal(routes, 1);
    assert.equal(requests.length, 2);
    assert.equal(signedIn, 0);
  }
}
