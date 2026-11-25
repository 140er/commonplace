// The Work screen: lanes grouped like the Board, counts, project chips, "Show more", the empty lane, escaping and the mount contract.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { LANES, SHOWN, TITLE, cardHTML, chipsHTML, healthHTML, laneHTML, mount, openCounts, pageHTML, projectFrom, workHash, workItems } from '../commonplace/web/static/deck-work.js';
import { boardGroup } from '../commonplace/web/static/deck-board.js';
import { setNeedsQueue } from '../commonplace/web/static/needs-me.js';
import { SCREENS } from '../commonplace/web/static/deck.js';

const css = readFileSync(new URL('../commonplace/web/static/deck-work.css', import.meta.url), 'utf8');
const me = 'user:owner';
const boards = [{ slug: 'app', name: 'App' }, { slug: 'site', name: 'Site' }];
const card = (number, patch = {}) => ({ number, title: `Card ${number}`, status: 'doing', need: 'idle', board: 'app', updated_at: `2026-11-25T10:${String(number % 60).padStart(2, '0')}:00Z`, ...patch });
const byBoard = (app = [], site = []) => new Map([['app', app], ['site', site]]);
const text = html => html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
const lane = (html, group) => new RegExp(`<section class="wk-lane" data-lane="${group}"[^]*?</section>`).exec(html)?.[0] || '';
const titles = html => [...html.matchAll(/<span class="wk-title">([^<]*)<\/span>/g)].map(m => m[1]);
const page = (cards, options = {}) => pageHTML({ boards, cards, me, bots: [], ...options });

test('the screen registers as Work and routes #/work', () => {
  assert.equal(TITLE, 'Work');
  assert.equal(typeof SCREENS.work, 'function');
  assert.deepEqual(LANES.map(([group, label]) => [group, label]), [['todo', 'Waiting on you'], ['doing', 'Active'], ['review', 'In review'], ['done', 'Shipped']]);
});

test('every card lands in the lane the Board gives it, and the owner queue decides Waiting on you', () => {
  setNeedsQueue(null);
  const cards = [card(1), card(2, { status: 'review' }), card(3, { status: 'done' }), card(4, { need: 'you', status: 'review' }), card(5, { status: 'backlog' }), card(6, { archived: true }), card(7, { board: 'site', need: 'you', status: 'review' })];
  const items = workItems({ boards, cards: byBoard(cards.slice(0, 6), cards.slice(6)), me });
  assert.deepEqual(Object.fromEntries(items.map(item => [item.card.number, item.group])), { 1: 'doing', 2: 'review', 3: 'done', 4: 'todo', 7: 'todo' }, 'backlog and archived cards stay out');
  for (const item of items) assert.equal(item.group, boardGroup(item.card, me), `card ${item.card.number} follows boardGroup`);
  // With the owner queue loaded, the queue (not the card's own flag) says what waits: the same list the Waiting count shows.
  setNeedsQueue({ asks: [{ number: 1 }, { number: 5 }], approvals: [] });
  try {
    const queued = workItems({ boards, cards: byBoard(cards.slice(0, 6), cards.slice(6)), me });
    assert.deepEqual(Object.fromEntries(queued.map(item => [item.card.number, item.group])), { 1: 'todo', 2: 'review', 3: 'done', 4: 'review', 5: 'todo', 7: 'review' });
    const html = page(byBoard(cards.slice(0, 6), cards.slice(6)));
    assert.match(lane(html, 'todo'), /Waiting on you <b class="wk-waits">2<\/b>/);
    assert.deepEqual(titles(lane(html, 'todo')).sort(), ['Card 1', 'Card 5']);
  } finally { setNeedsQueue(null); }
});

test('lane labels carry their count, newest first', () => {
  const html = page(byBoard([card(1, { updated_at: '2026-11-25T08:00:00Z' }), card(2, { updated_at: '2026-11-25T09:00:00Z' }), card(3, { status: 'done' })]));
  assert.match(lane(html, 'doing'), /Active <b>2<\/b>/);
  assert.match(lane(html, 'done'), /Shipped <b>1<\/b>/);
  assert.match(lane(html, 'todo'), /Waiting on you <b>0<\/b>/);
  assert.deepEqual(titles(lane(html, 'doing')), ['Card 2', 'Card 1']);
  assert.match(lane(html, 'doing'), /href="#\/card\/2"/, 'a card opens its page');
});

test('chips: All projects, then one per project with its open-card count; the choice filters the lanes', () => {
  const cards = byBoard([card(1), card(2, { status: 'review' }), card(3, { status: 'done' })], [card(4, { board: 'site' })]);
  const counts = openCounts(workItems({ boards, cards, me }));
  assert.deepEqual([...counts].sort(), [['app', 2], ['site', 1]], 'shipped cards are not open');
  const chips = chipsHTML(boards, counts, '', cards);
  assert.match(chips, /data-project="" aria-pressed="true">All projects</);
  assert.match(chips, /data-project="app" aria-pressed="false">App <b>2<\/b>/);
  assert.match(chips, /data-project="site" aria-pressed="false">Site <b>1<\/b>/);
  const site = page(cards, { project: 'site' });
  assert.match(site, /data-project="site" aria-pressed="true"/);
  assert.deepEqual(titles(lane(site, 'doing')), ['Card 4']);
  assert.equal(lane(site, 'review').includes('Card 2'), false);
  assert.match(lane(site, 'done'), /Shipped <b>0<\/b>/);
  // The chip counts stay the whole-workspace picture while one project is chosen.
  assert.match(site, /App <b>2<\/b>/);
  // A project that does not exist (a stale address) shows everything.
  const stale = page(cards, { project: 'gone' });
  assert.match(stale, /data-project="" aria-pressed="true"/);
  assert.equal(titles(lane(stale, 'doing')).length, 2);
});

test('the project choice lives in the address as #/work?project=<slug>', () => {
  assert.equal(projectFrom('#/work?project=site'), 'site');
  assert.equal(projectFrom('#/work?project=a%20b#x'), 'a b');
  assert.equal(projectFrom('#/work'), '');
  assert.equal(projectFrom(undefined), '');
  assert.equal(workHash(''), '#/work');
  assert.equal(workHash('a b'), '#/work?project=a%20b');
  assert.equal(projectFrom(workHash('a/b&c')), 'a/b&c');
});

test('a lane shows its first five cards and a Show N more link under it that expands it in place', () => {
  const many = Array.from({ length: 8 }, (_, i) => card(i + 1));
  const items = workItems({ boards, cards: byBoard(many), me });
  const closed = laneHTML(LANES[1], items, new Set());
  assert.equal(SHOWN, 5);
  assert.equal((closed.match(/<a class="wk-card"/g) || []).length, 5);
  assert.match(closed, /<\/div><button type="button" class="wk-more" data-wk="more" data-lane="doing" aria-expanded="false">Show 3 more<\/button><\/section>/, 'a text link under the cards, not a card');
  assert.doesNotMatch(closed, /class="wk-card wk-more"/);
  const open = laneHTML(LANES[1], items, new Set(['doing']));
  assert.equal((open.match(/<a class="wk-card"/g) || []).length, 8);
  assert.match(open, /class="wk-more" data-wk="less"[^>]*aria-expanded="true">Show fewer<\/button>/);
  assert.doesNotMatch(open, /Show \d+ more/);
  // Exactly five fit without a toggle.
  const five = laneHTML(LANES[1], items.slice(0, 5), new Set());
  assert.doesNotMatch(five, /wk-more/);
});

test('an empty lane says one quiet sentence; nothing at all shows four of them', () => {
  const html = page(byBoard());
  assert.match(lane(html, 'todo'), /<p class="wk-empty">Nothing waits on you\.<\/p>/);
  assert.match(lane(html, 'doing'), /<p class="wk-empty">No active cards\.<\/p>/);
  assert.match(lane(html, 'review'), /<p class="wk-empty">Nothing is waiting for review\.<\/p>/);
  assert.match(lane(html, 'done'), /<p class="wk-empty">Nothing shipped yet\.<\/p>/);
  assert.equal((html.match(/class="wk-empty"/g) || []).length, 4);
  assert.doesNotMatch(html, /class="wk-card[ "]/);
  assert.match(html, /data-project="app" aria-pressed="false">App <b>0<\/b>/, 'a loaded project with nothing open counts 0');
});

test('the health line counts ready bots and links Cloud workspaces when no machine is connected', () => {
  const ready = { model: { state: 'done' }, machine: { state: 'done' }, key: { state: 'not_needed' } };
  const html = healthHTML([{ id: 'a', readiness: ready }, { id: 'b', readiness: ready }, { id: 'c', readiness: { model: { state: 'needs_you' } } }, { id: 'd', archived: true, readiness: ready }]);
  assert.match(text(html), /^2 bots ready$/);
  assert.doesNotMatch(html, /#\/compute/, 'a connected machine needs no sentence');
  assert.match(text(healthHTML([{ id: 'a', readiness: { model: { state: 'done' } } }])), /^1 bot ready No computer is connected, so bots can plan but not run code\. Connect one in Cloud workspaces$/);
  assert.match(healthHTML([{ id: 'a', readiness: { model: { state: 'done' } } }]), /<a href="#\/compute">Connect one in Cloud workspaces<\/a>/);
  assert.match(text(healthHTML([])), /^No bots are ready yet No computer is connected/);
  assert.equal(healthHTML(null), '', 'no roster, no health line');
});

test('loading, failure and partial states are plain words with one way forward', () => {
  assert.match(pageHTML({ boards: null, me }), /role="status">Loading work…/);
  assert.match(pageHTML({ error: 'Work could not be loaded.', me }), /role="alert">Work could not be loaded\. <button[^>]*data-wk="retry">Try again/);
  assert.match(page(byBoard(), { partial: true }), /Some projects could not be loaded\. <button[^>]*data-wk="retry">Try again/);
  assert.doesNotMatch(page(byBoard()), /could not be loaded/);
});

test('titles, project and bot names, and slugs are escaped', () => {
  const evil = [{ slug: 'x"><img src=x>', name: '<b>Proj</b> & "co"' }];
  const html = pageHTML({ boards: evil, cards: new Map([[evil[0].slug, [card(1, { title: '<script>alert(1)</script>', board: evil[0].slug })]]]), me, bots: [{ id: 'z', board: evil[0].slug, name: '<i>Bot</i>', readiness: {} }] });
  assert.doesNotMatch(html, /<script|<img|<b>Proj|<i>Bot/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(html, /data-project="x&quot;&gt;&lt;img src=x&gt;"/);
  assert.match(html, /&lt;i&gt;Bot&lt;\/i&gt;/);
  assert.match(cardHTML({ card: card(5, { title: 'A "q" & <b>' }), name: 'Bo<b>' }), /A &quot;q&quot; &amp; &lt;b&gt;/);
});

test('a card names its bot (or the project) with an initial and how long ago it changed', () => {
  const items = workItems({ boards, cards: byBoard([card(1, { updated_at: new Date(Date.now() - 2 * 3600e3).toISOString() })], [card(2, { board: 'site' })]), bots: [{ id: 'k', board: 'app', name: 'Kit' }], me });
  const [first] = items.filter(item => item.card.number === 1), [second] = items.filter(item => item.card.number === 2);
  assert.equal(first.name, 'Kit');
  assert.equal(second.name, 'Site', 'no bot on the project: the project name');
  const html = cardHTML(first);
  assert.match(html, /<span class="wk-av" aria-hidden="true">K<\/span><span class="wk-name">Kit<\/span>(?:<span class="wk-state">[^<]+<\/span>)?<time datetime="[^"]+" title="[^"]+">2h<\/time>/);
  assert.doesNotMatch(cardHTML({ card: card(3, { updated_at: undefined, created_at: undefined }), name: 'X' }), /<time/);
});

// ---- mount ----
function fakeRoot() {
  const listeners = {}, classes = new Set();
  return {
    innerHTML: '', parentElement: null, ownerDocument: {}, listeners, classes,
    classList: { add: name => classes.add(name), remove: name => classes.delete(name) },
    addEventListener: (type, fn) => { listeners[type] = fn; }, removeEventListener: type => { delete listeners[type]; },
    querySelector: () => null,
    click: dataset => listeners.click?.({ target: { closest: () => ({ dataset }) } }),
  };
}
const settle = () => new Promise(resolve => setTimeout(resolve, 5));
const answers = { '/api/bots': { bots: [] }, '/api/boards/app/cards': { cards: [card(1), card(2, { status: 'review' })] }, '/api/boards/site/cards': { cards: [card(3, { board: 'site' })] } };
const apiFor = (log = []) => async path => { log.push(path); if (path in answers) return answers[path]; throw new Error(`unexpected ${path}`); };

test('mount reads the readable boards and their cards, filters on a chip, expands a lane, and stops cleanly', async () => {
  const root = fakeRoot(), log = [], replaced = [];
  const saved = globalThis.history;
  globalThis.history = { state: null, replaceState: (...args) => replaced.push(args[2]) };
  try {
    const ui = mount({ root, api: apiFor(log), me: { actor: me }, boards, route: '#/work' });
    assert.ok(root.classes.has('deck-work'));
    assert.match(root.innerHTML, /Loading work…/);
    await settle();
    assert.deepEqual([...log].sort(), ['/api/boards/app/cards', '/api/boards/site/cards', '/api/bots']);
    assert.match(root.innerHTML, /Card 3/);
    root.click({ wk: 'project', project: 'site' });
    assert.deepEqual(replaced, ['#/work?project=site']);
    assert.doesNotMatch(root.innerHTML, /Card 1/);
    assert.match(root.innerHTML, /data-project="site" aria-pressed="true"/);
    root.click({ wk: 'project', project: '' });
    assert.deepEqual(replaced.at(-1), '#/work');
    assert.match(root.innerHTML, /Card 1/);
    ui.refresh({ boards });
    await settle();
    assert.equal(log.filter(path => path === '/api/bots').length, 2, 'refresh reads again, once');
    ui.stop();
    assert.equal(root.innerHTML, '');
    assert.equal(root.classes.has('deck-work'), false);
    assert.equal(root.listeners.click, undefined);
  } finally { globalThis.history = saved; }
});

test('mount starts on the project named in the address, falls back to the shell list, and survives a failed board', async () => {
  const root = fakeRoot(), log = [];
  const api = async path => {
    log.push(path);
    if (path === '/api/boards?view=shell') return { boards };
    if (path === '/api/boards/site/cards') throw new Error('down');
    if (path === '/api/bots') throw new Error('down');
    return answers[path];
  };
  const ui = mount({ root, api, me: { actor: me }, boards: [], route: '#/work?project=app' });
  await settle();
  assert.ok(log.includes('/api/boards?view=shell'), 'no boards in the context: read the shell list');
  assert.match(root.innerHTML, /data-project="app" aria-pressed="true"/);
  assert.match(root.innerHTML, /Card 1/);
  assert.match(root.innerHTML, /Some projects could not be loaded/);
  assert.doesNotMatch(root.innerHTML, /wk-health/, 'no roster, no health line');
  ui.stop();
  const failing = fakeRoot();
  const down = mount({ root: failing, api: async () => { throw Object.assign(new Error('x'), { status: 500 }); }, me: { actor: me }, boards: [], route: '#/work' });
  await settle();
  assert.match(failing.innerHTML, /Work could not be loaded\. <button[^>]*data-wk="retry">/);
  down.stop();
});

test('the stylesheet follows the Deck tokens: 52px bar, lane grid, no italic, no shadows, 44px touch targets on the phone', () => {
  assert.match(css, /\.wk-bar \{[^}]*height: 52px/);
  assert.match(css, /\.wk-lane \{[^}]*grid-template-columns: 150px minmax\(0, 1fr\)/);
  assert.match(css, /\.wk-cards \{[^}]*repeat\(auto-fill, minmax\(260px, 1fr\)\)/);
  assert.doesNotMatch(css, /italic|box-shadow|gradient|#[0-9a-f]{3,8}\b/i, 'tokens only: no italic, shadows, gradients or raw colours');
  const phone = css.slice(css.indexOf('@media (max-width: 699px)'));
  assert.match(phone, /\.wk-card \{[^}]*min-height: 44px/);
  assert.match(phone, /\.wk-cards \{[^}]*grid-template-columns: 1fr/);
});

test('desktop lanes use three equal columns and Show N more is a text link, not a card', () => {
  const css = readFileSync(new URL('../commonplace/web/static/deck-work.css', import.meta.url), 'utf8');
  assert.match(css, /@media \(min-width: 1100px\) \{ \.deck-work \.wk-cards \{ grid-template-columns: repeat\(3, minmax\(0, 1fr\)\); \} \}/);
  assert.match(css, /\.deck-work \.wk-more \{ grid-column: 2;[^}]*border: 0; background: none;[^}]*text-decoration: underline/);
});
