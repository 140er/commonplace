// Capy shell: pure markup plus one refresh tied to the shell's existing refresh cycle.
import { escape as e, elapsed } from './api.js';
import { botPage } from './routes.js';
import { artHTML } from './deck-art.js';
import { needsCards } from './needs-me.js';
import { UNASSIGNED, botNode, currentNode, loadCollapsed, projectTree, saveCollapsed, treeKey } from './deck-tree.js';

// The places, in the order the sidebar shows them. Waiting on you is home: what needs an answer comes first.
export const SECTIONS = [['home', 'Waiting on you'], ['work', 'Work'], ['bots', 'Bots'], ['settings', 'Settings']];
// The phone tab bar. One list, so changing the bar is a one-line edit.
export const PHONE_TABS = [['home', 'Waiting', '#/'], ['bots', 'Bots', '#/bots'], ['work', 'Work', '#/work'], ['memory', 'Memory', '#/memory']];
/** The place a route belongs to: 'home', 'work', 'bots', 'settings', or '' for the pages in the account menu. */
export function placeOf(hash = '') {
  const name = /^#\/([a-z]+)/.exec(hash)?.[1] || '';
  if (!name || name === 'home' || name === 'today' || name === 'needs') return 'home';
  if (['work', 'board', 'boards', 'card', 'start'].includes(name)) return 'work';
  return ['bots', 'settings', 'memory'].includes(name) ? name : '';
}
export const phoneTabs = ({ hash = '', section = placeOf(hash) } = {}) => PHONE_TABS.map(([id, label, href]) => ({ id, label, href, on: section === id }));
const count = n => Number.isSafeInteger(n) && n > 0 ? n : 0;
const rows = value => Array.isArray(value) ? value : [];
const enc = encodeURIComponent;
const isHome = hash => ['', '#/', '#/home'].includes(hash || '');
const current = yes => yes ? ' aria-current="page"' : '';
const paths = {
  menu: '<path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"/>',
  home: '<path d="M2 8.5 3.6 4h8.8L14 8.5V12H2zM2 8.5h3.2l.8 1.5h4l.8-1.5H14"/>',
  work: '<rect x="2.5" y="3" width="4.5" height="10" rx="1"/><rect x="9" y="3" width="4.5" height="6.5" rx="1"/>',
  today: '<path d="M2 9l2-6h8l2 6v4H2zM2 9h3l1 2h4l1-2h3"/>',
  bots: '<rect x="2.5" y="5" width="11" height="8" rx="2"/><path d="M8 5V2.8M5.8 9h.1M10.2 9h.1"/>',
  compute: '<rect x="4" y="4" width="8" height="8" rx="1"/><path d="M6 1v3m4-3v3M6 12v3m4-3v3M1 6h3m-3 4h3m8-4h3m-3 4h3"/>',
  settings: '<path d="m6 2 .6-1h2.8l.6 1 .3 1.2 1.1.6 1.2-.3 1.4 2.4-.8.9v1.3l.8.9-1.4 2.4-1.2-.3-1.1.6L10 14H6l-.3-1.3-1.1-.6-1.2.3L2 10l.8-.9V7.8L2 6.9l1.4-2.4 1.2.3 1.1-.6z"/><circle cx="8" cy="8" r="2"/>',
  search: '<circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5 14 14"/>',
  board: '<rect x="2" y="2.5" width="12" height="11" rx="1.5"/><path d="M6 3v10m4-10v10"/>',
  memory: '<path d="M3.5 2h8A1.5 1.5 0 0 1 13 3.5V14H5a1.5 1.5 0 0 1-1.5-1.5zM3.5 12.5A1.5 1.5 0 0 1 5 11h8M6.5 5h3.5"/>',
  edit: '<path d="M9 3H3v10h10V7M8 8l5-6 2 2-6 5H7z"/>',
  folder: '<path d="M2 4h5l1.5 2H14v7H2z"/>',
  chevron: '<path d="m6 4 4 4-4 4"/>',
  lock: '<rect x="3.5" y="7" width="9" height="7" rx="1.5"/><path d="M5.5 7V4a2.5 2.5 0 0 1 5 0v3"/>',
  more: '<circle cx="3.5" cy="8" r="1"/><circle cx="8" cy="8" r="1"/><circle cx="12.5" cy="8" r="1"/>',
  needs: '<circle cx="8" cy="8" r="5.5"/><path d="M8 5v3.5l2 1.5"/>',
  projects: '<path d="M2 4.5h12M2 8h12M2 11.5h8"/>',
  rules: '<path d="M4 2.5h8v11H4zM6 5.5h4M6 8h4M6 10.5h2"/>',
  console: '<rect x="2" y="3" width="12" height="10" rx="1.5"/><path d="m5 7 2 1.5L5 10M8.5 10.5h3"/>',
  pipeline: '<circle cx="4" cy="8" r="1.6"/><circle cx="12" cy="8" r="1.6"/><path d="M5.6 8h4.8"/>',
  admin: '<path d="M8 2 3 4v4c0 3 2.2 5.2 5 6 2.8-.8 5-3 5-6V4z"/>',
  close: '<path d="m4 4 8 8M12 4l-8 8"/>',
  mic: '<rect x="6" y="1.5" width="4" height="8" rx="2"/><path d="M3.5 7v1a4.5 4.5 0 0 0 9 0V7M8 12.5V15m-3 0h6"/>',
};
const icon = name => `<svg class="i" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.bots}</svg>`;
const brand = '<svg class="deck-mark" viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="8" fill="currentColor"/><path d="M7 17v-6a5 5 0 0 1 10 0v6m-7 0v-6a2 2 0 0 1 4 0v6" fill="none" stroke="var(--ink)" stroke-width="1.8" stroke-linecap="round"/></svg>';
const badge = (n, type, label) => count(n) ? `<span class="count ${type}">${n}<span class="sr"> ${label}</span></span>` : '';
const waiting = options => Number.isSafeInteger(options.needs) ? count(options.needs) : count(options.botsWaiting);
const initials = user => String(user || '?').trim().split(/\s+/u).slice(0, 2).map(word => Array.from(word)[0]).join('').toUpperCase();
export function botInitials(bots) {
  const groups = new Map(), labels = new Map();
  for (const bot of bots) {
    const letters = Array.from(String(bot.name || 'Bot').toUpperCase()).filter(letter => /[\p{L}\p{N}]/u.test(letter));
    const first = letters[0] || 'B';
    if (!groups.has(first)) groups.set(first, []);
    groups.get(first).push({ bot, letters });
  }
  for (const [first, group] of groups) {
    const used = new Set();
    group.sort((a, b) => String(a.bot.name).localeCompare(String(b.bot.name)) || String(a.bot.id).localeCompare(String(b.bot.id)) || String(a.bot.shellKind || '').localeCompare(String(b.bot.shellKind || '')));
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
const botHref = bot => botPage(bot.id, bot.shellKind !== 'personal');
const botCoworkers = (bot, directory) => {
  if (Array.isArray(bot.coworkers)) return bot.coworkers;
  if (bot.shellKind !== 'coworker') return [];
  return rows(directory.conversations).filter(item => rows(item.bot_ids).some(id => String(id) === String(bot.id)));
};
// The tree is built in two steps: a model (which rows exist, which are open, which one takes the tab stop),
// then markup. Every bot is a row that opens onto its projects (and, for a coworker, its conversations);
// projects with no bot of the person's sit under Unassigned. A row is open unless it was folded on this
// device, and the row holding the page being viewed is open until the person folds it on that page.
function treeModel(options) {
  const { directory = {}, hash = '', collapsed = new Set(), manual = new Set() } = options;
  const roster = shellBots(options), labels = botInitials(roster), tree = projectTree({ bots: roster, boards: options.boards });
  const at = currentNode(hash, tree), items = [], groups = new Map();
  const open = (node, holdsCurrent) => !collapsed.has(node) || (holdsCurrent && !manual.has(node));
  const selectedHere = href => hash === href || hash.startsWith(`${href}/`) || hash.startsWith(`${href}?`);
  for (const bot of roster) {
    const personal = bot.shellKind === 'personal', node = personal ? botNode(bot.id) : `coworker:${bot.id}`, href = botHref(bot);
    const projects = personal ? tree.under.get(String(bot.id)) || [] : [];
    const talks = botCoworkers(bot, directory).map(worker => {
      const conversation = rows(directory.conversations).find(item => String(item.id) === String(worker.conversation_id || worker.id));
      const link = worker.conversation_id || conversation ? conversationHref(worker.conversation_id || conversation.id) : `#/bots/${enc(bot.id)}/coworkers`;
      return { worker, link, title: worker.name || worker.title || conversation?.title || 'Bot' };
    });
    const entry = { bot, node, href, selected: selectedHere(href), projects, talks, label: labels.get(bot), key: node };
    entry.expanded = open(node, at === node || talks.some(talk => hash === talk.link));
    const group = typeof bot.section === 'string' ? bot.section.trim() : '';
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(entry);
  }
  const lonely = options.rosterReady === false ? [] : tree.unassigned;
  const unassigned = lonely.length ? { projects: lonely, expanded: open(UNASSIGNED, at === UNASSIGNED) } : null;
  for (const entries of groups.values()) for (const entry of entries) {
    items.push({ key: entry.key, current: entry.selected });
    if (entry.expanded) {
      for (const project of entry.projects) items.push({ key: `project:${project.slug}`, current: atProject(hash, project.slug) });
      for (const talk of entry.talks) items.push({ key: `talk:${talk.link}`, current: hash === talk.link });
    }
  }
  if (unassigned) {
    items.push({ key: UNASSIGNED, current: false });
    if (unassigned.expanded) for (const project of unassigned.projects) items.push({ key: `project:${project.slug}`, current: atProject(hash, project.slug) });
  }
  const stop = (items.find(item => item.current) || items[0])?.key;
  return { groups, unassigned, stop, roster };
}
const atProject = (hash, slug) => { const href = `#/board/${enc(slug)}`; return hash === href || hash.startsWith(`${href}/`); };
const chevronNode = node => `<span class="deck-node-chevron" data-deck-toggle="${e(node)}" aria-hidden="true">${icon('chevron')}</span>`;
const treeHint = text => `<li role="none"><span class="deck-tree-empty" role="treeitem" aria-level="2" aria-disabled="true">${text}</span></li>`;
export const treeAttrs = html => html.includes('role="treeitem"') ? ' role="tree" aria-label="Bots and projects"' : '';
export function shellTrees(options = {}) {
  const { hash = '', scope = 'rail', moving = null } = options, model = treeModel(options);
  const waitingIn = slug => needsCards(options.needsQueue || {}).filter(card => card?.board === slug).length;
  const mine = model.roster.filter(bot => bot.shellKind === 'personal' && bot.mine !== false);
  let counter = 0;
  const holder = slug => [...model.groups.values()].flat().find(entry => entry.projects.some(project => project.slug === slug))?.bot.id;
  const tab = key => key === model.stop ? 0 : -1;
  const projectRow = project => {
    const href = `#/board/${enc(project.slug)}`, waiting = waitingIn(project.slug), key = `project:${project.slug}`;
    const mover = moving === project.slug ? `<div class="deck-move" role="group" aria-label="Move ${e(project.name)}"><label for="deck-move-${scope}">Move under</label><select id="deck-move-${scope}" data-deck-move-select="${e(project.slug)}"><option value="">Unassigned</option>${mine.map(bot => `<option value="${e(bot.id)}"${String(holder(project.slug)) === String(bot.id) ? ' selected' : ''}>${e(bot.name || 'Bot')}</option>`).join('')}</select></div>` : '';
    return `<li role="none" class="deck-project-li"><a class="deck-tree-row deck-project-row" href="${href}"${current(atProject(hash, project.slug))} role="treeitem" aria-level="2" tabindex="${tab(key)}" data-deck-item="${e(key)}" aria-keyshortcuts="M"><span class="deck-tree-name">${e(project.name)}</span>${waiting ? `<small>${waiting} waiting</small>` : ''}</a>${mine.length ? `<button type="button" class="deck-move-btn" tabindex="-1" data-deck-move="${e(project.slug)}" aria-expanded="${moving === project.slug}" aria-label="Move ${e(project.name)} under another bot or Unassigned">${icon('more')}</button>` : ''}${mover}</li>`;
  };
  const botHTML = entry => {
    const { bot, node, href, selected, projects, talks, label, expanded } = entry, id = `deck-tree-${scope}-${counter++}`;
    const waiting = states[bot.state]?.[1] === 'waiting' || (bot.kind === 'general' ? needsCards(options.needsQueue || {}).length > 0 : bot.board && needsCards(options.needsQueue || {}).some(card => card?.board === bot.board));
    const hint = bot.shellKind === 'personal' ? 'No projects yet' : 'No conversations yet';
    const children = [...projects.map(projectRow), ...talks.map(({ worker, link, title }) => `<li role="none"><a class="deck-tree-row" href="${link}"${current(hash === link)} role="treeitem" aria-level="2" tabindex="${tab(`talk:${link}`)}" data-deck-item="${e(`talk:${link}`)}"><span class="deck-tree-name">${e(title)}</span>${stateHTML(worker)}</a></li>`)];
    return `<div class="deck-bot-tree" role="none"><a class="deck-bot-row" href="${href}"${current(selected)} role="treeitem" aria-level="1" aria-expanded="${expanded}" aria-owns="${id}" tabindex="${tab(node)}" data-deck-item="${e(node)}">${chevronNode(node)}<span class="deck-bot-avatar" aria-hidden="true">${e(label)}</span><span class="deck-bot-name">${e(bot.name || 'Bot')}</span>${waiting ? '<span class="deck-state-dot" data-state="waiting" role="img" aria-label="Waiting on you" title="Waiting on you"></span>' : ''}${bot.private === true ? `<span class="deck-private" role="img" aria-label="Private bot" title="Private: only you can see this bot">${icon('lock')}</span>` : ''}${botTail(bot)}</a><ul role="group" id="${id}"${expanded ? '' : ' hidden'}>${children.join('') || treeHint(hint)}</ul></div>`;
  };
  const folders = [...model.groups].map(([group, entries]) => `${group ? `<details class="deck-bot-folder" data-deck-folder="${e(group)}"${(options.closed || new Set()).has(group) ? '' : ' open'}><summary>${icon('chevron')}${icon('folder')}<span>${e(group)}</span></summary>` : ''}${entries.map(botHTML).join('')}${group ? '</details>' : ''}`).join('');
  const lone = model.unassigned, loneId = `deck-tree-${scope}-unassigned`;
  const unassigned = lone ? `<div class="deck-bot-tree" role="none"><div class="deck-bot-row deck-unassigned-row" role="treeitem" aria-level="1" aria-expanded="${lone.expanded}" aria-owns="${loneId}" tabindex="${tab(UNASSIGNED)}" data-deck-item="${UNASSIGNED}" data-deck-toggle="${UNASSIGNED}">${chevronNode(UNASSIGNED)}<span class="deck-bot-name">Unassigned</span></div><ul role="group" id="${loneId}"${lone.expanded ? '' : ' hidden'}>${lone.projects.map(projectRow).join('')}</ul></div>` : '';
  return folders + unassigned || `<p class="deck-rail-empty">${artHTML('rail')}${!options.treeState || options.treeState === 'ready' ? 'No bots yet' : options.treeState === 'unavailable' ? 'Could not load bots' : 'Still loading bots…'}</p>`;
}
export function shellThreads({ directory = {} } = {}) {
  return rows(directory.conversations).map(item => `<a class="deck-thread-row" href="${conversationHref(item.id)}">${e(item.title || 'Conversation')}</a>`).join('');
}
// Everything that is not one of the places: reachable from the account menu (desktop) and the account sheet (phone), same rows in both.
// Projects is not here: it is the sidebar heading on desktop and a part of Work on a phone.
// The account menu and the phone's More sheet list the same places, in three labelled groups.
const menuGroup = (label, items) => items ? `<p class="deck-menu-group">${label}</p>${items}` : '';
const accountLinks = options => {
  const { can_admin, can_manage_instance } = options, at = String(options.hash || '');
  return menuGroup('Workspace', `<a href="#/settings" data-nav="settings"${current(options.section === 'settings')}>Settings</a><a href="#/compute" data-nav="compute"${current(at === '#/compute' || at.startsWith('#/compute/'))}>Cloud workspaces</a><a href="#/rules">Rules</a><a href="#/console">Console</a><a href="#/pipeline">Pipeline</a>${can_admin === true ? '<a href="#/admin">Admin</a>' : ''}`)
    + menuGroup('Server', can_manage_instance === true ? '<a href="#/instance-admin">Server admin</a>' : '')
    + menuGroup('Account', '<button data-action="shortcuts">Shortcuts</button><button data-action="switch-workspace">Switch workspace</button><button data-action="logout">Sign out</button>');
};
const roleLabel = options => ({ owner: 'Owner', member: 'Member' }[options.role]);
// The routes that live in the account menu and the phone More sheet, so the phone's menu button can show the person's place.
const MORE_ROUTES = /^#\/(?:settings|compute|rules|console|pipeline|admin|instance-admin)(?:[/?]|$)/;
export function renderShellBar(options) {
  const { workspace = 'Commonplace', user = '', hash = '' } = options, section = options.section ?? placeOf(hash);
  const role = roleLabel(options);
  const offered = id => options.sections?.some(([place]) => place === id);
  const threads = shellThreads(options), trees = shellTrees(options);
  // The Projects pages belong to Work on a phone (its tab stays selected), but the sidebar marks the Projects heading, not Work.
  const projectsPage = /^#\/(?:boards|start)(?:[/?]|$)/.test(hash);
  return `<nav class="rail" data-deck="bar" aria-label="Workspace">
    <div class="deck-brand-row"><a class="deck-brand" href="#/" aria-label="Commonplace home">${brand}<b>Commonplace</b></a></div>
    <button type="button" class="deck-find" id="cmdk" data-action="palette" aria-keyshortcuts="Control+K Meta+K" aria-label="Search or do">${icon('search')}<span>Search</span><kbd aria-hidden="true">Ctrl K</kbd></button>
    <a class="deck-new-thread nav" href="#/bots" data-deck-shell-action="new-thread">${icon('edit')}<span class="deck-row-label">New chat</span></a>
    <div class="deck-links">${options.homeOn === false ? '' : `<a class="nav" href="#/" data-nav="home"${current(section === 'home')}>${icon('home')}<span class="deck-row-label">Waiting on you</span>${badge(waiting(options), 'need', 'waiting on you')}</a>`}${offered('work') ? `<a class="nav" href="#/work" data-nav="work"${current(section === 'work' && !projectsPage)}>${icon('work')}<span class="deck-row-label">Work</span></a>` : ''}<a class="nav" href="#/memory"${current(section === 'memory')}>${icon('memory')}<span class="deck-row-label">Memory</span></a></div>
    <div class="deck-rail-scroll"><div class="deck-rail-group"><a class="deck-rail-heading" href="#/bots" data-nav="bots"${current(section === 'bots' && !shellBots(options).some(bot => { const href = botHref(bot); return hash === href || hash.startsWith(`${href}/`) || hash.startsWith(`${href}?`); }))}>Bots<span data-deck-unread>${badge(shellUnread(options), 'unread', 'unread')}</span></a><a class="deck-new-bot" href="#/bots?new=1" aria-label="New bot">+</a></div><div data-deck-bots${treeAttrs(trees)}>${trees}</div><h2 class="deck-rail-heading" data-deck-threads-heading${threads ? '' : ' hidden'}>Threads</h2><div data-deck-threads>${threads}</div><p class="deck-rail-error" data-deck-rail-error hidden></p><p class="deck-rail-error" data-deck-tree-note role="status" hidden></p>${rows(options.boards).length || projectsPage ? `<div class="deck-rail-group"><a class="deck-rail-heading deck-projects-heading" href="#/boards"${current(projectsPage)}>Projects</a></div>` : ''}</div>
    <div class="rail-foot"><details class="user-menu"><summary class="me" aria-label="${e(user || 'Account')}"><span class="mark" aria-hidden="true">${e(initials(user))}</span><span class="deck-owner"><b>${e(user || 'Account')}</b><small>${role ? `${role}, ` : ''}${e(workspace)}</small></span>${icon('chevron')}</summary><div class="user-links">${accountLinks(options)}</div></details><a class="deck-settings-link" href="#/settings" data-nav="settings"${current(section === 'settings')} aria-label="Settings" title="Settings">${icon('settings')}</a></div>
  </nav>`;
}
// Pages that are not one of the places still need a phone title, because the page heading hides on a phone.
const PAGE_TITLES = { '#/bots/-/coworkers': 'Conversations', '#/memory': 'Memory', '#/pipeline': 'Pipeline', '#/needs': 'Waiting on you', '#/boards': 'Projects', '#/start': 'Projects', '#/console': 'Console', '#/rules': 'Rules', '#/admin': 'Admin', '#/instance-admin': 'Server admin', '#/compute': 'Cloud workspaces', '#/settings': 'Settings', '#/work': 'Work', '#/bots': 'Bots' };
export function renderShellTop(options) {
  const { hash = '', user = '', board = null } = options;
  const page = Object.entries(PAGE_TITLES).find(([prefix]) => hash === prefix || hash.startsWith(`${prefix}/`) || hash.startsWith(`${prefix}?`))?.[1];
  const label = page || (board?.slug ? board.name || 'Board' : '') || (hash.startsWith('#/board') ? 'Board' : '') || (isHome(hash) || hash === '#/today' ? 'Waiting on you' : 'Commonplace');
  return `<header class="m-top" data-deck="top"><button type="button" class="deck-phone-avatar" data-deck-shell-action="more" aria-haspopup="dialog" aria-controls="deck-more" aria-label="More: settings, cloud workspaces and account"${current(MORE_ROUTES.test(hash))} data-deck-waiting="false">${icon('menu')}</button><b>${e(label)}</b><button class="iconbtn" data-action="palette" aria-label="Search or do (command palette)">${icon('search')}</button></header>`;
}
// The phone has no sidebar, so every place outside the three tabs lives in this sheet, opened from the account button.
// Any click inside it (a link, a button or the scrim) closes it; the shell's click handler does the work.
export const renderShellMore = options => `<div class="deck-more" id="deck-more" aria-label="More" data-deck-shell-action="more-close"${options.moreOpen === true ? ' role="dialog"' : ' hidden'}><div class="deck-more-panel"><span class="deck-more-grabber" aria-hidden="true"></span><button type="button" class="deck-more-close" data-deck-shell-action="more-close" aria-label="Close">${icon('close')}</button><p class="deck-more-account"><b>${e(options.user || 'Account')}</b> · ${roleLabel(options) ? `${roleLabel(options)}, ` : ''}${e(options.workspace || 'Commonplace')}</p>${accountLinks(options)}</div></div>`;
export function renderShellTabs(options) {
  const tabs = phoneTabs(options);
  return `<nav class="m-tabs" data-deck="tabs" data-count="${tabs.length}" aria-label="Main">${tabs.map(({ id, label, href, on }) => `<a href="${href}" data-nav="${id}"${current(on)}>${icon(id)}${label}${id === 'home' ? badge(waiting(options), 'need', 'waiting on you') : ''}</a>`).join('')}</nav>`;
}
const micButton = className => `<button type="button" class="${className}" data-deck-shell-action="mic" aria-label="Voice input">${icon('mic')}</button>`;
const composerSelector = ':is(#content, #card-panel) :is(.bh-form, .bt-reply form, .today-compose, .bots-compose, .cw-composer, .cb-composer, .composer:has([name="text"]), .space-compose, .space-composer, [data-form="comment"], [data-form="reply"])';

const hashNow0 = () => globalThis.location?.hash || '';
export function createDeckShell({ root, api, context, on, storage = null }) {
  let bots = [], directory = {}, identity = '', generation = 0, pending = null, error = '', rosterReady = false, directoryReady = false, syncedAt = 0;
  const closed = new Set();
  // Folded tree rows are remembered per device; `manual` holds the rows folded or opened by hand on the current page, so the page's own row opens again on the next route.
  const collapsed = loadCollapsed(storage), manual = new Set();
  let manualAt = hashNow0(), moving = null, moveNote = '';
  const composer = () => [...(root?.querySelectorAll?.(composerSelector) || [])].find(form => !form.closest('[hidden]') && form.getClientRects().length);
  // The sheet stays open across the shell's refreshes, and closes when the route changes.
  let moreAt = null;
  const hashNow = hashNow0;
  // role="dialog" is present only while the sheet is open, so a closed sheet is never mistaken for a dialog.
  function showMore(sheet, open) {
    moreAt = open ? hashNow() : null;
    sheet.hidden = !open;
    if (open) sheet.setAttribute('role', 'dialog'); else sheet.removeAttribute('role');
    root.querySelector('.deck-phone-avatar')?.setAttribute('aria-expanded', String(open));
    if (open) sheet.querySelector('a, button')?.focus();
  }
  const moreOpen = () => moreAt !== null && moreAt === hashNow();
  const options = () => {
    // A new page lets its own row open again; rows folded by hand on the previous page keep their saved state.
    if (manualAt !== hashNow()) { manualAt = hashNow(); manual.clear(); moving = null; }
    return { bots, directory, closed, collapsed, manual, moving, rosterReady, boards: rows(context?.()?.boards), treeState: error ? 'unavailable' : rosterReady && directoryReady ? 'ready' : 'loading', hash: hashNow(), hasComposer: !!composer(), moreOpen: moreOpen(), needsQueue: context?.()?.needs || {} };
  };
  function chrome() {
    if (!on()) return;
    const form = composer();
    if (form) {
      const ownMic = form.querySelector('[data-deck-shell-action="mic"]');
      const voiceMic = form.querySelector('[data-voice="mic"], [data-today="mic"], [data-bot-action="mic"]');
      if (voiceMic) ownMic?.remove();
      else if (!ownMic) (form.querySelector('.bh-toolbar, .cb-compose-bar') || form).insertAdjacentHTML('beforeend', micButton('deck-composer-mic'));
    }
  }
  if (root?.ownerDocument && globalThis.MutationObserver) new MutationObserver(chrome).observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden', 'open'] });
  // A host is repainted only when its markup changed, and keyboard focus stays on the same row across the repaint.
  const painted = new WeakMap();
  const itemOf = (host, key) => [...host.querySelectorAll('[data-deck-item]')].find(item => item.dataset.deckItem === key);
  function fill(host, html) {
    if (painted.get(host) === html) return;
    const active = root.ownerDocument?.activeElement, key = active && host.contains?.(active) ? active.closest?.('[data-deck-item]')?.dataset?.deckItem : undefined;
    host.innerHTML = html;
    painted.set(host, html);
    if (key !== undefined) itemOf(host, key)?.focus?.();
    if (html.includes('role="treeitem"')) { host.setAttribute?.('role', 'tree'); host.setAttribute?.('aria-label', 'Bots and projects'); } else { host.removeAttribute?.('role'); host.removeAttribute?.('aria-label'); }
  }
  function paint() {
    const hosts = ['[data-deck-bots]', '[data-deck-bots-page]'].map(selector => root?.querySelector?.(selector)).filter(Boolean), threads = root?.querySelector?.('[data-deck-threads]');
    for (const host of hosts) fill(host, shellTrees({ ...options(), scope: host.hasAttribute?.('data-deck-bots-page') ? 'page' : 'rail' }));
    if (threads && threads.innerHTML !== shellThreads(options())) threads.innerHTML = shellThreads(options());
    const heading = root?.querySelector?.('[data-deck-threads-heading]');
    if (heading) heading.hidden = !threads?.innerHTML;
    const unread = root?.querySelector?.('[data-deck-unread]');
    if (unread) unread.innerHTML = badge(shellUnread(options()), 'unread', 'unread');
    const status = root?.querySelector?.('[data-deck-rail-error]');
    if (status) { status.hidden = !error; status.innerHTML = error ? 'Could not load the bot tree. <button data-deck-shell-action="retry">Retry</button>' : ''; }
    for (const note of root?.querySelectorAll?.('[data-deck-tree-note]') || []) { note.hidden = !moveNote; note.textContent = moveNote; }
  }
  function reset() { generation++; bots = []; directory = {}; closed.clear(); pending = null; error = ''; rosterReady = directoryReady = false; syncedAt = 0; }
  function identify() {
    const me = context?.()?.me, key = JSON.stringify([me?.actor, me?.workspace]);
    if (key !== identity) { reset(); identity = key; }
  }
  async function sync({ cached = false } = {}) {
    if (!on() || !api) return;
    identify();
    if (pending) return pending;
    if (cached && syncedAt && Date.now() - syncedAt < 2000) return;
    const token = generation;
    pending = (async () => {
      const results = await Promise.allSettled([api('/api/coworkers/bots', { timeoutMs: 10000 }), api('/api/coworkers/conversations', { timeoutMs: 10000 })]);
      if (token !== generation || !on()) return;
      // Failed refreshes retain known rows; unknown lists never become empty.
      if (results[0].status === 'fulfilled') { directory = { ...directory, ...results[0].value }; directoryReady = true; }
      else if ([401, 403, 404].includes(results[0].reason?.status)) { directory = { conversations: directory.conversations }; directoryReady = false; }
      if (results[1].status === 'fulfilled') directory = { ...directory, conversations: rows(results[1].value?.conversations) };
      else if ([401, 403, 404].includes(results[1].reason?.status)) directory = { ...directory, conversations: [] };
      error = results.some(result => result.status === 'rejected') ? 'unavailable' : '';
      paint();
    })().finally(() => { if (token === generation) { pending = null; syncedAt = Date.now(); } });
    return pending;
  }
  // The tree: fold and unfold rows, move a project under another bot, and the keyboard rules of a tree.
  const hostOf = target => target?.closest?.('[data-deck-bots], [data-deck-bots-page]');
  const items = host => [...host.querySelectorAll('[role="treeitem"]')].filter(item => item.getAttribute('aria-disabled') !== 'true' && !item.closest('[hidden]'));
  const holderOf = slug => { for (const [id, projects] of projectTree({ bots: shellBots(options()), boards: options().boards }).under) if (projects.some(project => project.slug === slug)) return id; return ''; };
  function fold(node, open) {
    if (open) collapsed.delete(node); else collapsed.add(node);
    manual.add(node);
    saveCollapsed(storage, collapsed);
    paint();
  }
  const refocus = (host, key) => { if (host?.isConnected !== false) itemOf(host, key)?.focus?.(); };
  function startMove(host, slug) {
    moving = moving === slug ? null : slug; moveNote = '';
    paint();
    if (moving) host.querySelector('select[data-deck-move-select]')?.focus?.(); else refocus(host, `project:${slug}`);
  }
  // One request, then the roster is read back so the tree shows what the server holds, not what was asked.
  async function move(host, slug, target) {
    const before = holderOf(slug);
    moving = null; moveNote = '';
    try {
      if (target !== before) {
        if (target) await api(`/api/bots/${enc(target)}/projects/${enc(slug)}`, { method: 'PUT', timeoutMs: 10000 });
        else await api(`/api/bots/${enc(before)}/projects/${enc(slug)}`, { method: 'DELETE', timeoutMs: 10000 });
        const fresh = await api('/api/bots', { timeoutMs: 10000 });
        if (Array.isArray(fresh?.bots)) { bots = fresh.bots; context?.()?.onRoster?.(fresh.bots); }
      }
    } catch { moveNote = 'Could not move the project. Try again.'; }
    paint();
    refocus(host, `project:${slug}`);
  }
  root?.addEventListener?.('change', event => {
    const select = event.target?.closest?.('select[data-deck-move-select]'), host = hostOf(select);
    if (select && host && on()) void move(host, select.dataset.deckMoveSelect, select.value);
  });
  root?.addEventListener?.('focusin', event => {
    const item = event.target?.closest?.('[role="treeitem"]'), host = hostOf(item);
    if (!host) return;
    for (const other of host.querySelectorAll('[role="treeitem"][tabindex="0"]')) if (other !== item) other.setAttribute('tabindex', '-1');
    item.setAttribute('tabindex', '0');
  });
  root?.addEventListener?.('keydown', event => {
    const host = hostOf(event.target);
    if (!host || !on() || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.target.closest?.('select')) {
      if (event.key === 'Escape') { const slug = moving; moving = null; paint(); if (slug) refocus(host, `project:${slug}`); }
      return;
    }
    const item = event.target.closest?.('[role="treeitem"]');
    if (!item) return;
    const key = item.dataset.deckItem || '';
    if (key.startsWith('project:') && (event.key.toLowerCase() === 'm' || event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10'))) { event.preventDefault(); startMove(host, key.slice(8)); return; }
    const folds = item.hasAttribute('aria-expanded');
    if (folds && (event.key === 'Enter' || event.key === ' ') && item.dataset.deckToggle !== undefined) { event.preventDefault(); fold(key, item.getAttribute('aria-expanded') !== 'true'); refocus(host, key); return; }
    const list = items(host), result = treeKey(event.key, list.map(row => ({ level: Number(row.getAttribute('aria-level')), expandable: row.hasAttribute('aria-expanded'), expanded: row.getAttribute('aria-expanded') === 'true' })), list.indexOf(item));
    if (!result) return;
    event.preventDefault();
    if (result.focus !== undefined) list[result.focus]?.focus?.();
    else { fold(key, result.toggle); refocus(host, key); }
  });
  root?.addEventListener?.('toggle', event => {
    if (event.target?.isConnected === false) return;
    const folder = event.target?.dataset?.deckFolder;
    if (folder !== undefined) { if (event.target.open) closed.delete(folder); else closed.add(folder); }
  }, true);
  root?.addEventListener?.('click', event => {
    // Choosing anything in the account menu closes it; the page it opens is the answer.
    event.target?.closest?.('.user-links a, .user-links button')?.closest?.('details')?.removeAttribute?.('open');
    const chevron = event.target?.closest?.('[data-deck-toggle]'), mover = event.target?.closest?.('[data-deck-move]'), host = hostOf(chevron || mover);
    if (host && on()) {
      event.preventDefault();
      if (chevron) { const row = chevron.closest('[role="treeitem"]'), key = row?.dataset?.deckItem; if (key) { fold(key, row.getAttribute('aria-expanded') !== 'true'); refocus(host, key); } } else startMove(host, mover.dataset.deckMove);
      return;
    }
    const action = event.target?.closest?.('[data-deck-shell-action]')?.dataset.deckShellAction;
    if (!action || !on()) return;
    const sheet = root.querySelector('#phone-tabs .deck-more');
    if (action === 'more' && sheet) { event.preventDefault(); showMore(sheet, sheet.hidden); }
    if (action === 'more-close' && sheet) showMore(sheet, false);
    if (action === 'retry') { event.preventDefault(); void sync(); }
    // New chat opens the General bot with its reply box ready; without one it falls back to the Bots list.
    const general = action === 'new-thread' && shellBots(options()).find(bot => bot?.kind === 'general');
    if (general) {
      event.preventDefault();
      if (globalThis.location) globalThis.location.hash = botHref(general);
      let tries = 0;
      // The bot page renders after the route change and may redraw once, so wait until its box is shown and kept focus.
      const focus = () => {
        const box = [...root.querySelectorAll('#content textarea[name="text"]')].find(item => item.getClientRects().length);
        if (box && root.ownerDocument?.activeElement !== box) box.focus();
        if (++tries < 40 && (!box || tries < 10)) setTimeout(focus, 50);
      };
      setTimeout(focus, 0);
    }
    if (action === 'mic') {
      event.preventDefault();
      const mic = [...(root.querySelectorAll?.('#content [data-voice="mic"], #content [data-today="mic"], #content [data-bot-action="mic"]') || [])].find(button => !button.disabled);
      if (mic) mic.click(); else if (context?.()?.voice) void context().voice(composer()?.querySelector('[name="text"], [name="reply"], textarea[data-draft], input[data-draft], .cb-composer textarea')); else context?.()?.go?.('#/');
    }
  });
  // A route render can move focus to body while its lazy controller finishes.
  (root?.ownerDocument || root)?.addEventListener?.('keydown', event => {
    const sheet = root.querySelector('#phone-tabs .deck-more');
    if (event.key !== 'Escape' || !sheet || sheet.hidden) return;
    showMore(sheet, false); root.querySelector('.deck-phone-avatar')?.focus();
  });
  root?.ownerDocument?.addEventListener?.('signout', () => { reset(); paint(); });
  return { options, sync, reset, roster(value) { identify(); bots = rows(value); rosterReady = true; paint(); } };
}
