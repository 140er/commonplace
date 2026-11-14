import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { inspectorModel, inspectorHTML, computePanel, addMachineHTML, watchComputeMotion } from '../commonplace/web/static/deck-compute-capy.js';
import { mapModel, createMapCore } from '../commonplace/web/static/topology-map.js';

test('inspector uses recorded fields, honest capacity and exact label copy', () => {
  const record = { values: { role: 'Production · Provider', resources: { memory_gb: 32, memory_used_gb: 10.2, cpu_percent: 23, storage_gb: 240, storage_used_gb: 118 }, service: 'running', health: 'OK', oom_kill: 0, version: 'abc123', bots: ['Maintenance'] } };
  const model = inspectorModel({ record, node: { name: 'Machine', status: 'up' } });
  assert.deepEqual(model.metrics.map(m => m.text), ['10.2 of 32 GB', '23%', '118 of 240 GB']);
  assert.equal(model.metrics[0].percent, 31.874999999999996);
  assert.deepEqual(model.fields, [
    { label: 'Service', value: 'Running' }, { label: 'Health', value: 'Healthy' },
    { label: 'Memory kills', value: '0' }, { label: 'Version', value: 'abc123' }, { label: 'Bots', value: 'Maintenance' },
  ]);
  assert.doesNotMatch(inspectorHTML(model), /oom_kill|GiB|>OK</);
});

test('capacity alone never becomes used memory or an invented meter', () => {
  const model = inspectorModel({ detail: { fields: { resources: { value: { memory_gb: 32, storage_gb: 0 } } } } });
  assert.deepEqual(model.metrics, [{ label: 'Memory', text: '32 GB capacity', percent: null }, { label: 'Disk', text: '0 GB capacity', percent: null }]);
  assert.deepEqual(model.fields, []);
  assert.doesNotMatch(inspectorHTML(model), /<meter|Production|Provider|Healthy|Memory kills/);
  assert.deepEqual(inspectorModel({}).metrics, []);
});

test('current observations override graph values, stale observations cannot imply current usage', () => {
  const model = inspectorModel({ record: { stale: true, values: { service: 'running', resources: { memory_used_gb: 12 } } }, detail: { fields: { resources: { value: { memory_gb: 8 } }, version: { value: 'old', stale: true } } } });
  assert.equal(model.stale, true);
  assert.equal(model.metrics[0].text, '8 GB capacity');
  assert.deepEqual(model.fields, []);
  const html = inspectorHTML(inspectorModel({ record: { values: { service: '<script>bad</script>', version: '<img>', resources: { cpu_percent: NaN, memory_gb: -1 }, oom_kill: null } } }));
  assert.doesNotMatch(html, /<script|<img|NaN|<meter|Memory kills/);
  assert.match(html, /&lt;script&gt;/);
});

test('Add a machine leads to the existing Admin enrollment, with no second setup form', () => {
  const html = addMachineHTML();
  assert.match(html, /Add a machine/); assert.match(html, /href="#\/admin"/); assert.match(html, /Add runner/);
  assert.doesNotMatch(html, /<form|<input|curl|token|POST/);
});

test('reduced motion follows initial preference, live changes and cleans up', () => {
  let listener;
  const root = { dataset: {} }, media = { matches: true, addEventListener(type, fn) { assert.equal(type, 'change'); listener = fn; }, removeEventListener(type, fn) { assert.equal(fn, listener); listener = null; } };
  const stop = watchComputeMotion(root, media);
  assert.equal(root.dataset.computeMotion, 'reduced');
  media.matches = false; listener(); assert.equal(root.dataset.computeMotion, 'full');
  media.matches = true; listener(); assert.equal(root.dataset.computeMotion, 'reduced');
  stop(); assert.equal(listener, null); assert.equal(root.dataset.computeMotion, undefined);
});

test('only live link ends animate; real method survives to the scene; bundles stay interactive', () => {
  const payload = { nodes: ['a','b'].map(key => ({ key, values: { kind: 'server', name: key } })), links: Array.from({ length: 4 }, (_,i) => ({ id: `link-${i}`, from: 'a', to: 'b', method: i ? 'direct' : 'tailscale' })), status: { links: { 'link-0': { from: 'up', to: 'up' }, 'link-1': { from: 'up', to: 'unknown' } } } };
  const core = createMapCore({ route: true }); core.apply(payload);
  const scene = core.scene();
  assert.match(scene, /data-method="tailscale" data-active="true"/);
  assert.match(scene, /data-method="direct" data-active="false"/);
  assert.match(scene, /data-bundle-node=/);
  assert.match(scene, /data-link="link-0" tabindex="0" role="button"/);
  assert.equal(mapModel(payload).links[0].method, 'tailscale');
});

test('motion is one 4/6 dash per 1.4 seconds with idle and reduced-motion fallbacks', async () => {
  const css = await readFile(new URL('../commonplace/web/static/deck-compute-capy.css', import.meta.url), 'utf8');
  assert.match(css, /stroke-dasharray: 4 6/); assert.match(css, /1\.4s linear infinite/);
  assert.match(css, /stroke-dashoffset: -10/); assert.match(css, /prefers-reduced-motion: reduce/);
  assert.match(css, /data-compute-motion="reduced"[\s\S]*?animation: none/);
  assert.doesNotMatch(css, /#[0-9a-f]{3,8}\b|green|data-method="direct"/i);
});

test('phone tree follows real links, fits peer nodes, and keeps isolated nodes and cycles', async () => {
  const { phoneTree } = await import('../commonplace/web/static/deck-compute-capy.js');
  const nodes = ['hub','runner','a','b','c','isolated'].map(key => ({ key }));
  const links = [{ from: 'hub', to: 'runner' }, ...['a','b','c'].map(to => ({ from: 'runner', to })), { from: 'b', to: 'runner' }];
  const positions = phoneTree(nodes, links);
  assert.equal(Object.keys(positions).length, nodes.length);
  assert.ok(positions.hub.y < positions.runner.y);
  assert.ok(positions.runner.y < positions.a.y);
  assert.ok(Object.values(positions).every(p => Math.abs(p.x) <= 82.5));
  assert.deepEqual(positions, phoneTree([...nodes].reverse(), [...links].reverse()));
});
