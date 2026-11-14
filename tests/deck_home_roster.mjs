// Home tells loading, failure and an empty roster apart, and is reachable from the nav on desktop and phone.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { escape as e } from '../commonplace/web/static/api.js';
import * as home from '../commonplace/web/static/deck-home.js';
import { SECTIONS, renderShellBar, renderShellMore, renderShellTabs } from '../commonplace/web/static/deck-shell.js';

// Timers and promise chains settle over a few turns; never count on one.
const { mount, rosterNote } = home;
const settle = async () => { for (let i = 0; i < 12; i++) await new Promise((resolve) => setImmediate(resolve)); };
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };

// A small DOM: every selector yields one persistent element; only the fields Home reads and writes are modelled.
function dom() {
  const els = new Map(), listeners = new Map();
  const el = (key) => {
    if (!els.has(key)) els.set(key, { hidden: false, innerHTML: '', textContent: '', value: '', dataset: {}, disabled: false, options: [], selectedOptions: [], className: '', classList: { add() {}, remove() {} }, setAttribute() {}, addEventListener() {}, removeEventListener() {}, focus() {}, querySelector: (q) => el(`${key}>${q}`), closest: () => el(`${key}^`) });
    return els.get(key);
  };
  const fields = { text: el('text'), bot: el('bot'), coworker: el('coworker'), machine: el('machine'), model: el('model'), effort: el('effort') };
  const form = { elements: fields, querySelector: () => el('submit'), addEventListener() {}, removeEventListener() {}, requestSubmit() {} };
  const root = {
    html: '', ownerDocument: { hidden: false }, classList: { add() {}, remove() {} }, closest: () => null,
    set innerHTML(value) { this.html = value; }, get innerHTML() { return this.html; },
    querySelector: (q) => q === 'form' ? form : el(q),
    addEventListener: (type, fn) => listeners.set(type, fn), removeEventListener: (type) => listeners.delete(type),
  };
  return { root, empty: el('.bh-empty'), botPicker: el('.bh-bot'), click: (selector) => listeners.get('click')?.({ target: { closest: (q) => q === selector ? {} : null } }) };
}
function fixture(api) {
  const d = dom(), calls = [];
  const ui = mount({ root: d.root, api: (path, options) => { calls.push(path); return api(path, options); }, e, me: {}, needs: {} });
  return { ...d, ui, calls, roster: () => calls.filter((path) => path === '/api/bots').length };
}
const ok = (bots) => (path) => Promise.resolve(path === '/api/bots' ? { bots } : { conversations: [] });
const failure = (status = 500) => Object.assign(new Error('boom'), { status });

test('before the first read lands Home says Loading, never "No bots yet"', async () => {
  const gate = deferred();
  const f = fixture((path) => path === '/api/bots' ? gate.promise : Promise.resolve({ conversations: [] }));
  await settle();
  assert.equal(f.empty.hidden, false);
  assert.match(f.empty.innerHTML, /Loading bots/);
  assert.doesNotMatch(f.empty.innerHTML, /No bots yet/);
  gate.resolve({ bots: [{ id: 'atlas', name: 'Atlas', board: 'atlas' }] }); await settle();
  assert.equal(f.empty.hidden, true);
  assert.equal(f.empty.innerHTML, '');
  assert.equal(f.botPicker.hidden, false);
  f.ui.stop();
});

test('a failing roster read says it could not load, retries by itself, and Retry works', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let broken = true;
  const f = fixture((path) => path === '/api/bots' ? (broken ? Promise.reject(failure()) : Promise.resolve({ bots: [{ id: 'atlas', name: 'Atlas', board: 'atlas' }] })) : Promise.resolve({ conversations: [] }));
  await settle();
  assert.match(f.empty.innerHTML, /Could not load bots, retrying/);
  assert.match(f.empty.innerHTML, /data-bh-reload/);
  assert.doesNotMatch(f.empty.innerHTML, /No bots yet/);
  assert.equal(f.roster(), 1);
  // The automatic retry fires without any click, and keeps the failure note while it still fails.
  t.mock.timers.tick(3000); await settle();
  assert.equal(f.roster(), 2);
  assert.match(f.empty.innerHTML, /Could not load bots, retrying/);
  // The button re-reads at once, and a good answer replaces the note with the bots.
  broken = false;
  f.click('[data-bh-reload]'); await settle();
  assert.equal(f.roster(), 3);
  assert.equal(f.empty.hidden, true);
  assert.equal(f.botPicker.hidden, false);
  // No further automatic retries once it has loaded.
  t.mock.timers.tick(60000); await settle();
  assert.equal(f.roster(), 3);
  f.ui.stop();
});

test('"No bots yet" shows only when the read succeeded with an empty list', async () => {
  const f = fixture(ok([]));
  await settle();
  assert.equal(f.empty.hidden, false);
  assert.match(f.empty.innerHTML, /No bots yet/);
  assert.match(f.empty.innerHTML, /href="#\/bots\?new=1"[^>]*>New bot/);
  assert.equal(f.botPicker.hidden, true);
  f.ui.stop();
});

test('a later failure keeps the bots already shown instead of switching to a failure or empty note', async () => {
  let broken = false;
  const f = fixture((path) => path === '/api/bots' ? (broken ? Promise.reject(failure()) : Promise.resolve({ bots: [{ id: 'atlas', name: 'Atlas', board: 'atlas' }] })) : Promise.resolve({ conversations: [] }));
  await settle();
  broken = true; await f.ui.refresh(); await settle();
  assert.equal(f.empty.hidden, true);
  assert.equal(f.botPicker.hidden, false);
  f.ui.stop();
});

test('the note text is a pure function of status and count', () => {
  assert.match(rosterNote('loading', 0), /^Loading bots/);
  assert.match(rosterNote('error', 3), /Could not load bots, retrying/);
  assert.equal(rosterNote('ready', 2), '');
  assert.match(rosterNote('ready', 0), /^No bots yet\./);
});

const options = (hash) => ({ user: 'Maya', sections: SECTIONS, section: null, hash, bots: [], directory: {} });
test('the rail has a Home entry that links to home and marks itself current there', () => {
  const home = renderShellBar(options('#/'));
  assert.match(home, /<a class="nav" href="#\/" data-nav="home" aria-current="page">[^]*?<\/svg>Home<\/a>/);
  assert.ok(home.indexOf('>Home</a>') < home.indexOf('data-nav="today"'), 'Home comes before Today');
  assert.doesNotMatch(renderShellBar(options('#/today')), /data-nav="home" aria-current/);
  assert.match(renderShellBar(options('#/today')), /data-nav="home"/);
});

test('the phone keeps four tabs plus More and offers Home first in the More sheet', () => {
  const tabs = renderShellTabs({ ...options('#/today'), section: 'today' });
  assert.equal((tabs.match(/<a /g) || []).length, 4, 'the bottom bar is not widened');
  assert.doesNotMatch(tabs, /data-nav="home"/);
  const sheet = renderShellMore(options('#/today'));
  assert.match(sheet, /<div class="deck-more-panel"><a href="#\/" data-nav="home">[^]*?<\/svg>Home<\/a>/);
  assert.match(renderShellMore(options('#/')), /data-nav="home" aria-current="page"/);
});
