import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';
const source = await readFile(new URL('../commonplace/web/static/memory.js', import.meta.url), 'utf8');
const listeners = new Map();
const mount = { innerHTML: '', addEventListener(t, f) { listeners.set(t, f); }, removeEventListener() {}, querySelector() { return this; }, querySelectorAll() { return []; } };
const panel = { ...mount };
let mounted = 0, disposed = 0;
const paths = [];
const graph = { nodes: [], links: [], themes: [], weeks: [] };
const context = { mount, panel, mounted: 0, e: String, actor: String, cardId() {}, isBlocked() {}, URLSearchParams, AbortController, request() {}, globalThis: {},
  mountMap(root, data, open) { assert.equal(root, mount); assert.equal(data, graph); assert.equal(typeof open, 'function'); mounted++; return () => disposed++; },
  api: async path => { paths.push(path); return graph; }, boards: [{ slug: 'atlas', wing: 'project_atlas' }],
};
runInNewContext(source.replace(/^import .*;\n/gm, '').replaceAll('export ', '') + '\nglobalThis.memory = {createMemory, renderMemory};', context);
const { createMemory, renderMemory } = context.globalThis.memory;
if (process.argv[2] === 'entry') {
  assert.match(renderMemory({}), /data-memory-action="map"/);
  const app = await readFile(new URL('../commonplace/web/static/app.js', import.meta.url), 'utf8');
  assert.match(app.slice(app.indexOf('function spaceView()'), app.indexOf('function boardsView()')), /title\(board\?\.name[\s\S]*?href="#\/memory\?project=/, 'Space header must reach its Memory view');
} else {
  const ui = createMemory({ mount, panel, api: context.api, boards: context.boards });
  assert.match(mount.innerHTML, /data-memory-action="map"/, 'Map mode must be reachable');
  assert.equal(typeof ui.showMap, 'function', 'Map mode must mount through its controller');
  await ui.showMap('atlas');
  assert.equal(mounted, 1);
  assert.deepEqual(paths, ['/api/memory/map?theme=project_atlas']);
  assert.match(mount.innerHTML, /Private memories, including your own/);
  ui.stop(); assert.equal(disposed, 1);
  const owner = createMemory({ mount, panel, api: context.api, boards: context.boards, me: { can_admin: true } });
  await owner.showMap();
  assert.doesNotMatch(mount.innerHTML, /Private memories, including your own/);
  owner.stop();
}
