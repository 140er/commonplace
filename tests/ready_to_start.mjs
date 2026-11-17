import assert from 'node:assert/strict';
import {test} from 'node:test';
import * as setup from '../commonplace/web/static/ready-to-start.js';
import {settingsOptions} from '../commonplace/web/static/projects.js';
import {modelNoteHTML} from '../commonplace/web/static/bots.js';
import {escape as e} from '../commonplace/web/static/api.js';

const ids = ['model','key','machine','connection','trust','notifications'];
export const ready = {model:{state:'done'},key:{state:'not_needed'},machine:{state:'done'},connection:{state:'done'},trust:{state:'done'},notifications:{state:'not_needed'}};
const render = (rows, owner = true, board = 'product') => setup.readyToStartHTML(rows, {canConfigure:owner,board});
test('one checklist has every prerequisite, state, reason and exactly one owner action per row',()=>{
  assert.equal(typeof setup.readyToStartHTML,'function','develop has no Ready to start checklist');
  const html=render(ready);
  assert.match(html,/Ready to start\?/);
  for(const id of ids) {
    const row = new RegExp(`<li data-ready="${id}"[^]*?<\\/li>`).exec(html)?.[0] || '';
    assert.match(row,new RegExp(`data-state="${ready[id].state}"`));
    assert.equal((row.match(/<a /g)||[]).length,1,row);
    assert.match(row,/<p>[^]+<\/p>/);
  }
  assert.match(html,/Notifications[^]*Not needed[^]*optional/i);
});
test('missing prerequisites reuse recovery wording and lead to the actual setup flows',()=>{
  for(const [id,code,reason,href] of [
    ['model','needs_configuration','Needs a model','#/board/product'],
    ['key','key_missing','model has no key','#/board/product'],
    ['key','key_not_allowed','model has no key','#/board/product'],
    ['machine','runner_not_configured','Needs a machine','#/admin'],
    ['trust','machine_untrusted','Approve','#/admin'],
    ['connection','connection_missing','Connect','#/board/product'],
  ]) {
    const html=render({...ready,[id]:{state:'needs_you',code}});
    const row=new RegExp(`<li data-ready="${id}"[^]*?<\\/li>`).exec(html)[0];
    assert.ok(row.includes(reason),row);assert.ok(row.includes(`href="${href}"`),row);
    assert.match(row,/Needs you/);
    assert.equal((row.match(/<a /g)||[]).length,1,row);
  }
});
test('members see the same states and ask-the-owner guidance without action links',()=>{
  const html=render({...ready,trust:{state:'needs_you',code:'machine_untrusted'}},false);
  assert.equal((html.match(/data-ready=/g)||[]).length,6);
  assert.match(html,/Ask the project owner/);assert.doesNotMatch(html,/<a |<button|key_ref|token/);
});
test('personal bots do not demand a machine for chat and have one model settings action',()=>{
  const html=render({...ready,machine:{state:'not_needed'},connection:{state:'not_needed'},trust:{state:'not_needed'},model:{state:'needs_you',code:'needs_configuration'}},true,null);
  assert.match(html,/href="#\/settings"/);
  assert.doesNotMatch(html,/data-action="coordinator"/);
});
test('bot status is replaced even when ready; unknown states retain safe existing guidance',()=>{
  assert.match(modelNoteHTML({model:{ready:true},readiness:ready,canConfigure:true},e),/Ready to start\?/);
  assert.match(render({...ready,model:{state:'future',code:'future'}}),/Bots need a chat model/);
  assert.doesNotMatch(render(ready,true,'$&<img>'),/<img>/);
});

test('checklist actions focus their setup section through the existing shell handoff',()=>{
  for(const focus of ['chat','connection','trust']) assert.equal(settingsOptions({action:'coordinator',dataset:{readyFocus:focus}}).focus,focus);
  assert.equal(settingsOptions({action:'coordinator',dataset:{readyFocus:'future'}}).focus,undefined);
});
