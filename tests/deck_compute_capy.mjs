import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { inspectorModel, inspectorHTML, computePanel, addMachineHTML, watchComputeMotion, computeLinkPanel } from '../commonplace/web/static/deck-compute-capy.js';
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
  // The page's one Add a machine action is the header button; this card is plain instructions with no second button.
  assert.match(html, /Connecting a machine/); assert.doesNotMatch(html, /Open machine setup|Add a machine|<a |<button|href=/); assert.match(html, /button at the top of this page/); assert.match(html, /Bots run their code on a runner machine set up in Admin/);
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


for (const phone of [false, true]) test(`compact presentation: a device with more than three links shows its link count, ${phone ? 'phone' : 'desktop'}`, async () => {
  const { computePresentation } = await import('../commonplace/web/static/deck-compute-scene.js');
  const graph = count => ({ nodes: ['a','b','c'].map(key => ({ key, values: { kind: 'server', name: key } })), links: Array.from({ length: count }, (_, i) => ({ id: `link-${i}`, from: 'a', to: i % 2 ? 'b' : 'c', method: 'direct' })) });
  const badges = scene => [...scene.matchAll(/<g class="topo-bundle dc-bundle" data-bundle-node="([^"]+)" data-count="(\d+)">.*?<text class="topo-bundle-count"[^>]*>(\d+)<\/text><\/g>/g)].map(m => [m[1], Number(m[2]), Number(m[3])]);
  const draw = (count, options = { route: true }) => { const core = createMapCore({ ...options, presentation: computePresentation(phone) }); core.apply(graph(count)); return core; };
  const four = draw(4);
  assert.deepEqual(badges(four.scene()), [['a', 4, 4]], 'only the device with four link ends is marked, with its count');
  assert.match(four.scene(), /aria-label="a[^"]*\. 4 links"/, 'the count has words for assistive technology');
  assert.deepEqual(badges(draw(6).scene()), [['a', 6, 6]]);
  assert.deepEqual(badges(draw(3).scene()), [], 'three links stay unmarked');
  assert.deepEqual(badges(draw(4, {}).scene()), [], 'a map without link targets draws no count');
  four.selectLink('link-1');
  assert.deepEqual(badges(four.scene()), [['a', 4, 4]], 'every stroke is still drawn, so the count stays whole');
  assert.equal((four.scene().match(/data-link="/g) || []).length, 4);
  assert.doesNotMatch(four.scene(), /data-end=/, 'the Deck look keeps its own link ends');
});


// The card is drawn at screen size and the strokes in map units, so where the strokes meet the card edge moves with the
// zoom. The count's end mark must sit there, with its number beyond it, whichever side the strokes come in on.
const bundleCases = [
  ...[[1, 0], [-1, 0], [0, 1], [0, -1]].flatMap(dir => [4, 6, 8].map(count => ({ phone: false, dir, count }))),
  ...[4, 6, 8].map(count => ({ phone: true, dir: [-1, 0], count, parallel: true })),
];
for (const zoom of [1, .5, .25]) for (const one of bundleCases) test(`compact presentation: the link count's end mark sits where ${one.count} links meet the card, ${one.phone ? 'phone' : 'desktop'} ${one.parallel ? 'ported' : `toward ${one.dir}`}, zoom ${zoom}`, async () => {
  const { computePresentation, cardSize } = await import('../commonplace/web/static/deck-compute-scene.js');
  const device = key => ({ key, values: { kind: 'server', name: key === 'hub' ? 'Build machine' : key, role: 'Runner' } });
  const nodes = [device('hub')], links = [];
  for (let i = 0; i < one.count; i++) { const peer = one.parallel ? 'peer' : `peer-${i}`; if (!nodes.some(n => n.key === peer)) nodes.push(device(peer)); links.push({ id: `link-${i}`, from: 'hub', to: peer, method: 'direct' }); }
  const core = createMapCore({ route: true, presentation: computePresentation(one.phone) });
  core.apply({ nodes, links });
  core.positions.hub = { x: 0, y: 0 };
  nodes.slice(1).forEach(({ key }, i, peers) => { const spread = (i - (peers.length - 1) / 2) * 60; core.positions[key] = one.parallel ? { x: 0, y: 300 } : { x: one.dir[0] * 600 + (one.dir[1] ? spread * 2 : 0), y: one.dir[1] * 400 + (one.dir[0] ? spread : 0) }; });
  core.setView({ x: 0, y: 0, k: zoom }, { remember: false });
  const scene = core.scene(), hub = core.model.nodes[0], { width, height } = cardSize(hub, one.phone), hx = width / 2, hy = height / 2;
  // Measured independently of the scene code: walk every stroke out of the hub on the screen until it leaves the card.
  const places = core.links(), exits = links.map(({ id }) => {
    const points = places.get(id).points.map(p => ({ x: p.x * zoom, y: p.y * zoom }));
    for (let t = 0; t <= 1; t += .0002) {
      const at = t * (points.length - 1), i = Math.min(points.length - 2, Math.floor(at)), u = at - i;
      const p = { x: points[i].x + (points[i + 1].x - points[i].x) * u, y: points[i].y + (points[i + 1].y - points[i].y) * u };
      if (Math.abs(p.x) >= hx - .01 || Math.abs(p.y) >= hy - .01) return { ...p, side: Math.abs(p.x) >= hx - .5 ? (p.x < 0 ? 'left' : 'right') : (p.y < 0 ? 'top' : 'bottom') };
    }
    throw new Error('stroke never leaves the card');
  });
  const tally = side => exits.filter(p => p.side === side).length, most = Math.max(...['left', 'right', 'top', 'bottom'].map(tally));
  const own = new RegExp(`<g class="topo-bundle dc-bundle" data-bundle-node="hub" data-count="${one.count}">.*?</g>`).exec(scene)?.[0] || '';
  const mark = /<circle class="dc-bundle-mark" data-side="(\w+)" cx="(-?[\d.]+)" cy="(-?[\d.]+)" r="([\d.]+)"/.exec(own);
  assert.ok(mark, 'the count has an end mark');
  const [, side, cx, cy, radius] = mark, at = { x: Number(cx), y: Number(cy) }, horizontal = side === 'left' || side === 'right';
  assert.equal(tally(side), most, `the mark is on a side where most strokes meet the card (${exits.map(p => p.side)})`);
  const met = exits.filter(p => p.side === side), mean = met.reduce((sum, p) => sum + p[horizontal ? 'y' : 'x'], 0) / met.length, reach = (horizontal ? hy : hx) - 10;
  assert.ok(Math.abs(at[horizontal ? 'y' : 'x'] - Math.max(-reach, Math.min(reach, mean))) < .1, 'the mark is where those strokes meet the edge');
  assert.ok(Math.abs(horizontal ? Math.abs(at.x) - hx : Math.abs(at.y) - hy) < .01, 'the mark sits on the card edge');
  const chip = /<rect class="dc-bundle-chip" x="(-?[\d.]+)" y="(-?[\d.]+)" width="([\d.]+)" height="([\d.]+)"/.exec(own), box = { x: Number(chip[1]), y: Number(chip[2]), width: Number(chip[3]), height: Number(chip[4]) };
  const sign = side === 'left' || side === 'top' ? -1 : 1, centre = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  assert.ok(sign * (horizontal ? centre.x - at.x : centre.y - at.y) > Number(radius), 'the count is on the outer side of the mark');
  for (const [from, to] of [...places.values()].map(place => place.points.map(p => ({ x: p.x * zoom, y: p.y * zoom }))).flatMap(points => points.slice(1).map((p, i) => [points[i], p]))) {
    for (let t = 0; t <= 1; t += .002) {
      const x = from.x + (to.x - from.x) * t, y = from.y + (to.y - from.y) * t;
      assert.ok(!(x > box.x - 1.5 && x < box.x + box.width + 1.5 && y > box.y - 1.5 && y < box.y + box.height + 1.5), 'no stroke runs under the count');
    }
  }
  assert.ok(box.x >= hx || box.x + box.width <= -hx || box.y >= hy || box.y + box.height <= -hy, 'the count stays off the card, so it cannot meet its name, role or status');
  assert.ok(Number(radius) < 6 && Math.abs(horizontal ? at.x : at.y) + Number(radius) <= (horizontal ? hx : hy) + Number(radius), 'the mark only reaches over the card edge by its radius');
  const text = /<text class="dc-node-name" x="(-?[\d.]+)"/.exec(scene), status = /class="dc-node-status"[^>]*transform="translate\((-?[\d.]+) 0\)"/.exec(scene);
  if (side === 'left') assert.ok(Number(text[1]) - Number(radius) > -hx + Number(radius), 'the mark leaves the name its padding');
  if (side === 'right' && status) assert.ok(Number(status[1]) + 8 < hx - Number(radius), 'the mark leaves the status mark its room');
  assert.equal((own.match(/class="dc-bundle-mark"/g) || []).length, 1, 'one end mark per device');
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

test('the hub panel names its kind once: "Hub", not "Hub · Hub"', async () => {
  const { decorateComputePanel } = await import('../commonplace/web/static/deck-compute-capy.js');
  const panel = sub => `<header class="td-head"><div><h2 class="td-title">Commonplace</h2><p class="td-sub">${sub}</p></div></header><div>Body</div><p class="td-foot">Read-only. Changes go through agent proposals.</p>`;
  assert.match(decorateComputePanel(panel('Hub · Hub'), '<div>Facts</div>'), /<p class="td-sub">Hub<\/p>/);
  assert.match(decorateComputePanel(panel('Server · Ready · stale'), '<div>Facts</div>'), /<p class="td-sub">Server · Ready · stale<\/p>/);
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
  assert.match(decorated, /Bots propose changes here. You approve them in Waiting on you./);
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
    assert.equal(place.label.y,(place.points[0].y+place.points.at(-1).y)/2);
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
  assert.match(html,/>Last seen<\/dt><dd>Unknown/);
  assert.doesNotMatch(html,/undefined|Healthy/);
  const recorded=mod.computeLinkPanel({...options,link:{...options.link,observedAt:'2026-01-01T12:00:00Z',stale:true},names:new Map([['runner','<script>'],['hub','Hub']])});
  assert.match(recorded,/<time datetime="2026-01-01T12:00:00Z">/);
  assert.match(recorded,/data-shape="ring"/); assert.doesNotMatch(recorded,/<script>/);
});


test('phone curves attach twelve screen pixels apart on the node left edge', async () => {
  const {directLinks,cardSize} = await import('../commonplace/web/static/deck-compute-scene.js');
  const nodes=['runner','middle','a','b'].map(key=>({key,name:key}));
  const positions=Object.fromEntries(nodes.map((n,i)=>[n.key,{x:0,y:i*112}]));
  const links=[{id:'a',from:'runner',to:'a'},{id:'b',from:'runner',to:'b'}];
  for(const k of [1,.5]) {
    const places=directLinks({nodes,links},positions,true,k);
    const starts=links.map(link=>places.get(link.id).points[0]);
    assert.equal(Math.abs(starts[0].y-starts[1].y)*k,12);
    assert.equal(starts[0].x*k,-cardSize(nodes[0],true).width/2);
  }
});

test('summary separators wrap with their item and stay outside the text', async () => {
  const {summaryStatesHTML} = await import('../commonplace/web/static/deck-compute-capy.js');
  const html=summaryStatesHTML([{name:'Laptop',status:'unknown'}]);
  assert.doesNotMatch(html,/·/,'separators are clipped outside a wrapped item, never orphaned text');
  const css=await readFile(new URL('../commonplace/web/static/deck-compute-capy.css', import.meta.url),'utf8');
  assert.match(css,/dc-summary[^}]*flex-wrap:\s*wrap/);
  assert.match(css,/dc-summary-state::before[^}]*content:\s*['"]\\00b7['"]/);
});

test('summary sorts effective status worst first without reordering the input', async () => {
  const {summaryStatesHTML} = await import('../commonplace/web/static/deck-compute-capy.js');
  const nodes=[{name:'Unknown',status:'unknown'},{name:'Stale',status:'down',stale:true},{name:'Busy',status:'degraded'},{name:'Down',status:'down'}];
  const html=summaryStatesHTML(nodes);
  assert.ok(html.indexOf('Down down')<html.indexOf('Busy degraded'));
  assert.ok(html.indexOf('Busy degraded')<html.indexOf('Unknown unknown'));
  assert.ok(html.indexOf('Busy degraded')<html.indexOf('Stale unknown'));
  assert.equal(nodes[0].name,'Unknown');
});

test('link detail forwards both compact modes to the shared node close control', () => {
  const close=html=>/<button[^>]*data-td="close"[^>]*>[\s\S]*?<\/button>/.exec(html)[0];
  for (const compact of [false,true]) {
    const options={key:'runner',node:{name:'Runner',status:'up'},tab:'physical',detail:{fields:{},sites:[]},links:[],names:new Map(),compact};
    const node=computePanel(options,{});
    const link=computeLinkPanel({link:{from:'a',to:'b',method:'direct',status:{from:'up',to:'up'}},names:new Map(),compact});
    assert.match(link,/>Last seen<\/dt><dd>Unknown/);
    assert.equal(close(link),close(node),`same close control with compact=${compact}`);
  }
});


test('phone node and link inspectors share their separate Back treatment', () => {
  const options={key:'runner',node:{name:'Runner',status:'up'},tab:'physical',detail:{fields:{},sites:[]},links:[],names:new Map(),compact:true};
  const node=computePanel(options,{},true);
  const link=computeLinkPanel({link:{from:'a',to:'b',status:{from:'up',to:'up'}},names:new Map(),compact:true,phone:true});
  const close=html=>/<button[^>]*data-td="close"[^>]*>[\s\S]*?<\/button>/.exec(html)[0];
  assert.equal(close(link),close(node));
  assert.match(close(link),/>Back<\/button>/);
});
