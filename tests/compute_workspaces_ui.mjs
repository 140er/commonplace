import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// One list of accepted and refused input, read by this suite and by tests/test_compute_workspaces.py.
const inputs = JSON.parse(readFileSync(new URL('./fixtures/compute_workspace_inputs.json', import.meta.url), 'utf8'));

// Cloud workspaces on the Compute screen: the production module and the production Compute controller run
// against a synthetic in-memory server. No socket, browser or backend is opened.
const source = name => readFileSync(new URL(`../commonplace/web/static/${name}.js`, import.meta.url), 'utf8');
const moduleURL = text => `data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
const apiURL = moduleURL(source('api'));
const rewrite = (text, extra = {}) => Object.entries({ './api.js': apiURL, ...extra }).reduce((out, [name, url]) => out.replace(new RegExp(`(['"])${name.replace(/[./]/g, '\\$&')}\\1`, 'g'), () => JSON.stringify(url)), text);
const workspacesURL = moduleURL(rewrite(source('compute-workspaces')));
const ws = await import(workspacesURL);
const overviewURL = moduleURL(rewrite(source('compute-overview')));
const { createCompute } = await import(moduleURL(rewrite(source('compute'), { './compute-workspaces.js': workspacesURL, './compute-overview.js': overviewURL })));
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
  presets: [{ id: 'small', label: 'Small', cpu_cores: 1, memory_mib: 2048, storage_gib: 10 }, { id: 'standard', label: 'Standard', cpu_cores: 2, memory_mib: 4096, storage_gib: 20 }, { id: 'large', label: 'Large', cpu_cores: 4, memory_mib: 8192, storage_gib: 50 }],
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
const inventory = (nodes = [machine]) => ({ gateways: [], nodes, scopes: [], executions: [], can_manage: true, capabilities: { transport: false, passive_discovery: false, scripts: false } });

function fixture({ list = { workspaces: [], can_manage: true }, config = options, owner = true, nodes = [machine] } = {}) {
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
    if (request.method === 'POST') { state.writes.push([path, request.body]); const out = state.next.length ? state.next.shift() : null; if (typeof out === 'function') return out(); return out instanceof Error ? Promise.reject(out) : Promise.resolve(structuredClone(out)); }
    const answer = path === '/api/compute/workspaces' ? state.list : path === '/api/compute/workspaces/images' ? state.config : path === '/api/compute' ? (owner ? inventory(nodes) : failure(403, { error: 'owner_required' })) : path === '/api/boards' ? { boards: [] } : path === '/api/compute/discoveries' ? { discoveries: [], capabilities: {} } : path === '/api/compute/scripts' ? { scripts: [] } : path === '/api/compute/credentials' ? { credentials: [], status: 'ready' } : null;
    return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(structuredClone(answer));
  };
  let counter = 0;
  const ui = createCompute({ mount: () => host, api, current: () => user, route: () => hash, doc, events, now: () => clock, requestId: () => `r-${++counter}` });
  const dispatch = (kind, event) => listeners.get(kind)?.(event);
  const click = (action, id, extra = {}) => dispatch('click', { target: { closest: selector => selector === '[data-ws-action]' ? { dataset: { wsAction: action, id, ...extra } } : null } });
  const jump = key => { const event = { prevented: false, preventDefault() { this.prevented = true; }, target: { closest: selector => selector === '[data-ws-jump]' ? { dataset: { wsJump: key } } : null } }; dispatch('click', event); return event; };
  const input = (name, value, type = 'text') => dispatch('input', { type: 'input', target: { name, type, value, checked: value } });
  // A committed value (the browser's change event: blur after typing, or a choice made in a list).
  const change = (name, value, type = 'text') => dispatch('change', { type: 'change', target: { name, type, value, checked: value } });
  const submit = kind => dispatch('submit', { target: { closest: selector => selector === '[data-ws-form]' ? { dataset: { wsForm: kind } } : null }, preventDefault() {}, stopPropagation() {} });
  const key = (name, inside = true) => { const event = { key: name, target: { closest: selector => inside && selector === '.compute-ws' ? {} : null }, prevented: false, preventDefault() { this.prevented = true; } }; dispatch('keydown', event); return event; };
  const open = async () => { ui.show(); await flush(); };
  return { ui, host, state, calls, focused, status, click, jump, input, change, submit, key, open, doc, events, dispatch, setRoute(value) { hash = value; } };
}
const fill = (f, values) => { for (const [name, value] of Object.entries(values)) f.input(name, value); };
const valid = { ws_name: 'Acceptance', ws_image: 'ghcr.io/acme/tools:1.2', ws_cpu_cores: '2', ws_memory_gib: '4', ws_storage_gib: '20' };
const section = html => html.slice(html.indexOf('compute-ws"'), html.indexOf('<details class="compute-advanced'));
// Alerts that carry text: the static load-error node is always in the markup and is empty until something fails.
const alerts = html => (html.match(/role="alert">[^<]/g) || []).length;

const cases = {
  async pure() {
    assert.equal(ws.gib(4096), '4 GiB'); assert.equal(ws.gib(512), '0.5 GiB'); assert.equal(ws.gib(1536), '1.5 GiB'); assert.equal(ws.gib(4608), '4.5 GiB', 'GiB everywhere, never MiB');
    assert.equal(ws.resourcePreview(2, 4096, 20, 'Fixture machine'), '2 CPU cores, 4 GiB memory, 20 GiB storage. Machine: Fixture machine');
    assert.equal(ws.resourcePreview(1, 4096, 20, null), '1 CPU core, 4 GiB memory, 20 GiB storage. Machine: not chosen yet');
    assert.equal(ws.parseImage('ghcr.io/acme/tools@sha256:' + 'a'.repeat(64)).pinned, true);
    assert.equal(ws.parseImage('ubuntu:24.04').pinned, false);
    const base = { ...ws.blankValues(options), ws_name: 'Build box' };
    assert.deepEqual(ws.validate(base, options), {});
    const limits = [['ws_cpu_cores', '0', /from 0\.5 to 32/], ['ws_cpu_cores', '1.3', /steps of 0\.5/], ['ws_cpu_cores', '', /number of cores/], ['ws_memory_gib', '0.25', /from 0\.5 to 128 GiB/], ['ws_memory_gib', '129', /from 0\.5 to 128 GiB/], ['ws_memory_gib', 'x', /amount of memory/], ['ws_storage_gib', '0', /from 1 to 2048/], ['ws_storage_gib', '2049', /from 1 to 2048/], ['ws_storage_gib', '1.5', /whole number/], ['ws_name', '', /1 to 60/], ['ws_name', ' x', /normal spaces/], ['ws_network', 'host', /network/]];
    for (const [field, value, pattern] of limits) assert.match(Object.values(ws.validate({ ...base, [field]: value }, options))[0] || '', pattern, `${field}=${value}`);
    assert.match(ws.validate({ ...base, ws_image_source: 'custom', ws_image: 'bad image' }, options).image, /doesn't look like an image address/);
    assert.deepEqual(ws.workspacePayload({ ...base, ws_name: 'Build box', ws_image_source: 'custom', ws_image: ' ghcr.io/acme/tools:1.2 ', ws_node_id: 'n-one' }), { name: 'Build box', image_source: 'custom', image: 'ghcr.io/acme/tools:1.2', cpu_cores: 2, memory_mib: 4096, storage_gib: 20, network: 'outbound', node_id: 'n-one' });
    assert.equal(ws.createPayload(base, 'r-9').request_id, 'r-9'); assert.equal('request_id' in ws.createPayload(base), false);
    const before = { ...base };
    assert.equal(ws.editPayload(base, before, 3), null, 'nothing changed means nothing to send');
    assert.deepEqual(ws.editPayload({ ...base, ws_memory_gib: '8' }, before, 3), { memory_mib: 8192, expected_revision: 3 }, 'only the changed field and the version it opened at');
    assert.deepEqual(ws.editPayload({ ...base, ws_image_source: 'custom', ws_image: 'ghcr.io/a/b:1' }, before, 1), { image_source: 'custom', image: 'ghcr.io/a/b:1', expected_revision: 1 }, 'source and image travel together');
    assert.equal(ws.hostedOn('ghcr.io'), 'GitHub Container Registry (ghcr.io)'); assert.equal(ws.hostedOn('docker.io'), 'Docker Hub (docker.io)'); assert.equal(ws.hostedOn('registry.example.com:5000'), 'registry.example.com:5000');
    assert.equal(ws.editsOf({ version: 1 }), 'Not edited since it was created'); assert.equal(ws.editsOf({ version: 2 }), 'Edited once'); assert.equal(ws.editsOf({ version: 4 }), 'Edited 3 times');
  },

  async images() {
    // The same list of accepted and refused references the server suite reads.
    for (const ok of inputs.images.accepted) assert.equal(ws.parseImage(ok).reason, undefined, ok);
    for (const [value, reason] of Object.entries(inputs.images.refused)) assert.equal(ws.parseImage(value).reason, reason, JSON.stringify(value));
  },

  async names() {
    const base = { ...ws.blankValues(options), ws_name: 'Build box' };
    const name = value => ws.validate({ ...base, ws_name: value }, options).name || '';
    // The same names the server suite reads: accepted ones validate, refused ones fail with the server's reason.
    for (const ok of inputs.names.accepted) assert.equal(name(ok), '', JSON.stringify(ok));
    const words = { length: /1 to 60/, characters: /letters, numbers and normal spaces/, no_letter: /at least one letter or digit/ };
    for (const [value, reason] of Object.entries(inputs.names.refused)) assert.match(name(value), words[reason], `${JSON.stringify(value)} (${reason})`);
    // Code points, not UTF-16 units: 31 code points are 61 units and still fit, as on the server.
    assert.equal(name('a' + '\u{1f600}'.repeat(30)), ''); assert.equal(name('東京 7'), '');
    assert.equal(name('Café'), '', 'a combining accent normalises, so it counts as one code point');
    assert.equal(ws.workspacePayload({ ...base, ws_name: 'Café' }).name, 'Café', 'the name is sent normalised');
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
      record({ id: 'i', name: 'I curated', image_source: 'curated', image: 'docker.io/library/ubuntu:24.04', image_registry: 'docker.io', curated_label: 'Ubuntu 24.04' }),
      onMachine({ id: 'j', name: 'J failed first', status: 'failed', status_reason: 'image_pull_failed', started: true, applied_revision: null, discardable: false }),
    ];
    const f = fixture({ list: { workspaces: rows, can_manage: true } });
    // Loading first: the section never renders empty.
    f.ui.show(); assert.match(text(f.host.innerHTML), /Loading Compute inventory/); await flush();
    const t = text(f.host.innerHTML);
    for (const phrase of ['Saved, not started', 'Private image', 'Changes waiting', 'Settings applied', 'Failed', '2 CPU cores, 4 GiB memory, 20 GiB storage', 'Machine: Fixture machine', 'Machine: not chosen yet']) assert.ok(t.includes(phrase), phrase);
    // One vocabulary: nothing says a workspace is ready, running or restarting while no start exists to run it.
    assert.doesNotMatch(section(f.host.innerHTML).replace(/<[^>]*>/g, ' '), /\bReady\b|Restart needed|Not started\./, 'old status words are gone');
    assert.doesNotMatch(t, /It will run on/);
    // A failed record shows its reason in the list row, not only in the details.
    assert.match(text(f.host.innerHTML.slice(f.host.innerHTML.indexOf('data-ws-id="f"'), f.host.innerHTML.indexOf('data-ws-id="g"'))), /ran out of memory at 4 GiB/);
    assert.match(text(f.host.innerHTML.slice(f.host.innerHTML.indexOf('data-ws-id="g"'), f.host.innerHTML.indexOf('data-ws-id="h"'))), /Failed for a reason this page does not recognise\. Check the machine, then reload this list\./);
    // Rows are name, status, one resource line and the image; the explanation lives in the detail.
    assert.equal((f.host.innerHTML.match(/<h3 class="compute-ws-name" tabindex="-1">/g) || []).length, 10, 'every workspace name is a heading');
    assert.ok(t.includes('Image: ghcr.io/acme/tools:1.2') && t.includes('Image: Ubuntu 24.04 (docker.io/library/ubuntu:24.04)'), 'rows show the image');
    assert.doesNotMatch(f.host.innerHTML, /Nothing has been sent|publisher can update/);
    assert.equal((f.host.innerHTML.match(/data-ws-action="edit"/g) || []).length, 10 + 1, 'Change settings on every row, plus Change image on the private one');
    assert.equal((f.host.innerHTML.match(/data-ws-action="discard"/g) || []).length, 4, 'only records that never started can be discarded');
    assert.match(f.host.innerHTML, /aria-label="Details for A none"/); assert.match(f.host.innerHTML, /aria-label="Change settings for E restart"/); assert.match(f.host.innerHTML, /aria-label="Discard C pending"/); assert.match(f.host.innerHTML, /aria-label="Change image of B cred"/);
    assert.match(f.host.innerHTML, /class="btn danger compute-ws-apart" data-ws-action="discard"/, 'Discard is danger-styled and set apart from the other actions');
    assert.doesNotMatch(f.host.innerHTML, /data-ws-action="bind"|orchestrator|style="/i);
    assert.equal(alerts(section(f.host.innerHTML)), 0);
    const detailOf = id => { f.click('detail', id); const html = f.host.innerHTML, at = html.indexOf('compute-ws-detail'), out = text(html.slice(at, html.indexOf('</section>', at))); f.click('close'); return out; };
    for (const row of rows) {
      const shown = detailOf(row.id);
      assert.doesNotMatch(shown, /matches the saved settings/, row.name);
      assert.ok(!(shown.includes('has been started') && shown.includes('Not started')), `${row.name}: started and not started never contradict each other`);
      if (row.status === 'restart_needed') { assert.match(shown, /Changes waiting\./); assert.doesNotMatch(shown, /Ready|running|Restart needed/); }
      assert.doesNotMatch(shown, /Saved, not started\. Saved, not started|Not started\. Not started/, `${row.name}: the status is said once`);
    }
    // Statuses say what to do now: nothing is a dead end.
    assert.ok(detailOf('b').includes(`${ws.PRIVATE_IMAGE} Choose a public image.`));
    assert.match(detailOf('e'), /The saved settings are newer than the ones a machine applied\. Starting is not available yet, so they are kept for its next start\./);
    assert.match(detailOf('f'), /The last recorded start failed\. It ran out of memory at 4 GiB\. Raise the memory limit with Change settings/); assert.match(detailOf('g'), /Failed for a reason this page does not recognise\. Check the machine, then reload this list\./);
    assert.match(detailOf('d'), /Settings applied\. A machine recorded that it applied the saved settings\./); assert.match(detailOf('f'), /The last recorded start failed\./); assert.doesNotMatch(detailOf('f'), /running copy/);
    assert.match(detailOf('h'), /Locked to one exact version/); assert.match(detailOf('i'), /Official image\. The publisher can update its version over time/); assert.match(detailOf('a'), /The publisher can update this version over time/);
    assert.match(detailOf('c'), new RegExp(`Saved, not started\\. ${ws.NOT_STARTED_NOTE.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`), 'the one not-started sentence, reused');
    assert.match(detailOf('c'), /Machine Fixture machine/);
    assert.match(detailOf('d'), /A started cloud workspace can't be discarded yet\./); assert.match(detailOf('c'), /discard this workspace because it was never started/); assert.doesNotMatch(detailOf('c'), /can't be discarded yet/, 'a never-started record is not told about started ones');
    assert.doesNotMatch(detailOf('c'), /deleted/, 'one verb: discard');
    assert.match(detailOf('e'), /Edited once\. Changes are waiting for the next start\./); assert.match(detailOf('a'), /Not edited since it was created\./);
    // Every detail button names its workspace.
    f.click('detail', 'c'); for (const label of ['Change settings for C pending', 'Discard C pending', 'Close details of C pending']) assert.match(f.host.innerHTML, new RegExp(`aria-label="${label}"`)); f.click('close');
    assert.match(detailOf('c'), /Comes from GitHub Container Registry \(ghcr\.io\)/); assert.match(detailOf('i'), /Comes from Docker Hub \(docker\.io\)/);
    assert.match(detailOf('j'), /The last recorded start failed\./, 'a failed first start says so in History');
    // The private-image status offers its fix: Change image opens the form on the image.
    f.click('edit', 'b', { focus: 'image' }); assert.ok(f.focused.includes('[data-ws-control="image"]'), 'Change image puts focus on the image'); f.click('close');
    f.ui.stop();
    // Empty states say who can act, once, and disappear while a form is open or any workspace exists.
    const empty = fixture(); await empty.open();
    assert.match(text(empty.host.innerHTML), /Nothing here yet\. Choose New cloud workspace to create one\. A machine can be chosen later\./);
    assert.equal((text(empty.host.innerHTML).match(/a separate place on one of your machines where Coworkers can run/g) || []).length, 1, 'the definition appears once, and says what it is meant to be, not that it runs');
    empty.click('create'); assert.doesNotMatch(text(empty.host.innerHTML), /Nothing here yet/, 'no stale empty sentence under an open form');
    empty.ui.stop();
  },

  async 'status-copy'() {
    // Slice 1 records settings only: nothing may say a limit is enforced or a workspace runs, and no hint is a conditional promise.
    const f = fixture({ list: { workspaces: [onMachine()], can_manage: true } }); await f.open();
    f.click('create');
    const all = text(f.host.innerHTML);
    for (const promise of ['Internet access out', 'kept in a volume', 'is stopped if it goes over', 'saved on', 'stopped if it goes over.', 'Once workspaces can start', 'not checked yet']) assert.ok(!all.includes(promise), promise);
    assert.ok(all.includes(ws.NOT_STARTED), 'one sentence for the not-started state, on the form');
    assert.equal((all.match(/Starting is not available yet/g) || []).length, 1, 'said once on the form');
    f.ui.stop();
    const started = fixture({ list: { workspaces: [startedRow()], can_manage: true } }); await started.open();
    started.click('edit', 'w-one'); assert.ok(text(started.host.innerHTML).includes(ws.STARTED)); assert.ok(!text(started.host.innerHTML).includes('Starting is not available yet'));
    started.ui.stop();
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
    assert.ok(t.includes('New cloud workspace') && t.includes('Fields marked * are required') && t.includes('What it will use'));
    assert.ok(t.includes('2 CPU cores, 4 GiB memory, 20 GiB storage. Machine: not chosen yet'), 'defaults are previewed');
    assert.ok(t.includes('Default 2.') && t.includes('Default 4.') && t.includes('Default 20.'), 'defaults are labelled');
    assert.match(f.host.innerHTML, /<option value="docker\.io\/library\/ubuntu:24\.04" selected>/);
    assert.match(f.host.innerHTML, /<option value="n-one">Fixture machine<\/option>/, 'the machine is named, with no health-like suffix');
    // Each resource says what to choose: a "What do I need?" line with examples.
    for (const key of ['cpu_cores', 'memory_mib', 'storage_gib']) assert.match(f.host.innerHTML, new RegExp(`id="ws-need-${key}"><strong>What do I need\\?</strong>`));
    assert.ok(t.includes('Examples: 1 for a small web app, 4 for a large test suite.') && (t.match(/1 GiB is about 1\.07 GB/g) || []).length === 2, 'memory and storage both carry the GB note');
    assert.doesNotMatch(t, /saving files fails|is stopped|enforced/, 'no hint describes enforcement that does not exist');
    // Accessible structure: the label closes before the hint and the error; required is marked; error slots are polite live regions.
    assert.match(f.host.innerHTML, /<label for="ws-field-name">Name <span class="compute-req" aria-hidden="true">\*<\/span><\/label><input name="ws_name" id="ws-field-name" data-ws-control="name" aria-describedby="ws-hint-name ws-error-name" required/);
    assert.doesNotMatch(f.host.innerHTML, /name="ws_name"[^>]*placeholder/, 'the example is in the hint once, not repeated in a placeholder');
    assert.match(f.host.innerHTML, /aria-describedby="ws-hint-cpu_cores ws-need-cpu_cores ws-error-cpu_cores"/);
    assert.equal(alerts(section(f.host.innerHTML)), 0, 'no role=alert: focus or a live region announces, never both');
    assert.match(f.host.innerHTML, /<small class="compute-ws-error" id="ws-error-name" data-ws-error="name" aria-live="polite"><\/small>/);
    assert.doesNotMatch(f.host.innerHTML, /data-ws-preview[^>]*(role|aria-live)/);
    assert.doesNotMatch(section(f.host.innerHTML), /ws_private_registry|credential|type="password"|maxlength/);
    assert.ok(f.focused.includes('#compute-ws-heading'), 'focus moves to the form heading');
    // A blank name: review is refused; the summary lists the field with a link and only the name control is invalid.
    f.submit('create');
    t = text(f.host.innerHTML);
    assert.match(t, /Fix this field, then review again:/); assert.match(t, /Name: Give it a name of 1 to 60 characters\./);
    assert.match(f.host.innerHTML, /<a href="#ws-field-name" data-ws-jump="name">/);
    assert.equal((f.host.innerHTML.match(/aria-invalid="true"/g) || []).length, 1); assert.match(f.host.innerHTML, /data-ws-control="name" aria-describedby="ws-hint-name ws-error-name" aria-invalid="true"/);
    assert.ok(f.focused.includes('[data-ws-summary]') && !f.focused.includes('[data-ws-control="name"]'), 'a failed check focuses the summary, whose links jump to the fields');
    assert.equal(f.state.writes.length, 0);
    assert.equal(f.jump('name').prevented, true); assert.equal(f.focused.at(-1), '[data-ws-control="name"]', 'a summary link focuses its field');
    // Own image: the address box is in the form but hidden while a ready-made image is chosen; choosing "My own image" shows it in place.
    assert.match(f.host.innerHTML, /data-ws-field="image" hidden>/);
    f.input('ws_image_choice', 'custom');
    assert.doesNotMatch(f.host.innerHTML, /data-ws-field="image" hidden>/);
    assert.doesNotMatch(f.host.innerHTML, /name="ws_image"[^>]*placeholder/, 'an empty box is not dressed up as a filled one');
    t = text(f.host.innerHTML);
    assert.ok(t.includes('An image is the ready-made software a workspace starts from.') || t.includes('Paste the address of your own image below.'));
    assert.ok(t.includes('The address of the image, for example ubuntu:24.04. Not sure? Pick a ready-made image above.') && t.includes('Where do I find it?'));
    const help = f.host.innerHTML.slice(f.host.innerHTML.indexOf('<details class="compute-ws-help">'), f.host.innerHTML.indexOf('</details>', f.host.innerHTML.indexOf('compute-ws-help')));
    assert.ok(text(help).includes('docker pull') && text(help).includes('A registry is a website that stores images.') && text(help).includes('anyone must be able to download them without signing in'), 'the jargon lives in the disclosure, with one definition');
    assert.equal((text(f.host.innerHTML.replace(help, '')).match(/docker pull|registry/gi) || []).length, 0, 'docker pull and registry appear nowhere else on the form');
    assert.doesNotMatch(t, /@sha256:…|Pin it|Only public images work for now\./);
    // The image control is the one that carries the image error, not the select.
    fill(f, { ws_name: 'Acceptance', ws_image: 'ubuntu; rm -rf /' }); f.submit('create');
    assert.match(text(f.host.innerHTML), /doesn't look like an image address/);
    assert.match(f.host.innerHTML, /name="ws_image" id="ws-field-image" data-ws-control="image" aria-describedby="ws-hint-image ws-error-image" aria-invalid="true"/);
    assert.doesNotMatch(f.host.innerHTML, /name="ws_image_choice"[^>]*aria-invalid/);
    // Out-of-range limits are never previewed as the plan, and the preview never switches unit.
    fill(f, { ws_image: 'ghcr.io/acme/tools:1.2', ws_cpu_cores: '64', ws_memory_gib: '0.1' }); f.submit('create');
    t = text(f.host.innerHTML);
    assert.match(t, /Enter a number of cores from 0\.5 to 32\./); assert.match(t, /Enter an amount from 0\.5 to 128 GiB\./);
    assert.match(t, /Fix these 2 fields, then review again:/);
    assert.match(t, /Fix the highlighted fields to see what this will use\./); assert.doesNotMatch(t, /64 CPU|102 MiB|MiB/);
    fill(f, { ws_cpu_cores: '2', ws_memory_gib: '4', ws_storage_gib: '20', ws_node_id: 'n-one' });
    f.submit('create');
    t = text(f.host.innerHTML);
    assert.ok(t.includes('Create “Acceptance”?') && t.includes('2 CPU cores, 4 GiB memory, 20 GiB storage. Machine: Fixture machine'), 'resource preview before confirm');
    assert.ok(t.includes('the publisher can update its version over time') && t.includes('Confirming saves these settings in Commonplace only. Starting is not available yet, so nothing is sent to a machine.'));
    assert.doesNotMatch(t, /can't be discarded yet|Saved, not started/, 'the review says what confirming does once, without the long status sentence');
    assert.match(f.host.innerHTML, /<ul class="compute-ws-review" role="list">/);
    assert.ok(f.host.innerHTML.indexOf('data-ws-honesty') < f.host.innerHTML.indexOf('compute-ws-actions'), 'the honesty sentence sits above the action buttons');
    assert.equal(f.state.writes.length, 0, 'nothing is sent before confirm');
    f.click('back'); assert.match(f.host.innerHTML, /data-ws-form="create"/); assert.match(f.host.innerHTML, /value="Acceptance"/);
    // A lost answer then a retry reuses the request id, so the server can recognise the repeat.
    f.submit('create'); f.state.next = [new Error('timeout'), { workspace: created }]; f.click('confirm'); await flush();
    assert.match(text(f.host.innerHTML), /nothing is confirmed/); assert.match(text(f.host.innerHTML), /Create “Acceptance”\?/, 'still on the review, so the same request can be sent again');
    f.click('confirm'); await flush();
    assert.deepEqual(f.state.writes.map(([path]) => path), ['/api/compute/workspaces', '/api/compute/workspaces']);
    assert.deepEqual(f.state.writes[1][1], { name: 'Acceptance', image_source: 'custom', image: 'ghcr.io/acme/tools:1.2', cpu_cores: 2, memory_mib: 4096, storage_gib: 20, network: 'outbound', node_id: 'n-one', request_id: 'r-1' });
    assert.equal(f.state.writes[0][1].request_id, 'r-1');
    t = text(f.host.innerHTML);
    assert.ok(t.includes('Saved “Acceptance”, not started. Starting is not available yet.'), 'one honest confirmation, with the name quoted');
    assert.match(f.host.innerHTML, /data-ws-id="w-new"/); assert.doesNotMatch(f.host.innerHTML, /data-ws-form/);
    assert.ok(f.focused.includes('[data-ws-id="w-new"] .compute-ws-name') && !f.focused.includes('[data-ws-notice]'), 'focus goes to the saved row, never to the status text'); assert.equal(f.status.text.startsWith('Saved “Acceptance”'), true);
    // The next create form gets a fresh request id.
    f.click('create'); fill(f, valid); f.submit('create'); f.state.next = [{ workspace: onMachine({ id: 'w-2', name: 'Acceptance 2', created_at: clock + 2 }) }]; f.click('confirm'); await flush();
    assert.equal(f.state.writes[2][1].request_id, 'r-2');
    f.ui.stop();
    // At the cap there is no create button, and the sentence says why.
    const capped = fixture({ list: { workspaces: [record({ id: 'a', name: 'A' }), record({ id: 'b', name: 'B' })], can_manage: true }, config: { ...options, max_workspaces: 2 } }); await capped.open();
    assert.doesNotMatch(capped.host.innerHTML, /data-ws-action="create"/); assert.match(text(capped.host.innerHTML), /You have reached the limit of 2 cloud workspaces\. Discard one that was never started to make room\./);
    capped.click('create'); assert.doesNotMatch(capped.host.innerHTML, /data-ws-form/);
    capped.ui.stop();
    // When every one of them has started, the sentence says nothing can be discarded.
    const allIn = fixture({ list: { workspaces: [startedRow({ id: 'a', name: 'A' }), startedRow({ id: 'b', name: 'B' })], can_manage: true }, config: { ...options, max_workspaces: 2 } }); await allIn.open();
    assert.match(text(allIn.host.innerHTML), /You have reached the limit of 2 cloud workspaces\. All 2 have started, so none can be discarded yet\./); allIn.ui.stop();
    // More records than the list shows: the count is said, and creation is refused by the total, not the page.
    const many = fixture({ list: { workspaces: [record({ id: 'a', name: 'A' })], total: 100, can_manage: true } }); await many.open();
    assert.match(text(many.host.innerHTML), /Showing the newest 1 of 100 cloud workspaces\./); assert.doesNotMatch(many.host.innerHTML, /data-ws-action="create"/);
    many.ui.stop();
  },

  async presets() {
    const f = fixture(); await f.open(); f.click('create');
    const html = f.host.innerHTML;
    assert.match(html, /role="group" aria-labelledby="ws-presets-label"><span class="compute-label" id="ws-presets-label">Start from a size/);
    for (const [id, label] of [['small', 'Small'], ['standard', 'Standard'], ['large', 'Large']]) assert.match(html, new RegExp(`data-preset="${id}"[^>]*>${label}<span`));
    assert.match(html, /data-preset="standard" aria-current="true"/, 'the defaults are the Standard size, marked as current (not "pressed")'); assert.doesNotMatch(html, /data-preset="small"[^>]*aria-current|aria-pressed/);
    assert.match(html, /<fieldset><legend class="compute-ws-legend">/, 'the fields are grouped under a legend');
    assert.ok(text(html).includes('1 CPU core, 2 GiB, 10 GiB') && text(html).includes('Fills the three fields below; you can still change any of them.'));
    // Choosing a size fills the fields (still editable); a repaint (a blank name is refused) shows what they now hold.
    f.click('preset', undefined, { preset: 'large' }); f.submit('create');
    for (const [name, value] of [['ws_cpu_cores', '4'], ['ws_memory_gib', '8'], ['ws_storage_gib', '50']]) assert.match(f.host.innerHTML, new RegExp(`name="${name}"[^>]*value="${value}"`), name);
    assert.match(f.host.innerHTML, /data-preset="large" aria-current="true"/);
    // A preset stays editable: a typed value moves the pressed size away from it.
    fill(f, { ws_name: 'Sized', ws_cpu_cores: '3' }); f.submit('create'); f.state.next = [{ workspace: onMachine({ id: 'w-t', name: 'Sized', cpu_cores: 3, created_at: clock + 1 }) }]; f.click('confirm'); await flush();
    assert.equal(f.state.writes[0][1].cpu_cores, 3); assert.equal(f.state.writes[0][1].memory_mib, 8192);
    // An invalid value is replaced and its error cleared by a preset.
    f.click('create'); fill(f, { ws_name: 'Again', ws_cpu_cores: '64' }); f.submit('create'); assert.match(f.host.innerHTML, /data-ws-control="cpu_cores"[^>]*aria-invalid="true"/);
    f.click('preset', undefined, { preset: 'small' }); f.submit('create'); assert.doesNotMatch(f.host.innerHTML, /aria-invalid="true"/);
    f.state.next = [{ workspace: onMachine({ id: 'w-s', name: 'Again', cpu_cores: 1, memory_mib: 2048, storage_gib: 10, created_at: clock + 2 }) }]; f.click('confirm'); await flush();
    assert.deepEqual([f.state.writes[1][1].cpu_cores, f.state.writes[1][1].memory_mib, f.state.writes[1][1].storage_gib], [1, 2048, 10]);
    f.ui.stop();
    // No presets served: no group.
    const bare = fixture({ config: { ...options, presets: undefined } }); await bare.open(); bare.click('create'); assert.doesNotMatch(bare.host.innerHTML, /Start from a size/); bare.ui.stop();
  },

  async summary() {
    // The summary lists what is wrong, with a link to each field; it is no live region (focus announces it).
    const f = fixture(); await f.open(); f.click('create');
    fill(f, { ws_name: 'Box' }); f.input('ws_image_choice', 'custom');
    fill(f, { ws_image: 'bad image', ws_cpu_cores: '64' }); f.submit('create');
    const html = f.host.innerHTML;
    assert.match(html, /data-ws-summary tabindex="-1"><p>Fix these 2 fields, then review again:<\/p><ul role="list"><li><a href="#ws-field-image" data-ws-jump="image">Image: /);
    assert.match(html, /<li><a href="#ws-field-cpu" data-ws-jump="cpu_cores">CPU: Enter a number of cores from 0\.5 to 32\./);
    assert.doesNotMatch(html, /data-ws-summary[^>]*role=/, 'focus announces the summary; a live region would read it twice');
    // Once every field is valid a new attempt shows no summary at all.
    fill(f, { ws_cpu_cores: '2', ws_image: 'ubuntu:24.04' }); f.submit('create');
    assert.match(f.host.innerHTML, /Create “Box”\?/); assert.match(f.host.innerHTML, /data-ws-summary tabindex="-1"><\/div>/);
    f.ui.stop();
  },

  async 'server-errors'() {
    const f = fixture(); await f.open(); f.click('create');
    fill(f, { ws_name: 'Box' }); f.submit('create');
    f.state.next = [failure(400, { error: 'invalid_request', field: 'image', reason: 'characters' })]; f.click('confirm'); await flush();
    assert.match(f.host.innerHTML, /data-ws-form="create"/, 'back on the form');
    assert.match(text(f.host.innerHTML), /doesn't look like an image address/); assert.match(text(f.host.innerHTML), /Fix this field, then review again:/);
    assert.ok(f.focused.at(-1) === '[data-ws-summary]', 'a refusal focuses the summary, which links to the field');
    const again = async (failureValue, pattern) => { f.submit('create'); f.state.next = [failureValue]; f.click('confirm'); await flush(); assert.match(text(f.host.innerHTML), pattern); };
    await again(failure(409, { error: 'conflict', field: 'name', reason: 'name_taken' }), /That name is taken\. Choose another, for example Build box 2\./);
    await again(failure(400, { error: 'invalid_request', field: 'name', reason: 'no_letter' }), /Use at least one letter or digit in the name\./);
    await again(failure(400, { error: 'invalid_request', field: 'image', reason: 'registry_private' }), /local or private network/);
    await again(failure(400, { error: 'invalid_request', field: 'image', reason: 'registry_no_path' }), /Add the image name after the website name/);
    await again(failure(400, { error: 'invalid_request', field: 'private_registry', reason: 'unavailable' }), new RegExp(ws.PRIVATE_IMAGE.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ' Choose a public image\\.'));
    // A form-level refusal is the summary's, never pinned on the Name field.
    await again(failure(409, { error: 'conflict', field: 'request_id', reason: 'request_reused' }), /already sent with different values/);
    assert.equal((f.host.innerHTML.match(/aria-invalid="true"/g) || []).length, 0, 'no field is marked invalid for a form-level error'); assert.ok(f.focused.at(-1) === '[data-ws-summary]');
    await again(failure(409, { error: 'conflict', field: 'request_id', reason: 'request_discarded' }), /was discarded, so this form can't create it again/);
    // Each refusal has its own sentence; the cap is not "the workspace changed".
    await again(failure(409, { error: 'conflict', reason: 'limit_reached' }), /reached the limit of 100 cloud workspaces\. Discard one that was never started/);
    assert.doesNotMatch(text(f.host.innerHTML), /changed or the request conflicts/);
    await again(failure(400, { error: 'invalid_request', reason: 'unknown_field' }), /server refused these values/);
    await again(new Error('timeout'), /nothing is confirmed/);
    await again(failure(403, { error: 'owner_required', reason: 'member' }), /You can read this workspace's settings\. Only its owner can change them\./);
    f.ui.stop();
  },

  async edit() {
    const row = startedRow({ id: 'w-one', name: 'Build box' });
    const f = fixture({ list: { workspaces: [row], can_manage: true } }); await f.open();
    f.click('edit', 'w-one');
    let t = text(f.host.innerHTML);
    assert.ok(t.includes('Change “Build box”') && t.includes(ws.STARTED));
    assert.match(f.host.innerHTML, /<select name="ws_node_id"[^>]*disabled/, 'a started workspace cannot move'); assert.ok(t.includes("A started workspace can't move to another machine yet."), 'the reason sits beside the field');
    assert.doesNotMatch(f.host.innerHTML, /data-ws-id="w-one"/, 'the form replaces the row while it is open');
    assert.match(f.host.innerHTML, /value="2"/); assert.match(f.host.innerHTML, /value="4"/);
    f.submit('edit'); assert.match(text(f.host.innerHTML), /You haven't changed anything yet\./); assert.equal(f.state.writes.length, 0, 'an edit with no change sends nothing');
    fill(f, { ws_memory_gib: '8' }); f.submit('edit');
    t = text(f.host.innerHTML);
    assert.ok(t.includes('Confirm the change to “Build box”') && t.includes('Memory: 4 GiB → 8 GiB') && t.includes('2 CPU cores, 8 GiB memory, 20 GiB storage. Machine: Fixture machine') && t.includes(ws.STARTED));
    f.state.next = [{ workspace: { ...row, memory_mib: 8192, version: 2, revision: 2, restart_needed: true, status: 'restart_needed' } }]; f.click('confirm'); await flush();
    assert.equal(f.state.writes[0][0], '/api/compute/workspaces/w-one');
    assert.deepEqual(f.state.writes[0][1], { memory_mib: 8192, expected_revision: 1 }, 'only the changed field, with the version the form opened at');
    t = text(f.host.innerHTML);
    assert.ok(t.includes('Saved “Build box”. Changes waiting: they apply the next time it starts, and starting is not available yet.') && t.includes('8 GiB memory') && t.includes('Changes waiting'));
    // A stale version from the server says so, keeps what was typed (as typed), disables the real button and offers a way on beside it.
    f.click('edit', 'w-one'); fill(f, { ws_memory_gib: '6' }); f.submit('edit');
    f.state.next = [failure(409, { error: 'conflict', reason: 'stale_revision' })]; f.click('confirm'); await flush();
    assert.match(text(f.host.innerHTML), /Someone else changed this workspace\. Nothing was saved\. What you typed is still in the form below\./); assert.match(f.host.innerHTML, /data-ws-form="edit"/);
    assert.match(f.host.innerHTML, /data-ws-action="reload-edit">Reload to continue<\/button>/); assert.doesNotMatch(text(f.host.innerHTML), /copy it if you want to keep it|your typed values stay/, 'the banner says one thing: nothing saved, the form still holds it'); assert.match(f.host.innerHTML, /name="ws_memory_gib"[^>]*value="6"/, 'what was typed is still in the form');
    assert.match(f.host.innerHTML, /<button class="btn primary" type="submit" disabled>Review the change<\/button><button type="button" class="btn" data-ws-action="reload-edit">Reload to continue<\/button>/, 'a really disabled submit, with the way on right beside it');
    f.submit('edit'); assert.doesNotMatch(f.host.innerHTML, /Confirm the change/, 'a stale form cannot go on to review');
    // What the person typed stays in the form as typed: an empty field stays empty, and '3.50' stays '3.50'.
    f.input('ws_memory_gib', ''); f.input('ws_storage_gib', '25'); f.input('ws_cpu_cores', '3.50');
    f.state.list = { workspaces: [{ ...row, memory_mib: 8192, version: 3, revision: 3, restart_needed: true, status: 'restart_needed', updated_at: clock + 9 }], can_manage: true }; await f.ui.refresh(); await flush();
    assert.match(f.host.innerHTML, /name="ws_memory_gib"[^>]*value=""/); assert.match(f.host.innerHTML, /name="ws_cpu_cores"[^>]*value="3\.50"/); assert.doesNotMatch(text(f.host.innerHTML), /NaN/);
    f.ui.stop();
    // Never started: the editor says changes apply when it first starts.
    const fresh = fixture({ list: { workspaces: [record()], can_manage: true } }); await fresh.open(); fresh.click('edit', 'w-one');
    assert.ok(text(fresh.host.innerHTML).includes(ws.NOT_STARTED)); assert.doesNotMatch(fresh.host.innerHTML, /<select name="ws_node_id"[^>]*disabled/);
    fresh.ui.stop();
  },

  async conflict() {
    // Tab A opens the editor at version 1; tab B raises the CPU (version 2); A reloads the list and saves memory.
    const mine = onMachine({ id: 'w-one', version: 1, revision: 1, updated_at: clock });
    const f = fixture({ list: { workspaces: [mine], can_manage: true } }); await f.open();
    f.click('edit', 'w-one'); fill(f, { ws_memory_gib: '8' });
    f.state.list = { workspaces: [{ ...mine, cpu_cores: 8, version: 2, revision: 2, updated_at: clock + 5 }], can_manage: true };
    await f.ui.refresh(); await flush();
    // The form says so as soon as the list moved on, not only when the person tries to save.
    assert.match(text(f.host.innerHTML), /Someone else changed this workspace/); assert.match(f.host.innerHTML, /data-ws-action="reload-edit"/);
    assert.match(f.status.text, /changed while you had it open/, 'announced once through the status line, in a shorter sentence than the form message');
    f.submit('edit'); f.click('confirm'); await flush();
    assert.equal(f.state.writes.length, 0, 'a form opened before the list changed never sends');
    // Reload and start over: a fresh form on the current values, carrying the new version.
    f.state.list = { workspaces: [{ ...mine, cpu_cores: 8, version: 2, revision: 2, updated_at: clock + 5 }], can_manage: true };
    f.click('reload-edit'); await flush(); await flush();
    assert.match(f.host.innerHTML, /data-ws-form="edit"/); assert.doesNotMatch(f.host.innerHTML, /data-ws-action="reload-edit"/); assert.match(f.host.innerHTML, /name="ws_cpu_cores"[^>]*value="8"/, 'the form shows the current values');
    // What was typed waits behind one button; applying it puts back only the fields that were changed.
    assert.match(f.host.innerHTML, /data-ws-reapply/); assert.match(text(f.host.innerHTML), /Your earlier changes were not saved:.*Memory \(GiB\): 8/); assert.match(f.host.innerHTML, /name="ws_memory_gib"[^>]*value="4"/);
    f.click('reapply'); assert.doesNotMatch(f.host.innerHTML, /data-ws-reapply/); assert.match(f.host.innerHTML, /name="ws_memory_gib"[^>]*value="8"/); assert.match(f.host.innerHTML, /name="ws_cpu_cores"[^>]*value="8"/, 'the other person\'s CPU change is kept');
    f.submit('edit');
    f.state.next = [{ workspace: { ...mine, cpu_cores: 8, memory_mib: 8192, version: 3, revision: 3, updated_at: clock + 6 } }]; f.click('confirm'); await flush();
    assert.deepEqual(f.state.writes[0][1], { memory_mib: 8192, expected_revision: 2 });
    f.ui.stop();
    // A rename moves the version too: a form opened before it is stale even though nothing needs a restart.
    const renamed = fixture({ list: { workspaces: [mine], can_manage: true } }); await renamed.open(); renamed.click('edit', 'w-one'); fill(renamed, { ws_name: 'Mine' });
    renamed.state.list = { workspaces: [{ ...mine, name: 'Theirs', version: 2, updated_at: clock + 1 }], can_manage: true }; await renamed.ui.refresh(); await flush();
    renamed.submit('edit'); renamed.click('confirm'); await flush(); assert.equal(renamed.state.writes.length, 0, 'a stale rename is never sent');
    renamed.ui.stop();
    // A record deleted elsewhere while its form is open closes the form and says so.
    const gone = fixture({ list: { workspaces: [mine], can_manage: true } }); await gone.open(); gone.click('edit', 'w-one');
    gone.state.list = { workspaces: [], can_manage: true }; await gone.ui.refresh(); await flush();
    assert.doesNotMatch(gone.host.innerHTML, /data-ws-form/); assert.match(text(gone.host.innerHTML), /That cloud workspace is no longer available\./);
    gone.ui.stop();
  },

  async discard() {
    const never = onMachine({ id: 'w-one', name: 'Build box' }), started = startedRow({ id: 'w-two', name: 'Live box' });
    const f = fixture({ list: { workspaces: [never, started], can_manage: true } }); await f.open();
    f.click('discard', 'w-two'); assert.doesNotMatch(f.host.innerHTML, /Discard “Live box”\?/, 'a started workspace has no discard');
    f.click('discard', 'w-one');
    let t = text(f.host.innerHTML);
    assert.ok(t.includes('Discard “Build box”?') && t.includes('removes the saved settings for “Build box” from Commonplace') && t.includes('never started, so nothing on a machine is affected') && t.includes("You can't undo this"));
    // The confirmation lives inside the record's own row, with fixed labels (the name is in the sentence) and Keep as strong as Discard.
    const row = f.host.innerHTML.slice(f.host.innerHTML.indexOf('<li class="compute-row compute-ws-row" data-ws-id="w-one"'), f.host.innerHTML.indexOf('data-ws-id="w-two"'));
    assert.ok(row.includes('compute-ws-discard') && row.includes('<h3 class="compute-ws-name"'), 'the confirmation is inside the row of the record it is about');
    assert.match(row, /class="btn danger" data-ws-action="confirm">Discard workspace<\/button><button type="button" class="btn primary" data-ws-action="close">Keep it<\/button>/);
    assert.doesNotMatch(row, /btn danger solid|Discard Build box|Keep Build box/);
    assert.equal(f.state.writes.length, 0, 'nothing is sent before the confirmation');
    assert.ok(f.focused.includes('.compute-ws-actions [data-ws-action="close"]'), 'the safe choice, Keep, has focus');
    f.click('close'); assert.doesNotMatch(f.host.innerHTML, /Discard “Build box”\?/); assert.match(f.host.innerHTML, /data-ws-id="w-one"/);
    assert.ok(f.focused.includes('[data-ws-id="w-one"] [data-ws-action="discard"]'), 'focus returns to the button that opened it');
    // It started after the confirmation opened: the body is replaced by one sentence, Discard is gone and Keep has focus.
    f.click('discard', 'w-one'); f.state.next = [failure(409, { error: 'conflict', reason: 'already_started' })]; f.click('confirm'); await flush();
    t = text(f.host.innerHTML);
    assert.ok(t.includes('It started after you opened this, so nothing was discarded. Keep it, or reload the list to see its current state.'));
    assert.doesNotMatch(t, /never started, so nothing on a machine is affected|can't be discarded yet/, 'the refusal never contradicts itself');
    assert.doesNotMatch(f.host.innerHTML, /data-ws-action="confirm"|Discard workspace/, 'the red button is gone');
    assert.equal(f.focused.at(-1), '.compute-ws-actions [data-ws-action="close"]', 'focus stays on Keep it');
    assert.match(f.host.innerHTML, /data-ws-action="reload">Reload the list/);
    f.click('close');
    // A discard of a changed record is refused (stale): the button is really disabled, with its reason beside it, and offers the reload.
    f.click('discard', 'w-one'); f.state.next = [failure(409, { error: 'conflict', reason: 'stale_revision' })]; f.click('confirm'); await flush();
    assert.match(text(f.host.innerHTML), /changed after you opened this, so nothing was discarded/); assert.match(f.host.innerHTML, /data-ws-action="reload">Reload the list/);
    assert.match(f.host.innerHTML, /class="btn danger" data-ws-action="confirm" disabled aria-describedby="ws-summary"/, 'the confirm is disabled while the record is stale, and says why');
    f.click('close'); f.click('discard', 'w-one');
    f.state.next = [{ discarded: { id: 'w-one', name: 'Build box' } }]; f.click('confirm'); await flush();
    assert.deepEqual(f.state.writes.at(-1), ['/api/compute/workspaces/w-one/discard', { expected_revision: 1 }]);
    t = text(f.host.innerHTML);
    assert.ok(t.includes('Discarded “Build box”. Nothing on a machine was affected.')); assert.doesNotMatch(f.host.innerHTML, /data-ws-id="w-one"/); assert.match(f.host.innerHTML, /data-ws-id="w-two"/);
    assert.equal(f.focused.at(-1), '[data-ws-action="create"]', 'focus goes to the next sensible control, not to the status text');
    f.ui.stop();
  },

  async 'registry-hidden'() {
    // Registry logins have no custody purpose yet: no field, no checkbox, no bind button, no private flag on the wire.
    const legacy = record({ id: 'w-old', name: 'Legacy private', private_registry: true, status: 'needs_credential', node_id: 'n-one', machine_name: 'Fixture machine', machine_assigned: true, discardable: false });
    const f = fixture({ list: { workspaces: [legacy], can_manage: true } }); await f.open();
    assert.doesNotMatch(f.host.innerHTML, /data-ws-action="bind"/);
    assert.match(text(f.host.innerHTML), /Private image/);
    f.click('bind', 'w-old'); assert.doesNotMatch(f.host.innerHTML, /data-ws-form="bind"|ws_credential/);
    f.click('detail', 'w-old'); assert.ok(text(f.host.innerHTML).includes(ws.PRIVATE_IMAGE)); assert.match(text(f.host.innerHTML), /\(a private image\)/); assert.match(text(f.host.innerHTML), /Choose a public image\./); assert.match(f.host.innerHTML, /data-focus="image"[^>]*>Change image<\/button>/); f.click('close');
    f.click('create'); f.input('ws_image_choice', 'custom'); fill(f, valid); f.submit('create');
    f.state.next = [{ workspace: record({ id: 'w-n', name: 'Acceptance', created_at: clock + 1 }) }]; f.click('confirm'); await flush();
    assert.equal('private_registry' in f.state.writes[0][1], false); assert.equal(JSON.stringify(f.state.writes).includes('credential'), false);
    f.ui.stop();
    // A member is told the owner can fix it, with no button.
    const member = fixture({ owner: false, list: { workspaces: [{ ...legacy, node_id: null, machine_name: null }], can_manage: false } }); await member.open();
    assert.match(text(member.host.innerHTML), /Private image/); assert.doesNotMatch(member.host.innerHTML, /Change image/); member.click('detail', 'w-old');
    assert.match(text(member.host.innerHTML), /The workspace owner can choose a public image\./); member.ui.stop();
  },

  async member() {
    const row = onMachine({ discardable: false }); row.machine_name = null; row.node_id = null; row.private_registry = true; row.credential_bound = true;
    const f = fixture({ owner: false, list: { workspaces: [row], can_manage: false } }); await f.open();
    const t = text(f.host.innerHTML);
    assert.ok(t.includes("You can read this workspace's settings. Only its owner can change them."));
    assert.ok(t.includes('Build box') && t.includes('Saved, not started') && t.includes('2 CPU cores, 4 GiB memory, 20 GiB storage') && t.includes('Machine: assigned (name hidden)'));
    assert.ok(!t.includes('Fixture machine'), 'no machine name in the list'); assert.ok(t.includes('Image: ghcr.io/acme/tools:1.2'), 'the image address is shown to members too');
    assert.doesNotMatch(f.host.innerHTML, /data-ws-action="(create|edit|bind|discard)"|role="alert">Compute management/);
    assert.match(f.host.innerHTML, /data-ws-action="detail"/);
    f.click('create'); f.click('edit', 'w-one'); f.click('discard', 'w-one'); assert.doesNotMatch(f.host.innerHTML, /data-ws-form|Discard Build box/, 'a member can never open a form');
    f.click('detail', 'w-one'); const d = text(f.host.innerHTML);
    assert.ok(d.includes("You can read this workspace's settings. Only its owner can change them.") && d.includes('assigned (name hidden)') && !d.includes('Fixture machine') && !/Compute management requires/.test(d));
    assert.doesNotMatch(f.host.innerHTML, /data-ws-action="(edit|discard)"/);
    f.click('close'); assert.ok(f.focused.includes('[data-ws-id="w-one"] [data-ws-action="detail"]'), 'closing the details returns focus to the row');
    // Reload inventory keeps the member's section on screen all along (it used to vanish until the owner-only read answered).
    let seen = [];
    const original = Object.getOwnPropertyDescriptor(f.host, 'innerHTML');
    Object.defineProperty(f.host, 'innerHTML', { get: original.get, set(value) { seen.push(value); original.set(value); } });
    f.dispatch('click', { target: { closest: selector => selector === '[data-compute-action]' ? { dataset: { computeAction: 'refresh', id: '' }, disabled: false } : null } }); await flush();
    assert.ok(seen.length >= 2 && seen.every(html => html.includes('data-ws-root')), 'every repaint during Reload inventory still has the cloud workspaces section');
    assert.match(text(f.host.innerHTML), /Build box/);
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
    assert.match(text(down.host.innerHTML), /Nothing here yet/);
    assert.equal(down.status.text, 'Cloud workspaces reloaded.'); assert.ok(down.focused.includes('#compute-workspaces') && !down.focused.includes('[data-ws-notice]'), 'focus goes to the section heading after Reload, never to the status text');
    down.ui.stop();
    // The image list failing says why creation is unavailable instead of hiding the button silently.
    const noOptions = fixture({ config: failure(500, { error: 'x' }) }); await noOptions.open();
    assert.match(text(noOptions.host.innerHTML), /The image list could not be loaded, so new workspaces cannot be created right now/);
    assert.doesNotMatch(noOptions.host.innerHTML, /data-ws-action="create"/); noOptions.click('create'); assert.doesNotMatch(noOptions.host.innerHTML, /data-ws-form/);
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
    // Escape closes an untouched form and puts focus back on the button that opened it.
    f.click('edit', 'w-one'); assert.match(f.host.innerHTML, /data-ws-form="edit"/);
    assert.equal(f.key('Escape', false).prevented, false, 'Escape outside the section is not ours');
    assert.equal(f.key('Escape').prevented, true); assert.doesNotMatch(f.host.innerHTML, /data-ws-form/);
    assert.ok(f.focused.includes('[data-ws-id="w-one"] [data-ws-action="edit"]'));
    f.click('create'); f.key('Escape'); assert.doesNotMatch(f.host.innerHTML, /data-ws-form/); assert.equal(f.focused.at(-1), '[data-ws-action="create"]');
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

  async escape() {
    const f = fixture({ list: { workspaces: [onMachine()], can_manage: true } }); await f.open();
    // From the review, Escape goes back to the form with the typed values, not out of it.
    f.click('edit', 'w-one'); fill(f, { ws_memory_gib: '8' }); f.submit('edit'); assert.match(text(f.host.innerHTML), /Confirm the change to “Build box”/);
    f.key('Escape'); assert.match(f.host.innerHTML, /data-ws-form="edit"/); assert.match(f.host.innerHTML, /name="ws_memory_gib"[^>]*value="8"/); assert.equal(f.focused.at(-1), '#compute-ws-heading');
    // From a changed form, Escape (and Cancel) ask before throwing the typed values away; Escape again keeps editing.
    f.key('Escape'); assert.match(f.host.innerHTML, /Leave without saving\?/); assert.match(f.host.innerHTML, /data-ws-form="edit"/, 'the form is still there'); assert.equal(f.focused.at(-1), '[data-ws-action="keep-editing"]', 'the safe choice has focus');
    f.key('Escape'); assert.doesNotMatch(f.host.innerHTML, /Leave without saving\?/); assert.match(f.host.innerHTML, /name="ws_memory_gib"[^>]*value="8"/);
    f.click('close'); assert.match(f.host.innerHTML, /Leave without saving\?/); f.click('keep-editing'); assert.doesNotMatch(f.host.innerHTML, /Leave without saving\?/);
    f.click('close'); f.click('leave'); assert.doesNotMatch(f.host.innerHTML, /data-ws-form/); assert.ok(f.focused.includes('[data-ws-id="w-one"] [data-ws-action="edit"]'));
    // A form that was not changed closes at once; typing something and undoing it counts as not changed.
    f.click('create'); f.key('Escape'); assert.doesNotMatch(f.host.innerHTML, /data-ws-form|Leave without saving/);
    f.click('create'); fill(f, { ws_name: 'x' }); fill(f, { ws_name: '' }); f.key('Escape'); assert.doesNotMatch(f.host.innerHTML, /data-ws-form|Leave without saving/);
    f.click('create'); fill(f, { ws_name: 'Typed' }); f.key('Escape'); assert.match(f.host.innerHTML, /Leave without saving\?/); f.click('leave'); assert.doesNotMatch(f.host.innerHTML, /data-ws-form/);
    f.ui.stop();
  },

  async 'validation-timing'() {
    // A problem is shown when a field is committed (change or blur) or on submit, never while the person is still typing.
    // (The harness paints the live error slots through stubs, so a full repaint shows what the state holds.)
    const f = fixture(); await f.open(); f.click('create');
    const repaint = async () => { await f.ui.refresh(); await flush(); return f.host.innerHTML; };
    f.input('ws_image_choice', 'custom'); fill(f, { ws_name: 'Box' });
    for (const half of ['g', 'gh', 'ghcr', 'ghcr.', 'ghcr.i']) { f.input('ws_image', half); assert.doesNotMatch(await repaint(), /aria-invalid="true"/, `half-typed ${half} holds no error`); }
    f.input('ws_cpu_cores', '64'); assert.doesNotMatch(await repaint(), /aria-invalid="true"/, 'a bad number holds no error while it is typed');
    f.change('ws_cpu_cores', '64'); assert.match(await repaint(), /aria-invalid="true"/, 'committing the value records its problem'); assert.match(text(f.host.innerHTML), /Enter a number of cores from 0\.5 to 32\./);
    // Fixing a recorded problem clears it, per keystroke.
    f.input('ws_cpu_cores', '4'); assert.doesNotMatch(await repaint(), /aria-invalid="true"/);
    f.change('ws_image', 'ghcr.io'); assert.match(await repaint().then(text), /Add the image name after the website name/);
    f.change('ws_image', 'ghcr.io/acme/tools:1.2'); assert.doesNotMatch(await repaint().then(text), /Add the image name/);
    f.ui.stop();
  },

  async 'image-choice'() {
    // The address box is toggled with `hidden`, not repainted: nothing re-centres the page when the choice changes.
    const f = fixture(); await f.open(); f.click('create');
    assert.match(f.host.innerHTML, /<div class="compute-field compute-wide" data-ws-field="image" hidden>/);
    f.focused.length = 0; f.change('ws_image_choice', 'custom');
    assert.doesNotMatch(f.host.innerHTML, /data-ws-field="image" hidden/); assert.equal(f.focused.length, 0, 'choosing an image moves no focus and scrolls nothing');
    f.change('ws_image_choice', 'docker.io/library/python:3.12-slim'); assert.match(f.host.innerHTML, /data-ws-field="image" hidden/);
    assert.match(f.host.innerHTML, /<option value="docker\.io\/library\/python:3\.12-slim" selected>/); assert.ok(text(f.host.innerHTML).includes('Linux with Python 3.12 ready to use.'));
    // The review names the chosen image first and shows the address second.
    f.change('ws_image_choice', 'docker.io/library/ubuntu:24.04'); fill(f, { ws_name: 'Box' }); f.submit('create');
    assert.ok(text(f.host.innerHTML).includes('Image: Ubuntu 24.04 (docker.io/library/ubuntu:24.04)'));
    f.ui.stop();
  },

  async busy() {
    // Saving keeps the button that was pressed (no disabled attribute that would drop focus) and says it is busy once.
    const f = fixture(); await f.open(); f.click('create'); fill(f, valid); f.submit('create');
    let resolve; f.state.next = [() => new Promise(done => { resolve = done; })];
    const api = f.state; const original = f.calls.length;
    f.click('confirm');
    const during = f.host.innerHTML;
    assert.match(during, /aria-busy="true"/); assert.match(during, /data-ws-action="confirm" aria-disabled="true">Saving…<\/button>/); assert.doesNotMatch(section(during), /<button[^>]*\sdisabled[\s>]/, 'no disabled attribute: it would drop the focus');
    assert.equal(f.status.text, 'Saving…', 'the status region says it');
    f.click('confirm'); assert.equal(f.calls.length, original + 1, 'a second press while saving sends nothing');
    resolve(structuredClone({ workspace: record({ id: 'w-b', name: 'Acceptance', created_at: clock + 1 }) })); await flush();
    assert.match(f.status.text, /^Saved “Acceptance”/); assert.ok(api.writes.length === 1);
    f.ui.stop();
  },

  // With no machine anywhere the screen says so once and never tells the owner to choose one that cannot be chosen.
  async 'no-machines'() {
    const none = fixture({ list: { workspaces: [record({ id: 'w-none', name: 'No box' })], can_manage: true }, nodes: [] }); await none.open();
    none.click('detail', 'w-none');
    const detail = fixtureHTML => { const at = fixtureHTML.indexOf('compute-ws-statusline'); return text(fixtureHTML.slice(at, fixtureHTML.indexOf('</p>', at))); };
    let t = detail(none.host.innerHTML);
    assert.ok(t.includes(ws.NO_MACHINES_SENTENCE), 'the dead end is explained: nothing to choose, starting will need one');
    assert.doesNotMatch(t, /Choose a machine/, 'no instruction that cannot be followed');
    none.click('edit', 'w-none');
    const form = none.host.innerHTML.slice(none.host.innerHTML.indexOf('data-ws-field="node_id"'));
    assert.ok(text(form).includes('No machines set up yet'), 'the Machine field says there are none');
    assert.match(form, /<select name="ws_node_id"[^>]*disabled/, 'nothing to pick');
    assert.doesNotMatch(text(form.slice(0, form.indexOf('data-ws-error'))), /Choose a machine|Advanced|Targets/, 'no instruction and no pointer to SSH settings'); assert.doesNotMatch(form, /<option[^>]*>Choose later/, 'the empty field offers no choice');
    none.ui.stop();
    // With a machine the instruction is back, and the field offers "Choose later" and the machine.
    const some = fixture({ list: { workspaces: [record({ id: 'w-some', name: 'Some box' })], can_manage: true } }); await some.open();
    some.click('detail', 'w-some'); t = detail(some.host.innerHTML);
    assert.ok(t.includes('Choose a machine for it with Change settings.') && !t.includes(ws.NO_MACHINES_SENTENCE));
    some.click('edit', 'w-some');
    const field = some.host.innerHTML.slice(some.host.innerHTML.indexOf('data-ws-field="node_id"'));
    assert.ok(text(field).includes('Choose later') && text(field).includes('Fixture machine') && !text(field).includes('No machines set up yet'));
    assert.doesNotMatch(field, /<select name="ws_node_id"[^>]*disabled/);
    some.ui.stop();
    // A member is never told to act: the owner chooses.
    const member = fixture({ list: { workspaces: [record({ id: 'w-m', name: 'M box' })], can_manage: false }, owner: false, nodes: [] }); await member.open();
    member.click('detail', 'w-m'); t = detail(member.host.innerHTML);
    assert.ok(t.includes('The workspace owner can choose a machine.') && !/Change settings|Choose later/.test(t));
    member.ui.stop();
  },
  // The states a start would produce are worded as what a machine recorded; "Starting is not available yet" is said once per screen.
  async 'once-per-screen'() {
    const rows = [record({ id: 'a', name: 'A none' }), onMachine({ id: 'b', name: 'B pending' }), onMachine({ id: 'c', name: 'C changes', status: 'restart_needed', started: true, applied_revision: 1, revision: 2, restart_needed: true, discardable: false }), startedRow({ id: 'd', name: 'D ready' }), onMachine({ id: 'e', name: 'E failed', status: 'failed', status_reason: 'start_failed', started: true, discardable: false })];
    const f = fixture({ list: { workspaces: rows, can_manage: true } }); await f.open();
    const said = () => (text(f.host.innerHTML).match(/starting is not available yet/gi) || []).length;
    assert.ok(said() <= 1, `the list says it ${said()} times`);
    for (const id of ['a', 'b', 'c']) { f.click('detail', id); assert.ok(said() <= 1, `detail ${id} says it ${said()} times`); f.click('close'); }
    f.click('create'); assert.ok(said() <= 1, `the create form says it ${said()} times`); f.input('ws_name', 'Once'); f.click('close'); f.click('leave');
    f.click('edit', 'a'); assert.ok(said() <= 1, `the edit form says it ${said()} times`);
    f.input('ws_cpu_cores', '4'); f.submit('edit'); assert.ok(said() <= 1, `the review says it ${said()} times`);
    f.ui.stop();
    // No screen claims that anything started, failed or is up to date as a fact of this page.
    const all = fixture({ list: { workspaces: rows, can_manage: true } }); await all.open();
    for (const id of ['d', 'e', 'c']) { all.click('detail', id); const t = text(all.host.innerHTML); assert.doesNotMatch(t, /Up to date|Its last start failed|Its settings are up to date|^Started\./, id); all.click('close'); }
    const list = text(all.host.innerHTML);
    assert.doesNotMatch(list, /Up to date/); assert.match(list, /Settings applied/);
    assert.doesNotMatch(list, /isolated environment|Planned machine/);
    all.ui.stop();
  },
  // A refused discard says in the row's own status that the record moved on, in the same render.
  async 'refused-row'() {
    const f = fixture({ list: { workspaces: [onMachine({ id: 'w-one', name: 'Build box' })], can_manage: true } }); await f.open();
    f.click('discard', 'w-one'); assert.match(f.host.innerHTML, /compute-ws-status" data-tone="quiet">Saved, not started</);
    f.state.next = [failure(409, { error: 'conflict', reason: 'already_started' })]; f.click('confirm'); await flush();
    const row = f.host.innerHTML.slice(f.host.innerHTML.indexOf('data-ws-id="w-one"'));
    assert.ok(text(row).includes('It started after you opened this'), 'the refusal is shown');
    assert.doesNotMatch(row, /Saved, not started/, 'the status above it no longer says the opposite');
    assert.match(row, /compute-ws-status" data-tone="wait">Started since you opened this</); assert.doesNotMatch(row, /Changed since you opened it/);
    f.ui.stop();
  },
  // Review minors of the cloud workspaces card (#621): copy that agrees with itself, one voice per message, a Close in the header.
  async 'minors-621'() {
    const mine = onMachine({ id: 'w-one', version: 1, revision: 1, updated_at: clock });
    // Stale edit: nothing saved, the form still holds what was typed; after the reload the status line says the same and names the button.
    const f = fixture({ list: { workspaces: [mine], can_manage: true } }); await f.open();
    f.click('edit', 'w-one'); fill(f, { ws_memory_gib: '8' });
    f.state.list = { workspaces: [{ ...mine, cpu_cores: 8, version: 2, revision: 2, updated_at: clock + 5 }], can_manage: true };
    await f.ui.refresh(); await flush();
    assert.match(text(f.host.innerHTML), /Nothing was saved\. What you typed is still in the form below\./);
    f.click('reload-edit'); await flush(); await flush();
    assert.equal(f.status.text, 'Reloaded. Your earlier changes were not saved. Apply my changes again puts them back.');
    f.ui.stop();
    // Refused discard: the row says it started, and the reason is the description of the button focus lands on.
    const d = fixture({ list: { workspaces: [onMachine({ id: 'w-one', name: 'Build box' })], can_manage: true } }); await d.open();
    d.click('discard', 'w-one'); d.state.next = [failure(409, { error: 'conflict', reason: 'already_started' })]; d.click('confirm'); await flush();
    assert.match(d.host.innerHTML, /<p id="ws-discard-reason">It started after you opened this/); assert.match(d.host.innerHTML, /data-ws-action="close" aria-describedby="ws-discard-reason">Keep it</);
    assert.match(d.host.innerHTML, /Started since you opened this</); d.ui.stop();
    // One term for "no machine": the label says not chosen yet; "Choose later" is only the option of the select.
    const none = fixture({ list: { workspaces: [record({ id: 'w-none', name: 'No box' })], can_manage: true }, nodes: [] }); await none.open();
    assert.doesNotMatch(text(section(none.host.innerHTML)), /No machine chosen|Planned machine/); assert.match(none.host.innerHTML, /Machine: not chosen yet/);
    none.click('edit', 'w-none'); const machineField = text(none.host.innerHTML.slice(none.host.innerHTML.indexOf('data-ws-field="node_id"'))); assert.doesNotMatch(machineField, /Choose later/, 'the disabled field is not described as a choice'); assert.match(machineField, /No machines set up yet.*none can be chosen/);
    // The address box is an address, in both its label and its hint.
    assert.match(none.host.innerHTML, /Image address/); assert.match(none.host.innerHTML, /The address of the image/); none.ui.stop();
    // The review says when the chosen machine is not verified (and stays quiet when it is).
    const unverified = fixture({ list: { workspaces: [record({ id: 'w-r', name: 'R box' })], can_manage: true } }); await unverified.open();
    unverified.click('edit', 'w-r'); unverified.change('ws_node_id', 'n-one'); unverified.submit('edit');
    assert.match(text(unverified.host.innerHTML), /Fixture machine is not verified yet, so nothing can start on it\. Your settings are still saved\./); unverified.ui.stop();
    const verified = fixture({ list: { workspaces: [record({ id: 'w-r', name: 'R box' })], can_manage: true }, nodes: [{ ...machine, state: 'verified' }] }); await verified.open();
    verified.click('edit', 'w-r'); verified.change('ws_node_id', 'n-one'); verified.submit('edit');
    assert.doesNotMatch(text(verified.host.innerHTML), /not verified/); verified.ui.stop();
    // One sentence for private images wherever they come up.
    const priv = fixture({ list: { workspaces: [onMachine({ id: 'w-p', name: 'P box', status: 'needs_credential', private_registry: true })], can_manage: true } }); await priv.open();
    priv.click('detail', 'w-p'); assert.ok(text(priv.host.innerHTML).includes(ws.PRIVATE_IMAGE)); assert.doesNotMatch(text(priv.host.innerHTML), /which can't be added yet|sign-ins for private images/); priv.click('close');
    priv.click('edit', 'w-p'); priv.state.next = [failure(409, { error: 'conflict', reason: 'registry_logins_unavailable' })]; priv.change('ws_name', 'P box 2'); priv.submit('edit'); priv.click('confirm'); await flush();
    assert.ok(text(priv.host.innerHTML).includes(ws.PRIVATE_IMAGE)); priv.ui.stop();
    // The detail has its Close in the header (the first control after the name), and the list is a list.
    const g = fixture({ list: { workspaces: [onMachine({ id: 'w-one', name: 'Build box' })], can_manage: true } }); await g.open();
    assert.match(g.host.innerHTML, /<ul class="compute-list compute-ws-list" role="list">/);
    g.click('detail', 'w-one');
    const detail = g.host.innerHTML.slice(g.host.innerHTML.indexOf('compute-ws-detail'));
    assert.match(detail, /<header><h3 id="compute-ws-heading" tabindex="-1">Build box<\/h3><button type="button" class="btn" data-ws-action="close" aria-label="Close details of Build box">Close<\/button><\/header>/);
    assert.equal((detail.match(/data-ws-action="close"/g) || []).length, 1, 'one Close, in the header'); g.ui.stop();
    const member = fixture({ list: { workspaces: [record({ id: 'w-m', name: 'M box' })], can_manage: false }, owner: false, nodes: [] }); await member.open();
    member.click('detail', 'w-m'); assert.match(member.host.innerHTML.slice(member.host.innerHTML.indexOf('compute-ws-detail')), /<header><h3[^>]*>M box<\/h3><button type="button" class="btn" data-ws-action="close"/); member.ui.stop();
    // A failed load is one alert with the sentence, and a failed reload does not say it a second time through the status line.
    const down = fixture({ list: failure(500, { error: 'x' }) }); await down.open();
    assert.equal((detail.match(/data-ws-action="close"/g) || []).length, 1, "one Close, in the header"); g.ui.stop();
    down.click('reload'); await flush(); assert.equal(alerts(section(down.host.innerHTML)), 1); assert.equal(down.status.text, '', 'the alert speaks; the status line stays silent'); down.ui.stop();
    // Server refusal while a request waits has its own sentence.
    const pending = fixture({ list: { workspaces: [onMachine({ id: 'w-q', name: 'Q box' })], can_manage: true } }); await pending.open();
    pending.click('edit', 'w-q'); fill(pending, { ws_memory_gib: '8' }); pending.submit('edit');
    pending.state.next = [failure(409, { error: 'conflict', reason: 'request_pending' })]; pending.click('confirm'); await flush();
    assert.match(text(pending.host.innerHTML), /A request for this workspace is waiting to be handled/); assert.doesNotMatch(text(pending.host.innerHTML), /Saving failed/); pending.ui.stop();
    // History: a start with nothing applied yet is not a failure; a failed record is.
    const h = fixture({ list: { workspaces: [onMachine({ id: 'w-s', name: 'Started', started: true, status: 'pending', applied_revision: null, discardable: false }), onMachine({ id: 'w-f', name: 'Failed', started: true, status: 'failed', status_reason: 'start_failed', applied_revision: 1, discardable: false })], can_manage: true } }); await h.open();
    const inDetail = html => { const at = html.indexOf('compute-ws-detail'); return text(html.slice(at, html.indexOf('</section>', at))); };
    h.click('detail', 'w-s'); const started = inDetail(h.host.innerHTML); assert.doesNotMatch(started, /The last recorded start failed/); assert.match(started, /A start was recorded; no machine has recorded the saved settings as applied yet\./); h.click('close');
    h.click('detail', 'w-f'); assert.match(inDetail(h.host.innerHTML), /History[^]*The last recorded start failed\./); h.ui.stop();
  },
  // One tone per status, one machine label, and a record whose image cannot be read still lists.
  async 'status-tone'() {
    const f = fixture({ list: { workspaces: [record({ id: 'a', name: 'A none' }), onMachine({ id: 'b', name: 'B pending' }), record({ id: 'c', name: 'C bad', image: '', image_registry: null, image_unreadable: true })], can_manage: true } }); await f.open();
    const html = f.host.innerHTML;
    for (const id of ['a', 'b']) assert.match(html.slice(html.indexOf(`data-ws-id="${id}"`)), /compute-ws-status" data-tone="quiet">Saved, not started</, `${id}: one colour for one status`);
    assert.doesNotMatch(html, /Planned machine/); assert.match(html, /Machine: Fixture machine/); assert.match(html, /Machine: not chosen yet/);
    const bad = html.slice(html.indexOf('data-ws-id="c"'));
    assert.match(bad, />Image unreadable</); assert.match(bad, /Image: not readable/);
    f.click('detail', 'c'); assert.match(text(f.host.innerHTML), /The saved image address can't be read\. Choose the image again with Change settings\./); assert.doesNotMatch(f.host.innerHTML, /Comes from[^<]*<\/dt><dd>null/);
    f.ui.stop();
  },

  // A background reload repaints the top part (heading and New button) too: focus that was on it comes back.
  async 'background-reload-focus'() {
    const focused = [];
    const stub = path => ({ focus() { focused.push(path); }, setAttribute() {}, removeAttribute() {}, querySelector: child => stub(`${path} ${child}`) });
    const top = { innerHTML: '' }, root = { innerHTML: '' };
    const control = { matches: () => false, dataset: { wsAction: 'create' }, closest: () => null, focus() {} };
    const host = { querySelector: selector => selector === '[data-ws-root]' ? root : selector === '[data-ws-top]' ? top : selector === '.compute-ws :focus' ? control : stub(selector) };
    const request = async path => path.endsWith('/images') ? options : { workspaces: [record()], total: 1, can_manage: true };
    const w = ws.createWorkspaces({ request, epoch: () => 1, valid: () => true, denied: () => false, rerender() {}, host: () => host, machines: () => [machine], requestId: () => 'r-1', doc: null });
    await w.load();
    assert.ok(top.innerHTML.includes('New cloud workspace'), 'the top part was painted');
    assert.ok(focused.includes('[data-ws-action="create"]'), 'focus on the New button comes back after the repaint');
    w.reset();
  },
  // A ready-made image has no address box: the error is shown on and read from the list the person can see.
  async 'curated-image-error'() {
    const f = fixture(); await f.open();
    f.click('create');
    f.state.next = [failure(400, { error: 'invalid_request', field: 'image', reason: 'not_curated' })];
    f.input('ws_name', 'Curated'); f.submit('create'); f.click('confirm'); await flush();
    const html = f.host.innerHTML, choice = html.slice(html.indexOf('data-ws-field="image_choice"'), html.indexOf('data-ws-field="image"'));
    assert.match(choice, /<select name="ws_image_choice"[^>]*aria-invalid="true"/, 'the visible list is marked invalid');
    assert.match(choice, /id="ws-error-image_choice"[^>]*>Pick one of the listed images/, 'its own message slot carries the text');
    const address = html.slice(html.indexOf('data-ws-field="image"'), html.indexOf('data-ws-field="image"') + 900);
    assert.doesNotMatch(address, /aria-invalid="true"/, 'the hidden address box is not the one marked');
    f.ui.stop();
  },
};

const selection = process.argv[2];
assert.ok(cases[selection], `Unknown workspace UI case: ${selection}`);
await cases[selection](); console.log(`Compute workspaces UI: ${selection} passed`);
