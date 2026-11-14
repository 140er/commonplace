import assert from 'node:assert/strict';
import { test } from 'node:test';
import { deckHome, renderDeckBar, renderDeckTabs } from '../commonplace/web/static/deck.js';
import { resolveRoute } from '../commonplace/web/static/routes.js';

test('Home stays on the undimmed root route', () => assert.equal(deckHome(), '#/'));
test('root resolution preserves Home in Deck and the classic default', () => {
  assert.equal(resolveRoute('#/', { deck: true, boards: [{ slug: 'work' }] }), '#/');
  assert.equal(resolveRoute('#/', { deck: false, boards: [{ slug: 'work' }] }), '#/bots');
});
test('Today count is the personal queue count even when bots have more unread work', () => {
  for (const html of [renderDeckBar({ needs: 2, botsWaiting: 9 }), renderDeckTabs({ needs: 2, botsWaiting: 9 })]) {
    assert.match(html, /data-nav="today"[^>]*>[\s\S]*?class="count need">2/);
    assert.doesNotMatch(html, /class="count need">9/);
  }
});
