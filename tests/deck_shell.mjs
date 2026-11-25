import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { renderDeckBar, renderDeckTop, renderDeckTabs, renderDeckMore, deckSections, deckSection } from '../commonplace/web/static/deck.js';
import { PHONE_TABS } from '../commonplace/web/static/deck-shell.js';

const entries = [['home', 'Waiting on you'], ['work', 'Work'], ['bots', 'Bots'], ['settings', 'Settings']];
const links = html => [...html.matchAll(/href="([^"]+)" data-nav="([^"]+)"/g)].map(m => [m[2], m[1]]);
const data = {
  bots: [{ id: 'personal', name: 'Personal bot', private: true, section: 'Operations', unread: 2, state: 'working', coworkers: [{ id: 'job', name: 'Release', state: 'waiting', conversation_id: 'release' }] }],
  directory: { bots: [{ id: 'persona', name: 'Research bot', section: 'Research', state: 'live' }], conversations: [{ id: 'notes', title: 'Saved research', bot_ids: ['persona'], kind: 'dm' }] },
};

test('bot avatars use one letter unless the roster has a first-letter collision', () => {
  const labels = bots => [...renderDeckBar({ bots }).matchAll(/class="deck-bot-avatar"[^>]*>([^<]+)</g)].map(match => match[1]);
  assert.deepEqual(labels([{ id: 'one', name: 'Release bot' }, { id: 'two', name: 'Compute bot' }]), ['R', 'C']);
  const bots = [{ id: 'one', name: 'Release bot' }, { id: 'two', name: 'Research bot' }, { id: 'three', name: 'Compute bot' }];
  assert.deepEqual(labels(bots), ['RE', 'RS', 'C']);
  assert.deepEqual(labels(bots.toReversed()), ['C', 'RS', 'RE']);
});

test('the places are Waiting on you, Work, Bots and Settings, and every older destination stays reachable', () => {
  assert.deepEqual(deckSections(), entries);
  for (const hash of ['#/', '#/today', '#/bots', '#/bots/personal/coworkers', '#/board', '#/work', '#/compute', '#/memory', '#/settings']) {
    const html = renderDeckBar({ hash });
    assert.deepEqual(links(html).map(([id]) => id), ['home', 'work', 'bots', 'settings', 'compute', 'settings'], 'Waiting, Work, Bots, then the account menu (Settings first), then the footer Settings gear');
    for (const href of ['#/memory', '#/compute', '#/rules', '#/console', '#/pipeline']) assert.match(html, new RegExp(`href="${href}"`));
    assert.match(html, /<div class="user-links"><a href="#\/settings" data-nav="settings"[^>]*>Settings<\/a>/, 'the account menu opens with a labelled Settings row');
    assert.doesNotMatch(html, /href="#\/boards"/, 'Projects is the sidebar heading, not an account menu row');
  }
  assert.equal(deckSection('#/bots/personal/coworkers'), 'bots');
  for (const [hash, section] of [['#/', 'home'], ['#/today', 'home'], ['#/work', 'work'], ['#/board/x', 'work'], ['#/card/3', 'work'], ['#/settings', 'settings'], ['#/rules', '']]) assert.equal(deckSection(hash), section, hash);
});

test('only Waiting on you has an amber count and Bots has a violet unread count', () => {
  const html = renderDeckBar({ needs: 3, rulesWaiting: 8, botsWaiting: 3, ...data });
  assert.match(html, /data-nav="home"[^>]*>[\s\S]*?class="count need">3/);
  assert.match(html, /data-nav="bots"[^>]*>[\s\S]*?class="count unread">2/);
  assert.equal((html.match(/class="count need"/g) || []).length, 1);
  assert.equal((renderDeckBar({}).match(/class="count /g) || []).length, 0);
  const tabs = renderDeckTabs({ needs: 3, ...data });
  assert.equal((tabs.match(/class="count /g) || []).length, 1);
  assert.doesNotMatch(tabs, /unread/);
});

test('folders and bot trees render live records, coworker states and private locks only', () => {
  const html = renderDeckBar({ ...data, hash: '#/bots/personal/coworkers' });
  assert.match(html, /data-deck-folder="Operations"/);
  assert.match(html, /data-deck-folder="Research"/);
  assert.match(html, /<details[^>]*data-deck-folder/);
  assert.match(html, /Personal bot/);
  assert.match(html, /Release/);
  assert.match(html, /deck-tree-row[\s\S]*?Needs you[\s\S]*?data-state="waiting"/);
  assert.equal((html.match(/aria-label="Private bot"/g) || []).length, 1);
  assert.match(html, /href="#\/bots\/-\/coworkers\/conversations\/release"/);
  assert.match(html, /Saved research/);
  assert.doesNotMatch(html, /undefined|\[object Object\]/);
});

test('older lists render directly under Bots and no invented state or unread counts', () => {
  const html = renderDeckBar({ bots: [{ id: 'one', name: 'One', shared: false }], directory: { bots: [{ id: 'two', name: 'Two' }], conversations: [] } });
  assert.doesNotMatch(html, /data-deck-folder=/);
  assert.match(html, /One/); assert.match(html, /Two/);
  assert.doesNotMatch(html, /data-state=|class="count |Private bot/);
  const escaped = renderDeckBar({ bots: [{ id: 'x', name: '<script>', section: 'A"<', state: 'arbitrary' }] });
  assert.match(escaped, /&lt;script&gt;/);
  assert.doesNotMatch(escaped, /<script>|data-state="arbitrary"/);
});

test('phone chrome has the three phone tabs, the Waiting count, an account button and a search button', () => {
  for (const hash of ['#/', '#/bots/x/coworkers', '#/work', '#/compute', '#/memory', '#/settings']) {
    const tabs = renderDeckTabs({ hash, needs: 2 });
    assert.deepEqual(links(tabs), PHONE_TABS.map(([id, , href]) => [id, href]));
    assert.doesNotMatch(tabs, /data-deck-shell-action="mic"/, 'the voice button lives in the composer, not the tab bar');
  }
  assert.match(renderDeckTabs({ hash: '#/work' }), /data-nav="work" aria-current="page"/);
  assert.match(renderDeckTabs({ hash: '#/bots/x' }), /data-nav="bots" aria-current="page"/);
  const top = renderDeckTop({ hash: '#/', needs: 2, user: 'Owner' });
  assert.match(top, /<b>Waiting on you<\/b>/);
  assert.match(top, /data-deck-shell-action="more"/);
  assert.match(top, /data-action="palette"/);
  assert.match(renderDeckTop({ hash: '#/memory' }), /<b>Memory<\/b>/);
  assert.match(renderDeckTop({ hash: '#/board/work', board: { slug: 'work', name: 'Work site' } }), /<b>Work site<\/b>/);
  const css = readFileSync(new URL('../commonplace/web/static/deck-system.css', import.meta.url), 'utf8');
  assert.match(css, /grid-template-columns: 236px minmax\(0, 1fr\)/);
  assert.match(css, /grid-template-columns: repeat\(4, minmax\(0, 1fr\)\)/);
});

test('shell refreshes only readable lists, keeps folder collapse and rejects stale account responses', async () => {
  const { createDeckShell } = await import('../commonplace/web/static/deck-shell.js');
  const nodes = Object.fromEntries(['[data-deck-bots]', '[data-deck-threads]', '[data-deck-unread]', '[data-deck-rail-error]'].map(key => [key, { innerHTML: '' }]));
  const events = {}, calls = [], pending = [];
  let me = { actor: 'user:one', workspace: 'one' }, active = true;
  const shell = createDeckShell({ root: { querySelector: key => nodes[key], addEventListener: (name, cb) => { events[name] = cb; } }, context: () => ({ me }), on: () => active,
    api: path => { calls.push(path); return new Promise((resolve, reject) => pending.push({ resolve, reject })); } });
  shell.roster([{ id: 'one', name: 'One' }]);
  const load = shell.sync();
  void shell.sync();
  assert.equal(calls.length, 2, 'concurrent navigation coalesces the directory request');
  pending[0].resolve({ bots: [{ id: 'two', name: 'Two', section: 'Work', state: 'busy' }] });
  pending[1].resolve({ conversations: [{ id: 'private', title: 'Private conversation' }] });
  await load;
  assert.match(nodes['[data-deck-bots]'].innerHTML, /One/);
  assert.match(nodes['[data-deck-bots]'].innerHTML, /Two/);
  events.toggle({ target: { dataset: { deckFolder: 'Work' }, open: false } });
  shell.roster([{ id: 'one', name: 'One' }]);
  assert.match(nodes['[data-deck-bots]'].innerHTML, /data-deck-folder="Work"><summary>/);
  const stale = shell.sync();
  me = { actor: 'user:two', workspace: 'two' };
  const fresh = shell.sync();
  pending[2].resolve({ bots: [{ id: 'old', name: 'Stale private bot' }] });
  pending[3].resolve({ conversations: [{ id: 'old', title: 'Stale private thread' }] });
  pending[4].resolve({ bots: [] }); pending[5].resolve({ conversations: [] });
  await Promise.all([stale, fresh]);
  assert.doesNotMatch(nodes['[data-deck-bots]'].innerHTML, /One|Two|Stale/);
  assert.doesNotMatch(nodes['[data-deck-threads]'].innerHTML, /Private|Stale/);
  const failed = shell.sync(); pending[6].reject(new Error('forbidden')); pending[7].reject(new Error('forbidden')); await failed;
  assert.equal(nodes['[data-deck-rail-error]'].hidden, false);
  active = false; await shell.sync(); assert.equal(calls.length, 8);
});

 test('the workspace Board link resolves through the existing readable-board selection', async () => {
  const { resolveRoute } = await import('../commonplace/web/static/routes.js');
  assert.equal(resolveRoute('#/board', { deck: true, boards: [{ slug: 'one' }, { slug: 'two' }], last: 'two' }), '#/board/two');
  assert.equal(resolveRoute('#/board', { deck: true, boards: [] }), '#/start');
  assert.equal(resolveRoute('#/board', { deck: false, boards: [{ slug: 'one' }] }), '#/board');
});

 test('bot tails prefer unread over recorded time; child states and real roles are optional', () => {
  const html = renderDeckBar({ role: 'member', user: 'Account person', workspace: 'Studio', bots: [
    { id: 'time', name: 'Timed bot', last: { at: new Date().toISOString() }, coworkers: [
      {name:'Ready child',state:'ready'}, {name:'Healthy child',state:'healthy'},
      {name:'Busy child',state:'busy'}, {name:'Parked child',state:'parked'}, {name:'Unknown child'}] },
    { id: 'unread', name: 'Unread bot', unread: 3, last: {at: new Date().toISOString()} }
  ]});
  assert.equal((html.match(/deck-bot-time/g)||[]).length, 1);
  assert.match(html, /class="count unread">3/);
  assert.equal((html.match(/deck-tree-state/g)||[]).length, 4);
  for (const word of ['Ready', 'Healthy', 'Busy', 'Parked']) assert.match(html, new RegExp('deck-tree-state">'+word));
  assert.match(html, /Member, Studio/);
  assert.match(html, /href="#\/bots\?new=1" aria-label="New bot"/);
  assert.doesNotMatch(renderDeckBar({bots:[{id:'missing', name:'Missing',last:{at:'invalid'}}]}), /deck-bot-time|deck-tree-state/);
  assert.match(renderDeckBar({role:'owner',workspace:'Studio'}), /Owner, Studio/);
});

 test('Waiting on you is home and is offered first', async () => {
  const {SCREENS,deckHome}=await import('../commonplace/web/static/deck.js');
  const {resolveRoute}=await import('../commonplace/web/static/routes.js');
  assert.equal(deckHome(), '#/');
  assert.equal(await SCREENS.home(), await SCREENS.today(), 'Home and Today are the same Waiting screen');
  assert.equal((await SCREENS.work()).TITLE, 'Work');
  assert.equal(resolveRoute('#/',{deck:true}), '#/');
  const html=renderDeckBar({hash:'#/'});
  assert.match(html,/class="deck-brand" href="#\/"/);
  assert.match(html,/class="nav" href="#\/" data-nav="home" aria-current="page"/);
  assert.match(html,/class="deck-new-thread nav" href="#\/bots"/);
});

const sheetLinks = html => [...html.matchAll(/<a href="([^"]+)"/g)].map(m => m[1]);
test('phone: every place outside the three tabs is in the account sheet, with its count', () => {
  const sheet = renderDeckMore({ hash: '#/', needs: 3, can_admin: true, can_manage_instance: true });
  assert.match(sheet, /<div class="deck-more" id="deck-more" aria-label="More"[^>]* hidden>/);
  assert.doesNotMatch(sheet, /role="dialog"/, 'a closed sheet is not announced as a dialog');
  const bar = renderDeckBar({ hash: '#/', can_admin: true, can_manage_instance: true });
  const places = ['#/settings', '#/compute', '#/rules', '#/console', '#/pipeline', '#/admin', '#/instance-admin'];
  for (const href of places) assert.ok(bar.includes(`href="${href}"`), `the sidebar offers ${href}`);
  assert.deepEqual(sheetLinks(sheet), places, 'the sheet offers each of them, with the same routes');
  assert.match(sheet, /data-action="switch-workspace"/);
  assert.match(sheet, /data-action="logout"/);
  assert.deepEqual(sheetLinks(renderDeckMore({ hash: '#/' })), ['#/settings', '#/compute', '#/rules', '#/console', '#/pipeline']);
  assert.deepEqual(sheetLinks(sheet), sheetLinks(bar.slice(bar.indexOf('class="user-links"'))), 'the sheet and the desktop account menu offer the same rows');
  assert.match(renderDeckMore({ user: 'Maya', role: 'owner', workspace: 'Home' }), /<div class="deck-more-panel"><p class="deck-more-account"><b>Maya<\/b> · Owner, Home<\/p><a href="#\/settings"/, 'the sheet is headed by the account line, as the sidebar footer is');
  assert.doesNotMatch(sheet, /href="#\/boards"/, 'Projects is part of Work on a phone');
  assert.match(bar, /href="#\/memory"/, 'Memory is a place in the sidebar');
});

test('the More button opens and closes the sheet, any tap inside closes it, Escape returns focus', async () => {
  const { createDeckShell } = await import('../commonplace/web/static/deck-shell.js');
  const attrs = {}, focused = [];
  const sheet = { hidden: true, querySelector: () => ({ focus: () => focused.push('first') }), setAttribute(key, value) { attrs.role = value; }, removeAttribute(key) { delete attrs[key]; } };
  const opener = { setAttribute: (key, value) => { attrs[key] = value; }, focus: () => focused.push('opener') };
  const nodes = { '#phone-tabs .deck-more': sheet, '.deck-phone-avatar': opener }, events = {}, documentEvents = {};
  const shell = createDeckShell({ root: { ownerDocument: {addEventListener(name, cb) {documentEvents[name] = cb;}}, querySelector: key => nodes[key], addEventListener: (name, cb) => { events[name] = cb; } }, context: () => ({}), on: () => true });
  const tap = action => events.click({ target: { closest: () => ({ dataset: { deckShellAction: action } }) }, preventDefault() {} });
  tap('more');
  assert.equal(sheet.hidden, false); assert.equal(attrs.role, 'dialog'); assert.equal(attrs['aria-expanded'], 'true'); assert.deepEqual(focused, ['first']);
  tap('more-close');
  assert.equal(sheet.hidden, true); assert.equal(attrs.role, undefined); assert.equal(attrs['aria-expanded'], 'false');
  tap('more'); documentEvents.keydown({ key: 'Escape', target: {tagName:'BODY'} });
  assert.equal(sheet.hidden, true); assert.equal(focused.at(-1), 'opener');
  tap('more'); documentEvents.keydown({ key: 'a' }); assert.equal(sheet.hidden, false);
  tap('more-close'); tap('more');
  assert.equal(shell.options().moreOpen, true, 'a refresh redraws the sheet open');
  globalThis.location = { hash: '#/memory' };
  assert.equal(shell.options().moreOpen, false, 'a route change closes it');
  delete globalThis.location;
  assert.doesNotMatch(renderDeckMore({ moreOpen: true }), /id="deck-more"[^>]* hidden/);
  assert.match(renderDeckMore({ moreOpen: true }), /id="deck-more"[^>]* role="dialog"/);
});

test('phone CSS: five tab columns and a sheet fixed inside the viewport', () => {
  const css = readFileSync(new URL('../commonplace/web/static/deck-shell.css', import.meta.url), 'utf8');
  assert.match(css, /grid-template-columns: repeat\(5, minmax\(0, 1fr\)\) 54px/);
  assert.match(css, /\.deck-more \{[^}]*position: fixed; inset: 0/);
  assert.match(css, /\.deck-more-panel \{[^}]*left: 0; right: 0; bottom: calc\(64px[^}]*border-radius: 16px 16px 0 0/, 'a bottom sheet: full width, rounded top, on the tab bar');
});

test('the account menu and sheet call the server console "Server admin", and its page title agrees', () => {
  const opts = { hash: '#/', can_admin: true, can_manage_instance: true };
  for (const html of [renderDeckBar(opts), renderDeckMore(opts)]) {
    assert.match(html, /<a href="#\/instance-admin">Server admin<\/a>/);
    assert.doesNotMatch(html, /Instance console/);
  }
  assert.match(renderDeckTop({ hash: '#/instance-admin' }), /<b>Server admin<\/b>/);
  assert.match(renderDeckTop({ hash: '#/admin' }), /<b>Admin<\/b>/);
  const app = readFileSync(new URL('../commonplace/web/static/app.js', import.meta.url), 'utf8');
  assert.match(app, /const DECK_TITLES = \{ "Workspace administration": "Admin", "Instance console": "Server admin" \};/);
  assert.match(app, /dataset\.ui === "deck"\) text = DECK_TITLES\[text\] \|\| text;/);
});

test('the Projects pages mark the sidebar Projects heading, not Work, while a phone keeps Work selected', () => {
  const boards = [{ slug: 'demo', name: 'Demo' }];
  for (const hash of ['#/boards', '#/start']) {
    const bar = renderDeckBar({ hash, boards, sections: entries });
    assert.match(bar, /<a class="deck-rail-heading deck-projects-heading" href="#\/boards" aria-current="page">Projects<\/a>/, hash);
    assert.doesNotMatch(bar, /data-nav="work" aria-current/, hash);
    assert.doesNotMatch(bar, /data-current/, 'the account row is not the current place either');
    assert.match(renderDeckTabs({ hash }), /href="#\/work" data-nav="work" aria-current="page"/, `${hash}: the phone tab stays on Work`);
  }
  // With no project yet the heading still shows on the Projects page, so a nav item matches it.
  assert.match(renderDeckBar({ hash: '#/boards', boards: [], sections: entries }), /deck-projects-heading" href="#\/boards" aria-current="page"/);
  assert.doesNotMatch(renderDeckBar({ hash: '#/work', boards, sections: entries }), /deck-projects-heading[^>]*aria-current/);
  assert.match(renderDeckBar({ hash: '#/work', boards, sections: entries }), /data-nav="work" aria-current="page"/);
});

test('review fixes in the stylesheets: full-width bot bar, shared page column, readable checkboxes, one-line phone crumbs', () => {
  const read = name => readFileSync(new URL(`../commonplace/web/static/${name}`, import.meta.url), 'utf8');
  const system = read('deck-system.css');
  // A bot thread's bar bleeds to the same edges as every page bar and keeps its 1px rule inside 52px.
  assert.match(system, /#content > \.bots\[data-pane="thread"\] \{ width: auto; max-width: none; margin-inline: calc\(-1 \* var\(--page-side\)\); \}/);
  assert.match(system, /\.bots \.bots-thread > \.bots-head \{ box-sizing: border-box; height: 52px; min-height: 52px; \}/);
  assert.match(system, /\.bots-head \.deck-bots-strip \{ height: 51px; \}/);
  // Rules, Console and Pipeline use the shared page column, not a 560px one.
  for (const css of [system, read('deck-pipeline.css')]) assert.doesNotMatch(css, /560px/);
  assert.match(system, /\.console-empty \{[^}]*max-width: var\(--page-width\)/);
  assert.match(system, /\.rules-empty \{ max-width: var\(--page-width\)/);
  assert.match(read('deck-pipeline.css'), /\.pl-empty \{[^}]*max-width: var\(--page-width\)/);
  // Unchecked settings boxes are outlined in --control-line (over 3:1 on the card); checked stays violet.
  assert.match(system, /\.capy-settings input\[type="checkbox"\] \{[^}]*appearance: none;[^}]*border: 1\.5px solid var\(--control-line\)/);
  assert.match(system, /\.capy-settings input\[type="checkbox"\]:checked \{ border-color: var\(--violet\); background: var\(--violet\); \}/);
  // The phone bar names where you are, so the coworkers breadcrumb goes there.
  const coworkers = read('deck-coworkers.css');
  assert.match(coworkers.slice(coworkers.indexOf('@media (max-width: 699px)')), /\.cb-crumbs \{ display: none; \}/);
});

test('the Memory icon is a book in the sidebar and the phone tabs, not the old columns', () => {
  for (const html of [renderDeckBar({ hash: '#/' }), renderDeckTabs({ hash: '#/' })]) {
    assert.match(html, /href="#\/memory"[^>]*>\s*<svg[^>]*><path d="M3\.5 2h8A1\.5 1\.5 0 0 1 13 3\.5V14H5/s, 'a stroke book from the shared icon set');
    assert.doesNotMatch(html, /M2 14h12/);
  }
});

test('phone: the menu button is marked on the pages that live in the More sheet, and only there', () => {
  const marked = hash => /class="deck-phone-avatar"[^>]* aria-current="page"/.test(renderDeckTop({ hash }));
  for (const hash of ['#/settings', '#/compute', '#/compute/management', '#/rules', '#/console', '#/pipeline', '#/admin', '#/instance-admin']) assert.ok(marked(hash), hash);
  for (const hash of ['#/', '#/work', '#/bots', '#/memory', '#/boards', '#/board/x']) assert.ok(!marked(hash), hash);
  const css = readFileSync(new URL('../commonplace/web/static/deck-system.css', import.meta.url), 'utf8');
  assert.match(css, /\.m-top \.deck-phone-avatar\[aria-current\] \{[^}]*color: var\(--ink\)/);
});

test('the account row is never marked current, and choosing anything in its menu closes it', async () => {
  for (const hash of ['#/rules', '#/console', '#/admin', '#/pipeline', '#/compute', '#/settings']) assert.doesNotMatch(renderDeckBar({ hash }), /<summary class="me"[^>]*data-current/, hash);
  const css = readFileSync(new URL('../commonplace/web/static/deck-system.css', import.meta.url), 'utf8');
  assert.doesNotMatch(css, /\.me\[data-current\]/);
  const { createDeckShell } = await import('../commonplace/web/static/deck-shell.js');
  const removed = [], events = {};
  const details = { removeAttribute: key => removed.push(key) };
  createDeckShell({ root: { querySelector: () => null, addEventListener: (name, cb) => { events[name] = cb; } }, context: () => ({}), on: () => true });
  events.click({ target: { closest: selector => selector.startsWith('.user-links') ? { closest: () => details } : null }, preventDefault() {} });
  assert.deepEqual(removed, ['open'], 'a click on a menu row closes the details');
  events.click({ target: { closest: () => null }, preventDefault() {} });
  assert.deepEqual(removed, ['open'], 'a click elsewhere leaves it alone');
  const app = readFileSync(new URL('../commonplace/web/static/app.js', import.meta.url), 'utf8');
  assert.match(app, /\.open === true && state\.accountAt === state\.base;/, 'a redraw keeps the menu open only on the same route');
});

test('every desktop page starts 20px under the bar: pages that #content already pads add no second offset', () => {
  const css = readFileSync(new URL('../commonplace/web/static/deck-system.css', import.meta.url), 'utf8');
  assert.match(css, /#content > \.bt-bar \{[^}]*padding: 0 0 6px/, 'Waiting on you');
  assert.match(css, /> \.bots-roster \{[^}]*padding-top: 0/, 'Bots');
  assert.match(css, /#content \.admin\.settings \{ padding-top: 0; \}/, 'Admin');
  assert.match(css, /#content\.capy-coworkers \{ padding-top: 20px; \}/, 'Coworkers');
});
