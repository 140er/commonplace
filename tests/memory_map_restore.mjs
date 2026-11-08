import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';
const source = await readFile(new URL('../commonplace/web/static/memory.js', import.meta.url), 'utf8');
const listeners = new Map();
let notice = { hidden: true };
let canAdmin = false;
const mount = { _html: '', get innerHTML() { return this._html; }, set innerHTML(value) { this._html = value; notice = { hidden: !value.includes('data-map-notice') }; }, addEventListener(t, f) { listeners.set(t, f); }, removeEventListener() {}, querySelector(selector) { return selector === "[data-map-notice]" ? notice : this; }, querySelectorAll() { return []; } };
const panel = { ...mount };
let mounted = 0, disposed = 0;
const paths = [];
const graph = { nodes: [], links: [], themes: [], weeks: [] };
const context = { mount, panel, mounted: 0, e: String, actor: String, cardId() {}, isBlocked() {}, URLSearchParams, AbortController, request() {}, globalThis: {},
  mountMap(root, data, open) { assert.equal(root, mount); assert.equal(data, graph); assert.equal(typeof open, 'function'); mounted++; return () => disposed++; },
  api: async path => { paths.push(path); return path === "/api/me" ? { can_admin: canAdmin } : graph; }, boards: [{ slug: 'atlas', wing: 'project_atlas' }],
};
runInNewContext(source.replace(/^import .*;\n/gm, '').replaceAll('export ', '') + '\nglobalThis.memory = {createMemory, renderMemory};', context);
const { createMemory, renderMemory } = context.globalThis.memory;
if (process.argv[2] === 'entry') {
  assert.match(renderMemory({}), /data-memory-action="map"/);
  const app = await readFile(new URL('../commonplace/web/static/app.js', import.meta.url), 'utf8');
  assert.match(app.slice(app.indexOf('function spaceView()'), app.indexOf('function boardsView()')), /title\(board\?\.name[\s\S]*?href="#\/memory\?project=/, 'Space header must reach its Memory view');
} else if (process.argv[2] === 'me_failure') {
  const ui = createMemory({ mount, panel, boards: context.boards, me: { can_admin: true },
    api: async path => { paths.push(path); if (path === '/api/me') throw new Error('identity unavailable'); return graph; },
  });
  await ui.showMap('atlas');
  assert.equal(mounted, 1, 'Identity failure must not prevent mounting the map');
  assert.deepEqual(paths, ['/api/me', '/api/memory/map?theme=project_atlas']);
  assert.equal(notice.hidden, false, 'Failed identity proof must keep the notice visible');
  assert.doesNotMatch(mount.innerHTML, /Map could not be loaded/);
  ui.stop();
} else if (process.argv[2] === 'me_deadline') {
  const apiSource = await readFile(new URL('../commonplace/web/static/api.js', import.meta.url), 'utf8');
  let deadline, identitySignal, cleared = false;
  const realApi = runInNewContext(apiSource.replaceAll('export ', '') + '\n;api;', {
    AbortController,
    fetch: (path, options) => {
      if (path === '/api/me') { identitySignal = options.signal; return new Promise(() => {}); }
      return Promise.resolve({ ok: true, status: 200, json: async () => graph });
    },
    setTimeout(callback, ms) { assert.equal(ms, 10000); deadline = callback; return 1; },
    clearTimeout() { cleared = true; },
  });
  const ui = createMemory({ mount, panel, boards: context.boards, api: realApi });
  await ui.showMap('atlas');
  assert.equal(mounted, 1);
  assert.equal(notice.hidden, false);
  assert.equal(identitySignal.aborted, false);
  deadline();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(identitySignal.aborted, true, 'The real API deadline must abort pending identity');
  assert.equal(cleared, true, 'The complete-read deadline must settle and clear its timer');
  assert.equal(notice.hidden, false);
  ui.stop();
} else if (['me_pending', 'me_null', 'me_stale', 'me_stopped', 'me_owner_late'].includes(process.argv[2])) {
  const mode = process.argv[2];
  const identities = [], options = [];
  const ui = createMemory({ mount, panel, boards: context.boards,
    api: (path, opts) => {
      paths.push(path);
      if (path !== '/api/me') return Promise.resolve(graph);
      options.push(opts);
      return new Promise(resolve => identities.push(resolve));
    },
  });
  const first = ui.showMap('atlas');
  const oldNotice = notice;
  await new Promise(resolve => setImmediate(resolve));
  if (mode === 'me_pending') {
    assert.equal(mounted, 1, 'Pending identity must not block the map');
    assert.equal(notice.hidden, false);
    assert.equal(options[0]?.timeoutMs, 10000, 'Identity must have a bounded deadline');
  } else if (mode === 'me_owner_late') {
    await first;
    assert.equal(mounted, 1);
    assert.equal(notice.hidden, false);
    identities[0]({ can_admin: true });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(notice.hidden, true, 'Current owner proof must clear the pending notice');
  } else if (mode === 'me_null') {
    identities[0](null);
    await first;
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(mounted, 1);
    assert.equal(notice.hidden, false);
  } else if (mode === 'me_stale') {
    const second = ui.showMap();
    const currentNotice = notice;
    identities[1]({ can_admin: false });
    identities[0]({ can_admin: true });
    await Promise.all([first, second]);
    assert.notEqual(oldNotice, currentNotice);
    assert.equal(oldNotice.hidden, false);
    assert.equal(currentNotice.hidden, false, 'Old owner proof must not clear the current notice');
  } else {
    ui.stop();
    identities[0]({ can_admin: true });
    await first;
    assert.equal(oldNotice.hidden, false, 'Stopped identity proof must not mutate the notice');
    assert.equal(mounted, disposed);
  }
  ui.stop();
} else {
  const ui = createMemory({ mount, panel, api: context.api, boards: context.boards });
  assert.match(mount.innerHTML, /data-memory-action="map"/, 'Map mode must be reachable');
  assert.equal(typeof ui.showMap, 'function', 'Map mode must mount through its controller');
  await ui.showMap('atlas');
  assert.equal(mounted, 1);
  assert.deepEqual(paths, ['/api/me', '/api/memory/map?theme=project_atlas']);
  assert.equal(notice.hidden, false);
  assert.match(mount.innerHTML, /Private memories, including your own/);
  ui.stop(); assert.equal(disposed, 1);
  canAdmin = true;
  const owner = createMemory({ mount, panel, api: context.api, boards: context.boards, me: { can_admin: true } });
  await owner.showMap();
  assert.equal(notice.hidden, true);
  owner.stop();
}
