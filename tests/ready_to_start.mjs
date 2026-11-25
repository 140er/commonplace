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
    // A done step keeps one way to change it; a step that does not apply has nothing to do.
    assert.equal((row.match(/<a /g)||[]).length,ready[id].state==='not_needed'?0:1,row);
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
    assert.match(row,/<b>(Choose a model|Add the model key|Add a machine|Approve the machine|Connect an app)<\/b>/);
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
  // Without a project the model step points at a project's settings (where Chat answers lives), never workspace Settings.
  assert.match(html,/href="#\/boards">a project’s settings<\/a>, under Chat answers/);
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

test('the headline counts the steps that wait on the model apart from the ones that need you now',()=>{
  const waiting={...ready,model:{state:'needs_you',code:'needs_configuration'},key:{state:'needs_you',code:'choose_model_first'},machine:{state:'needs_you',code:'runner_not_configured'},connection:{state:'needs_you',code:'connection_missing'}};
  assert.match(render(waiting),/<p class="ready-summary">3 setup steps need you; 1 more waits on the model\. Start with the first one\.<\/p>/);
  assert.match(render({...ready,machine:{state:'needs_you',code:'runner_not_configured'}}),/<p class="ready-summary">One setup step needs you before this bot can work\./);
});
