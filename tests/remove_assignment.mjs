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
const deckOwner = {deck:true, canRemove:true};
const ownerHTML = details.renderDetails(card, deckOwner);
assert.match(ownerHTML, /data-action="remove-assignment"/);
assert.doesNotMatch(ownerHTML, /Anyone eligible|Anyone who can work/);
const memberHTML = details.renderDetails(card, {deck:true, canRemove:false});
assert.doesNotMatch(memberHTML, /data-action="remove-assignment"/);
assert.match(memberHTML, /Only the workspace owner can change this/);
assert.match(details.renderDetails({...card, assignee:null}, deckOwner), /Nobody yet.*Anyone who can work on this project can pick it up/);
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
  deckAssignmentForm:details.deckAssignmentForm, assignmentRefusal:details.assignmentRefusal,
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

// Deck runs the same click, form and submit handlers as Classic.
state.deck = {on:()=>true};
state.me = {can_admin:false};
panel.innerHTML = '';
await context.click({target:{closest:()=>({dataset:{action:'remove-assignment'}})}});
assert.equal(panel.innerHTML, '', 'Non-owners cannot open removal');
state.me.can_admin = true;
await context.click({target:{closest:()=>({dataset:{action:'remove-assignment'}})}});
assert.match(panel.innerHTML, /<label>Reason<textarea[^>]*required><\/textarea>/);
assert.match(panel.innerHTML, /type="submit" disabled/);
assert.match(panel.innerHTML, /Saved in the card&#39;s activity|Saved in the card's activity/);
assert.match(panel.innerHTML, /Anyone who can work on this project/);
assert.doesNotMatch(panel.innerHTML, /Reason \(required\)|var\(--block\)/);
const button = {disabled:true};
runInNewContext(slice('root.addEventListener("input",', 'root.addEventListener("change",'), context);
const input = {dataset:{}, value:' ', closest:()=>({dataset:{card:'1'}, querySelector:()=>button})};
context.input({target:input});
assert.equal(button.disabled,true);
input.value = 'Ready for someone else'; context.input({target:input});
assert.equal(button.disabled,false);
const known = [
  [403, {code:'handoff_refused', error:'Card is reserved for another connection; the workspace owner can move it.'}, 'Only the workspace owner can remove this assignment.'],
  [403, {code:'handoff_refused', error:'This card came from a restricted connection and has not been released, so it cannot be handed off.'}, 'This card is still locked by the bot that made it. The owner can unlock it in the card, then try again.'],
  [409, {current:{status:'doing',assignee:'conn:receiver'}}, 'receiver (agent) has started working on this card, so its assignment stays.'],
  [403, {code:'future_refusal',error:'<Unknown reason>'}, '&lt;Unknown reason&gt;'],
  [403, {code:'handoff_refused',error:'A new restriction.'}, 'A new restriction.'],
];
for (const [status,payload,expected] of known) {
  context.api = async()=>{throw Object.assign(new Error(payload.error || 'card changed'),{status,payload});};
  await submit();
  assert.ok(panel.innerHTML.includes(expected), panel.innerHTML);
  assert.match(panel.innerHTML, /Assignment not removed/);
  assert.match(panel.innerHTML, />Close<\/button>/);
  assert.doesNotMatch(panel.innerHTML, /<form|<textarea|confirm-removal/);
  assert.equal((panel.innerHTML.match(/<button/g)||[]).length,1);
}
console.log('Deck ownership, blank/filled reason, three refusal states and unknown fallback pass');
