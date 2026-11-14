import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createMemory } from '../commonplace/web/static/memory.js';

const board = { slug: 'work', name: 'Workspace', wing: 'project_work' };
const me = { actor: 'user:owner', workspace: 'workspace-one', people: {} };
const text = '  Agent note: café\ttwo  spaces\nsecond line — exact words.  ';
const note = { id: 'drw_note', theme: 'inbox', topic: 'general', text, author: 'agent-one', at: '2026-11-14T09:00:00Z' };
const tick = () => new Promise(resolve => setImmediate(resolve));
function mount() {
  const listeners = new Map();
  const node = { innerHTML: '', listeners, addEventListener(type, handler) { listeners.set(type, handler); }, removeEventListener(type) { listeners.delete(type); }, querySelector() { return null; } };
  node.ownerDocument = { documentElement: { dataset: { ui: 'deck' } } };
  return node;
}
const api = (paths, rooms) => async (path) => {
  paths.push(path);
  if (path === '/api/memory/rooms') return { rooms };
  if (path.startsWith('/api/memory/search')) return { results: new URL(path, 'http://x').searchParams.get('theme') === 'inbox' ? [note] : [] };
  return { results: [] };
};

test('the inbox room is searched next to the project rooms and shows the exact words', async () => {
  const node = mount(), paths = [];
  const ui = createMemory({ mount: node, boards: [board], me, api: api(paths, [{ wing: 'inbox', name: 'Agent notes' }]) });
  await ui.search({ query: 'exact' });
  assert.ok(paths.some(path => path.startsWith('/api/memory/search?q=exact&theme=inbox')));
  assert.ok(paths.some(path => path.startsWith('/api/memory/search?q=exact&theme=project_work')));
  assert.ok(!paths.some(path => path.includes('notes%3Ainbox') || path.includes('/api/chat/notes')));
  assert.match(node.innerHTML, /data-memory-room="notes:inbox"[^>]*>Agent notes/);
  assert.match(node.innerHTML, /data-memory-room="work"[^>]*>Workspace/);
  assert.ok(node.innerHTML.replace(/<\/?mark>/g, '').includes(text));
  assert.ok(node.innerHTML.includes('Saved by') && node.innerHTML.includes('Agent notes'));
  ui.stop();
});

test('a room the server does not list, or a project that already owns the wing, adds nothing', async () => {
  for (const rooms of [[], [{ wing: 'project_work', name: 'Agent notes' }]]) {
    const node = mount(), paths = [];
    const ui = createMemory({ mount: node, boards: [board], me, api: api(paths, rooms) });
    await ui.search({ query: 'exact' });
    assert.ok(!paths.some(path => path.includes('theme=inbox')));
    assert.ok(!node.innerHTML.includes('Agent notes'));
    ui.stop();
  }
});

test('a failing room listing leaves the project rooms working', async () => {
  const node = mount(), paths = [];
  const ui = createMemory({ mount: node, boards: [board], me, api: async path => { paths.push(path); if (path === '/api/memory/rooms') throw new Error('down'); return { results: [] }; } });
  await ui.search({ query: 'exact' });
  assert.ok(paths.some(path => path.startsWith('/api/chat/work/search')));
  assert.ok(!node.innerHTML.includes('Some sources could not be searched'));
  ui.stop();
});
