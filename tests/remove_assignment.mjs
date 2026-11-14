import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';

const read = name => readFile(new URL(`../commonplace/web/static/${name}`, import.meta.url), 'utf8');
const app = await read('app.js');
const source = (await read('details.js')).replace(/(from\s*)(['"])\.\/([\w-]+\.js)\2/g,
  (_all, from, _quote, name) => from + JSON.stringify(new URL(`../commonplace/web/static/${name}`, import.meta.url).href));
const details = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const card = { number: 1, version: 7, status: 'todo', assignee: 'conn:reserved', title: 'Release notes', body: '', events: [] };
const html = details.renderDetails(card);
assert.match(html, /data-action="remove-assignment"/);
for (const other of [{status:'doing'}, {status:'done'}, {assignee:null}, {claim_until:'later'}, {proposal_status:'pending'}, {proposal_status:'rejected'}]) {
  assert.doesNotMatch(details.renderDetails({...card, ...other}), /data-action="remove-assignment"/);
}
assert.match(details.renderDetails({...card, assignee:null}), /Anyone eligible to work on this board can pick it up/);
assert.match(details.assignmentPickup({...card, awaiting_release:true}), /After.*released/);
const slice = (start, end) => {
  const at = app.indexOf(start), stop = app.indexOf(end, at);
  assert.ok(at >= 0 && stop > at, `Missing ${start}`);
  return app.slice(at, stop);
};
let focused = false;
const panel = {innerHTML:'', hidden:false, querySelector:()=>({focus(){focused=true;}})};
const errors = [], requests = [];
const state = {pending:new Set(), card};
const context = {
  state, canRemoveAssignment:details.canRemoveAssignment, assignmentPickup:details.assignmentPickup,
  detailsUI:null, consoleUI:null, root:{querySelector:()=>panel, querySelectorAll:()=>[], addEventListener:(name, fn)=>{context[name]=fn;}},
  e:value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),
  showError:text=>errors.push(text), refresh:async()=>{}, api:async(path, options)=>{requests.push({path, ...options});},
  data:form=>form.values,
};
runInNewContext(slice('function removeAssignmentForm(', 'function handoffForm('), context);
runInNewContext(slice('root.addEventListener("click",', 'root.addEventListener("submit",'), context);
await context.click({target:{closest:()=>({dataset:{action:'remove-assignment'}})}});
assert.match(panel.innerHTML, /name="note"[^>]*required/);
assert.match(panel.innerHTML, /name="version" value="7"/);
assert.match(panel.innerHTML, /Anyone eligible to work on this board can pick it up/);
assert.doesNotMatch(panel.innerHTML, /name="to"|name="options"|name="context"/);
assert.ok(focused);
const form = {querySelector:()=>null, dataset:{form:'remove-assignment',card:'1'}, values:{note:'No longer assigned',version:'7'}};
runInNewContext(slice('async function mutate(', 'async function approveProposal('), context);
runInNewContext(slice('root.addEventListener("submit",', 'let drag = null;'), context);
const submit = ()=>context.submit({target:{closest:()=>form}, preventDefault(){}});
await submit();
assert.equal(requests.length, 1);
assert.equal(requests[0].path, '/api/cards/1/handoff');
assert.equal(requests[0].method, 'POST');
assert.deepEqual(JSON.parse(JSON.stringify(requests[0].body)), {to:'anyone',note:'No longer assigned',version:7});
form.values.note = ' ';
await submit();
assert.equal(requests.length, 1, 'A blank reason must not send a request');
form.values.note = 'Keep the exact reason';
for (const [status, message] of [[409,'Card changed.'],[403,'Card is reserved for another connection.'],[403,'This card came from a restricted connection and has not been released.']]) {
  context.api = async()=>{throw Object.assign(new Error(message),{status});};
  await submit();
  assert.equal(errors.at(-1),message, 'Show the server reason verbatim');
}
console.log('Removal button, form, request, blank reason and refusal messages pass');
