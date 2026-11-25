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
    assert.deepEqual(links(html).map(([id]) => id), ['home', 'work', 'bots', 'compute', 'settings'], 'Waiting, Work, Bots, then the account menu, then Settings');
    for (const href of ['#/memory', '#/compute', '#/boards', '#/rules', '#/console', '#/pipeline']) assert.match(html, new RegExp(`href="${href}"`));
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
  const places = ['#/settings', '#/compute', '#/boards', '#/rules', '#/console', '#/pipeline', '#/admin', '#/instance-admin'];
  for (const href of places) assert.ok(bar.includes(`href="${href}"`), `the sidebar offers ${href}`);
  assert.deepEqual(sheetLinks(sheet), places, 'the sheet offers each of them, with the same routes');
  assert.match(sheet, /data-action="switch-workspace"/);
  assert.match(sheet, /data-action="logout"/);
  assert.deepEqual(sheetLinks(renderDeckMore({ hash: '#/' })), ['#/settings', '#/compute', '#/boards', '#/rules', '#/console', '#/pipeline']);
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
  assert.match(css, /\.deck-more-panel \{[^}]*left: 8px; right: 8px; bottom: calc\(76px/);
});
