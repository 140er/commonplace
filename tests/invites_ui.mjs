// Invite block and workspace switcher against a minimal fake DOM: no browser, no network.
import assert from 'node:assert/strict';
let input = ''; for await (const part of process.stdin) input += part;
const sources = JSON.parse(input);
const load = text => import(`data:text/javascript;base64,${Buffer.from(text).toString('base64')}`);
const { createInviteBlock, inviteBody, openWorkspaces } = await load(sources.invites);
const { createAdmin } = await load(sources.admin);
const e = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const LINK = 'https://app.example.test/app/join?t=inv_LINKCANARY0001';

const leaks = [], ledger = [];
for (const name of ['log', 'info', 'warn', 'error', 'debug']) console[name] = (...args) => ledger.push(args.join(' '));
for (const name of ['localStorage', 'sessionStorage']) {
  globalThis[name] = { setItem: (k, v) => leaks.push(name + k + v), getItem: () => null, removeItem() {} };
}

function region() {
  return {
    innerHTML: '', textContent: '', dataset: {},
    replaceChildren() { this.innerHTML = ''; },
    querySelector() {
      const found = /data-invite-link value="([^"]*)"/.exec(this.innerHTML);
      return found ? { value: found[1] } : null;
    },
  };
}
function fixture({ fail = {} } = {}) {
  const regions = {}, listeners = {}, calls = [], copied = [];
  const host = {
    innerHTML: '',
    addEventListener(kind, fn) { listeners[kind] = fn; },
    removeEventListener(kind) { delete listeners[kind]; },
    querySelector(selector) { return regions[/data-invite-([\w-]+)/.exec(selector)[1]] ??= region(); },
  };
  const state = { invites: [
    { id: 'invite_open', email: '', state: 'pending', expires_at: '2026-11-18T10:00:00Z', accepted_by_email: null },
    { id: 'invite_used', email: '<b>guest@example.test</b>', state: 'accepted', expires_at: '2026-11-17T10:00:00Z', accepted_by_email: 'guest@example.test' },
  ] };
  const api = async (path, options = {}) => {
    calls.push({ path, method: options.method || 'GET', body: options.body });
    const key = `${options.method || 'GET'} ${path}`;
    if (fail[key]) throw Object.assign(new Error(fail[key]), { status: 400 });
    if (path === '/api/invites' && !options.method) return { invites: state.invites };
    if (path === '/api/invites') return { invite: { id: 'invite_new', state: 'pending' }, secret: 'inv_LINKCANARY0001', link: LINK };
    return { invite: { id: 'invite_open', state: 'revoked' } };
  };
  const block = createInviteBlock({ api, e, copy: async text => { copied.push(text); } });
  const field = value => ({ value });
  const submit = async (email, days) => {
    const form = { elements: { email: field(email), days: field(days) } };
    listeners.submit({ target: { closest: () => form }, preventDefault() {} });
    await flush();
    return form;
  };
  const click = async dataset => { await listeners.click({ target: { closest: () => ({ dataset }) } }); await flush(); };
  return { block, host, regions, calls, copied, submit, click, listeners };
}

// Request bodies: a blank email is left out and the days are bounded.
assert.deepEqual(inviteBody({ email: '  ', days: '7' }), { days: 7 });
assert.deepEqual(inviteBody({ email: ' a@example.test ', days: 30 }), { email: 'a@example.test', days: 30 });
for (const days of ['0', '31', '1.5', '', 'x']) assert.throws(() => inviteBody({ days }), /1 to 30/);

// The list shows state, escapes what it shows and offers Revoke for open invites only.
{
  const f = fixture();
  f.block.show(f.host); await flush();
  assert.deepEqual(f.calls, [{ path: '/api/invites', method: 'GET', body: undefined }]);
  const list = f.regions.list.innerHTML;
  assert.match(list, /Anyone with the link/);
  assert.match(list, /&lt;b&gt;guest@example.test&lt;\/b&gt;/);
  assert.ok(!list.includes('<b>guest'));
  assert.equal((list.match(/data-invite-action="revoke"/g) || []).length, 1);
  assert.match(list, /data-invite-id="invite_open"/);
  assert.match(f.host.innerHTML, /Members cannot read private wings/);
}

// Making an invite shows the link once, in memory only.
{
  const f = fixture();
  f.block.show(f.host); await flush();
  const form = await f.submit('guest@example.test', '3');
  assert.deepEqual(f.calls[1], { path: '/api/invites', method: 'POST', body: { email: 'guest@example.test', days: 3 } });
  assert.equal(f.calls[2].path, '/api/invites', 'the list reloads');
  assert.match(f.regions.reveal.innerHTML, /not shown again/);
  assert.ok(f.regions.reveal.innerHTML.includes(e(LINK)));
  assert.ok(!f.regions.list.innerHTML.includes('LINKCANARY'));
  assert.equal(form.elements.email.value, '');
  await f.click({ inviteAction: 'copy' });
  assert.deepEqual(f.copied, [LINK]);
  assert.equal(f.regions.status.textContent, 'Copied.');
  await f.click({ inviteAction: 'dismiss' });
  assert.equal(f.regions.reveal.innerHTML, '');
  f.block.stop();
  assert.equal(f.host.innerHTML, '');
  assert.deepEqual(leaks, []);
  assert.ok(!ledger.join('\n').includes('LINKCANARY'));
}

// A refused request leaves no link and says why; bad input never reaches the server.
{
  const f = fixture({ fail: { 'POST /api/invites': 'Enter a valid email address' } });
  f.block.show(f.host); await flush();
  await f.submit('nope', '7');
  assert.equal(f.regions.status.textContent, 'Enter a valid email address');
  assert.equal(f.regions.status.dataset.state, 'error');
  assert.equal(f.regions.reveal?.innerHTML || '', '');
  const before = f.calls.length;
  await f.submit('', '99');
  assert.equal(f.calls.length, before);
  assert.match(f.regions.status.textContent, /1 to 30/);
}

// Revoke posts to the invite's own route and reloads the list.
{
  const f = fixture();
  f.block.show(f.host); await flush();
  await f.click({ inviteAction: 'revoke', inviteId: 'invite/open' });
  assert.deepEqual(f.calls[1], { path: '/api/invites/invite%2Fopen/revoke', method: 'POST', body: undefined });
  assert.equal(f.calls[2].path, '/api/invites');
  assert.match(f.regions.status.textContent, /no longer works/);
}

// Admin mounts the block beside its refreshing sections and stops it on the way out.
{
  const shown = [], slots = {};
  const mount = { innerHTML: '', addEventListener() {}, removeEventListener() {}, querySelector(selector) { return slots[selector] ??= { innerHTML: '', selector }; } };
  const invites = { show(node) { shown.push(node.selector); }, stop() { shown.push('stop'); } };
  globalThis.document = { addEventListener() {}, removeEventListener() {} };
  const admin = createAdmin({ mount: () => mount, api: async () => ({}), e, current: () => ({ can_admin: true, workspace: 'ws_1', workspace_name: 'Home' }), invites, timers: { setTimeout: () => 0, clearTimeout() {} } });
  admin.show(); await flush();
  assert.deepEqual(shown, ['stop', '[data-admin-invites]']);
  admin.stop();
  assert.equal(shown.at(-1), 'stop');
  const member = createAdmin({ mount: () => mount, api: async () => ({}), e, current: () => ({ can_admin: false }), invites, timers: { setTimeout: () => 0, clearTimeout() {} } });
  shown.length = 0; member.show();
  assert.deepEqual(shown, ['stop'], 'a member never gets the invite block');
}

// The switcher lists only what the server returned and reloads after a switch.
{
  const calls = [], errors = [], reloads = [];
  let closed = 0, removed = 0, listener = {};
  const dialog = {
    className: '', innerHTML: '', attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(kind, fn) { listener[kind] = fn; },
    querySelector() { return this.error ??= { textContent: '' }; },
    showModal() { this.open = true; }, close() { closed++; }, remove() { removed++; },
  };
  const doc = { querySelector: () => null, createElement: () => dialog, body: { append(node) { doc.appended = node; } } };
  let fail = false;
  const api = async (path, options = {}) => {
    calls.push({ path, method: options.method || 'GET', body: options.body });
    if (fail) throw new Error('Workspace not found');
    return path === '/api/workspaces' ? { workspaces: [
      { id: 'ws_home', name: 'Mine <i>', role: 'owner', current: true },
      { id: 'ws_team', name: 'Team', role: 'member', current: false },
    ] } : { workspace: 'ws_team' };
  };
  const opened = await openWorkspaces({ api, e, onError: text => errors.push(text), doc, reload: () => reloads.push(1) });
  assert.equal(opened, dialog);
  assert.ok(dialog.open && doc.appended === dialog);
  assert.match(dialog.innerHTML, /data-workspace="ws_home" disabled aria-current="true"/);
  assert.match(dialog.innerHTML, /Mine &lt;i&gt; · owner · current/);
  assert.match(dialog.innerHTML, /data-workspace="ws_team">Team · member</);
  const press = target => listener.click({ target: { closest: selector => target[selector] || null } });
  await press({ '[data-workspace]': { dataset: { workspace: 'ws_team' } } });
  assert.deepEqual(calls[1], { path: '/api/workspaces/switch', method: 'POST', body: { workspace: 'ws_team' } });
  assert.equal(reloads.length, 1);
  fail = true;
  await press({ '[data-workspace]': { dataset: { workspace: 'ws_gone' } } });
  assert.equal(reloads.length, 1);
  assert.equal(dialog.error.textContent, 'Workspace not found');
  await press({ '[data-workspace-close]': {} });
  assert.equal(closed, 1);
  listener.close(); assert.equal(removed, 1);
  assert.equal(await openWorkspaces({ api, e, onError: text => errors.push(text), doc, reload() {} }), null);
  assert.deepEqual(errors, ['Workspace not found']);
}
