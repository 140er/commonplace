// Setup proposals in Waiting on you (card #675): the count, the item, and Approve or Reject through the real mount.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { escape as e } from '../commonplace/web/static/api.js';
import { needsMeCount, setupProposals } from '../commonplace/web/static/needs-me.js';
import { removeSetup, createNeedLedger } from '../commonplace/web/static/deck-home-today.js';
import { setupHTML, appliedLine, mount, summaryLine, chipsHTML } from '../commonplace/web/static/deck-today-capy.js';

const SHA = 'b'.repeat(64);
const setup = (extra = {}) => ({
  kind: 'setup', id: 3, ref: 'v3', sha256: SHA, proposed_by: 'conn:Claude Code', proposer_name: 'Claude Code',
  proposed_at: '2030-01-01T00:00:00Z', summary: 'Creates the bot "Work lead"; Creates a Coworker on Work with the bot "Work lead"',
  changes: [
    { kind: 'bot', action: 'create', text: 'Creates the bot "Work lead"' },
    { kind: 'coworker', action: 'create', text: 'Creates a Coworker on Work with the bot "Work lead"' },
    { kind: 'model', action: 'set', text: 'Workspace default Coworker: no choice to grok/grok-4.7' },
    { kind: 'model', action: 'unchanged', text: 'Work gate: stays claude/claude-opus-5-5' },
  ],
  problems: 0, ...extra,
});
const config = { board: 'acme', board_name: 'Acme', version: 2, ref: 'v2', channel: 'live', sha256: 'a'.repeat(64), summary: 'Agent limit: 3 to 5', diff: '' };
const card = { number: 7, version: 4, title: 'Review the release', actions: ['approve', 'reject'], board: 'acme' };
const queue = (extra = {}) => ({ count: 2, approvals: [card], setup_proposals: [setup()], ...extra });
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

test('a setup is counted with the cards and config proposals, once per id, and invalid ones are not', () => {
  assert.equal(needsMeCount(queue()), 2);
  assert.equal(needsMeCount(queue({ config_proposals: [config] })), 3);
  assert.equal(needsMeCount({ approvals: [], setup_proposals: [setup(), setup()] }), 1);
  assert.equal(needsMeCount({ approvals: [], setup_proposals: [setup({ id: 0 }), setup({ sha256: '' }), null, setup({ id: '3' })] }), 0);
  assert.equal(needsMeCount({ count: 4 }), 4, 'a member with no lists keeps the plain number');
  assert.equal(summaryLine(queue()), '2 items waiting');
  assert.deepEqual(setupProposals({ approvals: [] }), []);
});

test('deciding a setup removes it and the count follows; the ledger keeps a stale read from restoring it', () => {
  const needs = queue();
  const next = removeSetup(needs, setup());
  assert.equal(next.count, 1); assert.deepEqual(next.setup_proposals, []);
  assert.equal(needs.setup_proposals.length, 1, 'never mutates the shell snapshot');
  assert.equal(removeSetup(needs, setup({ id: 4 })).count, 2, 'another version stays');
  const ledger = createNeedLedger(), me = { actor: 'user:one', workspace: 'one' };
  ledger.settle(setup(), me);
  assert.equal(ledger.apply(needs, me).count, 1);
  assert.equal(ledger.apply(queue({ setup_proposals: [setup({ id: 4 })] }), me).count, 2);
  assert.equal(ledger.apply(needs, { ...me, workspace: 'two' }).count, 2);
});

test('the item names the proposer, says what it does in one line and lists every change', () => {
  const html = setupHTML(setup(), {}, e);
  assert.match(html, /<b>Claude Code<\/b><span class="bt-verb">suggests a setup<\/span>/);
  assert.doesNotMatch(html, /conn:/);
  assert.match(html, /<h2 id="bt-title-setup:3">Creates the bot &quot;Work lead&quot;; Creates a Coworker on Work/);
  assert.match(html, /Setup v3: 3 changes to your bots, Coworkers and models/);
  assert.match(html, /<summary>Changes<\/summary><p class="bt-full-text" data-setup-changes>\+ Creates the bot &quot;Work lead&quot;\n\+ Creates a Coworker on Work with the bot &quot;Work lead&quot;\n~ Workspace default Coworker: no choice to grok\/grok-4.7\n= Work gate: stays claude\/claude-opus-5-5<\/p>/);
  assert.match(html, /class="bt-approve bt-primary" data-bt-setup-action="apply">Approve</);
  assert.match(html, /class="bt-secondary" data-bt-setup-action="reject">Reject</);
  assert.match(html, /Approve means yes: everything under Changes is created or set for you now, exactly as listed\.<\/p>/);
  assert.doesNotMatch(html, /orchestrator/i);
  assert.match(setupHTML(setup(), { open: new Set(['setup:3']) }, e), /data-bt-setup-changes="setup:3" open>/);
  assert.match(setupHTML(setup({ changes: null }), {}, e), /Could not load the changes/);
});

test('a change that cannot be done now is marked and the note says how many', () => {
  const html = setupHTML(setup({ problems: 1, changes: [{ kind: 'coworker', action: 'create', text: 'Creates a Coworker on gone', problem: 'unknown_project' }] }), {}, e);
  assert.match(html, /\+ Creates a Coworker on gone \(cannot be done now: the project is gone\)/);
  assert.match(html, /1 of them cannot be done now and will be reported\./);
});

test('names and changes are escaped, never markup', () => {
  const html = setupHTML(setup({ proposer_name: '<img src=x onerror=bad()>', summary: '<b>x</b>', changes: [{ action: 'create', text: '<script>bad()</script>' }] }), {}, e);
  assert.doesNotMatch(html, /<img|<script|<b>x/);
  assert.match(html, /&lt;img/);
});

test('Reject asks once, Cancel goes back, and busy disables the buttons', () => {
  const html = setupHTML(setup(), { confirming: new Set(['setup:3']) }, e);
  assert.match(html, /Reject setup v3\? Nothing is created or changed\. This version cannot be approved after rejection\./);
  assert.match(html, /data-bt-setup-action="reject-confirm">Reject version</);
  assert.match(html, /data-bt-setup-action="cancel">Cancel</);
  assert.doesNotMatch(html, /data-bt-setup-action="apply"/);
  assert.equal((setupHTML(setup(), { busy: new Set(['setup:3']) }, e).match(/ disabled/g) || []).length, 2);
  assert.match(setupHTML(setup(), { errors: new Map([['setup:3', 'Nope']]) }, e), /<p class="bt-error" role="alert">Nope<\/p>/);
});

test('a setup counts as a review in the filter chips', () => {
  const chips = chipsHTML([card, { ...setup(), actions: ['approve', 'reject'] }]);
  assert.match(chips, /data-bt-filter="review" aria-pressed="false">Reviews <b>2<\/b>/);
});

test('the confirmation says what was done, all of it or how many items', () => {
  const ok = { status: 'applied', results: [{ ok: true }, { ok: true }] };
  assert.equal(appliedLine(ok, setup()), 'Approved. Setup v3 is in place: everything under Changes was created or set.');
  const partial = { status: 'partial', results: [{ ok: true }, { ok: false }, { ok: true }] };
  assert.equal(appliedLine(partial, setup()), 'Approved. Setup v3 is only partly in place: 1 of 3 parts could not be done. The rest is in place.');
});

class Node {
  hidden = false; innerHTML = ''; textContent = ''; disabled = false; value = ''; dataset = {};
  querySelector() { return null; } querySelectorAll() { return []; } focus() {} close() {} showModal() {} addEventListener() {} removeEventListener() {}
}
function harness(initial = queue(), { fail, result = { status: 'applied', results: [{ ok: true }, { ok: true }] } } = {}) {
  const regions = new Map(), listeners = new Map(), calls = [];
  const region = name => { if (!regions.has(name)) regions.set(name, new Node()); return regions.get(name); };
  const dialog = Object.assign(new Node(), { open: false });
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
      feed = { ...feed, count: 1, setup_proposals: [] };
      return path.endsWith('/apply') ? result : { status: 'rejected' };
    }
    throw new Error(`unexpected ${path}`);
  };
  const changed = [];
  const ui = mount({ root, api, me: { can_admin: true }, needs: initial, go() {}, onNeedsChanged: item => changed.push(item), singleKeys: () => false });
  const click = action => {
    const target = {
      closest: selector => selector === '[data-bt-setup-action]'
        ? { dataset: { btSetupAction: action }, disabled: false, closest: q => q === '[data-bt-setup]' ? { dataset: { btSetup: 'setup:3' } } : null }
        : null,
    };
    listeners.get('click')({ preventDefault() {}, target });
  };
  return { region, calls, ui, click, changed };
}

test('Approve is one click: it posts the digest shown to the apply route and the item leaves', async () => {
  const h = harness(queue({ config_proposals: [config] }));
  await flush();
  const list = h.region('cards').innerHTML;
  assert.match(list, /data-bt-setup="setup:3"/);
  assert.ok(list.indexOf('data-bt-setup=') < list.indexOf('data-bt-config=') && list.indexOf('data-bt-config=') < list.indexOf('data-bt-card='), 'setups, then configs, then cards');
  assert.equal(h.region('summary').textContent, '3 items waiting');
  h.click('apply'); await flush();
  const post = h.calls.find(call => call.method === 'POST');
  assert.equal(post.path, '/api/setup-proposals/3/apply');
  assert.deepEqual(post.body, { sha256: SHA });
  assert.doesNotMatch(h.region('cards').innerHTML, /data-bt-setup=/);
  assert.match(h.region('confirm').textContent, /Approved\. Setup v3 is in place: everything under Changes was created or set\./);
  assert.equal(h.changed.length, 1); assert.equal(h.changed[0].id, 3);
  h.ui.stop();
});

test('a partly applied setup says how many items were done', async () => {
  const h = harness(queue(), { result: { status: 'partial', results: [{ ok: true }, { ok: false }] } });
  await flush();
  h.click('apply'); await flush();
  assert.match(h.region('confirm').textContent, /only partly in place: 1 of 2 parts could not be done/);
  h.ui.stop();
});

test('Reject is two clicks and posts the digest to the reject route', async () => {
  const h = harness();
  await flush();
  h.click('reject'); await flush();
  assert.equal(h.calls.filter(call => call.method === 'POST').length, 0, 'the first click only asks');
  assert.match(h.region('cards').innerHTML, /Reject setup v3\?/);
  h.click('cancel'); await flush();
  assert.match(h.region('cards').innerHTML, /data-bt-setup-action="apply"/);
  h.click('reject'); await flush();
  h.click('reject-confirm'); await flush();
  const posts = h.calls.filter(call => call.method === 'POST');
  assert.equal(posts.length, 1); assert.equal(posts[0].path, '/api/setup-proposals/3/reject');
  assert.deepEqual(posts[0].body, { sha256: SHA });
  assert.doesNotMatch(h.region('cards').innerHTML, /data-bt-setup=/);
  assert.match(h.region('confirm').textContent, /Rejected\. Nothing from setup v3 was created or changed\./);
  h.ui.stop();
});

test('a refusal keeps the item and says so; a changed or decided version is refreshed', async () => {
  const h = harness(queue(), { fail: { status: 403, message: 'only the signed-in owner can approve or reject a setup' } });
  await flush();
  h.click('apply'); await flush();
  assert.match(h.region('cards').innerHTML, /data-bt-setup="setup:3"/);
  assert.match(h.region('cards').innerHTML, /only the signed-in owner can approve or reject a setup/);
  const stale = harness(queue(), { fail: { status: 409 } });
  await flush();
  stale.click('apply'); await flush();
  assert.match(stale.region('cards').innerHTML, /This setup changed or was already decided\. Review the latest version\./);
  h.ui.stop(); stale.ui.stop();
});

test('with only a setup waiting, the list is not empty', async () => {
  const h = harness({ count: 1, approvals: [], setup_proposals: [setup()] });
  await flush();
  assert.match(h.region('cards').innerHTML, /data-bt-setup=/);
  assert.doesNotMatch(h.region('cards').innerHTML, /Nothing waits on you/);
  h.ui.stop();
});
