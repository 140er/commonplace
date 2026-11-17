import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createCoworkers, coworkerJobHTML} from '../commonplace/web/static/coworkers.js';
import {sheetHTML} from '../commonplace/web/static/bots.js';
import {mount} from '../commonplace/web/static/deck-coworkers.js';
import {escape as e} from '../commonplace/web/static/api.js';

const settle = async () => {for (let i=0;i<30;i++) await new Promise(setImmediate);};
function conversationPage(canManage = true) {
  const events = new Map(), host = {innerHTML:'', addEventListener:(kind, fn)=>events.set(kind,fn), removeEventListener(){}, querySelector(){return null;},querySelectorAll(){return [];}};
  const doc = new EventTarget(); doc.hidden=false;
  const ui = createCoworkers({mount:()=>host, api:async()=>({bots:[],conversations:[],members:[],user_id:'owner',can_manage_bots:canManage}), current:()=>({workspace:'fixture',actor:'user:owner'}), doc, events:new EventTarget(), timers:{setTimeout(){},clearTimeout(){}}});
  const click = action => events.get('click')({target:{closest:selector=>selector==='[data-cw-action]'?{dataset:{cwAction:action}}:null},preventDefault(){}});
  ui.show();return {ui,host,click};
}
async function emptyRoster(extra = {}) {
  const body={innerHTML:''};
  const root={innerHTML:'',ownerDocument:{documentElement:{dataset:{ui:'deck'}}},classList:{add(){},remove(){}},querySelector:selector=>selector==='[data-cb="body"]'?body:null,addEventListener(){},removeEventListener(){}};
  const ui=mount({root,route:'#/bots/-/coworkers',me:{workspace:'fixture',actor:'user:owner'},api:async path=>path==='/api/bots'?{bots:[],...extra}:{bots:[],conversations:[]}});
  await settle();ui.stop();return body.innerHTML;
}
test('empty DM prerequisite opens the same bot editor',async()=>{
  const f=conversationPage();await settle();f.click('new-conversation');
  assert.match(f.host.innerHTML,/Create a bot before starting a DM\.[^]*data-cw-action="new-bot"[^]*>Create bot</);
  f.click('new-bot');assert.match(f.host.innerHTML,/data-cw-form="bot"/);f.ui.stop();
});
test('both create forms use the same name rule and wording',async()=>{
  const f=conversationPage();await settle();f.click('new-bot');
  for(const html of [f.host.innerHTML,sheetHTML({kind:'new'},{},e)]) {
    assert.match(html,/data-bot-create-fields/);
    assert.match(html,/maxlength="60"/);
    assert.match(html,/How to work/);
    assert.match(html,/starting with a letter or number/);
    assert.match(html,/>Create bot</);
  }
  f.ui.stop();
});
test('empty project roster names the Add action',async()=>{
  const html=await emptyRoster();
  assert.match(html,/No bots on this project yet\./);
  assert.match(html,/<a[^>]*href="#\/bots\/\-\/coworkers\/conversations"[^>]*>Add a bot</);
});
test('configuration and failed jobs explain the reason and link the fix',()=>{
  for(const [code,state,reason,action] of [
    ['needs_configuration','needs_configuration','Needs a model','Project settings'],
    ['runner_not_configured','done','Needs a machine','Add machine'],
    ['provider_unavailable','failed','provider could not be reached','Try again'],
    ['key_missing','needs_configuration','model has no key','Project settings'],
  ]) {
    const html=coworkerJobHTML({id:1,conversation_id:'room',message_id:1,state,code},[],false,{},'product');
    assert.ok(html.includes(reason),html);assert.ok(html.includes(action),html);
    assert.match(html,/<a [^>]*href=/);
    assert.doesNotMatch(html,/<small>needs_configuration<\/small>/);
  }
});

test('shared name validation rejects invalid names and preserves human display names', async()=>{
  const {botNameError} = await import('../commonplace/web/static/bot-create.js');
  for(const name of ['', '   ', ':lead', 'Bad/Name', '!!!', 'a'.repeat(61)]) assert.match(botNameError(name), /letters/);
  for(const name of [' Research Helper ', 'Helper_2', 'Local:helper@work']) assert.equal(botNameError(name), '');
});
test('members get an honest owner prerequisite without an unusable create action',async()=>{
  const f=conversationPage(false);await settle();f.click('new-conversation');
  assert.match(f.host.innerHTML,/Ask the workspace owner to create one/);
  assert.doesNotMatch(f.host.innerHTML,/data-cw-action="new-bot"/);f.ui.stop();
});
