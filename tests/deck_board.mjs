import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { bindDeckBoard } from '../commonplace/web/static/deck-board.js';
import { boardView, bindBoard } from '../commonplace/web/static/board.js';
import { escape as e } from '../commonplace/web/static/api.js';
import { renderDetails } from '../commonplace/web/static/details.js';
const board = {slug:'product',name:'Product',coordinator:'conn:coordinator'};
const bot = {id:'shipping',name:'Shipping',board:'product'};
const base = {number:7,version:3,board:'product',title:'Ship the change',body:'Review the change.',events:[],status:'doing',need:'working',assignee:'conn:worker'};
const options = {board,me:'user:owner',bots:[bot],deck:true,canRemove:true};
const htmlFor = card => boardView({...options,cards:[{...base,...card}]});
const visible = html => html.replace(/<[^>]*>/g,'');
test('Deck cards use exactly one state phrase from live card state', () => {
  for(const [patch,word] of [[{},'Building'],[{status:'review',need:'idle'},'In review'],[{status:'done',need:'done'},'Live'],[{status:'todo',need:'idle'},'Ready'],[{need:'checks'},'Checking'],[{status:'review',need:'you',ask:'Ship?',ask_to:'user:owner'},'Needs you']]) {
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
  assert.equal((html.match(/class="n"/g)||[]).length,4,'all column totals stay quiet grey');
  assert.equal((html.match(/class="col"/g)||[]).length,4);
  assert.match(html,/draggable="true"/);
  const proposal=htmlFor({status:'backlog',need:'you',proposal_status:'pending'});
  assert.match(proposal.split('id="col-doing"')[0],/data-card="7"/,'owner decisions never disappear in collapsed Backlog');
  assert.doesNotMatch(proposal,/class="backlog"/);
});
test('drawer shows summary, live Bot, Coworker, Version and truthful progress; controls survive', () => {
  const html=renderDetails({...base,status:'todo',need:'you',head_sha:'abc1234',can_prioritize:true,body:'Summary.\nEvidence: ~/private/review.json',ask:'Ship?',ask_to:'user:owner',ask_options:['Approve','Hold']},options);
  for(const label of ['Summary','Bot','Coworker','Version','Progress','Edit card','Who','Remove assignment','Approve','Hold']) assert.ok(visible(html).includes(label),label);
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
  assert.equal(resolveRoute('#/',{boards,deck:true}),'#/','Board alias preserves Deck Home');
  assert.equal(resolveRoute('#/',{boards,deck:false}),'#/bots','classic root stays unchanged');
});
test('recorded plan approvals and origin release retain their existing authority paths',()=>{
  const plan=renderDetails({...base,need:'you',actions:['approve'],ask:null},options);
  assert.match(plan,/data-action="need-approve" data-card="7" data-version="3"/);
  const origin=renderDetails({...base,need:'you',awaiting_release:true,can_approve:true},options);
  assert.match(origin,/data-deck-release>Approve</);
  assert.doesNotMatch(origin,/data-action="need-approve"/,'origin release still requires its reason form');
  assert.doesNotMatch(renderDetails({...base,need:'you',awaiting_release:true,can_approve:false,proposal_status:'pending'},options),/data-action="need-approve"|data-deck-release/);
  const unoffered=renderDetails({...base,need:'you',actions:[]},options);
  assert.doesNotMatch(unoffered,/data-action="need-approve"/);
});

test('Board has one title/filter row, live totals and reachable legacy filters', () => {
  const cards = [{...base,need:'you'}, {...base,number:8}, {...base,number:9,status:'review',need:'idle'}, {...base,number:10,status:'done',need:'done'}];
  const html = boardView({...options,cards});
  assert.match(html, /<h1>Board<\/h1>/);
  for (const state of ['todo','doing','review','done']) {
    assert.match(html, new RegExp(`data-board-filter="state:${state}"`));
    assert.match(html, new RegExp(`data-board-column="${state}"`));
  }
  for (const filter of ['mine','agents','blocked']) assert.match(html, new RegExp(`data-board-filter="${filter}"`));
  assert.match(html, /data-action="coordinator"/);
  assert.doesNotMatch(html, /<kbd>|Chat ·/);
  const filtered = boardView({...options,cards,filter:'state:review'});
  assert.match(filtered,/data-card="9"/);
  assert.doesNotMatch(filtered,/data-card="7"|data-card="8"|data-card="10"/);
});
test('drawer progress is a truthful tree, Reply is opt-in and the close control is named', () => {
  const html = renderDetails({...base,need:'you',stage:'Release',events:[{kind:'pr'},{kind:'approve'}],checks:'pass',ask_to:'user:owner',ask_options:['Approve','Hold']},options);
  assert.match(html, /<span>Release<\/span>/);
  assert.match(html, /class="child"><span>Builder<\/span><span class="live">Pushed/);
  assert.match(html, /class="waiting">Needs you/);
  assert.match(html, /id="deck-card-reply" hidden/);
  assert.match(html, /data-deck-reply-toggle aria-expanded="false"/);
  assert.match(html, /aria-label="Close card"><svg/);
  assert.doesNotMatch(visible(html), /More controls|Hide|Needs me|Needs a decision/);
  const memory = renderDetails({...base,linked:[{drawer_id:'note',title:'Saved words'}]},{...options,tab:'memory'});
  assert.match(memory, /data-details-tab="memory"/);
  assert.match(memory, /Saved words/);
});

test('the drawer always offers Move to…, for a card waiting on the person and for any other', () => {
  for (const card of [{...base,need:'you',ask_to:'user:owner',ask_options:['Approve','Hold']}, {...base,status:'doing',need:'working'}]) {
    const html = renderDetails(card, options);
    assert.match(html, new RegExp(`<button class="btn" data-action="card-menu" data-card="${card.number}" aria-haspopup="dialog">Move to…</button>`));
    assert.ok(visible(html).includes('Move to…'));
  }
});

test('drawer treats replacement tokens as literal card text and decision values', () => {
  for (const text of ['$&', '$`', "$'", '$$', "$& $` $' $$"]) {
    const card = {...base,title:`Title ${text}`,summary:`Summary ${text}`,body:`Body ${text}`,coworker:{name:`Coworker ${text}`},need:'you',ask_to:'user:owner',ask_options:[`Approve ${text}`,`Hold ${text}`]};
    const html = renderDetails(card, options);
    assert.equal(html.match(/data-details-title>([^<]*)<\/h2>/)?.[1], e(card.title));
    assert.equal(html.match(/aria-label="Summary">[\s\S]*?<p>([^<]*)<\/p>/)?.[1], e(card.summary));
    assert.equal(html.match(/<dt>Coworker<\/dt><dd>([^<]*)<\/dd>/)?.[1], e(card.coworker.name));
    assert.ok(html.includes(`data-choice="${e(card.ask_options[0])}"`));
    assert.ok(html.includes(`data-choice="${e(card.ask_options[1])}"`));
    assert.equal((html.match(/<header class="detail-h">/g) || []).length, 1);
    const fallback = renderDetails({...card,summary:null}, options);
    assert.equal(fallback.match(/aria-label="Summary">[\s\S]*?<p>([^<]*)<\/p>/)?.[1], e(card.body));
  }
});
test('Edit card contains only fields and editing actions; resting sections stay outside', () => {
  const card = {...base,can_prioritize:true,status:'review',need:'you',ask:'Ready?',ask_to:'user:owner',waiting:{text:'Waiting for a decision'},blocked_by:[],events:[{kind:'ask',options:['Approve'],context:'Decision context'}]};
  const html = renderDetails(card,{...options,origin:{actor:'user:owner',text:'Original request'}});
  const edit = html.match(/<details class="deck-card-controls"[\s\S]*?<\/details>/)?.[0];
  assert.ok(edit);
  for (const control of ['card-status','card-priority','data-action="handoff"','<dl class="fields">']) assert.ok(edit.includes(control),control);
  for (const marker of ['data-details-description','class="activity"','data-details-waiting','data-details-blockers','data-card-runs','data-context-text','data-origin-text']) {
    assert.ok(html.includes(marker),marker);
    assert.ok(!edit.includes(marker),`${marker} must not be collapsed under Edit card`);
    assert.ok(html.indexOf(marker) > html.indexOf('</details>'),marker);
  }
});

test('optional Deck binding preserves minimal classic roots and tolerates partial scrollers', () => {
  const events = new Map(), root = {
    addEventListener: (name, handler) => events.set(name, handler),
    removeEventListener: name => events.delete(name),
  };
  const stop = bindBoard(root, {board});
  assert.deepEqual([...events.keys()], ['click','submit','keydown']);
  stop();
  assert.equal(events.size, 0);
  for (const partial of [null, {}, {querySelector:null}, {querySelector:()=>null}, {querySelector:()=>({}),querySelectorAll:()=>[]}]) {
    const cleanup = bindDeckBoard(partial);
    assert.equal(typeof cleanup, 'function');
    assert.doesNotThrow(cleanup);
  }
  const methods = ['querySelector','querySelectorAll','getBoundingClientRect','addEventListener','removeEventListener'];
  for (const missing of methods) {
    const scroller = {querySelector:()=>null,querySelectorAll:()=>[],getBoundingClientRect:()=>({left:0}),addEventListener(){},removeEventListener(){}};
    delete scroller[missing];
    const cleanup = bindDeckBoard({querySelector:()=>scroller,querySelectorAll:()=>[]});
    assert.equal(typeof cleanup, 'function');
    assert.doesNotThrow(cleanup);
  }
});

test('phone Board actions share the title row and state pills have their own full-width row', () => {
  const html = htmlFor({});
  const title = html.match(/class="deck-board-title-row">([\s\S]*?)<\/header>/)?.[1];
  assert.ok(title, 'title row exists');
  assert.match(title, /<h1>Board<\/h1>[\s\S]*class="deck-board-phone-actions"[\s\S]*data-action="new-card"/);
  const pills = html.match(/class="deck-board-phone-tools">([\s\S]*?)<div class="board"/)?.[1];
  assert.ok(pills);
  assert.doesNotMatch(pills, /data-action="new-card"|class="deck-board-menu"/);
  const css = readFileSync(new URL('../commonplace/web/static/deck-board.css',import.meta.url),'utf8');
  assert.match(css, /mask-image:[^;]*16px/);
  assert.match(css, /deck-board-states\s*\{[^}]*width:\s*100%/);
});
test('Board phone chrome keeps the mark and controls while the page carries its title', async () => {
  const {renderDeckTop} = await import('../commonplace/web/static/deck.js');
  for (const options of [{hash:'#/board/product'},{hash:'#/board'},{board:{slug:'product'}}]) {
    const top = renderDeckTop(options);
    assert.doesNotMatch(top, /<b>Board<\/b>/);
    assert.match(top, /class="deck-mark"/);
    assert.match(top, /data-action="palette"/);
  }
  assert.match(renderDeckTop({hash:'#/board/product/chat'}), /<b>Board<\/b>/, 'chat chrome stays unchanged');
  assert.match(htmlFor({}), /<h1>Board<\/h1>/);
});
test('drawer progress parent uses its state text without a duplicate state dot', () => {
  const html = renderDetails({...base,need:'you'},options);
  const parent = html.match(/<li class="current">([\s\S]*?)<\/li>/)?.[1];
  assert.ok(parent);
  assert.doesNotMatch(parent, /deck-board-dot|<i/);
  assert.match(parent, /class="waiting">Needs you/);
});
test('desktop filter group gets twelve extra pixels before Add card', () => {
  const css = readFileSync(new URL('../commonplace/web/static/deck-board.css',import.meta.url),'utf8');
  assert.match(css, /\.deck-board-filters\s*\{[^}]*margin-right:\s*12px/);
});
