import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createMemory, renderMemory } from '../commonplace/web/static/memory.js';
import { ApiError } from '../commonplace/web/static/api.js';

const board = { slug: 'work', name: 'Workspace', wing: 'wing/work' };
const me = { actor: 'user:owner', workspace: 'workspace-one', people: {} };
const note = (id, text) => ({ id, theme: 'wing/work', author: 'user:owner', text, at: '2026-11-13T13:59:00Z' });
const warming = (seconds = 2) => new ApiError(503, { error: 'Memory is starting; try again in a few seconds', retry_after_s: seconds, warming_up: true });
const busy = (seconds = 1) => new ApiError(503, { error: 'Memory is busy indexing; try again in a moment', retry_after_s: seconds });
const plain = (node) => node.innerHTML.replace(/<[^>]*>/g, '');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
// Settle helper: wait for a condition, never a fixed sleep for the thing under test.
async function until(check, what, ms = 3000) {
  const end = Date.now() + ms;
  while (!check()) { if (Date.now() > end) assert.fail(`never settled: ${what}`); await sleep(1); }
}
function mount(look) {
  const listeners = new Map();
  const node = { innerHTML: '', listeners, addEventListener(type, fn) { listeners.set(type, fn); }, removeEventListener(type) { listeners.delete(type); }, querySelector() { return null; } };
  if (look === 'deck') node.ownerDocument = { documentElement: { dataset: { ui: 'deck' } } };
  return node;
}
const click = (node, dataset) => node.listeners.get('click')({ target: { closest: (selector) => (selector.includes('data-memory-result') ? null : { dataset }) } });
// A fake memory worker: `script` answers each note search in turn; the harness counts the
// requests and the most that were ever in flight at once.
function harness(look, script, options = {}) {
  const node = mount(look), asked = [];
  let inflight = 0, peak = 0;
  const ui = createMemory({ mount: node, boards: [board], me, retryTickMs: 4, ...options, api: async (path) => {
    if (path === '/api/memory/rooms') return { rooms: [] };
    const url = new URL(path, 'http://x');
    asked.push(url.searchParams.get('q'));
    inflight++; peak = Math.max(peak, inflight);
    try { await sleep(1); const step = script(asked.length, url.searchParams.get('q')); if (step instanceof Error) throw step; return step; } finally { inflight--; }
  } });
  return { node, ui, asked, peak: () => peak };
}
const notesOnly = (query) => ({ query, source: 'note' });

for (const look of ['classic', 'deck']) {
  test(`${look}: a warming memory shows the plain line, counts down, retries and shows the results`, async () => {
    const { node, ui, asked } = harness(look, (n) => (n < 3 ? warming(2) : { results: [note('a', 'Found after warming')] }));
    const done = ui.search(notesOnly('exact'));
    await until(() => node.innerHTML.includes('Memory is warming up, retrying in 2 s'), 'first countdown value');
    assert.ok(node.innerHTML.includes('value="exact"'), 'the typed query stays in the box');
    await until(() => node.innerHTML.includes('Memory is warming up, retrying in 1 s'), 'countdown ticks down');
    await done;
    assert.equal(asked.length, 3, 'two 503 answers then one success');
    assert.ok(node.innerHTML.includes('Found after warming'));
    assert.ok(!node.innerHTML.includes('retrying in'), 'the line goes away once the search answers');
    assert.ok(!node.innerHTML.includes('role="alert"'));
    ui.stop();
  });

  test(`${look}: a busy memory says busy, not warming up`, async () => {
    const { node, ui } = harness(look, (n) => (n < 2 ? busy(1) : { results: [note('a', 'Found when idle')] }));
    const done = ui.search(notesOnly('exact'));
    await until(() => node.innerHTML.includes('Memory is busy, retrying in 1 s'), 'busy line');
    assert.ok(!node.innerHTML.includes('warming up'));
    await done;
    assert.ok(node.innerHTML.includes('Found when idle'));
    ui.stop();
  });

  test(`${look}: after three retries it falls back to the existing message with a Retry button`, async () => {
    let healthy = false;
    const { node, ui, asked } = harness(look, () => (healthy ? { results: [note('a', 'Found on manual retry')] } : warming(1)));
    await ui.search(notesOnly('exact'));
    assert.equal(asked.length, 4, 'one request plus three automatic retries, never more');
    assert.ok(!node.innerHTML.includes('retrying in'), 'no countdown line after giving up');
    assert.match(node.innerHTML, /role="alert"[\s\S]*Memory could not be searched just now\. Try again\./);
    assert.ok(node.innerHTML.includes('data-memory-action="retry"'), 'Retry button');
    assert.ok(!node.innerHTML.includes('No exact words match'), 'a failure is not an empty result');
    await sleep(40);
    assert.equal(asked.length, 4, 'nothing retries on its own after the fallback');
    healthy = true;
    click(node, { memoryAction: 'retry' });
    await until(() => node.innerHTML.includes('Found on manual retry'), 'manual retry');
    assert.equal(asked.length, 5);
    ui.stop();
  });

  test(`${look}: one request at a time, and the wait is capped at 10 s`, async () => {
    const { node, ui, asked, peak } = harness(look, (n) => (n < 4 ? warming(60) : { results: [] }), { retryTickMs: 1 });
    const done = ui.search(notesOnly('exact'));
    await until(() => node.innerHTML.includes('retrying in 10 s'), 'capped wait');
    assert.ok(!node.innerHTML.includes('retrying in 60 s'));
    await done;
    assert.equal(asked.length, 4);
    assert.equal(peak(), 1, 'never two searches in flight');
    ui.stop();
  });

  test(`${look}: a new query cancels the pending retry and nothing old comes back`, async () => {
    const { node, ui, asked } = harness(look, (n, q) => (q === 'second' ? { results: [note('b', 'Second answer')] } : warming(5)), { retryTickMs: 20 });
    void ui.search(notesOnly('first'));
    await until(() => node.innerHTML.includes('retrying in 5 s'), 'first query waiting');
    await ui.search(notesOnly('second'));
    assert.ok(plain(node).includes('Second answer'));
    assert.ok(!node.innerHTML.includes('retrying in'));
    await sleep(150);
    assert.deepEqual(asked, ['first', 'second'], 'the cancelled retry never fires');
    assert.ok(plain(node).includes('Second answer'));
    ui.stop();
  });

  test(`${look}: results already on screen stay while the retry waits`, async () => {
    const { node, ui } = harness(look, (n) => (n === 1 ? { results: [note('a', 'Earlier answer')] } : n < 4 ? warming(1) : { results: [note('c', 'Fresh answer')] }));
    await ui.search(notesOnly('first'));
    assert.ok(node.innerHTML.includes('Earlier answer'));
    const done = ui.search(notesOnly('second'));
    await until(() => node.innerHTML.includes('retrying in'), 'waiting');
    assert.ok(node.innerHTML.includes('Earlier answer'), 'previous results stay while memory warms up');
    assert.ok(node.innerHTML.includes('value="second"'));
    await done;
    assert.ok(node.innerHTML.includes('Fresh answer')); assert.ok(!node.innerHTML.includes('Earlier answer'));
    ui.stop();
  });

  test(`${look}: leaving Memory stops the retries`, async () => {
    const { node, ui, asked } = harness(look, () => warming(5), { retryTickMs: 20 });
    void ui.search(notesOnly('exact'));
    await until(() => node.innerHTML.includes('retrying in'), 'waiting');
    ui.stop();
    await sleep(120);
    assert.equal(asked.length, 1);
  });
}

test('a 503 without a retry hint, or a different failure, keeps the existing message and never retries', async () => {
  for (const failure of [new ApiError(503, { error: 'down' }), new ApiError(500, { error: 'boom', retry_after_s: 1 }), new Error('network')]) {
    const { node, ui, asked } = harness('deck', () => failure);
    await ui.search(notesOnly('exact'));
    assert.equal(asked.length, 1);
    assert.ok(!node.innerHTML.includes('retrying in'));
    assert.ok(node.innerHTML.includes('Memory could not be searched just now. Try again.'));
    ui.stop();
  }
});

test('the line renders from state in both looks, escaped and without the loading placeholder', () => {
  for (const look of ['classic', 'deck']) {
    const html = renderMemory({ look, boards: [board], results: [], filters: { query: 'exact' }, busy: true, retry: { left: 3, warming: true } });
    assert.match(html, /<p class="memory-retry" role="status" data-memory-retry>Memory is warming up, retrying in 3 s<\/p>/);
    assert.ok(!html.includes('memory-loading'));
    assert.match(renderMemory({ look, boards: [board], results: [], filters: {}, busy: true, retry: { left: 7, warming: false } }), /Memory is busy, retrying in 7 s/);
    assert.ok(!renderMemory({ look, boards: [board], results: [], filters: {}, busy: true }).includes('memory-retry'));
  }
});
