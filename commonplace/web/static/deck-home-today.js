// Shared Home/Today presentation and action contracts. No new write authority.
import { elapsed } from './api.js';
import { BUCKETS, needsCards, needsMeCount, hasQueue, hideReferences, setNeedsQueue } from './needs-me.js';

export { needsCards, needsMeCount };
const positive = text => /^(?:yes\b|accept\b|approve\b|ship\b|start\b|send\b|publish\b|proceed\b|go ahead\b)/i.test(String(text));
const holding = text => /^(?:hold\b|wait\b|not now\b|later\b)/i.test(String(text));
const actionable = card => Number.isSafeInteger(card.number) && card.number > 0 && Number.isSafeInteger(card.version) && card.version > 0;
export const stamp = card => `${card.number}:${card.version}`;
// Display copy only; the source card and submitted choice are kept verbatim.
export const displayText = value => hideReferences(String(value ?? '').replace(/\s+/g, ' '), /\bPR\s*#\d+\b|#\d+\b/gi, /(?:~\/|\/(?:home|Users|tmp|var|srv|opt|etc|mnt|work|api)\/|[A-Z]:\\)\S+/g);
export function removeNeed(needs = {}, card) {
  const matches = item => item.number === card.number && item.version === card.version;
  const found = needsCards(needs).some(matches);
  const next = { ...needs };
  for (const bucket of BUCKETS) if (Array.isArray(needs[bucket])) next[bucket] = needs[bucket].filter(item => !matches(item));
  next.count = hasQueue(next) ? needsCards(next).length : Math.max(0, needsMeCount(needs) - (found ? 1 : 0));
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
    // The shell's one entry for a queue it read: settled cards removed, `count` derived from the listed cards, and
    // the Board told which queue is current. Everything the shell shows as a count comes out of here.
    apply(needs, me) {
      scope(me); for (const card of settled.values()) needs = removeNeed(needs, card);
      const next = { ...needs, count: needsMeCount(needs) };
      setNeedsQueue(next);
      return next;
    },
  };
}
export function todayKey(event, enabled = true) {
  if (!enabled || event.defaultPrevented || event.repeat || event.ctrlKey || event.metaKey || event.altKey || event.target?.closest?.('input, textarea, select, [contenteditable]:not([contenteditable="false"]), dialog[open], [role="menu"]')) return null;
  return ({ a: 'approve', h: 'hold', r: 'reply', j: 'next', k: 'previous' })[event.key?.toLowerCase()] || null;
}
export function actionRequest(card, action, { reply = '', reason = '', choice, key = globalThis.crypto?.randomUUID?.() } = {}) {
  if (!actionable(card)) return null;
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
export function canApprove(card) {
  if (!actionable(card)) return false;
  const actions = card.actions || [], options = card.ask_options || [];
  return actions.includes('approve') || (actions.includes('accept') && !options.length) ||
    (actions.some(action => action === 'accept' || action === 'answer') && options.some(positive));
}
/** The card's one main button: Approve when an approval is on offer, otherwise Answer when it asks a question,
 *  otherwise Open (the card page). Hold and Reply stay beside it. */
export function primaryAction(card) {
  if (canApprove(card)) return { action: 'approve', label: 'Approve' };
  if ((card.actions || []).some(action => action === 'answer' || action === 'accept')) return { action: 'reply', label: 'Answer' };
  return { action: 'reply', label: 'Open' };
}
export function actionRow(card, { busy = false } = {}, e) {
  const disabled = busy ? ' disabled' : '';
  const main = primaryAction(card);
  const reject = (card.actions || []).includes('reject');
  return `<div class="bt-actions"><button type="button" class="bt-approve bt-primary" data-bt-action="${main.action}"${disabled}>${main.label}</button><button type="button" class="bt-hold" data-bt-action="hold"${disabled}>Hold</button><button type="button" class="bt-desktop-reply" data-bt-action="reply"${disabled}>Reply</button><details class="bt-more"><summary aria-label="More">⋯</summary><div role="menu" aria-label="More actions"><button type="button" role="menuitem" data-bt-action="reply">Reply</button>${reject ? `<button type="button" role="menuitem" data-bt-action="reject"${disabled}>Turn down</button>` : ''}</div></details></div>`;
}
/** Keep an opened card menu inside the viewport: shift it sideways when it overhangs an edge, flip it above its button
 *  when the space below is taken by the tab bar. Inline offsets are cleared first so every open measures afresh. */
export function placeMenu(menu, view = globalThis, { margin = 8, bottomReserve = 80 } = {}) {
  if (!menu?.getBoundingClientRect) return;
  menu.style.translate = ''; menu.style.top = ''; menu.style.bottom = '';
  const rect = menu.getBoundingClientRect(), width = view.innerWidth, height = view.innerHeight;
  let shift = 0;
  if (rect.left < margin) shift = margin - rect.left;
  else if (rect.right > width - margin) shift = width - margin - rect.right;
  if (shift) menu.style.translate = `${shift}px 0`;
  if (rect.bottom > height - bottomReserve && rect.height + margin <= (menu.parentElement?.getBoundingClientRect?.().top ?? 0)) { menu.style.top = 'auto'; menu.style.bottom = '42px'; }
}
export const avatar = (bot, e) => `<span class="bt-avatar" aria-hidden="true">${e(Array.from(bot?.name || '?')[0])}</span>`;
export const timeHTML = (at, e) => at && Number.isFinite(Date.parse(at)) ? `<time datetime="${e(at)}">${e(elapsed(at))}</time>` : '';
export const stateName = state => ({ working: 'Working', busy: 'Working', running: 'Running', queued: 'Queued', pending: 'Queued', streaming: 'Running', sending: 'Sending', polling: 'Running', dropped: 'Waiting for an update', needs_you: 'Needs you', needs_person: 'Needs you', ready: 'Ready', healthy: 'Healthy', completed: 'Completed', succeeded: 'Completed', done: 'Completed', error: 'Could not complete', failed: 'Could not complete', cancelled: 'Cancelled', idle: 'Idle', blocked: 'Blocked', needs_configuration: 'Needs configuration', interrupted: 'Interrupted' })[state] || (String(state).startsWith('failed:') ? 'Could not complete' : state === 'ok' ? 'Completed' : 'State unavailable');
export const stateTone = state => ['needs_you', 'needs_person'].includes(state) ? 'waiting' : ['working', 'busy', 'running', 'streaming', 'sending', 'polling', 'queued', 'pending'].includes(state) ? 'busy' : ['ready', 'healthy', 'done', 'completed', 'succeeded'].includes(state) ? 'live' : 'idle';
export const runFailed = run => /^(?:error|failed(?::.*)?|needs_configuration|model_error)$/.test(run.state);
export function runUpdate(run, message = {}, detail = {}) {
  run.state = message.state || detail.state || run.state;
  if (runFailed(run)) {
    run.reason = detail.text || message.reason || message.error?.message || message.error || message.text || '';
    if (typeof run.reason !== 'string') run.reason = '';
    run.code = detail.code || String(run.state).replace(/^failed:/, '');
  }
}
// A terminal failure starts a new turn; uncertain delivery reuses its receipt.
export function retryIntent(run) {
  return { ...run.intent, body: { ...run.intent.body }, requestId: run.acked ? globalThis.crypto.randomUUID() : run.requestId };
}
export function quietSelector(name, label, className) {
  return `<label class="bh-choice ${className}" hidden><span class="sr">${label}</span><span class="bh-choice-text" aria-hidden="true"></span><svg aria-hidden="true" viewBox="0 0 12 12" fill="none" stroke="currentColor"><path d="m3 4.5 3 3 3-3"/></svg><select name="${name}" aria-label="${label}"></select></label>`;
}
export function runHTML(run, e) {
  const steps = (run.steps || []).filter(step => typeof step?.label === 'string' && step.label), failed = runFailed(run);
  return `<article class="bh-run${failed ? ' bh-run-failed' : ''}"><header>${run.bot ? `${avatar(run.bot, e)}<b>${e(run.bot.name)}</b>` : ''}${timeHTML(run.at, e)}</header><p>${e(displayText(run.text))}</p>${steps.length ? `<ul>${steps.map(step => `<li class="bt-step" data-state="${e(step.state)}"><span aria-hidden="true">${['ok', 'done', 'completed', 'succeeded'].includes(step.state) ? '✓' : ['running', 'working'].includes(step.state) ? '▍' : '·'}</span>${e(displayText(step.label))}</li>`).join('')}</ul>` : ''}${failed || !steps.length ? `<p class="bh-run-state" role="status">${e(stateName(run.state))}</p>` : ''}${failed ? `${run.reason ? `<p class="bh-run-reason">${e(displayText(run.reason))}${run.code === 'needs_configuration' && run.canConfigure ? ' <a href="#/settings">Choose one</a>' : ''}</p>` : ''}${run.intent ? `<button type="button" class="bh-retry" data-bh-retry="${e(run.requestId)}">Try again</button>` : ''}` : ''}</article>`;
}
export function addStyles(doc) {
  if (!doc?.head || doc.querySelector('link[data-home-today]')) return;
  const link = doc.createElement('link'); link.rel = 'stylesheet'; link.href = new URL('./deck-home-today.css', import.meta.url).href; link.dataset.homeToday = ''; doc.head.append(link);
}
