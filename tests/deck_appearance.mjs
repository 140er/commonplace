import assert from 'node:assert/strict';
import { test } from 'node:test';
import { B4, B8, RAMPS, valuePass, rampFor, quantize, StageDither } from '../commonplace/web/static/deck-dither.js';
import { appearanceStore, appearanceHTML, DEFAULTS } from '../commonplace/web/static/deck-appearance.js';

test('ordered thresholds, fixed value inputs and hue boundaries match the reference', () => {
  assert.equal(B4.length, 16); assert.equal(B8.length, 64);
  assert.deepEqual(B4.slice(0, 4), [0.5, 8.5, 2.5, 10.5].map(x => x / 16));
  assert.deepEqual(B8.slice(-8), [63, 31, 55, 23, 61, 29, 53, 21].map(x => (x + .5) / 64));
  assert.equal(valuePass(0, 0, 0, 100, 100, 0, 0), 0);
  assert.equal(valuePass(255, 0, 0, 100, 100, 0, 0), .45);
  assert.equal(valuePass(255, 0, 0, 100, 100, 0, .45), .45 * .55);
  assert.ok(Math.abs(valuePass(255, 0, 0, 100, 100, .55, 0) - .2025) < 1e-12);
  assert.equal(valuePass(150, 17, 23, 360, 225, .55, 0), .41376888551622965);
  assert.equal(valuePass(230, 240, 71, 360, 225, .55, .45), .5181483417887613);
  assert.equal(valuePass(80, 90, 150, 98, 211, .7, 0), .26982832779112287);
  assert.equal(rampFor(23, 0, 20, .1), 'grey');
  assert.equal(rampFor(100, 90, 100, .5), 'grey');
  assert.equal(rampFor(40, 190, 230, .99), 'cyan');
  assert.equal(rampFor(30, 20, 100, .9), 'blue');
  assert.equal(rampFor(200, 70, 220, .9), 'purple');
  assert.equal(rampFor(100, 100, 200, .49), 'cyan');
  assert.equal(rampFor(100, 100, 200, .51), 'purple');
  assert.equal(quantize(.5, 'purple', B4[0]), 4);
  assert.equal(quantize(.5, 'purple', B4[12]), 4);
  assert.equal(quantize(.55, 'purple', B4[0]), 4);
  assert.equal(quantize(.55, 'purple', B4[12]), 5);
  assert.equal(quantize(-1, 'grey', .5), 0);
  assert.equal(quantize(2, 'cyan', .5), 7);
  assert.deepEqual(RAMPS.grey, ['#121118', '#1d1c24', '#2a2833', '#3a3746', '#4d4960']);
});

test('appearance survives reload and is isolated by workspace AND person', () => {
  const values = new Map(), storage = { getItem: k => values.get(k), setItem: (k, v) => values.set(k, v) };
  const store = appearanceStore({ workspace: 'space', actor: 'user:one' }, storage);
  const chosen = { scene: 'palace-gate', bayer: 8, vignette: .7, dot: 7, surface: .6 };
  assert.deepEqual(store.read(), DEFAULTS);
  assert.deepEqual(store.write(chosen), chosen);
  assert.deepEqual(appearanceStore({ workspace: 'space', actor: 'user:one' }, storage).read(), chosen);
  assert.deepEqual(appearanceStore({ workspace: 'space', actor: 'user:two' }, storage).read(), DEFAULTS);
  assert.deepEqual(appearanceStore({ workspace: 'other', actor: 'user:one' }, storage).read(), DEFAULTS);
  assert.equal(appearanceStore({}, storage).write(chosen).scene, 'palace-gate');
  assert.equal(values.size, 1, 'never persist an anonymous preference');
  store.write({ scene: '<script>', dot: 100, surface: .1, vignette: -2, bayer: 6 });
  assert.deepEqual(store.read(), { ...DEFAULTS, dot: 7, surface: .6, vignette: 0 });
  assert.deepEqual(appearanceStore({ actor: 'one' }, { getItem() { throw Error(); } }).read(), DEFAULTS);
  assert.deepEqual(appearanceStore({ actor: 'one' }, { getItem: () => '{broken' }).read(), DEFAULTS);
  assert.equal(appearanceStore({ actor: 'one' }, { setItem() { throw Error(); } }).write(chosen).dot, 7);
});

test('Appearance has real canvas previews, accessible controls and no synthetic scene images', () => {
  const html = appearanceHTML(DEFAULTS);
  for (const scene of ['capy-cyber', 'palace-gate', 'nebula-quiet']) assert.ok(html.includes(`data-scene="${scene}"`));
  for (const name of ['vignette', 'dot', 'surface']) assert.ok(html.includes(`data-appearance="${name}"`));
  assert.equal((html.match(/<canvas/g) || []).length, 4);
  assert.match(html, /aria-pressed="true"/);
  assert.doesNotMatch(html, /gradient|<img|style=/);
});

test('reduced motion prevents twinkle, changes stop/restart it, and disposal releases observer and timer', () => {
  let change, resize, disconnected = 0, interval = 0, cleared = 0;
  const motion = { matches: true, addEventListener: (_, fn) => change = fn, removeEventListener: (_, fn) => assert.equal(fn, change) };
  class Observer { constructor(fn) { resize = fn; } observe() {} disconnect() { disconnected++; } }
  const doc = { defaultView: { devicePixelRatio: 1, matchMedia: () => motion, ResizeObserver: Observer,
    setInterval: fn => { interval++; return fn; }, clearInterval: () => cleared++ } };
  const canvas = { ownerDocument: doc, getContext: () => ({}), getBoundingClientRect: () => ({ width: 0, height: 0 }) };
  const renderer = new StageDither(canvas);
  assert.equal(interval, 0);
  let renders = 0; renderer.render = () => renders++;
  resize(); assert.equal(renders, 1);
  motion.matches = false; change(); assert.equal(interval, 1);
  motion.matches = true; change(); assert.equal(cleared, 1);
  renderer.destroy(); assert.equal(disconnected, 1);
  const thumbnail = new StageDither(canvas, { twinkle: false });
  motion.matches = false; change(); assert.equal(interval, 1);
  thumbnail.destroy();
});
