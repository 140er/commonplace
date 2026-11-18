import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const source = readFileSync('commonplace/web/static/app.js', 'utf8');
const content = { innerHTML: '' }, viewer = { actor: 'user:reader' };
const context = { state: { me: viewer }, remember() {}, location: { hash: '#/chat' },
  root: { querySelector: () => content },
  api: async () => { throw Object.assign(new Error('service unavailable'), { status: 503 }); },
};
runInNewContext(source.slice(source.indexOf('async function route()'), source.indexOf('function openingCard(')), context);
assert.equal(await context.route(), false);
assert.equal(context.state.me, viewer);
assert.match(content.innerHTML, /Could not load projects/);
assert.match(content.innerHTML, /Retry/);
assert.doesNotMatch(content.innerHTML, /Sign in|No bots yet/);
assert.equal(context.state.bootRoutePending, true);
let scheduled = 0;
context.polling = false; context.schedule = () => scheduled++;
const pollingStart = source.indexOf('async function poll()');
runInNewContext(source.slice(pollingStart, source.indexOf('document.addEventListener("visibilitychange"', pollingStart)), context);
await context.poll();
assert.equal(scheduled, 1, 'Visibility polling remains paused while route discovery is unresolved');
