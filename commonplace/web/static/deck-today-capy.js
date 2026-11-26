import { activityEvent, groupActivity, activityHTML } from './deck-home-activity.js';
export { activityText } from './deck-home-activity.js';
import { escape as e } from './api.js';
import { addStyles, primaryAction, needsCards, configProposals, removeNeed, removeConfig, configKey, todayKey, stamp, displayText, actionRequest, avatar, timeHTML, needsMeCount } from './deck-home-today.js';
import { previewText } from './needs-me.js';

export const TITLE = 'Waiting on you';

/** What approving does, in one plain line under the Approve button. */
export const approveNote = who => `Approve means yes: ${who || 'the coworker'} goes ahead with this.`;
/** The names a reply dialog takes from what the person pressed: Answer opens "Answer", Reply opens "Reply". */
export const replyWords = (action, answering) => action === 'approve' ? { title: 'Reason for approval', label: 'Your reason', submit: 'Approve' } : answering ? { title: 'Answer', label: 'Your answer', submit: 'Send answer' } : { title: 'Reply', label: 'Your reply', submit: 'Send reply' };
/** The line shown once a send succeeded: what was sent, to whom, and where the card went. */
export function sentLine(card, action, request, who, answering = false) {
  const name = who || 'the coworker';
  if (action === 'approve') return request?.options?.body?.then === 'done' ? 'Accepted. The card is done and left the list.' : `Approved. ${name} goes ahead. The card left the list and is back on its Board.`;
  if (action === 'reject') return `Turned down. The card went back to ${name} and left the list.`;
  return `${answering ? 'Answer' : 'Reply'} sent to ${name}. The card left the list and is back on its Board.`;
}
const enc = encodeURIComponent;


const MOVED = 'Moved to review.';
// On a phone an answer longer than this takes the whole row, so its words do not wrap inside a half-width button.
export const LONG_ANSWER = 18;
const answerable = card => (card.actions || []).some(action => ['answer', 'accept'].includes(action));
/** The two kinds the filter chips split the list into: a question to answer, or work to review and approve. */
export const kindOf = card => card.review_kind === 'completion' || (card.actions || []).some(action => ['approve', 'accept', 'reject'].includes(action)) ? 'review' : 'question';
/** The short verb after the bot's name. */
export const verbOf = card => card.review_kind === 'completion' ? 'finished work' : kindOf(card) === 'review' ? 'wants your OK' : 'asks';
/** The line in the top bar. The number is the shared needs-me count, the same one the sidebar, the badges and the Board use. */
export const summaryLine = needs => { const count = needsMeCount(needs); return count ? `${count} ${count === 1 ? 'item' : 'items'} waiting` : 'Nothing is waiting'; };
/** The filter chips: every count comes from the cards listed below them. */
export function chipsHTML(list, filter = 'all') {
  const counts = { all: list.length, question: list.filter(card => kindOf(card) === 'question').length, review: list.filter(card => kindOf(card) === 'review').length };
  return [['all', 'All'], ['question', 'Questions'], ['review', 'Reviews']].map(([key, label]) => `<button type="button" class="bt-chip" data-bt-filter="${key}" aria-pressed="${filter === key}">${label} <b>${counts[key]}</b></button>`).join('');
}
/** One card's answers: the first is the main button, the others sit beside it, and a quiet Reply (or "See the work"
 *  when the card cannot be answered here) ends the row. Every button goes through the same action code as the keys. */
export function answersHTML(card, { busy = false } = {}, escape = e) {
  const options = card.ask_options || [], actions = card.actions || [], disabled = busy ? ' disabled' : '';
  const button = (cls, attrs, label) => `<button type="button" class="${cls}" ${attrs}${disabled}>${escape(displayText(label))}</button>`;
  const buttons = [];
  if (actions.includes('answer') && options.length) {
    // An option that accepts finished work goes through Approve; any other option is sent as the chosen answer.
    const accepted = actions.includes('accept') ? actionRequest(card, 'approve', { key: 'probe' })?.options.body.choice : undefined;
    // Every choice the bot offered is a button: a hidden fourth answer would be an answer you cannot give.
    options.forEach((option, index) => buttons.push(option === accepted ? [index, 'data-bt-action="approve"', option] : [index, `data-bt-action="reply" data-bt-choice="${index}"`, option]));
  } else {
    const main = primaryAction(card);
    buttons.push([0, `data-bt-action="${main.action}"`, main.label]);
    if (actions.includes('reject')) buttons.push([1, 'data-bt-action="reject"', 'Turn down']);
  }
  // Peer answers carry equal weight; only an action the item itself recommends (accepting finished work, or the
  // one main action) is styled as primary. The first button keeps bt-approve so the A key still answers it.
  const recommended = ([, attrs]) => attrs.includes('data-bt-action="approve"') || buttons.length === 1 || !options.length;
  const html = buttons.map((entry, at) => button(`${at === 0 ? 'bt-approve ' : ''}${recommended(entry) && at === 0 ? 'bt-primary' : 'bt-secondary'}${displayText(entry[2]).length > LONG_ANSWER ? ' bt-long' : ''}`, entry[1], entry[2])).join('');
  // Finished work and cards that cannot be answered here open the card; a question offers a typed Reply instead.
  const quiet = !buttons.length || buttons[0][2] === 'Open' ? '' : kindOf(card) === 'review' || !answerable(card) ? button('bt-quiet', 'data-bt-open', 'See the work') : options.length ? button('bt-quiet bt-desktop-reply', 'data-bt-action="reply"', 'Reply') + button('bt-quiet', 'data-bt-open', 'See the work') : button('bt-quiet', 'data-bt-open', 'See the work');
  return `<div class="bt-actions">${html}${quiet}</div>`;
}

// One card: the asker, the question as its title, one line of context and the answers. The stored words stay whole
// behind "Show full text"; only the display is shortened.
export function cardHTML(card, bots, { busy, errors, held, exiting, open = new Set() }, escape = e) {
  const bot = bots.find(bot => bot.board && bot.board === card.board);
  const name = displayText(bot?.name || card.bot_name || card.board || '');
  const approves = primaryAction(card).action === 'approve' && !(card.ask_options || []).length;
  const fact = card.fact || card.bot_reason;
  const waiting = answerable(card) || (card.actions || []).some(action => ['approve', 'reject'].includes(action));
  const words = card.body || card.ask || '';
  const question = card.ask && card.ask !== MOVED ? card.ask : card.title;
  const rest = String(card.review_context || (words.startsWith(question) ? words.slice(question.length) : words)).trim();
  const flat = displayText(card.display_body || rest), preview = previewText(flat);
  const askedFlat = displayText(card.display_title || question), asked = previewText(askedFlat, 220);
  const long = preview !== flat || asked !== askedFlat || (rest && flat !== rest);
  const full = long ? `<details class="bt-full" data-bt-full="${card.number}"${open.has(card.number) ? ' open' : ''}><summary>Show full text</summary><p class="bt-full-text">${escape(words)}</p></details>` : '';
  return `<article class="bt-card${waiting ? ' bt-waiting' : ''}" data-bt-card="${card.number}" data-bt-kind="${kindOf(card)}" tabindex="-1" aria-labelledby="bt-title-${card.number}"><header>${name ? `${avatar({ name }, escape)}<b>${escape(name)}</b>` : ''}<span class="bt-verb">${verbOf(card)}</span>${timeHTML(card.updated_at, escape)}</header><h2 id="bt-title-${card.number}">${escape(asked)}</h2>${preview ? `<p class="bt-body bt-preview">${escape(preview)}</p>` : ''}${full}${fact ? `<p class="bt-fact">${escape(displayText(fact))}</p>` : ''}${answersHTML(card, { busy: busy.has(card.number) || exiting.has(stamp(card)) }, escape)}${approves ? `<p class="bt-approve-note">${escape(approveNote(name))}</p>` : ''}${held.has(stamp(card)) ? '<p class="bt-held" role="status">Held for now. Still waiting on you.</p>' : ''}${errors.has(card.number) ? `<p class="bt-error" role="alert">${escape(errors.get(card.number))}</p>` : ''}</article>`;
}
/** A proposed Coworker config: who proposed it, what changes in one line, the diff behind "Changes", and Accept or
 *  Reject. Rejecting asks once first, with the same words as the project's config list. Same markup as a card. */
export function configHTML(item, { busy = new Set(), errors = new Map(), confirming = new Set(), open = new Set() } = {}, escape = e) {
  const key = configKey(item), disabled = busy.has(key) ? ' disabled' : '';
  const who = displayText(item.proposer_name || item.proposed_by || ''), project = displayText(item.board_name || item.board);
  const where = item.channel && item.channel !== 'live' ? ` (experiment ${displayText(String(item.channel).replace(/^exp:/, ''))})` : '';
  const asked = previewText(displayText(item.summary || 'A new config'), 220);
  const changes = `<details class="bt-full" data-bt-config-changes="${escape(key)}"${open.has(key) ? ' open' : ''}><summary>Changes</summary><p class="bt-full-text" data-config-diff>${escape(item.diff ?? 'Could not load the changes')}</p></details>`;
  const actions = confirming.has(key)
    ? `<div class="bt-actions" role="group" aria-label="Reject config ${escape(item.ref)}"><p class="bt-body">Reject config ${escape(item.ref)}? The current config stays in use. This version cannot be accepted after rejection.</p><button type="button" class="bt-approve bt-secondary" data-bt-config-action="reject-confirm"${disabled}>Reject version</button><button type="button" class="bt-quiet" data-bt-config-action="cancel"${disabled}>Cancel</button></div>`
    : `<div class="bt-actions"><button type="button" class="bt-approve bt-primary" data-bt-config-action="accept"${disabled}>Accept</button><button type="button" class="bt-secondary" data-bt-config-action="reject"${disabled}>Reject</button></div><p class="bt-approve-note">Accept means yes: ${escape(project)} uses ${escape(item.ref)} from now on.</p>`;
  return `<article class="bt-card bt-waiting" data-bt-config="${escape(key)}" data-bt-kind="review" tabindex="-1" aria-labelledby="bt-title-${escape(key)}"><header>${who ? `${avatar({ name: who }, escape)}<b>${escape(who)}</b>` : ''}<span class="bt-verb">suggests a new config</span>${timeHTML(item.proposed_at, escape)}</header><h2 id="bt-title-${escape(key)}">${escape(asked)}</h2><p class="bt-body bt-preview">${escape(project)}: config ${escape(item.ref)}${escape(where)}</p>${changes}${actions}${errors.has(key) ? `<p class="bt-error" role="alert">${escape(errors.get(key))}</p>` : ''}</article>`;
}
/** The list when nothing is open: one calm line once loaded, otherwise the plain loading line. */
export const emptyCardsHTML = (loaded, filtered = false) => `<p class="bt-calm" role="status">${loaded ? filtered ? 'Nothing here. Choose All to see everything waiting on you.' : 'Nothing waits on you. Questions and finished work from your bots show up here.' : 'Loading…'}</p>`;
export function mount(context) {
  const { root, api } = context, doc = root.ownerDocument || globalThis.document;
  addStyles(doc);
  const host = root.closest?.('.center') || root;
  host.classList.add('deck-today-capy');
  root.innerHTML = `<header class="bt-bar"><h1>Waiting on you</h1><span class="bt-sub" data-bt="summary">Loading…</span><span class="bt-sp"></span><nav class="bt-chips" data-bt="chips" aria-label="Show"></nav></header><div class="bt-scroll"><div class="bt-col"><p class="bt-confirm" data-bt="confirm" role="status" hidden></p><section class="bt-list" data-bt="cards" aria-label="Waiting on you"></section><p class="bt-keys" data-bt="keys">Keys: J and K move between items, R replies, H puts an item on hold</p><section class="bt-recent" aria-labelledby="bt-recent-title"><h3 id="bt-recent-title">Since you last looked</h3><ol data-bt="activity"></ol></section><p class="bt-error" data-bt="error" role="alert" hidden></p></div></div><dialog class="bt-reply" aria-labelledby="bt-reply-h"><form><h2 id="bt-reply-h">Reply</h2><p data-bt="reply-title"></p><p class="bt-reply-note" data-bt="reply-note" hidden></p><label data-bt="choice-label">Choose an answer<select name="choice"></select></label><label><span data-bt="reply-label">Your reply</span><textarea name="reply" maxlength="8000" rows="3"></textarea></label><p class="bt-error" data-bt="reply-error" role="alert"></p><div><button type="button" data-bt-cancel>Cancel</button><button type="submit">Send reply</button></div></form></dialog>`;
  const region = name => root.querySelector(`[data-bt="${name}"]`);
  const dialog = root.querySelector('dialog'), form = dialog.querySelector('form');
  let ctx = context, active = true, generation = 0, read, current = null, currentAction = 'reply', currentAnswering = false, confirmTimer = null, focused = null, filter = 'all', needs = ctx.needs || {}, bots = [], events = [], seq = 0, eventEpoch = '', loaded = false;
  const busy = new Set(), errors = new Map(), held = new Set(), exiting = new Set(), settled = new Set(), leaving = new Map(), attempts = new Map(), writes = new Set(), timers = new Set(), activity = [], openGroups = new Set(), openFull = new Set(), openChanges = new Set(), confirming = new Set(), settledConfigs = new Set();
  const botName = card => (bots.find(bot => bot.board && bot.board === card.board)?.name || card.bot_name || '');
  function cards() {
    const list = needsCards(needs).filter(card => !settled.has(stamp(card)));
    for (const { card, index } of [...leaving.values()].sort((a, b) => a.index - b.index)) if (!list.some(item => item.number === card.number)) list.splice(Math.min(index, list.length), 0, card);
    return list;
  }
  const visible = () => cards().filter(card => filter === 'all' || kindOf(card) === filter);
  // Config proposals are decisions to approve, so they sit under All and Reviews, ahead of the cards.
  const configs = () => configProposals(needs).filter(item => !settledConfigs.has(configKey(item)));
  const visibleConfigs = () => filter === 'all' || filter === 'review' ? configs() : [];
  function focus(number) {
    focused = number;
    root.querySelector(`[data-bt-card="${number}"]`)?.focus({ preventScroll: true });
  }
  function paint() {
    if (!active) return;
    const all = cards(), list = visible(), proposed = visibleConfigs(), old = doc?.activeElement?.closest?.('[data-bt-card]'), mark = old?.dataset.btCard;
    region('summary').textContent = loaded || needsMeCount(needs) ? summaryLine(needs) : 'Loading…';
    region('chips').innerHTML = chipsHTML([...all, ...configs().map(item => ({ ...item, actions: ['approve', 'reject'] }))], filter);
    region('keys').hidden = !list.length;
    region('cards').innerHTML = list.length || proposed.length ? proposed.map(item => configHTML(item, { busy, errors, confirming, open: openChanges })).join('') + list.map(card => cardHTML(card, bots, { busy, errors, held, exiting, open: openFull })).join('') : emptyCardsHTML(loaded, all.length > 0 || configs().length > 0);
    for (const card of list.filter(card => exiting.has(stamp(card)))) {
      const node = root.querySelector(`[data-bt-card="${card.number}"]`);
      node?.getBoundingClientRect(); node?.classList.add('bt-exiting');
    }
    const rows = groupActivity([...activity, ...events.map(event => event.activity)].filter(row => row?.text)).slice(0, 6), names = bots.map(bot => displayText(bot.name));
    region('activity').innerHTML = rows.length ? rows.map(row => activityHTML(row, e, openGroups.has(row.id), names)).join('') : '<li class="bt-calm">Nothing new yet.</li>';
    for (const row of region('activity').querySelectorAll('.bt-new-activity')) for (const animation of row.getAnimations()) animation.currentTime = Math.max(0, Date.now() - Date.parse(row.dataset.at));
    if (mark && list.some(card => String(card.number) === mark)) focus(Number(mark));
  }
  async function refresh(next = {}) {
    if (!active) return;
    ctx = { ...ctx, ...next };
    read?.abort(); read = new AbortController();
    const token = ++generation, alive = () => active && token === generation;
    const options = { signal: read.signal, timeoutMs: 10000 };
    const reads = [
      ctx.me?.can_admin === true ? api('/api/needs-me', options) : Promise.resolve(ctx.needs || { count: 0 }),
      api('/api/bots', options),
      api(`/api/events?after=${seq}${eventEpoch ? `&epoch=${enc(eventEpoch)}` : ''}`, options),
    ];
    const results = reads.map(() => ({ status: 'pending' }));
    const settle = (index, result) => {
      results[index] = result;
      if (!alive()) return;
      const [queue, roster, feed] = results;
      if (queue.status === 'fulfilled') {
        needs = queue.value || { count: 0 };
        for (const card of needsCards(needs)) if (settled.has(stamp(card)) || exiting.has(stamp(card))) needs = removeNeed(needs, card);
        for (const item of configProposals(needs)) if (settledConfigs.has(configKey(item))) needs = removeConfig(needs, item);
        loaded = true;
      }
      if (roster.status === 'fulfilled') bots = roster.value?.bots || [];
      else if ([401, 403, 404].includes(roster.reason?.status)) bots = [];
      if (feed.status === 'fulfilled') {
        if (feed.value.reset) events = [];
        events = [...new Map([...events, ...(feed.value.events || [])].map(event => [event.seq || event.id, event])).values()].sort((a, b) => String(b.at).localeCompare(String(a.at)) || (b.seq || 0) - (a.seq || 0)).slice(0, 12).map(event => ({ ...event, activity: event.activity || activityEvent(event, bots, needs) }));
        seq = feed.value.seq || 0; eventEpoch = feed.value.epoch || '';
      } else if ([401, 403].includes(feed.reason?.status)) events = [];
      // Until the roster has arrived, an event cannot name its bot: read its words again once it has.
      events = events.map(event => event.named ? event : { ...event, activity: activityEvent(event, bots, needs), named: bots.length > 0 });
      region('error').hidden = queue.status !== 'rejected' && roster.status !== 'rejected';
      region('error').textContent = queue.status === 'rejected' ? 'This page could not be refreshed. Try again.' : roster.status === 'rejected' ? 'Your bots could not be refreshed.' : '';
      if (queue.status === 'rejected' && [401, 403, 404].includes(queue.reason?.status)) { needs = { count: 0 }; dialog.close(); current = null; }
      paint();
    };
    await Promise.all(reads.map((read, index) => read.then(
      value => settle(index, { status: 'fulfilled', value }),
      reason => settle(index, { status: 'rejected', reason }),
    )));
  }
  function openReply(card, action = 'reply', answering = false) {
    if (action === 'reply' && !(card.actions || []).some(action => ['answer', 'accept'].includes(action))) { ctx.go?.(`#/card/${card.number}`); return; }
    current = card; currentAction = action; currentAnswering = answering;
    const words = replyWords(action, answering);
    dialog.querySelector('h2').textContent = words.title;
    region('reply-label').textContent = words.label;
    form.querySelector('[type="submit"]').textContent = words.submit;
    region('reply-note').hidden = action !== 'approve';
    region('reply-note').textContent = action === 'approve' ? approveNote(displayText(botName(card))) : '';
    region('reply-title').textContent = displayText(card.title);
    const choices = card.ask_options || [];
    form.elements.choice.innerHTML = '<option value="">Reply in your own words</option>' + choices.map((choice, i) => `<option value="${i}">${e(displayText(choice))}</option>`).join('');
    region('choice-label').hidden = action !== 'reply' || !choices.length;
    form.elements.reply.value = ''; region('reply-error').textContent = '';
    dialog.showModal(); form.elements.reply.focus();
  }
  // One short line under the heading names what was sent and where the card went; it clears itself.
  function confirm(text) {
    const line = region('confirm'); line.textContent = text; line.hidden = false;
    clearTimeout(confirmTimer); confirmTimer = setTimeout(() => { line.hidden = true; line.textContent = ''; }, 8000);
  }
  async function act(card, action, extra = {}) {
    if (!active || busy.has(card.number) || settled.has(stamp(card)) || exiting.has(stamp(card))) return;
    if (action === 'approve' && card.approval_kind?.startsWith('origin') && !extra.submit) { openReply(card, action); return; }
    if (action === 'reply' && !extra.submit) { openReply(card, 'reply', extra.answering === true); return; }
    if (action === 'approve' && card.approval_kind?.startsWith('origin') && !extra.reason?.trim()) { region('reply-error').textContent = 'Write a reason for this approval.'; return; }
    const signature = JSON.stringify([stamp(card), action, extra.reply, extra.choice, extra.reason]), answering = extra.answering === true || currentAnswering;
    if (!attempts.has(signature)) attempts.set(signature, globalThis.crypto.randomUUID());
    const request = actionRequest(card, action, { ...extra, key: attempts.get(signature) });
    if (!request && action === 'hold') { held.add(stamp(card)); paint(); return; }
    if (!request) return;
    const controller = new AbortController(); writes.add(controller); busy.add(card.number); errors.delete(card.number); paint();
    try {
      await api(request.path, { ...request.options, signal: controller.signal, timeoutMs: 10000 });
      if (!active) return;
      if (current?.number === card.number) { dialog.close(); current = null; }
      const at = new Date().toISOString();
      activity.unshift({ at, text: `${action === 'approve' ? 'You approved' : action === 'reject' ? 'You turned down' : action === 'hold' ? 'You held' : answering ? 'You answered' : 'You replied to'} ${displayText(card.title)}`, fresh: true });
      leaving.set(stamp(card), { card, index: Math.max(0, cards().findIndex(item => item.number === card.number)) });
      confirm(sentLine(card, action, request, displayText(botName(card)), answering));
      needs = removeNeed(needs, card); ctx.onNeedsChanged?.(card);
      exiting.add(stamp(card)); paint();
      const timer = setTimeout(() => {
        timers.delete(timer); if (!active) return;
        settled.add(stamp(card)); exiting.delete(stamp(card)); leaving.delete(stamp(card)); paint();
        const remaining = visible(); if (remaining.length) focus(remaining[0].number);
      }, 280); timers.add(timer);
    } catch (error) {
      if (!active) return;
      const text = error.status === 409 ? 'This card changed. Review its latest version.' : error.message || 'Could not send. Try again.';
      errors.set(card.number, text); region('reply-error').textContent = text;
      if ([403, 404, 409].includes(error.status)) { dialog.close(); current = null; await refresh(); }
    } finally { writes.delete(controller); busy.delete(card.number); paint(); }
  }
  // Accept and Reject go through the project's existing config routes; the owner check stays on the server.
  async function decideConfig(item, action) {
    const key = configKey(item);
    if (!active || busy.has(key) || settledConfigs.has(key)) return;
    const controller = new AbortController(); writes.add(controller); busy.add(key); errors.delete(key); confirming.delete(key); paint();
    try {
      await api(`/api/orch/boards/${enc(item.board)}/configs/${item.version}/${action}`, { method: 'POST', body: action === 'accept' ? { sha256: item.sha256 } : {}, signal: controller.signal, timeoutMs: 10000 });
      if (!active) return;
      const project = displayText(item.board_name || item.board);
      activity.unshift({ at: new Date().toISOString(), text: `You ${action === 'accept' ? 'accepted' : 'rejected'} the ${project} config ${item.ref}`, fresh: true });
      confirm(action === 'accept' ? `Accepted. ${item.ref} is now the config for ${project}.` : `Rejected. The current config for ${project} stays in use.`);
      settledConfigs.add(key); needs = removeConfig(needs, item); ctx.onNeedsChanged?.(item);
    } catch (error) {
      if (!active) return;
      errors.set(key, error.status === 409 ? 'This config changed. Review the latest version.' : error.message || 'Could not send. Try again.');
      if ([403, 404, 409].includes(error.status)) await refresh();
    } finally { writes.delete(controller); busy.delete(key); paint(); }
  }
  function onConfigClick(event) {
    const button = event.target.closest?.('[data-bt-config-action]');
    if (!button) return false;
    event.preventDefault();
    const key = button.closest('[data-bt-config]')?.dataset.btConfig, item = configs().find(item => configKey(item) === key);
    if (!item || button.disabled) return true;
    const action = button.dataset.btConfigAction;
    if (action === 'reject') { confirming.add(key); paint(); [...root.querySelectorAll('[data-bt-config-action="cancel"]')].find(node => node.closest('[data-bt-config]')?.dataset.btConfig === key)?.focus?.(); }
    else if (action === 'cancel') { confirming.delete(key); paint(); }
    else void decideConfig(item, action === 'reject-confirm' ? 'reject' : 'accept');
    return true;
  }
  function onClick(event) {
    if (onConfigClick(event)) return;
    if (event.target.closest?.('[data-bt-cancel]')) { dialog.close(); current = null; return; }
    const chip = event.target.closest?.('[data-bt-filter]');
    if (chip) { filter = chip.dataset.btFilter; paint(); return; }
    const button = event.target.closest?.('[data-bt-action]'), article = button?.closest('[data-bt-card]');
    const card = cards().find(card => card.number === Number(article?.dataset.btCard));
    const open = event.target.closest?.('[data-bt-open]'), openCard = open && cards().find(card => card.number === Number(open.closest('[data-bt-card]')?.dataset.btCard));
    if (openCard) { event.preventDefault(); ctx.go?.(`#/card/${openCard.number}`); return; }
    if (!card || !button || button.disabled) return;
    event.preventDefault(); focused = card.number;
    // An answer button sends its own words straight away; the others open the reply dialog or act as before.
    if (button.dataset.btChoice !== undefined) void act(card, 'reply', { submit: true, answering: true, choice: card.ask_options?.[Number(button.dataset.btChoice)] });
    else void act(card, button.dataset.btAction, button.matches('.bt-primary') ? { answering: true } : {});
  }
  function onKey(event) {
    if (dialog.open || doc?.querySelector?.('dialog[open]')) return;
    const action = todayKey(event, ctx.singleKeys?.() !== false), list = visible().filter(card => !exiting.has(stamp(card)));
    // Approving takes a click on the button, never a single key: one stray key press must not ship work.
    if (!action || action === 'approve' || !list.length) return;
    event.preventDefault(); event.stopPropagation?.();
    const index = list.findIndex(card => card.number === focused);
    if (['next', 'previous'].includes(action)) focus(list[index < 0 ? action === 'next' ? 0 : list.length - 1 : (index + (action === 'next' ? 1 : list.length - 1)) % list.length].number);
    else void act(list[Math.max(0, index)], action);
  }
  function onSubmit(event) {
    event.preventDefault(); if (!current) return;
    if (currentAction === 'approve') { void act(current, 'approve', { submit: true, reason: form.elements.reply.value }); return; }
    const choice = current.ask_options?.[Number(form.elements.choice.value)];
    void act(current, 'reply', { submit: true, answering: currentAnswering, reply: form.elements.reply.value, ...(form.elements.choice.value !== '' ? { choice } : {}) });
  }
  function onGroupToggle(event) { const group = event.target; if (group.matches?.('[data-bt-config-changes]')) { const key = group.dataset.btConfigChanges; if (group.open) openChanges.add(key); else openChanges.delete(key); return; }
    if (group.matches?.('.bt-full')) { const number = Number(group.dataset.btFull); if (group.open) openFull.add(number); else openFull.delete(number); return; }
    if (!group.matches?.('[data-bt-group]')) return; if (group.open) openGroups.add(group.dataset.btGroup); else openGroups.delete(group.dataset.btGroup); }
  root.addEventListener('toggle', onGroupToggle, true);
  root.addEventListener('click', onClick); form.addEventListener('submit', onSubmit); doc?.addEventListener('keydown', onKey, true);
  paint(); void refresh();
  return { refresh, stop() {
    active = false; generation++; read?.abort(); writes.forEach(controller => controller.abort()); timers.forEach(clearTimeout); clearTimeout(confirmTimer);
    dialog.close(); root.removeEventListener('toggle', onGroupToggle, true); root.removeEventListener('click', onClick); form.removeEventListener('submit', onSubmit); doc?.removeEventListener('keydown', onKey, true); host.classList.remove('deck-today-capy');
  } };
}
