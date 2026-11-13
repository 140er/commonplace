import assert from 'node:assert/strict';
import { test } from 'node:test';
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
    assert.match(row, /<details[\s\S]*<summary>Technical name<\/summary>/);
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
