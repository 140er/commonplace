import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { boardView } from '../commonplace/web/static/board.js';
import { renderDetails } from '../commonplace/web/static/details.js';
const board = {slug:'product',name:'Product',coordinator:'conn:coordinator'};
const bot = {id:'shipping',name:'Shipping',board:'product'};
const base = {number:7,version:3,board:'product',title:'Ship the change',body:'Review the change.',events:[],status:'doing',need:'working',assignee:'conn:worker'};
const options = {board,me:'user:owner',bots:[bot],deck:true};
const htmlFor = card => boardView({...options,cards:[{...base,...card}]});
const visible = html => html.replace(/<[^>]*>/g,'');
test('Deck cards use exactly one state phrase from live card state', () => {
  for(const [patch,word] of [[{},'Building'],[{status:'review',need:'idle'},'In review'],[{status:'done',need:'done'},'Live'],[{status:'todo',need:'idle'},'Ready'],[{need:'checks'},'Checking'],[{status:'review',need:'you',ask:'Ship?',ask_to:'user:owner'},'Needs a decision']]) {
    const html=htmlFor(patch);
    assert.match(html,new RegExp(`data-card-state>${word}<`));
    assert.match(html,/>Shipping</);
    assert.match(html,/class="deck-board-avatar"/);
    assert.doesNotMatch(visible(html), /P-7|#7|builder|fix round/i);
  }
});
test('only personal action cards get the amber edge; routine waits never do', () => {
  for(const patch of [{need:'working'},{status:'review',need:'idle',ask:'Check?',ask_to:'conn:reviewer'},{need:'checks'},{need:'plan'},{status:'done',need:'done'},{waiting:{code:'project_limit',text:'Waiting for a slot'}}]) assert.doesNotMatch(htmlFor(patch), /class="card[^\"]* is-you/);
  assert.match(htmlFor({need:'you'}),/class="card[^\"]* is-you/);
  const html=boardView({...options,cards:[{...base,need:'you'},{...base,number:8,status:'done',need:'done'}]});
  assert.equal((html.match(/class="n"/g)||[]).length,1,'only the action column has a count');
  assert.equal((html.match(/class="col"/g)||[]).length,4);
  assert.match(html,/draggable="true"/);
});
test('drawer shows summary, live Bot, Coworker, Version and truthful progress; controls survive', () => {
  const html=renderDetails({...base,status:'todo',need:'you',head_sha:'abc1234',can_prioritize:true,body:'Summary.\nEvidence: ~/private/review.json',ask:'Ship?',ask_to:'user:owner',ask_options:['Approve','Hold']},options);
  for(const label of ['Summary','Bot','Coworker','Version','Progress','More controls','Who','Remove assignment','Approve','Hold']) assert.ok(visible(html).includes(label),label);
  assert.match(html,/<code[^>]*>abc1234<\/code>/);
  assert.doesNotMatch(visible(html),/~\/|review\.json|Evidence:|P-7|#7/);
  for(const action of ['edit-card','handoff','panel-move','remove-assignment']) assert.match(html,new RegExp(`data-action="${action}"`));
  assert.match(html,/data-choice="Approve"/);
  assert.match(html,/data-choice="Hold"/);
  assert.ok(html.indexOf('>Who<') < html.indexOf('data-action="remove-assignment"'));
  const missing=renderDetails(base,{...options,bots:[]});
  assert.doesNotMatch(missing,/Review gate|Checks.*Passed|abc1234/,'no invented progress');
});
test('slice CSS uses Deck tokens and scopes phone overflow and drawer motion',()=>{
  const css=readFileSync(new URL('../commonplace/web/static/deck-board.css',import.meta.url),'utf8');
  assert.doesNotMatch(css, /#[\da-f]{3,8}\b/i);
  assert.match(css,/border-left:\s*2px solid var\(--amber\)/);
  assert.match(css,/translateX\(32px\)/);
  assert.match(css,/240ms/);
  assert.match(css,/prefers-reduced-motion/);
  assert.match(css,/overflow-x:\s*auto/);
});
test('classic cards and drawer retain their existing identifiers and controls',()=>{
  const classic=boardView({...options,deck:false,cards:[base]});
  assert.match(classic,/>P-7</);
  assert.doesNotMatch(classic,/data-card-state/);
  assert.match(renderDetails(base,{...options,deck:false}),/>Description</);
});
test('Deck copy never rewrites exact decision payloads or version values',()=>{
  const card={...base,status:'review',need:'you',ask:'Ship?',ask_to:'user:owner',ask_options:['Approve P-7 #123','Hold']};
  const html=renderDetails(card,options);
  assert.match(html,/data-choice="Approve P-7 #123"/);
  assert.match(html,/data-version="3"/);
  assert.doesNotMatch(visible(html),/#123|P-7/);
});
test('canonical Board route reuses the last readable board and classic routing stays unchanged',async()=>{
  const {resolveRoute}=await import('../commonplace/web/static/routes.js');
  const boards=[board,{slug:'other',name:'Other'}];
  assert.equal(resolveRoute('#/board',{boards,last:'other',deck:true}),'#/board/other');
  assert.equal(resolveRoute('#/board',{boards,last:'hidden',deck:true}),'#/board/product');
  assert.equal(resolveRoute('#/board',{boards,deck:false}),'#/board');
});
