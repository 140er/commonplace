// The dithered art on empty and loading states is decorative: hidden from assistive tech, text-free, and the words beside it never change.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { artHTML } from '../commonplace/web/static/deck-art.js';
import { ART } from '../commonplace/web/static/deck-art-data.js';
import { botsHTML, emptyCardsHTML } from '../commonplace/web/static/deck-today-capy.js';
import { capyRosterHTML } from '../commonplace/web/static/deck-coworkers.js';
import { deckBoardView } from '../commonplace/web/static/deck-board.js';
import { renderDeckMemory } from '../commonplace/web/static/deck-memory.js';
import { showLoading } from '../commonplace/web/static/loading.js';
import { renderImport } from '../commonplace/web/static/memory-import.js';
import { shellTrees } from '../commonplace/web/static/deck-shell.js';

const text = html => html.replace(/<svg[^]*?<\/svg>/g, '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
// Every art element in the markup is hidden from assistive tech and holds a text-free drawing.
const arts = html => [...html.matchAll(/<span class="cp-art"[^>]*>[^]*?<\/span>/g)].map(match => match[0]);
function assertArt(html, name) {
  const found = arts(html).filter(item => item.includes(`data-art="${name}"`));
  assert.equal(found.length, 1, `${name} art missing from ${html.slice(0, 120)}`);
  assert.match(found[0], /^<span class="cp-art" data-art="[a-z-]+" aria-hidden="true"><svg /);
  assert.doesNotMatch(found[0], /<(text|title|desc|image|script)\b|href=|\bon\w+=/i);
}

test('the art helper is hidden, text-free and refuses names it does not know', () => {
  assert.deepEqual(Object.keys(ART).sort(), ['bots', 'clear', 'column', 'header', 'loading', 'pipeline', 'rail', 'rule', 'search', 'step-choose', 'step-preview', 'step-progress', 'step-report']);
  for (const name of Object.keys(ART)) {
    assertArt(artHTML(name), name);
    assert.equal(text(artHTML(name)), '');
    assert.ok(ART[name].length < 4096, `${name} is ${ART[name].length} bytes`);
  }
  assert.ok(Object.values(ART).join('').length < 20000);
  assert.equal(artHTML('nope'), '');
  assert.equal(artHTML('toString'), '');
});

test('Today all clear and its empty bots line keep their words and gain art', () => {
  const clear = emptyCardsHTML(true);
  assertArt(clear, 'clear');
  assert.equal(text(clear), 'Nothing is waiting on you');
  assert.match(clear, /^<p class="bt-empty" role="status">/);
  const loading = emptyCardsHTML(false);
  assert.equal(text(loading), 'Loading…');
  assert.equal(arts(loading).length, 0);
  const bots = botsHTML([]);
  assertArt(bots, 'bots');
  assert.match(text(bots), /No bots yet\. New bot$/);
});

test('the Bots list shows art for loading and for no bots, with the same words', () => {
  const none = capyRosterHTML({ bots: [], loaded: true });
  assertArt(none, 'bots');
  assert.equal(text(none), 'No bots yet. Create one to get started.');
  const loading = capyRosterHTML({ bots: [], loaded: false });
  assertArt(loading, 'loading');
  assert.equal(text(loading), 'Loading…');
  assert.equal(arts(capyRosterHTML({ bots: [{ id: 'a', name: 'Atlas' }] })).length, 0);
});

test('an empty Board column shows art; a column with cards does not', () => {
  const board = { slug: 'product', name: 'Product' };
  const empty = deckBoardView({ cards: [], board, me: 'user:owner', bots: [] });
  assert.ok(arts(empty).length >= 1);
  for (const item of arts(empty)) assert.match(item, /data-art="column" aria-hidden="true"/);
  const cells = [...empty.matchAll(/<p class="empty-col">([^]*?)<\/p>/g)].map(match => match[1]);
  assert.ok(cells.length >= 1);
  for (const cell of cells) assert.equal(text(cell), 'Nothing here yet');
  const full = deckBoardView({ cards: [{ number: 1, version: 1, board: 'product', title: 'One', status: 'doing', need: 'working', events: [] }], board, me: 'user:owner', bots: [] });
  assert.ok(arts(full).length < arts(empty).length);
});

test('Memory shows search art only after a search found nothing', () => {
  const view = state => renderDeckMemory({ boards: [], results: [], filters: { query: 'zebra' }, ...state }, { highlighted: String });
  const none = view({ searched: true });
  assertArt(none, 'search');
  assert.match(text(none), /No exact words match Try fewer or different words, or search every room\./);
  const idle = view({ searched: false });
  assert.equal(arts(idle).filter(item => item.includes('data-art="search"')).length, 0);
  assert.match(text(idle), /Search your memory Exact words from chats, cards and notes\. Never summarised\./);
});

test('a loading hint takes optional art and still carries the slow note', () => {
  const host = { innerHTML: '' }, timers = [];
  showLoading(host, 'Loading card…', { art: 'loading', timer: fn => (timers.push(fn), { unref() {} }) });
  assertArt(host.innerHTML, 'loading');
  assert.match(host.innerHTML, /^<div class="message" role="status"><span class="cp-art"/);
  assert.equal(text(host.innerHTML), 'Loading card…');
  timers[0]();
  assert.equal(text(host.innerHTML), 'Loading card… Still loading. This can take a few seconds.');
  assertArt(host.innerHTML, 'loading');
  showLoading(host, 'Loading…', { timer: () => ({ unref() {} }) });
  assert.equal(host.innerHTML, '<div class="message" role="status">Loading…</div>');
});

test('the stylesheet is linked, never animates, and keeps art from stretching its box', () => {
  const css = readFileSync(new URL('../commonplace/web/static/deck-art.css', import.meta.url), 'utf8');
  const index = readFileSync(new URL('../commonplace/web/static/index.html', import.meta.url), 'utf8');
  assert.match(index, /href="\/app\/deck-art\.css"/);
  assert.doesNotMatch(css.replace(/\/\*[^]*?\*\//g, ''), /animation|transition|@keyframes/);
  assert.match(css, /\.cp-art svg \{[^}]*max-width: 100%/);
});

test('the Memory page title row carries one small piece; the title and line stay', () => {
  const html = renderDeckMemory({ boards: [], results: [], filters: {} }, { highlighted: String });
  const heading = html.match(/<header class="memory-heading">[^]*?<\/header>/)[0];
  assertArt(heading, 'header');
  assert.equal(text(heading), 'Memory Your exact words, and which bot saved them.');
});

test('each import step shows one small cue beside its title and the words stay the same', () => {
  const preview = { total: 1, messages: 3, source: 'chatgpt', source_bytes: 900, scope: 'threads' };
  const report = { processed: 1, total: 1, imported: 3, accepted: 3, duplicates: 0, errors: 0, results: [] };
  const cases = [
    [{ phase: 'choose' }, 'step-choose', 'Import memory files'],
    [{ phase: 'previewing' }, 'step-progress', 'Checking file'],
    [{ phase: 'importing', preview }, 'step-progress', 'Importing memory'],
    [{ phase: 'preview', preview, filename: 'a.json' }, 'step-preview', 'Review import'],
    [{ phase: 'report', preview, report, filename: 'a.json' }, 'step-report', 'Import report'],
  ];
  for (const [state, name, title] of cases) {
    const html = renderImport(state, {}, 'deck');
    const header = html.match(/<header>[^]*?<\/header>/)[0];
    assertArt(header, name);
    assert.equal(arts(html).length, 1, `${state.phase}: one piece`);
    assert.equal(text(header), title);
    assert.match(header, /^<header><h1 id="memory-import-title" tabindex="-1">/);
    // The same step in the classic look has no art and the same words.
    const classic = renderImport(state, {}, 'classic');
    assert.equal(arts(classic).length, 0);
  }
  assert.equal(arts(renderImport({ phase: 'error', error: 'Nope' }, {}, 'deck')).length, 0);
});

test('the rail note and Today section lines keep their words and gain art', () => {
  const rail = shellTrees({});
  assertArt(rail, 'rail');
  assert.equal(text(rail), 'No bots yet');
  const bots = botsHTML([]);
  const header = bots.match(/<header>[^]*?<\/header>/)[0];
  assertArt(header, 'rule');
  assert.equal(text(header), 'Your bots Latest update per bot');
});

test('the section line and the Memory title piece stay off the phone and cost no height', () => {
  const css = readFileSync(new URL('../commonplace/web/static/deck-art.css', import.meta.url), 'utf8');
  assert.match(css, /@media \(max-width: 699px\) \{[^}]*data-art="rule"[^}]*display: none/);
  assert.match(css, /data-art="rule"\], \.cp-art\[data-art="header"\], \.cp-art\[data-art\^="step-"\] \{ margin: 0; flex: none; \}/);
});
