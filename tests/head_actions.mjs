import assert from 'node:assert/strict';
import { test } from 'node:test';
import { once, releaseActions, seatActions, seatedIn } from '../commonplace/web/static/head-actions.js';

// A tiny tree: enough of the DOM for seating, home and connection checks.
class Node {
  constructor(name, doc) { this.name = name; this.ownerDocument = doc; this.parentNode = null; this.children = []; this.root = false; }
  get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n.root === true; }
  get nextSibling() { const list = this.parentNode?.children || []; return list[list.indexOf(this) + 1] || null; }
  append(node) { node.remove(); node.parentNode = this; this.children.push(node); }
  insertBefore(node, next) { node.remove(); node.parentNode = this; const at = next ? this.children.indexOf(next) : -1; if (at < 0) this.children.push(node); else this.children.splice(at, 0, node); }
  remove() { if (!this.parentNode) return; this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1); this.parentNode = null; }
}
function page({ ui = 'deck', wide = true } = {}) {
  const listeners = [];
  const doc = { documentElement: { dataset: { ui } } };
  const make = (name) => new Node(name, doc);
  const top = make('body'); top.root = true;
  const head = make('head-actions'), content = make('content');
  top.append(head); top.append(content);
  doc.querySelector = (selector) => (selector === '#head-actions' ? head : null);
  globalThis.matchMedia = () => ({ matches: wide, addEventListener: (_type, fn) => listeners.push(fn) });
  const resize = (next) => { wide = next; for (const fn of listeners) fn(); };
  return { make, head, content, resize, listeners };
}
const names = (node) => node.children.map((child) => child.name);

test('a page action is seated in the desktop bar and goes home on a phone', () => {
  const { make, head, content, resize } = page();
  const bar = make('actions'), first = make('first'), add = make('add'), last = make('last');
  [first, add, last].forEach((node) => bar.append(node)); content.append(bar);
  assert.equal(seatActions(add), true);
  assert.deepEqual(names(head), ['add']);
  assert.deepEqual(names(bar), ['first', 'last']);
  assert.equal(seatedIn(head), add);
  resize(false);
  assert.deepEqual(names(head), []);
  assert.deepEqual(names(bar), ['first', 'add', 'last'], 'back at its own place in the page');
  resize(true);
  assert.deepEqual(names(head), ['add']);
  releaseActions(add);
  assert.deepEqual(names(bar), ['first', 'add', 'last']);
});

test('a phone or the Classic look leaves the action in the page', () => {
  for (const options of [{ wide: false }, { ui: 'classic' }]) {
    const { make, head, content } = page(options);
    const add = make('add'); content.append(add);
    assert.equal(seatActions(add), false);
    assert.deepEqual(names(head), []);
    assert.equal(add.parentNode, content);
  }
});

test('seating another element hands the first one back; a page that is gone drops it', () => {
  const { make, head, content } = page();
  const home = make('home'), one = make('one'), two = make('two');
  content.append(home); home.append(one); content.append(two);
  seatActions(one);
  seatActions(two);
  assert.deepEqual(names(head), ['two']);
  assert.deepEqual(names(home), ['one']);
  home.remove();
  releaseActions(two);
  assert.deepEqual(names(head), []);
  assert.equal(two.parentNode, content, 'its page was still there, so it went home');
  const lost = make('lost'), gone = make('gone'); gone.append(lost); content.append(gone);
  seatActions(lost); gone.remove(); releaseActions(lost);
  assert.deepEqual(names(head), []);
  assert.equal(lost.parentNode, null);
});

test('a redraw of the page title keeps the seated element; a stale one never lands in another page', () => {
  const { make, head, content, resize } = page();
  const home = make('home'), add = make('add'); home.append(add); content.append(home);
  seatActions(add);
  head.children.splice(0); add.parentNode = null; // the title redraw cleared the bar
  home.remove(); content.children.splice(0); // and the page moved on
  resize(false); resize(true);
  assert.deepEqual(names(head), [], 'nothing from the old page comes back');
  assert.equal(seatedIn(head), null);
});

test('a click that reaches both the page and the bar is handled once', () => {
  const calls = [], handler = once((event) => calls.push(event.id));
  const event = { id: 1 };
  handler(event); handler(event); handler({ id: 2 });
  assert.deepEqual(calls, [1, 2]);
});

test('without a document or a media query nothing moves', () => {
  delete globalThis.matchMedia;
  const doc = { documentElement: { dataset: { ui: 'deck' } }, querySelector: () => null };
  const node = new Node('add', doc);
  assert.equal(seatActions(node), false);
  assert.equal(seatActions({}), false);
});
