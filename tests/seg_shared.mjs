import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const STATIC = new URL('../commonplace/web/static/', import.meta.url);
const read = (name) => readFileSync(new URL(name, STATIC), 'utf8');
const { boardView } = await import('../commonplace/web/static/board.js');
const { renderNeeds } = await import('../commonplace/web/static/needs.js');
const { segHTML, segStyle } = await import('../commonplace/web/static/seg.js');

// The control's shape with keys and labels removed: tag, classes, role and per-button attributes.
const shape = (html) => {
  const [open] = html.match(/<div class="seg[^"]*"[^>]*>/);
  const buttons = [...html.match(/<div class="seg[^"]*"[^>]*>(.*?)<\/div>/s)[1].matchAll(/<button ([a-z-]+)="[^"]*" aria-pressed="(true|false)">/g)];
  return { open: open.replace(/aria-label="[^"]*"/, ''), attrs: [...new Set(buttons.map((m) => m[2] ? 'aria-pressed' : ''))] };
};
const board = { slug: 'work', name: 'Work' };
const card = (number, extra = {}) => ({ number, version: 1, board: 'work', title: `Card ${number}`, status: 'todo', ...extra });
const boardHTML = boardView({ board, cards: [card(1)], me: 'user:owner', filter: 'all' });
const needsHTML = renderNeeds({ needs: {}, boards: [board], filter: 'all' });

// Both filters are the same control: same markup shape, and the styles live in one place.
assert.deepEqual(shape(needsHTML), shape(boardHTML));
assert.equal(segHTML({ label: 'x', attr: 'data-k', current: 'a', items: [['a', 'A'], ['b', 'B']] }),
  '<div class="seg" role="group" aria-label="x"><button data-k="a" aria-pressed="true">A</button><button data-k="b" aria-pressed="false">B</button></div>');
for (const file of ['board.js', 'needs.js']) {
  assert.ok(!/\.seg\s+button[^{]*\{/.test(read(file)), `${file} must not carry its own segment control styles`);
}
assert.ok(segStyle.includes('.workbench-board') && segStyle.includes('.needs-me'), 'one sheet styles both views');
assert.ok(/needs-me/.test(read('deck-board.css')) && !/needs-me \.seg/.test(read('deck.css')), 'the Deck look styles both through deck-board.css');

// The Needs me filter keeps its behaviour: one pressed key, the filter keys as data attributes.
for (const filter of ['all', 'approvals', 'decisions', 'reviews']) {
  const html = renderNeeds({ needs: {}, boards: [board], filter });
  const pressed = [...html.matchAll(/data-needs-filter="([a-z]+)" aria-pressed="true"/g)].map((m) => m[1]);
  assert.deepEqual(pressed, [filter]);
}
assert.equal([...needsHTML.matchAll(/data-needs-filter="([a-z]+)"/g)].map((m) => m[1]).join(), 'all,approvals,decisions,reviews');

// The header counts exactly the rows the list shows, set-up rows included, for every filter.
const needs = {
  approvals: [card(1, { status: 'review', proposal_status: 'pending' })], asks: [card(2, { status: 'review', ask: 'q' })],
  done_review: [card(3, { status: 'review' })],
  runner_setup: [{ id: 's1', kind: 'runner_settings', message: 'Review settings' }],
  model_roles: [{ kind: 'model_role', board: 'work', role: 'r', message: 'Pick a model' }],
};
for (const [filter, rows] of [['all', 5], ['approvals', 1], ['decisions', 3], ['reviews', 1]]) {
  const html = renderNeeds({ needs, boards: [board], filter });
  const list = html.slice(html.indexOf('<div class="list">'));
  const shown = (list.match(/<article class="row-item/g) || []).length;
  assert.equal(shown, rows, `${filter} rows`);
  assert.ok(html.includes(`${rows} ${rows === 1 ? 'item' : 'items'}, `), `${filter} header counts ${rows}`);
}
