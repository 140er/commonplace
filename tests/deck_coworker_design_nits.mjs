import assert from 'node:assert/strict';
import { test } from 'node:test';
import { accessLogText, sharingHTML } from '../commonplace/web/static/bot-sharing.js';
import { escape as e } from '../commonplace/web/static/api.js';

const people = {'user:owner':'Workspace owner', 'user:member':'Workspace member', 'user:other':'Workspace owner'};
const entry = {action:'share', actor_id:'owner', member_id:'member', at:'2026-01-02T12:34:00Z'};
test('access history uses You only for the authenticated viewer identity', () => {
  assert.equal(accessLogText(entry, people, 'owner'), 'You gave Workspace member access to this bot.');
  assert.equal(accessLogText({...entry, actor_id:'other'}, people, 'owner'), 'Workspace owner gave Workspace member access to this bot.');
  assert.equal(accessLogText(entry, people), 'Workspace owner gave Workspace member access to this bot.');
  assert.equal(accessLogText({...entry, actor_id:'missing'}, people, 'owner'), 'a former workspace member gave Workspace member access to this bot.');
});
test('both sharing views pass the viewer identity and retain text escaping', () => {
  const bot = {name:'Helper', mine:true, kind:'custom', owner:{id:'owner',name:'Workspace owner'}};
  for (const capy of [true,false]) for (const identity of [{user:'owner'}, {actor:'user:owner'}]) {
    const html = sharingHTML({kind:'sharing', capy, tab:'log', settings:{sharing:true}, shares:[], log:[entry], me:{...identity,people}}, bot, e);
    assert.match(html, /You gave Workspace member access to this bot\./);
    assert.doesNotMatch(html, /Workspace owner gave/);
    const escaped = sharingHTML({kind:'sharing', capy, tab:'log', settings:{sharing:true}, shares:[], log:[{...entry,actor_id:'member'}], me:{...identity,people:{...people,'user:member':'<img>'}}}, bot, e);
    assert.match(escaped, /&lt;img&gt;/); assert.doesNotMatch(escaped, /<img>/);
  }
});
