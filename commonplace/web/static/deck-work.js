// Work: every project's cards in status lanes. The lanes use the Board's own grouping (boardGroup over the
// owner queue), so "Waiting on you" here is the same list the Board and the Waiting count show.
import { escape as e, elapsed } from './api.js';
import { boardGroup, needsOwner, cardBot, cardTitle, boardCopy, stateWord } from './deck-board.js';

export const TITLE = 'Work';
export const SHOWN = 5;
// [group, label, dot tone, what an empty lane says]
export const LANES = [
  ['todo', 'Waiting on you', 'wait', 'Nothing waits on you.'],
  ['doing', 'Active', 'work', 'No active cards.'],
  ['review', 'In review', '', 'Nothing is waiting for review.'],
  ['done', 'Shipped', 'ok', 'Nothing shipped yet.'],
];
export const NO_MACHINE = 'No computer is connected, so bots can plan but not run code.';

const boardName = board => board?.name || board?.slug || 'Project';
const initial = name => Array.from(String(name || ''))[0]?.toUpperCase() || '?';
const stamp = card => Date.parse(card.updated_at || card.created_at) || 0;

/** The project a Work address names: `#/work?project=<slug>`. */
export function projectFrom(hash = '') {
  const query = String(hash).split('?')[1]?.split('#')[0] || '';
  try { return new URLSearchParams(query).get('project') || ''; } catch { return ''; }
}
export const workHash = project => project ? `#/work?project=${encodeURIComponent(project)}` : '#/work';

/** Every card the Board would show, once, with its lane and the name to draw. Backlog cards stay out unless they wait on the person. */
export function workItems({ boards = [], cards = new Map(), bots = [], me = '' } = {}) {
  const items = [];
  for (const board of boards) {
    for (const card of cards.get(board.slug) || []) {
      if (card.archived || (card.status === 'backlog' && !needsOwner(card, me))) continue;
      const bot = cardBot(card, { board, bots });
      items.push({ card, board, group: boardGroup(card, me), state: stateWord(card, me), name: boardCopy(bot?.name || '') || boardName(board) });
    }
  }
  return items.sort((a, b) => stamp(b.card) - stamp(a.card) || b.card.number - a.card.number);
}
/** Open cards (not shipped) per project, for the filter chips. */
export const openCounts = items => items.reduce((sum, item) => item.group === 'done' ? sum : sum.set(item.board.slug, (sum.get(item.board.slug) || 0) + 1), new Map());

const when = at => {
  const age = at ? elapsed(at) : '';
  return age && age !== '—' ? `<time datetime="${e(at)}" title="${e(at)}">${e(age)}</time>` : '';
};
export const cardHTML = ({ card, name, state }) => `<a class="wk-card" href="#/card/${e(card.number)}" data-card="${e(card.number)}"><span class="wk-title">${e(cardTitle(card.title))}</span><span class="wk-meta"><span class="wk-av" aria-hidden="true">${e(initial(name))}</span><span class="wk-name">${e(name)}</span>${state ? `<span class="wk-state">${e(state === 'Needs you' ? 'Waiting on you' : state)}</span>` : ''}${when(card.updated_at)}</span></a>`;

export function laneHTML([group, label, tone, empty], list, expanded) {
  const open = expanded.has(group), shown = open ? list : list.slice(0, SHOWN), hidden = list.length - shown.length;
  const toggle = hidden > 0 ? `<button type="button" class="wk-more" data-wk="more" data-lane="${group}" aria-expanded="false">Show ${hidden} more</button>` : open && list.length > SHOWN ? `<button type="button" class="wk-more" data-wk="less" data-lane="${group}" aria-expanded="true">Show fewer</button>` : '';
  const cards = list.length ? `<div class="wk-cards">${shown.map(cardHTML).join('')}</div>${toggle}` : `<div class="wk-cards"><p class="wk-empty">${e(empty)}</p></div>`;
  return `<section class="wk-lane" data-lane="${group}" aria-label="${e(label)}"><h2 class="wk-lane-name"><i class="wk-dot${tone ? ` ${tone}` : ''}" aria-hidden="true"></i>${e(label)} <b${group === 'todo' && list.length ? ' class="wk-waits"' : ''}>${list.length}</b></h2>${cards}</section>`;
}

/** The health line: how many bots can take work, and a plain sentence when no machine is connected. */
export function healthHTML(bots) {
  if (!Array.isArray(bots)) return '';
  const live = bots.filter(bot => !bot.archived);
  const ready = live.filter(bot => Object.values(bot.readiness || {}).every(row => !row?.state || row.state === 'done' || row.state === 'not_needed')).length;
  const machine = live.some(bot => bot.readiness?.machine?.state === 'done');
  const count = ready ? `${ready} ${ready === 1 ? 'bot' : 'bots'} ready` : 'No bots are ready yet';
  return `<p class="wk-health" data-wk-health><span><i class="wk-dot${ready ? ' ok' : ''}" aria-hidden="true"></i>${count}</span>${machine ? '' : `<span><i class="wk-dot" aria-hidden="true"></i><span>${NO_MACHINE} <a href="#/compute">Connect one in Cloud workspaces</a></span></span>`}</p>`;
}

export function chipsHTML(boards, counts, project, cards = new Map()) {
  const chip = (slug, label, count) => `<button type="button" class="wk-chip" data-wk="project" data-project="${e(slug)}" aria-pressed="${project === slug}">${e(label)}${count == null ? '' : ` <b>${count}</b>`}</button>`;
  return `<div class="wk-chips" role="group" aria-label="Project">${chip('', 'All projects', null)}${boards.map(board => chip(board.slug, boardName(board), counts.get(board.slug) ?? (cards.has(board.slug) ? 0 : null))).join('')}</div>`;
}

export function pageHTML({ boards = null, cards = new Map(), bots = null, me = '', project = '', expanded = new Set(), error = '', partial = false } = {}) {
  const bar = (chips = '') => `<header class="wk-bar"><h1>${TITLE}</h1>${chips}</header>`;
  if (error) return `${bar()}<div class="wk-main"><p class="wk-note" role="alert">${e(error)} <button type="button" class="wk-btn" data-wk="retry">Try again</button></p></div>`;
  if (boards === null) return `${bar()}<div class="wk-main"><p class="wk-note" role="status">Loading work…</p></div>`;
  const items = workItems({ boards, cards, bots: bots || [], me });
  const chosen = boards.some(board => board.slug === project) ? project : '';
  const shown = chosen ? items.filter(item => item.board.slug === chosen) : items;
  const lanes = LANES.map(lane => laneHTML(lane, shown.filter(item => item.group === lane[0]), expanded)).join('');
  const note = partial ? '<p class="wk-note" role="status">Some projects could not be loaded. <button type="button" class="wk-btn" data-wk="retry">Try again</button></p>' : '';
  return `${bar(chipsHTML(boards, openCounts(items), chosen, cards))}<div class="wk-main">${healthHTML(bots)}${note}<div class="wk-lanes">${lanes}</div></div>`;
}

function styles(doc) {
  if (globalThis.commonplaceAssetManifest?.styles.includes('deck-work.css')) return;
  if (!doc?.head || doc.querySelector('link[data-work-style]')) return;
  const link = doc.createElement('link');
  link.rel = 'stylesheet'; link.href = new URL('./deck-work.css', import.meta.url).href;
  link.setAttribute('data-work-style', ''); doc.head.append(link);
}

/** Mount the screen. The shell calls refresh() when the queue or the route changes; nothing here polls. */
export function mount(context) {
  const { root, api } = context, doc = root.ownerDocument || globalThis.document;
  styles(doc);
  let ctx = context, stopped = false, epoch = 0, request = null, drawn = '';
  const view = { boards: null, cards: new Map(), bots: null, error: '', partial: false, expanded: new Set(), project: projectFrom(context.route) || projectFrom(globalThis.location?.hash) };
  root.classList.add('deck-work');
  const headers = ['.head'].map(selector => root.parentElement?.querySelector(selector)).filter(Boolean).map(node => ({ node, hidden: node.hidden }));
  for (const { node } of headers) node.hidden = true;
  const paint = () => {
    if (stopped) return;
    const html = pageHTML({ ...view, me: ctx.me?.actor || '' });
    if (html === drawn) return;
    const top = root.querySelector('.wk-main')?.scrollTop || 0, chipsLeft = root.querySelector('.wk-chips')?.scrollLeft || 0;
    root.innerHTML = drawn = html;
    const main = root.querySelector('.wk-main'), chips = root.querySelector('.wk-chips');
    if (main) main.scrollTop = top;
    if (chips) chips.scrollLeft = chipsLeft;
  };
  async function load() {
    const mine = ++epoch;
    request?.abort(); request = new AbortController();
    const options = { signal: request.signal, timeoutMs: 10000 };
    try {
      let boards = Array.isArray(ctx.boards) && ctx.boards.length ? ctx.boards : null;
      const roster = api('/api/bots', options).then(answer => answer?.bots || null, () => null);
      boards ||= (await api('/api/boards?view=shell', options))?.boards || [];
      const reads = await Promise.allSettled(boards.map(board => api(`/api/boards/${encodeURIComponent(board.slug)}/cards`, options)));
      const bots = await roster;
      if (stopped || mine !== epoch) return;
      const cards = new Map(view.cards);
      reads.forEach((read, index) => { if (read.status === 'fulfilled' && Array.isArray(read.value?.cards)) cards.set(boards[index].slug, read.value.cards); });
      Object.assign(view, { boards, cards, bots: bots || view.bots, error: '', partial: reads.some(read => read.status === 'rejected') });
    } catch (failure) {
      if (stopped || mine !== epoch || failure?.name === 'AbortError') return;
      view.error = 'Work could not be loaded.';
    }
    paint();
  }
  const choose = project => {
    view.project = project;
    // The address follows the choice without starting a new route; the screen is already showing.
    try { globalThis.history?.replaceState?.(globalThis.history.state, '', workHash(project)); } catch { /* The filter still applies. */ }
    paint();
  };
  const onClick = event => {
    const target = event.target?.closest?.('[data-wk]');
    if (!target) return;
    const action = target.dataset.wk;
    if (action === 'project') choose(target.dataset.project || '');
    if (action === 'more') { view.expanded.add(target.dataset.lane); paint(); }
    if (action === 'less') { view.expanded.delete(target.dataset.lane); paint(); }
    if (action === 'retry') { view.error = ''; view.boards = null; paint(); void load(); }
  };
  root.addEventListener('click', onClick);
  paint(); void load();
  return {
    refresh(next) {
      if (next) {
        ctx = { ...ctx, ...next };
        const asked = projectFrom(next.route);
        if (next.route && asked !== view.project && /\?/.test(next.route)) view.project = asked;
      }
      void load();
    },
    stop() {
      stopped = true; epoch++; request?.abort();
      root.removeEventListener('click', onClick);
      root.classList.remove('deck-work'); root.innerHTML = '';
      for (const { node, hidden } of headers) node.hidden = hidden;
    },
  };
}
