import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolveRoute } from '../commonplace/web/static/routes.js';
import { createDeck, renderDeckBar, renderDeckTabs } from '../commonplace/web/static/deck.js';
import { renderRail, renderPhoneBar, renderPhoneTabs } from '../commonplace/web/static/rail.js';
import { workspaceRoute } from '../commonplace/web/static/routes.js';
import { coworkerRow, mount } from '../commonplace/web/static/deck-workspace.js';
import { coworkersNavHTML, messageHTML } from '../commonplace/web/static/bots.js';
import { escape as e } from '../commonplace/web/static/api.js';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const hrefs = html => [...html.matchAll(/href="([^"]*)"/g)].map(m => m[1]);
test('legacy workspace links resolve once, retaining conversations and message anchors', () => {
  for (const deck of [false, true]) {
    const routes = [
      ['#/compute', deck ? '#/compute' : '#/compute/management'],
      ['#/compute-map', deck ? '#/compute' : '#/compute-map'],
      ['#/coworkers', '#/bots/-/coworkers'],
      ['#/coworkers/c-1?message=m-2', '#/bots/-/coworkers/conversations/c-1?message=m-2'],
      ['#/bots/coworkers', '#/bots/coworkers'], // an existing bot ID is never stolen
      ['#/bots/project-1/coworkers', '#/bots/project-1/coworkers'],
    ];
    for (const [from, to] of routes) {
      assert.equal(resolveRoute(from, { deck }), to);
      assert.equal(resolveRoute(to, { deck }), to, 'no redirect loops');
    }
  }
});
test('every canonical nested route parses without stealing Bot threads or accepting malformed addresses', () => {
  for (const hash of ['#/compute', '#/compute/management', '#/bots/-/coworkers', '#/bots/b-1/coworkers', '#/bots/-/coworkers/conversations', '#/bots/-/coworkers/conversations/c%2F1?message=m%202']) assert.ok(workspaceRoute(hash), hash);
  assert.deepEqual(workspaceRoute('#/bots/-/coworkers/conversations/c%2F1?message=m%202'), { screen: 'coworkers', bot: null, conversations: true, conversation: 'c/1', message: 'm 2' });
  for (const hash of ['#/bots/coworkers', '#/bots/main', '#/compute/management/extra', '#/bots/b/coworkers/conversations', '#/bots/-/coworkers/conversations/%ZZ', '#/bots/-/coworkers/conversations/c?bad=1']) assert.equal(workspaceRoute(hash), null, hash);
});
test('Coworker rows show the recorded parent, owned project/repository and child threads, escaping all data', () => {
  const row = coworkerRow({ bot: { id: 'bot-1', name: 'Shipping', board: 'product' }, board: { name: 'Product', repo: 'example/product' }, project: { name: 'Product', main: { connected: true, name: 'orchestrator:product' } }, checkpoint: { jobs: [{ thread_id: 'child-1', state: 'running' }, { source: 'journal', assignee: 'conn:builder', state: 'started' }, { thread_id: 'child-2', state: 'queued' }] } });
  for (const text of ['Coworker', 'Shipping', 'Product', 'example/product', 'child-1', 'running', 'conn:builder', 'started']) assert.ok(row.includes(text), text);
  assert.match(row, /href="#\/bots\/bot-1"/);
  assert.doesNotMatch(row, /orchestrator/i);
  assert.equal((row.match(/class="topo-dot s-up"/g) || []).length, 3, 'connection and running workers share the map up mark');
  assert.equal((row.match(/class="topo-dot s-unknown"/g) || []).length, 1, 'queued work has the map unknown ring');
  const missing = coworkerRow({ bot: { id: 'x', name: '<script>', board: 'p' } });
  assert.match(missing, /No child threads recorded/);
  assert.match(missing, /&lt;script&gt;/);
  assert.doesNotMatch(missing, /<script>/);
});
test('Deck Bot navigation is parent-scoped and forwarded worker replies use Coworker copy', () => {
  assert.deepEqual(hrefs(coworkersNavHTML('shipping')), ['#/bots/shipping/coworkers']);
  assert.deepEqual(hrefs(coworkersNavHTML()), ['#/bots/-/coworkers']);
  const message = { id: 'm1', role: 'event', blocks: [{ type: 'orchestrator' }] };
  const deck = messageHTML(message, { deck: true }, e);
  assert.match(deck, />Coworker</);
  assert.doesNotMatch(deck + renderDeckBar() + renderDeckTabs(), /orchestrator/i);
  assert.match(messageHTML(message, { deck: false }, e), />Coworker</, 'classic and Deck use the same product term');
});

const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
function hostFixture() {
  const regions = new Map(['content', 'parent'].map(name => [name, { innerHTML: '', addEventListener() {}, removeEventListener() {}, querySelector() { return null; } }]));
  const classes = new Set();
  const root = { innerHTML: '', querySelector: selector => regions.get(/data-workspace="([^"]+)"/.exec(selector)?.[1]), classList: { add: name => classes.add(name), remove: name => classes.delete(name) } };
  return { root, regions, classes };
}
test('Management uses the existing controller and restores the shell and phone destinations on departure', () => {
  for (const deck of [false, true]) {
    const f = hostFixture(), header = { hidden: false }, phone = { hidden: false }, tabs = { style: { position: 'relative' } };
    f.root.ownerDocument = { documentElement: { dataset: deck ? { ui: 'deck' } : {} }, addEventListener() {}, removeEventListener() {} };
    f.root.parentElement = { querySelector: selector => ({ '.head': header, '#phone-bar': phone, '#phone-tabs': tabs })[selector] };
    const ui = mount({ root: f.root, route: '#/compute/management', me: { workspace: 'w', actor: 'u' }, api: () => new Promise(() => {}) });
    assert.deepEqual(hrefs(f.root.innerHTML), [deck ? '#/compute' : '#/compute-map', '#/compute/management']);
    assert.match(f.regions.get('content').innerHTML, /Loading Compute inventory/);
    assert.match(f.regions.get('content').innerHTML, /<h1>Compute<\/h1>/);
    assert.doesNotMatch(f.root.innerHTML, /<p>Management<\/p>/);
    assert.doesNotMatch(f.root.innerHTML + f.regions.get('content').innerHTML, /orchestrator/i);
    assert.equal(header.hidden, true); assert.equal(phone.hidden, !deck); assert.equal(tabs.style.position, 'fixed');
    ui.stop();
    assert.equal(header.hidden, false); assert.equal(phone.hidden, false); assert.equal(tabs.style.position, 'relative');
    assert.equal(f.root.innerHTML, '');
  }
});
test('Coworkers puts the parent scope switch and conversation action in its title row', () => {
  for (const bot of ['bot-1', '-']) {
    const f = hostFixture();
    const ui = mount({ root: f.root, route: `#/bots/${bot}/coworkers`, api: () => new Promise(() => {}) });
    const title = /<header class="dw-title">([\s\S]*?)<\/header>/.exec(f.root.innerHTML)?.[1];
    assert.ok(title);
    assert.match(title, /aria-label="Coworker scope"/);
    assert.match(title, />This Bot</);
    assert.match(title, />All Bots</);
    assert.match(title, /href="#\/bots\/-\/coworkers\/conversations">Open conversation</);
    if (bot === '-') assert.match(title, /aria-disabled="true">This Bot</);
    else assert.match(title, /href="#\/bots\/bot-1\/coworkers" aria-current="page">This Bot</);
    ui.stop();
  }
});
test('Coworkers mounts from existing reads, refreshes once and ignores late replies after departure', async () => {
  const f = hostFixture(), requests = [];
  const api = (path, options) => new Promise(resolve => requests.push({ path, options, resolve }));
  const ui = mount({ root: f.root, route: '#/bots/bot-1/coworkers', api, me: { workspace: 'w', actor: 'u' }, boards: [{ slug: 'product', name: 'Product', repo: 'example/product' }] });
  requests[0].resolve({ bots: [{ id: 'bot-1', name: 'Shipping', board: 'product' }] }); await flush();
  requests.find(r => r.path === '/api/projects/product').resolve({ main: { connected: true } });
  requests.find(r => r.path.endsWith('/checkpoint')).resolve({ jobs: [{ thread_id: 'child-1', state: 'running' }] }); await flush();
  assert.match(f.regions.get('content').innerHTML, /example\/product/);
  assert.match(f.regions.get('parent').innerHTML, />Shipping</);
  void ui.refresh();
  const pending = requests.at(-1); ui.stop();
  assert.equal(pending.options.signal.aborted, true);
  pending.resolve({ bots: [] }); await flush();
  assert.equal(f.root.innerHTML, '');
  assert.equal(f.classes.size, 0);
});
test('the real shell accepts every nested page and tears down the previous controller', async () => {
  const app = readFileSync(new URL('../commonplace/web/static/app.js', import.meta.url), 'utf8');
  const source = app.slice(app.indexOf('async function route()'), app.indexOf('// The current view'));
  for (const deck of [false, true]) {
    let stopped = 0, left = 0, refreshed = 0;
    const state = { me: {}, base: '#/bots/b1', route: '#/bots/b1', screen: 1, boards: [], chatVisible: new Set(), deck: createDeck({ storage: { getItem: () => deck ? 'deck' : 'classic' }, doc: {} }) };
    const leave = state.deck.leave; state.deck.leave = () => { left++; leave(); };
    const panel = {}, content = { children: [], innerHTML: '' };
    const context = { state, location: { hash: '' }, history: { state: null, replaceState(_state, _title, hash) { context.location.hash = hash; } }, localStorage: { getItem() {} }, root: { querySelector: s => s === '#content' ? content : s === '#card-panel' ? panel : null }, resolveRoute, boardRoute() {}, boardPreference() {}, decideStart() {}, remember() {}, clearAgentSecret() {}, voice: { stop() {} }, speech: { stop() {} }, botsUI: { stop() { stopped++; } }, boardDispose: null, detailsUI: null, markOpenCard() {}, showError() {}, message: s => s, showLoading: (host, text) => { host.innerHTML = text; }, refresh() { refreshed++; } };
    runInNewContext(source, context);
    for (const hash of ['#/compute', '#/compute/management', '#/coworkers', '#/coworkers/c1?message=m1', '#/bots/b1/coworkers', '#/bots/-/coworkers/conversations']) {
      context.location.hash = hash; await context.route();
      assert.equal(state.base, resolveRoute(hash, { deck }), hash);
    }
    assert.equal(refreshed, 6);
    assert.ok(stopped > 0 && left > 0);
  }
});
test('nested pages belong to their existing Deck section and keep four destinations', () => {
  for (const [hash, section] of [['#/compute', 'compute'], ['#/compute/management', 'compute'], ['#/bots/-/coworkers', 'bots'], ['#/bots/project-1/coworkers', 'bots'], ['#/bots/-/coworkers/conversations/c-1?message=m-2', 'bots']]) {
    const ui = createDeck({ storage: { getItem: () => 'deck' }, doc: {} });
    assert.equal(ui.owns(hash), true, hash);
    for (const html of [renderDeckBar({ hash }), renderDeckTabs({ hash })]) {
      assert.deepEqual([...html.matchAll(/data-nav="([^"]*)"/g)].map(m => m[1]), ['today', 'bots', 'compute', 'settings']);
      assert.match(html, new RegExp(`data-nav="${section}" aria-current="page"`));
      assert.equal((html.match(/aria-current="page"/g) || []).length, 1);
    }
  }
});
test('classic rail and phone tabs remain exactly Bots and Memory, with the same owner map menu', () => {
  assert.deepEqual(hrefs(renderRail().split('rail-foot')[0]), ['#/bots', '#/memory']);
  assert.deepEqual(hrefs(renderPhoneTabs()), ['#/bots', '#/memory']);
  for (const render of [renderRail, renderPhoneBar]) {
    assert.equal(hrefs(render({ can_admin: true })).filter(href => href === '#/compute-map').length, 1);
    assert.ok(!hrefs(render({ can_admin: false })).includes('#/compute-map'));
    assert.doesNotMatch(render({ can_admin: true }), /coworkers|compute\/management/);
  }
});

test('Deck Compute phone workspace keeps its 16px gutter around the nested map', () => {
  const css = readFileSync(new URL('../commonplace/web/static/deck-workspace.css', import.meta.url), 'utf8');
  const phone = css.slice(css.indexOf('@media(max-width:699px)'));
  assert.match(phone, /:root\[data-ui="deck"\] \.shell #content\.deck-workspace\.dw-map\s*\{[^}]*padding:\s*12px 16px 88px/);
  assert.match(phone, /:root\[data-ui="deck"\] \.dw-map > \.dw-tabs\s*\{\s*margin:\s*0;/);
  assert.match(phone, /:root\[data-ui="deck"\] \.dw-map > \[data-workspace="content"\]\s*\{[^}]*min-width:\s*0;/);
});
