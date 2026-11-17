import assert from 'node:assert/strict';
import { test } from 'node:test';
import { renderMemory, createMemory, searchMemory } from '../commonplace/web/static/memory.js';
import { noteProblem, saveNote, renderNote, createNote } from '../commonplace/web/static/memory-note.js';

test('both Memory looks have an Add a note action before search', () => {
  for (const look of ['deck', 'classic']) {
    const html = renderMemory({ look });
    assert.match(html, /data-memory-action="note"[^>]*>Add a note/);
    assert.ok(html.indexOf('Add a note') < html.indexOf('data-memory-search'));
  }
});

test('a workspace with no projects still searches its standalone notes', async () => {
  const row = { id: 'standalone', theme: 'shared', text: 'needle', author: 'user:writer' };
  const page = await searchMemory({ boards: [], query: 'needle', api: async path => {
    assert.ok(path.startsWith('/api/memory/search?')); return { results: [row] };
  } });
  assert.equal(page.results[0].text, row.text);
  assert.equal(page.results[0].board, 'notes:shared');
});

for (const status of [503, 507, 403, 400]) test(`failed save ${status} keeps exact draft and destination for an explicit retry`, async () => {
  const text = '  café 🧭\n  ', calls = [];
  let failure = true, draws = 0;
  const note = createNote({ alive: () => true, draw: () => draws++, api: async (path, options) => {
    if (path.includes('compose=1')) return { composer: { default_wing: 'shared', wings: [{ wing: 'shared', rooms: ['general'] }] } };
    calls.push(options.body);
    if (failure) { const error = new Error('Use up to 100,000 characters.'); error.status = status; error.payload = { retry_after_s: 2 }; throw error; }
    return { id: 'saved', theme: 'shared' };
  } });
  await note.open(); note.state.text = text;
  await note.save();
  assert.equal(note.state.text, text); assert.equal(note.state.wing, 'shared');
  assert.equal(note.state.saved, null); assert.equal(calls.length, 1);
  assert.match(note.state.error, status === 503 ? /Memory is busy.*Try again in about 2 seconds/ : status === 507 ? /Storage is low.*Free some space and try again/ : status === 403 ? /Check your access/ : /100,000 characters/);
  failure = false; await note.save();
  assert.deepEqual(calls[0], calls[1]); assert.equal(note.state.text, '');
  assert.equal(note.state.saved.id, 'saved'); assert.ok(draws > 0);
});

test('saving locks double submission and leaving Memory fences a late answer', async () => {
  let alive = true, finish, calls = 0, draws = 0;
  const note = createNote({ alive: () => alive, draw: () => draws++, api: async path => {
    if (path.includes('compose=1')) return { composer: { default_wing: 'shared', wings: [{ wing: 'shared' }] } };
    calls++; return new Promise(resolve => { finish = resolve; });
  } });
  await note.open(); note.state.text = 'exact';
  const first = note.save(); await note.save(); assert.equal(calls, 1);
  alive = false; const before = draws; finish({ id: 'saved', theme: 'shared' }); await first;
  assert.equal(draws, before); assert.equal(note.state.text, 'exact'); assert.equal(note.state.saved, null);
});

test('the Memory controller submits the note route and preserves a draft across room loading', async () => {
  const listeners = new Map(), calls = [];
  const mount = { innerHTML: '', addEventListener(type, handler) { listeners.set(type, handler); }, removeEventListener(type) { listeners.delete(type); }, querySelector() { return null; } };
  const ui = createMemory({ mount, api: async (path, options) => {
    calls.push([path, options]);
    if (path.includes('compose=1')) return { composer: { default_wing: 'shared', wings: [{ wing: 'shared', name: 'Workspace notes' }] } };
    return path === '/api/memory' ? { id: 'saved', theme: 'shared' } : { rooms: [] };
  } });
  const action = value => listeners.get('click')({ target: { closest: selector => selector === '[data-memory-action]' ? { dataset: { memoryAction: value } } : null } });
  action('note'); await new Promise(resolve => setImmediate(resolve));
  listeners.get('input')({ target: { name: 'note-text', value: '  exact 🧭\n  ' } });
  ui.setProject('other'); assert.match(mount.innerHTML, />  exact 🧭\n  <\/textarea>/);
  listeners.get('submit')({ preventDefault() {}, target: { matches: selector => selector === '[data-memory-note]' } });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.find(([path]) => path === '/api/memory')[1].body.text, '  exact 🧭\n  ');
  assert.match(mount.innerHTML, /Saved to Workspace notes/);
  ui.stop(); assert.equal(listeners.size, 0);
});
test('validation counts unicode characters and never trims the submitted words', async () => {
  assert.equal(noteProblem(' \n\t'), 'Write a note first.');
  assert.equal(noteProblem('x'.repeat(100001)), 'Use up to 100,000 characters.');
  assert.equal(noteProblem('🧭'.repeat(50001)), '');
  const calls = [], text = '  café é 漢字 🧭\t\r\n' + 'long words\n'.repeat(2400) + '  ';
  await saveNote({ text, wing: 'shared', room: 'general' }, async (path, options) => { calls.push([path, options]); return { id: 'note' }; });
  assert.deepEqual(calls, [['/api/memory', { method: 'POST', body: { text, theme: 'shared', topic: 'general' } }]]);
  await assert.rejects(saveNote({ text: '' }, () => { throw Error('must not call'); }), /Write a note first/);
});
test('preview, destination, errors and saved drawer are escaped plain words', () => {
  const state = { open: true, text: '  <script>漢字\n  ', wing: 'shared', room: 'general',
    choices: { default_wing: 'shared', wings: [{ wing: 'shared', name: 'Workspace notes' }] } };
  const html = renderNote(state);
  assert.match(html, /value="shared" selected/);
  assert.match(html, /data-note-preview>  &lt;script&gt;漢字\n  <\/pre>/);
  assert.match(renderNote({ ...state, error: '<busy>' }), /role="alert">&lt;busy&gt;/);
  const saved = renderNote({ ...state, saved: { id: 'opaque', theme: 'shared' } });
  assert.match(saved, /Saved to Workspace notes/);
  assert.match(saved, /href="#\/memory\?drawer=opaque"[^>]*>Open drawer/);
  assert.match(saved, /searchable shortly/);
});
