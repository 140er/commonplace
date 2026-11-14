import assert from 'node:assert/strict';
import { test } from 'node:test';
import { renderMemory, renderMemoryPanel, createMemory } from '../commonplace/web/static/memory.js';
import { createMemoryImport, renderImport } from '../commonplace/web/static/memory-import.js';
const board = { slug: 'work', name: 'Workspace', wing: 'wing/private', coordinator: 'conn:bot-opaque' };
const me = { actor: 'user:owner', workspace: 'workspace-one', people: { 'conn:bot-opaque': 'Notebook' } };
const text = '  <exact> & café\r\n\tsecond line  ';
const row = { board: 'work', kind: 'note', id: 'drw_opaque', actor: 'bot-opaque', author: 'Notebook (agent)', at: '2026-11-13T13:59:00Z', text, sourceLabel: 'Note: wing/private' };
const state = { look: 'deck', people: me.people, boards: [board], results: [row], filters: { query: '' }, searched: true };
const tick = () => new Promise(resolve => setImmediate(resolve));
function mount(deck = false) {
  const listeners = new Map();
  const node = { innerHTML: '', listeners, addEventListener(type, handler) { listeners.set(type, handler); }, removeEventListener(type) { listeners.delete(type); }, querySelector() { return null; } };
  if (deck) node.ownerDocument = { documentElement: { dataset: { ui: 'deck' } } };
  return node;
}
const click = (node, selector, dataset) => node.listeners.get('click')({ target: { closest: s => s === selector ? { dataset } : null } });

test('Deck maps saved-by provenance to display names and preserves every quote character', () => {
  const html = renderMemory(state);
  assert.ok(html.includes('Saved by <b class="memory-saver">Notebook</b> · Workspace · 13 Nov,'));
  assert.ok(html.includes('Search saved memory'));
  assert.ok(html.includes('  &lt;exact&gt; &amp; café&#13;\n\tsecond line  '));
  for (const internal of ['wing/private', 'drw_opaque', 'bot-opaque', 'Note:']) assert.ok(!html.replace(/<[^>]*>/g, '').includes(internal), internal);
});
test('drawer identifiers appear only in a closed source disclosure', () => {
  const html = renderMemoryPanel({ ...state, result: row, destination: board, look: 'deck' });
  assert.match(html, /<details class="memory-source-details"><summary>Source details<\/summary>.*drw_opaque/s);
  assert.ok(!html.includes('<details open'));
  assert.ok(!html.includes('wing/private'));
});
test('restoration note appears only for a restored saved query', () => {
  for (const extra of [{}, { filters: { query: 'typed' } }, { searched: true }]) assert.ok(!renderMemory({ ...state, ...extra }).includes('Kept from last time'));
  assert.ok(renderMemory({ ...state, restoredQuery: true }).includes('Kept from last time'));
});
test('room chips use the readable board inventory and submit the existing project filter', async () => {
  const node = mount(true), paths = [];
  const ui = createMemory({ mount: node, boards: [board], me, api: async path => { paths.push(path); return { results: [] }; } });
  await ui.search({ query: 'exact', source: 'chat' });
  assert.match(node.innerHTML, /aria-label="Rooms"/);
  assert.match(node.innerHTML, /data-memory-room="" aria-pressed="true">All/);
  assert.match(node.innerHTML, /data-memory-room="work" aria-pressed="false">Workspace/);
  assert.ok(!node.innerHTML.includes('wing/private'));
  click(node, '[data-memory-room]', { memoryRoom: 'work' }); await tick();
  assert.ok(paths.at(-1).includes('/api/chat/work/search?q=exact'));
  assert.match(node.innerHTML, /data-memory-room="work" aria-pressed="true"/);
  ui.stop(); assert.equal(node.listeners.size, 0);
});
test('query restoration is scoped to person and workspace and editing clears the note', async () => {
  const { queryHistory } = await import('../commonplace/web/static/deck-memory.js');
  const values = new Map();
  const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  const old = globalThis.sessionStorage; globalThis.sessionStorage = storage;
  try {
    const history = queryHistory(me, storage); history.save('exact');
    assert.equal(queryHistory({ ...me, actor: 'user:member' }, storage).read(), '');
    assert.equal(queryHistory({ ...me, workspace: 'workspace-two' }, storage).read(), '');
    assert.equal(queryHistory({}, storage).read(), '');
    assert.equal(queryHistory(me, { getItem() { throw Error(); } }).read(), '');
    const node = mount(true), paths = [];
    const ui = createMemory({ mount: node, boards: [board], me, api: async path => { if (path !== '/api/memory/rooms') paths.push(path); return { results: [] }; } });
    await tick();
    assert.ok(node.innerHTML.includes('Kept from last time')); assert.equal(paths.length, 2);
    node.listeners.get('input')({ target: { name: 'query', value: 'edited' } });
    click(node, '[data-memory-room]', { memoryRoom: 'work' }); await tick();
    assert.ok(!node.innerHTML.includes('Kept from last time'));
    assert.equal(history.read(), 'edited');
    ui.stop();
    history.save(''); assert.equal(history.read(), '');
  } finally { globalThis.sessionStorage = old; }
});
test('typing searches once after a pause, and a newer query aborts the older request', async () => {
  const node = mount(true), searches = [], signals = [];
  const ui = createMemory({ mount: node, boards: [board], me, typingPauseMs: 30, api: (path, options) => {
    if (path === '/api/memory/rooms') return Promise.resolve({ rooms: [] });
    if (!path.startsWith('/api/memory/search')) return new Promise(() => {});
    searches.push(new URL(path, 'http://x').searchParams.get('q')); signals.push(options.signal);
    return new Promise(() => {});
  } });
  const handler = node.listeners.get('input');
  const type = value => handler({ target: { name: 'query', value } });
  for (const value of ['e', 'ex', 'exa', 'exac']) type(value);
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.deepEqual(searches, [], 'nothing is sent while the person is still typing');
  await new Promise(resolve => setTimeout(resolve, 80));
  assert.deepEqual(searches.filter(q => q === 'exac'), ['exac']);
  assert.equal(searches.filter(q => q === 'exac').length, 1, 'one request per pause, not one per keystroke');
  const before = signals.length;
  type('exact'); await new Promise(resolve => setTimeout(resolve, 80));
  assert.equal(signals.length, before + 1); assert.equal(signals[before - 1].aborted, true);
  ui.stop();
  type('later'); await new Promise(resolve => setTimeout(resolve, 80));
  assert.equal(signals.length, before + 1, 'no search after leaving Memory');
});
test('Deck consent blocks confirmation, unchecking blocks it again, retries keep bytes and consent', async () => {
  const node = mount(), calls = [], payload = Buffer.from(' \r\n[{"role":"user","content":" café "}]\r\n');
  const preview = { total: 1, messages: 1, source_bytes: payload.length, scope: 'threads', source: 'cursor', preview: 'preview-token' };
  const ui = createMemoryImport({ mount: node, me: {}, look: 'deck', api: async (path, options) => {
    calls.push([path, options.body]);
    if (path.endsWith('/preview')) return preview;
    throw Error('interrupted');
  } });
  assert.ok(node.innerHTML.includes('Import memory files'));
  assert.ok(node.innerHTML.includes('Each memory is marked with the file it came from.'));
  assert.ok(!node.innerHTML.includes('value="private"'));
  assert.ok(!node.innerHTML.includes('Memory text (.md or .txt)'));
  const file = { name: 'export.json', size: payload.length, arrayBuffer: async () => payload };
  const choose = () => node.listeners.get('submit')({ preventDefault() {}, target: { matches: () => true, elements: { file: { files: [file] }, format: { value: '2' }, scope: { value: 'threads' } } } });
  const confirm = action => click(node, '[data-import-action]', { importAction: action });
  const consent = checked => node.listeners.get('change')({ target: { matches: s => s === '[data-import-consent]', checked } });
  await choose();
  assert.match(node.innerHTML, /data-import-action="confirm" disabled/);
  confirm('confirm'); await tick(); assert.equal(calls.length, 1);
  consent(true); consent(false); confirm('confirm'); await tick(); assert.equal(calls.length, 1);
  consent(true); confirm('confirm'); await tick(); assert.equal(calls.length, 2);
  assert.equal(calls[1][1].consent, true); assert.equal(calls[1][1].transcript, payload.toString());
  confirm('retry'); await tick(); assert.deepEqual(calls[2], calls[1]);
  confirm('choose'); await choose(); confirm('confirm'); await tick(); assert.equal(calls.length, 4);
  ui.stop(); assert.equal(node.listeners.size, 0);
  assert.match(renderImport({ phase: 'preview', preview }, {}, 'deck'), /type="checkbox" data-import-consent/);
});

test('quiet search, counted display-name chips and viewer attribution use returned data', async () => {
  const { savedBy, roomName, rememberRoomCounts, roomChips } = await import('../commonplace/web/static/deck-memory.js');
  assert.equal(savedBy({ actor: me.actor, author: 'owner' }, { [me.actor]: 'owner' }, me.actor), 'you');
  assert.equal(savedBy({ actor: 'conn:opaque', author: 'owner' }, {}), 'Unknown author');
  assert.equal(savedBy({ actor: 'user:writer' }, { 'user:writer': 'Memory writer' }, me.actor), 'Memory writer');
  assert.equal(roomName({ slug: 'private-work', name: 'Private notes' }), 'Private notes');
  assert.equal(roomName({ slug: 'private-work', name: 'private-work' }), 'Private work');
  const html = renderMemory(state);
  assert.match(html, /class="memory-search-icon" width="16" height="16"/);
  assert.ok(!html.includes('type="submit"'));
  assert.match(html, /class="memory-filterbar">.*aria-label="Rooms".*<summary>More filters<\/summary>.*class="memory-status"/s);
  assert.match(html, /data-memory-room=""[^>]*>All <span class="memory-room-count">1<\/span>/);
  const counts = { boards: [board, { slug: 'private-work', name: 'Private notes' }], filters: { query: 'exact' } };
  rememberRoomCounts(counts, [row, { ...row, board: 'private-work' }, { ...row, id: 'second' }], counts.filters);
  counts.filters.project = 'work';
  rememberRoomCounts(counts, [row, { ...row, id: 'second' }], counts.filters);
  assert.match(roomChips(counts), />All <span class="memory-room-count">3<\/span>/);
  assert.match(roomChips(counts), />Private notes <span class="memory-room-count">1<\/span>/);
  counts.filters.query = 'different';
  assert.ok(!roomChips(counts).includes('memory-room-count'), 'old query counts must not carry over');
});
test('import presentation keeps the picked format and puts the primary action last', async () => {
  const preview = { source: 'cursor', total: 1, messages: 1, scope: 'threads', source_bytes: 50 };
  const choose = renderImport({}, {}, 'deck');
  for (const copy of ['Where it comes from', '>File</label>', 'Save into', "Everyone who can read this workspace's conversations will see these.", 'Each memory is marked with the file it came from.']) assert.ok(choose.includes(copy), copy);
  assert.ok(choose.indexOf('>Cancel</button>') < choose.indexOf('>Preview file</button>'));
  const review = renderImport({ phase: 'preview', preview, source: 'chatgpt', format: 'auto', sourceLabel: 'ChatGPT conversation (.json)' }, {}, 'deck');
  assert.match(review, /<dt>Source<\/dt><dd>ChatGPT conversation \(\.json\)<\/dd>/);
  assert.ok(review.indexOf('>Choose another file</button>') < review.indexOf('>Cancel</button>'));
  assert.ok(review.indexOf('>Cancel</button>') < review.indexOf('>Import</button>'));
});
test('release nits keep More filters inside the chip scroll and the count outside it', () => {
  const html = renderMemory(state);
  assert.match(html, /<div class="memory-chip-scroll"><nav class="memory-rooms".*<\/nav><details class="memory-filters".*<summary>More filters<\/summary>.*<\/details><\/div><span class="memory-status"/s);
});
test('release nits shorten Deck retry copy while retaining Classic presentation', () => {
  const preview = { source: 'cursor', total: 1, messages: 1, scope: 'threads', source_bytes: 50 };
  const state = { phase: 'preview', preview };
  assert.ok(renderImport(state, {}, 'deck').includes('Importing the same file here again adds nothing.'));
  assert.ok(!renderImport(state, {}, 'deck').includes('same destination'));
  assert.ok(renderImport(state, {}, 'classic').includes('Importing the same file to the same destination again adds nothing.'));
});
