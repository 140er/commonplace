// Home is reachable from the nav on desktop and phone, and is only offered when a Home screen is registered.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { SECTIONS, renderShellBar, renderShellMore, renderShellTabs } from '../commonplace/web/static/deck-shell.js';

const options = (hash) => ({ user: 'Maya', sections: SECTIONS, section: null, hash, bots: [], directory: {} });
test('the rail has a Waiting on you entry that links home and marks itself current there', () => {
  const home = renderShellBar(options('#/'));
  assert.match(home, /<a class="nav" href="#\/" data-nav="home" aria-current="page">[^]*?<\/svg><span class="deck-row-label">Waiting on you<\/span>/);
  assert.ok(home.indexOf('data-nav="home"') < home.indexOf('data-nav="bots"'), 'Waiting on you comes before the bots');
  assert.doesNotMatch(renderShellBar(options('#/bots')), /data-nav="home" aria-current/);
  assert.match(renderShellBar(options('#/today')), /data-nav="home" aria-current/, 'the old Today address is the same place');
});

test('the phone keeps three tabs and puts Settings first in the account sheet', () => {
  const tabs = renderShellTabs({ ...options('#/'), section: 'home' });
  assert.equal((tabs.match(/<a /g) || []).length, 4, 'Waiting, Chats, Work, Memory');
  assert.match(tabs, /href="#\/" data-nav="home" aria-current="page"/);
  const sheet = renderShellMore(options('#/'));
  assert.match(sheet, /<div class="deck-more-panel"><span class="deck-more-grabber"[^>]*><\/span><button[^>]*class="deck-more-close"[^>]*>[^]*?<\/button><p class="deck-more-account"><b>[^<]*<\/b>[^<]*<\/p><p class="deck-menu-group">Workspace<\/p><a href="#\/settings" data-nav="settings">/);
});

test('without a registered Home screen neither the rail nor the More sheet offers Home', () => {
  assert.doesNotMatch(renderShellBar({ ...options('#/'), homeOn: false }), /data-nav="home"/);
  assert.doesNotMatch(renderShellMore({ ...options('#/'), homeOn: false }), /data-nav="home"/);
});
