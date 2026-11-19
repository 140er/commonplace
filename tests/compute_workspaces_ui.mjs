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
    { id: 'ubuntu-24-04', label: 'Ubuntu 24.04', reference: 'docker.io/library/ubuntu:24.04', description: 'A general-purpose Linux system.' },
    { id: 'python-3-12-slim', label: 'Python 3.12 (slim)', reference: 'docker.io/library/python:3.12-slim', description: 'Linux with Python 3.12 ready to use.' },
  ],
  limits: { cpu_cores: { min: 0.5, max: 32, step: 0.5, default: 2 }, memory_mib: { min: 512, max: 131072, step: 128, default: 4096 }, storage_gib: { min: 1, max: 2048, step: 1, default: 20 } },
  networks: ['outbound', 'none'], default_network: 'outbound', name_limit: 60, max_workspaces: 100, registry_logins: false,
};
const machine = { id: 'n-one', name: 'Fixture machine', state: 'pending_verification', gateway_id: 'g-one', board: 'demo' };
const record = (extra = {}) => ({
  id: 'w-one', name: 'Build box', image_source: 'custom', image: 'ghcr.io/acme/tools:1.2', image_registry: 'ghcr.io', image_tag: '1.2', image_digest: null, image_pinned: false, curated_label: null,
  private_registry: false, credential_bound: false, cpu_cores: 2, memory_mib: 4096, storage_gib: 20, network: 'outbound',
  node_id: null, machine_name: null, machine_assigned: false, status: 'no_machine', status_reason: null, started: false, version: 1, revision: 1, applied_revision: null, restart_needed: false, discardable: true, created_at: clock, updated_at: clock, ...extra,
});
const onMachine = (extra = {}) => record({ node_id: 'n-one', machine_name: 'Fixture machine', machine_assigned: true, status: 'pending', ...extra });
const startedRow = (extra = {}) => onMachine({ status: 'ready', started: true, applied_revision: 1, discardable: false, ...extra });
const inventory = () => ({ gateways: [], nodes: [machine], scopes: [], executions: [], can_manage: true, capabilities: { transport: false, passive_discovery: false, scripts: false } });

function fixture({ list = { workspaces: [], can_manage: true }, config = options, owner = true } = {}) {
  let html = '', hash = '#/compute';
  const user = { workspace: 'w', actor: 'user:u', can_admin: true };
  const listeners = new Map(), calls = [], doc = new EventTarget(), events = new EventTarget(), focused = [], status = { text: '' };
  // The roots stay null so every repaint goes through the full render; other lookups record what got focus.
  const stub = path => ({ focus() { focused.push(path); }, setAttribute() {}, removeAttribute() {}, set textContent(value) { if (path === '[data-ws-notice]') status.text = value; }, querySelector: child => stub(`${path} ${child}`) });
  const host = {
    get innerHTML() { return html; }, set innerHTML(value) { html = value; },
    addEventListener(kind, fn) { listeners.set(kind, fn); }, removeEventListener(kind) { listeners.delete(kind); },
    querySelector(selector) { return selector === '[data-ws-root]' || selector === '[data-ws-top]' ? null : stub(selector); },
  };
  const state = { list, config, writes: [], next: [] };
  const api = (path, request = {}) => {
    calls.push([request.method || 'GET', path, request.body]);
    if (request.method === 'POST') { state.writes.push([path, request.body]); const out = state.next.length ? state.next.shift() : null; return out instanceof Error ? Promise.reject(out) : Promise.resolve(structuredClone(out)); }
    const answer = path === '/api/compute/workspaces' ? state.list : path === '/api/compute/workspaces/images' ? state.config : path === '/api/compute' ? (owner ? inventory() : failure(403, { error: 'owner_required' })) : path === '/api/boards' ? { boards: [] } : path === '/api/compute/discoveries' ? { discoveries: [], capabilities: {} } : path === '/api/compute/scripts' ? { scripts: [] } : path === '/api/compute/credentials' ? { credentials: [], status: 'ready' } : null;
    return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(structuredClone(answer));
  };
  let counter = 0;
  const ui = createCompute({ mount: () => host, api, current: () => user, route: () => hash, doc, events, now: () => clock, requestId: () => `r-${++counter}` });
  const dispatch = (kind, event) => listeners.get(kind)?.(event);
  const click = (action, id) => dispatch('click', { target: { closest: selector => selector === '[data-ws-action]' ? { dataset: { wsAction: action, id } } : null } });
  const input = (name, value, type = 'text') => dispatch('input', { target: { name, type, value, checked: value } });
  const submit = kind => dispatch('submit', { target: { closest: selector => selector === '[data-ws-form]' ? { dataset: { wsForm: kind } } : null }, preventDefault() {}, stopPropagation() {} });
  const key = (name, inside = true) => { const event = { key: name, target: { closest: selector => inside && selector === '.compute-ws' ? {} : null }, prevented: false, preventDefault() { this.prevented = true; } }; dispatch('keydown', event); return event; };
  const open = async () => { ui.show(); await flush(); };
  return { ui, host, state, calls, focused, status, click, input, submit, key, open, doc, events, dispatch, setRoute(value) { hash = value; } };
}
const fill = (f, values) => { for (const [name, value] of Object.entries(values)) f.input(name, value); };
const valid = { ws_name: 'Acceptance', ws_image: 'ghcr.io/acme/tools:1.2', ws_cpu_cores: '2', ws_memory_gib: '4', ws_storage_gib: '20' };
const section = html => html.slice(html.indexOf('compute-ws"'), html.indexOf('<details class="compute-advanced'));
const alerts = html => (html.match(/role="alert"/g) || []).length;

const cases = {
  async pure() {
    assert.equal(ws.gib(4096), '4 GiB'); assert.equal(ws.gib(512), '0.5 GiB'); assert.equal(ws.gib(1536), '1.5 GiB'); assert.equal(ws.gib(4608), '4.5 GiB', 'GiB everywhere, never MiB');
    assert.equal(ws.resourcePreview(2, 4096, 20, 'Fixture machine'), '2 CPU cores, 4 GiB memory, 20 GiB storage on Fixture machine');
    assert.equal(ws.resourcePreview(1, 4096, 20, null), '1 CPU core, 4 GiB memory, 20 GiB storage. No machine chosen yet');
    const digest = 'sha256:' + 'a'.repeat(64);
    for (const ok of ['ubuntu', 'ubuntu:24.04', 'ghcr.io/acme/tools@' + digest, 'registry.example.com:5000/team/app:v1']) assert.equal(ws.parseImage(ok).reason, undefined, ok);
    assert.equal(ws.parseImage('ghcr.io/acme/tools@' + digest).pinned, true);
    assert.equal(ws.parseImage('ubuntu:24.04').pinned, false);
    const bad = { '': 'empty', ['a'.repeat(256)]: 'too_long', 'ubuntu; rm -rf /': 'characters', 'ubuntu$(id)': 'characters', '--privileged': 'characters', '-v/:/host': 'characters', '/etc/passwd': 'characters', 'https://x.example/a': 'scheme', 'ubuntu@sha256:abc': 'digest', Ubuntu: 'name', 'ubuntu:': 'tag', 'host.example:99999/app': 'registry', 'ubuntu image': 'characters', 'ubuntu\nid': 'characters',
      'localhost/app': 'registry_private', 'localhost:5000/app:latest': 'registry_private', '127.0.0.1:5000/app': 'registry_private', '169.254.169.254/app:1': 'registry_private', '10.0.0.5/team/app': 'registry_private', '192.168.1.10:5000/app': 'registry_private', '172.16.0.1/app': 'registry_private', '0x7f.0.0.1/app': 'registry_private', 'registry:5000/app': 'registry_private', 'registry.local/app': 'registry_private', 'build.internal:5000/app': 'registry_private', 'foo.localhost/app': 'registry_private',
      'ghcr.io:443': 'registry_no_path', 'ghcr.io': 'registry_no_path', 'registry.example.com:5000': 'registry_no_path' };
    for (const [value, reason] of Object.entries(bad)) assert.equal(ws.parseImage(value).reason, reason, JSON.stringify(value));
    const base = { ...ws.blankValues(options), ws_name: 'Build box' };
    assert.deepEqual(ws.validate(base, options), {});
    const limits = [['ws_cpu_cores', '0', /from 0\.5 to 32/], ['ws_cpu_cores', '1.3', /steps of 0\.5/], ['ws_cpu_cores', '', /number of cores/], ['ws_memory_gib', '0.25', /from 0\.5 to 128 GiB/], ['ws_memory_gib', '129', /from 0\.5 to 128 GiB/], ['ws_memory_gib', 'x', /amount of memory/], ['ws_storage_gib', '0', /from 1 to 2048/], ['ws_storage_gib', '2049', /from 1 to 2048/], ['ws_storage_gib', '1.5', /whole number/], ['ws_name', '', /1 to 60/], ['ws_name', ' x', /visible characters/], ['ws_network', 'host', /network/]];
    for (const [field, value, pattern] of limits) assert.match(Object.values(ws.validate({ ...base, [field]: value }, options))[0] || '', pattern, `${field}=${value}`);
    assert.match(ws.validate({ ...base, ws_image_source: 'custom', ws_image: 'bad image' }, options).image, /doesn't look like an image address/);
    assert.deepEqual(ws.workspacePayload({ ...base, ws_name: 'Build box', ws_image_source: 'custom', ws_image: ' ghcr.io/acme/tools:1.2 ', ws_node_id: 'n-one' }), { name: 'Build box', image_source: 'custom', image: 'ghcr.io/acme/tools:1.2', cpu_cores: 2, memory_mib: 4096, storage_gib: 20, network: 'outbound', node_id: 'n-one' });
    assert.equal(ws.createPayload(base, 'r-9').request_id, 'r-9'); assert.equal('request_id' in ws.createPayload(base), false);
    const before = { ...base };
    assert.equal(ws.editPayload(base, before, 3), null, 'nothing changed means nothing to send');
    assert.deepEqual(ws.editPayload({ ...base, ws_memory_gib: '8' }, before, 3), { memory_mib: 8192, expected_revision: 3 }, 'only the changed field and the revision it opened at');
    assert.deepEqual(ws.editPayload({ ...base, ws_image_source: 'custom', ws_image: 'ghcr.io/a/b:1' }, before, 1), { image_source: 'custom', image: 'ghcr.io/a/b:1', expected_revision: 1 }, 'source and image travel together');
  },

  async names() {
    const base = { ...ws.blankValues(options), ws_name: 'Build box' };
    const name = value => ws.validate({ ...base, ws_name: value }, options).name || '';
    for (const hidden of ['​', '​​', 'a​b', '‮evil', 'abc‬', '⁦x⁩', 'x‎y', '﻿name', 'a b', '\ud800broken', '\u{e0041}tag']) assert.match(name(hidden), /visible characters/, JSON.stringify(hidden));
    for (const blank of ['⠀⠀', '---', '!!! ???']) assert.match(name(blank), /at least one letter or digit/, JSON.stringify(blank));
    for (const empty of ['', '   ']) assert.match(name(empty), /1 to 60/);
    assert.match(name('é'.repeat(61)), /1 to 60/); assert.equal(name('é'.repeat(60)), '');
    // Code points, not UTF-16 units: 31 code points are 61 units and still fit, as on the server.
    assert.equal(name('a' + '\u{1f600}'.repeat(30)), ''); assert.equal(name('東京 7'), '');
    assert.equal(name('Café'), '', 'a combining accent normalises, so it counts as one code point');
    assert.equal(ws.workspacePayload({ ...base, ws_name: 'Café' }).name, 'Café', 'the name is sent normalised');
  },

  async states() {
    const rows = [
      record({ id: 'a', name: 'A none' }),
      record({ id: 'b', name: 'B cred', status: 'needs_credential', private_registry: true, node_id: 'n-one', machine_name: 'Fixture machine', machine_assigned: true, discardable: false }),
      onMachine({ id: 'c', name: 'C pending' }),
      startedRow({ id: 'd', name: 'D ready' }),
      startedRow({ id: 'e', name: 'E restart', status: 'restart_needed', version: 2, revision: 2, restart_needed: true }),
      startedRow({ id: 'f', name: 'F oom', status: 'failed', status_reason: 'out_of_memory' }),
      startedRow({ id: 'g', name: 'G odd', status: 'failed', status_reason: null }),
      record({ id: 'h', name: 'H pinned', image_pinned: true, image_digest: 'sha256:' + 'b'.repeat(64) }),
      record({ id: 'i', name: 'I curated', image_source: 'curated', image: 'docker.io/library/ubuntu:24.04' }),
    ];
    const f = fixture({ list: { workspaces: rows, can_manage: true } });
    // Loading first: the section never renders empty.
    f.ui.show(); assert.match(text(f.host.innerHTML), /Loading Compute inventory/); await flush();
    const t = text(f.host.innerHTML);
    for (const phrase of ['No machine chosen', 'Needs a registry login', 'Not started yet', 'Restart needed', 'Ready', 'Failed', '2 CPU cores, 4 GiB memory, 20 GiB storage on Fixture machine', '2 CPU cores, 4 GiB memory, 20 GiB storage. No machine chosen yet']) assert.ok(t.includes(phrase), phrase);
    // Rows are name, status and one resource line; the explanation lives in the detail.
    assert.equal((f.host.innerHTML.match(/<h3 class="compute-ws-name">/g) || []).length, 9, 'every workspace name is a heading');
    assert.doesNotMatch(f.host.innerHTML, /Nothing has been sent|ghcr\.io\/acme|publisher can update/);
    assert.equal((f.host.innerHTML.match(/data-ws-action="edit"/g) || []).length, 9);
    assert.equal((f.host.innerHTML.match(/data-ws-action="discard"/g) || []).length, 4, 'only records that never started can be discarded');
    assert.match(f.host.innerHTML, /aria-label="Details for A none"/); assert.match(f.host.innerHTML, /aria-label="Change settings for E restart"/); assert.match(f.host.innerHTML, /aria-label="Discard C pending"/);
    assert.doesNotMatch(f.host.innerHTML, /data-ws-action="bind"|orchestrator|style="/i);
    assert.ok(alerts(section(f.host.innerHTML)) <= 1);
    // A restart is its own status: "matches the saved settings" never sits beside "Restart needed".
    const detailOf = id => { f.click('detail', id); const html = f.host.innerHTML, at = html.indexOf('compute-ws-detail'), out = text(html.slice(at, html.indexOf('</section>', at))); f.click('close'); return out; };
    for (const row of rows) {
      const shown = detailOf(row.id);
      assert.doesNotMatch(shown, /matches the saved settings/, row.name);
      if (row.status === 'restart_needed') { assert.match(shown, /Restart needed\./); assert.doesNotMatch(shown, /Ready|running with the saved settings/); }
    }
    assert.match(detailOf('f'), /ran out of memory at 4 GiB/); assert.match(detailOf('g'), /Failed: reason not recognised/);
    assert.match(detailOf('d'), /It is running with the saved settings/);
    assert.match(detailOf('h'), /Locked to one exact version/); assert.match(detailOf('i'), /Official image\. The publisher can update its version over time/); assert.match(detailOf('a'), /The publisher can update this version over time/);
    assert.match(detailOf('c'), /Saved in Commonplace\. Nothing has been sent to Fixture machine yet\. It will run there once starting is available\./);
    assert.match(detailOf('d'), /can't be deleted yet/); assert.match(detailOf('c'), /discard it because it was never started/);
    assert.match(detailOf('e'), /Changed 1 time since it was created/); assert.match(detailOf('a'), /Not changed since it was created/);
    f.ui.stop();
    // Empty states say who can act, once, and disappear while a form is open or any workspace exists.
    const empty = fixture(); await empty.open();
    assert.match(text(empty.host.innerHTML), /No cloud workspaces yet\. Choose New cloud workspace to create one\./);
    assert.equal((text(empty.host.innerHTML).match(/isolated environment/g) || []).length, 1, 'the definition appears once');
    empty.click('create'); assert.doesNotMatch(text(empty.host.innerHTML), /No cloud workspaces yet/, 'no stale empty sentence under an open form');
    empty.ui.stop();
  },

  async 'status-copy'() {
    // Slice 1 records settings only: nothing may say a limit is enforced or a workspace runs, except as "once it can start".
    const f = fixture({ list: { workspaces: [onMachine()], can_manage: true } }); await f.open();
    f.click('create');
    const all = text(f.host.innerHTML);
    for (const promise of ['Internet access out', 'kept in a volume', 'is stopped if it goes over', 'saved on', 'stopped if it goes over.']) assert.ok(!all.includes(promise), promise);
    assert.ok(all.includes('Once workspaces can start, it will be slowed down at this limit, not stopped.'));
    assert.ok(all.includes('Once workspaces can start, it will be stopped if it goes over this.'));
    assert.ok(all.includes('Once workspaces can start, writes will fail when it is full.'));
    assert.ok(all.includes('Applied when the workspace starts.'));
    assert.ok(all.includes('Saved in Commonplace. Nothing is sent to a machine yet.'));
    f.ui.stop();
  },

  async escaping() {
    const attack = '<img src=x onerror="alert(1)"> & <script>bad()</script>';
    const f = fixture({ list: { workspaces: [onMachine({ name: attack, image: attack, machine_name: attack })], can_manage: true } });
    await f.open(); f.click('detail', 'w-one');
    assert.doesNotMatch(f.host.innerHTML, /<img|<script>/);
    assert.match(f.host.innerHTML, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt; &amp;/);
    f.click('close'); assert.doesNotMatch(f.host.innerHTML, /<img|<script>|aria-label="[^"]*"[^>]*onerror/); f.click('edit', 'w-one');
    assert.doesNotMatch(f.host.innerHTML, /<img|<script>/);
    f.ui.stop();
  },

  async create() {
    const created = onMachine({ id: 'w-new', name: 'Acceptance', created_at: clock + 1 });
    const f = fixture(); await f.open();
    f.click('create');
    let t = text(f.host.innerHTML);
    assert.match(f.host.innerHTML, /data-ws-form="create"/);
    assert.ok(t.includes('New cloud workspace') && t.includes('Fields marked * are required') && t.includes('What it will use') && t.includes('Sizes are in GiB (about 1.07 GB)'));
    assert.ok(t.includes('2 CPU cores, 4 GiB memory, 20 GiB storage. No machine chosen yet'), 'defaults are previewed');
    assert.ok(t.includes('Default 2.') && t.includes('Default 4.') && t.includes('Default 20.'), 'defaults are labelled');
    assert.match(f.host.innerHTML, /<option value="docker\.io\/library\/ubuntu:24\.04" selected>/);
    assert.match(f.host.innerHTML, /<option value="n-one">Fixture machine \(not checked yet\)<\/option>/);
    // Accessible structure: the label closes before the hint and the error; required is marked; one summary alert.
    assert.match(f.host.innerHTML, /<label for="ws-field-name">Name <span class="compute-req" aria-hidden="true">\*<\/span><\/label><input name="ws_name" id="ws-field-name" data-ws-control="name" aria-describedby="ws-hint-name ws-error-name" required/);
    assert.match(f.host.innerHTML, /aria-describedby="ws-hint-cpu_cores ws-error-cpu_cores"/);
    assert.equal(alerts(section(f.host.innerHTML)), 1, 'one summary alert, none on the individual errors');
    assert.doesNotMatch(f.host.innerHTML, /data-ws-preview[^>]*(role|aria-live)|data-ws-error="[a-z_]+"[^>]*role/);
    assert.doesNotMatch(section(f.host.innerHTML), /ws_private_registry|credential|type="password"|maxlength/);
    assert.ok(f.focused.includes('#compute-ws-heading'), 'focus moves to the form heading');
    // A blank name: review is refused, the summary says so and only the name control is invalid.
    f.submit('create');
    t = text(f.host.innerHTML);
    assert.match(t, /Fix the highlighted fields, then review again\./); assert.match(t, /Give it a name of 1 to 60 characters, for example Build box\./);
    assert.equal((f.host.innerHTML.match(/aria-invalid="true"/g) || []).length, 1); assert.match(f.host.innerHTML, /data-ws-control="name" aria-describedby="ws-hint-name ws-error-name" aria-invalid="true"/);
    assert.ok(f.focused.includes('[data-ws-control="name"]'), 'the first invalid field gets focus');
    assert.equal(f.state.writes.length, 0);
    // Own image: a real example and a plain definition of "image".
    f.input('ws_image_choice', 'custom');
    assert.match(f.host.innerHTML, /name="ws_image"[^>]*placeholder="ghcr\.io\/acme\/tools:1\.2"/);
    t = text(f.host.innerHTML);
    assert.ok(t.includes('An image is the ready-made software a workspace starts from.') && t.includes('for example ghcr.io/acme/tools:1.2. Only public images work for now.'));
    assert.doesNotMatch(t, /@sha256:…|Pin it/);
    // The image control is the one that carries the image error, not the select.
    fill(f, { ws_name: 'Acceptance', ws_image: 'ubuntu; rm -rf /' }); f.submit('create');
    assert.match(text(f.host.innerHTML), /doesn't look like an image address/);
    assert.match(f.host.innerHTML, /name="ws_image" id="ws-field-image" data-ws-control="image" aria-describedby="ws-hint-image ws-error-image" aria-invalid="true"/);
    assert.doesNotMatch(f.host.innerHTML, /name="ws_image_choice"[^>]*aria-invalid/);
    // Out-of-range limits are never previewed as the plan, and the preview never switches unit.
    fill(f, { ws_image: 'ghcr.io/acme/tools:1.2', ws_cpu_cores: '64', ws_memory_gib: '0.1' }); f.submit('create');
    t = text(f.host.innerHTML);
    assert.match(t, /Enter a number of cores from 0\.5 to 32\./); assert.match(t, /Enter an amount from 0\.5 to 128 GiB\./);
    assert.match(t, /Fix the highlighted fields to see what this will use\./); assert.doesNotMatch(t, /64 CPU|102 MiB|MiB/);
    fill(f, { ws_cpu_cores: '2', ws_memory_gib: '4', ws_storage_gib: '20', ws_node_id: 'n-one' });
    f.submit('create');
    t = text(f.host.innerHTML);
    assert.ok(t.includes('Create Acceptance?') && t.includes('2 CPU cores, 4 GiB memory, 20 GiB storage on Fixture machine'), 'resource preview before confirm');
    assert.ok(t.includes('the publisher can update its version over time') && t.includes('nothing is sent to a machine yet') && t.includes("A cloud workspace that has been started can't be deleted yet."));
    assert.equal(f.state.writes.length, 0, 'nothing is sent before confirm');
    f.click('back'); assert.match(f.host.innerHTML, /data-ws-form="create"/); assert.match(f.host.innerHTML, /value="Acceptance"/);
    // A lost answer then a retry reuses the request id, so the server can recognise the repeat.
    f.submit('create'); f.state.next = [new Error('timeout'), { workspace: created }]; f.click('confirm'); await flush();
    assert.match(text(f.host.innerHTML), /nothing is confirmed/); assert.match(text(f.host.innerHTML), /Create Acceptance\?/, 'still on the review, so the same request can be sent again');
    f.click('confirm'); await flush();
    assert.deepEqual(f.state.writes.map(([path]) => path), ['/api/compute/workspaces', '/api/compute/workspaces']);
    assert.deepEqual(f.state.writes[1][1], { name: 'Acceptance', image_source: 'custom', image: 'ghcr.io/acme/tools:1.2', cpu_cores: 2, memory_mib: 4096, storage_gib: 20, network: 'outbound', node_id: 'n-one', request_id: 'r-1' });
    assert.equal(f.state.writes[0][1].request_id, 'r-1');
    t = text(f.host.innerHTML);
    assert.ok(t.includes('Saved Acceptance in Commonplace. Nothing has been sent to Fixture machine yet.'), 'one honest confirmation');
    assert.equal((t.match(/Nothing has been sent/g) || []).length, 1, 'said once');
    assert.match(f.host.innerHTML, /data-ws-id="w-new"/); assert.doesNotMatch(f.host.innerHTML, /data-ws-form/);
    assert.ok(f.focused.includes('[data-ws-notice]'), 'the confirmation gets focus so it is announced'); assert.equal(f.status.text.startsWith('Saved Acceptance'), true);
    // The next create form gets a fresh request id.
    f.click('create'); assert.equal(f.ui && true, true); fill(f, valid); f.submit('create'); f.state.next = [{ workspace: onMachine({ id: 'w-2', name: 'Acceptance 2', created_at: clock + 2 }) }]; f.click('confirm'); await flush();
    assert.equal(f.state.writes[2][1].request_id, 'r-2');
    f.ui.stop();
    // At the cap the button is disabled and the sentence says why.
    const capped = fixture({ list: { workspaces: [record({ id: 'a', name: 'A' }), record({ id: 'b', name: 'B' })], can_manage: true }, config: { ...options, max_workspaces: 2 } }); await capped.open();
    assert.match(capped.host.innerHTML, /data-ws-action="create" disabled/); assert.match(text(capped.host.innerHTML), /You have reached the limit of 2 cloud workspaces\. Discard one that was never started to make room\./);
    capped.click('create'); assert.doesNotMatch(capped.host.innerHTML, /data-ws-form/);
    capped.ui.stop();
    // More records than the list shows: the count is said, and creation is refused by the total, not the page.
    const many = fixture({ list: { workspaces: [record({ id: 'a', name: 'A' })], total: 100, can_manage: true } }); await many.open();
    assert.match(text(many.host.innerHTML), /Showing the newest 1 of 100 cloud workspaces\./); assert.match(many.host.innerHTML, /data-ws-action="create" disabled/);
    many.ui.stop();
  },

  async 'server-errors'() {
    const f = fixture(); await f.open(); f.click('create');
    fill(f, { ws_name: 'Box' }); f.submit('create');
    f.state.next = [failure(400, { error: 'invalid_request', field: 'image', reason: 'characters' })]; f.click('confirm'); await flush();
    assert.match(f.host.innerHTML, /data-ws-form="create"/, 'back on the form');
    assert.match(text(f.host.innerHTML), /doesn't look like an image address/); assert.match(text(f.host.innerHTML), /Fix the highlighted field, then review again\./);
    assert.ok(f.focused.includes('[data-ws-control="image"]'));
    const again = async (failureValue, pattern) => { f.submit('create'); f.state.next = [failureValue]; f.click('confirm'); await flush(); assert.match(text(f.host.innerHTML), pattern); };
    await again(failure(409, { error: 'conflict', field: 'name', reason: 'name_taken' }), /Another cloud workspace already uses this name\./);
    await again(failure(400, { error: 'invalid_request', field: 'name', reason: 'no_letter' }), /Use at least one letter or digit in the name\./);
    await again(failure(400, { error: 'invalid_request', field: 'image', reason: 'registry_private' }), /local or private network/);
    await again(failure(400, { error: 'invalid_request', field: 'image', reason: 'registry_no_path' }), /Add the image name after the registry address/);
    await again(failure(400, { error: 'invalid_request', field: 'private_registry', reason: 'unavailable' }), /can't be used yet\. Use a public image\./);
    await again(failure(409, { error: 'conflict', field: 'request_id', reason: 'request_reused' }), /already sent with different values/);
    // Each refusal has its own sentence; the cap is not "the workspace changed".
    await again(failure(409, { error: 'conflict', reason: 'limit_reached' }), /reached the limit of 100 cloud workspaces\. Discard one that was never started/);
    assert.doesNotMatch(text(f.host.innerHTML), /changed or the request conflicts/);
    await again(failure(400, { error: 'invalid_request', reason: 'unknown_field' }), /server refused these values/);
    await again(new Error('timeout'), /nothing is confirmed/);
    await again(failure(403, { error: 'owner_required' }), /Only the workspace owner can change cloud workspaces\./);
    f.ui.stop();
  },

  async edit() {
    const row = startedRow({ id: 'w-one', name: 'Build box' });
    const f = fixture({ list: { workspaces: [row], can_manage: true } }); await f.open();
    f.click('edit', 'w-one');
    let t = text(f.host.innerHTML);
    assert.ok(t.includes('Change Build box') && t.includes('has been started. A change to the image, limits or network applies when it is restarted; a new name applies at once.'));
    assert.match(f.host.innerHTML, /<select name="ws_node_id"[^>]*disabled/, 'a started workspace cannot move'); assert.ok(t.includes("A started workspace can't move to another machine yet."), 'the reason sits beside the field');
    assert.doesNotMatch(f.host.innerHTML, /data-ws-id="w-one"/, 'the form replaces the row while it is open');
    assert.match(f.host.innerHTML, /value="2"/); assert.match(f.host.innerHTML, /value="4"/);
    f.submit('edit'); assert.match(text(f.host.innerHTML), /You haven't changed anything yet\./); assert.equal(f.state.writes.length, 0, 'an edit with no change sends nothing');
    fill(f, { ws_memory_gib: '8' }); f.submit('edit');
    t = text(f.host.innerHTML);
    assert.ok(t.includes('Confirm the change to Build box') && t.includes('Memory: 4 GiB → 8 GiB') && t.includes('2 CPU cores, 8 GiB memory, 20 GiB storage on Fixture machine') && t.includes('keeps its old settings until it is restarted'));
    f.state.next = [{ workspace: { ...row, memory_mib: 8192, version: 2, revision: 2, restart_needed: true, status: 'restart_needed' } }]; f.click('confirm'); await flush();
    assert.equal(f.state.writes[0][0], '/api/compute/workspaces/w-one');
    assert.deepEqual(f.state.writes[0][1], { memory_mib: 8192, expected_revision: 1 }, 'only the changed field, with the revision the form opened at');
    t = text(f.host.innerHTML);
    assert.ok(t.includes('Saved Build box in Commonplace. Restart needed: the running copy keeps its old settings until it is restarted.') && t.includes('8 GiB memory'));
    // A stale revision from the server says so and keeps the form.
    f.click('edit', 'w-one'); fill(f, { ws_memory_gib: '6' }); f.submit('edit');
    f.state.next = [failure(409, { error: 'conflict', reason: 'stale_revision' })]; f.click('confirm'); await flush();
    assert.match(text(f.host.innerHTML), /This workspace changed while you were editing/); assert.match(f.host.innerHTML, /data-ws-form="edit"/);
    f.ui.stop();
    // Never started: the editor says changes apply when it first starts.
    const fresh = fixture({ list: { workspaces: [record()], can_manage: true } }); await fresh.open(); fresh.click('edit', 'w-one');
    assert.match(text(fresh.host.innerHTML), /Not started yet, so changes apply when it first starts\./); assert.doesNotMatch(fresh.host.innerHTML, /<select name="ws_node_id"[^>]*disabled/);
    fresh.ui.stop();
  },

  async conflict() {
    // Tab A opens the editor at revision 1; tab B raises the CPU (revision 2); A reloads the list and saves memory.
    const mine = onMachine({ id: 'w-one', version: 1, revision: 1, updated_at: clock });
    const f = fixture({ list: { workspaces: [mine], can_manage: true } }); await f.open();
    f.click('edit', 'w-one'); fill(f, { ws_memory_gib: '8' }); f.submit('edit');
    f.state.list = { workspaces: [{ ...mine, cpu_cores: 8, version: 2, revision: 2, updated_at: clock + 5 }], can_manage: true };
    await f.ui.refresh(); await flush();
    f.click('confirm'); await flush();
    assert.equal(f.state.writes.length, 0, 'a form opened before the list changed never sends');
    const t = text(f.host.innerHTML);
    assert.match(t, /This workspace changed while you were editing\. Close this form, reload the list, and make your change again\./);
    assert.match(f.host.innerHTML, /data-ws-form="edit"/);
    assert.ok(f.focused.includes('[data-ws-summary]'));
    // Closing and reopening picks up the new state, and the change then carries the new revision.
    f.click('close'); f.click('edit', 'w-one'); fill(f, { ws_memory_gib: '8' }); f.submit('edit');
    f.state.next = [{ workspace: { ...mine, cpu_cores: 8, memory_mib: 8192, version: 3, revision: 3, updated_at: clock + 6 } }]; f.click('confirm'); await flush();
    assert.deepEqual(f.state.writes[0][1], { memory_mib: 8192, expected_revision: 2 });
    f.ui.stop();
    // A record deleted elsewhere while its form is open closes the form and says so.
    const gone = fixture({ list: { workspaces: [mine], can_manage: true } }); await gone.open(); gone.click('edit', 'w-one');
    gone.state.list = { workspaces: [], can_manage: true }; await gone.ui.refresh(); await flush();
    assert.doesNotMatch(gone.host.innerHTML, /data-ws-form/); assert.match(text(gone.host.innerHTML), /That cloud workspace is no longer available\./);
    gone.ui.stop();
  },

  async discard() {
    const never = onMachine({ id: 'w-one', name: 'Build box' }), started = startedRow({ id: 'w-two', name: 'Live box' });
    const f = fixture({ list: { workspaces: [never, started], can_manage: true } }); await f.open();
    f.click('discard', 'w-two'); assert.doesNotMatch(f.host.innerHTML, /Discard Live box\?/, 'a started workspace has no discard');
    f.click('discard', 'w-one');
    let t = text(f.host.innerHTML);
    assert.ok(t.includes('Discard Build box?') && t.includes('removes the saved settings for Build box from Commonplace') && t.includes('never started, so nothing on a machine is affected') && t.includes("You can't undo this"));
    assert.equal(f.state.writes.length, 0, 'nothing is sent before the confirmation');
    assert.ok(f.focused.includes('#compute-ws-heading'));
    f.click('close'); assert.doesNotMatch(f.host.innerHTML, /Discard Build box\?/); assert.match(f.host.innerHTML, /data-ws-id="w-one"/);
    assert.ok(f.focused.includes('[data-ws-id="w-one"] [data-ws-action="discard"]'), 'focus returns to the button that opened it');
    // The server refuses: the panel stays and says why.
    f.click('discard', 'w-one'); f.state.next = [failure(409, { error: 'conflict', reason: 'already_started' })]; f.click('confirm'); await flush();
    assert.match(text(f.host.innerHTML), /has been started, so it can't be discarded yet/); assert.match(f.host.innerHTML, /Discard Build box\?/);
    f.state.next = [{ discarded: { id: 'w-one', name: 'Build box' } }]; f.click('confirm'); await flush();
    assert.deepEqual(f.state.writes[1], ['/api/compute/workspaces/w-one/discard', { expected_revision: 1 }]);
    t = text(f.host.innerHTML);
    assert.ok(t.includes('Discarded Build box. Nothing on a machine was affected.')); assert.doesNotMatch(f.host.innerHTML, /data-ws-id="w-one"/); assert.match(f.host.innerHTML, /data-ws-id="w-two"/);
    assert.ok(f.focused.includes('[data-ws-notice]'));
    f.ui.stop();
  },

  async 'registry-hidden'() {
    // Registry logins have no custody purpose yet: no field, no checkbox, no bind button, no private flag on the wire.
    const legacy = record({ id: 'w-old', name: 'Legacy private', private_registry: true, status: 'needs_credential', node_id: 'n-one', machine_name: 'Fixture machine', machine_assigned: true, discardable: false });
    const f = fixture({ list: { workspaces: [legacy], can_manage: true } }); await f.open();
    assert.doesNotMatch(f.host.innerHTML, /data-ws-action="bind"/);
    assert.match(text(f.host.innerHTML), /Needs a registry login/);
    f.click('bind', 'w-old'); assert.doesNotMatch(f.host.innerHTML, /data-ws-form="bind"|ws_credential/);
    f.click('detail', 'w-old'); assert.match(text(f.host.innerHTML), /needs a login, which can't be added yet/); f.click('close');
    f.click('create'); f.input('ws_image_choice', 'custom'); fill(f, valid); f.submit('create');
    f.state.next = [{ workspace: record({ id: 'w-n', name: 'Acceptance', created_at: clock + 1 }) }]; f.click('confirm'); await flush();
    assert.equal('private_registry' in f.state.writes[0][1], false); assert.equal(JSON.stringify(f.state.writes).includes('credential'), false);
    f.ui.stop();
  },

  async member() {
    const row = onMachine({ discardable: false }); row.machine_name = null; row.node_id = null; row.private_registry = true; row.credential_bound = true;
    const f = fixture({ owner: false, list: { workspaces: [row], can_manage: false } }); await f.open();
    const t = text(f.host.innerHTML);
    assert.ok(t.includes('Compute management requires the current workspace owner. You can still look at the cloud workspaces below.'));
    assert.ok(t.includes('Build box') && t.includes('Not started yet') && t.includes('2 CPU cores, 4 GiB memory, 20 GiB storage on an assigned machine'));
    assert.ok(!t.includes('Fixture machine') && !t.includes('ghcr.io/acme'), 'no machine name or image address in the list');
    assert.doesNotMatch(f.host.innerHTML, /data-ws-action="(create|edit|bind|discard)"|role="alert">Compute management/);
    assert.match(f.host.innerHTML, /data-ws-action="detail"/);
    f.click('create'); f.click('edit', 'w-one'); f.click('discard', 'w-one'); assert.doesNotMatch(f.host.innerHTML, /data-ws-form|Discard Build box/, 'a member can never open a form');
    f.click('detail', 'w-one'); const d = text(f.host.innerHTML);
    assert.ok(d.includes('Only the workspace owner can change cloud workspaces.') && d.includes('A machine is assigned') && !d.includes('Fixture machine'));
    assert.doesNotMatch(f.host.innerHTML, /data-ws-action="(edit|discard)"/);
    f.click('close'); assert.ok(f.focused.includes('[data-ws-id="w-one"] [data-ws-action="detail"]'), 'closing the details returns focus to the row');
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
    assert.equal(down.status.text, 'Cloud workspaces reloaded.'); assert.ok(down.focused.includes('[data-ws-notice]'), 'focus goes to the status line after Reload');
    down.ui.stop();
    // The image list failing says why creation is unavailable instead of hiding the button silently.
    const noOptions = fixture({ config: failure(500, { error: 'x' }) }); await noOptions.open();
    assert.match(text(noOptions.host.innerHTML), /The image list could not be loaded, so new workspaces cannot be created right now/);
    assert.match(noOptions.host.innerHTML, /data-ws-action="create" disabled/); noOptions.click('create'); assert.doesNotMatch(noOptions.host.innerHTML, /data-ws-form/);
    // Editing needs the served options too: no fallback copy of the limits.
    const noEdit = fixture({ list: { workspaces: [record()], can_manage: true }, config: failure(500, { error: 'x' }) }); await noEdit.open();
    noEdit.click('edit', 'w-one'); assert.doesNotMatch(noEdit.host.innerHTML, /data-ws-form/);
    noOptions.ui.stop(); noEdit.ui.stop();
    // Leaving the route clears everything; a late answer publishes nothing.
    const gone = fixture(); gone.ui.show(); gone.setRoute('#/board/demo'); gone.events.dispatchEvent(new Event('hashchange')); await flush();
    assert.equal(gone.host.innerHTML, '');
  },

  async focus() {
    const f = fixture({ list: { workspaces: [onMachine()], can_manage: true } }); await f.open();
    // Escape closes an open form and puts focus back on the button that opened it.
    f.click('edit', 'w-one'); assert.match(f.host.innerHTML, /data-ws-form="edit"/);
    assert.equal(f.key('Escape', false).prevented, false, 'Escape outside the section is not ours');
    assert.equal(f.key('Escape').prevented, true); assert.doesNotMatch(f.host.innerHTML, /data-ws-form/);
    assert.ok(f.focused.includes('[data-ws-id="w-one"] [data-ws-action="edit"]'));
    f.click('create'); f.key('Escape'); assert.doesNotMatch(f.host.innerHTML, /data-ws-form/); assert.equal(f.focused.at(-1), '[data-ws-action="create"]:not([disabled])');
    f.click('detail', 'w-one'); f.key('Escape'); assert.doesNotMatch(f.host.innerHTML, /compute-ws-detail/);
    // From the details, Change settings keeps the original opener for the way back.
    f.click('detail', 'w-one'); f.click('edit', 'w-one'); f.key('Escape'); assert.equal(f.focused.at(-1), '[data-ws-id="w-one"] [data-ws-action="detail"]');
    // Escape does nothing while a save is in flight.
    f.click('edit', 'w-one'); fill(f, { ws_memory_gib: '8' }); f.submit('edit'); f.state.next = [onMachine({ memory_mib: 8192, version: 2, revision: 2 })]; f.click('confirm');
    assert.equal(f.key('Escape').prevented, false); await flush();
    // The top Reload inventory button gets focus back once the reload ends.
    f.focused.length = 0;
    f.dispatch('click', { target: { closest: selector => selector === '[data-compute-action]' ? { dataset: { computeAction: 'refresh', id: '' }, disabled: false } : null } }); await flush();
    assert.ok(f.focused.includes('[data-compute-action="refresh"]'));
    f.ui.stop();
  },
};

const selection = process.argv[2];
assert.ok(cases[selection], `Unknown workspace UI case: ${selection}`);
await cases[selection](); console.log(`Compute workspaces UI: ${selection} passed`);
