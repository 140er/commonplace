import assert from 'node:assert/strict';
import { test } from 'node:test';
import { agentsView, memberRequestRows, createAgent, agentAction } from '../commonplace/web/static/agents.js';
import { connectionRequestHTML, connectionRequestClick } from '../commonplace/web/static/connection-requests.js';
import { needsMeCount, connectionRequests } from '../commonplace/web/static/needs-me.js';
import { createNeedLedger } from '../commonplace/web/static/deck-home-today.js';
import { renderNeeds } from '../commonplace/web/static/needs.js';
import { mount } from '../commonplace/web/static/deck-today-capy.js';

const item = (extra = {}) => ({ kind: 'connection_request', id: 'request_one', user_id: 'member', requester_name: 'Member Example', name: 'My Coworker', note: 'For planning', status: 'pending', connection: null, ...extra });
const queue = (items = [item()]) => ({ approvals: [], count: items.length, connection_requests: items });
const text = html => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
const until = async predicate => {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (predicate()) return;
    await new Promise(resolve => setImmediate(resolve));
  }
  assert.fail('The expected UI state did not arrive');
};

test('members can request, cancel, collect, and see a decline reason on Connections', () => {
  const html = agentsView({ member_requests: true, requests: [item()] });
  for (const words of ['Connections', 'Name or purpose', 'Note (optional)', 'Request connection', 'Requested, waiting for the owner', 'Cancel request']) assert.ok(text(html).includes(words));
  assert.doesNotMatch(html, /Create token|data-action="agent-rotate"|orchestrator/i);
  const approved = item({ status: 'approved', connection_id: 'conn1', connection: { id: 'conn1', expires_at: '2099-01-01T00:00:00Z' } });
  assert.match(memberRequestRows([approved]), /Collect token/);
  assert.match(memberRequestRows([{ ...approved, collected_at: '2026-01-01' }]), /Connected/);
  assert.doesNotMatch(memberRequestRows([{ ...approved, collected_at: '2026-01-01' }]), /Collect token/);
  assert.match(memberRequestRows([{ ...approved, connection: { ...approved.connection, revoked_at: '2026-01-01' } }]), /Revoked/);
  assert.doesNotMatch(memberRequestRows([{ ...approved, connection: { ...approved.connection, revoked_at: '2026-01-01' } }]), /Collect token|Revoke connection/);
  assert.match(memberRequestRows([item({ status: 'declined', reason: 'Use & review <this>' })]), /Reason: Use &amp; review &lt;this&gt;/);
  const owner = agentsView({ connections: [{ id: 'conn1', auth: 'pat', name: 'member-one', display_name: 'My Coworker', user_name: 'Member Example', can_rotate: false }] });
  assert.match(owner, /Member Example/);
  assert.match(owner, /agent-revoke/);
  assert.doesNotMatch(owner, /agent-rotate/);
});

test('typing a member purpose accepts plain words without owner identifier validation', async t => {
  const originalDocument = globalThis.document, listeners = new Map();
  t.after(() => { globalThis.document = originalDocument; });
  globalThis.document = { addEventListener: (type, callback) => listeners.set(type, callback) };
  await import('../commonplace/web/static/agents.js?member-input');
  const section = { dataset: { memberRequests: 'true' }, querySelectorAll: () => [] };
  const form = { closest: () => section, elements: { namedItem: () => input }, querySelector: () => null };
  const input = { name: 'name', value: 'Plan café / next week?', closest: () => form,
    setCustomValidity(value) { this.validationMessage = value; }, setAttribute() {} };
  assert.doesNotThrow(() => listeners.get('input')({ target: input }));
  assert.equal(input.validationMessage || '', '');
  assert.equal(input.value, 'Plan café / next week?');
});

test('Waiting on you counts each request once and both approval lists show the requester and exact words', () => {
  assert.equal(connectionRequests(queue([item(), item(), null, item({ status: 'declined' })])).length, 1);
  assert.equal(needsMeCount(queue([item(), item()])), 1);
  const html = connectionRequestHTML(item({ name: '<My Coworker>', note: 'One\n  <two>' }));
  assert.match(html, /Member Example/);
  assert.match(html, /&lt;My Coworker&gt;/);
  assert.match(html, /One\n  &lt;two&gt;/);
  assert.match(html, /Approve/); assert.match(html, /Decline/);
  assert.match(html, /Reason if declined \(optional\)/);
  assert.match(html, /90 days/);
  assert.doesNotMatch(html, /orchestrator/i);
  assert.match(renderNeeds({ needs: queue(), filter: 'approvals' }), /data-connection-request="request_one"/);
  assert.match(renderNeeds({ needs: queue() }), /1 item/);
  assert.doesNotMatch(renderNeeds({ needs: queue(), filter: 'decisions' }), /data-connection-request=/);
  const ledger = createNeedLedger(), me = { actor: 'owner', workspace: 'here' };
  ledger.settle(item(), me);
  assert.equal(needsMeCount(ledger.apply(queue(), me)), 0, 'a stale read must not restore a decided request');
  assert.equal(needsMeCount(ledger.apply(queue(), { ...me, workspace: 'elsewhere' })), 1);
});

function requestButton(action = 'approve') {
  const error = { textContent: '' }, reason = { value: 'Please use the project connection.\n  Exact words.' };
  const row = { dataset: { connectionRequest: 'request_one' }, querySelector: selector => selector === '[data-connection-error]' ? error : reason, querySelectorAll: () => buttons };
  const buttons = ['approve', 'decline'].map(value => ({ disabled: false, dataset: { connectionAction: value }, closest: () => row }));
  return { button: buttons.find(button => button.dataset.connectionAction === action), row, error, reason, buttons };
}

test('Approve and Decline send one decision, with the optional reason only on Decline', async () => {
  for (const action of ['approve', 'decline']) {
    const h = requestButton(action), calls = [], done = [];
    let finish;
    const pending = new Promise(resolve => { finish = resolve; });
    const options = { items: [item()], api: (path, options) => { calls.push({ path, ...options }); return pending; }, done: value => done.push(value) };
    const running = connectionRequestClick(h.button, options);
    assert.ok(h.buttons.every(button => button.disabled));
    await connectionRequestClick(h.button, options);
    assert.equal(calls.length, 1);
    finish({ request: item({ status: action === 'approve' ? 'approved' : 'declined' }) });
    await running;
    assert.equal(calls[0].path, `/api/connection-requests/request_one/${action}`);
    assert.deepEqual(calls[0].body, action === 'decline' ? { reason: h.reason.value } : {});
    assert.equal(done.length, 1);
    assert.ok(h.buttons.every(button => !button.disabled));
  }
});

test('a refused decision keeps the item, note, and readable error', async () => {
  const h = requestButton('decline');
  await connectionRequestClick(h.button, { items: [item()], api: async () => { throw new Error('This request has already closed.'); }, done: () => assert.fail('removed a refused request') });
  assert.equal(h.error.textContent, 'This request has already closed.');
  assert.match(h.reason.value, /Exact words/);
  assert.ok(h.buttons.every(button => !button.disabled));
});

test('the member form submits a request and refreshes its status without creating a connection directly', async t => {
  const originalFetch = globalThis.fetch, originalData = globalThis.FormData;
  t.after(() => { globalThis.fetch = originalFetch; globalThis.FormData = originalData; });
  const calls = [], list = { innerHTML: '' }, section = { dataset: { memberRequests: 'true' }, isConnected: true, querySelectorAll: () => [], querySelector: () => list };
  let reset = false;
  const values = { name: 'My Coworker', note: 'For planning' };
  globalThis.FormData = class extends Map { constructor() { super(Object.entries(values)); } };
  globalThis.fetch = async (path, options) => { calls.push({ path, ...options }); return { ok: true, status: 200, json: async () => options.method === 'POST' ? { request: item() } : { member_requests: true, requests: [item()] } }; };
  await createAgent({ hasAttribute: () => true, closest: () => section, reset: () => { reset = true; } });
  assert.deepEqual(calls.map(call => call.path), ['/api/connection-requests', '/api/connection-requests']);
  assert.deepEqual(JSON.parse(calls[0].body), values);
  assert.ok(reset);
  assert.match(list.innerHTML, /Requested, waiting for the owner/);
});

test('the member Cancel action closes the request and refreshes the screen', async t => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  const calls = [], list = { innerHTML: '' };
  const section = { dataset: { memberRequests: 'true' }, isConnected: true, querySelectorAll: () => [], querySelector: () => list };
  globalThis.fetch = async (path, options) => { calls.push({ path, ...options }); return { ok: true, status: 200, json: async () => ({ member_requests: true, requests: [item({ status: 'cancelled' })] }) }; };
  await agentAction({ closest: () => section, dataset: { action: 'agent-request-cancel', requestId: 'request_one' } });
  assert.equal(calls[0].path, '/api/connection-requests/request_one/cancel');
  assert.match(list.innerHTML, /Cancelled/);
  assert.doesNotMatch(list.innerHTML, /Cancel request/);
});

test('Collect shows the token once in the dismissible region, and Revoke clears it', async t => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  const calls = [], list = { innerHTML: '' }, region = { dataset: {}, innerHTML: '', replaceChildren() { this.innerHTML = ''; }, querySelector: () => ({ focus() {} }) };
  const section = { dataset: { memberRequests: 'true' }, isConnected: true, querySelectorAll: () => [], querySelector: selector => selector === '[data-agent-reveal]' ? region : list };
  const connection = { id: 'conn1', display_name: 'My Coworker', name: 'member-one', client: 'generic', expires_at: '2099-01-01T00:00:00Z' };
  const approved = item({ status: 'approved', connection_id: 'conn1', connection, collected_at: '2026-01-01' });
  globalThis.fetch = async (path, options) => {
    calls.push({ path, ...options });
    return { ok: true, status: 200, json: async () => path.endsWith('/collect') ? { connection, token: 'dummy-test-secret', mcp_url: 'https://example.test/mcp' } : { member_requests: true, requests: [approved] } };
  };
  const button = action => ({ closest: () => section, dataset: { action, requestId: 'request_one', agentId: 'conn1' } });
  await agentAction(button('agent-request-collect'));
  assert.match(region.innerHTML, /dummy-test-secret/);
  assert.match(region.innerHTML, /will not be shown again/);
  assert.doesNotMatch(list.innerHTML, /Collect token|dummy-test-secret/);
  await agentAction(button('agent-request-revoke'));
  assert.equal(region.innerHTML, '');
  assert.equal(calls.filter(call => call.path.endsWith('/collect')).length, 1);
  assert.ok(calls.some(call => call.path.endsWith('/revoke') && call.method === 'POST'));
});

class Node {
  hidden = false; innerHTML = ''; textContent = ''; disabled = false; value = ''; dataset = {};
  querySelector() { return null; } querySelectorAll() { return []; } focus() {} close() {} showModal() {} addEventListener() {} removeEventListener() {}
}

function today() {
  const regions = new Map(), listeners = new Map(), calls = [], changed = [];
  const region = name => { if (!regions.has(name)) regions.set(name, new Node()); return regions.get(name); };
  const form = Object.assign(new Node(), { elements: { choice: new Node(), reply: new Node() }, querySelector: () => new Node() });
  const dialog = Object.assign(new Node(), { querySelector: selector => selector === 'form' ? form : new Node() });
  const root = Object.assign(new Node(), {
    ownerDocument: { addEventListener() {}, removeEventListener() {}, querySelector: () => null }, classList: { add() {}, remove() {} }, closest: () => null,
    addEventListener: (type, fn) => listeners.set(type, fn),
    querySelector: selector => selector === 'dialog' ? dialog : /data-bt="([^"]+)"/.test(selector) ? region(/data-bt="([^"]+)"/.exec(selector)[1]) : null,
  });
  const api = async (path, options = {}) => {
    calls.push({ path, ...options });
    if (path === '/api/needs-me') return queue();
    if (path === '/api/bots') return { bots: [] };
    if (path.startsWith('/api/events')) return { events: [], seq: 0 };
    if (path.startsWith('/api/connection-requests/')) return { request: item({ status: 'approved' }) };
    assert.fail(`Unexpected request ${path}`);
  };
  const ui = mount({ root, api, me: { can_admin: true }, needs: queue(), onNeedsChanged: item => changed.push(item) });
  return { ui, calls, changed, region, click(action) {
    const h = requestButton(action), button = { ...h.button, closest: () => h.row };
    listeners.get('click')({ preventDefault() {}, target: { closest: selector => selector === '[data-connection-action]' ? button : null } });
  } };
}

test('Today offers real Approve and Decline actions, removes the decision, and resists stale reads', async () => {
  for (const action of ['approve', 'decline']) {
    const h = today();
    try {
      await until(() => h.region('summary').textContent === '1 item waiting');
      assert.match(h.region('cards').innerHTML, /Member Example/);
      assert.match(h.region('chips').innerHTML, /Reviews <b>1/);
      h.click(action);
      await until(() => h.changed.length === 1);
      assert.ok(h.calls.some(call => call.path === `/api/connection-requests/request_one/${action}` && call.method === 'POST'));
      assert.equal(h.region('summary').textContent, 'Nothing is waiting');
      assert.doesNotMatch(h.region('cards').innerHTML, /data-connection-request=/);
      await h.ui.refresh();
      assert.equal(h.region('summary').textContent, 'Nothing is waiting');
    } finally { h.ui.stop(); }
  }
});
