import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
const source = await readFile(new URL('../commonplace/web/static/invites.js', import.meta.url), 'utf8');
const { openWorkspaces } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const e = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');

async function fixture({ refusal = false, uncertain = false, deck = false, workspaces = [{ id: 'old', name: 'Current workspace', role: 'owner', current: true }] } = {}) {
  const calls = [], listeners = {}, error = { textContent: '' }, input = { value: '', focus() {} };
  const dialog = {
    innerHTML: '', setAttribute() {}, addEventListener: (kind, fn) => listeners[kind] = fn,
    querySelector: selector => selector.includes('name') ? input : selector.includes('error') ? error : { textContent: '' },
    querySelectorAll: () => [], showModal() {}, close() { this.closed = true; }, remove() {},
  };
  let reloads = 0, attempts = 0;
  const api = async (path, options) => {
    calls.push({ path, ...options });
    if (!options) return { workspaces };
    if (path.endsWith('/switch')) return {};
    if (refusal) throw Object.assign(new Error(typeof refusal === 'string' ? refusal : 'This instance is closed to new workspaces.'), { status: refusal.includes?.('five') ? 429 : 403 });
    if (uncertain && ++attempts === 1) throw new Error('Connection lost. Try again.');
    return { workspace: { id: 'new', name: options.body.name, role: 'owner' } };
  };
  await openWorkspaces({ api, e, doc: { documentElement: { dataset: { ui: deck ? 'deck' : 'classic' } }, querySelector: () => null, createElement: () => dialog, body: { append() {} } }, reload: () => reloads++ });
  const click = (selector, dataset = {}) => listeners.click({ target: { closest: query => query === selector ? { dataset } : null } });
  const submit = name => {
    input.value = name;
    return listeners.submit({ preventDefault() {}, target: { closest: () => ({ elements: { name: input } }) } });
  };
  return { dialog, calls, error, click, submit, reloads: () => reloads };
}

test('switcher confirms, creates once, shows result, then switches', async () => {
  const f = await fixture();
  assert.match(f.dialog.innerHTML, /Create workspace/);
  await f.click('[data-workspace-create]');
  await f.submit('Research');
  assert.match(f.dialog.innerHTML, /empty isolated workspace owned by you/);
  assert.match(f.dialog.innerHTML, /current workspace is unaffected/);
  assert.equal(f.calls.length, 1, 'name and confirmation do not create anything');
  await Promise.all([f.click('[data-workspace-confirm]'), f.click('[data-workspace-confirm]')]);
  assert.equal(f.calls.filter(c => c.path === '/api/workspaces' && c.method === 'POST').length, 1);
  assert.match(f.calls[1].body.request_id, /^[0-9a-f-]{36}$/);
  assert.match(f.dialog.innerHTML, /Workspace created/);
  assert.match(f.dialog.innerHTML, /empty/);
  assert.equal(f.reloads(), 0);
  await f.click('[data-workspace]', { workspace: 'new' });
  assert.equal(f.calls.at(-1).path, '/api/workspaces/switch');
  assert.equal(f.reloads(), 1);
});

test('cancel confirmation creates nothing and escapes names', async () => {
  const f = await fixture();
  await f.click('[data-workspace-create]');
  await f.submit('<script>');
  assert.ok(!f.dialog.innerHTML.includes('<script>'));
  assert.match(f.dialog.innerHTML, /&lt;script>/);
  await f.click('[data-workspace-cancel]');
  assert.match(f.dialog.innerHTML, /Switch workspace/);
  assert.equal(f.calls.length, 1);
});

test('refusal explains why and stays in current workspace', async () => {
  const f = await fixture({ refusal: true });
  await f.click('[data-workspace-create]'); await f.submit('Research');
  await f.click('[data-workspace-confirm]');
  assert.equal(f.error.textContent, 'This instance is closed to new workspaces.');
  assert.equal(f.reloads(), 0);
});

test('uncertain retry keeps the same idempotency request', async () => {
  const f = await fixture({ uncertain: true });
  await f.click('[data-workspace-create]'); await f.submit('Research');
  await f.click('[data-workspace-confirm]'); await f.click('[data-workspace-confirm]');
  const creates = f.calls.filter(c => c.method === 'POST');
  assert.equal(creates.length, 2);
  assert.equal(creates[0].body.request_id, creates[1].body.request_id);
  assert.match(f.dialog.innerHTML, /Workspace created/);
});


test('Deck switcher uses workspace rows, a current tick and quiet creation', async () => {
  const f = await fixture({ deck: true });
  assert.match(f.dialog.innerHTML, /class="workspace-row"[^>]*aria-current="true"/);
  assert.match(f.dialog.innerHTML, /class="workspace-label">Current workspace</);
  assert.match(f.dialog.innerHTML, /class="workspace-role">owner</);
  assert.match(f.dialog.innerHTML, /workspace-current[^>]*>✓</);
  assert.doesNotMatch(f.dialog.innerHTML, /disabled aria-current/);
  assert.match(f.dialog.innerHTML, /data-workspace-create[^>]*>\+ Create workspace</);
  assert.doesNotMatch(f.dialog.innerHTML, /class="btn primary"/);
  await f.click('[data-workspace]', { workspace: 'old' });
  assert.equal(f.calls.length, 1, 'current row never switches or changes its session');
});

test('Deck copy names the workspace once in confirmation and once in result', async () => {
  const f = await fixture({ deck: true });
  await f.click('[data-workspace-create]'); await f.submit('Separate work');
  assert.match(f.dialog.innerHTML, /Create "Separate work"\?/);
  assert.match(f.dialog.innerHTML, /This makes a new, empty workspace that only you can see. Your current workspace does not change./);
  assert.doesNotMatch(f.dialog.innerHTML, /isolated|unaffected|workspace-name/);
  assert.equal((f.dialog.innerHTML.match(/<p>/g) || []).length, 1);
  await f.click('[data-workspace-confirm]');
  assert.match(f.dialog.innerHTML, /"Separate work" is ready. It is empty and only you are in it./);
  assert.doesNotMatch(f.dialog.innerHTML, /only member and owner|unaffected|workspace-name/);
});

for (const [reason, expected] of [
  ['This instance is closed to new workspaces.', 'New workspaces are turned off on this server.'],
  ['You have created five workspaces recently. Try again in an hour.', 'Too many new workspaces. Try again in 60 minutes.'],
  ['Your plan does not allow another workspace.', 'Your plan does not allow another workspace.'],
]) {
  test(`Deck refusal ends creation: ${reason}`, async () => {
    const f = await fixture({ deck: true, refusal: reason });
    await f.click('[data-workspace-create]'); await f.submit('Separate work');
    await f.click('[data-workspace-confirm]');
    assert.match(f.dialog.innerHTML, /Workspace not created/);
    assert.ok(f.dialog.innerHTML.includes(expected));
    assert.doesNotMatch(f.dialog.innerHTML, /data-workspace-confirm|data-workspace-cancel|Create workspace/);
    assert.equal((f.dialog.innerHTML.match(/<button/g) || []).length, 1);
    assert.match(f.dialog.innerHTML, /data-workspace-close>Close</);
    assert.equal(f.reloads(), 0);
  });
}

test('Deck name step rejects blank, too-long and existing names without sending a request', async () => {
  for (const name of ['', 'x'.repeat(129), ' current WORKSPACE ']) {
    const f = await fixture({ deck: true });
    await f.click('[data-workspace-create]'); await f.submit(name);
    assert.match(f.dialog.innerHTML, /data-workspace-name/);
    assert.doesNotMatch(f.dialog.innerHTML, /data-workspace-confirm/);
    assert.equal(f.calls.length, 1);
  }
});
