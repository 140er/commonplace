// The Pipeline view (#/pipeline): how each pull request was built and shipped, as connected blocks.
// Read-only. The server computes every number (GET /api/pipeline/prs); this file only draws them:
// one block per step with its state, times (Amsterdam time), duration and links, and between blocks
// the time spent waiting. The look is inspired by the block-and-connector canvas of
// github.com/AdamJBurns/workflows (MIT); no code or markup is taken from it.
import { escape as e } from './api.js';
import { artHTML } from './deck-art.js';

export const TITLE = 'Pipeline';
export const ZONE = 'Europe/Amsterdam';
export const STATE_WORDS = { pending: 'Not started', queued: 'Queued', running: 'Running', pass: 'Passed', fail: 'Failed', skipped: 'Skipped' };
export const PR_WORDS = { running: 'In progress', done: 'Deployed', failed: 'Stopped' };
const LINK_WORDS = { gate: 'Gate verdict', check: 'Check log', pr_opened: 'Pull request', merge: 'Pull request', card: 'Card' };
const marks = {
  pass: '<path d="m4.5 8.5 2.5 2.5 4.5-5.5"/>',
  fail: '<path d="m5 5 6 6m0-6-6 6"/>',
  running: '<circle cx="8" cy="8" r="2" fill="currentColor" stroke="none"/>',
  queued: '<path d="M8 4.5V8l2.5 1.5"/>',
  skipped: '<path d="M5 8h6"/>',
  pending: '',
};
const mark = state => `<svg class="pl-mark" data-state="${state}" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="8" cy="8" r="6.5"${state === 'pending' ? ' stroke-dasharray="2 2.4"' : ''}/>${marks[state] ?? ''}</svg>`;

// ── Formatting: Amsterdam time, durations like "3m 12s" and "1h 04m" ──
const clockFormat = new Intl.DateTimeFormat('en-GB', { timeZone: ZONE, hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
const dayFormat = new Intl.DateTimeFormat('en-GB', { timeZone: ZONE, weekday: 'short', day: 'numeric', month: 'short' });
const moment = iso => { const t = Date.parse(iso); return Number.isFinite(t) ? new Date(t) : null; };
/** HH:MM:SS in Amsterdam time ('' when the value is not a time). */
export const clock = iso => { const t = moment(iso); return t ? clockFormat.format(t) : ''; };
const stamp = iso => { const t = moment(iso); return t ? `${dayFormat.format(t)} ${clockFormat.format(t)}` : ''; };
const two = n => String(n).padStart(2, '0');
/** 45s, 3m 12s, 1h 04m, 1d 02h. Not a number gives ''. */
export function duration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return '';
  const s = Math.floor(seconds);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${two(s % 60)}s`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${two(Math.floor(s % 3600 / 60))}m`;
  return `${Math.floor(s / 86400)}d ${two(Math.floor(s % 86400 / 3600))}h`;
}
const stepLabel = (pr, name) => pr.steps.find(step => step.step === name)?.label || name;
const timeHTML = iso => iso ? `<time datetime="${e(iso)}" title="${e(stamp(iso))} (Amsterdam time)">${clock(iso)}</time>` : '';

// ── One block ──
function linkHTML(step, pr) {
  const ref = typeof step.ref === 'string' ? step.ref : '';
  if (!ref) return '';
  if (/^https:\/\//i.test(ref)) return `<a href="${e(ref)}" target="_blank" rel="noopener noreferrer">${e(LINK_WORDS[step.step] || 'Details')}</a>`;
  if (/^#\/card\/\d+$/.test(ref)) return `<a href="${e(ref)}">${LINK_WORDS.card} #${e(ref.slice(7))}</a>`;
  return `<span class="pl-ref" title="${e(LINK_WORDS[step.step] || 'Reference')}"><code>${e(ref)}</code></span>`;
}
export function blockHTML(step, pr) {
  const slowest = pr.slowest?.step === step.step, current = pr.current_step === step.step && pr.state !== 'done';
  const attempts = step.attempts > 1 ? `<span class="pl-chip" title="${step.attempts} attempts${step.span ? `, ${duration(step.span)} in all` : ''}">Attempt ${step.attempts}</span>` : '';
  const state = step.present ? step.state : 'pending';
  const word = step.present ? STATE_WORDS[state] : 'Not started';
  const times = [];
  if (step.present) {
    if (step.start) times.push(['Start', timeHTML(step.start)]);
    if (step.end) times.push(['End', timeHTML(step.end)]);
    else if (state === 'queued' && step.queued_at) times.push(['Queued', timeHTML(step.queued_at)]);
    if (step.duration !== null && step.duration !== undefined) times.push([step.running ? 'So far' : 'Took', `<b>${duration(step.duration)}</b>`]);
  }
  const label = `${step.label}: ${word}${step.duration != null ? `, ${duration(step.duration)}` : ''}${slowest ? ', the slowest step' : ''}`;
  const link = step.present ? linkHTML(step, pr) : '';
  const detail = step.present && step.detail ? `<p class="pl-detail">${e(step.detail)}</p>` : '';
  return `<article class="pl-block" aria-label="${e(label)}"><header>${mark(state)}<h4>${e(step.label)}</h4></header><p class="pl-state"><span>${word}</span>${attempts}${slowest ? '<span class="pl-chip pl-slowest">Slowest</span>' : ''}${current ? '<span class="pl-chip pl-now">Now</span>' : ''}</p>${times.length ? `<dl class="pl-times">${times.map(([name, value]) => `<div><dt>${name}</dt><dd>${value}</dd></div>`).join('')}</dl>` : ''}${detail}${link ? `<p class="pl-links">${link}</p>` : ''}</article>`;
}

/** The connector after a block: the wait before the next step, drawn on the line. */
export function connectorHTML(next, pr) {
  const wait = next?.wait;
  if (!wait || wait.overlap || !(wait.seconds > 0)) return '<span class="pl-wire" aria-hidden="true"></span>';
  const longest = pr.longest_wait?.before === next.step && pr.longest_wait.seconds === wait.seconds;
  const words = wait.ongoing ? `Waiting ${duration(wait.seconds)} for ${stepLabel(pr, next.step)} so far` : `Waited ${duration(wait.seconds)} before ${stepLabel(pr, next.step)}`;
  const queued = wait.queued_seconds > 0 && !wait.ongoing ? `, ${duration(wait.queued_seconds)} of it queued` : '';
  return `<span class="pl-wire" data-wait="${wait.seconds}"${wait.ongoing ? ' data-ongoing="true"' : ''}${longest ? ' data-longest="true"' : ''} title="${e(words + queued)}" role="img" aria-label="${e(words + queued)}"><span class="pl-wait">${duration(wait.seconds)}</span></span>`;
}

// ── One pull request ──
const stat = (name, value, note = '', title = '') => `<span class="pl-stat" data-stat="${name}"${title ? ` title="${e(title)}"` : ''}><small>${{ lead: 'Lead time', slowest: 'Slowest step', wait: 'Longest wait', now: 'Now' }[name]}</small><b>${value}</b>${note ? `<i>${e(note)}</i>` : ''}</span>`;
// The review comments of a PR, from GET /api/pr-review/prs (only PRs whose card the person can
// read): one chip on the PR and a link to the card, where each comment is shown.
export function reviewChip(review) {
  const counts = review?.counts || {};
  const total = Object.values(counts).reduce((sum, n) => sum + (Number(n) || 0), 0);
  if (!review || !total) return '';
  const busy = (Number(counts.new) || 0) + (Number(counts.working) || 0);
  const words = counts.needs_you ? `${counts.needs_you} review ${counts.needs_you === 1 ? 'comment needs' : 'comments need'} you`
    : busy ? `${busy} review ${busy === 1 ? 'comment is' : 'comments are'} being answered`
    : `${total} review ${total === 1 ? 'comment' : 'comments'}, all handled`;
  const tone = counts.needs_you ? 'failed' : busy ? 'running' : 'done';
  return `<span class="pl-pill pl-reviews" data-state="${tone}" data-pl-reviews>${e(words)}</span>`;
}
const reviewLink = review => Number.isInteger(review?.card) && review.card > 0 ? `<a href="#/card/${review.card}" data-pl-review-card>Review comments on card #${review.card}</a>` : '';

export function prHTML(pr, { open = true, review = null } = {}) {
  const lead = pr.lead || {}, slow = pr.slowest, wait = pr.longest_wait;
  const leadNote = lead.in_progress ? 'so far' : pr.state === 'failed' ? 'until it stopped' : lead.basis === 'first_event' ? 'from the first step' : '';
  const stats = [
    stat('lead', lead.seconds == null ? '–' : duration(lead.seconds), leadNote, lead.from ? `From ${stamp(lead.from)}${lead.to ? ` to ${stamp(lead.to)}` : ''} (Amsterdam time)` : ''),
    stat('slowest', slow ? `${e(stepLabel(pr, slow.step))} · ${duration(slow.seconds)}` : '–'),
    stat('wait', wait ? `${duration(wait.seconds)}` : '–', wait ? `before ${stepLabel(pr, wait.before)}${wait.ongoing ? ', still waiting' : ''}` : ''),
    pr.current_step && pr.state !== 'done' ? stat('now', e(stepLabel(pr, pr.current_step))) : '',
  ].join('');
  const flow = pr.steps.map((step, index) => `<li class="pl-cell" data-step="${e(step.step)}" data-state="${step.present ? step.state : 'pending'}"${pr.slowest?.step === step.step ? ' data-slowest="true"' : ''}>${blockHTML(step, pr)}${index < pr.steps.length - 1 ? connectorHTML(pr.steps[index + 1], pr) : ''}</li>`).join('');
  const url = `https://github.com/${pr.repo}/pull/${pr.pr}`;
  return `<details class="pl-pr" data-pr="${e(pr.repo)}#${pr.pr}" data-state="${pr.state}"${open ? ' open' : ''}><summary><span class="pl-title"><b>#${pr.pr}</b> ${e(pr.title || 'Pull request')}</span><span class="pl-pill" data-state="${pr.state}">${PR_WORDS[pr.state] || ''}</span>${reviewChip(review)}<span class="pl-stats">${stats}</span></summary><p class="pl-meta"><a href="${e(url)}" target="_blank" rel="noopener noreferrer">Pull request #${pr.pr}</a><span>${e(pr.repo)}</span>${reviewChip(review) ? reviewLink(review) : ''}</p><ol class="pl-flow" aria-label="Steps of pull request ${pr.pr}">${flow}</ol></details>`;
}

// ── The page ──
export const EMPTY_COPY = { title: 'No pull requests yet', body: 'Pipeline events come from the deploy flow. Each pull request shows up here as it goes from card to merge to deploy.' };
export const emptyHTML = () => `<div class="pl-empty" role="status">${artHTML('pipeline')}<h2>${EMPTY_COPY.title}</h2><p>${EMPTY_COPY.body}</p><button type="button" class="btn" data-pl="demo">See an example</button></div>`;
export const pageHTML = ({ prs = null, demo = false, error = '', choices = new Map(), reviews = new Map() } = {}) => {
  const head = `<header class="pl-head"><div><h1>${TITLE}</h1><p>How each pull request was built and shipped, step by step, and how long it waited in between. Times are Amsterdam time.</p></div></header>`;
  const banner = demo ? '<p class="pl-demo" role="status">This is an example, not your data. <button type="button" class="btn" data-pl="live">Back to your pull requests</button></p>' : '';
  let body;
  if (error) body = `<p class="pl-error" role="alert">${e(error)} <button type="button" class="btn" data-pl="retry">Try again</button></p>`;
  else if (prs === null) body = `<p class="pl-loading" role="status">${artHTML('loading')}Loading…</p>`;
  else if (!prs.length) body = emptyHTML();
  else body = prs.map((pr, index) => prHTML(pr, { open: choices.get(`${pr.repo}#${pr.pr}`) ?? index < 3, review: demo ? null : reviews.get(`${pr.repo}#${pr.pr}`) })).join('');
  return `${head}${banner}<div class="pl-list">${body}</div>`;
};

function styles(doc) {
  if (globalThis.commonplaceAssetManifest?.styles?.includes('deck-pipeline.css')) return;
  if (!doc?.head || doc.querySelector('link[data-pipeline-style]')) return;
  const link = doc.createElement('link');
  link.rel = 'stylesheet'; link.href = new URL('./deck-pipeline.css', import.meta.url).href;
  link.setAttribute('data-pipeline-style', ''); doc.head.append(link);
}

/** Mount the screen. `?demo=1` on the page address, or the example button, draws the fixed example. */
export function mount(context) {
  const { root, api } = context, doc = root.ownerDocument || globalThis.document;
  styles(doc);
  let demo = false, prs = null, error = '', stopped = false, epoch = 0, request = null, timer = null, reviews = new Map();
  try { demo = new URLSearchParams(globalThis.location?.search || '').get('demo') === '1'; } catch { /* live data */ }
  const choices = new Map();
  root.classList.add('deck-pipeline');
  const headers = ['.head'].map(selector => root.parentElement?.querySelector(selector)).filter(Boolean).map(node => ({ node, hidden: node.hidden }));
  for (const { node } of headers) node.hidden = true;
  let drawn = '';
  const paint = () => {
    if (stopped) return;
    const html = pageHTML({ prs, demo, error, choices, reviews });
    if (html === drawn) return;
    const top = root.scrollTop;
    root.innerHTML = drawn = html;
    root.scrollTop = top;
  };
  async function load() {
    const mine = ++epoch;
    request?.abort(); request = new AbortController();
    try {
      // The review counts are extra: a failure there never hides the pipeline itself.
      const [answer, review] = await Promise.all([
        api(`/api/pipeline/prs?limit=20${demo ? '&demo=1' : ''}`, { signal: request.signal, timeoutMs: 10000 }),
        demo ? null : api('/api/pr-review/prs?limit=100', { signal: request.signal, timeoutMs: 10000 }).catch(() => null),
      ]);
      if (stopped || mine !== epoch) return;
      prs = Array.isArray(answer?.prs) ? answer.prs : []; error = '';
      if (Array.isArray(review?.prs)) reviews = new Map(review.prs.map(item => [`${item.repo}#${item.pr}`, item]));
    } catch (failure) {
      if (stopped || mine !== epoch || failure?.name === 'AbortError') return;
      error = failure?.status === 403 ? 'You cannot see the pipeline in this workspace.' : 'The pipeline could not be loaded.';
    }
    paint();
  }
  const onToggle = event => {
    const key = event.target?.dataset?.pr;
    if (!key || event.target.isConnected === false) return;
    choices.set(key, event.target.open);
  };
  const onClick = event => {
    const action = event.target?.closest?.('[data-pl]')?.dataset.pl;
    if (!action) return;
    if (action === 'demo' || action === 'live') { demo = action === 'demo'; prs = null; error = ''; choices.clear(); paint(); void load(); }
    if (action === 'retry') { error = ''; prs = null; paint(); void load(); }
  };
  root.addEventListener('toggle', onToggle, true);
  root.addEventListener('click', onClick);
  paint(); void load();
  // Open work keeps counting, so the page reads again now and then while it is shown.
  timer = globalThis.setInterval?.(() => { if (!demo && !doc?.hidden) void load(); }, 15000);
  return {
    refresh() { void load(); },
    stop() {
      stopped = true; epoch++; request?.abort(); globalThis.clearInterval?.(timer);
      root.removeEventListener('toggle', onToggle, true); root.removeEventListener('click', onClick);
      root.classList.remove('deck-pipeline'); root.innerHTML = '';
      for (const { node, hidden } of headers) node.hidden = hidden;
    },
  };
}
