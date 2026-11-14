// Capy shell: pure markup plus one refresh tied to the shell's existing refresh cycle.
import { escape as e, elapsed } from './api.js';

export const SECTIONS = [['today', 'Today'], ['bots', 'Bots'], ['compute', 'Compute'], ['settings', 'Settings']];
const count = n => Number.isSafeInteger(n) && n > 0 ? n : 0;
const rows = value => Array.isArray(value) ? value : [];
const enc = encodeURIComponent;
const current = yes => yes ? ' aria-current="page"' : '';
const paths = {
  today: '<path d="M2 9l2-6h8l2 6v4H2zM2 9h3l1 2h4l1-2h3"/>',
  bots: '<rect x="2.5" y="5" width="11" height="8" rx="2"/><path d="M8 5V2.8M5.8 9h.1M10.2 9h.1"/>',
  compute: '<rect x="4" y="4" width="8" height="8" rx="1"/><path d="M6 1v3m4-3v3M6 12v3m4-3v3M1 6h3m-3 4h3m8-4h3m-3 4h3"/>',
  settings: '<path d="m6 2 .6-1h2.8l.6 1 .3 1.2 1.1.6 1.2-.3 1.4 2.4-.8.9v1.3l.8.9-1.4 2.4-1.2-.3-1.1.6L10 14H6l-.3-1.3-1.1-.6-1.2.3L2 10l.8-.9V7.8L2 6.9l1.4-2.4 1.2.3 1.1-.6z"/><circle cx="8" cy="8" r="2"/>',
  search: '<circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5 14 14"/>',
  board: '<rect x="2" y="2.5" width="12" height="11" rx="1.5"/><path d="M6 3v10m4-10v10"/>',
  memory: '<path d="m2 6 6-4 6 4M3 7v6m3-6v6m4-6v6m3-6v6M2 14h12"/>',
  edit: '<path d="M9 3H3v10h10V7M8 8l5-6 2 2-6 5H7z"/>',
  folder: '<path d="M2 4h5l1.5 2H14v7H2z"/>',
  chevron: '<path d="m6 4 4 4-4 4"/>',
  lock: '<rect x="3.5" y="7" width="9" height="7" rx="1.5"/><path d="M5.5 7V4a2.5 2.5 0 0 1 5 0v3"/>',
  more: '<circle cx="3.5" cy="8" r="1"/><circle cx="8" cy="8" r="1"/><circle cx="12.5" cy="8" r="1"/>',
  mic: '<rect x="6" y="1.5" width="4" height="8" rx="2"/><path d="M3.5 7v1a4.5 4.5 0 0 0 9 0V7M8 12.5V15m-3 0h6"/>',
};
const icon = name => `<svg class="i" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.bots}</svg>`;
const brand = '<svg class="deck-mark" viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="8" fill="currentColor"/><path d="M7 17v-6a5 5 0 0 1 10 0v6m-7 0v-6a2 2 0 0 1 4 0v6" fill="none" stroke="var(--ink)" stroke-width="1.8" stroke-linecap="round"/></svg>';
const badge = (n, type, label) => count(n) ? `<span class="count ${type}">${n}<span class="sr"> ${label}</span></span>` : '';
const waiting = options => Number.isSafeInteger(options.needs) ? count(options.needs) : count(options.botsWaiting);
const initials = user => String(user || '?').trim().split(/\s+/u).slice(0, 2).map(word => Array.from(word)[0]).join('').toUpperCase();
function botInitials(bots) {
  const groups = new Map(), labels = new Map();
  for (const bot of bots) {
    const letters = Array.from(String(bot.name || 'Bot').toUpperCase()).filter(letter => /[\p{L}\p{N}]/u.test(letter));
    const first = letters[0] || 'B';
    if (!groups.has(first)) groups.set(first, []);
    groups.get(first).push({ bot, letters });
  }
  for (const [first, group] of groups) {
    const used = new Set();
    group.sort((a, b) => String(a.bot.name).localeCompare(String(b.bot.name)) || String(a.bot.id).localeCompare(String(b.bot.id)) || a.bot.shellKind.localeCompare(b.bot.shellKind));
    for (const { bot, letters } of group) {
      const second = group.length > 1 ? [...letters.slice(1), ...'123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'].find(letter => !used.has(letter)) || '?' : '';
      used.add(second); labels.set(bot, first + second);
    }
  }
  return labels;
}
const states = { live: ['Ready', 'live'], ready: ['Ready', 'live'], healthy: ['Healthy', 'live'], busy: ['Busy', 'busy'], working: ['Busy', 'busy'], waiting: ['Needs you', 'waiting'], needs_you: ['Needs you', 'waiting'], idle: ['Parked', 'idle'], parked: ['Parked', 'idle'] };
const stateHTML = row => {
  const state = Object.hasOwn(states, row.state) ? states[row.state] : null;
  return state ? `<small class="deck-tree-state">${state[0]}</small><span class="deck-state-dot" data-state="${state[1]}" aria-hidden="true"></span>` : '';
};
const botTail = bot => count(bot.unread) ? badge(bot.unread, 'unread', 'unread') : Number.isFinite(Date.parse(bot.last?.at)) ? `<time class="deck-bot-time" datetime="${e(bot.last.at)}">${e(elapsed(bot.last.at))}</time>` : '';
const conversationHref = id => `#/bots/-/coworkers/conversations/${enc(id)}`;

// The two registries have distinct identities. Only the explicit team projection may enrich
// a personal bot; display names never join private records across registries.
export function shellBots({ bots = [], directory = {} } = {}) {
  const team = new Map(rows(directory.team_bots).map(bot => [String(bot.id), bot]));
  return [...rows(bots).map(bot => ({ ...bot, ...team.get(String(bot.id)), shellKind: 'personal' })),
    ...rows(directory.bots).map(bot => ({ ...bot, shellKind: 'coworker' }))];
}
export const shellUnread = options => shellBots(options).reduce((total, bot) => total + count(bot.unread), 0);
const botHref = bot => bot.shellKind === 'personal' ? `#/bots/${enc(bot.id)}` : `#/bots/-/coworkers`;
const botCoworkers = (bot, directory) => {
  if (Array.isArray(bot.coworkers)) return bot.coworkers;
  if (bot.shellKind !== 'coworker') return [];
  return rows(directory.conversations).filter(item => rows(item.bot_ids).some(id => String(id) === String(bot.id)));
};
export function shellTrees(options = {}) {
  const { directory = {}, hash = '', closed = new Set() } = options, groups = new Map();
  const roster = shellBots(options), labels = botInitials(roster);
  for (const bot of roster) {
    const group = typeof bot.section === 'string' ? bot.section.trim() : '';
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(bot);
  }
  return [...groups].map(([group, bots]) => `${group ? `<details class="deck-bot-folder" data-deck-folder="${e(group)}"${closed.has(group) ? '' : ' open'}><summary>${icon('chevron')}${icon('folder')}<span>${e(group)}</span></summary>` : ''}${bots.map(bot => {
    const href = botHref(bot), selected = bot.shellKind === 'personal' && (hash === href || hash.startsWith(`${href}/`));
    return `<div class="deck-bot-tree"><a class="deck-bot-row" href="${href}"${current(selected)}><span class="deck-bot-avatar" aria-hidden="true">${e(labels.get(bot))}</span><span class="deck-bot-name">${e(bot.name || 'Bot')}</span>${bot.private === true ? `<span class="deck-private" aria-label="Private bot">${icon('lock')}</span>` : ''}${botTail(bot)}</a><ul>${botCoworkers(bot, directory).map(worker => {
      const conversation = rows(directory.conversations).find(item => String(item.id) === String(worker.conversation_id || worker.id));
      const title = worker.name || worker.title || conversation?.title || 'Coworker';
      const href = worker.conversation_id || conversation ? conversationHref(worker.conversation_id || conversation.id) : `#/bots/${enc(bot.id)}/coworkers`;
      return `<li><a class="deck-tree-row" href="${href}"${current(hash === href)}><span class="deck-tree-name">${e(title)}</span>${stateHTML(worker)}</a></li>`;
    }).join('')}</ul></div>`;
  }).join('')}${group ? '</details>' : ''}`).join('') || '<p class="deck-rail-empty">No bots yet</p>';
}
export function shellThreads({ directory = {} } = {}) {
  return rows(directory.conversations).map(item => `<a class="deck-thread-row" href="${conversationHref(item.id)}">${e(item.title || 'Conversation')}</a>`).join('');
}
const accountLinks = (options, { count = false } = {}) => { const { can_admin, can_manage_instance } = options; return `<a href="#/needs">Needs me${count ? badge(waiting(options), 'need', 'waiting on you') : ''}</a><a href="#/boards">Projects</a><a href="#/console">Console</a><a href="#/rules">Rules</a>${can_admin === true ? '<a href="#/admin">Admin</a>' : ''}${can_manage_instance === true ? '<a href="#/instance-admin">Instance console</a>' : ''}<button data-action="shortcuts">Shortcuts</button><button data-action="logout">Sign out</button>`; };
export function renderShellBar(options) {
  const { workspace = 'Commonplace', user = '', hash = '', sections, section } = options;
  const role = { owner: 'Owner', member: 'Member' }[options.role];
  return `<nav class="rail" data-deck="bar" aria-label="Workspace">
    <div class="deck-brand-row"><a class="deck-brand" href="#/" aria-label="Commonplace home">${brand}<b>Commonplace</b></a><button class="iconbtn" id="cmdk" data-action="palette" aria-keyshortcuts="Control+K Meta+K" aria-label="Search or do">${icon('search')}</button></div>
    <a class="deck-new-thread nav" href="#/" data-deck-shell-action="new-thread">${icon('edit')}New thread</a>
    <div class="deck-links">${sections.map(([id, label]) => `<a class="nav" href="#/${id}" data-nav="${id}"${current(section === id)}>${icon(id)}${label}${id === 'today' ? badge(waiting(options), 'need', 'waiting on you') : id === 'bots' ? `<span data-deck-unread>${badge(shellUnread(options), 'unread', 'unread')}</span>` : ''}</a>`).join('')}</div>
    <div class="deck-rail-scroll"><button class="deck-rail-heading ws" data-action="switch-workspace" aria-label="Workspace: ${e(workspace)}. Switch workspace">${e(workspace)} workspace</button><a class="nav" href="#/board"${current(/^#\/board(?:\/|$)/.test(hash))}>${icon('board')}Board</a><a class="nav" href="#/memory"${current(hash.startsWith('#/memory'))}>${icon('memory')}Memory</a>
    <h2 class="deck-rail-heading">Bots<a class="deck-new-bot" href="#/bots?new=1" aria-label="New bot">+</a></h2><div data-deck-bots>${shellTrees(options)}</div><h2 class="deck-rail-heading">Threads</h2><div data-deck-threads>${shellThreads(options)}</div><p class="deck-rail-error" data-deck-rail-error hidden></p></div>
    <div class="rail-foot"><details class="user-menu"><summary class="me" aria-label="${e(user || 'Account')}"><span class="mark" aria-hidden="true">${e(initials(user))}</span><span class="deck-owner"><b>${e(user || 'Account')}</b><small>${role ? `${role} · ` : ''}${e(workspace)}</small></span>${icon('chevron')}</summary><div class="user-links">${accountLinks(options)}</div></details></div>
  </nav>`;
}
export function renderShellTop(options) {
  const { hash = '', section, user = '', board = null } = options;
  const label = SECTIONS.find(([id]) => id === section)?.[1] || (hash.startsWith('#/memory') ? 'Memory' : hash.startsWith('#/board') ? 'Board' : ['#/', '#/home'].includes(hash) ? 'Home' : board?.slug ? 'Board' : 'Commonplace');
  const boardPage = label === 'Board' && (!!board?.slug || /^#\/board(?:\/[^/?#]+)?(?:\?.*)?$/.test(hash));
  return `<header class="m-top" data-deck="top"><a class="deck-brand" href="#/" aria-label="Commonplace home">${brand}</a>${boardPage ? '' : `<b>${label}</b>`}<span class="spacer"></span>${board?.slug ? `<button class="iconbtn" data-action="coordinator" data-board="${e(board.slug)}" aria-label="Project settings">${icon('settings')}</button>` : ''}<button class="iconbtn" data-action="palette" aria-label="Search or do (command palette)">${icon('search')}</button><button class="deck-phone-avatar" data-action="switch-workspace" aria-label="Account and workspace" data-deck-waiting="${waiting(options) > 0}">${e(initials(user))}</button></header>`;
}
// The phone has no rail, so every destination the rail and account menu offer lives in this sheet (rendered beside the tabs).
// Any click inside it (a link, a button or the scrim) closes it; the shell's click handler does the work.
export const renderShellMore = options => `<div class="deck-more" id="deck-more" aria-label="More" data-deck-shell-action="more-close"${options.moreOpen === true ? ' role="dialog"' : ' hidden'}><div class="deck-more-panel"><a href="#/board">${icon('board')}Board</a><a href="#/memory">${icon('memory')}Memory</a>${accountLinks(options, { count: true })}<button data-action="switch-workspace">Switch workspace</button></div></div>`;
export function renderShellTabs(options) {
  const { sections, section, hash } = options, mic = !['#/', '#/home'].includes(hash) && !options.hasComposer;
  const more = !section && !['#/', '#/home'].includes(hash);
  return `<nav class="m-tabs" data-deck="tabs" data-count="${sections.length}" data-mic="${mic}" aria-label="Main">${sections.map(([id, label]) => `<a href="#/${id}" data-nav="${id}"${current(section === id)}>${icon(id)}${label}${id === 'today' ? badge(waiting(options), 'need', 'waiting on you') : ''}</a>`).join('')}<button type="button" class="deck-tab-more" data-deck-shell-action="more" aria-haspopup="dialog" aria-expanded="${options.moreOpen === true}" aria-controls="deck-more"${current(more)}>${icon('more')}More</button>${mic ? micButton('deck-tab-mic') : ''}</nav>`;
}
const micButton = className => `<button type="button" class="${className}" data-deck-shell-action="mic" aria-label="Voice input">${icon('mic')}</button>`;
const composerSelector = ':is(#content, #card-panel) :is(.bh-form, .bt-reply form, .today-compose, .bots-compose, .cw-composer, .cb-composer, .composer:has([name="text"]), .space-compose, .space-composer, [data-form="comment"], [data-form="reply"])';

export function createDeckShell({ root, api, context, on }) {
  let bots = [], directory = {}, identity = '', generation = 0, pending = null, error = '';
  const closed = new Set();
  const composer = () => [...(root?.querySelectorAll?.(composerSelector) || [])].find(form => !form.closest('[hidden]') && form.getClientRects().length);
  // The sheet stays open across the shell's refreshes, and closes when the route changes.
  let moreAt = null;
  const hashNow = () => globalThis.location?.hash || '';
  // role="dialog" is present only while the sheet is open, so a closed sheet is never mistaken for a dialog.
  function showMore(sheet, open) {
    moreAt = open ? hashNow() : null;
    sheet.hidden = !open;
    if (open) sheet.setAttribute('role', 'dialog'); else sheet.removeAttribute('role');
    root.querySelector('.deck-tab-more')?.setAttribute('aria-expanded', String(open));
    if (open) sheet.querySelector('a, button')?.focus();
  }
  const moreOpen = () => moreAt !== null && moreAt === hashNow();
  const options = () => ({ bots, directory, closed, hash: hashNow(), hasComposer: !!composer(), moreOpen: moreOpen() });
  function chrome() {
    if (!on()) return;
    const form = composer(), tabs = root?.querySelector?.('[data-deck="tabs"]');
    if (form) {
      const ownMic = form.querySelector('[data-deck-shell-action="mic"]');
      const voiceMic = form.querySelector('[data-voice="mic"], [data-today="mic"], [data-bot-action="mic"]');
      if (voiceMic) ownMic?.remove();
      else if (!ownMic) (form.querySelector('.bh-toolbar, .cb-compose-bar') || form).insertAdjacentHTML('beforeend', micButton('deck-composer-mic'));
    }
    if (!tabs) return;
    const show = !['#/', '#/home'].includes(globalThis.location?.hash) && !form;
    if (tabs.dataset.mic !== String(show)) tabs.dataset.mic = String(show);
    const mic = tabs.querySelector('[data-deck-shell-action="mic"]');
    if (show && !mic) tabs.insertAdjacentHTML('beforeend', micButton('deck-tab-mic'));
    if (!show && mic) mic.remove();
  }
  if (root?.ownerDocument && globalThis.MutationObserver) new MutationObserver(chrome).observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden', 'open'] });
  function paint() {
    const tree = root?.querySelector?.('[data-deck-bots]'), threads = root?.querySelector?.('[data-deck-threads]');
    if (tree && tree.innerHTML !== shellTrees(options())) tree.innerHTML = shellTrees(options());
    if (threads && threads.innerHTML !== shellThreads(options())) threads.innerHTML = shellThreads(options());
    const unread = root?.querySelector?.('[data-deck-unread]');
    if (unread) unread.innerHTML = badge(shellUnread(options()), 'unread', 'unread');
    const status = root?.querySelector?.('[data-deck-rail-error]');
    if (status) { status.hidden = !error; status.innerHTML = error ? 'Could not load the bot tree. <button data-deck-shell-action="retry">Retry</button>' : ''; }
  }
  function reset() { generation++; bots = []; directory = {}; closed.clear(); pending = null; error = ''; }
  function identify() {
    const me = context?.()?.me, key = JSON.stringify([me?.actor, me?.workspace]);
    if (key !== identity) { reset(); identity = key; }
  }
  async function sync() {
    if (!on() || !api) return;
    identify();
    if (pending) return pending;
    const token = generation;
    pending = (async () => {
      const results = await Promise.allSettled([api('/api/coworkers/bots', { timeoutMs: 10000 }), api('/api/coworkers/conversations', { timeoutMs: 10000 })]);
      if (token !== generation || !on()) return;
      directory = { ...(results[0].status === 'fulfilled' ? results[0].value : {}), conversations: results[1].status === 'fulfilled' ? rows(results[1].value?.conversations) : [] };
      error = results.some(result => result.status === 'rejected') ? 'unavailable' : '';
      paint();
    })().finally(() => { if (token === generation) pending = null; });
    return pending;
  }
  root?.addEventListener?.('toggle', event => {
    if (event.target?.isConnected === false) return;
    const folder = event.target?.dataset?.deckFolder;
    if (folder !== undefined) { if (event.target.open) closed.delete(folder); else closed.add(folder); }
  }, true);
  root?.addEventListener?.('click', event => {
    const action = event.target?.closest?.('[data-deck-shell-action]')?.dataset.deckShellAction;
    if (!action || !on()) return;
    const sheet = root.querySelector('#phone-tabs .deck-more');
    if (action === 'more' && sheet) { event.preventDefault(); showMore(sheet, sheet.hidden); }
    if (action === 'more-close' && sheet) showMore(sheet, false);
    if (action === 'retry') { event.preventDefault(); void sync(); }
    if (action === 'new-thread' && globalThis.location?.hash === '#/') {
      event.preventDefault(); root.querySelector('#content textarea, #content input')?.focus();
    }
    if (action === 'mic') {
      event.preventDefault();
      const mic = [...(root.querySelectorAll?.('#content [data-voice="mic"], #content [data-today="mic"], #content [data-bot-action="mic"]') || [])].find(button => !button.disabled);
      if (mic) mic.click(); else if (context?.()?.voice) void context().voice(composer()?.querySelector('[name="text"], [name="reply"], textarea[data-draft], input[data-draft], .cb-composer textarea')); else context?.()?.go?.('#/');
    }
  });
  root?.addEventListener?.('keydown', event => {
    const sheet = root.querySelector('#phone-tabs .deck-more');
    if (event.key !== 'Escape' || !sheet || sheet.hidden) return;
    showMore(sheet, false); root.querySelector('.deck-tab-more')?.focus();
  });
  root?.ownerDocument?.addEventListener?.('signout', () => { reset(); paint(); });
  return { options, sync, reset, roster(value) { identify(); bots = rows(value); paint(); } };
}
