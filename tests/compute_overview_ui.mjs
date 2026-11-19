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

const seen = (age, extra = {}) => ({ at: clock - age, age_s: age, source: 'machine_connection', label: `Last seen ${age} seconds ago`, prefix: 'Last seen', suffix: '', when: `${age} seconds ago`, ...extra });
const item = (kind, id, name, status, extra = {}) => ({
  kind, id, name, subtitle: kind === 'runner' ? 'Runner machine' : kind === 'machine' ? 'Target machine' : 'ghcr.io/acme/tools:1.2',
  status: { code: status.code, tone: status.tone || 'ok', label: status.label, detail: status.detail || `${status.label} detail.` },
  last_seen: extra.last_seen === undefined ? seen(40) : extra.last_seen,
  health: { state: extra.attention ? 'attention' : 'ok', blockers: extra.blockers || [], issues: extra.issues || [] },
  usage: { summary: extra.usage || 'Usage line.', runs: { text: '1 of 2 runs in progress' }, resources: { text: 'Not reported: this machine has not sent a resource report.' }, spend: { text: 'No runs in the last 30 days.' }, limits: { text: '2 CPU cores, 4 GiB memory, 20 GiB storage (limits)' }, measured: { text: 'Not measured yet.' } },
  placement: {}, basis: extra.basis ?? { seq: 0 }, actions: [], ...extra.more,
});
const fleet = () => [
  item('runner', 'mach_1', 'build-runner', { code: 'online', label: 'Online', detail: 'Waiting for work. Last seen 40 seconds ago.' }),
  item('runner', 'mach_2', 'edge-runner', { code: 'offline', tone: 'bad', label: 'Offline', detail: 'Not heard from for 25 minutes.' }, { attention: true, blockers: [{ code: 'offline', text: 'Commonplace has not heard from it for over 15 minutes, so new runs would wait.' }], last_seen: seen(1500, { label: 'Last seen 25 minutes ago', when: '25 minutes ago' }) }),
  item('machine', 'n_1', 'Fixture machine', { code: 'not_verified', tone: 'wait', label: 'Not verified', detail: 'Commonplace has not verified this machine yet.' }, { last_seen: null }),
  item('workspace', 'w_1', 'Build box', { code: 'running', label: 'Running', detail: 'Reported running 2 minutes ago.' }, { last_seen: seen(120, { prefix: 'Last reported', label: 'Last reported 2 minutes ago' }) }),
  item('workspace', 'w_2', 'Old box', { code: 'unknown', tone: 'quiet', label: 'Unknown', detail: 'Last reported Running 14 minutes ago and nothing newer since.' }, { attention: true, last_seen: seen(840, { prefix: 'Last reported', label: 'Last reported 14 minutes ago' }) }),
];
const overviewOf = (items = fleet(), extra = {}) => ({
  as_of: new Date(clock * 1000).toISOString().replace('.000', ''), refresh_seconds: 30, thresholds: { green_within_s: 180, red_after_s: 900, report_stale_s: 600 }, can_manage: true, executor: { available: false, text: 'Starting and stopping cloud workspaces on a machine is not available in this version. Requests are saved and wait.' },
  counts: {}, items, hidden: null, truncated: {}, ...extra,
});
const detailOf = (base, extra = {}) => ({ item: { ...base, activity: [{ at: 1, age_s: 60, text: 'Created by the owner' }], facts: [{ label: 'Image', value: 'ghcr.io/acme/tools:1.2' }], ...extra }, can_manage: true });

function fixture({ data = overviewOf(), hidden = false, narrow = false, signature = () => '', skew = 0 } = {}) {
  const slot = () => { const n = { writes: 0, html: '' }; Object.defineProperty(n, 'innerHTML', { get() { return n.html; }, set(v) { n.html = v; n.writes++; } }); return n; };
  const nodes = { root: slot(), top: slot(), meta: { textContent: '' }, announce: { textContent: '' }, summary: { textContent: '' }, stale: (() => { const n = { text: '', hidden: true, writes: 0 }; Object.defineProperty(n, 'textContent', { get() { return n.text; }, set(v) { n.text = v; n.writes++; } }); return n; })(), state: slot(), notes: slot(), panel: slot(), filters: slot(), list: slot() };
  const focused = [], timers = [], calls = [], docListeners = new Map(), flags = { statusFocused: false };
  let epoch = 1, timerId = 0;
  const state = { answers: new Map(), delay: new Map(), denied: [], signature };
  const stub = sel => ({ focus() { focused.push(sel); }, setAttribute() {}, removeAttribute() {}, matches: q => q === ':focus' && sel === '#compute-ov-status' && flags.statusFocused, querySelector: child => stub(`${sel} ${child}`), scrollIntoView() {} });
  const body = { html: '', writes: 0, get innerHTML() { return body.html; }, set innerHTML(v) { body.html = v; body.writes++; }, querySelector: () => null };
  const chip = { html: '', writes: 0, get innerHTML() { return chip.html; }, set innerHTML(v) { chip.html = v; chip.writes++; } };
  const panel = { setAttribute() {}, querySelector: sel => sel === '[data-ov-detail-body]' ? body : sel === '[data-ov-chip-slot]' ? chip : null };
  // The rows of the list slot as the clock tick sees them: an id, a status code and a writable health line.
  const rows = () => [...nodes.list.html.matchAll(/<li class="compute-row compute-ov-row" data-ov-id="([^"]+)"[^>]*data-status="([^"]+)"[\s\S]*?<p class="compute-ov-health" data-ov-health>([^<]*)<\/p>/g)].map(m => {
    const health = { textContent: m[3].replace(/&#39;/g, "'") };
    return { dataset: { ovId: m[1], status: m[2] }, health, querySelector: q => q === '[data-ov-health]' ? health : null };
  });
  const host = {
    querySelector(sel) {
      if (sel === '[data-ov-root]') return nodes.root;
      if (sel === '[data-ov-top]') return nodes.top;
      if (sel === '[data-ov-meta]') return nodes.meta;
      if (sel === '[data-ov-announce]') return nodes.announce;
      if (sel === '[data-ov-summary]') return nodes.summary;
      if (sel === '[data-ov-stale]') return nodes.stale;
      const m = /^\[data-ov-slot="(\w+)"\]$/.exec(sel);
      if (m) return nodes[m[1]];
      if (sel === '.compute-ov :focus') return null;
      if (sel === '[data-ov-panel]') return nodes.panel.html.includes('data-ov-panel') ? panel : null;
      return stub(sel);
    },
    querySelectorAll(sel) { return sel === "[data-ov-slot='list'] .compute-ov-row" ? rows() : []; },
  };
  const doc = { hidden, defaultView: { matchMedia: () => ({ matches: narrow }) }, addEventListener(kind, fn) { docListeners.set(kind, fn); }, removeEventListener(kind) { docListeners.delete(kind); } };
  const fake = { setTimeout(fn, ms) { const id = ++timerId; timers.push({ id, fn, ms, live: true }); return id; }, clearTimeout(id) { const t = timers.find(x => x.id === id); if (t) t.live = false; } };
  let now = (clock + skew) * 1000; // the browser's wall clock, skewed from the server's by ``skew`` seconds
  let monoNow = 5_000_000; // the monotonic clock: only time that really passes moves it
  const request = path => {
    calls.push(path);
    const answer = state.answers.get(path) ?? (path === '/api/compute/overview' ? data : failure(404, { error: 'not_found' }));
    const out = typeof answer === 'function' ? answer() : answer;
    if (typeof out?.then === 'function') return out;
    return out instanceof Error ? Promise.reject(out) : Promise.resolve(structuredClone(out));
  };
  const ui = ov.createOverview({ request, epoch: () => epoch, valid: page => page === epoch, denied: f => { state.denied.push(f.status); return true; }, host: () => host, rerender() { nodes.root.innerHTML = ui.html(); }, doc, now: () => now, timers: fake, mono: () => monoNow, workspaceSignature: () => state.signature() });
  const dataset = d => ({ dataset: d, getAttribute: () => null, closest: sel => sel === '.compute-ov' ? {} : null });
  const click = (action, d = {}) => ui.click({ target: { closest: sel => sel === '[data-ov-action]' ? dataset({ ovAction: action, ...d }) : null } });
  const kindFilter = value => ui.click({ target: { closest: sel => sel === '[data-ov-kind-filter]' ? { dataset: { ovKindFilter: value } } : null } });
  const select = value => ui.change({ target: { value, matches: sel => sel === '[data-ov-status]' } });
  const key = (name, inside = true) => { const event = { key: name, target: { closest: sel => inside && sel === '.compute-ov' ? {} : null }, prevented: false, preventDefault() { this.prevented = true; } }; return [ui.keydown(event), event.prevented]; };
  // The timers: the poll (interval from the server) and the clock (10 s). ``fire`` runs the newest live poll timer.
  const fire = async (ms = 30000) => { const t = timers.filter(x => x.live && x.ms === ms).pop(); assert.ok(t, `a ${ms} ms timer is waiting`); t.live = false; t.fn(); await flush(); return t; };
  const liveTimers = (ms) => timers.filter(x => x.live && (ms === undefined || x.ms === ms));
  const slotsHTML = () => ['state', 'notes', 'panel', 'filters', 'list'].map(n => nodes[n].html).join('');
  return { ui, nodes, focused, timers, liveTimers, calls, state, doc, docListeners, flags, body, chip, click, kindFilter, select, key, fire, bump(ms) { now += ms; monoNow += ms; }, step(ms) { now += ms; }, bumpEpoch() { epoch++; }, html: slotsHTML, shown: () => text(`${nodes.summary.textContent} ${nodes.stale.textContent} ${slotsHTML()}`) };
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
    assert.equal(ov.summaryText(items), '2 runners, 1 machine, 2 cloud workspaces. 2 need attention: edge-runner, Old box.');
    assert.equal(ov.summaryText([items[0]]), '1 runner. Nothing needs attention.');
    const many = ['a', 'b', 'c', 'd', 'e'].map(n => item('runner', n, `Runner ${n}`, { code: 'offline', label: 'Offline' }, { attention: true }));
    assert.match(ov.summaryText(many), /5 need attention: Runner a, Runner b, Runner c and 2 more\.$/);
    assert.equal(ov.summaryText([]), '');
    // A workspace waiting for a machine needs nothing from anyone, and the sentence says what it waits for.
    const waiting = item('workspace', 'w_m', 'Machine-less', { code: 'not_started', tone: 'quiet', label: 'Saved, not started' }, { last_seen: null, more: { placement: { assigned: false } } });
    assert.equal(ov.summaryText([waiting]), '1 cloud workspace. Nothing needs attention. 1 cloud workspace is waiting for a machine.');
    assert.match(ov.summaryText([waiting, { ...waiting, id: 'w_n' }]), /2 cloud workspaces are waiting for a machine\.$/);
  },

  async rows() {
    const [runner, offline] = fleet();
    const html = ov.rowHTML(runner);
    assert.doesNotMatch(html, /Next step/, 'a healthy row has no next step');
    assert.match(html, /<h4 class="compute-ov-name">build-runner<\/h4>/);
    assert.match(html, /data-tone="ok" data-ov-chip>Online<\/span>/);
    assert.match(text(html), /Waiting for work\. Last seen 40 seconds ago\./); assert.doesNotMatch(html, /compute-ov-sub/, 'a runner row has no kind line: its group heading says it');
    assert.match(html, /aria-label="Status and activity of build-runner"/);
    const rowOffline = text(ov.rowHTML(offline));
    assert.match(rowOffline, /Last seen 25 minutes ago · 1 thing blocks new work: offline/);
    // Blockers are named, not only counted; one with no short name is counted without a made-up one.
    const two = item('workspace', 'w_n', 'Two blockers', { code: 'not_started', tone: 'quiet', label: 'Saved, not started' }, { blockers: [{ code: 'machine_not_verified', text: 'x' }, { code: 'not_available', text: 'y' }] });
    assert.match(text(ov.rowHTML(two)), /2 things block new work: its machine is not verified, starting is not available yet/);
    const odd = item('workspace', 'w_o', 'Odd', { code: 'not_started', tone: 'quiet', label: 'Saved, not started' }, { blockers: [{ code: 'brand_new', text: 'z' }] });
    assert.match(text(ov.rowHTML(odd)), /1 thing blocks new work Usage line\./);
    const next = item('runner', 'mach_8', 'slow-runner', { code: 'slow', tone: 'wait', label: 'Slow to report' });
    next.status.next = 'Wait a little.';
    assert.match(text(ov.rowHTML(next)), /Next step: Wait a little\./);
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
    assert.match(shown, /Status and activity edge-runner Offline Close Not heard from for 25 minutes\./, 'Close is in the header, before the body');
    assert.equal((html.match(/data-ov-action="close"/g) || []).length, 1, 'one Close, in the header');
    assert.match(shown, /Blocking new work.*Commonplace has not heard from it for over 15 minutes/);
    assert.doesNotMatch(text(ov.detailHTML({ ...fleet()[3], activity: [], facts: [] }, {})), /What stops it starting/, 'one term for what blocks new work');
    assert.match(text(ov.detailHTML({ ...fleet()[3], activity: [], facts: [] }, {})), /Blocking new work/);
    assert.match(shown, /Failed: demo card 7 \(start_failed\) \(2 minutes ago\)/);
    assert.match(shown, /Runs at once Up to 1/);
    assert.match(shown, /Machine resources Not reported: this machine has not sent a resource report\./);
    const ws = ov.detailHTML({ ...fleet()[3], activity: [], facts: [] }, { manage: true });
    assert.match(text(ws), /No recent activity\./); assert.match(ws, /Change settings in Cloud workspaces/);
    assert.doesNotMatch(ov.detailHTML({ ...fleet()[3], activity: [], facts: [] }, { manage: false }), /Change settings in Cloud workspaces/);
    assert.match(text(ov.detailHTML(fleet()[0], {})), /Nothing is blocking new work\./);
    assert.match(ov.detailHTML(fleet()[0], { loading: true }), /aria-busy="true"[\s\S]*Loading details…/);
    assert.match(ov.detailHTML(fleet()[0], { failed: true }), /The details could not be loaded\. Showing what the list knew\./);
    for (const kind of ['runner', 'machine', 'workspace']) assert.doesNotMatch(ov.detailHTML({ ...fleet()[0], kind, usage: {}, activity: [], facts: [] }, {}), /undefined/, `${kind}: a missing usage field is left out`);
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
    assert.match(shown, /2 runners, 1 machine, 2 cloud workspaces\. 2 need attention: edge-runner, Old box\./);
    for (const label of ['Runners', 'Machines', 'Cloud workspaces']) assert.match(shown, new RegExp(`${label} \\(`));
    assert.match(shown, /Starting and stopping cloud workspaces on a machine is not available in this version/);
    assert.match(f.nodes.meta.textContent, /^Updated just now \(as of \d{1,2}:\d\d:\d\d.*\)\. It refreshes every 30 seconds while this tab is visible, or use Refresh above\.$/);
    assert.match(f.nodes.summary.textContent, /2 runners, 1 machine, 2 cloud workspaces\. 2 need attention: edge-runner, Old box\./, 'the closed fold still says what needs attention');
    assert.equal(f.nodes.stale.hidden, true);
    // The one Refresh button is the page's: the overview has no button of its own named for refreshing.
    assert.doesNotMatch(f.ui.html(), /Update overview|data-ov-action="refresh"/);
    // The empty state tells the owner what to do and a member who to ask.
    const empty = fixture({ data: overviewOf([]) }); await empty.ui.load();
    assert.match(empty.shown(), /Nothing here yet\. Create a cloud workspace below\. A machine can be chosen later\./);
    assert.doesNotMatch(empty.shown(), /runner machine|Advanced/, 'no jargon in the first-run copy');
    // Never "Nothing here yet" while the workspaces list holds records: the overview says it is catching up.
    const behind = fixture({ data: overviewOf([]), signature: () => 'w_1:1' }); await behind.ui.load();
    assert.match(behind.shown(), /The overview is catching up\. Your cloud workspaces are listed below\./);
    assert.doesNotMatch(behind.shown(), /Nothing here yet/);
    const noMember = fixture({ data: overviewOf([], { can_manage: false }) }); await noMember.ui.load();
    assert.match(noMember.shown(), /No cloud workspaces yet\. The workspace owner can create one\./);
    // The headline says the state and the body only what to do: loading and empty never say one thing twice.
    assert.equal((empty.shown().match(/Nothing here yet/g) || []).length, 1);
    const loading = fixture(); const pending = loading.ui.load(); assert.equal((loading.shown().match(/Loading the overview/g) || []).length, 1); await pending;
    // A closed fold: on a phone, above five items, and open for an empty or failed overview.
    const phone = fixture({ narrow: true }); await phone.ui.load();
    assert.doesNotMatch(phone.ui.html(), /data-ov-fold open>/, 'a phone folds');
    const broken = fixture({ narrow: true }); broken.state.answers.set('/api/compute/overview', failure(500, { error: 'boom' })); await broken.ui.load();
    assert.match(broken.ui.html(), /data-ov-fold open>/, 'a failed first read opens the fold so Try again is reachable');
    const small = fixture({ data: overviewOf(fleet().slice(0, 3)) }); await small.ui.load();
    assert.match(small.ui.html(), /data-ov-fold open>/, 'a handful of items stays open on a desktop');
    const many = fixture({ data: overviewOf([...fleet(), ...fleet().map(i => ({ ...i, id: i.id + 'x' }))]) }); await many.ui.load();
    assert.doesNotMatch(many.ui.html(), /data-ov-fold open>/, 'above five items the overview is folded to its summary');
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
    const spoken = f.nodes.announce.textContent;
    f.state.answers.set('/api/compute/overview', failure(503, { error: 'authority_unavailable' }));
    await f.ui.load({ quiet: true });
    // It is said above the fold (an alert, the meta line and the summary), so a closed fold cannot hide it.
    assert.match(f.nodes.stale.textContent, /^Could not refresh\. Showing the data from \d{1,2}:\d\d:\d\d.*; it may be out of date\.$/);
    assert.equal(f.nodes.meta.textContent, '', 'the alert says it once; the meta line does not say it a second time');
    assert.match(f.nodes.summary.textContent, /Could not refresh: data from \d{1,2}:\d\d:\d\d/);
    assert.doesNotMatch(f.nodes.meta.textContent, /Updated just now/);
    assert.equal(f.nodes.announce.textContent, spoken, 'the alert speaks; the status line adds nothing');
    assert.match(f.shown(), /Could not refresh\. Showing the data from \d{1,2}:\d\d:\d\d.*; it may be out of date\./);
    assert.match(f.shown(), /2 runners/);
    assert.equal(f.ui.state.refreshFailed, true);
    // Every row says it is old: "Last known: Online", "As of HH:MM:SS: ..." and never a bare "Online".
    assert.match(f.html(), /data-tone="ok" data-ov-chip>Last known: Online<\/span>/);
    assert.match(f.shown(), /As of \d{1,2}:\d\d:\d\d.*: Waiting for work\./);
    assert.doesNotMatch(f.html(), />Online<\/span>/);
    // A failure that lasts writes its alert once, not at every poll (writing the same text again would speak it again).
    const writes = f.nodes.stale.writes;
    await f.ui.load({ quiet: true });
    assert.equal(f.nodes.stale.writes, writes, 'the second failed poll says nothing more');
    assert.equal(f.nodes.stale.hidden, false);
    // When it recovers the rows read as current again and the alert goes.
    f.state.answers.delete('/api/compute/overview');
    await f.ui.load({ quiet: true });
    assert.match(f.html(), /data-ov-chip>Online<\/span>/); assert.equal(f.nodes.stale.hidden, true); assert.equal(f.nodes.stale.textContent, '');
    assert.match(f.nodes.meta.textContent, /^Updated just now/);
    f.state.answers.set('/api/compute/overview', failure(503, { error: 'authority_unavailable' }));
    await f.ui.load({ quiet: true });
    assert.notEqual(f.nodes.stale.textContent, '', 'a new failure is said again');
    // A sign-out is handed to the screen, not shown as an error.
    f.state.answers.set('/api/compute/overview', failure(401, { error: 'sign_in' }));
    await f.ui.load({ quiet: true }); assert.deepEqual(f.state.denied, [401]);
  },

  async polling() {
    const f = fixture();
    f.ui.start();
    assert.equal(f.liveTimers(30000).length, 0, 'no read is scheduled before the first answer');
    await f.ui.load();
    const timers = f.liveTimers(30000);
    assert.equal(timers.length, 1); assert.equal(timers[0].ms, 30000, 'the served interval, in milliseconds');
    await f.fire(); assert.equal(f.calls.length, 2, 'the timer reads again'); assert.equal(f.liveTimers(30000).length, 1, 'and schedules the next read');
    // A hidden page does not read; it asks again later.
    f.doc.hidden = true;
    await f.fire(); assert.equal(f.calls.length, 2); assert.equal(f.liveTimers(30000).length, 1);
    // Back in view with old data: read at once instead of waiting.
    f.doc.hidden = false; f.bump(31000);
    f.docListeners.get('visibilitychange')();
    await flush(); assert.equal(f.calls.length, 3);
    f.bump(5000); f.docListeners.get('visibilitychange')(); await flush(); assert.equal(f.calls.length, 3, 'fresh data is not read again');
    // The interval is never below 15 seconds, whatever the server says.
    const quick = fixture({ data: overviewOf(fleet(), { refresh_seconds: 1 }) }); await quick.ui.load();
    assert.equal(quick.liveTimers(15000).length, 1);
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
    assert.match(f.nodes.announce.textContent, /^Overview updated\. 2 runners, 1 machine, 2 cloud workspaces\. 3 need attention: build-runner, edge-runner, Old box\.$/);
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
    assert.match(f.nodes.announce.textContent, /Showing 2 of 5\./);
    assert.match(f.shown(), /Showing 2 of 5/);
    assert.match(f.html(), /aria-label="Filter by kind"/, 'the group is named by what it does');
    f.select('offline');
    assert.deepEqual([...f.html().matchAll(/data-ov-id="([^"]+)"/g)].map(m => m[1]), ['mach_2']);
    assert.match(f.html(), /<option value="offline" selected>Offline \(1\)<\/option>/);
    f.kindFilter('workspace'); // offline runners are not workspaces: the status falls back to all, never to an empty list
    assert.equal(f.ui.state.status, 'all');
    assert.deepEqual([...f.html().matchAll(/data-ov-id="([^"]+)"/g)].map(m => m[1]), ['w_1', 'w_2']);
    assert.doesNotMatch(f.html(), /<option value="offline"/, 'only the statuses that exist for the chosen kind are offered');
    assert.match(f.html(), /<option value="running">Running \(1\)<\/option>/);
    assert.equal((f.html().match(/data-ov-action="clear"/g) || []).length, 1, 'one Clear filters, in the filter row');
    f.click('clear');
    assert.equal(f.ui.state.kind, 'all'); assert.equal(f.ui.state.status, 'all');
    assert.equal([...f.html().matchAll(/data-ov-id=/g)].length, 5);
    assert.ok(f.focused.includes('#compute-ov-status'), 'focus goes to a control that is still there');
    f.select('attention');
    assert.deepEqual([...f.html().matchAll(/data-ov-id="([^"]+)"/g)].map(m => m[1]), ['mach_2', 'w_2']);
    // "2 need attention" is a button that sets the same filter and unsets it again.
    f.click('clear');
    assert.match(f.html(), /data-ov-action="attention" aria-pressed="false">2 need attention</);
    f.click('attention');
    assert.equal(f.ui.state.status, 'attention');
    assert.match(f.html(), /data-ov-action="attention" aria-pressed="true">2 need attention</);
    f.click('attention'); assert.equal(f.ui.state.status, 'all');
    f.select('attention');
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
    assert.match(f.shown(), /2 runners, 1 machine/, 'the summary stays visible with the panel open');
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
    assert.equal(f.nodes.announce.textContent, 'The details could not be loaded.', 'a failed detail load is said, not silent');
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
    assert.match(f.focused.at(-1), /compute-overview|data-ov-action="detail"/, 'focus is moved to the list or the section heading, not left on a removed panel');
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

  async staleness() {
    // The clock only makes a claim older. Pure view first.
    const th = { green_within_s: 180, red_after_s: 900, report_stale_s: 600 };
    const [online, , , running, unknownBox] = fleet();
    const at = age => ({ nowS: clock + age, thresholds: th });
    assert.equal(ov.viewOf(running, at(0)).status.code, 'running');
    assert.equal(ov.viewOf(running, at(0)).seen, 'Last reported 2 minutes ago');
    assert.equal(ov.viewOf(running, at(60)).seen, 'Last reported 3 minutes ago', 'the age is recomputed from the report time');
    const old = ov.viewOf(running, at(600));
    assert.equal(old.status.code, 'unknown'); assert.equal(old.status.label, 'Unknown'); assert.equal(old.health.state, 'attention');
    assert.match(old.status.detail, /Its last report said Running 12 minutes ago and nothing newer has arrived, so it may not be running any more\./);
    assert.equal(ov.viewOf(online, at(0)).status.code, 'online');
    assert.equal(ov.viewOf(online, at(200)).status.code, 'slow');
    assert.equal(ov.viewOf(online, at(1000)).status.code, 'offline');
    assert.match(ov.viewOf(online, at(1000)).status.detail, /It read Online when this was last checked/);
    // A stopped or failed workspace is a fact: it does not turn Unknown with age.
    const stopped = item('workspace', 'w_s', 'Stopped box', { code: 'stopped', tone: 'quiet', label: 'Stopped' }, { last_seen: seen(10, { prefix: 'Last reported' }) });
    assert.equal(ov.viewOf(stopped, at(5000)).status.code, 'stopped');
    // A removed runner keeps its own words.
    const gone = item('runner', 'm_g', 'old', { code: 'revoked', tone: 'bad', label: 'Revoked' }, { last_seen: seen(21, { prefix: 'Last contact', suffix: ', before it was removed' }) });
    assert.equal(ov.viewOf(gone, at(0)).seen, 'Last contact 21 seconds ago, before it was removed');
    assert.equal(ov.viewOf(gone, at(0)).status.code, 'revoked');
    // After a failed refresh every row is marked as of the last good read.
    const marked = ov.viewOf(online, { ...at(0), failedAt: '10:42:07' });
    assert.equal(marked.status.label, 'Last known: Online'); assert.match(marked.status.detail, /^As of 10:42:07: /); assert.equal(marked.stale, true);
    assert.equal(ov.viewOf(unknownBox, { ...at(0), failedAt: '10:42:07' }).status.label, 'Unknown');
    // On screen: a running workspace the clock has passed turns Unknown by itself, without a new read, and says so aloud nowhere (polite status stays quiet).
    const f = fixture();
    f.ui.start();
    await f.ui.load();
    assert.match(f.html(), /data-status="running"/);
    const readsBefore = f.calls.length;
    f.bump(700 * 1000);
    await f.fire(10000);
    assert.equal(f.calls.length, readsBefore, 'the clock tick reads nothing');
    assert.match(f.html(), /data-status="unknown"[\s\S]{0,300}Build box|Build box[\s\S]{0,600}data-status="unknown"/);
    assert.match(f.nodes.summary.textContent, /Build box/, 'it now counts as needing attention, by name');
  },

  async 'clock-ticks-in-place'() {
    const f = fixture();
    f.ui.start();
    await f.ui.load();
    assert.match(f.nodes.meta.textContent, /Updated just now/);
    const listWrites = f.nodes.list.writes, panelWrites = f.nodes.panel.writes;
    f.bump(95 * 1000);
    await f.fire(10000);
    assert.match(f.nodes.meta.textContent, /Updated 1 minute ago/);
    assert.equal(f.nodes.list.writes, listWrites, 'ages are rewritten in place: the list is not rebuilt when no status changed');
    assert.equal(f.nodes.panel.writes, panelWrites);
    assert.equal(f.liveTimers(10000).length, 1, 'and the clock goes on');
    // A hidden tab does no work.
    f.doc.hidden = true; f.bump(60000);
    const text = f.nodes.meta.textContent;
    await f.fire(10000);
    assert.equal(f.nodes.meta.textContent, text);
  },

  async 'refresh-in-place'() {
    const f = fixture();
    await f.ui.load();
    // Same data again: nothing is rewritten, so an open status list and a focused row keep their nodes.
    const writes = ['state', 'notes', 'filters', 'list'].map(n => f.nodes[n].writes);
    await f.ui.load({ quiet: true });
    assert.deepEqual(['state', 'notes', 'filters', 'list'].map(n => f.nodes[n].writes), writes);
    // Changed rows are rewritten; the filter list is left alone while the person has it open.
    const changed = fleet(); changed[0] = item('runner', 'mach_1', 'build-runner', { code: 'offline', tone: 'bad', label: 'Offline' }, { attention: true });
    f.state.answers.set('/api/compute/overview', overviewOf(changed));
    f.flags.statusFocused = true;
    const before = f.nodes.filters.writes;
    await f.ui.load({ quiet: true });
    assert.equal(f.nodes.filters.writes, before, 'an open status list is not closed under the pointer');
    assert.ok(f.nodes.list.writes > writes[3], 'the rows are repainted');
    f.flags.statusFocused = false;
    await f.ui.load({ quiet: true });
    assert.ok(f.nodes.filters.writes > before, 'its counts catch up once it is closed');
    // An open detail keeps its panel: a refresh writes neither the panel slot nor an unchanged body.
    f.state.answers.set('/api/compute/overview/runner/mach_2', detailOf(fleet()[1]));
    f.click('detail', { id: 'mach_2', kind: 'runner' }); await flush();
    const panelWrites = f.nodes.panel.writes, bodyWrites = f.body.writes, chipWrites = f.chip.writes;
    f.state.answers.set('/api/compute/overview', overviewOf(changed));
    await f.ui.load({ quiet: true }); await flush();
    assert.equal(f.nodes.panel.writes, panelWrites, 'the panel node is never rebuilt by a refresh');
    assert.equal(f.body.writes, bodyWrites, 'an unchanged body is not rewritten');
    assert.equal(f.chip.writes, chipWrites);
    assert.deepEqual(f.calls.slice(-2), ['/api/compute/overview', '/api/compute/overview/runner/mach_2']);
    // When the detail did change, only its body is rewritten.
    f.state.answers.set('/api/compute/overview/runner/mach_2', detailOf(fleet()[1], { activity: [{ at: 2, age_s: 5, text: 'Drain started' }] }));
    await f.ui.load({ quiet: true }); await flush();
    assert.equal(f.nodes.panel.writes, panelWrites); assert.equal(f.body.writes, bodyWrites + 1);
  },

  async pause() {
    const f = fixture();
    f.ui.start();
    await f.ui.load();
    assert.equal(f.liveTimers(30000).length, 1);
    f.click('pause');
    assert.equal(f.ui.state.paused, true);
    assert.equal(f.liveTimers(30000).length, 0, 'no automatic read while paused');
    assert.match(f.nodes.meta.textContent, /Automatic updates are paused; statuses and ages keep counting from the last read\. Use Refresh above to read again\./);
    assert.match(f.nodes.top.html, />Resume automatic updates</);
    assert.equal(f.nodes.announce.textContent, 'Automatic updates paused. Statuses and ages keep counting from the last read.');
    // A visibility change does not read either; pressing the page's Refresh (load) still does.
    f.bump(60000); f.docListeners.get('visibilitychange')(); await flush();
    assert.equal(f.calls.length, 1);
    await f.ui.load({ say: true }); assert.equal(f.calls.length, 2);
    assert.equal(f.liveTimers(30000).length, 0, 'still no schedule while paused');
    f.click('pause'); await flush();
    assert.equal(f.ui.state.paused, false); assert.equal(f.calls.length, 3, 'resuming reads at once');
    assert.equal(f.liveTimers(30000).length, 1);
    assert.match(f.nodes.top.html, />Pause automatic updates</);
  },

  async 'workspaces-sync'() {
    // The workspaces list is the record of cloud workspaces: when it changes, the overview reads again on the next clock tick, once.
    let signature = '';
    const f = fixture({ data: overviewOf([]), signature: () => signature });
    f.ui.start();
    await f.ui.load();
    assert.match(f.shown(), /Nothing here yet\./);
    signature = 'w_1:1';
    f.state.answers.set('/api/compute/overview', overviewOf([item('workspace', 'w_1', 'Machine-less', { code: 'not_started', tone: 'quiet', label: 'Saved, not started' }, { last_seen: null })]));
    const reads = f.calls.length;
    await f.fire(10000);
    assert.equal(f.calls.length, reads + 1, 'a changed list is read again');
    assert.match(f.html(), /Machine-less/); assert.match(f.html(), /Saved, not started/);
    assert.doesNotMatch(f.shown(), /Nothing here yet/);
    assert.match(f.nodes.summary.textContent, /1 cloud workspace\. Nothing needs attention\./, 'no machine is not an attention state');
    await f.fire(10000);
    assert.equal(f.calls.length, reads + 1, 'an unchanged list is not');
  },

  async fold() {
    // The summary sentence is the fold's summary: open on a desktop, closed on a phone, and the person's choice is kept.
    const desktop = fixture();
    assert.match(desktop.ui.html(), /<details class="compute-ov-fold" data-ov-fold data-plain>/, 'while the first read is under way the fold is closed (the headline says it is loading)');
    await desktop.ui.load();
    assert.match(desktop.ui.html(), /<details class="compute-ov-fold" data-ov-fold open>/, 'five items on a desktop: open');
    const phone = fixture({ narrow: true });
    assert.match(phone.ui.html(), /<details class="compute-ov-fold" data-ov-fold( data-plain)?>/);
    assert.doesNotMatch(phone.ui.html(), /data-ov-fold open/);
    const toggle = f => f.ui.click({ target: { closest: sel => sel === '[data-ov-fold-summary]' ? { closest: q => q === '.compute-ov' ? {} : null } : null } });
    assert.equal(toggle(phone), true);
    assert.match(phone.ui.html(), /data-ov-fold open( data-plain)?>/, 'opened by the person, kept across a re-render');
    toggle(phone);
    assert.doesNotMatch(phone.ui.html(), /data-ov-fold open/);
  },

  async skew() {
    // Every timestamp on screen is the server's: a browser clock that is minutes or hours off must not turn a healthy runner
    // Offline or a fresh workspace Unknown, nor hide a stale one. "Now" is the server time at the read plus the time since.
    for (const skew of [1000, -1000, 200, -200, 86400]) {
      const f = fixture({ skew });
      f.ui.start();
      await f.ui.load();
      assert.match(f.html(), /data-status="online"/, `skew ${skew}: the healthy runner stays Online`);
      assert.match(f.html(), /data-status="running"/, `skew ${skew}: the fresh workspace stays Running`);
      assert.doesNotMatch(f.html(), /Slow to report|data-status="offline"[\s\S]*build-runner/, `skew ${skew}`);
      assert.match(f.nodes.meta.textContent, /^Updated just now/, `skew ${skew}: the age counts from the read`);
      assert.match(f.html(), /Last seen 40 seconds ago/);
      // Time that really passes still ages it: 200 s later the runner is slow, whatever the browser clock says.
      f.bump(200 * 1000);
      await f.fire(10000);
      assert.match(f.html(), /data-status="slow"[\s\S]{0,400}build-runner|build-runner[\s\S]{0,400}data-status="slow"/, `skew ${skew}: elapsed time is honoured`);
      assert.match(f.nodes.meta.textContent, /^Updated 3 minutes ago/);
    }
    // The overrides do not leave "Nothing is blocking" beside Offline or Unknown: they name what they are.
    const [online, , , running] = fleet();
    const th = { green_within_s: 180, red_after_s: 900, report_stale_s: 600 };
    const off = ov.viewOf(online, { nowS: clock + 1000, thresholds: th });
    assert.equal(off.status.code, 'offline'); assert.match(off.health.blockers.at(-1).text, /^Offline: nothing heard for 17 minutes, so new card runs would wait\./);
    const quiet = ov.viewOf(running, { nowS: clock + 660, thresholds: th });
    assert.equal(quiet.status.code, 'unknown'); assert.equal(quiet.health.blockers.at(-1).code, 'unknown');
    assert.match(text(ov.detailHTML({ ...online, activity: [], facts: [] }, { clock: { nowS: clock + 1000, thresholds: th } })), /Blocking new work.*Offline: nothing heard for 17 minutes/);
    assert.doesNotMatch(text(ov.detailHTML({ ...online, activity: [], facts: [] }, { clock: { nowS: clock + 1000, thresholds: th } })), /Nothing is blocking new work/);
  },

  async 'clock-step'() {
    // A wall clock that is stepped (NTP, wake from sleep, a person changing the time) between two reads moves no age:
    // elapsed time comes from the monotonic clock.
    const f = fixture();
    f.ui.start();
    await f.ui.load();
    f.step(1000 * 1000); // +1000 s on the wall clock, nothing has really passed
    await f.fire(10000);
    const healthy = () => /data-ov-id="mach_1"[^>]*data-status="online"/.test(f.html());
    assert.ok(healthy(), 'a wall clock stepped forward does not turn the healthy runner Offline');
    assert.match(f.nodes.meta.textContent, /^Updated just now/);
    f.step(-5000 * 1000); // and back by more than that
    await f.fire(10000);
    assert.ok(healthy()); assert.match(f.nodes.meta.textContent, /^Updated just now/);
    // Real time still ages it.
    f.bump(200 * 1000);
    await f.fire(10000);
    assert.match(f.html(), /data-status="slow"[\s\S]{0,400}build-runner|build-runner[\s\S]{0,400}data-status="slow"/);
  },

  async 'every-runner-status-ages'() {
    // A draining or drained runner that went quiet does not keep its label: the age override covers every status that
    // claims a recent signal, and it still says the runner is draining.
    const th = { green_within_s: 180, red_after_s: 900, report_stale_s: 600 };
    for (const code of ['draining', 'drained', 'busy', 'online']) {
      const runner = item('runner', 'r', 'night-runner', { code, label: code }, { more: { draining: code === 'draining' || code === 'drained' } });
      const quiet = ov.viewOf(runner, { nowS: clock + 1000, thresholds: th });
      assert.equal(quiet.status.code, 'offline', code);
      if (runner.draining) assert.match(quiet.status.detail, /It is also set to drain: no new card runs start on it\./);
    }
    // A removed or disabled runner is left alone: its age is not a claim about its health.
    for (const code of ['revoked', 'disabled', 'never_connected']) assert.equal(ov.viewOf(item('runner', 'r', 'x', { code, label: code }), { nowS: clock + 1000, thresholds: th }).status.code, code);
  },

  async 'scope-and-state'() {
    // "N need attention" names its scope when a kind is chosen, so it never changes meaning silently.
    const f = fixture();
    await f.ui.load();
    assert.match(f.html(), />2 need attention</);
    f.kindFilter('runner');
    assert.match(f.html(), />1 runner needs attention</);
    f.kindFilter('workspace');
    assert.match(f.html(), />1 cloud workspace needs attention</);
    // The state sentence (loading, failed with "Try again", empty with what to do) sits outside the fold, and with
    // nothing to fold the summary has no disclosure marker.
    const phone = fixture({ narrow: true, data: overviewOf([]) });
    const before = phone.ui.html();
    assert.ok(before.indexOf('data-ov-slot="state"') < before.indexOf('<details'), 'the state slot comes before the fold');
    assert.match(before, /data-ov-fold data-plain>/);
    await phone.ui.load();
    assert.match(phone.nodes.state.html, /Create a cloud workspace below\./, 'the empty-state next step is outside the fold');
    // Pressing "Try again" visibly does something: a headline while it runs, and a count when it fails again.
    const broken = fixture({ narrow: true });
    broken.state.answers.set('/api/compute/overview', failure(500, { error: 'boom' }));
    await broken.ui.load();
    assert.match(broken.nodes.state.html, /role="alert">The overview could not be loaded\. Nothing is shown rather than a guess\.</);
    let release; broken.state.answers.set('/api/compute/overview', () => new Promise(done => { release = done; }));
    const again = broken.ui.load();
    assert.equal(broken.nodes.summary.textContent, 'Trying again…');
    release(failure(500, { error: 'boom' })); await again;
    assert.match(broken.nodes.state.html, /Tried 2 times\./);
    assert.equal(broken.nodes.summary.textContent, 'Overview not loaded.');
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
