import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { arrangeSettings, permissionLabel } from '../commonplace/web/static/deck-settings.js';
import { createDeck } from '../commonplace/web/static/deck.js';
import { mountToolPolicy, policyRow } from '../commonplace/web/static/tool-policy.js';
import { escape as e } from '../commonplace/web/static/api.js';

const ui = on => createDeck({ storage: { getItem: () => on ? 'deck' : null }, doc: null });
const modes = ['allowed', 'ask', 'off'];
const names = JSON.parse(process.env.POLICY_ACTIONS || '["task_add","memory_save","notification_list","webhook_add"]');
const actions = [...names.map(name => ({name, kind:'write', mode:'allowed'})),
  {name:'retired_custom_action', kind:'unknown', mode:'off'}];
async function mounted(on = true) {
  const list = {innerHTML:''}, input = {}, host = {isConnected:true, innerHTML:'',
    querySelector: q => q === '[data-tool-policy-list]' ? list : input};
  const calls = [];
  mountToolPolicy(host, {e, presentation:ui(on).policyPresentation?.(), api:async (path, options) => {
    calls.push([path, options]);
    return {actions,modes,approval:{approval_ttl_s:600}};
  }});
  await new Promise(resolve => setImmediate(resolve));
  return {host,list,input,calls};
}
test('Deck groups permissions with human labels and searches notification purposes', async () => {
  const {host,list,input} = await mounted();
  assert.match(host.innerHTML, /Search permissions/);
  assert.match(host.innerHTML, /Cards and projects/);
  input.oninput({target:{value:'notif'}});
  assert.match(list.innerHTML, /Notifications and connections/);
  assert.match(list.innerHTML, /webhook_add/);
  assert.doesNotMatch(list.innerHTML, /data-tool-policy-row="task_add"/);
  input.oninput({target:{value:'no matching permission'}});
  assert.match(list.innerHTML, /No action matches/);
});
test('every old permission remains reachable by its label and raw search; wire names stay in details', async () => {
  const {host,list,input,calls} = await mounted();
  const presentation = ui(true).policyPresentation?.();
  assert.ok(presentation, 'Deck provides a permission presentation');
  for (const action of actions) {
    const label = presentation.label(action);
    assert.ok(label && !label.includes('_'), `${action.name} needs a human label`);
    for (const search of [label, action.name]) {
      input.oninput({target:{value:search}});
      assert.ok(list.innerHTML.includes(`data-tool-policy-row="${action.name}"`), `${action.name}: ${search}`);
    }
    const row = presentation.rows([action],modes,'',e);
    assert.ok(row.includes(`title="${e(action.name)}"`), 'wire name is available in the row title');
    const visible = row.replace(/<details[\s\S]*?<\/details>/g,'').replace(/<[^>]*>/g,'');
    assert.ok(!visible.includes(action.name), action.name);
    assert.ok(!row.match(/aria-label="[^"]*_/), 'accessible label is human too');
  }
  // Exercise the existing save handler and prove that the wire identity is preserved.
  await list.onchange({target:{closest:() => ({dataset:{toolPolicyMode:'task_add'},value:'off'})}});
  assert.deepEqual(calls.at(-1), ['/api/tool-policy', {method:'POST',body:{actions:{task_add:'off'}}}]);
  assert.match(host.innerHTML, /Search permissions/);
});
test('Classic policy markup remains byte-identical', async () => {
  const {host} = await mounted(false);
  assert.equal(ui(false).policyPresentation?.() ?? null, null);
  assert.ok(host.innerHTML.includes(policyRow(actions[0],modes,e)));
  assert.doesNotMatch(host.innerHTML, /Technical name|Search permissions/);
});

test('permission labels ignore prototype members and use notification outcomes', () => {
  assert.equal(permissionLabel({name:'constructor'}), 'Constructor');
  assert.equal(permissionLabel({name:'toString'}), 'ToString');
  const expected = {webhook_add:'Send notifications to another app', webhook_list:'See where notifications are sent', webhook_remove:'Stop sending to an app', webhook_test:'Send a test notification'};
  for (const [name,label] of Object.entries(expected)) assert.equal(permissionLabel({name}),label);
  assert.equal(ui(true).policyPresentation().introduction(10), 'Ask sends the request to Today first. Deny refuses it.');
});
test('notification guidance distinguishes the owner and tolerates a missing connection heading', () => {
  for (const owner of [false,true]) {
    const notice = {textContent:'Not set up on this server.'};
    const connection = {querySelector:()=>null};
    const box = {querySelector:q=>q==='#phone-push'?{querySelector:()=>notice}:q==='.agents-settings'?connection:null,
      querySelectorAll:()=>[],prepend(){},insertAdjacentHTML(){}};
    assert.doesNotThrow(()=>arrangeSettings(box,{owner}));
    assert.equal(notice.textContent.startsWith('Ask the workspace owner'),!owner);
  }
});
test('Settings keeps one scroll listener when a render replaces its navigation', () => {
  const listeners=[];
  let nav=null;
  const box={dataset:{},querySelector:q=>q==='#deck-settings-nav'?nav:null,
    querySelectorAll:()=>[],prepend(){},insertAdjacentHTML(){nav={};},
    addEventListener:(...args)=>listeners.push(args)};
  arrangeSettings(box);
  arrangeSettings(box);
  nav=null; // A late render can replace the children of the same scrolling box.
  arrangeSettings(box);
  assert.equal(listeners.length,1);
  assert.equal(listeners[0][0],'scroll');
  assert.deepEqual(listeners[0][2],{passive:true});
});
for (const initial of ['#/boards','#/needs','#/settings']) test(`late Voice after boot on ${initial} respects the active Settings epoch`, async () => {
  const app=readFileSync(new URL('../commonplace/web/static/app.js',import.meta.url),'utf8');
  const boot=app.slice(app.indexOf('async function boot()'),app.lastIndexOf('boot();'));
  const settings=app.slice(app.indexOf('async function settingsView('),app.indexOf('async function memoryView('));
  for (const navigate of [false,true]) {
    let resolveVoice, injected=0;
    const ready=new Promise(resolve=>{resolveVoice=resolve;});
    const content={innerHTML:'existing screen',querySelector:q=>q==='.settings'?{}:null};
    const state={base:initial,screen:0,deck:{on:()=>true,policyPresentation:()=>null,settings:()=>{injected++;}},pending:new Set()};
    const context={state,root:{querySelector:()=>content},voice:{load:()=>ready,settings:()=>''},
      boundedRequest:async()=>({people:{}}),api:async path=>path==='/api/chat-settings'?{front:{enabled:false},usage:{}}:{},
      setPeople(){},shell(){},schedule(){},signin(){},title(){},remember:()=>({}),restore(){},message:()=>'',e:String,
      phonePushView:()=>'',planView:()=>'',agentsView:()=>'',mountWorkspaceOrchestration(){},mountAgentSchedules(){},mountToolPolicy(){},
      location:{hash:initial,search:'',pathname:'/app/'},URLSearchParams,
      route:async()=>{state.screen++; if(initial==='#/settings') await context.settingsView(state.screen);}};
    runInNewContext(settings+'\n'+boot,context);
    await context.boot();
    const before=injected;
    if(navigate){state.base='#/boards';state.screen++;}
    resolveVoice(); await new Promise(resolve=>setImmediate(resolve));
    assert.equal(injected-before,initial==='#/settings'&&!navigate?1:0,'late Voice must not inject Settings chrome into another screen');
  }
});
