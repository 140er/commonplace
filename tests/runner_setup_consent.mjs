import assert from 'node:assert/strict';
import * as consent from '../commonplace/web/static/runnerconsent.js';
const { runnerConsent, runnerStep, decideRunner } = consent;
import { renderNeeds } from '../commonplace/web/static/needs.js';

const item = { id:'opaque-request',machine_id:'opaque-machine',kind:'runner_consent',approval_kind:'grant',provider:'host',state:'pending',scope_digest:'frozen',fingerprint:'a'.repeat(64),label:'Build machine',projects:['Website'],topics:['Development'],runtime:'approval-required',setup_steps:['check','codex','grant','attest'] };
assert.ok(!renderNeeds().includes('runner-consent-card'));
assert.ok(!renderNeeds({needs:{runner_setup:[item]},filter:'reviews'}).includes('runner-consent-card'));
const html = runnerConsent(item);
assert.match(html, /class="runner-primary" disabled/);
assert.match(html, /This is not my machine/);
assert.match(html, /Step 3 of 4/);
assert.equal((html.match(/<span>aaaaaaaa<\/span>/g) || []).length,8);
assert.ok(!html.replace(/data-[a-z-]+="[^"]*"/g,'').includes('opaque-'));
assert.ok(runnerConsent({...item,label:'<script>'}).includes('&lt;script&gt;'));
assert.match(runnerConsent({...item,approval_kind:'login',provider:'codex'}), /<h2>Check this machine<\/h2>/);
const completed=runnerConsent({...item,ui_result:'confirmed'});
assert.match(completed,/Approved\. The machine continues setup by itself\./);
assert.ok(!completed.includes('data-runner-fingerprint'));
assert.ok(!completed.includes('<button'));
assert.ok(!completed.includes('This machine cannot run work yet.'));
const manual=runnerConsent({...item,approval_kind:'login',provider:'claude',manual_login:true});
assert.ok(manual.includes('<ol><li>Run the command below on the machine.</li><li>Open the link it prints, on any device.</li><li>Type the code from that page into the machine.</li></ol>'));
assert.ok(manual.includes('Commonplace never sees this code.'));
assert.ok(manual.includes('Run on the machine</p><code'));
assert.ok(manual.includes('sh "$HOME/.commonplace-runner/sign-in" claude'));
assert.ok(manual.includes('Waiting for sign-in on the machine'));
assert.ok(!manual.includes('checkbox'));
assert.ok(!manual.includes('data-runner-consent-action="login"'));
assert.ok(manual.includes('This is not my machine'));
const confirming=runnerConsent({...item,ui_refusing:true});
assert.match(confirming,/Yes, refuse this machine/);
assert.ok(!confirming.includes('checkbox'));
assert.ok(!confirming.includes('Approve access'));
// Count the machine's actual steps; each title describes a different action.
for (const steps of [['check','codex','grant','attest'],['claude','grant','attest'],['grant','attest'],['check','codex','claude','grant','attest']]) {
  const titles=[];
  for (const [index,key] of steps.entries()) {
    const login=['check','codex','claude'].includes(key);
    const current={...item,setup_steps:steps,approval_kind:login?'login':key,
      provider:key==='claude'?'claude':'codex',manual_login:key==='claude',state:key==='codex'?'approved':'pending'};
    const step=runnerStep(current),markup=runnerConsent(current);
    assert.equal(step.number,index+1);
    assert.equal(step.total,steps.length);
    assert.ok(markup.includes(`Step ${index+1} of ${steps.length}`));
    assert.ok(markup.includes(`<h2>${step.title}</h2>`));
    titles.push(step.title);
  }
  assert.equal(new Set(titles).size,steps.length);
}
assert.equal(runnerStep({...item,approval_kind:'login',provider:'codex'}).title,'Check this machine');
assert.equal(runnerStep({...item,approval_kind:'login',provider:'codex',state:'approved'}).title,'Sign in to Codex');
assert.equal(runnerStep(item).title,'Approve machine access');
assert.equal(runnerStep({...item,approval_kind:'attest'}).title,'Confirm this machine');
const calls=[];
const api=async (path,options)=>calls.push({path,...options});
await decideRunner(api,item,'grant');
assert.deepEqual(calls[0],{path:'/api/runners/opaque-machine/setup-requests/opaque-request/decide',method:'POST',body:{approve:true,scope_digest:'frozen',fingerprint:'a'.repeat(64)}});
await decideRunner(api,{...item,approval_kind:'attest',binding:{topics:['Development'],provider_instance_id:'private-provider',worker_connection_id:'private-worker'}},'attest');
assert.ok(calls[1].path.endsWith('/attest'));
assert.equal(calls[1].body.setup_attestation.request_id,item.id);
assert.equal(calls[1].body.setup_attestation.fingerprint,item.fingerprint);
await decideRunner(api,item,'device');
assert.ok(calls[2].path.endsWith('/device'));
assert.deepEqual(calls[2].body,{});
await decideRunner(api,item,'refuse');
assert.ok(calls[3].path.endsWith('/refuse'));
assert.deepEqual(calls[3].body,{scope_digest:item.scope_digest,fingerprint:item.fingerprint});
assert.ok(renderNeeds({needs:{runner_setup:[item]}}).includes('1 item, 0 projects'));
