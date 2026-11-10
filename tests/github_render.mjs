import assert from 'node:assert/strict';
import { renderNeedDetail } from '../commonplace/web/static/needs.js';
const card = {number: 7, version: 1, board: 'work', title: 'Review', need: 'merge', actions: [], pr_url: 'https://github.com/example/project/pull/1', pr_state: 'open'};
const absent = renderNeedDetail(card, {github: {state: 'absent'}});
assert.ok(absent.includes('Connect GitHub'), 'an unconnected person must connect their own account');
const connected = renderNeedDetail(card, {github: {state: 'connected', login: 'test-person'}});
assert.ok(connected.includes('Open PR') && connected.includes('Disconnect'));
assert.ok(connected.includes('noopener noreferrer'));
assert.ok(connected.includes('unverified'));
for (const pr_url of ['http://github.com/a/b/pull/1', 'https://evil.example/a/b/pull/1', 'https://github.com@evil.example/a/b/pull/1', 'https://github.com/a/b/pull/0', 'https://github.com:444/a/b/pull/1']) {
  assert.ok(!renderNeedDetail({...card, pr_url}, {github: {state: 'connected'}}).includes('Open PR'));
}
assert.ok(renderNeedDetail(card, {github: {state: 'loading'}}).includes('disabled'));
assert.ok(renderNeedDetail(card, {github: {state: 'unconfigured'}}).includes('setup'));

// Exercise the real Needs controller with synthetic DOM roots and deferred API reads.
const { createNeeds } = await import('../commonplace/web/static/needs.js');
function root() {
  return { innerHTML: '', contains: () => false, addEventListener() {}, removeEventListener() {}, querySelector: () => null, querySelectorAll: () => [] };
}
const mount = root(), detailMount = root();
const doc = { hidden: false, addEventListener() {}, removeEventListener() {} };
let release, reads = 0;
const ui = createNeeds({ mount, detailMount, document: doc, setInterval: () => 1, clearInterval() {}, api: async (path) => {
  if (path.startsWith('/api/cards/')) return {...card, number: Number(path.split('/').at(-1))};
  if (path === '/api/github') {
    reads++;
    if (reads === 1) return new Promise(resolve => { release = resolve; });
    return {state: 'absent', csrf: 'synthetic-csrf'};
  }
  return {holds: []};
}});
const first = ui.select(7);
await new Promise(resolve => setImmediate(resolve));
assert.ok(detailMount.innerHTML.includes('Loading GitHub'));
await ui.select(8);
assert.ok(detailMount.innerHTML.includes('Connect GitHub'));
release({state: 'connected', login: 'stale-person'});
await first;
assert.ok(!detailMount.innerHTML.includes('Open PR'), 'an old workspace/card response must not restore a connection');
ui.stop();
const before = detailMount.innerHTML;
await ui.select(7);
assert.equal(detailMount.innerHTML, before, 'stopped controllers must ignore late session work');

const elsewhere = renderNeedDetail(card, {github: {state:'other_workspace'}});
assert.ok(elsewhere.includes('another workspace') && elsewhere.includes('Disconnect'));
assert.ok(!elsewhere.includes('Open PR') && !elsewhere.includes('Connect GitHub'));
const errorMount=root(), errorDetail=root();
const failedStatus=createNeeds({mount:errorMount,detailMount:errorDetail,document:doc,setInterval:()=>1,clearInterval(){},api:async path=>{
 if(path.startsWith('/api/cards/')) return {...card,proposal_status:'pending',can_approve:true};
 if(path==='/api/github') throw new Error('synthetic status failure');
 return {holds:[]};
}});
await failedStatus.show({needs:{approvals:[{...card,proposal_status:'pending',actions:['approve','reject']}]},selected:card.number});
assert.ok(errorDetail.innerHTML.includes('GitHub connection unavailable'));
assert.ok(errorDetail.innerHTML.includes('data-needs-approve') && errorDetail.innerHTML.includes('Review'));
assert.ok(!errorDetail.innerHTML.includes('Could not load this card'));
failedStatus.stop();

// Merging through the person's own connection: a separate opt-in, public repositories only.
const ready = {...card, pr_head: 'a'.repeat(40)};
const noConsent = renderNeedDetail(ready, {github: {state: 'connected', merge: false, login: 'test-person'}});
assert.ok(noConsent.includes('Open PR') && noConsent.includes('data-needs-github="allow-merge"'), 'connected without consent offers Allow merging beside Open PR');
assert.ok(!noConsent.includes('data-needs-github="merge"') && !noConsent.includes('arm-merge'), 'no Merge without the separate consent');
const consented = renderNeedDetail(ready, {github: {state: 'connected', merge: true, login: 'test-person'}});
assert.ok(consented.includes('data-needs-github="arm-merge"') && consented.includes('Open PR'));
assert.ok(!consented.includes('data-needs-github="merge"'), 'merging asks for a second confirmation first');
const armed = renderNeedDetail(ready, {github: {state: 'connected', merge: true, armed: true}});
assert.ok(armed.includes('data-needs-github="merge"') && armed.includes('aaaaaaa') && armed.includes('example/project#1') && armed.includes('squash') && armed.includes('merge commit') && armed.includes('rebase'), 'Confirm names the head and how GitHub merges');
for (const pr_url of ['https://GitHub.com/example/project/pull/1', 'https://github.com:443/example/project/pull/1']) {
  const odd = renderNeedDetail({...ready, pr_url}, {github: {state: 'connected', merge: true}});
  assert.ok(odd.includes('Open PR') && !odd.includes('arm-merge') && !odd.includes('allow-merge'), 'a URL form the server would refuse offers no merge controls: ' + pr_url);
}
const done = renderNeedDetail(ready, {github: {state: 'connected', merge: true, merged: 'a'.repeat(40)}});
assert.ok(done.includes('Merged') && !done.includes('arm-merge') && !done.includes('data-needs-github="merge"') && !done.includes('allow-merge'), 'no merge controls after a successful merge');
assert.ok(!renderNeedDetail({...ready, pr_head: undefined}, {github: {state: 'connected', merge: true}}).includes('arm-merge'));
const unsafe = renderNeedDetail({...ready, pr_url: 'https://evil.example/a/b/pull/1'}, {github: {state: 'connected', merge: true}});
assert.ok(!unsafe.includes('arm-merge') && !unsafe.includes('allow-merge'), 'no merge controls without a safe github.com PR URL');
const refused = renderNeedDetail(ready, {github: {state: 'connected', merge: true, notice: 'This repository is private, so Commonplace will not merge it. Use Open PR to merge it on GitHub.'}});
assert.ok(refused.includes('role="alert"') && refused.includes('private') && refused.includes('Open PR'));
const asking = renderNeedDetail(ready, {github: {state: 'merge_confirm', login: 'test-person', scopes: ['public_repo'], connect_enabled: true}});
assert.ok(asking.includes('public_repo') && asking.includes('every public repository your GitHub account can write to') && asking.includes('organisation') && asking.includes('data-needs-github="confirm"') && asking.includes('Allow merging'));
assert.ok(!renderNeedDetail({...ready, need: 'checks'}, {github: {state: 'connected', merge: true}}).includes('arm-merge'));

// The controller sends the card number only; the server resolves the PR and refuses by code.
{
  const calls = [];
  const mount2 = root(), detail2 = root();
  let clickHandler;
  detail2.querySelector = () => null;
  const click = [];
  const doc2 = { hidden: false, addEventListener() {}, removeEventListener() {} };
  const api2 = async (path, options = {}) => {
    calls.push([path, options.method, options.body]);
    if (path.startsWith('/api/cards/')) return {...ready, number: Number(path.split('/').at(-1))};
    if (path === '/api/github' && !options.method) return {state: 'connected', merge: true, csrf: 'synthetic-csrf', login: 'test-person'};
    if (path === '/api/github/merge') { const err = new Error('github_private_repo'); err.status = 409; err.payload = {error: 'github_private_repo'}; throw err; }
    return {holds: []};
  };
  mount2.addEventListener = (name, fn) => { if (name === 'click') clickHandler = fn; };
  detail2.addEventListener = (name, fn) => { if (name === 'click') clickHandler = fn; };
  const ui2 = createNeeds({mount: mount2, detailMount: detail2, document: doc2, setInterval: () => 1, clearInterval() {}, api: api2});
  await ui2.select(7);
  const press = async (name) => clickHandler({target: {closest: () => ({disabled: false, hasAttribute: (a) => a === 'data-needs-github', dataset: {needsGithub: name}})}, preventDefault() {}, stopPropagation() {}});
  await press('arm-merge');
  assert.ok(detail2.innerHTML.includes('Confirm merge') && !calls.some(([path]) => path === '/api/github/merge'), 'arming never calls the server');
  await press('merge');
  const sent = calls.find(([path]) => path === '/api/github/merge');
  assert.deepEqual(Object.keys(sent[2]).sort(), ['accept', 'acknowledge', 'card', 'csrf', 'head'].sort());
  assert.equal(sent[2].head, 'a'.repeat(40), 'the confirmed head travels with the request');
  assert.equal(sent[2].card, 7);
  assert.ok(detail2.innerHTML.includes('This repository is private') && detail2.innerHTML.includes('Open PR'), 'a private repository leaves Open PR with a plain reason');
  assert.ok(!detail2.innerHTML.includes('Confirm merge'));
  ui2.stop();
}

// A merged PR stays merged across a Needs re-select (the panel re-reads status each time).
{
  const detail3 = root();
  let clickHandler;
  detail3.addEventListener = (name, fn) => { if (name === 'click') clickHandler = fn; };
  const api3 = async (path, options = {}) => {
    if (path.startsWith('/api/cards/')) return {...ready, number: Number(path.split('/').at(-1))};
    if (path === '/api/github' && !options.method) return {state: 'connected', merge: true, csrf: 'synthetic-csrf', login: 'test-person'};
    if (path === '/api/github/merge') return {merged: true, sha: ready.pr_head};
    return {holds: []};
  };
  const ui3 = createNeeds({mount: root(), detailMount: detail3, document: doc, setInterval: () => 1, clearInterval() {}, api: api3});
  await ui3.select(7);
  const press = async (name) => clickHandler({target: {closest: () => ({disabled: false, hasAttribute: (a) => a === 'data-needs-github', dataset: {needsGithub: name}})}, preventDefault() {}, stopPropagation() {}});
  await press('arm-merge');
  await press('merge');
  assert.ok(detail3.innerHTML.includes('Merged') && !detail3.innerHTML.includes('arm-merge'));
  await ui3.select(7);
  assert.ok(detail3.innerHTML.includes('Merged') && !detail3.innerHTML.includes('arm-merge') && !detail3.innerHTML.includes('data-needs-github="merge"'), 'merge controls stay gone after a re-select');
  ui3.stop();
}
