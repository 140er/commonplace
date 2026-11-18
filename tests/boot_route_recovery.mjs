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
await context.route();
assert.equal(context.state.me, viewer);
assert.match(content.innerHTML, /Could not load projects/);
assert.match(content.innerHTML, /Retry/);
assert.doesNotMatch(content.innerHTML, /Sign in|No bots yet/);
