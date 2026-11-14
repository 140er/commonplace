import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { renderDeckBar, renderDeckTop, renderDeckTabs, renderDeckMore, deckSections, deckSection } from '../commonplace/web/static/deck.js';

const entries = [['today', 'Today'], ['bots', 'Bots'], ['compute', 'Compute'], ['settings', 'Settings']];
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

test('the four Deck entries and existing route destinations remain unchanged', () => {
  assert.deepEqual(deckSections(), entries);
  for (const hash of ['#/', '#/today', '#/bots', '#/bots/personal/coworkers', '#/board', '#/compute', '#/memory', '#/settings']) {
    const html = renderDeckBar({ hash });
    assert.deepEqual(links(html), entries.map(([id]) => [id, `#/${id}`]));
    assert.match(html, /href="#\/board"/);
    assert.match(html, /href="#\/memory"/);
  }
  assert.equal(deckSection('#/bots/personal/coworkers'), 'bots');
});

test('only Today has an amber action count and Bots has a violet unread count', () => {
  const html = renderDeckBar({ needs: 3, rulesWaiting: 8, botsWaiting: 3, ...data });
  assert.match(html, /data-nav="today"[^>]*>[\s\S]*?class="count need">3/);
  assert.match(html, /data-nav="bots"[^>]*>[\s\S]*?class="count unread">2/);
  assert.equal((html.match(/class="count /g) || []).length, 3);
  assert.equal((html.match(/class="count need"/g) || []).length, 1);
  assert.equal((renderDeckBar({}).match(/class="count /g) || []).length, 0);
  const tabs = renderDeckTabs({ needs: 3, ...data });
  assert.equal((tabs.match(/class="count /g) || []).length, 1);
  assert.doesNotMatch(tabs, /unread/);
  assert.doesNotMatch(renderDeckTabs({ needs: 3, screens: {} }), /class="count /);
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

test('phone chrome has four destinations, Today badge, avatar wait dot and a mic only without a composer', () => {
  for (const hash of ['#/today', '#/bots/x/coworkers', '#/board', '#/compute', '#/memory', '#/settings']) {
    const tabs = renderDeckTabs({ hash, needs: 2, hasComposer: false });
    assert.deepEqual(links(tabs), entries.map(([id]) => [id, `#/${id}`]));
    assert.match(tabs, /data-deck-shell-action="mic"/);
  }
  for (const hash of ['#/today', '#/bots/x', '#/bots/x/coworkers', '#/board/work/chat', '#/memory']) assert.doesNotMatch(renderDeckTabs({ hash, hasComposer: true }), /data-deck-shell-action="mic"/);
  assert.doesNotMatch(renderDeckTabs({ hash: '#/' }), /data-deck-shell-action="mic"/);
  const top = renderDeckTop({ hash: '#/today', needs: 2, user: 'Owner' });
  assert.match(top, /<b>Today<\/b>/);
  assert.match(top, /data-deck-waiting="true"/);
  assert.match(top, /data-action="palette"/);
  assert.match(top, /data-action="switch-workspace"/);
  assert.match(renderDeckTop({ hash: '#/memory' }), /<b>Memory<\/b>/);
  assert.doesNotMatch(renderDeckTop({ board: { slug: 'work' } }), /<b>Board<\/b>/);
  const css = readFileSync(new URL('../commonplace/web/static/deck-shell.css', import.meta.url), 'utf8');
  assert.match(css, /grid-template-columns: 252px/);
  assert.match(css, /height: 52px/);
  assert.match(css, /height: 26px/);
  assert.match(css, /border-left: 1\.5px solid var\(--tree-line\)/);
  assert.match(css, /h1[^}]*display: none/s);
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
  assert.match(html, /Member · Studio/);
  assert.match(html, /href="#\/bots\?new=1" aria-label="New bot"/);
  assert.doesNotMatch(renderDeckBar({bots:[{id:'missing', name:'Missing',last:{at:'invalid'}}]}), /deck-bot-time|deck-tree-state/);
  assert.match(renderDeckBar({role:'owner',workspace:'Studio'}), /Owner · Studio/);
});

 test('rail destinations preserve registered Home and Today screens', async () => {
  const {SCREENS,deckHome}=await import('../commonplace/web/static/deck.js');
  const {resolveRoute}=await import('../commonplace/web/static/routes.js');
  assert.equal(deckHome(), '#/');
  assert.equal((await SCREENS.home()).TITLE, 'Commonplace');
  assert.equal((await SCREENS.today()).TITLE, 'Today');
  assert.equal(resolveRoute('#/',{deck:true}), '#/');
  assert.equal(resolveRoute('#/today',{deck:true}), '#/today');
  const html=renderDeckBar({hash:'#/today'});
  assert.match(html,/class="deck-brand" href="#\/"/);
  assert.match(html,/class="deck-new-thread nav" href="#\/"/);
  assert.doesNotMatch(renderDeckTabs({hash:'#/home'}), /data-deck-shell-action="mic"/);
});

const sheetLinks = html => [...html.matchAll(/<a href="([^"]+)"/g)].map(m => m[1]);
test('phone: every rail and account destination is reachable from the More sheet with its count', () => {
  const tabs = renderDeckTabs({ hash: '#/today', needs: 3 }), sheet = renderDeckMore({ hash: '#/today', needs: 3, can_admin: true, can_manage_instance: true });
  assert.match(tabs, /<button[^>]*class="deck-tab-more"[^>]*aria-expanded="false"[^>]*aria-controls="deck-more"[^>]*>[\s\S]*?More<\/button>/);
  assert.match(sheet, /<div class="deck-more" id="deck-more" role="dialog" aria-label="More"[^>]* hidden>/);
  const bar = renderDeckBar({ hash: '#/today', can_admin: true, can_manage_instance: true });
  const railTools = ['#/board', '#/memory', '#/needs', '#/boards', '#/console', '#/rules', '#/admin', '#/instance-admin'];
  for (const href of railTools) assert.ok(bar.includes(`href="${href}"`), `rail offers ${href}`);
  assert.deepEqual(sheetLinks(sheet), railTools, 'the sheet offers each of them, with the same routes');
  assert.match(sheet, /href="#\/needs">Needs me<span class="count need">3<span class="sr"> waiting on you/);
  assert.match(sheet, /data-action="switch-workspace"/);
  assert.match(sheet, /data-action="logout"/);
  assert.deepEqual(sheetLinks(renderDeckMore({ hash: '#/today' })), ['#/board', '#/memory', '#/needs', '#/boards', '#/console', '#/rules']);
  assert.doesNotMatch(renderDeckBar({ hash: '#/today', needs: 3 }), /deck-more|waiting on you<\/span><\/span><\/a><a href="#\/boards"/);
  assert.match(renderDeckTabs({ hash: '#/memory' }), /class="deck-tab-more"[^>]*aria-current="page"/);
  assert.doesNotMatch(renderDeckTabs({ hash: '#/today' }), /class="deck-tab-more"[^>]*aria-current/);
});

test('the More button opens and closes the sheet, any tap inside closes it, Escape returns focus', async () => {
  const { createDeckShell } = await import('../commonplace/web/static/deck-shell.js');
  const attrs = {}, focused = [];
  const sheet = { hidden: true, querySelector: () => ({ focus: () => focused.push('first') }) };
  const opener = { setAttribute: (key, value) => { attrs[key] = value; }, focus: () => focused.push('opener') };
  const nodes = { '#phone-tabs .deck-more': sheet, '.deck-tab-more': opener }, events = {};
  const shell = createDeckShell({ root: { querySelector: key => nodes[key], addEventListener: (name, cb) => { events[name] = cb; } }, context: () => ({}), on: () => true });
  const tap = action => events.click({ target: { closest: () => ({ dataset: { deckShellAction: action } }) }, preventDefault() {} });
  tap('more');
  assert.equal(sheet.hidden, false); assert.equal(attrs['aria-expanded'], 'true'); assert.deepEqual(focused, ['first']);
  tap('more-close');
  assert.equal(sheet.hidden, true); assert.equal(attrs['aria-expanded'], 'false');
  tap('more'); events.keydown({ key: 'Escape' });
  assert.equal(sheet.hidden, true); assert.equal(focused.at(-1), 'opener');
  tap('more'); events.keydown({ key: 'a' }); assert.equal(sheet.hidden, false);
  tap('more-close'); tap('more');
  assert.equal(shell.options().moreOpen, true, 'a refresh redraws the sheet open');
  globalThis.location = { hash: '#/memory' };
  assert.equal(shell.options().moreOpen, false, 'a route change closes it');
  delete globalThis.location;
  assert.doesNotMatch(renderDeckMore({ moreOpen: true }), /id="deck-more"[^>]* hidden/);
  assert.match(renderDeckTabs({ moreOpen: true }), /aria-expanded="true"/);
});

test('phone CSS: five tab columns and a sheet fixed inside the viewport', () => {
  const css = readFileSync(new URL('../commonplace/web/static/deck-shell.css', import.meta.url), 'utf8');
  assert.match(css, /grid-template-columns: repeat\(5, minmax\(0, 1fr\)\) 46px/);
  assert.match(css, /\.deck-more \{[^}]*position: fixed; inset: 0/);
  assert.match(css, /\.deck-more-panel \{[^}]*left: 8px; right: 8px; bottom: calc\(76px/);
});
