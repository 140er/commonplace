import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const [path, method] = process.argv.slice(2);
let now = 0, sequence = 0, settled = false, result;
const timers = new Map();
const context = {
  AbortController, Event, document: { dispatchEvent() {} },
  setTimeout(fn, ms) { const id = ++sequence; timers.set(id, { fn, at: now + ms }); return id; },
  clearTimeout(id) { timers.delete(id); },
  fetch(url) {
    assert.equal(url, path);
    // A valid large read takes eight seconds, including body decoding.
    return Promise.resolve({ status: 200, ok: true, json: () => new Promise(resolve => {
      context.setTimeout(() => resolve({ words: 'verbatim result' }), 8000);
    }) });
  },
};
runInNewContext(readFileSync('commonplace/web/static/api.js', 'utf8').replace(/export /g, ''), context);
const load = context.api(path, method ? { method } : {}).then(value => {
  result = value; settled = true;
}, error => { result = error; settled = true; });
const tick = () => new Promise(resolve => setImmediate(resolve));
await tick();
for (const at of [3000, 8000]) {
  now = at;
  for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.fn(); }
  await tick();
  if (at === 3000) assert.equal(settled, false, 'Ordinary GET must retain its unlimited default');
}
await load;
assert.equal(result.words, 'verbatim result');
assert.equal(timers.size, 0);
