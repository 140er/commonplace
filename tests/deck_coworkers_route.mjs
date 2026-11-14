import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount } from '../commonplace/web/static/deck-workspace.js';

test('the canonical Bot Coworkers route mounts the Capy split view only in Deck', () => {
  for (const deck of [true, false]) {
    const classes = new Set(), regions = new Map(['content', 'parent', 'body', 'sharing'].map(key => [key, { innerHTML: '' }]));
    const header = { hidden: false };
    const root = {
      innerHTML: '', ownerDocument: { documentElement: { dataset: deck ? { ui: 'deck' } : {} } },
      classList: { add: key => classes.add(key), remove: key => classes.delete(key) },
      querySelector: selector => regions.get(/data-(?:workspace|cb)="([^"]+)"/.exec(selector)?.[1]),
      parentElement: { querySelector: selector => selector === '.head' ? header : null },
      addEventListener() {}, removeEventListener() {},
    };
    const ui = mount({ root, route: '#/bots/atlas/coworkers', me: { workspace: 'w', actor: 'u' }, api: () => new Promise(() => {}) });
    try {
      assert.equal(classes.has('capy-coworkers'), deck, 'Deck must use the new split view on the existing route');
      assert.equal(classes.has('deck-workspace'), !deck, 'classic keeps the existing page');
      assert.equal(header.hidden, true);
    } finally { ui.stop(); }
    assert.equal(header.hidden, false);
  }
});
