import assert from 'node:assert/strict';
import { renderNeedDetail } from '../commonplace/web/static/needs.js';
const card = {number: 7, board: 'work', title: 'Review', need: 'merge', actions: [], pr_url: 'https://github.com/example/project/pull/1', pr_state: 'open'};
const absent = renderNeedDetail(card, {github: {state: 'absent'}});
assert.ok(absent.includes('Connect GitHub'), 'an unconnected person must connect their own account');
const connected = renderNeedDetail(card, {github: {state: 'connected', login: 'test-person'}});
assert.ok(connected.includes('Open PR') && connected.includes('Disconnect'));
assert.ok(connected.includes('noopener noreferrer'));
assert.ok(connected.includes('unverified'));
for (const pr_url of ['http://github.com/a/b/pull/1', 'https://evil.example/a/b/pull/1', 'https://github.com@evil.example/a/b/pull/1', 'https://github.com/a/b/pull/0', 'https://github.com:444/a/b/pull/1']) {
  assert.ok(!renderNeedDetail({...card, pr_url}, {github: {state: 'connected'}}).includes('Open PR'));
}
assert.ok(renderNeedDetail(card, {github: {state: 'loading'}}).includes('disabled'));
assert.ok(renderNeedDetail(card, {github: {state: 'unconfigured'}}).includes('setup'));

// Exercise the real Needs controller with synthetic DOM roots and deferred API reads.
const { createNeeds } = await import('../commonplace/web/static/needs.js');
function root() {
  return { innerHTML: '', contains: () => false, addEventListener() {}, removeEventListener() {}, querySelector: () => null, querySelectorAll: () => [] };
}
const mount = root(), detailMount = root();
const doc = { hidden: false, addEventListener() {}, removeEventListener() {} };
let release, reads = 0;
const ui = createNeeds({ mount, detailMount, document: doc, setInterval: () => 1, clearInterval() {}, api: async (path) => {
  if (path.startsWith('/api/cards/')) return {...card, number: Number(path.split('/').at(-1))};
  if (path === '/api/github') {
    reads++;
    if (reads === 1) return new Promise(resolve => { release = resolve; });
    return {state: 'absent', csrf: 'synthetic-csrf'};
  }
  return {holds: []};
}});
const first = ui.select(7);
await new Promise(resolve => setImmediate(resolve));
assert.ok(detailMount.innerHTML.includes('Loading GitHub'));
await ui.select(8);
assert.ok(detailMount.innerHTML.includes('Connect GitHub'));
release({state: 'connected', login: 'stale-person'});
await first;
assert.ok(!detailMount.innerHTML.includes('Open PR'), 'an old workspace/card response must not restore a connection');
ui.stop();
const before = detailMount.innerHTML;
await ui.select(7);
assert.equal(detailMount.innerHTML, before, 'stopped controllers must ignore late session work');
