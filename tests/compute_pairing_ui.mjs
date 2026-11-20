import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// Add a machine on the Compute screen: the production modules and the production Compute controller run against a
// synthetic in-memory server and a manual clock. No socket, browser, timer or backend is opened, and nothing sleeps:
// a check on the machine runs when the test calls tick().
const source = name => readFileSync(new URL(`../commonplace/web/static/${name}.js`, import.meta.url), 'utf8');
const moduleURL = text => `data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
const apiURL = moduleURL(source('api'));
const rewrite = (text, extra = {}) => Object.entries({ './api.js': apiURL, ...extra }).reduce((out, [name, url]) => out.replace(new RegExp(`(['"])${name.replace(/[./]/g, '\\$&')}\\1`, 'g'), () => JSON.stringify(url)), text);
const wingURL = moduleURL(source('wing-label'));
const pairingURL = moduleURL(rewrite(source('compute-pairing'), { './wing-label.js': wingURL }));
const pr = await import(pairingURL);
const workspacesURL = moduleURL(rewrite(source('compute-workspaces')));
const overviewURL = moduleURL(rewrite(source('compute-overview')));
const { createCompute } = await import(moduleURL(rewrite(source('compute'), { './compute-workspaces.js': workspacesURL, './compute-overview.js': overviewURL, './compute-pairing.js': pairingURL })));
const flush = async () => { for (let i = 0; i < 80; i++) await Promise.resolve(); };
const clock = 1800000000;
const failure = (status, payload) => Object.assign(new Error(payload?.error || String(status)), { status, payload });
const text = html => html.replace(/<[^>]*>/g, ' ').replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/\s+/g, ' ');
const STAMP = '2026-11-19T12:00:00Z';
const COMMAND = " printf '%s' 'SyntheticCodeSentinel_0123456789abcdefghijk' | sh -c \"$(curl -fsSL 'https://hub.example.test/machine-setup.sh')\" machine-setup 'ENVELOPE'";
const FINGERPRINT = '0123abcd 4567ef01 89abcdef 01234567 89abcdef 01234567 89abcdef 01234567';

const reasons = {
  required: { text: 'This is required.', next: 'Fill it in.' },
  invalid: { text: 'This value is not allowed.', next: 'Change it and try again.' },
  setup_off: { text: 'Machine setup is turned off for this workspace.', next: 'Turn machine setup on, then add the machine.' },
  stale_version: { text: 'This machine changed since you opened it.', next: 'Reload to see where it is now.' },
  identity_changed: { text: 'The machine or its settings changed since you looked.', next: 'Reload and compare again before approving.' },
  label_in_use: { text: 'A machine or a waiting setup already uses this name.', next: 'Choose another name, or cancel the setup that uses it.' },
  kind_unavailable: { text: 'Script targets cannot be set up from the app yet.', next: 'Choose a runner machine.' },
  rate_limited: { text: 'Too many tries in a minute.', next: 'Wait a minute and try again.' },
};
const ownerOptions = {
  kinds: [
    { id: 'runner', label: 'Runner machine', description: 'A machine that runs Coworkers.', available: true },
    { id: 'target', label: 'Script target', description: 'A machine that runs approved scripts over SSH.', available: false, reason: 'kind_unavailable' },
  ],
  setup_enabled: true, code_minutes: 15, offline_after_seconds: 900, reasons, steps: ['prerequisites', 'files', 'service', 'check'],
  defaults: { max_runtime_mode: 'approval-required', max_in_flight: 1, max_starts_per_hour: 6 }, modes: ['approval-required', 'auto', 'auto-accept-edits', 'full-access'],
  wings: ['shared', 'project_compute'],
  copy_from: [{ id: 'mach_0123456789abcdef', label: 'night-runner', projects: ['demo'], topics: ['worker'], wings: ['shared'], requesters: ['conn_a', 'conn_b'], max_runtime_mode: 'approval-required', max_in_flight: 2, max_starts_per_hour: 6 }],
};
const memberOptions = { kinds: ownerOptions.kinds, setup_enabled: true, reasons, steps: ownerOptions.steps };
const steps = (...states) => ['prerequisites', 'files', 'service', 'check'].map((step, index) => ({ step, state: states[index] || 'pending', ...(states[index] === 'failed' ? { cause: 'service_failed' } : {}), ...(states[index] ? { reported_at: STAMP } : {}) }));
const sentences = {
  waiting: ['Waiting for the machine', 'The code is ready. Nothing has connected yet.', 'Run the command on the machine. The code stops working after 15 minutes.'],
  connected: ['Machine connected, waiting for your approval', 'The machine used the code and connected. It cannot do anything until you approve it.', "Compare the machine's name and fingerprint, then approve or choose This is not my machine."],
  approved: ['Approved', 'You approved this machine. It is finishing its setup.', 'Nothing to do here. This page updates when the machine reports that its checks passed.'],
  ready: ['Set up and checked', 'The machine passed its setup checks. This page cannot see whether it is switched on right now.', 'This version only checks the machine: Coworkers do not run on it yet. To get ready for later, sign in a provider and confirm this machine, both in Needs me.'],
  failed: ['A step failed', 'A setup step failed on the machine.', 'Open it to see the cause, then choose Set up again.'],
  expired: ['Code expired', 'The code was not used within 15 minutes and no longer works.', 'Choose Set up again for a new command.'],
  cancelled: ['Cancelled', 'This setup was cancelled before the machine connected. The code no longer works.', 'Choose Set up again for a new command.'],
  declined: ['Not approved', 'You said this is not your machine. Its access was removed.', 'Choose Set up again if you want to add a machine with the same settings.'],
  abandoned: ['Setup removed', 'This setup was removed before it finished. Its access was removed.', 'Choose Set up again to add the machine with the same settings.'],
  revoked: ['Access removed', "This machine's access was removed.", 'Choose Set up again to add it back.'],
  offline: ['Offline', 'This machine sent signs of life before, and nothing has been heard for over 15 minutes.', 'Check that the machine is on and online. It returns to Set up and checked by itself.'],
};
const grant = { projects: ['demo'], topics: ['worker'], wings: ['shared'], requesters: ['conn_a'], max_runtime_mode: 'approval-required', max_in_flight: 1, max_starts_per_hour: 6 };
function item(state, extra = {}) {
  const [label, sentence, next] = sentences[state];
  const sheet = ['connected', 'approved', 'ready', 'failed', 'offline'].includes(state);
  const stepStates = { waiting: [], connected: ['ok', 'ok', 'ok', 'ok'], approved: ['ok', 'ok'], ready: ['ok', 'ok', 'ok', 'ok'], failed: ['ok', 'ok', 'failed'], offline: ['ok', 'ok', 'ok', 'ok'] }[state] || [];
  return {
    id: 'pair_0123456789abcdef', kind: 'runner', label: 'build-box', state, state_label: label, sentence, next, cause: state === 'failed' ? { step: 'service', code: 'service_failed', text: 'The background service did not start.', next: 'The window where you ran the command shows the reason on its last lines. Then choose Set up again.' } : null,
    steps: steps(...stepStates), last_report_at: sheet ? STAMP : null, created_at: STAMP, expires_at: '2026-11-19T12:15:00Z', version: 1, fingerprint: sheet ? FINGERPRINT : null, grant,
    identity_digest: sheet ? 'f'.repeat(64) : null, accepted_at: ['approved', 'ready', 'offline'].includes(state) ? STAMP : null, binding_state: ['approved', 'ready', 'offline'].includes(state) ? 'unbound' : null,
    can_accept: state === 'connected', can_close: ['waiting', 'connected', 'approved', 'ready', 'offline', 'failed'].includes(state),
    retry: ['expired', 'cancelled', 'declined', 'abandoned', 'revoked', 'failed'].includes(state) ? { kind: 'runner', label: 'build-box', ...grant } : null, ...extra,
  };
}
const memberNext = { connected: "Waiting for the owner's approval.", failed: 'A setup step failed on this machine. The owner can set it up again.' };
const memberView = row => ({ ...Object.fromEntries(['id', 'kind', 'label', 'state', 'state_label', 'sentence', 'next', 'cause', 'steps', 'last_report_at', 'created_at', 'expires_at'].map(key => [key, row[key]])), next: memberNext[row.state] || row.next });

function fixture({ pairings = [], owner = true, setup = true, options = owner ? ownerOptions : memberOptions } = {}) {
  let html = '', hash = '#/compute';
  const user = { workspace: 'w', actor: 'user:u', can_admin: true };
  const listeners = new Map(), calls = [], doc = new EventTarget(), events = new EventTarget(), focused = [], status = { writes: [], copy: [] }, timers = new Map(), copied = [];
  doc.hidden = false;
  const stub = path => ({ focus() { focused.push(path); }, setAttribute() {}, removeAttribute() {}, scrollIntoView() {}, set textContent(value) { if (path === '[data-pr-notice]') status.writes.push(value); if (path === '[data-pr-copy-note]') status.copy.push(value); }, get textContent() { return ''; }, querySelector: child => stub(`${path} ${child}`) });
  // With roots mounted the section repaints only its own parts, and the counters show what a check really rewrote.
  const mounted = { on: false, rootWrites: 0, topWrites: 0, clock: [] };
  const root = { get innerHTML() { return ''; }, set innerHTML(value) { mounted.rootWrites++; } };
  const top = { get innerHTML() { return 'x'; }, set innerHTML(value) { mounted.topWrites++; } };
  const clockNode = { get textContent() { return mounted.clock.at(-1) ?? ''; }, set textContent(value) { mounted.clock.push(value); } };
  const host = {
    get innerHTML() { return html; }, set innerHTML(value) { html = value; },
    addEventListener(kind, fn) { listeners.set(kind, fn); }, removeEventListener(kind) { listeners.delete(kind); },
    querySelector(selector) {
      if (mounted.on && selector === '[data-pr-root]') return root;
      if (mounted.on && selector === '[data-pr-top]') return top;
      if (mounted.on && selector === '[data-pr-checked]') return clockNode;
      return ['[data-pr-root]', '[data-pr-top]', '[data-ws-root]', '[data-ws-top]'].includes(selector) ? null : stub(selector);
    },
    querySelectorAll() { return []; },
  };
  const state = { pairings: pairings.map(row => structuredClone(row)), setup, options, writes: [], next: [], fail: null, clipboard: 'ok' };
  const view = row => owner ? row : memberView(row);
  const api = (path, request = {}) => {
    calls.push([request.method || 'GET', path, request.body]);
    if (request.method === 'PUT' || request.method === 'POST') {
      state.writes.push([request.method, path, request.body]);
      const out = state.next.length ? state.next.shift() : null;
      if (typeof out === 'function') return out();
      if (out instanceof Error) return Promise.reject(out);
      if (path === '/api/runners/install-settings') { state.setup = true; return Promise.resolve({ enabled: true, version: 1 }); }
      return Promise.resolve(structuredClone(out));
    }
    if (state.fail && path.startsWith('/api/compute/pairings/') && path !== '/api/compute/pairings/options') return Promise.reject(state.fail);
    const one = path.match(/^\/api\/compute\/pairings\/(pair_[0-9a-f]{16})$/);
    const answer = path === '/api/compute/pairings' ? { pairings: state.pairings.map(view), total: state.pairings.length, server_time: '2026-11-19T12:00:05Z', setup_enabled: state.setup, offline_after_seconds: 900 }
      : path === '/api/compute/pairings/options' ? { ...state.options, setup_enabled: state.setup }
      : one ? (state.pairings.find(row => row.id === one[1]) ? { pairing: view(state.pairings.find(row => row.id === one[1])), server_time: '2026-11-19T12:00:09Z' } : failure(404, { error: 'not_found', reason: 'route' }))
      : path === '/api/compute/workspaces' ? { workspaces: [], can_manage: owner }
      : path === '/api/compute' ? (owner ? { gateways: [], nodes: [], scopes: [], executions: [], can_manage: true, capabilities: {} } : failure(403, { error: 'owner_required' }))
      : path === '/api/boards' ? { boards: [] } : path === '/api/compute/discoveries' ? { discoveries: [], capabilities: {} } : path === '/api/compute/scripts' ? { scripts: [] } : path === '/api/compute/credentials' ? { credentials: [], status: 'ready' } : null;
    return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(structuredClone(answer));
  };
  let counter = 0, timerId = 0;
  const copy = value => { copied.push(value); if (state.clipboard === 'none') return undefined; if (state.clipboard === 'deny') return Promise.reject(new Error('denied')); return Promise.resolve(); };
  const ui = createCompute({ mount: () => host, api, current: () => user, route: () => hash, doc, events, now: () => clock, requestId: () => `r-${++counter}`, copy, schedule: (fn, ms) => { timers.set(++timerId, { fn, ms }); return timerId; }, cancel: id => timers.delete(id) });
  const dispatch = (kind, event) => listeners.get(kind)?.(event);
  const click = (action, id) => dispatch('click', { target: { closest: selector => selector === '[data-pr-action]' ? { dataset: { prAction: action, id }, getAttribute: name => name === 'aria-disabled' && new RegExp(`data-pr-action="${action}"[^>]*aria-disabled="true"`).test(html) ? 'true' : null } : null } });
  const jump = key => { const event = { prevented: false, preventDefault() { this.prevented = true; }, target: { closest: selector => selector === '[data-pr-jump]' ? { dataset: { prJump: key } } : null } }; dispatch('click', event); return event; };
  const input = (name, value, type = 'text') => dispatch('input', { type: 'input', target: { name, type, value, checked: value, removeAttribute() {} } });
  const submit = () => dispatch('submit', { target: { closest: selector => selector === '[data-pr-form]' ? {} : null }, preventDefault() {}, stopPropagation() {} });
  const key = (name, inside = true) => { const event = { key: name, target: {}, prevented: false, preventDefault() { this.prevented = true; } }; dispatch('keydown', event); return event; };
  const open = async () => { ui.show(); await flush(); };
  const tick = async () => { const first = [...timers.entries()][0]; assert.ok(first, 'a check on the machine is scheduled'); timers.delete(first[0]); first[1].fn(); await flush(); };
  return { ui, host, doc, mounted, state, calls, focused, status, timers, copied, click, jump, input, submit, key, open, tick, dispatch, setRoute(value) { hash = value; }, html: () => html, section: () => { const start = html.indexOf('compute-pr"'); return html.slice(start, html.indexOf('compute-ws"', start) > 0 ? html.indexOf('compute-ws"', start) : undefined); } };
}
const valid = { pr_label: 'build-box', pr_projects: 'demo', pr_topics: 'worker' };
const fill = (f, values = valid) => { for (const [name, value] of Object.entries(values)) f.input(name, value); };
const tickets = f => f.state.writes.filter(([method, path]) => method === 'POST' && path === '/api/compute/pairings');
const created = (extra = {}) => ({ pairing: item('waiting'), repeated: false, setup: { code: 'x', envelope: 'ENVELOPE', command: COMMAND, expires_at: '2026-11-19T12:15:00Z' }, ...extra });
// Alerts that carry text: the static load-error node is always in the markup and is empty until something fails.
const alerts = html => (html.match(/role="alert"[^>]*>(?:<p>|[^<\s])/g) || []).length;
const wingBoxes = f => f.dispatch('input', { type: 'input', target: { name: 'wings', type: 'checkbox', value: 'shared', checked: true, removeAttribute() {} } });

const cases = {
  async pure() {
    assert.deepEqual(pr.listOf(' a, b ,a,,c '), ['a', 'b', 'c']);
    assert.match(pr.clockText('2026-11-19T12:00:00Z'), /\d/); assert.equal(pr.clockText('nope'), ''); assert.equal(pr.clockText(null), '');
    const base = { ...pr.blankValues(), pr_label: 'build-box', pr_projects: 'demo', pr_topics: 'worker', wings: ['shared'] };
    assert.deepEqual(pr.validate(base, ownerOptions), {});
    const label = value => pr.validate({ ...base, pr_label: value }, ownerOptions).label || '';
    for (const bad of ['', ' ', 'Build Box', 'mach_0123456789abcdef', 'a'.repeat(41), '-x', 'x y']) assert.notEqual(label(bad), '', JSON.stringify(bad));
    for (const ok of ['build-box', 'a', 'x.y_z-1', 'a'.repeat(40)]) assert.equal(label(ok), '', ok);
    assert.match(pr.validate({ ...base, pr_projects: '' }, ownerOptions).projects, /at least one project/);
    assert.match(pr.validate({ ...base, pr_projects: 'Bad Name' }, ownerOptions).projects, /lowercase/);
    assert.match(pr.validate({ ...base, pr_topics: '' }, ownerOptions).topics, /at least one topic/);
    assert.match(pr.validate({ ...base, wings: [] }, ownerOptions).wings, /at least one wing/);
    // Copying makes projects, topics and wings optional: the server copies them.
    assert.deepEqual(pr.validate({ ...base, pr_copy_from: 'mach_0123456789abcdef', pr_projects: '', pr_topics: '', wings: [] }, ownerOptions), {});
    assert.deepEqual(pr.createPayload({ ...base, pr_mode: 'auto', pr_max_in_flight: '2' }, 'r-9'), { request_id: 'r-9', kind: 'runner', label: 'build-box', max_runtime_mode: 'auto', max_in_flight: 2, projects: ['demo'], topics: ['worker'], wings: ['shared'] });
    assert.deepEqual(pr.createPayload({ ...base, pr_copy_from: 'mach_0123456789abcdef', pr_projects: '', pr_topics: '', wings: [] }, 'r-1'), { request_id: 'r-1', kind: 'runner', label: 'build-box', max_runtime_mode: 'approval-required', max_in_flight: 1, copy_from: 'mach_0123456789abcdef' });
    const retry = pr.blankValues({ label: 'again', projects: ['a', 'b'], topics: ['t'], wings: ['shared'], max_runtime_mode: 'auto', max_in_flight: 3 });
    assert.deepEqual([retry.pr_label, retry.pr_projects, retry.pr_topics, retry.wings, retry.pr_mode, retry.pr_max_in_flight], ['again', 'a, b', 't', ['shared'], 'auto', '3']);
    const sheet = text(pr.sheetHTML(item('connected')));
    for (const phrase of ['build-box', FINGERPRINT, 'Workspace notes', 'demo', 'worker', '1 trusted agent', 'Asks before each action', 'The machine printed these same groups']) assert.ok(sheet.includes(phrase), phrase);
    assert.match(text(pr.sheetHTML(item('connected', { grant: { ...grant, requesters: [] } }))), /Who can start work on it nobody yet/);
    assert.match(text(pr.sheetHTML(item('connected', { grant: { ...grant, max_in_flight: 3 } }))), /Works on at most 3 cards at a time/);
    assert.match(text(pr.sheetHTML(item('connected'))), /Works on at most 1 card at a time/);
    // The fingerprint is not offered for comparison before the machine has printed it (it prints it as setup finishes).
    const early = text(pr.sheetHTML(item('connected', { steps: steps('ok') })));
    assert.match(early, /Waiting for the machine to print its fingerprint\./); assert.ok(!early.includes('0123abcd'), 'no fingerprint to compare yet');
    assert.equal(pr.finished(item('connected')), true); assert.equal(pr.finished(item('waiting')), false);
    // One mapping for owner and member: steps that never ran in a closed or failed setup are Not run, not Waiting.
    const failedSteps = text(pr.stepsHTML(item('failed')));
    assert.match(failedSteps, /Final check Not run/); assert.match(failedSteps, /Checks on the machine Done/); assert.match(failedSteps, /Background service started Failed/);
    for (const state of ['expired', 'cancelled', 'declined', 'revoked']) assert.doesNotMatch(text(pr.stepsHTML(item(state))), /Waiting/, state);
    assert.match(text(pr.stepsHTML(item('waiting'))), /Waiting/); assert.doesNotMatch(text(pr.stepsHTML(item('waiting'))), /Not run/);
    assert.match(pr.stepsHTML(item('failed')), /Background service started<\/span> <span class="compute-pr-step-state">Failed/);
    assert.match(pr.causeHTML(item('failed')), /Background service started failed\.[\s\S]*did not start[\s\S]*Then choose Set up again/);
    assert.equal(pr.causeHTML(item('waiting')), '');
    assert.ok(pr.LIVE.has('waiting') && pr.LIVE.has('connected') && pr.LIVE.has('approved') && pr.LIVE.has('failed'));
    for (const settled of ['ready', 'expired', 'cancelled', 'declined', 'revoked', 'offline']) assert.equal(pr.LIVE.has(settled), false, settled);
  },

  async off() {
    const f = fixture({ setup: false });
    await f.open();
    let t = text(f.section());
    assert.match(t, /Machine setup is off for this workspace, so a one-time code cannot be made yet/);
    assert.match(t, /A machine added this way can be removed here again/);
    assert.match(f.html(), /data-pr-action="enable"/); assert.doesNotMatch(f.html(), /data-pr-action="add"/);
    // The page says what this version does not do, once, before anything is asked of the person.
    assert.match(t, /does not install the runner program itself/); assert.equal((t.match(/does not install the runner program itself/g) || []).length, 1);
    f.click('enable'); await flush();
    assert.deepEqual(f.state.writes, [['PUT', '/api/runners/install-settings', { enabled: true }]]);
    assert.match(f.html(), /data-pr-action="add"/); assert.doesNotMatch(f.html(), /data-pr-action="enable"/);
    assert.ok(f.status.writes.includes('Machine setup is on. You can add a machine now.'));
    // A refused or unanswered turn-on says so and keeps the button.
    const g = fixture({ setup: false }); await g.open(); g.state.next.push(failure(500, { error: 'x' })); g.click('enable'); await flush();
    assert.ok(g.status.writes.includes('Machine setup could not be turned on. Reload and try again.')); assert.match(g.html(), /data-pr-action="enable"/);
    const h = fixture({ setup: false }); await h.open(); h.state.next.push(Object.assign(new Error('offline'), {})); h.click('enable'); await flush();
    assert.ok(h.status.writes.some(line => /No answer from the server/.test(line)));
  },

  async member() {
    const rows = [item('connected'), item('failed', { id: 'pair_1111111111111111', label: 'other-box' })];
    const f = fixture({ pairings: rows, owner: false });
    await f.open();
    const html = f.section(), t = text(html);
    assert.doesNotMatch(html, /data-pr-action="(add|enable|approve|decline|remove|cancel-setup|again|copy)"/);
    assert.match(t, /Waiting for the owner's approval\./);
    assert.doesNotMatch(html, new RegExp(FINGERPRINT)); assert.doesNotMatch(html, /identity_digest|data-pr-command/);
    f.click('open', 'pair_0123456789abcdef'); await flush();
    const detail = text(f.section());
    assert.doesNotMatch(detail, /Approve this machine|Fingerprint|May read/);
    // Off for a member: nothing to turn on, and the owner is named as the one who can.
    const g = fixture({ owner: false, setup: false }); await g.open();
    assert.match(text(g.section()), /Ask the workspace owner to turn it on/); assert.doesNotMatch(g.html(), /data-pr-action="enable"/);
  },

  async empty() {
    const f = fixture(); await f.open();
    assert.match(text(f.section()), /No machine has been added this way yet\. Choose Add a machine to start\./);
    assert.match(f.html(), /data-pr-action="add"/);
    assert.doesNotMatch(f.section(), /role="alert">[^<]/, 'nothing is announced as an error');
  },

  async form() {
    const f = fixture(); await f.open();
    f.click('add'); await flush();
    const html = f.section(), t = text(html);
    assert.ok(f.focused.some(path => path.includes('#compute-pr-heading')), 'the form starts at its heading');
    assert.match(t, /Runner machine/); assert.match(t, /Script target/);
    assert.match(html, /name="pr_kind" value="target" disabled aria-describedby="pr-target-note"/);
    assert.match(t, /Not available yet: it cannot be set up from the app yet\./);
    for (const label of ['Name', 'Same settings as', 'Projects', 'Topics', 'Wings it may read', 'What it may do without asking']) assert.ok(t.includes(label), label);
    assert.match(html, /<label for="pr_label">Name/); assert.match(html, /<label for="pr_projects">/);
    assert.match(html, /value="shared"/); assert.match(t, /Workspace notes/);
    assert.match(html, /<option value="mach_0123456789abcdef">night-runner/);
    assert.doesNotMatch(html, /position: sticky|data-sticky/);
    // Submitting an empty form sends nothing, names every field, and moves focus to the summary once.
    f.submit(); await flush();
    assert.equal(tickets(f).length, 0);
    const bad = f.section();
    assert.match(text(bad), /4 things need fixing before the command can be made\./);
    for (const field of ['pr_label', 'pr_projects', 'pr_topics', 'pr_wings']) assert.match(bad, new RegExp(`data-pr-jump="${field}"`));
    assert.match(bad, /aria-invalid="true"/); assert.ok(f.focused.some(path => path.includes('[data-pr-summary]')));
    assert.equal(alerts(bad), 1, 'one alert, not one per field');
    const jumped = f.jump('pr_label'); assert.equal(jumped.prevented, true);
    // Escape leaves the form.
    f.key('Escape'); await flush(); assert.doesNotMatch(f.section(), /data-pr-form/);
  },

  async create() {
    const f = fixture(); await f.open();
    f.click('add'); await flush();
    fill(f); wingBoxes(f);
    f.state.next.push(created());
    f.submit(); await flush();
    assert.deepEqual(tickets(f)[0][2], { request_id: 'r-1', kind: 'runner', label: 'build-box', max_runtime_mode: 'approval-required', max_in_flight: 1, projects: ['demo'], topics: ['worker'], wings: ['shared'] });
    const html = f.section(), t = text(html);
    assert.match(html, /<pre id="compute-pr-command" role="region" tabindex="0" data-pr-command aria-labelledby="compute-pr-command-title">/);
    assert.ok(html.includes('SyntheticCodeSentinel_0123456789abcdefghijk'.replace(/'/g, '&#39;')) || html.includes('SyntheticCodeSentinel_'), 'the command carries the code');
    assert.match(t, /Run this on the machine/); assert.match(t, /holds a one-time code/); assert.match(t, /stops working after one use/);
    assert.match(t, /only question it asks/);
    assert.match(t, /Waiting for the machine/); assert.match(t, /Run the command on the machine\. The code stops working after 15 minutes\./);
    for (const label of ['Checks on the machine', 'Runner files installed', 'Background service started', 'Final check']) assert.ok(t.includes(label), label);
    assert.equal((t.match(/Waiting/g) || []).length >= 5, true, 'every step says Waiting');
    assert.ok(f.focused.some(path => path.includes('#compute-pr-heading')));
    assert.ok(f.status.writes.includes('The command is ready. Copy it and run it on the machine.'));
    assert.equal([...f.timers.values()][0].ms, pr.POLL_MS, 'the page starts checking on the machine');
    // The command is held in memory for this one pairing: leaving the pairing drops it.
    f.click('close'); await flush();
    assert.doesNotMatch(f.html(), /SyntheticCodeSentinel_/);
    assert.equal(f.timers.size, 0, 'nothing is checked once the person has left the pairing');
    // Opening it again says the command is not shown again and offers a fresh one.
    f.click('open', 'pair_0123456789abcdef'); await flush();
    assert.match(text(f.section()), /shown once, when it was made, and is not kept/); assert.doesNotMatch(f.html(), /data-pr-command/);
    assert.match(f.html(), /data-pr-action="again"[^>]*>Make a new command \(this one stops working\)/);
  },

  async refusals() {
    const cases = [
      [{ field: 'label', reason: 'label_in_use', error: 'conflict' }, /already uses this name/],
      [{ field: 'label', reason: 'invalid', error: 'invalid_request' }, /lowercase letters, numbers, dots and dashes/],
      [{ field: 'wings', reason: 'invalid', error: 'invalid_request' }, /does not exist or cannot be granted/],
      [{ field: 'copy_from', reason: 'not_found', error: 'invalid_request' }, /no longer available to copy from/],
      [{ field: 'requesters', reason: 'invalid', error: 'invalid_request' }, /can no longer do so/],
      [{ reason: 'setup_off', error: 'conflict' }, /Machine setup is turned off for this workspace\. Turn machine setup on/],
      [{ reason: 'rate_limited', error: 'rate_limited' }, /Too many tries in a minute\. Wait a minute/],
    ];
    for (const [payload, pattern] of cases) {
      const f = fixture(); await f.open(); f.click('add'); await flush(); fill(f); wingBoxes(f);
      f.state.next.push(failure(payload.error === 'rate_limited' ? 429 : 409, payload));
      f.submit(); await flush();
      assert.match(text(f.section()), pattern, JSON.stringify(payload));
      assert.match(f.section(), /data-pr-form/, 'the form stays open with what was typed');
      assert.match(f.section(), /value="build-box"/);
      assert.ok(f.focused.some(path => path.includes('[data-pr-summary]')), 'a refusal moves focus to the summary');
      assert.doesNotMatch(f.html(), /SyntheticCodeSentinel_/);
    }
    // No answer at all: nothing is confirmed, and the request id is kept so a retry cannot make two.
    const g = fixture(); await g.open(); g.click('add'); await flush(); fill(g); wingBoxes(g);
    g.state.next.push(new Error('network'));
    g.submit(); await flush();
    assert.match(text(g.section()), /No answer from the server, so nothing is confirmed\./);
    g.state.next.push(created()); g.submit(); await flush();
    assert.deepEqual(tickets(g).map(([, , body]) => body.request_id), ['r-1', 'r-1']);
    // A replay answers 200 with no command: the page says the command is not kept.
    const h = fixture(); await h.open(); h.click('add'); await flush(); fill(h); wingBoxes(h);
    h.state.next.push({ pairing: item('waiting'), repeated: true }); h.submit(); await flush();
    assert.match(text(h.section()), /shown once, when it was made, and is not kept/); assert.doesNotMatch(h.html(), /data-pr-command/);
  },

  async copy() {
    const f = fixture(); await f.open(); f.click('add'); await flush(); fill(f); wingBoxes(f); f.state.next.push(created()); f.submit(); await flush();
    f.click('copy'); await flush();
    assert.deepEqual(f.copied, [COMMAND]);
    assert.deepEqual(f.status.copy, ['Copied. Paste it on the machine.']);
    // A clipboard that is missing, returns nothing, or refuses is a failed copy: never "Copied." without proof.
    for (const mode of ['none', 'deny']) {
      const g = fixture(); g.state.clipboard = mode; await g.open(); g.click('add'); await flush(); fill(g); wingBoxes(g); g.state.next.push(created()); g.submit(); await flush();
      g.click('copy'); await flush();
      assert.deepEqual(g.status.copy, ['Copying is not available here. The command above is selected: copy it with your keyboard or the long-press menu.'], mode);
    }
  },

  async connected() {
    const f = fixture(); await f.open(); f.click('add'); await flush(); fill(f); wingBoxes(f); f.state.next.push(created()); f.submit(); await flush();
    // Still waiting: a check changes nothing on screen and says nothing.
    f.state.pairings = [item('waiting')];
    const writes = f.status.writes.length;
    await f.tick();
    assert.equal(f.status.writes.length, writes, 'a check that finds the same state is silent');
    assert.equal(f.timers.size, 1, 'and the next check is scheduled');
    assert.match(text(f.section()), /Checked at/);
    // The machine connects: the page shows it, asks for one approval, and says so once.
    f.state.pairings = [item('connected')];
    await f.tick();
    const t = text(f.section());
    assert.match(t, /Machine connected, waiting for your approval/); assert.match(t, /Approve this machine\?/);
    assert.ok(t.includes(FINGERPRINT)); assert.match(t, /May read Workspace notes/); assert.match(t, /Who can start work on it 1 trusted agent/); assert.match(t, /Works on at most 1 card at a time/);
    assert.match(f.html(), /data-pr-action="approve"/); assert.match(f.html(), /data-pr-action="decline">This is not my machine/);
    assert.equal(f.status.writes.filter(line => /Machine connected/.test(line)).length, 1);
    await f.tick(); assert.equal(f.status.writes.filter(line => /Machine connected/.test(line)).length, 1, 'said once, not on every check');
    // The command is gone once the machine has connected: the code is spent.
    assert.doesNotMatch(f.html(), /data-pr-command/);
  },

  async polling() {
    const f = fixture({ pairings: [item('connected')] }); await f.open(); f.click('open', 'pair_0123456789abcdef'); await flush();
    assert.equal(f.timers.size, 1);
    // A failed check keeps what was true, says so honestly, and tries again.
    f.state.fail = failure(503, { error: 'x' });
    await f.tick();
    assert.equal(f.status.writes.filter(line => /^Could not check just now\. Showing what was true at \d.*trying again\.$/.test(line)).length, 1, 'the status line says so once, in words');
    assert.doesNotMatch(text(f.section()), /Could not check just now/, 'the clock line does not say it a second time');
    assert.match(text(f.section()), /Last checked at \d/);
    assert.match(text(f.section()), /Machine connected, waiting for your approval/, 'the last known state stays');
    assert.equal(f.timers.size, 1);
    f.state.fail = null; f.state.pairings = [item('connected', { version: 2 })];
    await f.tick();
    assert.doesNotMatch(text(f.section()), /Could not check just now/);
    // A pairing that vanished is said once and stops the checks.
    f.state.fail = failure(404, { error: 'not_found', reason: 'route' });
    await f.tick();
    assert.match(f.status.writes.at(-1), /no longer available/); assert.equal(f.timers.size, 0);
    // A hidden tab does not ask: it waits and tries again later.
    const g = fixture({ pairings: [item('waiting')] }); await g.open(); g.click('open', 'pair_0123456789abcdef'); await flush();
    g.doc.hidden = true; const before = g.calls.length; await g.tick();
    assert.equal(g.calls.length, before, 'no request while hidden'); assert.equal(g.timers.size, 1);
    // A settled state is never checked again.
    g.doc.hidden = false; g.state.pairings = [item('ready')]; await g.tick();
    assert.equal(g.timers.size, 0); assert.match(text(g.section()), /Set up and checked/);
    // Leaving Compute stops everything.
    const h = fixture({ pairings: [item('waiting')] }); await h.open(); h.click('open', 'pair_0123456789abcdef'); await flush();
    h.ui.stop(); assert.equal(h.timers.size, 0);
  },

  async 'reload-notices-change'() {
    // A reload (the Refresh button) that finds the change says it once, exactly like a check does.
    const f = fixture({ pairings: [item('waiting')] }); await f.open(); f.click('open', 'pair_0123456789abcdef'); await flush();
    f.state.pairings = [item('connected')];
    await f.ui.refresh(); await flush();
    assert.equal(f.status.writes.filter(line => /Machine connected/.test(line)).length, 1);
    await f.tick(); assert.equal(f.status.writes.filter(line => /Machine connected/.test(line)).length, 1, 'the next check adds nothing');
  },

  async 'run-steps'() {
    // M2: where and how to run the command is said, in order, above the command.
    const f = fixture(); await f.open(); f.click('add'); await flush(); fill(f); wingBoxes(f); f.state.next.push(created()); f.submit(); await flush();
    const html = f.section(), t = text(html);
    const at = pattern => html.search(pattern);
    assert.ok(at(/<ol class="compute-pr-run">/) > 0 && at(/<ol class="compute-pr-run">/) < at(/<pre id="compute-pr-command"/), 'the numbered lines come above the command');
    assert.match(t, /Open a terminal on that machine\. On Ubuntu: press the Super key, type Terminal and press Enter\./);
    assert.match(t, /Paste the command below \(Ctrl\+Shift\+V\) and press Enter\. Do not type sudo in front of it\./);
    assert.match(t, /Wait for the steps below to turn to Done\. This page updates by itself\./);
    assert.equal((html.match(/<ol class="compute-pr-run">[\s\S]*?<\/ol>/)[0].match(/<li>/g) || []).length, 3);
    assert.match(t, /If you leave this page you need a new command: this one is not kept\./);
    assert.match(html, /data-pr-action="again"[^>]*>Make a new command \(this one stops working\)/);
    assert.match(t, /Your terminal may keep this line in its history; that is harmless/);
    assert.doesNotMatch(t, /shell history/);
  },

  async grants() {
    // M1: the sheet says what approval grants and what it does not, above the buttons; Approve waits for the fingerprint.
    const early = fixture({ pairings: [item('connected', { steps: steps('ok', 'ok') })] }); await early.open(); early.click('open', 'pair_0123456789abcdef'); await flush();
    let html = early.section(), t = text(html);
    assert.match(t, /Waiting for the machine to print its fingerprint\./);
    assert.match(html, /data-pr-action="approve" aria-disabled="true" aria-describedby="compute-pr-approve-note"/);
    assert.match(t, /You can approve once the machine has finished its setup and printed its fingerprint\./);
    early.click('approve'); await flush(); assert.equal(early.state.writes.length, 0, 'a press on the waiting Approve changes nothing');
    const f = fixture({ pairings: [item('connected')] }); await f.open(); f.click('open', 'pair_0123456789abcdef'); await flush();
    html = f.section(); t = text(html);
    assert.doesNotMatch(html, /data-pr-action="approve" aria-disabled/);
    assert.match(t, /Approving lets this machine use the access listed above\. It does not connect a provider: Coworkers cannot run on it yet\. You sign in a provider and confirm the machine later, in Needs me\./);
    assert.ok(html.indexOf('data-pr-grants') < html.indexOf('data-pr-action="approve"'), 'the sentence is above the buttons');
    assert.match(t, /Who can start work on it 1 trusted agent/);
    for (const old of ['May start work on it', 'Cards at once', 'What it may do alone', 'No agent yet']) assert.ok(!t.includes(old), old);
    f.click('approve'); await flush(); assert.equal(f.state.writes.length, 1);
  },

  async 'confirm-focus'() {
    // M7: the confirm dialogs open on the safe button, and a second Enter on it removes nothing.
    for (const [state, trigger, heading, yes] of [['connected', 'decline', /Is this not your machine\?/, /Yes, this is not my machine/], ['ready', 'remove', /Remove this machine\?/, /Yes, remove it/]]) {
      const f = fixture({ pairings: [item(state)] }); await f.open(); f.click('open', 'pair_0123456789abcdef'); await flush();
      f.click(trigger); await flush();
      const html = f.section();
      assert.match(text(html), heading); assert.match(text(html), yes);
      assert.ok(html.indexOf('data-pr-action="keep"') < html.indexOf('data-pr-action="confirm-close"'), 'the safe answer comes first');
      assert.match(text(html), /Go back/); assert.doesNotMatch(text(html), /Keep it/);
      assert.ok(f.focused.at(-1).includes('[data-pr-action="keep"]'), `${trigger}: default focus is Go back`);
      f.click('keep'); await flush();
      assert.equal(f.state.writes.length, 0, 'going back changes nothing');
      assert.ok(f.focused.at(-1).includes(`[data-pr-action="${trigger}"]`), 'going back returns focus to the button that asked');
      f.click(trigger); await flush(); f.key('Escape'); await flush();
      assert.ok(f.focused.at(-1).includes(`[data-pr-action="${trigger}"]`), 'Escape returns focus to the button that asked');
      assert.equal(f.state.writes.length, 0);
    }
  },

  async stalled() {
    // M3: a machine that connected and never finished says so, and the way out is Set up again or Remove, never Approve.
    const row = item('connected', { stalled: true, steps: steps('ok'), sentence: 'This setup has not finished.', next: 'Remove this machine and set it up again.' });
    const f = fixture({ pairings: [row] }); await f.open(); f.click('open', 'pair_0123456789abcdef'); await flush();
    const html = f.section(), t = text(html);
    assert.match(t, /This setup has not finished\. Remove this machine and set it up again\./);
    assert.doesNotMatch(html, /data-pr-action="(approve|decline)"/); assert.doesNotMatch(t, /Approve this machine\?|Approving lets/);
    assert.match(html, /data-pr-action="again"[^>]*>Set up again/); assert.match(html, /data-pr-action="remove"/);
    assert.match(text(f.html()), /Not run/, 'the steps it never reached are Not run');
    // A member reads it too, with no controls.
    const m = fixture({ pairings: [row], owner: false }); await m.open();
    assert.match(text(m.section()), /This setup has not finished\./); assert.doesNotMatch(m.section(), /data-pr-action="(approve|remove|again)"/);
  },

  async 'no-repaint'() {
    // M6: a check that finds nothing new rewrites only the clock line, so the command keeps its focus and selection.
    const f = fixture({ pairings: [item('waiting')] });
    await f.open(); f.click('open', 'pair_0123456789abcdef'); await flush();
    f.state.next.push(created()); // (unused) keep the queue shape the same as a real session
    f.mounted.on = true; f.mounted.rootWrites = f.mounted.topWrites = 0; f.mounted.clock.length = 0;
    await f.tick();
    assert.equal(f.mounted.rootWrites, 0, 'the root is not rebuilt when nothing changed');
    assert.equal(f.mounted.topWrites, 0);
    assert.equal(f.mounted.clock.length, 1, 'only the clock line is written'); assert.match(f.mounted.clock[0], /^Checked at /);
    await f.tick();
    assert.equal(f.mounted.rootWrites, 0, 'and again on the next check'); assert.equal(f.mounted.clock.length, 1, 'an identical clock is not rewritten');
    // A real change repaints once.
    f.state.pairings = [item('connected')]; await f.tick();
    assert.equal(f.mounted.rootWrites, 1);
    // A failed check changes the clock line only, too.
    f.mounted.rootWrites = 0; f.mounted.clock.length = 0; f.state.fail = failure(503, { error: 'x' }); await f.tick();
    assert.equal(f.mounted.rootWrites, 0, 'a failed check says so in the clock line and rebuilds nothing');
    assert.match(f.mounted.clock.at(-1), /^Last checked at /);
    assert.doesNotMatch(f.mounted.clock.at(-1), /Could not check just now/, 'the status line says it; the clock line says how old what is shown is');
    f.state.fail = null; await f.tick();
    assert.doesNotMatch(f.mounted.clock.at(-1), /Could not check/); assert.equal(f.mounted.rootWrites, 0);
  },

  async 'member-intro'() {
    const f = fixture({ pairings: [item('waiting')], owner: false }); await f.open();
    const t = text(f.section());
    assert.match(t, /This is a read-only view of the machines being added\. Only the workspace owner adds a machine and approves it\./);
    assert.doesNotMatch(t, /To add one you copy one command/); assert.doesNotMatch(t, /There is no switch here/);
    const o = fixture(); await o.open();
    assert.match(text(o.section()), /A machine here is a computer that runs Coworkers \(a runner\)\./);
    assert.match(text(o.section()), /its settings are under Runner machines in Admin; cloud workspaces are below/);
    assert.match(text(o.section()), /There is no switch here to turn machine setup off\./);
    assert.equal((text(o.section()).match(/There is no switch here/g) || []).length, 1);
  },

  async 'ready-row'() {
    const f = fixture({ pairings: [item('ready'), item('ready', { id: 'pair_4444444444444444', label: 'attested-box', binding_state: 'owner_attested' })] }); await f.open();
    const t = text(f.section());
    assert.equal((t.match(/Coworkers cannot run on it yet\./g) || []).length, 1, 'only the machine that is not attested says it');
    assert.equal((t.match(/confirm the machine/g) || []).length, 1, 'and the section note (which says what is confirmed, and where) is not repeated per row');
    assert.doesNotMatch(t, /To get ready for later/, 'the ready next step is not repeated in a row');
  },

  async 'copy-in-place'() {
    // F: a failed copy selects the command and says so; the note is written in place, so nothing is rebuilt and the
    // selection survives the next check.
    const f = fixture(); f.state.clipboard = 'deny'; await f.open(); f.click('add'); await flush(); fill(f); wingBoxes(f); f.state.next.push(created()); f.submit(); await flush();
    f.mounted.on = true; f.mounted.rootWrites = f.mounted.topWrites = 0;
    f.click('copy'); await flush();
    assert.deepEqual(f.status.copy, ['Copying is not available here. The command above is selected: copy it with your keyboard or the long-press menu.']);
    assert.equal(f.mounted.rootWrites, 0, 'the note is written in place');
    f.state.pairings = [item('waiting')];
    await f.tick();
    assert.equal(f.mounted.rootWrites, 0, 'and the next check rebuilds nothing');
    assert.equal(pr.finished(item('waiting')), false);
  },

  async 'overview-follows-the-machines'() {
    // M4: the overview reads again when a machine appears, is approved or removed, so it never says "Nothing here yet"
    // beside a machine; a check that finds nothing new does not make it read.
    const reads = f => f.calls.filter(([method, path]) => method === 'GET' && path === '/api/compute/overview').length;
    const f = fixture({ pairings: [item('connected')] }); await f.open(); f.click('open', 'pair_0123456789abcdef'); await flush();
    const first = reads(f);
    await f.tick();
    assert.equal(reads(f), first, 'a check that finds the same state reads nothing');
    f.state.pairings = [item('ready')];
    await f.tick();
    assert.equal(reads(f), first + 1, 'a changed state makes the overview read once');
    await f.tick().catch(() => {});
    assert.equal(reads(f), first + 1, 'and the settled state does not');
  },

  async 'member-detail-has-a-way-back'() {
    // A member has no control over a machine but is never stranded in a detail.
    const f = fixture({ pairings: [item('waiting')], owner: false, options: memberOptions }); await f.open(); f.click('open', 'pair_0123456789abcdef'); await flush();
    assert.match(f.html(), /data-pr-action="close"[^>]*>Back to the list/);
    assert.doesNotMatch(text(f.section()), /Approve this machine|Set up again|Remove this machine/);
    f.click('close'); await flush();
    assert.match(text(f.section()), /Open build-box|build-box/);
    assert.doesNotMatch(f.html(), /data-pr-action="close"/);
  },

  async 'stalled-chip'() {
    // C: a stalled setup is not drawn as a quiet "waiting" chip: the server's label is shown and the tone is the failed one.
    const stalled = item('connected', { stalled: true, state_label: 'Not finished' });
    const f = fixture({ pairings: [stalled] }); await f.open();
    assert.match(f.html(), /data-tone="bad">Not finished</, 'the list row');
    f.click('open', 'pair_0123456789abcdef'); await flush();
    assert.match(f.html(), /compute-pr-state" data-tone="bad">Not finished</, 'the detail chip');
    assert.doesNotMatch(f.html(), /waiting for your approval/i);
    const waiting = fixture({ pairings: [item('connected')] }); await waiting.open();
    assert.match(waiting.html(), /data-tone="wait">Machine connected/, 'a live one still waits');
  },

  async 'poll-failure-announced'() {
    const f = fixture({ pairings: [item('waiting')] }); await f.open(); f.click('open', 'pair_0123456789abcdef'); await flush();
    f.state.fail = failure(503, { error: 'x' }); await f.tick();
    assert.equal(f.status.writes.filter(line => /Could not check just now/.test(line)).length, 1, 'said once, by the status line');
    await f.tick(); await f.tick();
    assert.equal(f.status.writes.filter(line => /Could not check just now/.test(line)).length, 1, 'not again while it keeps failing');
  },

  async 'set-up-again-keeps-requesters'() {
    const f = fixture({ pairings: [item('expired')] }); await f.open(); f.click('open', 'pair_0123456789abcdef'); await flush();
    f.click('again', 'pair_0123456789abcdef'); await flush();
    f.state.next.push(created()); f.submit(); await flush();
    assert.deepEqual(tickets(f)[0][2].requesters, ['conn_a'], 'who may start work on the machine is carried over');
    // Copying from a machine takes its requesters from the server instead.
    assert.equal('requesters' in pr.createPayload({ ...pr.blankValues({ label: 'x', requesters: ['c'] }), pr_copy_from: 'mach_0123456789abcdef' }, 'r'), false);
    assert.match(text(f.section()), /Setup removed|Waiting for the machine/);
    const g = fixture({ pairings: [item('abandoned')] }); await g.open(); g.click('open', 'pair_0123456789abcdef'); await flush();
    assert.match(text(g.section()), /Setup removed/); assert.match(g.html(), /data-pr-action="again"[^>]*>Set up again/);
    assert.doesNotMatch(text(pr.stepsHTML(item('abandoned'))), /Waiting/);
  },

  async 'list-quiet'() {
    // Going back to the list clears what was said about the pairing just left, and a settled row does not repeat the section's note.
    const f = fixture({ pairings: [item('ready'), item('failed', { id: 'pair_3333333333333333', label: 'other-box' })] }); await f.open();
    const list = text(f.section());
    assert.equal((list.match(/confirm the machine/g) || []).length, 1, 'the ready machine\'s next step is not repeated beside the section note');
    assert.match(list, /Open it to see the cause, then choose Set up again/, "a failed row keeps its next step");
    f.click('open', 'pair_0123456789abcdef'); await flush();
    assert.match(text(f.section()), /Coworkers do not run on it yet\. To get ready for later, sign in a provider and confirm this machine, both in Needs me/);
    // Something was said about it (the machine connected), then the person goes back.
    f.state.pairings = [item('connected'), item('failed', { id: 'pair_3333333333333333', label: 'other-box' })];
    await f.ui.refresh(); await flush();
    f.click('close'); await flush(); await f.ui.refresh(); await flush();
    assert.match(f.html(), /data-pr-notice role="status"><\/p>/, 'the status line is cleared on the way back');
  },

  async approve() {
    const f = fixture({ pairings: [item('connected')] }); await f.open(); f.click('open', 'pair_0123456789abcdef'); await flush();
    f.state.next.push({ pairing: item('approved', { version: 2 }) });
    f.click('approve'); await flush();
    assert.deepEqual(f.state.writes[0], ['POST', '/api/compute/pairings/pair_0123456789abcdef/accept', { request_id: 'r-1', expected_version: 1, identity_digest: 'f'.repeat(64) }]);
    let t = text(f.section());
    assert.match(t, /Approved/); assert.match(t, /finishing its setup/); assert.doesNotMatch(f.html(), /data-pr-action="approve"/);
    assert.ok(f.status.writes.some(line => /^Approved “build-box”\./.test(line)));
    assert.equal(f.timers.size, 1, 'an approved machine is still being watched until its checks pass');
    f.state.pairings = [item('ready', { version: 2 })]; await f.tick();
    t = text(f.section());
    assert.match(t, /Set up and checked/); assert.match(t, /Confirm this machine in Needs me: not done yet/);
    assert.match(t, /This version only checks the machine: Coworkers do not run on it yet\. To get ready for later, sign in a provider and confirm this machine, both in Needs me\./);
    assert.doesNotMatch(t, /\b(running|runs now|is running|ready to run)\b/i, 'nothing says that anything runs');
    assert.equal(f.timers.size, 0);
  },

  async 'stale-accept'() {
    for (const reason of ['stale_version', 'identity_changed']) {
      const f = fixture({ pairings: [item('connected')] }); await f.open(); f.click('open', 'pair_0123456789abcdef'); await flush();
      f.state.pairings = [item('connected', { version: 2, identity_digest: 'e'.repeat(64) })];
      f.state.next.push(failure(409, { error: 'conflict', field: reason === 'stale_version' ? 'expected_version' : 'identity_digest', reason }));
      f.click('approve'); await flush();
      assert.ok(f.status.writes.some(line => line.includes(reasons[reason].text) && line.includes(reasons[reason].next)), reason);
      // The screen reloaded the one pairing: the next approve uses what is true now.
      f.state.next.push({ pairing: item('approved', { version: 3 }) }); f.click('approve'); await flush();
      assert.deepEqual(f.state.writes.at(-1)[2], { request_id: 'r-2', expected_version: 2, identity_digest: 'e'.repeat(64) }, reason);
    }
  },

  async decline() {
    const f = fixture({ pairings: [item('connected')] }); await f.open(); f.click('open', 'pair_0123456789abcdef'); await flush();
    f.click('decline'); await flush();
    const t = text(f.section());
    assert.match(t, /Is this not your machine\?/); assert.match(t, /You can set it up again/); assert.match(t, /Yes, this is not my machine/); assert.match(t, /Go back/); assert.doesNotMatch(t, /Approve this machine\?/, 'the heading above must not still ask to approve');
    assert.match(f.html(), /role="group" aria-labelledby="compute-pr-confirm-title"/);
    assert.ok(f.focused.at(-1).includes('[data-pr-action="keep"]'), 'focus goes to the safe button, never the destructive one');
    assert.equal(f.state.writes.length, 0, 'asking changes nothing');
    f.click('keep'); await flush(); assert.doesNotMatch(f.section(), /compute-pr-confirm/); assert.equal(f.state.writes.length, 0);
    f.click('decline'); await flush();
    f.key('Escape'); await flush(); assert.doesNotMatch(f.section(), /compute-pr-confirm/);
    f.click('decline'); await flush();
    f.state.next.push({ pairing: item('declined', { version: 2 }) });
    f.click('confirm-close'); await flush();
    assert.deepEqual(f.state.writes[0], ['POST', '/api/compute/pairings/pair_0123456789abcdef/close', { request_id: 'r-1', expected_version: 1 }]);
    assert.match(text(f.section()), /Not approved/); assert.match(f.html(), /Set up again/);
    assert.equal(f.timers.size, 0);
  },

  async failed() {
    const f = fixture({ pairings: [item('failed')] }); await f.open(); f.click('open', 'pair_0123456789abcdef'); await flush();
    const t = text(f.section());
    assert.match(t, /A step failed/); assert.match(t, /Background service started failed\. The background service did not start\./);
    assert.match(t, /The window where you ran the command shows the reason on its last lines\. Then choose Set up again\./); assert.doesNotMatch(t, /run the (same )?command again/i, 'no next step points at a command the page does not hold');
    assert.match(f.html(), /Set up again/); assert.match(f.html(), /data-pr-action="remove"/); assert.doesNotMatch(f.html(), /data-pr-action="approve"/);
    assert.equal(f.timers.size, 1, 'a failed machine is still watched: a machine that finishes by itself moves the page on');
    f.state.pairings = [item('connected')]; await f.tick();
    assert.match(text(f.section()), /Machine connected, waiting for your approval/); assert.doesNotMatch(f.section(), /compute-pr-cause/);
    // Set up again carries the values over so nothing is typed twice.
    const g = fixture({ pairings: [item('failed')] }); await g.open(); g.click('open', 'pair_0123456789abcdef'); await flush();
    assert.match(g.html(), /data-pr-action="again"[^>]*>Set up again/, 'Set up again is the first and primary next step');
    assert.ok(g.section().indexOf('data-pr-action="again"') < g.section().indexOf('data-pr-action="remove"'));
    g.state.next.push({ pairing: item('declined', { version: 2 }) });
    g.click('again', 'pair_0123456789abcdef'); await flush();
    // The old setup is cancelled first (its name would be in the way), then the form opens with the same values.
    assert.equal(g.state.writes[0][1], '/api/compute/pairings/pair_0123456789abcdef/close');
    assert.match(g.section(), /name="pr_label" value="build-box"/); assert.match(g.section(), /name="pr_projects" value="demo"/); assert.match(g.section(), /name="pr_topics" value="worker"/);
    assert.match(g.section(), /name="wings" value="shared"[^>]* checked/);
    assert.match(g.section(), /data-pr-form/);
  },

  async closed() {
    for (const state of ['expired', 'cancelled', 'declined', 'abandoned', 'revoked']) {
      const f = fixture({ pairings: [item(state)] }); await f.open(); f.click('open', 'pair_0123456789abcdef'); await flush();
      const t = text(f.section());
      assert.ok(t.includes(sentences[state][0]), state); assert.ok(t.includes(sentences[state][1]), state); assert.ok(t.includes(sentences[state][2]), state);
      assert.match(f.html(), /data-pr-action="again"[^>]*>Set up again/); assert.doesNotMatch(f.html(), /data-pr-action="(approve|remove|cancel-setup)"/);
      assert.equal(f.timers.size, 0, state);
      f.click('again', 'pair_0123456789abcdef'); await flush();
      assert.match(f.section(), /name="pr_label" value="build-box"/, state);
    }
    // Cancel and set up again cancels the waiting setup first, then opens the form with the same values.
    const g = fixture({ pairings: [item('waiting')] }); await g.open(); g.click('open', 'pair_0123456789abcdef'); await flush();
    g.state.next.push({ pairing: item('cancelled', { version: 2 }) });
    g.click('again', 'pair_0123456789abcdef'); await flush();
    assert.equal(g.state.writes[0][1], '/api/compute/pairings/pair_0123456789abcdef/close');
    assert.match(g.section(), /name="pr_label" value="build-box"/);
  },

  async remove() {
    const f = fixture({ pairings: [item('ready')] }); await f.open(); f.click('open', 'pair_0123456789abcdef'); await flush();
    f.click('remove'); await flush();
    assert.match(text(f.section()), /Remove this machine\?/); assert.match(text(f.section()), /credentials stop working/);
    f.state.next.push({ pairing: item('revoked', { version: 2 }) }); f.click('confirm-close'); await flush();
    assert.match(text(f.section()), /Access removed/);
    // Stale close: said with its reason, then the one pairing is read again.
    const g = fixture({ pairings: [item('ready')] }); await g.open(); g.click('open', 'pair_0123456789abcdef'); await flush();
    g.click('remove'); await flush(); g.state.pairings = [item('revoked', { version: 5 })];
    g.state.next.push(failure(409, { error: 'conflict', field: 'expected_version', reason: 'stale_version' }));
    g.click('confirm-close'); await flush();
    assert.ok(g.status.writes.some(line => line.includes('This machine changed since you opened it.')));
    assert.match(text(g.section()), /Access removed/);
  },

  async offline() {
    const f = fixture({ pairings: [item('offline')] }); await f.open(); f.click('open', 'pair_0123456789abcdef'); await flush();
    const t = text(f.section());
    assert.match(t, /Offline/); assert.match(t, /nothing has been heard for over 15 minutes/); assert.match(t, /returns to Set up and checked by itself/);
    assert.match(t, /Last seen at \d/); assert.match(f.html(), /data-pr-action="remove"/);
  },

  async escaping() {
    const evil = '<img src=x onerror=alert(1)>';
    const row = item('connected', { label: evil, fingerprint: evil, grant: { ...grant, projects: [evil], topics: [evil] }, cause: null });
    const f = fixture({ pairings: [row, item('failed', { id: 'pair_2222222222222222', label: evil, cause: { step: 'files', code: 'unknown', text: evil, next: evil } })] }); await f.open();
    f.click('open', 'pair_0123456789abcdef'); await flush();
    assert.doesNotMatch(f.section(), /<img/); assert.match(f.section(), /&lt;img src=x/);
    f.click('close'); await flush(); f.click('open', 'pair_2222222222222222'); await flush();
    assert.doesNotMatch(f.section(), /<img/);
    const g = fixture(); await g.open(); g.click('add'); await flush(); g.input('pr_label', evil);
    g.submit(); await flush(); assert.doesNotMatch(g.section(), /<img/);
  },

  async 'once-per-screen'() {
    const f = fixture({ pairings: [item('waiting')] }); await f.open();
    const t = text(f.section());
    assert.equal((t.match(/does not install the runner program itself/g) || []).length, 1, 'what this version does not do is said once');
    f.click('open', 'pair_0123456789abcdef'); await flush();
    assert.equal((text(f.section()).match(/does not install the runner program itself/g) || []).length, 1);
  },

  async loading() {
    const f = fixture(); f.ui.show();
    assert.match(text(f.html()), /Loading/); await flush();
    // A failed list read is one alert with a Reload button; a good reload is said once.
    const g = fixture({ pairings: [item('waiting')] });
    g.state.pairings = null;
    await g.open();
    assert.match(text(g.section()), /Machines being added could not be loaded\. Reload to try again\./);
    assert.match(g.section(), /data-pr-action="reload"/); assert.equal((g.section().match(/role="alert">[^<]/g) || []).length, 1);
    g.state.pairings = [item('waiting')]; g.click('reload'); await flush();
    assert.doesNotMatch(g.section(), /could not be loaded/); assert.ok(g.status.writes.includes('Machines being added reloaded.'));
  },
};

const only = process.argv[2];
const names = only ? [only] : Object.keys(cases);
for (const name of names) {
  assert.ok(cases[name], `unknown case ${name}`);
  await cases[name]();
}
console.log('ok');
process.exit(0);
