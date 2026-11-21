import { escape as e, actor } from './api.js';

export const PRIORITIES = ['urgent', 'high', 'normal', 'low'];
export const priorityLevel = card => PRIORITIES.includes(card.priority) ? card.priority : 'normal';
const label = value => value[0].toUpperCase() + value.slice(1);
const source = card => ['person','coworker'].includes(card.priority_source) ? card.priority_source : 'default';
const anchoredHints = globalThis.CSS?.supports?.('anchor-scope: --card-priority') && globalThis.HTMLElement?.prototype.showPopover;

export function priorityInfo(card, { board = {}, me, people = {} } = {}) {
  if (source(card) === 'default') return 'Unranked · Normal by default. Nobody has chosen a priority yet.';
  const prefix = `Priority set to ${priorityLevel(card)}`;
  const events = card.events || [];
  const event = events.findLast(ev => ['edit','move'].includes(ev.kind) &&
    (source(card) === 'coworker' ? ev.text?.startsWith(`${prefix} by the Coworker: `) : ev.text === prefix))
    || (source(card) === 'person' ? events.find(ev => ev.kind === 'add') : null);
  const who = source(card) === 'coworker' ? `${board.name || board.slug || 'Project'} Coworker`
    : event?.actor === me ? 'You' : people[event?.actor] || (event?.actor ? actor(event.actor) : 'Person');
  const reason = source(card) === 'coworker' ? event?.text.slice(`${prefix} by the Coworker: `.length) : '';
  return `${label(priorityLevel(card))} · Set by ${who}.${reason ? ` ${reason}` : source(card) === 'person' ? ' A person’s choice takes precedence over the Coworker.' : ' Open the card for its recorded reason.'}`;
}

export function priorityBadge(card, options) {
  const id = `priority-info-${card.number}`, by = source(card);
  const text = `${label(priorityLevel(card))} · ${by === 'default' ? 'Unranked' : label(by)}`;
  const badge = card.can_prioritize === true
    ? `<button type="button" class="priority-label" id="priority-${e(card.number)}" data-action="card-menu" data-card="${e(card.number)}" aria-haspopup="dialog" aria-label="Change priority: ${e(text)}" aria-describedby="${id}">${text}</button>`
    : `<span class="priority-label" tabindex="0" aria-describedby="${id}">${text}</span>`;
  return `<span class="card-priority"${priorityLevel(card) === 'normal' ? '' : ` data-priority="${priorityLevel(card)}"`} data-priority-source="${by}" data-priority-card="${e(card.number)}" data-priority-version="${e(card.version)}">${badge}<span class="priority-info" id="${id}" role="tooltip"${anchoredHints ? ' popover="manual"' : ''}>${e(priorityInfo(card, options))}</span></span>`;
}

export const priorityDetails = (card, options) => `<p class="priority-attribution" data-priority-info>${e(priorityInfo(card, options))}</p>`;

export function priorityChoices(card, options) {
  const details = priorityDetails(card, options);
  if (card.can_prioritize !== true) return `<section aria-label="Priority"><p class="move-label">Priority</p>${details}<p class="priority-attribution">Only the workspace owner can change priority.</p></section>`;
  return `<section aria-label="Priority"><p class="move-label">Priority</p>${details}<div role="group" aria-label="Set priority">${PRIORITIES.map(value => `<button type="button" class="opt" data-menu-priority="${value}"${source(card) === 'person' && priorityLevel(card) === value ? ' aria-current="true"' : ''}>${label(value)}${priorityLevel(card) === value ? '<span class="sub">Current level</span>' : ''}</button>`).join('')}</div></section>`;
}

export function priorityFilter(value = 'all') {
  return `<label class="priority-filter">Priority<select data-priority-filter aria-label="Filter by priority">${[['all','All priorities'],['unranked','Unranked'],...PRIORITIES.map(p => [p,label(p)])].map(([key,text]) => `<option value="${key}"${key === value ? ' selected' : ''}>${text}</option>`).join('')}</select></label>`;
}
export const matchesPriority = (card, value) => value === 'unranked' ? source(card) === 'default' : !PRIORITIES.includes(value) || priorityLevel(card) === value;

export const putPriority = (api, card, priority) => api(`/api/cards/${card.number}`, { method:'PATCH', body:{ version:card.version, priority } });
export function priorityUndo(api, previous, saved) {
  const { number, version } = saved, priority = priorityLevel(previous);
  return () => putPriority(api, { number, version }, priority);
}
export function priorityFailure(error, undo = false) {
  const action = undo ? 'Could not undo priority' : 'Priority not changed';
  const reason = error?.status === 409 ? 'This card changed. Reopen its actions to see the latest priority.'
    : error?.status === 403 ? 'Only the workspace owner can change priority here.'
    : error?.status === 401 ? 'Sign in again before changing priority.'
    : error?.status ? `The server could not save it (error ${error.status}). Try again.` : 'The server did not answer. Check your connection and try again.';
  return `${action}. ${reason} ${undo ? 'The current priority is unchanged.' : 'The previous priority is unchanged.'}`;
}

// List cards omit history. Read it only for a hovered/focused badge, once per card version.
export function bindPriorityHints(root, { api, board, me }) {
  if (!root?.querySelectorAll) return () => {};
  const reads = new Map();
  let stopped = false;
  const reveal = async event => {
    const badge = event.target.closest?.('[data-priority-card]');
    if (!badge) return;
    const info = badge.querySelector('[role="tooltip"]');
    if (info.hasAttribute('popover') && !info.matches(':popover-open')) info.showPopover();
    if (badge.dataset.prioritySource === 'default') return;
    const key = `${badge.dataset.priorityCard}:${badge.dataset.priorityVersion}`;
    if (!reads.has(key)) reads.set(key, api(`/api/cards/${badge.dataset.priorityCard}`));
    try {
      const card = await reads.get(key);
      if (stopped || !info.isConnected) return;
      info.textContent = String(card.version) === badge.dataset.priorityVersion
        ? priorityInfo(card, { board, me }) : 'This priority changed. Open the card to see its latest details.';
    } catch {
      if (!stopped && info.isConnected) info.textContent = 'Could not load who set this priority. Open the card to retry.';
    }
  };
  const hide = event => {
    const badge = event.target.closest?.('[data-priority-card]');
    if (!badge || badge.contains(event.relatedTarget) || badge.contains(root.ownerDocument.activeElement) || badge.matches(':hover')) return;
    const info = badge.querySelector('[role="tooltip"]');
    if (info.hasAttribute('popover') && info.matches(':popover-open')) info.hidePopover();
  };
  const dismiss = event => {
    if (event.key === 'Escape' && anchoredHints) root.querySelectorAll('.priority-info:popover-open').forEach(info => info.hidePopover());
  };
  root.addEventListener('pointerover', reveal); root.addEventListener('focusin', reveal);
  root.addEventListener('pointerout', hide); root.addEventListener('focusout', hide);
  root.addEventListener('keydown', dismiss);
  return () => {
    stopped = true;
    if (anchoredHints) root.querySelectorAll('.priority-info:popover-open').forEach(info => info.hidePopover());
    root.removeEventListener('pointerover', reveal); root.removeEventListener('focusin', reveal);
    root.removeEventListener('pointerout', hide); root.removeEventListener('focusout', hide);
    root.removeEventListener('keydown', dismiss);
  };
}

export function showPriorityUndo({ mount, card, change, onChange, valid = () => true }) {
  if (!mount) return;
  mount.hidden = false;
  mount.innerHTML = `<div class="priority-toast" role="status"><p>${label(priorityLevel(card))} priority saved. Undo restores ${label(priorityLevel(change.previous))} as your choice.</p><button class="btn" data-priority-undo>Undo</button><button class="btn ghost" data-priority-dismiss>Dismiss</button><p data-priority-error role="alert"></p></div>`;
  const box = mount.querySelector('.priority-toast'), undo = box.querySelector('[data-priority-undo]');
  const dismiss = () => { box.remove(); if (!mount.children.length) mount.hidden = true; };
  box.querySelector('[data-priority-dismiss]').addEventListener('click', dismiss);
  undo.addEventListener('click', async () => {
    if (undo.disabled || !valid()) return;
    undo.disabled = true;
    try {
      await change.undo();
    } catch (error) {
      if (!valid() || !box.isConnected) return;
      box.querySelector('[data-priority-error]').textContent = priorityFailure(error, true);
      // A rejected version is never retried with a newer version.
      undo.disabled = error.status === 409 || error.status === 403 || error.status === 401;
      await onChange().catch(() => {});
      return;
    }
    if (!valid() || !box.isConnected) return;
    box.querySelector('p').textContent = `${label(priorityLevel(change.previous))} priority restored as your choice.`;
    undo.remove();
    await onChange().catch(() => {});
  });
}

const styles = `
.card-priority { anchor-scope:--card-priority; position:relative; display:block; width:fit-content; max-width:100%; color:var(--text-2); font-size:12px; line-height:18px; }
.priority-label { anchor-name:--card-priority; display:inline-block; border:0; background:none; color:inherit; font:inherit; text-align:left; border-radius:4px; padding:2px 0; }
.priority-label:focus-visible,.priority-filter select:focus-visible,.priority-toast button:focus-visible { outline:2px solid var(--accent); outline-offset:3px; }
.card-priority[data-priority="urgent"] .priority-label { color:var(--block); font-weight:650; }
.priority-info { display:none; position:absolute; inset-block-start:100%; inset-inline-start:0; z-index:30; width:min(260px,calc(100vw - 64px)); padding:10px 12px; border:1px solid var(--line-strong); border-radius:6px; background:var(--surface); color:var(--text); white-space:normal; overflow-wrap:anywhere; box-shadow:0 4px 16px rgba(0,0,0,.16); }
.card-priority:hover .priority-info:not([popover]),.card-priority:focus-within .priority-info:not([popover]),.priority-info:popover-open { display:block; }
.priority-info[popover] { position:fixed; position-anchor:--card-priority; inset:auto; top:anchor(bottom); left:anchor(left); margin:6px 0; position-try-fallbacks:flip-block,flip-inline; }
.priority-attribution { font-size:13px; line-height:20px; margin:6px 0; overflow-wrap:anywhere; }
.priority-filter { display:flex; align-items:center; flex:none; gap:8px; font-size:13px; color:var(--text-2); }
.priority-filter select { min-height:32px; max-width:100%; border:1px solid var(--line-strong); border-radius:6px; padding:4px 8px; font:inherit; color:var(--text); background:var(--surface); }
.priority-toast { display:flex; align-items:center; flex-wrap:wrap; gap:8px; padding:12px; border:1px solid var(--line); background:var(--surface); color:var(--text); border-radius:6px; }
.priority-toast p { margin:0; overflow-wrap:anywhere; }
.priority-toast [role="alert"] { flex-basis:100%; }
.priority-toast [role="alert"]:empty { display:none; }
:root[data-ui="deck"] .card-priority { color:var(--ink2); }
:root[data-ui="deck"] .card-priority[data-priority="urgent"] .priority-label { color:var(--amber); }
:root[data-ui="deck"] .priority-filter,:root[data-ui="deck"] .priority-attribution { color:var(--ink2); }
:root[data-ui="deck"] .priority-info,:root[data-ui="deck"] .priority-toast,:root[data-ui="deck"] .priority-filter select { background:var(--s1-solid); color:var(--ink); border-color:var(--line2); }
:root[data-ui="deck"] .card-menu { background:var(--s1-solid); color:var(--ink); }
:root[data-ui="deck"] .deck-board-heading .board-tools { flex-wrap:wrap; justify-content:flex-end; }
:root[data-ui="deck"] .deck-board-filters { display:flex; align-items:center; gap:8px; }
@media(max-width:699px) {
  .priority-filter select,.priority-label,.priority-toast button { min-height:44px; }
  .priority-label { display:inline-flex; align-items:center; }
  .priority-info[popover] { top:anchor(top); translate:0 calc(-100% - 6px); }
  .priority-info:not([popover]) { inset-block-start:auto; inset-block-end:100%; }
  .deck-board-phone-tools .priority-filter { margin-top:8px; }
}
`;
if (globalThis.document?.adoptedStyleSheets) {
  const sheet = new CSSStyleSheet(); sheet.replaceSync(styles);
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
}
