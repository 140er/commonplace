// Shared Home/Today presentation and action contracts. No new write authority.
import { elapsed } from './api.js';

const BUCKETS = ['approvals', 'backlog_from_agents', 'asks', 'done_review', 'ready_to_merge', 'plans', 'stale'];
const positive = text => /^(?:yes\b|accept\b|approve\b|ship\b|start\b|send\b|publish\b|proceed\b|go ahead\b)/i.test(String(text));
const holding = text => /^(?:hold\b|wait\b|not now\b|later\b)/i.test(String(text));
export const stamp = card => `${card.number}:${card.version}`;
// Display copy only; the source card and submitted choice are kept verbatim.
export const displayText = value => String(value ?? '').replace(/\bPR\s*#\d+\b|#\d+\b/gi, '').replace(/(?:~\/|\/(?:home|Users|tmp|var|srv|opt|etc|mnt|work|api)\/|[A-Z]:\\)\S+/g, '').replace(/\s+/g, ' ').trim();
export function needsCards(needs = {}) {
  return [...new Map(BUCKETS.flatMap(bucket => Array.isArray(needs[bucket]) ? needs[bucket] : []).filter(card => Number.isSafeInteger(card?.number) && card.number > 0).map(card => [card.number, card])).values()];
}
export function removeNeed(needs = {}, card) {
  const matches = item => item.number === card.number && item.version === card.version;
  const found = needsCards(needs).some(matches);
  const next = { ...needs };
  for (const bucket of BUCKETS) if (Array.isArray(needs[bucket])) next[bucket] = needs[bucket].filter(item => !matches(item));
  next.count = Math.max(0, (Number.isSafeInteger(needs.count) ? needs.count : needsCards(needs).length) - (found ? 1 : 0));
  return next;
}
/** Keep a pre-decision read from restoring a badge, scoped to one signed-in person. */
export function createNeedLedger() {
  let identity = '', settled = new Map();
  function scope(me = {}) {
    const next = JSON.stringify([me.workspace, me.actor]);
    if (identity !== next) { identity = next; settled = new Map(); }
  }
  return {
    settle(card, me) { scope(me); settled.set(stamp(card), card); },
    apply(needs, me) { scope(me); for (const card of settled.values()) needs = removeNeed(needs, card); return needs; },
  };
}
export function todayKey(event, enabled = true) {
  if (!enabled || event.defaultPrevented || event.repeat || event.ctrlKey || event.metaKey || event.altKey || event.target?.closest?.('input, textarea, select, [contenteditable]:not([contenteditable="false"]), dialog[open], [role="menu"]')) return null;
  return ({ a: 'approve', h: 'hold', r: 'reply', j: 'next', k: 'previous' })[event.key?.toLowerCase()] || null;
}
export function actionRequest(card, action, { reply = '', reason = '', choice, key = globalThis.crypto?.randomUUID?.() } = {}) {
  if (!Number.isSafeInteger(card.number) || card.number < 1 || !Number.isSafeInteger(card.version) || card.version < 1) return null;
  const actions = card.actions || [], options = card.ask_options || [];
  const body = { version: card.version };
  let route = 'decide';
  if (action === 'approve' && actions.includes('approve')) { route = 'approve'; body.approve = true; }
  else if (action === 'reject' && actions.includes('reject')) { route = 'approve'; body.approve = false; }
  else if (action === 'approve' && actions.includes('accept')) {
    // Completion acceptance is the existing decision destination, with an explicit answer.
    body.then = 'done';
    const accepted = options.find(positive);
    if (accepted) body.choice = accepted;
    else if (!options.length) body.reply = 'Accepted';
    else return null;
  } else if (actions.includes('answer')) {
    if (action === 'approve') choice = options.find(positive);
    if (action === 'hold') choice = options.find(holding);
    if (choice !== undefined && options.includes(choice)) body.choice = choice;
    else if (action !== 'reply' || !reply.trim()) return null;
    if (reply.trim()) body.reply = reply;
  } else return null;
  if (route === 'approve' && reason.trim()) body.reason = reason;
  if (!key) return null;
  return { path: `/api/cards/${card.number}/${route}`, options: { method: 'POST', headers: { 'Idempotency-Key': key }, body } };
}
export function actionRow(card, { busy = false } = {}, e) {
  const disabled = busy ? ' disabled' : '';
  const approve = actionRequest(card, 'approve', { key: 'check' });
  const reject = (card.actions || []).includes('reject');
  const reply = true;
  return `<div class="bt-actions">${approve ? `<button type="button" class="bt-approve" data-bt-action="approve"${disabled}>Approve</button>` : ''}<button type="button" class="bt-hold" data-bt-action="hold"${disabled}>Hold</button>${reply ? `<button type="button" class="bt-desktop-reply" data-bt-action="reply"${disabled}>Reply</button>` : ''}<details class="bt-more"><summary aria-label="More">⋯</summary><div role="menu" aria-label="More actions">${reply ? '<button type="button" role="menuitem" data-bt-action="reply">Reply</button>' : ''}${reject ? `<button type="button" role="menuitem" data-bt-action="reject"${disabled}>Turn down</button>` : ''}${!reply && !reject ? `<a role="menuitem" href="#/card/${card.number}">Open card</a>` : ''}</div></details></div>`;
}
export const avatar = (bot, e) => `<span class="bt-avatar" aria-hidden="true">${e(Array.from(bot?.name || '?')[0])}</span>`;
export const timeHTML = (at, e) => at && Number.isFinite(Date.parse(at)) ? `<time datetime="${e(at)}">${e(elapsed(at))}</time>` : '';
export const stateName = state => ({ working: 'Working', busy: 'Working', running: 'Running', queued: 'Queued', pending: 'Queued', streaming: 'Running', sending: 'Sending', polling: 'Running', dropped: 'Waiting for an update', needs_you: 'Needs you', needs_person: 'Needs you', ready: 'Ready', healthy: 'Healthy', completed: 'Completed', succeeded: 'Completed', done: 'Completed', error: 'Could not complete', failed: 'Could not complete', cancelled: 'Cancelled', idle: 'Idle', blocked: 'Blocked', needs_configuration: 'Needs configuration', interrupted: 'Interrupted' })[state] || 'State unavailable';
export const stateTone = state => ['needs_you', 'needs_person'].includes(state) ? 'waiting' : ['working', 'busy', 'running', 'streaming', 'sending', 'polling', 'queued', 'pending'].includes(state) ? 'busy' : ['ready', 'healthy', 'done', 'completed', 'succeeded'].includes(state) ? 'live' : 'idle';
export function runHTML(run, e) {
  const steps = (run.steps || []).filter(step => typeof step?.label === 'string' && step.label);
  return `<article class="bh-run"><header>${run.bot ? `${avatar(run.bot, e)}<b>${e(run.bot.name)}</b>` : ''}${timeHTML(run.at, e)}</header><p>${e(displayText(run.text))}</p>${steps.length ? `<ul>${steps.map(step => `<li class="bt-step" data-state="${e(step.state)}"><span aria-hidden="true">${['ok', 'done', 'completed', 'succeeded'].includes(step.state) ? '✓' : ['running', 'working'].includes(step.state) ? '▍' : '·'}</span>${e(displayText(step.label))}</li>`).join('')}</ul>` : `<p class="bh-run-state" role="status">${e(stateName(run.state))}</p>`}</article>`;
}
export function addStyles(doc) {
  if (!doc?.head || doc.querySelector('link[data-home-today]')) return;
  const link = doc.createElement('link'); link.rel = 'stylesheet'; link.href = new URL('./deck-home-today.css', import.meta.url).href; link.dataset.homeToday = ''; doc.head.append(link);
}
