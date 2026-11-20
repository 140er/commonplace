// Compute Activity receives aggregate snapshots only. Labels are local constants.
import { escape as e } from './api.js';

export const TITLE = 'Activity';
const BASE = '/api/compute/activity';
const ACTIVE = new Set(['queued', 'preparing', 'starting', 'running', 'waiting']);
const number = n => Number.isFinite(n) ? new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 }).format(n) : 'n/a';
const hours = n => Number.isFinite(n) ? `${n.toFixed(2)} h` : 'n/a';
const seconds = n => Number.isFinite(n) ? n >= 3600 ? hours(n / 3600) : `${n.toFixed(1)} s` : 'n/a';
const short = id => id ? id.slice(-8) : 'n/a';
const date = n => new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Amsterdam', dateStyle: 'medium', timeStyle: 'short' }).format(n * 1000);
const reason = code => ({ task_mapping_missing: 'No authoritative card-to-thread mapping.', pr_snapshot_missing: 'No dated pull request state snapshot.', correlation_missing: 'No correlated person response record.', thinking_not_measured: 'Thinking is not measured separately.', usage_missing: 'No complete attributable turn usage.', context_missing: 'No retained context snapshot.', edit_lines_unavailable: 'Some recorded edits have no countable diff.', no_samples: 'No answered asks with recorded timing.', no_timing: 'Source timing is missing.', partial_scan: 'Collection reached its explicit limit.', no_attribution: 'No source attribution.', not_recorded: 'Not recorded by this source.', subagent_usage_unknown: 'Native subagent usage is not separately reported.' })[code] || 'Unavailable in this snapshot.';
const labels = {
  agent_hours: ['Measured agent hours', 'Clipped run time, unioned per agent; native children counted once.'],
  runs_started: ['Started runs', 'Actual starts in this window; requested runs may never start.'],
  runs_requested: ['Requested runs', 'Requests recorded in this window, including queued work.'],
  threads: ['Threads', 'Threads with observed started or queued work in the window.'],
  peak_agents: ['Peak agents', 'Peak concurrency of unioned executing agents.'],
  tasks_finished: ['Tasks finished'], tasks_failed: ['Tasks failed'], tasks_stopped: ['Tasks stopped'], tasks_running: ['Tasks running'],
  prs_merged: ['Linked PRs merged'], prs_open: ['Linked PRs open'], prs_closed: ['Linked PRs closed'],
  tool_hours: ['Exclusive tool hours', 'Unioned tool time after wait and compaction precedence.'],
  waiting_hours: ['Recorded input wait', 'Clipped runtime-request waits, including cancellations.'],
  asks: ['Input requests'], asks_answered: ['Answered requests'], asks_cancelled: ['Cancelled requests'],
  ask_typical_seconds: ['Typical answered wait', 'Nearest-rank median of recorded answered wait samples, clipped to this window.'],
  ask_longest_seconds: ['Longest answered wait'], waiting_on_owner_hours: ['Waiting on you / coordinator'], chief_asks: ['Correlated coordinator asks'],
  tool_calls: ['Tool / command calls'], failed_commands: ['Failed commands'], retries: ['Recorded retries', 'Only attempts explicitly marked retry.'],
  failed_hours: ['Failed run hours', 'Measured runtime; it is not necessarily wasted work.'],
  no_run_gap_hours: ['Observed no-run hours', 'Gaps between the first and last observed runs, not an attributed person wait.'],
  compactions: ['Compactions'], context_over_90: ['Context snapshots >90%', 'Retained snapshots can miss pre-compaction peaks.'], turns_with_context: ['Turns with context snapshots'],
  thinking_hours: ['Thinking time'], lines_added: ['Recorded lines added', 'Diff edit lines, including repeated edits; not net repository changes.'], lines_deleted: ['Recorded lines removed'],
  tokens_input: ['Input tokens', 'Complete main-agent turn deltas only; includes cached input.'], tokens_cached: ['Cached input tokens', 'A subset of input tokens, not additional input.'],
  tokens_output: ['Output tokens'], cache_creation: ['Cache creation tokens'], reasoning_tokens: ['Reasoning tokens', 'Reported token subset, not measured thinking duration.'],
  tasks_from_person: ['Tasks from a person'], tasks_from_agents: ['Tasks from agents'], handoffs: ['Recorded provider handoffs'],
};
const groupNames = { shell: 'Shell commands', build: 'Build commands', test: 'Test commands', check: 'Check commands', file: 'File tools', web: 'Web tools', delegate: 'Delegation tools', other: 'Other tools', compaction: 'Compactions', wait: 'Recorded input waits', unknown: 'Unattributed', failed_run: 'Failed run time', recorded_retry: 'Recorded retry time', no_run_gap: 'Observed no-run gaps' };
const phaseNames = { tool: 'Tools', wait: 'Input wait', compaction: 'Compaction', unknown: 'Unattributed' };
const metricValue = (key, item) => item?.value == null ? 'n/a' : key.endsWith('_hours') || key === 'agent_hours' ? hours(item.value) : key.endsWith('_seconds') ? seconds(item.value) : number(item.value);
const metric = (snapshot, key) => {
  const item = snapshot?.metrics?.[key], c = snapshot?.coverage;
  const provenance = key.startsWith('prs_') ? `Retained linked state · ${c?.pr_first ? `As of ${date(c.pr_first)} – ${date(c.pr_last)}` : 'As of: unavailable; no dated state snapshot'}. Not window events.` : '';
  const caption = item?.value == null ? reason(item?.reason) : labels[key][1] || (provenance ? '' : 'Recorded within the selected window.');
  return `<div class="ca-metric"><dt>${e(labels[key][0])}</dt><dd>${metricValue(key, item)}</dd><small>${e([caption, provenance].filter(Boolean).join(' '))}</small></div>`;
};
const field = (label, value) => `<span class="ca-field-label">${e(label)}</span>${value}`;

export function heatHTML(daily = []) {
  const maximum = Math.max(0, ...daily.map(row => row.seconds));
  return `<div class="ca-heat" aria-label="Daily agent hours in Europe/Amsterdam">${daily.map(row => {
    const fill = maximum ? row.seconds / maximum * 56 : 0;
    return `<div><svg class="ca-heat-bar" viewBox="0 0 100 56" preserveAspectRatio="none" role="img" aria-label="${e(row.day)}: ${hours(row.seconds / 3600)}, ${number(row.runs)} starts"><rect x="0" y="${56 - fill}" width="100" height="${fill}"></rect></svg><span>${e(row.day.slice(5))}</span><strong>${hours(row.seconds / 3600)}</strong></div>`;
  }).join('')}</div>`;
}

function table(headers, rows, caption = '', empty = 'No recorded samples in this window.') {
  return `<div class="ca-table-wrap"><table>${caption ? `<caption>${e(caption)}</caption>` : ''}<thead><tr>${headers.map(h => `<th scope="col">${e(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('') || `<tr><td class="ca-empty" colspan="${headers.length}">${e(empty)}</td></tr>`}</tbody></table></div>`;
}

export function recapHTML(s) {
  const c = s.coverage;
  const groups = [
    ['Outcomes and ownership', ['tasks_finished', 'tasks_failed', 'tasks_stopped', 'tasks_running', 'prs_merged', 'prs_open', 'prs_closed', 'tasks_from_person', 'tasks_from_agents']],
    ['Recorded work and waiting', ['runs_requested', 'tool_hours', 'waiting_hours', 'asks', 'asks_answered', 'asks_cancelled', 'ask_typical_seconds', 'ask_longest_seconds', 'waiting_on_owner_hours', 'chief_asks', 'tool_calls', 'failed_commands', 'retries', 'failed_hours', 'no_run_gap_hours', 'compactions', 'thinking_hours']],
    ['Tokens, context and edits', ['tokens_input', 'tokens_cached', 'tokens_output', 'cache_creation', 'reasoning_tokens', 'turns_with_context', 'context_over_90', 'lines_added', 'lines_deleted', 'handoffs']],
  ];
  const models = table(['Model', 'Run hours / starts', 'Tools / thinking', 'Input / cached / output', 'Turn coverage'], s.models.map(row => `<tr><th scope="row">${e(row.model === 'other' ? 'Unknown / other model' : row.model)}</th><td>${field('Run time', hours(row.seconds / 3600))}<small>${number(row.runs)} projected starts</small></td><td>${field('Tools', hours(row.tool_seconds / 3600))}<small>Thinking n/a: not measured</small></td><td class="ca-tokens"><span class="ca-token">${field('Input', number(row.input))}</span><span class="ca-separator"> / </span><span class="ca-token">${field('Cached input', number(row.cached))}</span><span class="ca-separator"> / </span><span class="ca-token">${field('Output', number(row.output))}</span></td><td>${field('Turn coverage', `${number(row.complete_turns)} / ${number(row.usage_turns)} complete`)}<small>${number(row.inclusive_turns)} with subagents</small></td></tr>`));
  return `<dl class="ca-lead">${['agent_hours', 'runs_started', 'threads', 'peak_agents'].map(key => metric(s, key)).join('')}</dl><p class="ca-timing-note">Timing coverage: ${number(c.runs_timed)} projected runs timed; ${number(c.runs_untimed)} have missing timing. ${number(c.native_agents_timed)} / ${number(c.native_agents)} additional native agents are timed. Missing timing is excluded from measured hours; queued work contributes no execution time.</p><section class="ca-panel"><h2>Daily activity</h2>${heatHTML(s.daily)}<p>Europe/Amsterdam · summed agent hours, so parallel work can exceed 24 h per day.</p></section><section class="ca-panel"><h2>By model</h2>${models}<p>Main-agent turn usage only. Cached input overlaps input. Native subagent token coverage is n/a: not separately reported. ${number(c.turns_complete)} / ${number(c.turns_seen)} turns have complete, attributable usage.</p></section>${groups.map(([title, keys]) => `<section class="ca-panel"><h2>${title}</h2><dl class="ca-metrics">${keys.map(key => metric(s, key)).join('')}</dl>${title === 'Outcomes and ownership' ? '<p>Merge events in this window: n/a. Open/close events: n/a. Linked state snapshots do not record the dates of these events.</p>' : ''}</section>`).join('')}<section class="ca-panel"><h2>Handoffs from → to</h2>${table(['From model', 'To model', 'Count'], s.handoffs.map(row => `<tr><th scope="row">${e(row.from)}</th><td>${e(row.to)}</td><td>${number(row.count)}</td></tr>`))}<p>Only recorded, resolved provider handoffs. A model change alone is not a handoff; “other” means source model attribution is unavailable.</p></section>`;
}

export function sinksHTML(s) {
  const top = s.sinks[0];
  return `<section class="ca-panel"><h2>What eats time</h2><p class="ca-finding">${top ? `${e(groupNames[top.category])}: ${hours(top.seconds / 3600)} of recorded elapsed time.` : 'No timed calls were recorded in this window.'}</p><p>Ranked by recorded elapsed time: summed calls, failed/retry intervals, or per-thread no-run gaps. Calls overlap; gaps are not person waiting. Rows are not additive agent hours. Wall shares use wait → compaction → tools → unattributed precedence. Failed time is not all wasted.</p>${table(['Time sink', 'Recorded hours / wall share', 'Count / failure rate', 'p95 / timed samples', 'Top thread / project'], s.sinks.map((row, index) => `<tr><th scope="row">${index + 1}. ${e(groupNames[row.category])}<svg class="ca-rank" viewBox="0 0 100 2" preserveAspectRatio="none" aria-hidden="true"><rect x="0" y="0" width="${top?.seconds ? row.seconds / top.seconds * 100 : 0}" height="2"></rect></svg></th><td>${field('Recorded time', hours(row.seconds / 3600))}<small>${row.share == null ? 'n/a: overlaps or outside run time' : `${(row.share * 100).toFixed(1)}% exclusive wall share`}</small></td><td>${field('Recorded calls', number(row.count))}<small>${row.count ? (row.failed / row.count * 100).toFixed(1) : 'n/a'}% failed</small></td><td>${field('p95', seconds(row.p95))}<small>${number(row.timed)} timed · nearest rank</small></td><td>${row.thread ? `<button type="button" class="ca-text" data-ca-thread="${e(row.thread)}">Thread ${short(row.thread)}</button>` : 'n/a: no thread attribution'}<small>${row.project ? `Project ${short(row.project)} · opaque source identity` : 'n/a: no project attribution'}</small></td></tr>`))}<p>${['build', 'test', 'check'].filter(cat => !s.sinks.some(row => row.category === cat)).map(cat => `${groupNames[cat]}: n/a — no classified samples in the scanned data.`).join(' ')}</p></section><section class="ca-panel"><h2>Slowest threads and cards</h2><p>Across all models: the leading retained threads for this source and window, ranked by measured agent hours. Collection and trace retention limits can exclude other work. Cards n/a: no authoritative card-to-thread mapping. Each thread includes its observed run count and opaque project identity where available.</p>${threadsHTML(s)}<button class="btn" type="button" data-ca-tab="trace">Browse all retained thread traces</button></section><section class="ca-panel"><h2>Server spans</h2>${table(['Category', 'Summed span time', 'Count / failures', 'p95'], s.spans.map(row => `<tr><th scope="row">${e(row.category)}</th><td>${field('Summed span time', seconds(row.seconds))}</td><td>${field('Calls / failures', `${number(row.count)} / ${number(row.failed)}`)}</td><td>${field('p95', seconds(row.p95))}<small>${number(row.count)} timed samples</small></td></tr>`))}<p>Unattributed server spans overlap and are separate from agent time. ${s.coverage.log_first ? `${date(s.coverage.log_first)} – ${date(s.coverage.log_last)}` : 'n/a: no readable retained spans'} · ${number(s.coverage.logs_read)} rotated files read; ${number(s.coverage.logs_skipped)} skipped records.</p></section>`;
}

function threadsHTML(s, { live = false, model = '' } = {}) {
  const rows = s.threads.filter(row => (!live || ACTIVE.has(row.status)) && (!model || row.model === model));
  return table(['Thread / project', 'Measured hours / runs', 'Model / state', 'Observed through'], rows.map(row => `<tr><th scope="row"><button type="button" class="ca-text" data-ca-thread="${e(row.id)}">Thread ${short(row.id)}</button><small>${row.project ? `Project ${short(row.project)}` : 'Project n/a: no source attribution'} · ${e(row.creator)} created</small></th><td>${field('Run time', hours(row.seconds / 3600))}<small>${number(row.runs)} observed projected runs</small></td><td>${field('Model / state', e(row.model))}<small>${e(row.status)}</small></td><td>${field('Observed through', date(row.last))}</td></tr>`), '', `No retained traces for this filter in the selected window.${s.coverage?.partial || s.coverage?.threads_kept < s.coverage?.threads_total ? ' Collection or trace retention was capped; absence of a trace does not establish zero work. Refresh only rereads this retained snapshot.' : ''}`);
}

export function traceHTML(thread) {
  const peak = Math.max(0, ...thread.buckets.map(b => b.wait + b.compaction + b.tool + b.unknown));
  const buckets = thread.buckets.map(b => {
    let x = 0;
    const rects = Object.keys(phaseNames).map(key => {
      const width = peak ? b[key] / peak * 100 : 0;
      const rect = `<rect class="ca-phase-${key}" x="${x}" y="0" width="${width}" height="16"></rect>`;
      x += width;
      return rect;
    }).join('');
    const label = Object.entries(phaseNames).map(([key, name]) => `${name} ${seconds(b[key])}`).join(', ');
    return `<tr><th scope="row">${date(b.at)}</th><td><svg class="ca-bucket" viewBox="0 0 100 16" preserveAspectRatio="none" role="img" aria-label="${label}">${rects}</svg><small>${Object.keys(phaseNames).map(key => `${phaseNames[key]} ${seconds(b[key])}`).join(' · ')}</small></td><td>${field('Recorded calls', number(b.calls))}</td></tr>`;
  });
  return `<section class="ca-panel"><button type="button" class="ca-text" data-ca-back>Back to threads</button><h2 tabindex="-1" data-ca-detail-title>Thread ${short(thread.id)}</h2><p>${thread.project ? `Project ${short(thread.project)}` : 'Project n/a: no source attribution'} · ${e(thread.model)} · ${hours(thread.seconds / 3600)} agent time. Time buckets contain aggregate seconds and counts only.</p><div class="ca-phases">${Object.entries(phaseNames).map(([key, label]) => `<span><i class="ca-phase-${key}"></i>${label}: ${seconds(thread.phases[key])}</span>`).join('')}</div>${table(['Bucket start (Amsterdam)', 'Phase seconds', 'Recorded calls'], buckets)}</section>`;
}

function coverageHTML(s) {
  const c = s.coverage;
  const names = { runs: 'Run metadata', native: 'Native agents', run_details: 'Detailed runs', items: 'Items', asks: 'Input requests', retries: 'Retry attempts', turns: 'Provider turns', transfers: 'Handoffs', pr_links: 'PR links', logs: 'Log records' };
  const scans = c.scans ? `<p>Eligible source rows inspected (limits are budgets, not total denominators): ${Object.entries(c.scans).map(([key, scan]) => `${names[key]} ${number(scan.scanned)} / ${number(scan.limit)} budget${scan.truncated ? ' · CAPPED; eligible total unknown' : ''}`).join('; ')}. PR links also have a 512-link limit per thread. Omitted rows and samples are not included in counts or percentiles.</p>` : '<p>Per-source scan coverage is unavailable in this older snapshot.</p>';
  const prIncomplete = c.scans?.pr_links?.truncated || c.scans?.runs?.truncated;

  return `<section class="ca-panel ca-coverage"><h2>Coverage and definitions</h2><p>${c.partial ? 'Partial collection: an explicit limit or unreadable source was reached.' : 'All selected run/item scans completed within collection limits.'} ${number(c.runs_scanned)} / ${number(c.runs_seen)} selected runs scanned; ${number(c.runs_timed)} timed / ${number(c.runs_untimed)} missing timing; ${number(c.items_timed)} timed items / ${number(c.items_seen)} retained items inspected. ${number(c.threads_kept)} / ${number(c.threads_total)} thread traces retained.</p><p>${number(c.native_agents_timed)} / ${number(c.native_agents)} additional native agents have usable timing. Native children with projected runs are counted once. Missing timing is not included in measured hours. ${number(c.unknown_tools)} calls and ${number(c.unknown_models)} runs have unrecognized tool/model categories.</p><p>Source projection last updated: ${c.source_updated ? date(c.source_updated) : 'n/a: source timestamp unavailable'}. This records projection freshness, not a host heartbeat.</p><p>Initial recap query ${(c.metadata_ms / 1000).toFixed(3)} s · detail/log collection ${(c.detail_ms / 1000).toFixed(3)} s · process peak RSS ${(c.peak_rss_kib / 1024).toFixed(1)} MiB · source ${(c.source_bytes / 2 ** 30).toFixed(2)} GiB. These measurements describe this collection, not a cold-cache guarantee.</p><p>PR state snapshots: ${number(c.pr_states)} dated states among ${number(c.pr_unique)} inspected unique linked PRs (${number(c.pr_links)} links inspected before deduplication). ${prIncomplete ? 'Link collection was capped; the total denominator and state counts are unavailable.' : ''} ${c.pr_first ? `As of ${date(c.pr_first)} – ${date(c.pr_last)}; these are retained state counts, not live provider state or merge events in this window.` : 'n/a: no dated state snapshots.'}</p>${scans}<p>Local-only aggregation. Only numbers, fixed categories and opaque source identities reach this page. Source names, prompts, paths, command arguments and file contents are excluded. Task outcomes and live PR states require authoritative links; missing values retain their reason.</p></section>`;
}

export function mount(context) {
  let ctx = context, stopped = false, paused = false, pending = null, timer = null, epoch = 0;
  let hosts = [], host = '', window = '1', offset = 0, tab = 'recap', model = '', data = null, detail = null, problem = '', openedFrom = '';
  const root = ctx.root, doc = root.ownerDocument || globalThis.document;
  if (doc?.head && !doc.querySelector('link[data-activity-style]')) {
    const link = doc.createElement('link'); link.rel = 'stylesheet'; link.href = '/app/activity.css';
    link.dataset.activityStyle = ''; doc.head.append(link);
  }
  root.classList.add('compute-activity');
  root.innerHTML = `<header class="ca-title"><div><h1>Activity</h1><p>Where agent time went, from your local collector.</p></div><div class="ca-actions"><button type="button" class="btn" data-ca-disable hidden>Stop aggregate access</button><button type="button" class="btn" data-ca-pause aria-pressed="false">Pause updates</button><button type="button" class="btn" data-ca-refresh>Refresh</button></div></header><div class="ca-controls"><label>Source<select data-ca-host aria-label="Activity source"><option>Loading sources…</option></select></label><label>Window<select data-ca-window><option value="1">Last 24 hours</option><option value="7">Last 7 days</option><option value="30">Last 30 days</option></select></label><label data-ca-model-label hidden>Thread model<select data-ca-model><option value="">All models</option></select></label></div><p class="ca-fresh" data-ca-fresh role="status">Loading aggregate snapshots…</p><div data-ca-problem role="alert"></div><nav class="ca-tabs" aria-label="Activity views">${[['recap', 'Recap'], ['sinks', 'Time sinks'], ['trace', 'Trace'], ['live', 'Live']].map(([id, title]) => `<button type="button" data-ca-tab="${id}" aria-pressed="${id === tab}">${title}</button>`).join('')}</nav><div data-ca-body aria-busy="true"><section class="ca-panel"><h2>Recap</h2><p>Loading recorded activity…</p></section></div>`;
  const find = name => root.querySelector(`[data-ca-${name}]`);
  function paint() {
    if (stopped) return;
    find('problem').textContent = problem;
    find('model-label').hidden = !['trace', 'live'].includes(tab);
    find('disable').hidden = !hosts.some(row => row.id === host && row.enabled);
    find('body').setAttribute('aria-busy', String(!!pending));
    for (const button of root.querySelectorAll('[data-ca-tab]')) button.setAttribute('aria-pressed', String(button.dataset.caTab === tab));
    const focusedThread = root.contains(doc?.activeElement) ? doc.activeElement?.dataset?.caThread : '';
    const snapshot = data?.snapshot;
    find('fresh').textContent = snapshot ? `LOCAL · ${data.state === 'stale' || Date.now() / 1000 - Math.min(snapshot.collected_at, snapshot.window_end) > 180 ? 'Stale snapshot' : 'Snapshot'} collected ${date(snapshot.collected_at)} · ${date(snapshot.window_start)} – ${date(snapshot.window_end)} · Europe/Amsterdam${snapshot.coverage.partial ? ' · PARTIAL: collection limit or unreadable source; see coverage' : ''}${paused ? ' · Updates paused' : ''}` : paused ? 'Updates paused.' : 'No aggregate snapshot available.';
    if (problem && !snapshot) { find('body').innerHTML = '<section class="ca-panel"><h2>Activity is unavailable</h2><p>n/a: the protected aggregate source could not be read. Use Refresh to try again.</p></section>'; return; }
    if (!hosts.length) { find('body').innerHTML = '<section class="ca-panel"><h2>No Activity source connected</h2><p>No collector has been connected to a machine you enrolled. This is unavailable data, not zero activity. Enroll a machine in Compute Management, then allow its aggregate reporting here.</p><a class="ca-text" href="#/compute/management">Open Compute Management</a></section>'; return; }
    if (data?.state === 'not_enabled') {
      find('body').innerHTML = '<section class="ca-panel"><h2>Allow aggregate reporting</h2><p>This machine may report numeric activity across its locally observed projects to this workspace. Only you, as the enrolling owner, can read it. Prompts, titles, paths, commands and file contents are excluded. This grants no execution access.</p><button class="btn" type="button" data-ca-consent>Allow aggregates from this host</button></section>'; return;
    }
    if (!snapshot) { find('body').innerHTML = '<section class="ca-panel"><h2>Collector has not reported this window</h2><p>n/a: no aggregate snapshot is available. Check the collector on your source machine, or choose a window it has reported. Refresh reads stored aggregates; it does not scan source files.</p></section>'; return; }
    if (detail) { find('body').innerHTML = traceHTML(detail); return; }
    const page = `<div class="ca-page"><span>Threads ${data.page.total ? offset + 1 : 0}–${Math.min(offset + 25, data.page.total)} of ${data.page.total} retained · ${number(snapshot.coverage.threads_total)} observed</span><button class="btn" type="button" data-ca-prev ${offset ? '' : 'disabled'}>Previous</button><button class="btn" type="button" data-ca-next ${data.page.next == null ? 'disabled' : ''}>Next</button></div>`;
    find('body').innerHTML = tab === 'recap' ? recapHTML(snapshot) + coverageHTML(snapshot) : tab === 'sinks' ? sinksHTML(snapshot) + coverageHTML(snapshot) : tab === 'trace' ? `<section class="ca-panel"><h2>Thread traces</h2><p>Opaque source identities keep conversation titles private. Choose a thread to inspect aggregate phase buckets. Hours include additional native agents where timing is known.</p>${threadsHTML(snapshot, { model })}${page}</section>` : `<section class="ca-panel"><h2>Known active work</h2><p class="ca-finding">${number(snapshot.coverage.active_threads)} known active / queued threads across all models · ${number(snapshot.coverage.active_native)} additional native agents active as of collection.</p><p>This is a snapshot of known work, not a heartbeat or readiness signal. Stale data cannot establish current liveness. Storage: ${(snapshot.coverage.source_bytes / 2 ** 30).toFixed(2)} GiB database; ${(snapshot.coverage.log_bytes / 2 ** 20).toFixed(1)} MiB retained trace files.</p>${threadsHTML(snapshot, { live: true, model })}${page}</section>`;
    if (focusedThread) root.querySelector(`[data-ca-thread="${focusedThread}"]`)?.focus();
  }
  const schedule = () => { clearTimeout(timer); if (!stopped && !paused && !doc?.hidden) timer = setTimeout(() => { void load(false); }, 30000); };
  async function load(manual = true) {
    if (stopped || (!manual && (paused || doc?.hidden || pending || detail))) { schedule(); return; }
    pending?.abort(); const controller = new AbortController(); pending = controller; const mine = ++epoch;
    problem = ''; find('body').setAttribute('aria-busy', 'true');
    try {
      const sources = await ctx.api(BASE, { signal: controller.signal });
      if (mine !== epoch || stopped) return;
      hosts = sources.hosts || [];
      if (!hosts.some(row => row.id === host)) host = hosts[0]?.id || '';
      find('host').innerHTML = hosts.length ? hosts.map(row => `<option value="${e(row.id)}">Host ${short(row.id)}</option>`).join('') : '<option value="">No connected source</option>';
      find('host').value = host;
      if (host) data = await ctx.api(`${BASE}/${encodeURIComponent(host)}?window=${window}&offset=${offset}&view=${tab === 'live' ? 'live' : 'all'}&model=${encodeURIComponent(['trace', 'live'].includes(tab) ? model : '')}`, { signal: controller.signal });
      else data = null;
      if (mine !== epoch || stopped) return;
      const models = [...new Set([...(data?.thread_models || []), ...(data?.snapshot?.models || []).map(row => row.model), ...(data?.snapshot?.threads || []).map(row => row.model)])].sort();
      find('model').innerHTML = `<option value="">All models</option>${models.map(name => `<option value="${e(name)}">${e(name)}</option>`).join('')}`;
      find('model').value = model;
    } catch (error) {
      if (controller.signal.aborted || mine !== epoch || stopped) return;
      if ([401, 403, 404].includes(error?.status)) { data = null; detail = null; }
      problem = error?.status === 401 || error?.status === 403 ? 'Activity requires the signed-in enrolling owner. Sign in with that account.' : 'Activity could not be refreshed. Retained values below may be stale. Use Refresh to retry.';
    } finally {
      if (mine === epoch && !stopped) { pending = null; paint(); schedule(); }
    }
  }
  async function openThread(id) {
    if (!host) return;
    openedFrom = id;
    pending?.abort(); const controller = new AbortController(); pending = controller; const mine = ++epoch;
    problem = ''; find('body').setAttribute('aria-busy', 'true');
    try {
      const result = await ctx.api(`${BASE}/${encodeURIComponent(host)}/${encodeURIComponent(id)}?window=${window}`, { signal: controller.signal });
      if (!stopped && mine === epoch) detail = result.thread;
    } catch (error) { if (!controller.signal.aborted && mine === epoch) {
      if ([401, 403].includes(error?.status)) { data = null; detail = null; problem = 'Activity requires the signed-in enrolling owner. Sign in with that account.'; }
      else if (error?.status === 404 && error?.payload?.error !== 'trace_not_retained') { data = null; detail = null; problem = 'This Activity source is no longer available to this account.'; }
      else if (error?.status === 404) problem = 'No retained trace is available for this thread in this snapshot. Collection or retention limits may exclude its detail. Refresh only rereads the stored snapshot; it cannot restore omitted detail.';
      else problem = 'This thread trace could not be read. Use Refresh to retry.';
    } }
    finally { if (!stopped && mine === epoch) { pending = null; paint(); find('detail-title')?.focus(); } }
  }
  async function enable(enabled = true) {
    if (!host || pending) return;
    try { await ctx.api(`${BASE}/${encodeURIComponent(host)}/consent`, { method: 'POST', body: { enabled } }); await load(); }
    catch { problem = 'Aggregate reporting could not be enabled. Refresh and try again.'; paint(); }
  }
  const click = event => {
    const node = event.target.closest?.('button'); if (!node || !root.contains(node)) return;
    if (node.hasAttribute('data-ca-refresh')) void load();
    else if (node.hasAttribute('data-ca-pause')) { paused = !paused; node.textContent = paused ? 'Resume updates' : 'Pause updates'; node.setAttribute('aria-pressed', String(paused)); paint(); schedule(); }
    else if (node.dataset.caTab) { if (tab !== node.dataset.caTab) { tab = node.dataset.caTab; detail = null; offset = 0; data = null; void load(); } }
    else if (node.dataset.caThread) void openThread(node.dataset.caThread);
    else if (node.hasAttribute('data-ca-back')) { detail = null; paint(); (find('body').querySelector(`[data-ca-thread="${openedFrom}"]`) || find('body').querySelector('[data-ca-thread]'))?.focus(); schedule(); }
    else if (node.hasAttribute('data-ca-next') && !pending && data?.page.next != null) { offset = data.page.next; data = null; void load(); }
    else if (node.hasAttribute('data-ca-prev') && !pending && offset) { offset = Math.max(0, offset - 25); data = null; void load(); }
    else if (node.hasAttribute('data-ca-consent')) void enable();
    else if (node.hasAttribute('data-ca-disable')) void enable(false);
  };
  const change = event => {
    if (event.target === find('host')) host = event.target.value;
    else if (event.target === find('window')) window = event.target.value;
    else if (event.target === find('model')) { model = event.target.value; offset = 0; data = null; detail = null; void load(); return; }
    else return;
    offset = 0; model = ''; data = null; detail = null; void load();
  };
  const visibility = () => schedule();
  root.addEventListener('click', click); root.addEventListener('change', change); doc?.addEventListener('visibilitychange', visibility);
  void load();
  return { refresh(next = {}) { ctx = { ...ctx, ...next }; }, stop() { stopped = true; epoch++; pending?.abort(); clearTimeout(timer); root.removeEventListener('click', click); root.removeEventListener('change', change); doc?.removeEventListener('visibilitychange', visibility); root.classList.remove('compute-activity'); } };
}
