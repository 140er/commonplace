// Deck Board presentation. Card mutations, drag/drop and drawer lifecycle stay in the shell.
import { escape as e, actor } from './api.js';

export const needsOwner = (card, me) => card.status !== 'done' && (card.need === 'you' ||
  (card.awaiting_release === true && card.can_approve === true) ||
  (card.status === 'review' && !!card.ask && !!me && card.ask_to === me && card.review_kind !== 'completion'));
export function stateWord(card, me) {
  if (card.status === 'done') return 'Live';
  if (needsOwner(card, me)) return card.ask ? 'Needs a decision' : 'Needs you';
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
const group = (card, me) => needsOwner(card, me) ? 'todo' : card.status === 'done' ? 'done' : card.status === 'review' ? 'review' : 'doing';
const columns = [['todo', 'Needs me', 'waiting'], ['doing', 'Active', 'busy'], ['review', 'In review', 'idle'], ['done', 'Shipped', 'live']];
function cardHTML(card, options, first) {
  const selected = options.selected === card.number, name = botName(card, options);
  return `<article class="card${needsOwner(card, options.me) ? ' is-you' : ''}${selected ? ' is-selected' : ''}" data-card="${e(card.number)}" data-key="${e(card.number)}" draggable="true" tabindex="${first || selected ? 0 : -1}" aria-label="${e(boardCopy(card.title))}"${selected ? ' aria-current="true"' : ''}><div class="card-top"></div><h3><a href="#/card/${e(card.number)}" tabindex="-1">${e(boardCopy(card.title))}</a></h3><div class="card-meta">${avatar(name)}<span class="deck-board-bot">${e(name)}</span><span aria-hidden="true">·</span><span data-card-state>${e(stateWord(card, options.me))}</span></div></article>`;
}
export function deckBoardView({ cards = [], filter = 'all', form = '', sheet = true, ...options }) {
  const backlog = cards.filter(card => card.status === 'backlog');
  const columnHTML = columns.map(([status, name, tone]) => {
    const list = cards.filter(card => card.status !== 'backlog' && group(card, options.me) === status);
    return `<section class="col" id="col-${status}" data-status="${status}" data-active="true" aria-label="${name}"><div class="col-h">${dot(status === 'todo' && !list.length ? 'idle' : tone)}<h2>${name}</h2>${status === 'todo' && list.length ? `<span class="n">${list.length}</span>` : ''}</div><div class="cards">${status === 'todo' && !sheet ? form : ''}${list.map((card, i) => cardHTML(card, options, i === 0)).join('') || '<p class="empty-col">Nothing here yet</p>'}</div></section>`;
  }).join('');
  return `<div class="workbench-board deck-board"><p class="deck-board-intro">Every card follows its change from idea to live.</p><div class="board-tools"><div class="seg" role="group" aria-label="Filter cards">${[['all','All'],['mine','Mine'],['agents','Agents'],['blocked','Blocked']].map(([key,label]) => `<button data-board-filter="${key}" aria-pressed="${filter === key}">${label}</button>`).join('')}</div></div><div class="board" role="region" aria-label="Board" tabindex="0">${columnHTML}</div>${backlog.length ? `<details class="backlog"><summary>Backlog</summary><div class="cards">${backlog.map((card,i) => cardHTML(card, options, i === 0)).join('')}</div></details>` : ''}${sheet ? form : ''}</div>`;
}
const stepWords = {claim:['Work','Started'],pr:['Change','Pushed'],approve:['Review','Approved'],done:['Change','Finished'],handoff:['Handoff','Sent']};
export function drawerSummary(card, options = {}) {
  const name = botName(card, options), bot = cardBot(card, options);
  const coworker = card.coworker?.name || options.people?.[card.assignee] || (card.assignee ? actor(card.assignee) : '—');
  const version = card.callstack?.head || card.head_sha || card.version;
  const steps = (card.events || []).filter(ev => stepWords[ev.kind]).map(ev => stepWords[ev.kind]);
  if (card.checks) steps.push(['Checks', {pass:'Passed',passed:'Passed',success:'Passed',pending:'Running',fail:'Failed',failure:'Failed'}[card.checks] || 'Not reported']);
  return `<section class="deck-card-summary" aria-label="Summary"><h3 class="sr">Summary</h3><p>${e(boardCopy(card.summary || card.body)) || 'No summary recorded.'}</p></section><dl class="deck-card-facts"><dt>Bot</dt><dd>${avatar(name)}${bot ? `<a href="#/bots/${e(encodeURIComponent(bot.id))}">${e(name)}</a>` : e(name)}</dd><dt>Coworker</dt><dd>${e(boardCopy(coworker))}</dd>${version != null ? `<dt>Version</dt><dd><code>${e(version)}</code></dd>` : ''}</dl><section class="deck-card-progress" aria-label="Progress"><h3>Progress</h3><ol><li class="current">${dot(needsOwner(card, options.me) ? 'waiting' : card.status === 'done' ? 'live' : card.status === 'doing' ? 'busy' : 'idle')}<span>Change</span><span>${e(stateWord(card, options.me))}</span></li>${steps.map(([label,state]) => `<li><span>${label}</span><span>${state}</span></li>`).join('')}</ol></section>`;
}
export function drawerActions(card, me) {
  if (!needsOwner(card, me)) return '';
  const choices = card.ask_to === me ? card.ask_options || [] : [];
  const affirmative = choices.find(choice => /^(approve|accept|yes|ship|go ahead)(?:\b|$)/i.test(choice));
  const hold = choices.find(choice => /^(hold|wait|later)(?:\b|$)/i.test(choice));
  const decision = (choice, label, primary = false) => `<button class="btn${primary ? ' primary' : ''}" data-action="decide" data-card="${e(card.number)}" data-choice="${e(choice)}">${label}</button>`;
  const approve = card.awaiting_release && card.can_approve ? '<button class="btn primary" data-deck-release>Approve</button>' : affirmative ? decision(affirmative,'Approve',true) : (card.actions || []).includes('approve') || card.proposal_status === 'pending' ? `<button class="btn primary" data-action="need-approve" data-card="${e(card.number)}" data-version="${e(card.version)}">Approve</button>` : '';
  // Without a server-provided Hold choice, leave this decision waiting and close the sheet.
  return `<div class="deck-card-actions" data-version="${e(card.version)}">${approve}${hold ? decision(hold,'Hold') : '<button class="btn" data-action="close-panel" title="Keep this waiting for your decision">Hold</button>'}</div>`;
}
// Clean only text nodes. Links, IDs, exact decision values and mutation versions remain untouched.
export const drawerCopy = (html, id = '') => html.replace(/(^|>)([^<]+)(?=<|$)/g, (_all, prefix, text) => /^\s*$/.test(text) ? prefix + text : prefix + boardCopy(id ? text.replaceAll(e(id), 'this card') : text));
