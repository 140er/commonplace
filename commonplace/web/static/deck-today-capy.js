import { escape as e } from './api.js';
import { addStyles, needsCards, removeNeed, todayKey, stamp, displayText, actionRequest, actionRow, avatar, timeHTML, stateName, stateTone } from './deck-home-today.js';

export const TITLE = 'Today';
const enc = encodeURIComponent;
export function activityText(event, bots = [], needs = {}) {
  if (event.summary) return event.summary;
  const title = event.data?.title, name = bots.find(bot => bot.board && bot.board === event.data?.board)?.name;
  const waiting = needsCards(needs).some(card => card.number === event.data?.number);
  if (!title) return event.kind === 'memory.saved' ? 'Memory was saved' : '';
  if (event.kind === 'card.review_needed' || (event.kind === 'card.created' && waiting)) return name ? `${name} asked: ${title}` : `Waiting for your decision: ${title}`;
  const verbs = { 'card.created': 'added', 'card.updated': 'updated', 'card.moved': 'made progress on', 'card.commented': 'replied to', 'card.decided': 'recorded a decision on' };
  const verb = verbs[event.kind];
  return verb ? `${name ? `${name} ${verb}` : verb[0].toUpperCase() + verb.slice(1)} ${title}` : '';
}

export function cardHTML(card, bots, { busy, errors, held, exiting }, escape = e) {
  const bot = bots.find(bot => bot.board && bot.board === card.board);
  const name = bot?.name || card.bot_name;
  const fact = card.fact || card.bot_reason;
  const waiting = (card.actions || []).some(action => ['approve', 'answer', 'accept', 'reject'].includes(action));
  return `<article class="bt-card${waiting ? ' bt-waiting' : ''}" data-bt-card="${card.number}" tabindex="-1" aria-labelledby="bt-title-${card.number}"><header>${name ? `${avatar({ name }, escape)}<b>${escape(displayText(name))}</b>` : ''}${timeHTML(card.updated_at, escape)}</header><h2 id="bt-title-${card.number}">${escape(displayText(card.display_title || card.title))}</h2>${card.body || card.ask ? `<p class="bt-body">${escape(displayText(card.display_body || card.body || card.ask))}</p>` : ''}${fact ? `<p class="bt-fact">${escape(displayText(fact))}</p>` : ''}${actionRow(card, { busy: busy.has(card.number) || exiting.has(stamp(card)) }, escape)}${held.has(stamp(card)) ? '<p class="bt-held" role="status">Held for now. Still waiting on you.</p>' : ''}${errors.has(card.number) ? `<p class="bt-error" role="alert">${escape(errors.get(card.number))}</p>` : ''}</article>`;
}
export function botsHTML(bots, escape = e) {
  return `<section class="bt-bots"><header><h2>Your bots</h2><span>Latest from each</span></header><ul>${bots.map(bot => `<li><a href="#/bots/${enc(bot.id)}">${avatar(bot, escape)}<span class="bt-bot-copy"><b>${escape(displayText(bot.name))}${bot.state ? ` <i class="bt-dot" data-tone="${stateTone(bot.state)}" aria-label="${escape(stateName(bot.state))}"></i>` : ''}</b>${bot.last?.text ? `<span>${escape(displayText(bot.last.text))}</span>` : ''}</span>${timeHTML(bot.last?.at, escape)}</a></li>`).join('')}</ul>${bots.length ? '' : '<p class="bt-empty">No bots yet. <a href="#/bots?new=1">New bot</a></p>'}</section>`;
}
export function mount(context) {
  const { root, api } = context, doc = root.ownerDocument || globalThis.document;
  addStyles(doc);
  const host = root.closest?.('.center') || root;
  host.classList.add('deck-today-capy');
  root.innerHTML = `<div class="bt-scroll"><div class="bt-page"><header class="bt-title"><h1>Today</h1><p data-bt="summary">Loading…</p></header><div class="bt-grid"><div class="bt-main"><section class="bt-list" data-bt="cards" aria-label="Needs me"></section><p class="bt-keys">A Approve · H Hold · R Reply · J K Move</p><div data-bt="bots"></div></div><aside class="bt-activity" aria-labelledby="bt-activity-title"><header><h2 id="bt-activity-title">Activity</h2></header><ol data-bt="activity"></ol></aside></div><p class="bt-error" data-bt="error" role="alert" hidden></p></div></div><dialog class="bt-reply"><form><h2>Reply</h2><p data-bt="reply-title"></p><label data-bt="choice-label">Choose an answer<select name="choice"></select></label><label>Your reply<textarea name="reply" maxlength="8000" rows="3"></textarea></label><p class="bt-error" data-bt="reply-error" role="alert"></p><div><button type="button" data-bt-cancel>Cancel</button><button type="submit">Send reply</button></div></form></dialog>`;
  const region = name => root.querySelector(`[data-bt="${name}"]`);
  const dialog = root.querySelector('dialog'), form = dialog.querySelector('form');
  let ctx = context, active = true, generation = 0, read, current = null, currentAction = 'reply', focused = null, needs = ctx.needs || {}, bots = [], events = [], seq = 0, eventEpoch = '', loaded = false;
  const busy = new Set(), errors = new Map(), held = new Set(), exiting = new Set(), settled = new Set(), leaving = new Map(), attempts = new Map(), writes = new Set(), timers = new Set(), activity = [];
  function cards() {
    const list = needsCards(needs).filter(card => !settled.has(stamp(card)));
    for (const { card, index } of [...leaving.values()].sort((a, b) => a.index - b.index)) if (!list.some(item => item.number === card.number)) list.splice(Math.min(index, list.length), 0, card);
    return list;
  }
  function focus(number) {
    focused = number;
    root.querySelector(`[data-bt-card="${number}"]`)?.focus({ preventScroll: true });
  }
  function paint() {
    if (!active) return;
    const list = cards(), old = doc?.activeElement?.closest?.('[data-bt-card]'), mark = old?.dataset.btCard;
    const count = Number.isSafeInteger(needs.count) ? needs.count : list.length;
    region('summary').textContent = `${new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })} · ${count} waiting on you`;
    region('cards').innerHTML = list.length ? list.map(card => cardHTML(card, bots, { busy, errors, held, exiting })).join('') : `<p class="bt-empty" role="status">${loaded ? 'Nothing is waiting on you' : 'Loading…'}</p>`;
    for (const card of list.filter(card => exiting.has(stamp(card)))) {
      const node = root.querySelector(`[data-bt-card="${card.number}"]`);
      node?.getBoundingClientRect(); node?.classList.add('bt-exiting');
    }
    region('bots').innerHTML = botsHTML(bots);
    const rows = [...activity, ...events.map(event => ({ at: event.at, text: activityText(event, bots, needs), fresh: false }))].filter(row => row.text).slice(0, 12);
    region('activity').innerHTML = rows.length ? rows.map(row => `<li${row.fresh && Date.now() - Date.parse(row.at) < 4000 ? ` class="bt-new-activity" data-at="${e(row.at)}"` : ''}>${timeHTML(row.at, e)}<span>${e(displayText(row.text))}</span></li>`).join('') : '<li class="bt-empty">No recent activity.</li>';
    for (const row of region('activity').querySelectorAll('.bt-new-activity')) for (const animation of row.getAnimations()) animation.currentTime = Math.max(0, Date.now() - Date.parse(row.dataset.at));
    if (mark && list.some(card => String(card.number) === mark)) focus(Number(mark));
  }
  async function refresh(next = {}) {
    if (!active) return;
    ctx = { ...ctx, ...next };
    read?.abort(); read = new AbortController();
    const token = ++generation, alive = () => active && token === generation;
    const options = { signal: read.signal, timeoutMs: 10000 };
    const results = await Promise.allSettled([
      ctx.me?.can_admin === true ? api('/api/needs-me', options) : Promise.resolve(ctx.needs || { count: 0 }),
      api('/api/bots', options),
      api(`/api/events?after=${seq}${eventEpoch ? `&epoch=${enc(eventEpoch)}` : ''}`, options),
    ]);
    if (!alive()) return;
    const [queue, roster, feed] = results;
    if (queue.status === 'fulfilled') {
      needs = queue.value || { count: 0 };
      for (const card of needsCards(needs)) if (settled.has(stamp(card)) || exiting.has(stamp(card))) needs = removeNeed(needs, card);
      loaded = true;
    }
    if (roster.status === 'fulfilled') bots = roster.value?.bots || [];
    else if ([401, 403, 404].includes(roster.reason?.status)) bots = [];
    if (feed.status === 'fulfilled') {
      if (feed.value.reset) events = [];
      events = [...new Map([...events, ...(feed.value.events || [])].map(event => [event.seq || event.id, event])).values()].sort((a, b) => String(b.at).localeCompare(String(a.at))).slice(0, 12).map(event => ({ ...event, summary: activityText(event, bots, needs) }));
      seq = feed.value.seq || 0; eventEpoch = feed.value.epoch || '';
    } else if ([401, 403].includes(feed.reason?.status)) events = [];
    region('error').hidden = queue.status === 'fulfilled' && roster.status === 'fulfilled';
    region('error').textContent = queue.status === 'rejected' ? 'Today could not be refreshed. Try again.' : roster.status === 'rejected' ? 'Your bots could not be refreshed.' : '';
    if (queue.status === 'rejected' && [401, 403, 404].includes(queue.reason?.status)) { needs = { count: 0 }; dialog.close(); current = null; }
    paint();
  }
  function openReply(card, action = 'reply') {
    if (action === 'reply' && !(card.actions || []).some(action => ['answer', 'accept'].includes(action))) { ctx.go?.(`#/card/${card.number}`); return; }
    current = card; currentAction = action;
    dialog.querySelector('h2').textContent = action === 'approve' ? 'Reason for approval' : 'Reply';
    form.querySelector('[type="submit"]').textContent = action === 'approve' ? 'Approve' : 'Send reply';
    region('reply-title').textContent = displayText(card.title);
    const choices = card.ask_options || [];
    form.elements.choice.innerHTML = '<option value="">Reply in your own words</option>' + choices.map((choice, i) => `<option value="${i}">${e(displayText(choice))}</option>`).join('');
    region('choice-label').hidden = action !== 'reply' || !choices.length;
    form.elements.reply.value = ''; region('reply-error').textContent = '';
    dialog.showModal(); form.elements.reply.focus();
  }
  async function act(card, action, extra = {}) {
    if (!active || busy.has(card.number) || settled.has(stamp(card)) || exiting.has(stamp(card))) return;
    if (action === 'approve' && card.approval_kind?.startsWith('origin') && !extra.submit) { openReply(card, action); return; }
    if (action === 'reply' && !extra.submit) { openReply(card); return; }
    if (action === 'approve' && card.approval_kind?.startsWith('origin') && !extra.reason?.trim()) { region('reply-error').textContent = 'Write a reason for this approval.'; return; }
    const signature = JSON.stringify([stamp(card), action, extra.reply, extra.choice, extra.reason]);
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
      activity.unshift({ at, text: `${action === 'approve' ? 'You approved' : action === 'reject' ? 'You turned down' : action === 'hold' ? 'You held' : 'You replied to'} ${displayText(card.title)}`, fresh: true });
      leaving.set(stamp(card), { card, index: Math.max(0, cards().findIndex(item => item.number === card.number)) });
      needs = removeNeed(needs, card); ctx.onNeedsChanged?.(card);
      exiting.add(stamp(card)); paint();
      const timer = setTimeout(() => {
        timers.delete(timer); if (!active) return;
        settled.add(stamp(card)); exiting.delete(stamp(card)); leaving.delete(stamp(card)); paint();
        const remaining = cards(); if (remaining.length) focus(remaining[0].number);
      }, 280); timers.add(timer);
    } catch (error) {
      if (!active) return;
      const text = error.status === 409 ? 'This card changed. Review its latest version.' : error.message || 'Could not send. Try again.';
      errors.set(card.number, text); region('reply-error').textContent = text;
      if ([403, 404, 409].includes(error.status)) { dialog.close(); current = null; await refresh(); }
    } finally { writes.delete(controller); busy.delete(card.number); paint(); }
  }
  function onClick(event) {
    if (event.target.closest?.('[data-bt-cancel]')) { dialog.close(); current = null; return; }
    const button = event.target.closest?.('[data-bt-action]'), article = button?.closest('[data-bt-card]');
    const card = cards().find(card => card.number === Number(article?.dataset.btCard));
    if (card && button && !button.disabled) { event.preventDefault(); focused = card.number; void act(card, button.dataset.btAction); }
  }
  function onKey(event) {
    if (dialog.open || doc?.querySelector?.('dialog[open]')) return;
    const action = todayKey(event, ctx.singleKeys?.() !== false), list = cards().filter(card => !exiting.has(stamp(card)));
    if (!action || !list.length) return;
    event.preventDefault(); event.stopPropagation?.();
    const index = list.findIndex(card => card.number === focused);
    if (['next', 'previous'].includes(action)) focus(list[index < 0 ? action === 'next' ? 0 : list.length - 1 : (index + (action === 'next' ? 1 : list.length - 1)) % list.length].number);
    else void act(list[Math.max(0, index)], action);
  }
  function onSubmit(event) {
    event.preventDefault(); if (!current) return;
    if (currentAction === 'approve') { void act(current, 'approve', { submit: true, reason: form.elements.reply.value }); return; }
    const choice = current.ask_options?.[Number(form.elements.choice.value)];
    void act(current, 'reply', { submit: true, reply: form.elements.reply.value, ...(form.elements.choice.value !== '' ? { choice } : {}) });
  }
  root.addEventListener('click', onClick); form.addEventListener('submit', onSubmit); doc?.addEventListener('keydown', onKey, true);
  paint(); void refresh();
  return { refresh, stop() {
    active = false; generation++; read?.abort(); writes.forEach(controller => controller.abort()); timers.forEach(clearTimeout);
    dialog.close(); root.removeEventListener('click', onClick); form.removeEventListener('submit', onSubmit); doc?.removeEventListener('keydown', onKey, true); host.classList.remove('deck-today-capy');
  } };
}
