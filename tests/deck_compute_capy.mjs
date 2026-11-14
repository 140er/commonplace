import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { inspectorModel, inspectorHTML, computePanel, addMachineHTML, watchComputeMotion } from '../commonplace/web/static/deck-compute-capy.js';
import { mapModel, createMapCore } from '../commonplace/web/static/topology-map.js';

test('inspector uses recorded fields, honest capacity and exact label copy', () => {
  const record = { values: { role: 'Production · Provider', resources: { memory_gb: 32, memory_used_gb: 10.2, cpu_percent: 23, storage_gb: 240, storage_used_gb: 118 }, service: 'running', health: 'OK', oom_kill: 0, version: 'abc123', bots: ['Maintenance'] } };
  const model = inspectorModel({ record, node: { name: 'Machine', status: 'up' } });
  assert.deepEqual(model.metrics.map(m => m.text), ['10.2 of 32 GB', '23%', '118 of 240 GB']);
  assert.ok(Math.abs(model.metrics[0].percent - 31.875) < 1e-9);
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
  assert.match(html, /Add a machine/); assert.match(html, /href="#\/admin"/); assert.match(html, /Then choose which bots may use it/);
  assert.doesNotMatch(html, /Add runner|workspace administration/);
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
  // The approved failure rose is the only slice-local colour; all other hues remain Deck tokens.
  assert.doesNotMatch(css.replace('#f0708a',''), /#[0-9a-f]{3,8}\b|green/i);
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

test('phone inspector has a visible Back control and preserves the existing detail tab controller', () => {
  const html = computePanel({ key: 'machine', node: { name: 'Machine', kind: 'server', status: 'up' }, tab: 'physical', detail: { fields: {}, sites: [] }, links: [], names: new Map(), compact: true }, {}, true);
  assert.match(html, /data-td="close" aria-label="Back to machines">Back<\/button>/);
  assert.match(html, /Device details/);
  assert.match(html, /data-td-tab="physical"/);
  assert.match(html, /Healthy/);
});

test('compact presentation uses straight links, local curves for blockers, no end marks or loose counts', async () => {
  const { computePresentation, directLinks, cardSize, computeFrame, linkName } = await import('../commonplace/web/static/deck-compute-scene.js');
  const nodes = ['a','b','blocker'].map(key => ({ key, name: key, kind: 'server', role: 'Builds', status: 'up' }));
  const link = { id:'ab', from:'a',to:'b',method:'tailscale',status:{from:'up',to:'up'} };
  const positions = {a:{x:0,y:0},b:{x:400,y:0},blocker:{x:200,y:160}};
  let places = directLinks({nodes,links:[link]},positions);
  assert.equal(places.get('ab').d,'M0 0L400 0');
  positions.blocker.y=0;
  places=directLinks({nodes,links:[link]},positions);
  assert.ok(places.get('ab').control);
  assert.ok(Math.abs(places.get('ab').control.y / 2) > 30, 'curve clears the intervening 48px card');
  assert.match(places.get('ab').d,/Q/);
  const core=createMapCore({route:true,presentation:computePresentation()});
  core.apply({nodes:nodes.map(n=>({key:n.key,values:{kind:n.kind,name:n.name}})),links:[link]});
  const scene=core.scene();
  assert.doesNotMatch(scene,/data-end=|topo-bundle/);
  assert.match(scene,/dc-node-tile/);
  assert.match(scene,/dc-link-caption/);
  const unclaimed = computePresentation().link({...link,status:{from:'none',to:'none'}},places.get('ab'),new Map([['a','Hub'],['b','Machine']]),{words:{},sel:false});
  assert.doesNotMatch(unclaimed,/undefined|Healthy/);
  assert.equal(linkName('tailscale'),'Tailscale'); assert.equal(linkName('direct'),'Direct');
  const phone = computePresentation(true).node({...nodes[0],name:'Build machine'},positions.a,{words:{up:'Healthy'},sel:false});
  assert.doesNotMatch(phone,/dc-node-tile|tspan/);
  assert.match(phone,/>Build machine<\/text>/);
  assert.equal(cardSize(nodes[0]).height,48);
  const fit=computePresentation().fit({nodes},positions,900,600);
  assert.ok(fit.height>=360); assert.equal(fit.k,1);
  const frame=computeFrame();
  assert.doesNotMatch(frame,/topo-legend|zoom-in|zoom-out|<label>Route/);
  assert.equal((frame.match(/<button/g)||[]).length,2);
});


test('compact node states have distinct geometry and accessible status words', async () => {
  const { computePresentation } = await import('../commonplace/web/static/deck-compute-scene.js');
  const words = { up: 'Healthy', degraded: 'Degraded', down: 'Unreachable', unknown: 'Not reported' };
  for (const phone of [false, true]) {
    const draw = node => computePresentation(phone).node({ key: 'machine', name: 'Machine', kind: 'server', ...node }, { x: 0, y: 0 }, { words });
    const marks = Object.entries({ up: 'disc', degraded: 'triangle', down: 'diamond', unknown: 'ring' }).map(([status, shape]) => {
      const html = draw({ status });
      assert.match(html, new RegExp(`aria-label="Machine\\. ${words[status]}"`));
      assert.match(html, new RegExp(`<title>Machine\\. ${words[status]}</title>`));
      const mark = /<g class="dc-node-status"[^>]*>(.*?)<\/g>/.exec(html)?.[0];
      assert.ok(mark, `mark for ${status}`);
      assert.match(mark, new RegExp(`data-shape="${shape}"`));
      return mark.replace(/data-shape="[^"]*"/, '');
    });
    assert.equal(new Set(marks).size, 4, 'actual SVG geometry differs, even without colour');
    for (const flag of ['stale', 'missing']) {
      const html = draw({ status: 'up', [flag]: true });
      assert.match(html, /data-shape="ring"[^>]*><circle[^>]*fill="none"/);
      assert.match(html, flag === 'stale' ? /Machine\. Healthy\. Stale/ : /Machine\. Healthy\. Not recorded/);
    }
    assert.doesNotMatch(draw({ status: 'none' }), /dc-node-status/);
  }
});

test('panel decoration falls back intact if any upstream marker changes', async () => {
  const { decorateComputePanel } = await import('../commonplace/web/static/deck-compute-capy.js');
  const html = '<header>Machine</header><div>Tabs and body</div><p class="td-foot">Read-only. Changes go through agent proposals.</p>';
  for (const marker of ['</header>', '<p class="td-foot">', 'Read-only. Changes go through agent proposals.']) {
    const changed = html.replace(marker, 'changed');
    assert.equal(decorateComputePanel(changed, '<div>Facts</div>', true), changed);
  }
  const decorated = decorateComputePanel(html, '<div>Facts</div>');
  assert.match(decorated, /<div>Facts<\/div><details[^>]*><summary>Device details<\/summary><div>Tabs and body<\/div><\/details><p/);
  assert.match(decorated, /Bots propose changes here. You approve them in Today./);
  const reordered = '<p class="td-foot">Read-only. Changes go through agent proposals.</p><header>Machine</header>';
  assert.equal(decorateComputePanel(reordered, ''), reordered);
});


test('follow-up status sizes, selection exclusivity and endpoint rings', async () => {
  const { computePresentation } = await import('../commonplace/web/static/deck-compute-scene.js');
  for (const phone of [false, true]) {
    const presentation = computePresentation(phone);
    const draw = status => presentation.node({ key:'a',name:'Hub',role:'Production',kind:'server',status },{x:0,y:0},{words:{up:'Up'}});
    assert.match(draw('up'), new RegExp(`r="${phone ? 5 : 4}"`));
    assert.match(draw('up'), /translate\([^ ]+ 0\)/);
    assert.match(draw('degraded'), /data-shape="triangle"/);
    const core = createMapCore({route:true,presentation});
    core.apply({nodes:['a','b'].map(key=>({key,values:{name:key}})),links:[{id:'ab',from:'a',to:'b',method:'tailscale'}]});
    core.choose('a'); core.selectLink('ab');
    assert.equal(core.selected,null,'link selection clears the node');
    assert.equal(core.selectedLink,'ab');
    assert.equal((core.scene().match(/dc-link-end/g)||[]).length,2);
    core.select('b'); assert.equal(core.selectedLink,null,'node selection clears the link');
    assert.doesNotMatch(core.scene(),/dc-link-end/);
  }
});

test('phone curves use separate lanes with captions at their own midpoint and fit above bottom navigation', async () => {
  const { computePresentation, directLinks } = await import('../commonplace/web/static/deck-compute-scene.js');
  const nodes = ['hub','runner','bench','build','laptop'].map(key=>({key,name:key,kind:'server'}));
  const positions = Object.fromEntries(nodes.map((n,i)=>[n.key,{x:0,y:i*112}]));
  const links = [{id:'a',from:'runner',to:'build',method:'direct'}, {id:'b',from:'runner',to:'laptop',method:'tailscale'}];
  const places = directLinks({nodes,links},positions,true);
  const a=places.get('a'),b=places.get('b');
  assert.ok(Math.abs(a.lane-b.lane)>=16);
  for(const link of links) {
    const place=places.get(link.id);
    assert.equal(place.label.x,place.lane);
    assert.equal(place.label.y,(positions[link.from].y+positions[link.to].y)/2);
  }
  const pair = directLinks({nodes,links:[{id:'forward',from:'hub',to:'runner'},{id:'back',from:'runner',to:'hub'}]},positions,true,.5);
  assert.ok(Math.abs(pair.get('forward').lane-pair.get('back').lane)*.5 >= 16);
  const straight = directLinks({nodes,links:[{id:'single',from:'hub',to:'runner'}]},positions,true).get('single');
  assert.deepEqual(straight.label,{x:0,y:56});
  const fit=computePresentation(true).fit({nodes,links},positions,358,400);
  assert.ok(fit.height<=400);
  for(const p of Object.values(positions)) {
    assert.ok(p.y*fit.k+fit.y-24>=24-1e-6);
    assert.ok(p.y*fit.k+fit.y+24<=fit.height-24+1e-6);
  }
});

test('health, summary and selected-link facts use the same shapes and honest observations', async () => {
  const mod=await import('../commonplace/web/static/deck-compute-capy.js');
  assert.match(inspectorHTML(inspectorModel({node:{status:'degraded'}})),/dc-state[^>]*>[\s\S]*data-shape="triangle"[\s\S]*Degraded/);
  assert.match(mod.summaryStatesHTML([{name:'Laptop',status:'unknown'}]),/data-shape="ring"[\s\S]*Laptop unknown/);
  const options={link:{from:'runner',to:'hub',method:'tailscale',status:{from:'up',to:'degraded'}},names:new Map([['runner','Runner'],['hub','Hub']])};
  const html=mod.computeLinkPanel(options);
  assert.match(html,/Runner to Hub/); assert.match(html,/>Route<\/dt><dd>Tailscale/);
  assert.match(html,/>State<\/dt><dd>[\s\S]*Degraded/);
  assert.match(html,/>Last seen<\/dt><dd>Not reported/);
  assert.doesNotMatch(html,/undefined|Healthy/);
  const recorded=mod.computeLinkPanel({...options,link:{...options.link,observedAt:'2026-01-01T12:00:00Z',stale:true},names:new Map([['runner','<script>'],['hub','Hub']])});
  assert.match(recorded,/<time datetime="2026-01-01T12:00:00Z">/);
  assert.match(recorded,/data-shape="ring"/); assert.doesNotMatch(recorded,/<script>/);
});
