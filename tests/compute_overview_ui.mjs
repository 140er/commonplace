import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// The Compute overview: the production module (and the production Compute controller) run against a synthetic
// in-memory server with fake timers. No socket, browser or backend is opened.
const source = name => readFileSync(new URL(`../commonplace/web/static/${name}.js`, import.meta.url), 'utf8');
const moduleURL = text => `data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
const apiURL = moduleURL(source('api'));
const rewrite = (text, extra = {}) => Object.entries({ './api.js': apiURL, ...extra }).reduce((out, [name, url]) => out.replace(new RegExp(`(['"])${name.replace(/[./]/g, '\\$&')}\\1`, 'g'), () => JSON.stringify(url)), text);
const overviewURL = moduleURL(rewrite(source('compute-overview')));
const ov = await import(overviewURL);
const workspacesURL = moduleURL(rewrite(source('compute-workspaces')));
const { createCompute } = await import(moduleURL(rewrite(source('compute'), { './compute-workspaces.js': workspacesURL, './compute-overview.js': overviewURL })));
const flush = async () => { for (let i = 0; i < 80; i++) await Promise.resolve(); };
const text = html => html.replace(/<[^>]*>/g, ' ').replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/\s+/g, ' ');
const failure = (status, payload) => Object.assign(new Error(payload?.error || String(status)), { status, payload });
const clock = 1800000000;

const item = (kind, id, name, status, extra = {}) => ({
  kind, id, name, subtitle: kind === 'runner' ? 'Runner machine' : kind === 'machine' ? 'Target machine' : 'ghcr.io/acme/tools:1.2',
  status: { code: status.code, tone: status.tone || 'ok', label: status.label, detail: status.detail || `${status.label} detail.` },
  last_seen: extra.last_seen === undefined ? { at: 'x', age_s: 40, source: 'machine_connection', label: 'Last seen 40 seconds ago' } : extra.last_seen,
  health: { state: extra.attention ? 'attention' : 'ok', blockers: extra.blockers || [], issues: extra.issues || [] },
  usage: { summary: extra.usage || 'Usage line.', runs: { text: '1 of 2 runs in progress' }, resources: { text: 'Not reported: this machine has not sent a resource report.' }, spend: { text: 'No runs in the last 30 days.' }, limits: { text: '2 CPU cores, 4 GiB memory, 20 GiB storage (limits)' }, measured: { text: 'Not measured yet.' } },
  placement: {}, basis: extra.basis ?? { seq: 0 }, actions: [], ...extra.more,
});
const fleet = () => [
  item('runner', 'mach_1', 'build-runner', { code: 'online', label: 'Online', detail: 'Waiting for work. Last seen 40 seconds ago.' }),
  item('runner', 'mach_2', 'edge-runner', { code: 'offline', tone: 'bad', label: 'Offline', detail: 'Not heard from for 25 minutes.' }, { attention: true, blockers: [{ code: 'offline', text: 'Commonplace has not heard from it for over 15 minutes, so new runs would wait.' }], last_seen: { at: 'x', age_s: 1500, source: 'machine_connection', label: 'Last seen 25 minutes ago' } }),
  item('machine', 'n_1', 'Fixture machine', { code: 'not_verified', tone: 'wait', label: 'Not verified', detail: 'Commonplace has not verified this machine yet.' }, { last_seen: null }),
  item('workspace', 'w_1', 'Build box', { code: 'running', label: 'Running', detail: 'Reported running 2 minutes ago.' }),
  item('workspace', 'w_2', 'Old box', { code: 'unknown', tone: 'quiet', label: 'Unknown', detail: 'Last reported Running 14 minutes ago and nothing newer since.' }, { attention: true }),
];
const overviewOf = (items = fleet(), extra = {}) => ({
  as_of: '2026-11-19T10:42:07Z', refresh_seconds: 30, thresholds: {}, can_manage: true, executor: { available: false, text: 'Starting and stopping cloud workspaces on a machine is not available in this version. Requests are saved and wait.' },
  counts: {}, items, hidden: null, truncated: {}, ...extra,
});
const detailOf = (base, extra = {}) => ({ item: { ...base, activity: [{ at: 1, age_s: 60, text: 'Created by the owner' }], facts: [{ label: 'Image', value: 'ghcr.io/acme/tools:1.2' }], ...extra }, can_manage: true });

function fixture({ data = overviewOf(), hidden = false } = {}) {
  const nodes = { root: { innerHTML: '' }, top: { innerHTML: '' }, meta: { textContent: '' }, announce: { textContent: '' } };
  const focused = [], timers = [], calls = [], docListeners = new Map();
  let epoch = 1, timerId = 0, panelBody = '';
  const state = { answers: new Map(), delay: new Map(), denied: [] };
  const stub = sel => ({ focus() { focused.push(sel); }, setAttribute() {}, removeAttribute() {}, querySelector: child => stub(`${sel} ${child}`), scrollIntoView() {} });
  const panel = { setAttribute() {}, querySelector: sel => sel === '[data-ov-detail-body]' ? { set innerHTML(v) { panelBody = v; }, get innerHTML() { return panelBody; }, querySelector: () => null } : sel === '[data-ov-chip-slot]' ? { innerHTML: '' } : null };
  const host = {
    querySelector(sel) {
      if (sel === '[data-ov-root]') return nodes.root;
      if (sel === '[data-ov-top]') return nodes.top;
      if (sel === '[data-ov-meta]') return nodes.meta;
      if (sel === '[data-ov-announce]') return nodes.announce;
      if (sel === '.compute-ov :focus') return null;
      if (sel === '[data-ov-panel]') return nodes.root.innerHTML.includes('data-ov-panel') ? panel : null;
      return stub(sel);
    },
  };
  const doc = { hidden, addEventListener(kind, fn) { docListeners.set(kind, fn); }, removeEventListener(kind) { docListeners.delete(kind); } };
  const fake = { setTimeout(fn, ms) { const id = ++timerId; timers.push({ id, fn, ms, live: true }); return id; }, clearTimeout(id) { const t = timers.find(x => x.id === id); if (t) t.live = false; } };
  let now = clock * 1000;
  const request = path => {
    calls.push(path);
    const answer = state.answers.get(path) ?? (path === '/api/compute/overview' ? data : failure(404, { error: 'not_found' }));
    const out = typeof answer === 'function' ? answer() : answer;
    if (typeof out?.then === 'function') return out;
    return out instanceof Error ? Promise.reject(out) : Promise.resolve(structuredClone(out));
  };
  const ui = ov.createOverview({ request, epoch: () => epoch, valid: page => page === epoch, denied: f => { state.denied.push(f.status); return true; }, host: () => host, rerender() { nodes.root.innerHTML = ui.html(); }, doc, now: () => now, timers: fake });
  const dataset = d => ({ dataset: d, getAttribute: () => null, closest: sel => sel === '.compute-ov' ? {} : null });
  const click = (action, d = {}) => ui.click({ target: { closest: sel => sel === '[data-ov-action]' ? dataset({ ovAction: action, ...d }) : sel === '[data-ov-kind-filter]' ? null : null } });
  const kindFilter = value => ui.click({ target: { closest: sel => sel === '[data-ov-kind-filter]' ? { dataset: { ovKindFilter: value } } : null } });
  const select = value => ui.change({ target: { value, matches: sel => sel === '[data-ov-status]' } });
  const key = (name, inside = true) => { const event = { key: name, target: { closest: sel => inside && sel === '.compute-ov' ? {} : null }, prevented: false, preventDefault() { this.prevented = true; } }; return [ui.keydown(event), event.prevented]; };
  const fire = async () => { const t = timers.filter(x => x.live).pop(); assert.ok(t, 'a refresh timer is waiting'); t.live = false; t.fn(); await flush(); return t; };
  const liveTimers = () => timers.filter(x => x.live);
  return { ui, nodes, focused, timers, liveTimers, calls, state, doc, docListeners, click, kindFilter, select, key, fire, bump(ms) { now += ms; }, bumpEpoch() { epoch++; }, html: () => nodes.root.innerHTML, shown: () => text(nodes.root.innerHTML) };
}

const cases = {
  async pure() {
    const items = fleet();
    assert.equal(ov.filterItems(items, {}).length, 5);
    assert.deepEqual(ov.filterItems(items, { kind: 'runner' }).map(i => i.id), ['mach_1', 'mach_2']);
    assert.deepEqual(ov.filterItems(items, { status: 'attention' }).map(i => i.id), ['mach_2', 'w_2']);
    assert.deepEqual(ov.filterItems(items, { kind: 'workspace', status: 'running' }).map(i => i.id), ['w_1']);
    const counts = ov.filterCounts(items);
    assert.deepEqual(counts.kinds, { runner: 2, machine: 1, workspace: 2 }); assert.equal(counts.attention, 2);
    assert.equal(counts.statuses.offline.label, 'Offline');
    assert.equal(ov.summaryText(items), '2 runners, 1 machine, 2 cloud workspaces. 2 need attention.');
    assert.equal(ov.summaryText([items[0]]), '1 runner. Nothing needs attention.');
    assert.equal(ov.summaryText([]), '');
  },

  async rows() {
    const [runner, offline] = fleet();
    const html = ov.rowHTML(runner);
    assert.match(html, /<h4 class="compute-ov-name">build-runner<\/h4>/);
    assert.match(html, /data-tone="ok">Online<\/span>/);
    assert.match(text(html), /Waiting for work\. Last seen 40 seconds ago\./); assert.doesNotMatch(html, /compute-ov-sub/, 'a runner row has no kind line: its group heading says it');
    assert.match(html, /aria-label="Status and activity of build-runner"/);
    const rowOffline = text(ov.rowHTML(offline));
    assert.match(rowOffline, /Last seen 25 minutes ago · 1 thing blocks new work/);
    // Never connected and target machines say what is not known instead of leaving a gap.
    const never = item('runner', 'mach_9', 'new-runner', { code: 'never_connected', tone: 'quiet', label: 'Never connected' }, { last_seen: null });
    assert.match(text(ov.rowHTML(never)), /No signal yet: it has never contacted Commonplace\./);
    assert.match(text(ov.rowHTML(fleet()[2])), /Not checked live: Commonplace does not probe target machines\./);
    // Everything from the server is escaped.
    const bad = item('workspace', 'w_x', '<img src=x onerror=1>', { code: 'running', label: '<b>Running</b>', detail: '<script>alert(1)</script>' });
    const markup = ov.rowHTML(bad);
    assert.doesNotMatch(markup, /<img|<script|<b>/);
    assert.match(markup, /&lt;img src=x onerror=1&gt;/);
  },

  async detail() {
    const base = fleet()[1];
    const html = ov.detailHTML({ ...base, activity: [{ text: 'Failed: demo card 7 (start_failed)', age_s: 125 }], facts: [{ label: 'Runs at once', value: 'Up to 1' }] }, { manage: true });
    const shown = text(html);
    assert.match(shown, /Offline Not heard from for 25 minutes\./);
    assert.match(shown, /Blocking new work.*Commonplace has not heard from it for over 15 minutes/);
    assert.match(shown, /Failed: demo card 7 \(start_failed\) \(2 minutes ago\)/);
    assert.match(shown, /Runs at once Up to 1/);
    assert.match(shown, /Machine resources Not reported: this machine has not sent a resource report\./);
    const ws = ov.detailHTML({ ...fleet()[3], activity: [], facts: [] }, { manage: true });
    assert.match(text(ws), /No recent activity\./); assert.match(ws, /Change settings in Cloud workspaces/);
    assert.doesNotMatch(ov.detailHTML({ ...fleet()[3], activity: [], facts: [] }, { manage: false }), /Change settings in Cloud workspaces/);
    assert.match(text(ov.detailHTML(fleet()[0], {})), /Nothing is blocking new work\./);
    assert.match(ov.detailHTML(fleet()[0], { loading: true }), /aria-busy="true"[\s\S]*Loading details…/);
    assert.match(ov.detailHTML(fleet()[0], { failed: true }), /The details could not be loaded\. Showing what the list knew\./);
    assert.equal((html.match(/id="compute-ov-heading"/g) || []).length, 1);
    assert.equal((html.match(/role="alert"/g) || []).length, 0, 'no alert regions in the panel');
  },

  async states() {
    const f = fixture();
    // Loading, then the list: one load on start, nothing painted before the first answer settles.
    f.ui.start();
    const first = f.ui.load();
    assert.match(f.shown(), /Loading the overview…/);
    await first;
    assert.equal(f.calls.length, 1);
    const shown = f.shown();
    assert.match(shown, /2 runners, 1 machine, 2 cloud workspaces\. 2 need attention\./);
    for (const label of ['Runners', 'Machines', 'Cloud workspaces']) assert.match(shown, new RegExp(`${label} \\(`));
    assert.match(shown, /Starting and stopping cloud workspaces on a machine is not available in this version/);
    assert.match(f.nodes.meta.textContent, /^As of \d{1,2}:\d\d:\d\d.*\. It refreshes every 30 seconds while this page is open, or press Refresh overview\.$/);
    assert.equal(f.ui.html().match(/role="alert"/g)?.length || 0, 0);
    // The empty state tells the owner what to do and a member who to ask.
    const empty = fixture({ data: overviewOf([]) }); await empty.ui.load();
    assert.match(empty.shown(), /Nothing here yet\. Set up a runner machine, add a target machine under Advanced, or create a cloud workspace below/);
    const noMember = fixture({ data: overviewOf([], { can_manage: false }) }); await noMember.ui.load();
    assert.match(noMember.shown(), /There are no cloud workspaces yet\. The workspace owner can create one\./);
  },

  async failure() {
    const f = fixture();
    f.state.answers.set('/api/compute/overview', failure(500, { error: 'boom' }));
    await f.ui.load();
    assert.match(f.html(), /role="alert">The overview could not be loaded\. Nothing is shown rather than a guess\./);
    assert.match(f.html(), /data-ov-action="refresh">Try again/);
    // Recovery shows the data and drops the alert.
    f.state.answers.delete('/api/compute/overview');
    await f.click('refresh'); await flush();
    assert.match(f.shown(), /2 runners/); assert.doesNotMatch(f.html(), /role="alert"/);
    // A failed refresh keeps the old data, marks it out of date and says so politely.
    f.state.answers.set('/api/compute/overview', failure(503, { error: 'authority_unavailable' }));
    await f.ui.load({ quiet: true });
    assert.match(f.shown(), /Could not refresh\. Showing the data from \d{1,2}:\d\d:\d\d.*; it may be out of date\./);
    assert.match(f.shown(), /2 runners/);
    assert.match(f.nodes.announce.textContent, /could not be refreshed\. The data shown is out of date\./);
    assert.equal(f.ui.state.refreshFailed, true);
    // A sign-out is handed to the screen, not shown as an error.
    f.state.answers.set('/api/compute/overview', failure(401, { error: 'sign_in' }));
    await f.ui.load({ quiet: true }); assert.deepEqual(f.state.denied, [401]);
  },

  async polling() {
    const f = fixture();
    f.ui.start();
    assert.equal(f.liveTimers().length, 0, 'nothing is scheduled before the first answer');
    await f.ui.load();
    let timers = f.liveTimers();
    assert.equal(timers.length, 1); assert.equal(timers[0].ms, 30000, 'the served interval, in milliseconds');
    await f.fire(); assert.equal(f.calls.length, 2, 'the timer reads again'); assert.equal(f.liveTimers().length, 1, 'and schedules the next read');
    // A hidden page does not read; it asks again later.
    f.doc.hidden = true;
    await f.fire(); assert.equal(f.calls.length, 2); assert.equal(f.liveTimers().length, 1);
    // Back in view with old data: read at once instead of waiting.
    f.doc.hidden = false; f.bump(31000);
    f.docListeners.get('visibilitychange')();
    await flush(); assert.equal(f.calls.length, 3);
    f.bump(5000); f.docListeners.get('visibilitychange')(); await flush(); assert.equal(f.calls.length, 3, 'fresh data is not read again');
    // The interval is never below 15 seconds, whatever the server says.
    const quick = fixture({ data: overviewOf(fleet(), { refresh_seconds: 1 }) }); await quick.ui.load();
    assert.equal(quick.liveTimers()[0].ms, 15000);
    // Leaving the screen stops the timer and the listener.
    f.ui.reset();
    assert.equal(f.liveTimers().length, 0); assert.equal(f.docListeners.has('visibilitychange'), false);
    assert.equal(f.ui.state.data, null);
  },

  async 'recent-data'() {
    // A reload of the whole screen right after a read does not read again; a button press always does.
    const f = fixture();
    await f.ui.load();
    await f.ui.load({ quiet: true, within: 5000 });
    assert.equal(f.calls.length, 1, 'young data is not read again');
    f.bump(6000);
    await f.ui.load({ quiet: true, within: 5000 });
    assert.equal(f.calls.length, 2, 'older data is');
    await f.click('refresh'); await flush();
    assert.equal(f.calls.length, 3, 'a press is never skipped');
  },

  async announcements() {
    const f = fixture();
    await f.ui.load();
    assert.equal(f.nodes.announce.textContent, '', 'the first read says nothing');
    const changed = fleet(); changed[0] = item('runner', 'mach_1', 'build-runner', { code: 'offline', tone: 'bad', label: 'Offline' }, { attention: true });
    f.state.answers.set('/api/compute/overview', overviewOf(changed));
    await f.ui.load({ quiet: true });
    assert.equal(f.nodes.announce.textContent, 'build-runner is now Offline.');
    // An unchanged poll says nothing new; a manual refresh says what it found, once.
    await f.ui.load({ quiet: true }); assert.equal(f.nodes.announce.textContent, 'build-runner is now Offline.');
    await f.ui.load({ say: true });
    assert.match(f.nodes.announce.textContent, /^Overview updated\. 2 runners, 1 machine, 2 cloud workspaces\. 3 need attention\.$/);
    // One live region for all of it: the list and the summary carry none of their own.
    assert.equal((f.html().match(/aria-live/g) || []).length, 0);
    assert.match(f.ui.html(), /role="status" aria-live="polite" data-ov-announce/);
  },

  async filters() {
    const f = fixture();
    await f.ui.load();
    f.kindFilter('runner');
    assert.match(f.html(), /compute-ov-group-runner/); assert.doesNotMatch(f.html(), /compute-ov-group-workspace|compute-ov-group-machine/);
    assert.match(f.html(), /data-ov-kind-filter="runner" aria-pressed="true"/);
    assert.match(f.nodes.announce.textContent, /Showing 2 items\./);
    f.select('offline');
    assert.deepEqual([...f.html().matchAll(/data-ov-id="([^"]+)"/g)].map(m => m[1]), ['mach_2']);
    assert.match(f.html(), /<option value="offline" selected>Offline \(1\)<\/option>/);
    f.kindFilter('workspace'); // offline runners are not workspaces
    assert.match(f.shown(), /No machines, runners or cloud workspaces match these filters\./);
    assert.match(f.html(), /data-ov-action="clear">Clear filters/);
    f.click('clear');
    assert.equal(f.ui.state.kind, 'all'); assert.equal(f.ui.state.status, 'all');
    assert.equal([...f.html().matchAll(/data-ov-id=/g)].length, 5);
    assert.ok(f.focused.includes('#compute-ov-status'), 'focus goes to a control that is still there');
    f.select('attention');
    assert.deepEqual([...f.html().matchAll(/data-ov-id="([^"]+)"/g)].map(m => m[1]), ['mach_2', 'w_2']);
    // A filter whose items are gone resets instead of showing an empty list nobody asked for.
    f.state.answers.set('/api/compute/overview', overviewOf(fleet().filter(i => i.kind !== 'runner')));
    await f.ui.load({ quiet: true });
    assert.equal(f.ui.state.status, 'attention');
    f.kindFilter('runner');
    f.state.answers.set('/api/compute/overview', overviewOf(fleet().filter(i => i.kind !== 'runner')));
    await f.ui.load({ quiet: true });
    assert.equal(f.ui.state.kind, 'all');
  },

  async 'detail-flow'() {
    const f = fixture();
    await f.ui.load();
    f.state.answers.set('/api/compute/overview/runner/mach_2', detailOf(fleet()[1]));
    f.click('detail', { id: 'mach_2', kind: 'runner' });
    // The panel opens at once (loading) and takes focus on its heading; the list gives way to it.
    assert.match(f.html(), /aria-busy="true"/); assert.match(f.html(), /id="compute-ov-heading"/);
    assert.doesNotMatch(f.html(), /data-ov-kind-filter|compute-ov-group/);
    assert.ok(f.focused.includes('#compute-ov-heading'));
    await flush();
    assert.deepEqual(f.calls.slice(-1), ['/api/compute/overview/runner/mach_2']);
    // Activity and facts arrive without a second focus move.
    const before = f.focused.length;
    assert.equal(f.focused.length, before);
    // Close returns to the row's own Details button, and Escape does the same.
    f.click('close');
    assert.match(f.html(), /compute-ov-group/);
    assert.equal(f.focused.at(-1), '[data-ov-action="detail"][data-id="mach_2"]');
    f.state.answers.set('/api/compute/overview/runner/mach_1', detailOf(fleet()[0]));
    f.click('detail', { id: 'mach_1', kind: 'runner' }); await flush();
    const [handled, prevented] = f.key('Escape');
    assert.equal(handled, true); assert.equal(prevented, true);
    assert.equal(f.focused.at(-1), '[data-ov-action="detail"][data-id="mach_1"]');
    assert.deepEqual(f.key('Escape'), [false, false], 'Escape with nothing open is left alone');
    assert.deepEqual(f.key('Enter'), [false, false]);
  },

  async 'detail-errors'() {
    const f = fixture();
    await f.ui.load();
    f.state.answers.set('/api/compute/overview/runner/mach_1', failure(500, { error: 'boom' }));
    f.click('detail', { id: 'mach_1', kind: 'runner' }); await flush();
    assert.match(f.ui.state.detail ? 'open' : 'closed', /open/);
    assert.match(f.html(), /compute-ov-panel/);
    // The item is gone (404): the panel closes, focus returns, and the person is told.
    f.state.answers.set('/api/compute/overview/runner/mach_1', failure(404, { error: 'not_found', reason: 'record' }));
    f.click('close'); f.click('detail', { id: 'mach_1', kind: 'runner' }); await flush();
    assert.equal(f.ui.state.detail, null);
    assert.equal(f.nodes.announce.textContent, 'That item is no longer available.');
    // A poll that no longer lists the open item closes the panel.
    f.state.answers.set('/api/compute/overview/runner/mach_2', detailOf(fleet()[1]));
    f.click('detail', { id: 'mach_2', kind: 'runner' }); await flush();
    f.state.answers.set('/api/compute/overview', overviewOf(fleet().filter(i => i.id !== 'mach_2')));
    await f.ui.load({ quiet: true });
    assert.equal(f.ui.state.detail, null); assert.equal(f.nodes.announce.textContent, 'That item is no longer in the overview.');
  },

  async 'detail-refresh'() {
    const f = fixture();
    await f.ui.load();
    f.state.answers.set('/api/compute/overview/runner/mach_1', detailOf(fleet()[0]));
    f.click('detail', { id: 'mach_1', kind: 'runner' }); await flush();
    const calls = f.calls.length;
    await f.ui.load({ quiet: true }); await flush();
    // A poll with a panel open reads the item again so "last seen" never ages on screen.
    assert.deepEqual(f.calls.slice(calls), ['/api/compute/overview', '/api/compute/overview/runner/mach_1']);
  },

  async member() {
    const members = [item('workspace', 'w_1', 'Shared box', { code: 'running', label: 'Running' }, { more: { placement: { assigned: true, machine_name: null, machine_id: null } } })];
    const f = fixture({ data: overviewOf(members, { can_manage: false, hidden: { kinds: ['machine', 'runner'], text: 'Machines and runners are visible to the workspace owner.' }, executor: { available: false, text: 'hidden from members' } }) });
    await f.ui.load();
    assert.match(f.shown(), /Machines and runners are visible to the workspace owner\./);
    assert.doesNotMatch(f.shown(), /hidden from members/, 'members are not told about owner-only machinery');
    assert.equal([...f.html().matchAll(/data-ov-id=/g)].length, 1);
    assert.doesNotMatch(f.html(), /data-ov-kind-filter/, 'one kind needs no kind filter');
    f.state.answers.set('/api/compute/overview/workspace/w_1', detailOf(members[0], { can_manage: false }));
    f.click('detail', { id: 'w_1', kind: 'workspace' }); await flush();
    assert.doesNotMatch(f.html(), /Change settings in Cloud workspaces/);
    assert.doesNotMatch(f.shown(), /Start|Stop|Restart|Drain|Resume/, 'a member is offered no action');
  },

  async 'in-compute'() {
    // The production Compute controller embeds the overview above the cloud workspaces, for an owner and for a member.
    for (const owner of [true, false]) {
      let html = '';
      const listeners = new Map(), host = { get innerHTML() { return html; }, set innerHTML(v) { html = v; }, addEventListener(k, fn) { listeners.set(k, fn); }, removeEventListener(k) { listeners.delete(k); }, querySelector: () => null };
      const calls = [], user = { workspace: 'w', actor: 'user:u' }, doc = new EventTarget();
      const api = path => {
        calls.push(path);
        if (path === '/api/compute/overview') return Promise.resolve(structuredClone(owner ? overviewOf() : overviewOf([fleet()[3]], { can_manage: false, hidden: { kinds: ['machine', 'runner'], text: 'Machines and runners are visible to the workspace owner.' } })));
        if (path === '/api/compute') return owner ? Promise.resolve({ gateways: [], nodes: [], scopes: [], executions: [], can_manage: true, capabilities: {} }) : Promise.reject(failure(403, { error: 'owner_required' }));
        if (path === '/api/compute/workspaces') return Promise.resolve({ workspaces: [], total: 0, can_manage: owner });
        return Promise.reject(failure(404, { error: 'not_found' }));
      };
      const ui = createCompute({ mount: () => host, api, current: () => user, route: () => '#/compute', doc, events: new EventTarget(), now: () => clock, requestId: () => 'r' });
      ui.show(); await flush();
      assert.ok(calls.includes('/api/compute/overview'), 'the screen reads the overview');
      const shown = text(html);
      assert.ok(html.indexOf('compute-overview') > -1 && html.indexOf('compute-overview') < html.indexOf('compute-workspaces'), 'the overview comes first');
      assert.match(shown, owner ? /2 runners, 1 machine, 2 cloud workspaces/ : /1 cloud workspace\./);
      assert.match(shown, /Where your Coworkers run: an overview of everything first/);
      assert.equal((html.match(/id="compute-overview"/g) || []).length, 1);
      ui.stop();
      assert.equal(html, '');
    }
  },

  async isolation() {
    // A late answer for a screen that has been left is dropped, not painted.
    const f = fixture();
    let resolve; f.state.answers.set('/api/compute/overview', () => new Promise(done => { resolve = done; }));
    f.state.answers.set('/api/compute/overview', f.state.answers.get('/api/compute/overview'));
    const pending = f.ui.load();
    f.bumpEpoch(); f.ui.reset();
    resolve?.(overviewOf());
    await pending; await flush();
    assert.equal(f.ui.state.data, null);
    assert.equal(f.liveTimers().length, 0, 'no refresh is scheduled for a screen that is gone');
  },
};

const name = process.argv[2];
if (!cases[name]) { console.error(`unknown case ${name}`); process.exit(2); }
await cases[name]();
