// Config proposals in Waiting on you: the count, the item, and Accept or Reject through the real mount.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { escape as e } from '../commonplace/web/static/api.js';
import { needsMeCount, configProposals } from '../commonplace/web/static/needs-me.js';
import { removeConfig, createNeedLedger } from '../commonplace/web/static/deck-home-today.js';
import { configHTML, mount, summaryLine, chipsHTML } from '../commonplace/web/static/deck-today-capy.js';

const SHA = 'a'.repeat(64);
const proposal = (extra = {}) => ({
  board: 'acme', board_name: 'Acme', version: 2, ref: 'v2', channel: 'live', sha256: SHA,
  proposed_by: 'conn:Coworker:acme', proposer_name: 'Acme bot', proposed_at: '2030-01-01T00:00:00Z',
  summary: 'Agent limit: 3 to 5', diff: '~ limits.max_agents: 3 -> 5\n', ...extra,
});
const card = { number: 7, version: 4, title: 'Review the release', actions: ['approve', 'reject'], board: 'acme' };
const queue = (extra = {}) => ({ count: 2, approvals: [card], config_proposals: [proposal()], ...extra });
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

test('a proposal is counted with the cards, by board and version, and invalid ones are not', () => {
  assert.equal(needsMeCount(queue()), 2);
  assert.equal(needsMeCount({ approvals: [], config_proposals: [proposal(), proposal()] }), 1);
  assert.equal(needsMeCount({ approvals: [], config_proposals: [proposal({ version: 0 }), proposal({ board: '' }), null, proposal({ sha256: undefined })] }), 0);
  assert.equal(needsMeCount({ count: 3 }), 3, 'a member with no lists keeps the plain number');
  assert.equal(summaryLine(queue()), '2 items waiting');
  assert.deepEqual(configProposals({ approvals: [] }), []);
});

test('deciding a proposal removes it and the count follows; the ledger keeps a stale read from restoring it', () => {
  const needs = queue();
  const next = removeConfig(needs, proposal());
  assert.equal(next.count, 1); assert.deepEqual(next.config_proposals, []);
  assert.equal(needs.config_proposals.length, 1, 'never mutates the shell snapshot');
  assert.equal(removeConfig(needs, proposal({ version: 3 })).count, 2, 'another version stays');
  const ledger = createNeedLedger(), me = { actor: 'user:one', workspace: 'one' };
  ledger.settle(proposal(), me);
  assert.equal(ledger.apply(needs, me).count, 1);
  assert.equal(ledger.apply(queue({ config_proposals: [proposal({ version: 3 })] }), me).count, 2);
  assert.equal(ledger.apply(needs, { ...me, workspace: 'two' }).count, 2);
});

test('the item names the proposer, says the change in one line and expands to the diff', () => {
  const html = configHTML(proposal(), {}, e);
  assert.match(html, /<b>Acme bot<\/b>/);
  assert.doesNotMatch(html, /conn:|Coworker:/);
  assert.match(html, /<h2 id="bt-title-config:acme:2">Agent limit: 3 to 5<\/h2>/);
  assert.match(html, /<summary>Changes<\/summary><p class="bt-full-text" data-config-diff>~ limits.max_agents: 3 -&gt; 5\n<\/p>/);
  assert.match(html, /class="bt-approve bt-primary" data-bt-config-action="accept">Accept</);
  assert.match(html, /class="bt-secondary" data-bt-config-action="reject">Reject</);
  assert.match(html, /Accept means yes: Acme uses v2 from now on\./);
  assert.match(configHTML(proposal(), { open: new Set(['config:acme:2']) }, e), /data-bt-config-changes="config:acme:2" open>/);
  assert.match(configHTML(proposal({ diff: null }), {}, e), /Could not load the changes/);
  assert.match(configHTML(proposal({ channel: 'exp:trial' }), {}, e), /experiment trial/);
});

test('names and diffs are escaped, never markup', () => {
  const html = configHTML(proposal({ proposer_name: '<img src=x onerror=bad()>', summary: '<b>x</b>', diff: '+ <script>bad()</script>' }), {}, e);
  assert.doesNotMatch(html, /<img|<script|<b>x/);
  assert.match(html, /&lt;img/);
});

test('Reject asks once with the project words, Cancel goes back, and busy disables the buttons', () => {
  const html = configHTML(proposal(), { confirming: new Set(['config:acme:2']) }, e);
  assert.match(html, /Reject config v2\? The current config stays in use\. This version cannot be accepted after rejection\./);
  assert.match(html, /data-bt-config-action="reject-confirm">Reject version</);
  assert.match(html, /data-bt-config-action="cancel">Cancel</);
  assert.doesNotMatch(html, /data-bt-config-action="accept"/);
  assert.equal((configHTML(proposal(), { busy: new Set(['config:acme:2']) }, e).match(/ disabled/g) || []).length, 2);
  assert.match(configHTML(proposal(), { errors: new Map([['config:acme:2', 'Nope']]) }, e), /<p class="bt-error" role="alert">Nope<\/p>/);
});

test('a proposal counts as a review in the filter chips', () => {
  const chips = chipsHTML([card, { ...proposal(), actions: ['approve', 'reject'] }]);
  assert.match(chips, /data-bt-filter="review" aria-pressed="false">Reviews <b>2<\/b>/);
  assert.match(chips, /data-bt-filter="all" aria-pressed="true">All <b>2<\/b>/);
});

class Node {
  hidden = false; innerHTML = ''; textContent = ''; disabled = false; value = ''; dataset = {};
  querySelector() { return null; } querySelectorAll() { return []; } focus() {} close() {} showModal() {} addEventListener() {} removeEventListener() {}
}
function harness(initial = queue(), { fail } = {}) {
  const regions = new Map(), listeners = new Map(), calls = [];
  const region = name => { if (!regions.has(name)) regions.set(name, new Node()); return regions.get(name); };
  const dialog = Object.assign(new Node(), { open: false, querySelector: () => new Node() });
  const form = Object.assign(new Node(), { elements: { choice: new Node(), reply: new Node() }, querySelector: () => new Node() });
  dialog.querySelector = selector => selector === 'form' ? form : new Node();
  const doc = { addEventListener() {}, removeEventListener() {}, activeElement: null, querySelector: () => null };
  const root = Object.assign(new Node(), {
    ownerDocument: doc, classList: { add() {}, remove() {} }, closest: () => null,
    addEventListener: (type, fn) => listeners.set(type, fn), removeEventListener() {},
    querySelector: selector => selector === 'dialog' ? dialog : /data-bt="([^"]+)"/.test(selector) ? region(/data-bt="([^"]+)"/.exec(selector)[1]) : null,
    querySelectorAll: () => [],
  });
  let feed = initial;
  const api = async (path, options = {}) => {
    calls.push({ path, ...options });
    if (path === '/api/needs-me') return feed;
    if (path === '/api/bots') return { bots: [] };
    if (path.startsWith('/api/events')) return { events: [], seq: 0, epoch: '' };
    if (options.method === 'POST') {
      if (fail) throw Object.assign(new Error(fail.message || 'no'), { status: fail.status });
      feed = { ...feed, count: 1, config_proposals: [] };
      return { ok: true };
    }
    throw new Error(`unexpected ${path}`);
  };
  const changed = [];
  const ui = mount({ root, api, me: { can_admin: true }, needs: initial, go() {}, onNeedsChanged: item => changed.push(item), singleKeys: () => false });
  const click = key => {
    const [, button] = /data-bt-config-action="([^"]+)"/.exec(key) || [];
    const target = {
      closest: selector => selector === '[data-bt-config-action]'
        ? { dataset: { btConfigAction: key }, disabled: false, closest: q => q === '[data-bt-config]' ? { dataset: { btConfig: 'config:acme:2' } } : null }
        : null,
    };
    listeners.get('click')({ preventDefault() {}, target });
    return button;
  };
  return { region, calls, ui, click, changed, listeners };
}

test('Accept is one click: it posts the version digest to the existing route and the item leaves', async () => {
  const h = harness();
  await flush();
  const list = h.region('cards').innerHTML;
  assert.match(list, /data-bt-config="config:acme:2"/);
  assert.ok(list.indexOf('data-bt-config=') < list.indexOf('data-bt-card='), 'proposals come first');
  assert.equal(h.region('summary').textContent, '2 items waiting');
  h.click('accept'); await flush();
  const post = h.calls.find(call => call.method === 'POST');
  assert.equal(post.path, '/api/orch/boards/acme/configs/2/accept');
  assert.deepEqual(post.body, { sha256: SHA });
  assert.doesNotMatch(h.region('cards').innerHTML, /data-bt-config=/);
  assert.equal(h.region('summary').textContent, '1 item waiting');
  assert.match(h.region('confirm').textContent, /Accepted\. v2 is now the config for Acme\./);
  assert.equal(h.changed.length, 1); assert.equal(h.changed[0].board, 'acme');
  h.ui.stop();
});

test('Reject is two clicks: Reject asks, Reject version posts an empty body to the reject route', async () => {
  const h = harness();
  await flush();
  h.click('reject'); await flush();
  assert.equal(h.calls.filter(call => call.method === 'POST').length, 0, 'the first click only asks');
  assert.match(h.region('cards').innerHTML, /Reject config v2\?/);
  h.click('cancel'); await flush();
  assert.match(h.region('cards').innerHTML, /data-bt-config-action="accept"/);
  h.click('reject'); await flush();
  h.click('reject-confirm'); await flush();
  const posts = h.calls.filter(call => call.method === 'POST');
  assert.equal(posts.length, 1); assert.equal(posts[0].path, '/api/orch/boards/acme/configs/2/reject');
  assert.deepEqual(posts[0].body, {});
  assert.doesNotMatch(h.region('cards').innerHTML, /data-bt-config=/);
  assert.match(h.region('confirm').textContent, /Rejected\. The current config for Acme stays in use\./);
  h.ui.stop();
});

test('a refusal keeps the item and says so; a stale version is refreshed', async () => {
  const h = harness(queue(), { fail: { status: 403, message: 'no: only the workspace owner can do that' } });
  await flush();
  h.click('accept'); await flush();
  assert.match(h.region('cards').innerHTML, /data-bt-config="config:acme:2"/);
  assert.match(h.region('cards').innerHTML, /only the workspace owner can do that/);
  const stale = harness(queue(), { fail: { status: 409 } });
  await flush();
  stale.click('accept'); await flush();
  assert.match(stale.region('cards').innerHTML, /This config changed\. Review the latest version\./);
  h.ui.stop(); stale.ui.stop();
});

test('with only a proposal waiting, the list is not empty and Questions hides it', async () => {
  const h = harness({ count: 1, approvals: [], config_proposals: [proposal()] });
  await flush();
  assert.match(h.region('cards').innerHTML, /data-bt-config=/);
  assert.doesNotMatch(h.region('cards').innerHTML, /Nothing waits on you/);
  h.ui.stop();
});
