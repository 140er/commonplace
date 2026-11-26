import assert from 'node:assert/strict';
import { test } from 'node:test';
import { renderDeckBar } from '../commonplace/web/static/deck.js';
import { createDeckShell, shellTrees } from '../commonplace/web/static/deck-shell.js';
import { COLLAPSED_KEY, currentNode, loadCollapsed, projectTree, saveCollapsed, treeKey } from '../commonplace/web/static/deck-tree.js';

const boards = [{ slug: 'blue-eye', name: 'Blue Eye' }, { slug: 'halo-avatar', name: 'HALO avatar' }, { slug: 'loose', name: 'Loose ends' }];
const bots = [
  { id: 'a', name: 'Atlas', kind: 'custom', mine: true, projects: ['blue-eye', 'halo-avatar'] },
  { id: 'g', name: 'General', kind: 'general', mine: true, projects: [] },
];
const tree = (extra = {}) => shellTrees({ bots, boards, treeState: 'ready', ...extra });
const rows = html => [...html.matchAll(/role="treeitem"[^>]*data-deck-item="([^"]+)"/g)].map(match => match[1]);

test('each project sits under the bot that lists it, the rest under Unassigned', () => {
  const model = projectTree({ bots, boards });
  assert.deepEqual(model.under.get('a').map(project => project.name), ['Blue Eye', 'HALO avatar']);
  assert.deepEqual(model.under.get('g'), []);
  assert.deepEqual(model.unassigned.map(project => project.slug), ['loose']);
  assert.equal(currentNode('#/board/halo-avatar/chat', model), 'bot:a');
  assert.equal(currentNode('#/board/loose', model), 'unassigned');
  assert.equal(currentNode('#/bots/a', model), '');
});

test('one project is under one bot, and a slug the person cannot read is never drawn', () => {
  const twice = [{ id: 'a', projects: ['blue-eye'] }, { id: 'b', projects: ['blue-eye', 'secret'] }];
  const model = projectTree({ bots: twice, boards });
  assert.deepEqual(model.under.get('a').map(project => project.slug), ['blue-eye']);
  assert.deepEqual(model.under.get('b'), []);
  const html = shellTrees({ bots: twice, boards, treeState: 'ready' });
  assert.doesNotMatch(html, /secret/);
  assert.equal(projectTree({ bots: twice, boards: [] }).unassigned.length, 0, 'no readable project, nothing to leak through the Unassigned group');
  assert.doesNotMatch(shellTrees({ bots: twice, boards: [], treeState: 'ready' }), /Unassigned/);
});

test('shared bots and coworkers hold no projects', () => {
  const model = projectTree({ bots: [{ id: 's', mine: false, projects: ['blue-eye'] }, { id: 'c', shellKind: 'coworker', projects: ['blue-eye'] }], boards });
  assert.deepEqual([...model.under.keys()], []);
  assert.equal(model.unassigned.length, 3);
});

test('the tree nests projects under their bot with tree semantics', () => {
  const html = tree();
  assert.match(html, /<div class="deck-bot-tree" role="none"><a class="deck-bot-row" href="#\/bots\/a" role="treeitem" aria-level="1" aria-expanded="true" aria-owns="deck-tree-rail-0"/);
  assert.match(html, /<ul role="group" id="deck-tree-rail-0"><li role="none" class="deck-project-li"><a class="deck-tree-row deck-project-row" href="#\/board\/blue-eye" role="treeitem" aria-level="2"/);
  assert.deepEqual(rows(html), ['bot:a', 'project:blue-eye', 'project:halo-avatar', 'bot:g', 'unassigned', 'project:loose']);
  assert.match(html, /Unassigned/);
  assert.equal((html.match(/tabindex="0"/g) || []).length, 1, 'one tab stop in the tree');
  assert.equal(tree({ rosterReady: false }).includes('deck-unassigned-row'), false, 'nothing is called unassigned before the roster is known');
});

test('a bot without projects still shows, and opens onto an empty hint', () => {
  const html = tree();
  assert.match(html, /data-deck-item="bot:g"[^>]*>[\s\S]*?General[\s\S]*?<ul role="group" id="deck-tree-rail-1"><li role="none"><span class="deck-tree-empty" role="treeitem" aria-level="2" aria-disabled="true">No projects yet<\/span>/);
});

test('the current project is marked, takes the tab stop and opens its bot even when that bot was folded', () => {
  const html = tree({ hash: '#/board/halo-avatar', collapsed: new Set(['bot:a', 'unassigned']) });
  assert.match(html, /href="#\/board\/halo-avatar" aria-current="page" role="treeitem" aria-level="2" tabindex="0"/);
  assert.match(html, /aria-expanded="true" aria-owns="deck-tree-rail-0"/);
  assert.match(html, /aria-expanded="false" aria-owns="deck-tree-rail-unassigned"/);
  assert.match(html, /<ul role="group" id="deck-tree-rail-unassigned" hidden>/);
  assert.equal((html.match(/tabindex="0"/g) || []).length, 1);
  const folded = tree({ hash: '#/board/halo-avatar', collapsed: new Set(['bot:a']), manual: new Set(['bot:a']) });
  assert.match(folded, /aria-expanded="false" aria-owns="deck-tree-rail-0"/, 'folding the current bot by hand on this page is respected');
  assert.match(folded, /<ul role="group" id="deck-tree-rail-0" hidden>/);
});

test('the current bot is marked and an unassigned current project opens that group', () => {
  assert.match(tree({ hash: '#/bots/g' }), /href="#\/bots\/g" aria-current="page"[^>]*tabindex="0"/);
  const html = tree({ hash: '#/board/loose', collapsed: new Set(['unassigned']) });
  assert.match(html, /<ul role="group" id="deck-tree-rail-unassigned"><li/);
});

test('a project shows its waiting decisions and an owner can move it under another bot or Unassigned', () => {
  const needsQueue = { items: [{ type: 'decision', board: 'blue-eye', card: 3, title: 'x' }] };
  assert.match(tree({ needsQueue }), /Blue Eye<\/span>(<small>1 waiting<\/small>)?/);
  const html = tree({ moving: 'blue-eye' });
  assert.match(html, /<div class="deck-move" role="group" aria-label="Move Blue Eye"><label for="deck-move-rail">Move under<\/label><select id="deck-move-rail" data-deck-move-select="blue-eye"><option value="">Unassigned<\/option><option value="a" selected>Atlas<\/option><option value="g">General<\/option><\/select>/);
  assert.match(html, /data-deck-move="blue-eye" aria-expanded="true" aria-label="Move Blue Eye under another bot or Unassigned"/);
  assert.equal((tree().match(/deck-move"/g) || []).length, 0);
  assert.match(tree({ scope: 'page', moving: 'loose' }), /<option value="" selected>|id="deck-move-page"/);
});

test('the rail draws the tree in place of a separate Projects list', () => {
  const bar = renderDeckBar({ bots, boards, hash: '#/board/blue-eye' });
  assert.match(bar, /<div data-deck-bots role="tree" aria-label="Bots and projects">/);
  assert.doesNotMatch(bar, /class="deck-projects"|deck-project-row" href="#\/board\/blue-eye" aria-current="page"><svg/);
  assert.equal((bar.match(/href="#\/boards"/g) || []).length, 1, 'one link to the Projects page remains');
});

test('keys: arrows move, right opens or enters, left folds or leaves, Home and End jump', () => {
  const items = [{ level: 1, expandable: true, expanded: true }, { level: 2 }, { level: 2 }, { level: 1, expandable: true, expanded: false }];
  assert.deepEqual(treeKey('ArrowDown', items, 0), { focus: 1 });
  assert.equal(treeKey('ArrowDown', items, 3), null);
  assert.deepEqual(treeKey('ArrowUp', items, 2), { focus: 1 });
  assert.equal(treeKey('ArrowUp', items, 0), null);
  assert.deepEqual(treeKey('ArrowRight', items, 0), { focus: 1 });
  assert.deepEqual(treeKey('ArrowRight', items, 3), { toggle: true });
  assert.equal(treeKey('ArrowRight', items, 1), null);
  assert.deepEqual(treeKey('ArrowLeft', items, 0), { toggle: false });
  assert.deepEqual(treeKey('ArrowLeft', items, 2), { focus: 0 });
  assert.equal(treeKey('ArrowLeft', items, 3), null);
  assert.deepEqual(treeKey('Home', items, 2), { focus: 0 });
  assert.deepEqual(treeKey('End', items, 0), { focus: 3 });
  assert.equal(treeKey('x', items, 0), null);
  assert.equal(treeKey('ArrowDown', [], 0), null);
});

test('folded rows are remembered per device and a damaged store means nothing is folded', () => {
  const memory = new Map(), storage = { getItem: key => memory.get(key) ?? null, setItem: (key, value) => memory.set(key, value) };
  saveCollapsed(storage, new Set(['bot:a', 'unassigned']));
  assert.deepEqual([...loadCollapsed(storage)], ['bot:a', 'unassigned']);
  for (const bad of ['', 'nope', '{"a":1}', '[1,null]']) { memory.set(COLLAPSED_KEY, bad); assert.equal(loadCollapsed(storage).size, 0, bad); }
  assert.equal(loadCollapsed({ getItem() { throw new Error('denied'); } }).size, 0);
  assert.doesNotThrow(() => saveCollapsed({ setItem() { throw new Error('full'); } }, new Set(['x'])));
});

// A root that records its listeners and answers the few selectors the tree handlers use.
function harness({ hash = '#/bots', storage } = {}) {
  globalThis.location = { hash };
  const listeners = {}, host = { innerHTML: '', contains: () => false, querySelectorAll: () => [], hasAttribute: () => false, setAttribute() {}, removeAttribute() {} };
  const root = {
    ownerDocument: null,
    addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
    querySelector: selector => selector === '[data-deck-bots]' ? host : null,
    querySelectorAll: () => [],
  };
  const shell = createDeckShell({ root, api: async () => ({}), context: () => ({ boards }), on: () => true, storage });
  shell.roster(bots);
  const fire = (type, event) => (listeners[type] || []).forEach(fn => fn({ preventDefault() {}, ...event }));
  return { shell, host, fire };
}
const chevronOf = (host, key, expanded) => {
  const row = { dataset: { deckItem: key }, getAttribute: () => String(expanded), closest: selector => selector === '[role="treeitem"]' ? row : null };
  const chevron = { dataset: { deckToggle: key }, closest: selector => selector === '[data-deck-toggle]' ? chevron : selector === '[role="treeitem"]' ? row : selector === '[data-deck-bots], [data-deck-bots-page]' ? host : null };
  return chevron;
};

test('tapping a chevron folds the bot, redraws the tree and remembers it for the next visit', () => {
  const memory = new Map(), storage = { getItem: key => memory.get(key) ?? null, setItem: (key, value) => memory.set(key, value) };
  const { host, fire } = harness({ storage });
  assert.match(host.innerHTML, /aria-expanded="true" aria-owns="deck-tree-rail-0"/);
  fire('click', { target: chevronOf(host, 'bot:a', true) });
  assert.match(host.innerHTML, /aria-expanded="false" aria-owns="deck-tree-rail-0"/);
  assert.match(host.innerHTML, /<ul role="group" id="deck-tree-rail-0" hidden>/);
  assert.deepEqual(JSON.parse(memory.get(COLLAPSED_KEY)), ['bot:a']);
  const again = harness({ storage });
  assert.match(again.host.innerHTML, /aria-expanded="false" aria-owns="deck-tree-rail-0"/, 'a new visit starts as it was left');
  fire('click', { target: chevronOf(host, 'bot:a', false) });
  assert.match(host.innerHTML, /aria-expanded="true" aria-owns="deck-tree-rail-0"/);
  assert.deepEqual(JSON.parse(memory.get(COLLAPSED_KEY)), []);
});
