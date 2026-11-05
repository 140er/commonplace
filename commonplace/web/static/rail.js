import { escape as e } from './api.js';

const enc = encodeURIComponent;
const count = (n) => Number.isSafeInteger(n) && n > 0 ? n : 0;
const current = (yes) => yes ? ' aria-current="page"' : '';
const paths = {
  search: '<circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5 14 14"/>',
  needs: '<path d="M2 9h3.5l1 2h3l1-2H14M3.8 3h8.4L14 9v4H2V9z"/>',
  memory: '<path d="M3.5 2.5h6.5L12.5 5v8.5h-9zM5.8 7h4.4M5.8 9.5h4.4M5.8 12h2.4"/>',
  board: '<rect x="2" y="2.5" width="3.4" height="11" rx=".6"/><rect x="6.3" y="2.5" width="3.4" height="7.5" rx=".6"/><rect x="10.6" y="2.5" width="3.4" height="9" rx=".6"/>',
  chat: '<path d="M2.5 3.5h11v7H7.2L4.3 13v-2.5H2.5z"/>',
  settings: '<path d="M2 4.5h7M12.5 4.5H14M2 11.5h1.5M6.5 11.5H14"/><circle cx="10.8" cy="4.5" r="1.6"/><circle cx="5" cy="11.5" r="1.6"/>',
  keys: '<rect x="1.5" y="4" width="13" height="8" rx="1.5"/><path d="M4 7h.1M6.5 7h.1M9 7h.1M11.5 7h.1M5 9.5h6"/>',
  chevron: '<path d="m4.5 6 3.5 3.5L11.5 6"/>',
};
const icon = (name) => `<svg class="i" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]}</svg>`;
const badge = (n, label) => count(n) ? `<span class="count need" aria-label="${n} ${label}">${n}</span>` : '';
const boardHref = (board) => board?.slug ? `#/board/${enc(board.slug)}` : '#/start';

export function projectSubline(board) {
  const agents = count(board.agents_working);
  const doing = count(board.counts?.doing);
  return agents ? `${agents} agent${agents === 1 ? '' : 's'} working` : doing ? `${doing} doing` : 'Idle';
}

// Pure markup: the shell delegates data-action clicks and owns navigation/state.
// needsByBoard contains attention counts, never the whole Review column count.
export function renderRail({ workspace = 'Commonplace', user = '', boards = [], board = null, view = 'board', needs = 0, needsByBoard = {} } = {}) {
  return `${railStyles}<nav class="rail" aria-label="Workspace">
    <button class="ws" data-action="switch-workspace" aria-label="Workspace: ${e(workspace)}. Switch workspace"><span class="mark" aria-hidden="true">${e(Array.from(workspace)[0] || 'C')}</span>${e(workspace)}${icon('chevron')}</button>
    <button class="cmdk" id="cmdk" data-action="palette" aria-keyshortcuts="Control+K Meta+K">${icon('search')}<span>Search or do…</span><kbd>⌘/Ctrl K</kbd></button>
    <a class="nav" href="#/needs" data-nav="needs"${current(view === 'needs')}>${icon('needs')}Needs me ${badge(needs, 'items')}</a>
    <a class="nav" href="#/memory" data-nav="memory"${current(view === 'memory')}>${icon('memory')}Memory</a>
    <div class="rail-h"><span id="ph">Projects</span><button class="iconbtn" id="new-project" data-action="new-board" aria-label="New project">+</button></div>
    <div role="list" aria-labelledby="ph">${boards.map((b) => `<div role="listitem"><a class="proj" href="${boardHref(b)}"${current(['board', 'chat', 'settings', 'space'].includes(view) && b.slug === board?.slug)}><span class="sw" aria-hidden="true"></span><span class="pn"><b>${e(b.name || b.slug)}</b><small>${projectSubline(b)}</small></span>${badge(needsByBoard[b.slug], 'need you')}</a></div>`).join('')}</div>
    <div class="rail-foot"><button class="nav" id="shortcuts-link" data-action="shortcuts">${icon('keys')}Shortcuts <kbd>?</kbd></button>
    <details class="user-menu"><summary class="me"><span class="mark" aria-hidden="true">${e(Array.from(user)[0] || '?')}</span>${e(user || 'Account')}</summary><div class="user-links"><a href="#/rules">Rules</a><a href="#/console">Console</a><a href="#/settings">Workspace settings</a><button data-action="logout">Sign out</button></div></details></div>
  </nav>`;
}

export function renderPhoneBar({ board = null } = {}) {
  const name = board?.name || board?.slug || 'Projects';
  return `<header class="m-top"><button class="projbtn" data-action="switch-project" aria-label="Project: ${e(name)}. Switch project"><span class="sw" aria-hidden="true"></span>${e(name)}${icon('chevron')}</button><span class="spacer"></span><button class="iconbtn" data-action="palette" aria-label="Search or do (command palette)">${icon('search')}</button>${board?.slug ? `<button class="btn m-settings" id="m-settings" data-action="coordinator" data-board="${e(board.slug)}" aria-label="Settings">${icon('settings')}Settings</button>` : ''}</header>`;
}

export function renderPhoneTabs({ board = null, view = 'board', needs = 0, unread = 0 } = {}) {
  const n = count(unread);
  const name = board?.name || board?.slug || 'Project';
  const chatLabel = view === 'needs' ? `${name} chat` : 'Chat';
  const newLabel = n ? `${n} new repl${n === 1 ? 'y' : 'ies'}` : '';
  return `<nav class="m-tabs" aria-label="Main"><a href="${boardHref(board)}" data-nav="boards"${current(view === 'board')}>${icon('board')}Board</a><a href="${board?.slug ? `${boardHref(board)}/chat` : '#/start'}" id="m-chat-tab" data-nav="chat" aria-haspopup="dialog" aria-label="${e(chatLabel)}${n ? ` ${newLabel}` : ''}"${current(view === 'chat')}>${icon('chat')}${e(chatLabel)}${n ? `<span class="badge newb">${n} new<span class="sr"> repl${n === 1 ? 'y' : 'ies'}</span></span>` : ''}</a><a href="#/needs" data-nav="needs"${current(view === 'needs')}>${icon('needs')}Needs me${badge(needs, 'items')}</a><span class="sr" role="status">${n ? `${newLabel} in ${e(name)} chat` : ''}</span></nav>`;
}

export const railStyles = `<style>
.ws { display: flex; align-items: center; gap: 8px; width: 100%; padding: 4px 6px; min-height: 36px; border: 0; background: none; border-radius: var(--r); text-align: left; font-weight: 600; }
.mark { width: 24px; height: 24px; border-radius: 6px; background: var(--text); color: #FEFEFC; display: grid; place-items: center; font-size: 13px; font-weight: 700; flex: none; }
.ws .chev { margin-left: auto; color: var(--text-3); }
.cmdk { display: flex; align-items: center; gap: 8px; width: 100%; min-height: 36px; margin: 8px 0 10px; padding: 0 8px; border-radius: var(--r); border: 1px solid var(--line-strong); background: var(--surface); color: var(--text-2); text-align: left; }
.cmdk kbd { margin-left: auto; }
.nav { display: flex; align-items: center; gap: 10px; min-height: 34px; padding: 0 8px; border-radius: var(--r); color: var(--text); text-decoration: none; font-weight: 500; }
.nav .i { color: var(--text-2); }
.nav[aria-current] { background: var(--surface); box-shadow: inset 0 0 0 1px var(--line); font-weight: 600; }
.nav[aria-current]::before { content: ""; width: 3px; height: 18px; border-radius: 2px; background: var(--accent); margin-left: -8px; margin-right: -5px; }
.count { margin-left: auto; font: 600 12px/18px var(--font); min-width: 22px; text-align: center; padding: 0 6px; border-radius: 9px; }
.count.need { background: var(--wait-bg); color: var(--wait); box-shadow: inset 0 0 0 1px var(--wait-line); }
.count.quiet { color: var(--text-3); font-weight: 500; }
.rail-h { display: flex; align-items: center; justify-content: space-between; margin: 14px 0 2px; padding-left: 8px; font-size: 12px; font-weight: 600; color: var(--text-3); letter-spacing: .02em; }
.rail-h .iconbtn { width: 28px; height: 28px; }
.proj { display: flex; align-items: center; gap: 10px; min-height: 40px; padding: 4px 8px; border-radius: var(--r); color: var(--text); text-decoration: none; }
.proj .sw { width: 10px; height: 10px; border-radius: 3px; flex: none; }
.proj .pn { display: flex; flex-direction: column; min-width: 0; line-height: 16px; }
.proj .pn b { font-weight: 600; }
.proj .pn small { font-size: 12px; color: var(--text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.proj[aria-current] { background: var(--surface); box-shadow: inset 0 0 0 1px var(--line); }
.rail-foot { margin-top: auto; padding-top: 8px; border-top: 1px solid var(--line); display: flex; flex-direction: column; gap: 2px; }
.me { display: flex; align-items: center; gap: 10px; min-height: 36px; padding: 0 8px; font-weight: 500; }
.m-top { display: flex; align-items: center; gap: 4px; min-height: 56px; padding: 6px 8px 6px 12px; border-bottom: 1px solid var(--line); background: var(--bg); }
.m-top .projbtn { display: flex; align-items: center; gap: 8px; min-height: 44px; padding: 0 8px; border: 0; background: none; font-size: 17px; font-weight: 650; }
.m-top .iconbtn { width: 44px; height: 44px; position: relative; }
.m-top .iconbtn .i { width: 20px; height: 20px; }
.m-top .spacer { flex: 1; }
.m-tabs .badge { position: absolute; top: 4px; right: 2px; min-width: 18px; height: 18px; padding: 0 5px; border-radius: 9px; background: var(--wait-bg); color: var(--wait); box-shadow: inset 0 0 0 1px var(--wait-line); font-size: 11px; font-weight: 700; line-height: 18px; text-align: center; }
.m-tabs { display: grid; grid-template-columns: repeat(3, 1fr); border-top: 1px solid var(--line); background: var(--bg); padding-bottom: 14px; }
.m-tabs a { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px; min-height: 56px; font-size: 12px; font-weight: 600; color: var(--text-2); text-decoration: none; position: relative; }
.m-tabs a .i { width: 22px; height: 22px; }
.m-tabs a[aria-current] { color: var(--accent); }
.m-tabs a[aria-current]::before { content: ""; position: absolute; top: 0; left: 28%; right: 28%; height: 3px; border-radius: 0 0 3px 3px; background: var(--accent); }
.m-tabs .m-tabs .badge { top: 4px; right: calc(50% - 22px); }
.m-tabs .badge.newb { background: var(--accent-weak); color: var(--accent); box-shadow: inset 0 0 0 1px #AEB8EC; right: calc(50% - 52px); }
.rail .nav { border: 0; background: none; width: 100%; text-align: left; }
.rail .nav[aria-current] { background: var(--surface); }
.rail .nav kbd { margin-left: auto; }
.rail .sw, .m-top .sw { background: var(--accent); width: 10px; height: 10px; border-radius: 3px; flex: none; }
.rail .pn b { overflow-wrap: anywhere; }
.rail .me { cursor: pointer; }
.rail .user-links { display: flex; flex-direction: column; padding: 8px; gap: 4px; }
.rail .user-links a, .rail .user-links button { color: var(--text); background: none; border: 0; text-align: left; padding: 6px; min-height: 32px; }
.m-top, .m-tabs { display: none; }
@media (max-width: 699px) {
  .shell > .rail { display: none; }
  .m-top { display: flex; min-width: 0; }
  .m-top .projbtn { min-width: 0; overflow-wrap: anywhere; font-size: 17px; }
  .m-top .m-settings { min-height: 44px; }
  .m-tabs { display: grid; padding-bottom: max(14px, env(safe-area-inset-bottom)); }
  .m-tabs a { min-width: 0; overflow-wrap: anywhere; }
  .m-tabs .count { position: absolute; top: 4px; right: calc(50% - 22px); }
}
</style>`;
