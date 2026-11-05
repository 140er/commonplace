import { escape as e, actor, elapsed } from './api.js';

const columns = ['todo', 'doing', 'review', 'done'];
const heading = (s) => s[0].toUpperCase() + s.slice(1);
export function cardId(board, number) {
  const letter = (value) => String(value || '').normalize('NFKD').toUpperCase().match(/[A-Z]/)?.[0];
  return `${letter(board.name) || letter(board.slug) || 'C'}-${number}`;
}
export function isBlocked(card, me) {
  return card.status === 'review' && Boolean(card.ask) && card.review_kind !== 'completion' && card.ask_to !== me;
}
function author(ref, board) {
  if (ref && ref === board.coordinator) return `${board.name} orchestrator`;
  return `${actor(ref)}${String(ref).startsWith('conn:') ? ' (agent)' : ''}`;
}
export function cardTags(card, { me, board = {}, messages = [] } = {}) {
  const tags = [];
  if (isBlocked(card, me)) tags.push('Blocked');
  else if (card.need === 'you' || (card.status === 'review' && card.ask && card.ask_to === me && card.review_kind !== 'completion')) tags.push('Needs me');
  if (card.message_id) {
    const at = messages.find((m) => m.id === card.message_id)?.at || card.created_at;
    const date = new Date(at);
    tags.push(`From chat${Number.isFinite(date.getTime()) ? ` · ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}` : ''}`);
  }
  if (card.need === 'working') tags.push(`Working on it: ${author(card.assignee, board)}`, `working ${elapsed(card.heartbeat_at || card.updated_at)}`);
  else if (card.status === 'todo' && !card.assignee) tags.push('Available for an agent');
  if (card.status === 'review' && String(card.return_to).startsWith('conn:')) tags.push(`Built by ${author(card.return_to, board)}`);
  if (card.status === 'review' && card.review_kind === 'completion' && card.ask_to) tags.push(`Reviewer: ${author(card.ask_to, board)}`);
  return tags;
}
export function filterCards(cards, filter, me) {
  return cards.filter((c) => filter === 'mine' ? c.assignee === me : filter === 'agents' ? String(c.assignee).startsWith('conn:') : filter === 'blocked' ? isBlocked(c, me) : true);
}
function cardView(card, options, first) {
  const id = cardId(options.board, card.number), tags = cardTags(card, options);
  const blocked = isBlocked(card, options.me), selected = options.selected === card.number;
  return `<article class="card${blocked ? ' is-blocked' : ''}${tags.includes('Needs me') ? ' is-you' : ''}${selected ? ' is-selected' : ''}" data-card="${card.number}" data-key="${card.number}" tabindex="${first ? 0 : -1}" aria-label="${e(id + ' ' + card.title)}"${selected ? ' aria-current="true"' : ''}><div class="card-top"><span class="cid">${e(id)}</span>${(card.labels || []).map((l) => `<span class="tag lbl">${e(l)}</span>`).join('')}</div><h3><a href="#/card/${card.number}" tabindex="-1">${e(card.title)}</a></h3><div class="card-meta">${tags.map((t) => `<span class="tag ${t === 'Blocked' ? 'block' : t === 'Needs me' ? 'you' : ''}">${e(t)}</span>`).join('')}${card.assignee && card.need !== 'working' ? `<span class="who">${e(author(card.assignee, options.board))}</span>` : ''}</div>${blocked ? `<p class="card-note">Waiting on ${e(author(card.ask_to, options.board))}: ${e(card.ask)}</p>` : ''}</article>`;
}
// Component styles stay with this renderer until the shell mounts it.
const styles = `<style>
.workbench-board { --board-paper:#FEFEFC; }
.workbench-board .board { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; padding:16px; align-items:start; }
.workbench-board .col { background:var(--well); border-radius:var(--r); min-width:0; padding:8px; }
.workbench-board .col-h,.workbench-board .card-top,.workbench-board .card-meta { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
.workbench-board .col-h h2 { font-size:14px; margin:0; flex:1; }
.workbench-board .cards { display:grid; gap:8px; margin-top:10px; }
.workbench-board .card { background:var(--board-paper); border:1px solid var(--line); border-radius:var(--r); padding:10px; cursor:pointer; }
.workbench-board h3 { font-size:14px; line-height:20px; margin:8px 0; } .workbench-board h3 a { color:inherit; text-decoration:none; }
.workbench-board .cid { font:12px var(--mono); color:var(--text-3); }
.workbench-board .tag { font-size:12px; } .workbench-board .block { color:var(--block); background:var(--block-bg); } .workbench-board .you { color:var(--wait); background:var(--wait-bg); }
.workbench-board .is-selected { outline:2px solid var(--accent); } .workbench-board .is-blocked { border-color:var(--block-line); }
.workbench-board .col-jump { display:flex; gap:16px; padding:8px 16px; flex-wrap:wrap; } .workbench-board .col-jump button { min-height:32px; display:inline-flex; align-items:center; }
.workbench-board .backlog,.workbench-board .empty-strip,.workbench-board form { margin:16px; } .workbench-board summary { cursor:pointer; min-height:32px; }
.workbench-board form { display:grid; gap:10px; } .workbench-board label { display:grid; gap:4px; }
@media (min-width:1100px) { .workbench-board .col-jump { display:none; } }
@media (min-width:700px) and (max-width:1099px) { .workbench-board .board { grid-template-columns:repeat(4,minmax(190px,1fr)); overflow-x:auto; } }
@media (max-width:699px) { .workbench-board .board { display:block; } .workbench-board .col { display:none; margin-bottom:12px; } .workbench-board .col[data-active="true"] { display:block; } .workbench-board .col-jump button,.workbench-board button,.workbench-board summary { min-height:44px; } }
</style>`;
export function boardView({ board, cards = [], me, filter = 'all', selected, messages = [], adding = false, people = [], column = 'todo' }) {
  const options = { board, me, selected, messages }, visible = filterCards(cards, filter, me);
  const backlog = visible.filter((c) => c.status === 'backlog');
  return `${styles}<div class="workbench-board"><div class="seg" role="group" aria-label="Filter cards">${['all','mine','agents','blocked'].map((f) => `<button data-board-filter="${f}" aria-pressed="${filter === f}">${heading(f)}${f === 'blocked' ? ` ${cards.filter((c) => isBlocked(c, me)).length}` : ''}</button>`).join('')}</div>${!cards.length ? '<section class="empty-strip" aria-label="Get started"><h2>Your project starts here</h2><button data-action="new-card">Add your first card</button><button data-action="connect-agent">Connect an agent</button><button data-action="open-chat">Ask for something · Write in chat /</button></section>' : ''}<nav class="col-jump" aria-label="Jump to a column">${columns.map((s) => `<button type="button" data-board-column="${s}" aria-controls="col-${s}" aria-pressed="${column === s}">${heading(s)}${s === 'done' ? ` ${visible.filter((c) => c.status === s).length}` : ''}</button>`).join('')}</nav><div class="board" role="region" aria-label="Board">${columns.map((s) => {
    const list = visible.filter((c) => c.status === s);
    return `<section class="col" id="col-${s}" data-status="${s}" data-active="${column === s}" aria-label="${heading(s)}, ${list.length} cards"><div class="col-h"><h2>${heading(s)}</h2><span class="n">${list.length}</span>${s === 'todo' ? '<button class="iconbtn" data-action="new-card" aria-label="Add card to Todo">+</button>' : ''}</div><div class="cards">${list.map((c, i) => cardView(c, options, i === 0)).join('')}</div></section>`;
  }).join('')}</div><details class="backlog"><summary>Backlog (${backlog.length})</summary><div class="cards">${backlog.map((c, i) => cardView(c, options, i === 0)).join('')}</div></details>${adding ? addCardForm({ people }) : ''}</div>`;
}
export function addCardForm({ people = [] } = {}) {
  return `<form data-board-add><h2>Add card</h2><label>Title<input name="title" required maxlength="500"></label><label>Details<textarea name="body"></textarea></label><label>Who<select name="assignee"><option value="">Available for an agent</option>${people.map((p) => `<option value="${e(p.ref)}">${e(p.name)}</option>`).join('')}</select></label><label>Label<input name="label"></label><button class="btn primary" type="submit">Add card</button><button type="button" data-action="cancel-card">Cancel</button><p role="status" data-add-status></p></form>`;
}
export async function submitCard({ api, board, values }) {
  return api(`/api/boards/${encodeURIComponent(board.slug)}/cards`, { method:'POST', body:{ title:values.title, body:values.body || '', assignee:values.assignee || null, labels:values.label ? [values.label] : [] } });
}
export function bindBoard(root, { api, board, onAdded = () => {}, onFilter = () => {} }) {
  const click = (event) => { const button = event.target.closest('[data-board-filter]'); if (button) onFilter(button.dataset.boardFilter); const jump = event.target.closest('[data-board-column]'); if (jump) { root.querySelectorAll('[data-status]').forEach((el) => { el.dataset.active = String(el.dataset.status === jump.dataset.boardColumn); }); root.querySelectorAll('[data-board-column]').forEach((el) => el.setAttribute('aria-pressed', String(el === jump))); root.querySelector(`#col-${jump.dataset.boardColumn}`)?.scrollIntoView({ block:'nearest', inline:'nearest' }); } };
  const submit = async (event) => {
    if (!event.target.matches('[data-board-add]')) return;
    event.preventDefault();
    const form = event.target, button = form.querySelector('[type="submit"]'), status = form.querySelector('[data-add-status]');
    if (button.disabled) return;
    button.disabled = true;
    try { const card = await submitCard({ api, board, values:Object.fromEntries(new FormData(form)) }); status.textContent = `Card ${cardId(board, card.number)} added to Todo`; onAdded(card); }
    catch { status.textContent = 'Could not add card. Your words are kept here. Try again.'; }
    finally { button.disabled = false; }
  };
  root.addEventListener('click', click); root.addEventListener('submit', submit);
  return () => { root.removeEventListener('click', click); root.removeEventListener('submit', submit); };
}
