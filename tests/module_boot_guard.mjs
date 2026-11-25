import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
const source = readFileSync('commonplace/web/static/boot-guard.js', 'utf8');
const mode = process.argv[2], saved = new Map();
let now = 0, sequence = 0, reloads = 0;
function page() {
  const timers = new Map(), events = new Map();
  const status = {textContent:'Loading Commonplace…'}, retry = {hidden:true};
  let loading = true, observer;
  const context = {Date: {now: () => now}, location: {reload() {reloads++;}},
    sessionStorage: {getItem: key => saved.get(key) || null,
      setItem(key, value) {if (mode === 'storage-off') throw new Error('Disabled'); saved.set(key, value);},
      removeItem: key => saved.delete(key)},
    setTimeout(fn, ms) {const id = ++sequence; timers.set(id, {fn,at:now+ms}); return id;},
    clearTimeout(id) {timers.delete(id);},
    document: {readyState:'loading', querySelector(selector) {
      if (selector === '[data-module-loading]') return loading ? {} : null;
      if (selector === '[data-module-status]') return status;
      if (selector === '[data-module-retry]') return retry;
    }},
    MutationObserver: class {constructor(callback) {observer = callback;} observe() {} disconnect() {}},
    addEventListener(name, callback) {events.set(name, callback);},
  };
  runInNewContext(source, context);
  return {context, timers, status, retry,
    fail() {events.get('error')({target:{type:'module'}});},
    finish() {loading = false; context.document.readyState = 'complete'; observer();},
    advance(ms) {now += ms; for (const [id,t] of [...timers]) if (t.at <= now) {timers.delete(id);t.fn();}},
    events};
}
const p = page();
if (mode === 'pending') {
  p.context.commonplaceBootGuard.started();
  assert.equal(p.timers.size, 0, 'Identity owns its own deadline after module evaluation');
  assert.equal(p.status.textContent, 'Loading Commonplace…');
  assert.equal(p.retry.hidden, true);
} else if (mode === 'recovery') {
  p.fail(); p.context.commonplaceBootGuard.started(); p.finish(); p.advance(30000);
  assert.equal(reloads, 0); assert.equal(saved.size, 0);
} else if (mode === 'optional-preload') {
  p.events.get('error')({target:{rel:'modulepreload'}});
  assert.equal(p.retry.hidden, true);
} else if (mode === 'backoff') {
  for (const [index, delay] of [1000,2000,4000].entries()) {
    const current = index === 0 ? p : page(); current.fail();
    assert.equal(reloads, index); current.advance(delay-1); assert.equal(reloads, index);
    current.advance(1); assert.equal(reloads, index+1);
  }
  const last = page(); last.fail(); assert.equal(last.timers.size, 0);
  last.retry.onclick(); assert.equal(reloads, 4, 'Manual Retry can restart an exhausted graph');
} else {
  if (mode === 'watchdog') p.advance(30000);
  else p.fail();
  assert.match(p.status.textContent, /Could not reach Commonplace, retrying/);
  assert.doesNotMatch(p.status.textContent, /Sign in|Email a sign-in link/);
  assert.equal(p.retry.hidden, false);
  if (mode === 'storage-off') assert.equal(p.timers.size, 0, 'Blocked storage must not create a reload loop');
  else {p.advance(999); assert.equal(reloads, 0); p.advance(1); assert.equal(reloads, 1);}
}
