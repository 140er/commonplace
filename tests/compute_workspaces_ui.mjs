import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// Cloud workspaces on the Compute screen: the production module and the production Compute controller run
// against a synthetic in-memory server. No socket, browser or backend is opened.
const source = name => readFileSync(new URL(`../commonplace/web/static/${name}.js`, import.meta.url), 'utf8');
const moduleURL = text => `data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
const apiURL = moduleURL(source('api'));
const rewrite = (text, extra = {}) => Object.entries({ './api.js': apiURL, ...extra }).reduce((out, [name, url]) => out.replace(new RegExp(`(['"])${name.replace(/[./]/g, '\\$&')}\\1`, 'g'), () => JSON.stringify(url)), text);
const workspacesURL = moduleURL(rewrite(source('compute-workspaces')));
const ws = await import(workspacesURL);
const { createCompute } = await import(moduleURL(rewrite(source('compute'), { './compute-workspaces.js': workspacesURL })));
const flush = async () => { for (let i = 0; i < 60; i++) await Promise.resolve(); };
const clock = 1800000000;
const failure = (status, payload) => Object.assign(new Error(payload?.error || String(status)), { status, payload });
const text = html => html.replace(/<[^>]*>/g, ' ').replace(/&#39;/g, "'").replace(/\s+/g, ' ');
const options = {
  images: [
    { id: 'ubuntu-24-04', label: 'Ubuntu 24.04', reference: 'docker.io/library/ubuntu:24.04', description: 'General-purpose Linux.' },
    { id: 'python-3-12-slim', label: 'Python 3.12 (slim)', reference: 'docker.io/library/python:3.12-slim', description: 'Python 3.12.' },
  ],
  limits: { cpu_cores: { min: 0.5, max: 32, step: 0.5, default: 2 }, memory_mib: { min: 512, max: 131072, step: 128, default: 4096 }, storage_gib: { min: 1, max: 2048, step: 1, default: 20 } },
  networks: ['outbound', 'none'], default_network: 'outbound', name_limit: 60,
};
const machine = { id: 'n-one', name: 'Fixture machine', state: 'pending_verification', gateway_id: 'g-one', board: 'demo' };
const record = (extra = {}) => ({
  id: 'w-one', name: 'Build box', image_source: 'custom', image: 'ghcr.io/acme/tools:1.2', image_registry: 'ghcr.io', image_tag: '1.2', image_digest: null, image_pinned: false, curated_label: null,
  private_registry: false, credential_bound: false, registry_credential_id: null, registry_credential_version: null, cpu_cores: 2, memory_mib: 4096, storage_gib: 20, network: 'outbound',
  node_id: null, machine_name: null, status: 'no_machine', status_reason: null, started: false, revision: 1, applied_revision: null, restart_needed: false, created_at: clock, updated_at: clock, ...extra,
});
const inventory = () => ({ gateways: [], nodes: [machine], scopes: [], executions: [], can_manage: true, capabilities: { transport: false, passive_discovery: false, scripts: false } });

function fixture({ list = { workspaces: [], can_manage: true }, config = options, owner = true } = {}) {
  let html = '', hash = '#/compute';
  const user = { workspace: 'w', actor: 'user:u', can_admin: true };
  const listeners = new Map(), calls = [], doc = new EventTarget(), events = new EventTarget(), focused = [];
  const host = {
    get innerHTML() { return html; }, set innerHTML(value) { html = value; },
    addEventListener(kind, fn) { listeners.set(kind, fn); }, removeEventListener(kind) { listeners.delete(kind); },
    querySelector(selector) { return selector === '#compute-editor-heading' || selector === '#compute-ws-heading' || selector === '#compute-workspaces' ? { focus() { focused.push(selector); } } : null; },
  };
  const state = { list, config, writes: [], next: null };
  const api = (path, options = {}) => {
    calls.push([options.method || 'GET', path, options.body]);
    if (options.method === 'POST') { state.writes.push([path, options.body]); const out = state.next; state.next = null; return out instanceof Error ? Promise.reject(out) : Promise.resolve(structuredClone(out)); }
    const answer = path === '/api/compute/workspaces' ? state.list : path === '/api/compute/workspaces/images' ? state.config : path === '/api/compute' ? (owner ? inventory() : failure(403, { error: 'owner_required' })) : path === '/api/boards' ? { boards: [] } : path === '/api/compute/discoveries' ? { discoveries: [], capabilities: {} } : path === '/api/compute/scripts' ? { scripts: [] } : path === '/api/compute/credentials' ? { credentials: [], status: 'ready' } : null;
    return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(structuredClone(answer));
  };
  const ui = createCompute({ mount: () => host, api, current: () => user, route: () => hash, doc, events, now: () => clock, requestId: () => 'r-1' });
  const dispatch = (kind, event) => listeners.get(kind)?.(event);
  const click = (action, id) => dispatch('click', { target: { closest: selector => selector === '[data-ws-action]' ? { dataset: { wsAction: action, id } } : null } });
  const input = (name, value, type = 'text') => dispatch('input', { target: { name, type, value, checked: value } });
  const submit = kind => dispatch('submit', { target: { closest: selector => selector === '[data-ws-form]' ? { dataset: { wsForm: kind } } : null }, preventDefault() {}, stopPropagation() {} });
  const open = async () => { ui.show(); await flush(); };
  return { ui, host, state, calls, focused, click, input, submit, open, doc, events, setRoute(value) { hash = value; } };
}
const fill = (f, values) => { for (const [name, value] of Object.entries(values)) f.input(name, value); };

const cases = {
  async pure() {
    assert.equal(ws.gib(4096), '4 GiB'); assert.equal(ws.gib(512), '512 MiB'); assert.equal(ws.gib(1536), '1.5 GiB');
    assert.equal(ws.resourcePreview(2, 4096, 20, 'Fixture machine'), '2 CPU, 4 GiB memory, 20 GiB storage on Fixture machine');
    assert.equal(ws.resourcePreview(2, 4096, 20, null), '2 CPU, 4 GiB memory, 20 GiB storage. No machine chosen yet');
    const digest = 'sha256:' + 'a'.repeat(64);
    for (const ok of ['ubuntu', 'ubuntu:24.04', 'ghcr.io/acme/tools@' + digest, 'registry.example.com:5000/team/app:v1', 'localhost:5000/app']) assert.equal(ws.parseImage(ok).reason, undefined, ok);
    assert.equal(ws.parseImage('ghcr.io/acme/tools@' + digest).pinned, true);
    assert.equal(ws.parseImage('ubuntu:24.04').pinned, false);
    const bad = { '': 'empty', ['a'.repeat(256)]: 'too_long', 'ubuntu; rm -rf /': 'characters', 'ubuntu$(id)': 'characters', '--privileged': 'characters', '-v/:/host': 'characters', '/etc/passwd': 'characters', 'https://x.example/a': 'scheme', 'ubuntu@sha256:abc': 'digest', Ubuntu: 'name', 'ubuntu:': 'tag', 'host.example:99999/app': 'registry', 'ubuntu image': 'characters', 'ubuntu\nid': 'characters' };
    for (const [value, reason] of Object.entries(bad)) assert.equal(ws.parseImage(value).reason, reason, JSON.stringify(value));
    const base = { ...ws.blankValues(options), ws_name: 'Build box' };
    assert.deepEqual(ws.validate(base, options), {});
    const limits = [['ws_cpu_cores', '0', /between 0\.5 and 32/], ['ws_cpu_cores', '1.3', /steps of 0\.5/], ['ws_cpu_cores', '', /number of CPU/], ['ws_memory_gib', '0.25', /between 0\.5 and 128 GiB/], ['ws_memory_gib', '129', /between 0\.5 and 128 GiB/], ['ws_memory_gib', 'x', /amount of memory/], ['ws_storage_gib', '0', /between 1 and 2048/], ['ws_storage_gib', '2049', /between 1 and 2048/], ['ws_storage_gib', '1.5', /whole number/], ['ws_name', '', /1 to 60/], ['ws_name', ' x', /plain text/], ['ws_network', 'host', /network/]];
    for (const [field, value, pattern] of limits) assert.match(Object.values(ws.validate({ ...base, [field]: value }, options))[0] || '', pattern, `${field}=${value}`);
    assert.match(ws.validate({ ...base, ws_image_source: 'custom', ws_image: 'bad image' }, options).image, /Use only lowercase/);
    assert.deepEqual(ws.workspacePayload({ ...base, ws_name: 'Build box', ws_image_source: 'custom', ws_image: ' ghcr.io/acme/tools:1.2 ', ws_node_id: 'n-one' }), { name: 'Build box', image_source: 'custom', image: 'ghcr.io/acme/tools:1.2', cpu_cores: 2, memory_mib: 4096, storage_gib: 20, network: 'outbound', node_id: 'n-one', private_registry: false });
    assert.equal(ws.workspacePayload(base, { revision: 3 }).expected_revision, 3);
    assert.equal(ws.workspacePayload({ ...base, ws_image_source: 'curated', ws_private_registry: true }).private_registry, false, 'a curated image is never private');
  },

  async states() {
    const rows = [
      record({ id: 'a', name: 'A none' }),
      record({ id: 'b', name: 'B cred', status: 'needs_credential', private_registry: true, node_id: 'n-one', machine_name: 'Fixture machine' }),
      record({ id: 'c', name: 'C pending', status: 'pending', node_id: 'n-one', machine_name: 'Fixture machine' }),
      record({ id: 'd', name: 'D ready', status: 'ready', started: true, applied_revision: 1, node_id: 'n-one', machine_name: 'Fixture machine' }),
      record({ id: 'e', name: 'E restart', status: 'ready', started: true, revision: 2, applied_revision: 1, restart_needed: true, node_id: 'n-one', machine_name: 'Fixture machine' }),
      record({ id: 'f', name: 'F oom', status: 'failed', status_reason: 'out_of_memory', started: true, applied_revision: 1, memory_mib: 4096, node_id: 'n-one', machine_name: 'Fixture machine' }),
      record({ id: 'g', name: 'G odd', status: 'failed', status_reason: null, node_id: 'n-one', machine_name: 'Fixture machine' }),
      record({ id: 'h', name: 'H pinned', image_pinned: true, image_digest: 'sha256:' + 'b'.repeat(64) }),
      record({ id: 'i', name: 'I curated', image_source: 'curated', image: 'docker.io/library/ubuntu:24.04' }),
    ];
    const f = fixture({ list: { workspaces: rows, can_manage: true } });
    // Loading first: the section never renders empty.
    f.ui.show(); assert.match(text(f.host.innerHTML), /Loading Compute inventory/); await flush();
    const t = text(f.host.innerHTML);
    for (const phrase of ['No machine yet', 'Choose a machine to run it on. Nothing is started.', 'No credential bound yet', 'Not started yet', 'Starting cloud workspaces is not available yet, so nothing runs.', 'Ready', 'Restart needed to apply the new settings.', 'ran out of memory at 4 GiB', 'Failed: reason not recognised', 'Pinned to digest sha256:bbbbbbbbbbbb', 'Not pinned: the tag can change', 'Curated image', '2 CPU, 4 GiB memory, 20 GiB storage on Fixture machine', '2 CPU, 4 GiB memory, 20 GiB storage. No machine chosen yet']) assert.ok(t.includes(phrase), phrase);
    assert.equal((f.host.innerHTML.match(/data-ws-action="edit"/g) || []).length, 9);
    assert.equal((f.host.innerHTML.match(/data-ws-action="bind"/g) || []).length, 1);
    assert.doesNotMatch(f.host.innerHTML, /orchestrator|style="/i);
    f.ui.stop();
    // Empty states explain themselves, for an owner and for a member.
    const empty = fixture(); await empty.open();
    assert.match(text(empty.host.innerHTML), /No cloud workspaces yet\. A cloud workspace is an isolated place where Coworkers run/);
    assert.match(empty.host.innerHTML, /data-ws-action="create"/); empty.ui.stop();
  },

  async escaping() {
    const attack = '<img src=x onerror="alert(1)"> & <script>bad()</script>';
    const f = fixture({ list: { workspaces: [record({ name: attack, image: attack, machine_name: attack, node_id: 'n-one', status: 'pending' })], can_manage: true } });
    await f.open(); f.click('detail', 'w-one');
    assert.doesNotMatch(f.host.innerHTML, /<img|<script>/);
    assert.match(f.host.innerHTML, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt; &amp;/);
    f.click('close'); f.click('edit', 'w-one');
    assert.doesNotMatch(f.host.innerHTML, /<img|<script>/);
    f.ui.stop();
  },

  async create() {
    const created = record({ id: 'w-new', name: 'Acceptance', node_id: 'n-one', machine_name: 'Fixture machine', status: 'pending', created_at: clock + 1 });
    const f = fixture(); await f.open();
    f.click('create');
    let t = text(f.host.innerHTML);
    assert.match(f.host.innerHTML, /data-ws-form="create"/);
    assert.ok(t.includes('New cloud workspace') && t.includes('Nothing starts when you save') && t.includes('What it will use'));
    assert.ok(t.includes('2 CPU, 4 GiB memory, 20 GiB storage. No machine chosen yet'), 'defaults are previewed');
    assert.match(f.host.innerHTML, /<option value="docker\.io\/library\/ubuntu:24\.04" selected>/);
    assert.match(f.host.innerHTML, /<option value="n-one">Fixture machine \(pending verification\)<\/option>/);
    assert.match(f.host.innerHTML, /aria-describedby="ws-hint-cpu_cores ws-error-cpu_cores"/);
    // Review is refused until every field is valid, with each reason beside its field.
    fill(f, { ws_name: '' }); f.submit('create');
    assert.match(text(f.host.innerHTML), /Give it a name of 1 to 60 characters\./); assert.match(f.host.innerHTML, /aria-invalid="true"/);
    assert.equal(f.state.writes.length, 0);
    // Own image: custom reference, injection attempt first.
    f.input('ws_image_choice', 'custom');
    assert.match(f.host.innerHTML, /name="ws_image"/); assert.match(f.host.innerHTML, /name="ws_private_registry"/);
    fill(f, { ws_name: 'Acceptance', ws_image: 'ubuntu; rm -rf /' }); f.submit('create');
    assert.match(text(f.host.innerHTML), /Use only lowercase letters, digits/);
    fill(f, { ws_image: 'ghcr.io/acme/tools:1.2', ws_cpu_cores: '2', ws_memory_gib: '4', ws_storage_gib: '20', ws_node_id: 'n-one' });
    f.submit('create');
    t = text(f.host.innerHTML);
    assert.ok(t.includes('Create Acceptance?') && t.includes('2 CPU, 4 GiB memory, 20 GiB storage on Fixture machine'), 'resource preview before confirm');
    assert.ok(t.includes('not pinned: the tag can change') && t.includes('Nothing starts yet'));
    assert.equal(f.state.writes.length, 0, 'nothing is sent before confirm');
    f.click('back'); assert.match(f.host.innerHTML, /data-ws-form="create"/); assert.match(f.host.innerHTML, /value="Acceptance"/);
    f.submit('create'); f.state.next = { workspace: created }; f.click('confirm'); await flush();
    assert.deepEqual(f.state.writes, [['/api/compute/workspaces', { name: 'Acceptance', image_source: 'custom', image: 'ghcr.io/acme/tools:1.2', cpu_cores: 2, memory_mib: 4096, storage_gib: 20, network: 'outbound', node_id: 'n-one', private_registry: false }]]);
    t = text(f.host.innerHTML);
    assert.ok(t.includes('Created Acceptance. Not started yet'), 'notice names the honest status');
    assert.match(f.host.innerHTML, /data-ws-id="w-new"/); assert.doesNotMatch(f.host.innerHTML, /data-ws-form/);
    assert.ok(f.focused.includes('#compute-workspaces'));
    f.ui.stop();
  },

  async 'server-errors'() {
    const f = fixture(); await f.open(); f.click('create');
    fill(f, { ws_name: 'Box' }); f.submit('create');
    f.state.next = failure(400, { error: 'invalid_request', field: 'image', reason: 'characters' }); f.click('confirm'); await flush();
    assert.match(f.host.innerHTML, /data-ws-form="create"/, 'back on the form');
    assert.match(text(f.host.innerHTML), /Use only lowercase letters, digits/); assert.match(text(f.host.innerHTML), /Fix the field below/);
    // A taken name is shown beside the name.
    f.submit('create'); f.state.next = failure(409, { error: 'conflict', field: 'name', reason: 'name_taken' }); f.click('confirm'); await flush();
    assert.match(text(f.host.innerHTML), /Another cloud workspace already uses this name\./);
    // No answer: nothing is claimed.
    f.submit('create'); f.state.next = new Error('timeout'); f.click('confirm'); await flush();
    assert.match(text(f.host.innerHTML), /nothing is confirmed/);
    // Owner changed meanwhile.
    f.submit('create'); f.state.next = failure(403, { error: 'owner_required' }); f.click('confirm'); await flush();
    assert.match(text(f.host.innerHTML), /Only the workspace owner can change cloud workspaces\./);
    f.ui.stop();
  },

  async edit() {
    const row = record({ id: 'w-one', name: 'Build box', node_id: 'n-one', machine_name: 'Fixture machine', status: 'ready', started: true, applied_revision: 1, revision: 1 });
    const f = fixture({ list: { workspaces: [row], can_manage: true } }); await f.open();
    f.click('edit', 'w-one');
    let t = text(f.host.innerHTML);
    assert.ok(t.includes('Change Build box') && t.includes('has been started. A change to the image, limits or network needs a restart to apply'));
    assert.match(f.host.innerHTML, /name="ws_node_id" disabled/, 'a started workspace cannot move');
    assert.match(f.host.innerHTML, /value="2"/); assert.match(f.host.innerHTML, /value="4"/);
    fill(f, { ws_memory_gib: '8' }); f.submit('edit');
    t = text(f.host.innerHTML);
    assert.ok(t.includes('Confirm the change to Build box') && t.includes('2 CPU, 8 GiB memory, 20 GiB storage on Fixture machine') && t.includes('keeps its old settings until it is restarted'));
    f.state.next = { workspace: { ...row, memory_mib: 8192, revision: 2, restart_needed: true } }; f.click('confirm'); await flush();
    assert.equal(f.state.writes[0][0], '/api/compute/workspaces/w-one'); assert.equal(f.state.writes[0][1].expected_revision, 1); assert.equal(f.state.writes[0][1].memory_mib, 8192);
    t = text(f.host.innerHTML);
    assert.ok(t.includes('Saved Build box. Restart needed to apply the new settings.') && t.includes('8 GiB memory'));
    // A stale revision tells the person to reload.
    f.click('edit', 'w-one'); fill(f, { ws_memory_gib: '6' }); f.submit('edit');
    f.state.next = failure(409, { error: 'conflict', reason: 'stale_revision' }); f.click('confirm'); await flush();
    assert.match(text(f.host.innerHTML), /Someone changed this workspace in the meantime/);
    f.ui.stop();
    // Never started: the editor says changes apply when it starts.
    const fresh = fixture({ list: { workspaces: [record()], can_manage: true } }); await fresh.open(); fresh.click('edit', 'w-one');
    assert.match(text(fresh.host.innerHTML), /Not started yet, so changes apply when it starts\./); assert.doesNotMatch(fresh.host.innerHTML, /name="ws_node_id" disabled/);
    fresh.ui.stop();
  },

  async bind() {
    const row = record({ private_registry: true, status: 'needs_credential', node_id: 'n-one', machine_name: 'Fixture machine' });
    const f = fixture({ list: { workspaces: [row], can_manage: true } }); await f.open();
    f.click('bind', 'w-one');
    assert.match(text(f.host.innerHTML), /No password, token or key is typed here/); assert.doesNotMatch(f.host.innerHTML, /type="password"/);
    fill(f, { ws_credential_id: 'bad id!' }); f.submit('bind');
    assert.match(text(f.host.innerHTML), /reference name of a stored credential/); assert.equal(f.state.writes.length, 0);
    fill(f, { ws_credential_id: 'registry-login', ws_credential_version: '2' }); f.state.next = { workspace: { ...row, status: 'pending', credential_bound: true, registry_credential_id: 'registry-login', registry_credential_version: 2 } }; f.submit('bind'); await flush();
    assert.deepEqual(f.state.writes, [['/api/compute/workspaces/w-one/credential', { credential_id: 'registry-login', credential_version: 2 }]]);
    await flush(); assert.match(text(f.host.innerHTML), /Registry login bound to Build box/);
    f.ui.stop();
  },

  async member() {
    const row = record({ node_id: 'n-one', machine_name: 'Fixture machine', status: 'pending', private_registry: true, credential_bound: true });
    delete row.registry_credential_id; delete row.registry_credential_version;
    const f = fixture({ owner: false, list: { workspaces: [row], can_manage: false } }); await f.open();
    const t = text(f.host.innerHTML);
    assert.ok(t.includes('Compute management requires the current workspace owner. You can still look at the cloud workspaces below.'));
    assert.ok(t.includes('Build box') && t.includes('Not started yet'));
    assert.doesNotMatch(f.host.innerHTML, /data-ws-action="(create|edit|bind)"|role="alert">Compute management/);
    assert.match(f.host.innerHTML, /data-ws-action="detail"/);
    f.click('create'); f.click('edit', 'w-one'); assert.doesNotMatch(f.host.innerHTML, /data-ws-form/, 'a member can never open a form');
    f.click('detail', 'w-one'); const d = text(f.host.innerHTML);
    assert.ok(d.includes('Only the workspace owner can change cloud workspaces.') && d.includes('Bound') && !d.includes('registry-login'));
    assert.doesNotMatch(f.host.innerHTML, /data-ws-action="edit"/);
    // An empty list says who can create one.
    const none = fixture({ owner: false, list: { workspaces: [], can_manage: false } }); await none.open();
    assert.match(text(none.host.innerHTML), /The workspace owner can create one here\./);
    f.ui.stop(); none.ui.stop();
  },

  async failures() {
    const down = fixture({ list: failure(500, { error: 'x' }) }); await down.open();
    assert.match(text(down.host.innerHTML), /Cloud workspaces could not be loaded\. Reload to try again\./); assert.match(down.host.innerHTML, /data-ws-action="reload"/);
    assert.doesNotMatch(down.host.innerHTML, /data-ws-action="create"/);
    down.state.list = { workspaces: [], can_manage: true }; down.click('reload'); await flush();
    assert.match(text(down.host.innerHTML), /No cloud workspaces yet/);
    down.ui.stop();
    // The image list failing says why creation is unavailable instead of hiding the button silently.
    const noOptions = fixture({ config: failure(500, { error: 'x' }) }); await noOptions.open();
    assert.match(text(noOptions.host.innerHTML), /The image list could not be loaded, so new workspaces cannot be created right now/);
    assert.match(noOptions.host.innerHTML, /data-ws-action="create" disabled/); noOptions.click('create'); assert.doesNotMatch(noOptions.host.innerHTML, /data-ws-form/);
    noOptions.ui.stop();
    // Leaving the route clears everything; a late answer publishes nothing.
    const gone = fixture(); gone.ui.show(); gone.setRoute('#/board/demo'); gone.events.dispatchEvent(new Event('hashchange')); await flush();
    assert.equal(gone.host.innerHTML, '');
  },
};

const selection = process.argv[2];
assert.ok(cases[selection], `Unknown workspace UI case: ${selection}`);
await cases[selection](); console.log(`Compute workspaces UI: ${selection} passed`);
