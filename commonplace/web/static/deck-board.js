// Deck Board presentation. Card mutations, drag/drop and drawer lifecycle stay in the shell.
import { escape as e, actor } from './api.js';

export const needsOwner = (card, me) => card.status !== 'done' && (card.need === 'you' ||
  (card.awaiting_release === true && card.can_approve === true) ||
  (card.status === 'review' && !!card.ask && !!me && card.ask_to === me && card.review_kind !== 'completion'));
export function stateWord(card, me) {
  if (card.status === 'done') return 'Live';
  if (needsOwner(card, me)) return 'Needs you';
  if (card.need === 'checks' || card.checks === 'pending') return 'Checking';
  if (card.status === 'review') return 'In review';
  if (card.status === 'doing' || card.need === 'working') return 'Building';
  return card.snoozed ? 'On hold' : 'Ready';
}
// Display copy only: stored text and action values are never changed.
export function boardCopy(value) {
  return String(value ?? '')
    .replace(/\bEvidence\s*:[^\n]*/gi, '')
    .replace(/(?:~\/|\/(?:home|Users|tmp|var|work)\/|[A-Z]:\\)[^\s<>]*/g, '')
    .replace(/(?:\bPR\s*)?(?<!&)#\d+\b/gi, '')
    .replace(/\b(?:Sonnet|Opus|Haiku|GPT|Claude)[ -]?\d+(?:\.\d+)*(?:[- ](?:high|low|medium))?\b/gi, '')
    .replace(/[ \t]{2,}/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
}
export function cardBot(card, { board = {}, bots = [] } = {}) {
  return bots.find(bot => bot.id === card.bot_id) || bots.find(bot => bot.board === (card.board || board.slug)) || null;
}
const botName = (card, options) => boardCopy(cardBot(card, options)?.name || card.bot?.name || '') || 'No bot assigned';
const avatar = name => `<span class="deck-board-avatar" aria-hidden="true">${e(Array.from(name)[0] || '?')}</span>`;
const dot = tone => `<i class="deck-board-dot ${tone}" aria-hidden="true"></i>`;
export const boardGroup = (card, me) => needsOwner(card, me) ? 'todo' : card.status === 'done' ? 'done' : card.status === 'review' ? 'review' : 'doing';
const columns = [['todo', 'Needs you', 'waiting'], ['doing', 'Active', 'busy'], ['review', 'In review', 'idle'], ['done', 'Shipped', 'live']];
function cardHTML(card, options, first) {
  const selected = options.selected === card.number, name = botName(card, options);
  return `<article class="card${needsOwner(card, options.me) ? ' is-you' : ''}${selected ? ' is-selected' : ''}" data-card="${e(card.number)}" data-key="${e(card.number)}" draggable="true" tabindex="${first || selected ? 0 : -1}" aria-label="${e(boardCopy(card.title))}"${selected ? ' aria-current="true"' : ''}><div class="card-top"></div><h3><a href="#/card/${e(card.number)}" tabindex="-1">${e(boardCopy(card.title))}</a></h3><div class="card-meta">${avatar(name)}<span class="deck-board-bot">${e(name)}</span><span aria-hidden="true">·</span><span data-card-state>${e(stateWord(card, options.me))}</span></div></article>`;
}
export function deckBoardView({ cards = [], filter = 'all', form = '', sheet = true, column = 'todo', ...options }) {
  const backlog = cards.filter(card => card.status === 'backlog' && !needsOwner(card, options.me));
  const count = status => cards.filter(card => (card.status !== 'backlog' || needsOwner(card, options.me)) && boardGroup(card, options.me) === status).length;
  const columnHTML = columns.map(([status, name, tone]) => {
    const list = cards.filter(card => (!filter.startsWith('state:') || filter === `state:${status}`) && (card.status !== 'backlog' || needsOwner(card, options.me)) && boardGroup(card, options.me) === status);
    return `<section class="col" id="col-${status}" data-status="${status}" data-active="${column === status}" aria-label="${name}"><div class="col-h">${dot(status === 'todo' && !list.length ? 'idle' : tone)}<h2>${name}</h2><span class="n">${count(status)}</span></div><div class="cards">${status === 'todo' && !sheet ? form : ''}${list.map((card, i) => cardHTML(card, options, i === 0)).join('') || '<p class="empty-col">Nothing here yet</p>'}</div></section>`;
  }).join('');
  const stateFilters = [['all','All'], ...columns.map(([status,name]) => [`state:${status}`,name])];
  const add = '<button class="btn deck-board-add" data-action="new-card">Add card</button>';
  const menu = `<details class="deck-board-menu"><summary aria-label="Board options">…</summary><div>${[['mine','Mine'],['agents','Agents'],['blocked','Blocked']].map(([key,label]) => `<button data-board-filter="${key}" aria-pressed="${filter === key}">${label}</button>`).join('')}<button data-action="coordinator" data-board="${e(options.board?.slug)}">Settings</button><a href="#/board/${e(options.board?.slug)}/chat">New thread</a></div></details>`;
  return `<div class="workbench-board deck-board"><header class="deck-board-heading"><div class="deck-board-title"><div class="deck-board-title-row"><h1>Board</h1><div class="deck-board-phone-actions">${add}${menu}</div></div><p class="deck-board-intro">Every card follows its change from idea to live.</p></div><div class="board-tools"><div class="seg deck-board-filters" role="group" aria-label="Filter cards">${stateFilters.map(([key,label]) => `<button data-board-filter="${key}" aria-pressed="${filter === key}">${label} <span>${key === 'all' ? columns.reduce((sum,[status]) => sum + count(status),0) : count(key.slice(6))}</span></button>`).join('')}</div>${add}${menu}</div></header><div class="deck-board-phone-tools"><nav class="deck-board-states" aria-label="Board states">${columns.map(([status,name]) => `<button data-board-column="${status}" aria-controls="col-${status}" aria-pressed="${column === status}">${name} <span>${count(status)}</span></button>`).join('')}</nav></div><div class="board" role="region" aria-label="Board" tabindex="0">${columnHTML}</div>${backlog.length ? `<details class="backlog"><summary>Backlog</summary><div class="cards">${backlog.map((card,i) => cardHTML(card, options, i === 0)).join('')}</div></details>` : ''}${sheet ? form : ''}</div>`;
}
const stepWords = {claim:['Work','Started'],pr:['Builder','Pushed'],approve:['Review gate','Approved'],done:['Work','Finished'],handoff:['Handoff','Sent']};
export function drawerSummary(card, options = {}) {
  const name = botName(card, options), bot = cardBot(card, options);
  const coworker = card.coworker?.name || options.people?.[card.assignee] || (card.assignee ? actor(card.assignee) : '—');
  const version = card.callstack?.head || card.head_sha || card.version;
  const steps = (card.events || []).filter(ev => stepWords[ev.kind]).map(ev => stepWords[ev.kind]);
  if (card.checks) steps.push(['Checks', {pass:'Passed',passed:'Passed',success:'Passed',pending:'Running',fail:'Failed',failure:'Failed'}[card.checks] || 'Not reported']);
  const stage = typeof card.stage === 'string' ? boardCopy(card.stage) : card.awaiting_release ? 'Release' : 'Change';
  const stateTone = state => /^(Pushed|Approved|Passed|Finished)$/.test(state) ? 'live' : state === 'Needs you' ? 'waiting' : 'idle';
  return `<section class="deck-card-summary" aria-label="Summary"><h3 class="sr">Summary</h3><p>${e(boardCopy(card.summary || card.body)) || 'No summary recorded.'}</p></section><dl class="deck-card-facts"><dt>Bot</dt><dd>${avatar(name)}${bot ? `<a href="#/bots/${e(encodeURIComponent(bot.id))}">${e(name)}</a>` : e(name)}</dd><dt>Coworker</dt><dd>${e(boardCopy(coworker))}</dd>${version != null ? `<dt>Version</dt><dd><code>${e(version)}</code></dd>` : ''}</dl><section class="deck-card-progress" aria-label="Progress"><h3>Progress</h3><ol><li class="current"><span>${e(stage)}</span><span class="${stateTone(stateWord(card, options.me))}">${e(stateWord(card, options.me))}</span></li>${steps.map(([label,state]) => `<li class="child"><span>${label}</span><span class="${stateTone(state)}">${state}</span></li>`).join('')}</ol></section>`;
}
export function drawerActions(card, me) {
  const reply = '<button class="btn ghost" data-deck-reply-toggle aria-expanded="false" aria-controls="deck-card-reply">Reply</button>';
  const move = `<button class="btn" data-action="card-menu" data-card="${e(card.number)}" aria-haspopup="dialog">Move to…</button>`;
  if (!needsOwner(card, me)) return `<div class="deck-card-actions">${move}${reply}</div>`;
  const choices = card.ask_to === me ? card.ask_options || [] : [];
  const affirmative = choices.find(choice => /^(approve|accept|yes|ship|go ahead)(?:\b|$)/i.test(choice));
  const hold = choices.find(choice => /^(hold|wait|later)(?:\b|$)/i.test(choice));
  const decision = (choice, label, primary = false) => `<button class="btn${primary ? ' primary' : ''}" data-action="decide" data-card="${e(card.number)}" data-choice="${e(choice)}">${label}</button>`;
  const approve = card.awaiting_release && card.can_approve ? '<button class="btn primary" data-deck-release>Approve</button>' : affirmative ? decision(affirmative,'Approve',true) : !card.awaiting_release && ((card.actions || []).includes('approve') || card.proposal_status === 'pending') ? `<button class="btn primary" data-action="need-approve" data-card="${e(card.number)}" data-version="${e(card.version)}">Approve</button>` : '';
  // Without a server-provided Hold choice, leave this decision waiting and close the sheet.
  return `<div class="deck-card-actions" data-version="${e(card.version)}">${approve}${hold ? decision(hold,'Hold') : '<button class="btn" data-action="close-panel" title="Keep this waiting for your decision">Hold</button>'}${move}${reply}</div>`;
}
// Clean only text nodes. Links, IDs, exact decision values and mutation versions remain untouched.
export const drawerCopy = (html, id = '') => html.replace(/(^|>)([^<]+)(?=<|$)/g, (_all, prefix, text) => /^\s*$/.test(text) ? prefix + text : prefix + boardCopy(id ? text.replaceAll(e(id), 'this card') : text).replace(/Needs me|Needs a decision/g, 'Needs you'));

// Keep the phone state pills aligned with swipes and board refreshes.
export function bindDeckBoard(root) {
  const noop = () => {};
  if (typeof root?.querySelector !== 'function' || typeof root?.querySelectorAll !== 'function') return noop;
  const scroller = root.querySelector('.deck-board .board');
  if (!scroller || ['querySelector', 'querySelectorAll', 'getBoundingClientRect', 'addEventListener', 'removeEventListener'].some(method => typeof scroller[method] !== 'function')) return noop;
  const syncColumn = () => {
    if (!scroller || scroller.scrollWidth <= scroller.clientWidth) return;
    const columns = [...scroller.querySelectorAll('.col')], left = scroller.getBoundingClientRect().left;
    const closest = columns.reduce((best, col) => Math.abs(col.getBoundingClientRect().left - left) < Math.abs(best.getBoundingClientRect().left - left) ? col : best, columns[0]);
    if (!closest) return;
    columns.forEach(col => { col.dataset.active = String(col === closest); });
    root.querySelectorAll('[data-board-column]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.boardColumn === closest.dataset.status)));
  };
  scroller.addEventListener('scrollend', syncColumn);
  const activeColumn = scroller.querySelector('[data-active="true"]'), firstColumn = scroller.querySelector('.col');
  if (activeColumn && firstColumn && scroller.scrollWidth > scroller.clientWidth) scroller.scrollLeft = activeColumn.offsetLeft - firstColumn.offsetLeft;
  return () => scroller.removeEventListener('scrollend', syncColumn);
}
