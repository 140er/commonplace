import assert from 'node:assert/strict';
const b = await import('../commonplace/web/static/board.js');
for (const name of ['cardId', 'isBlocked', 'cardTags', 'boardView', 'filterCards', 'addCardForm', 'submitCard', 'bindBoard']) assert.equal(typeof b[name], 'function');
const board = {name:'Atlas', slug:'atlas'}, me = 'user:maya';
const card = {number:121, title:'Sync', status:'review', ask:'Which limit?', ask_to:'user:leon'};
const render = (cards, options = {}) => b.boardView({board, me, cards, ...options});
switch (process.argv[2]) {
case 'ids':
  assert.equal(b.cardId(board,121),'A-121'); assert.equal(b.cardId({name:'Élan'},2),'E-2'); assert.equal(b.cardId({name:'123',slug:'harbor'},3),'H-3'); assert.equal(b.cardId({name:'日本'},1),'C-1'); break;
case 'blocked':
  assert.equal(b.isBlocked(card,me),true);
  assert.equal(b.isBlocked({...card,status:'doing'},me),false);
  assert.equal(b.isBlocked({...card,ask:null},me),false);
  assert.equal(b.isBlocked({...card,review_kind:'completion'},me),false);
  assert.ok(b.cardTags({...card,ask_to:me},{me}).includes('Needs me'));
  assert.ok(b.cardTags({...card,review_kind:'completion',return_to:'conn:Iris'},{me}).includes('Reviewer: leon'));
  assert.ok(b.cardTags({...card,return_to:'conn:Iris'},{me}).includes('Built by Iris (agent)')); break;
case 'chat': {
  const at = '2026-11-05T14:03:00', tags = b.cardTags({...card,message_id:10,created_at:at},{messages:[{id:10,at}]});
  assert.ok(tags.includes('From chat · 14:03')); assert.ok(!render([{...card,starts_after:118}]).includes('Starts after')); assert.ok(!render([card]).includes('Linked in chat')); break;
}
case 'columns': {
  const html = render([card]); for (const s of ['todo','doing','review','done']) assert.ok(html.includes(`id="col-${s}"`));
  assert.ok(html.includes('Jump to a column')); assert.ok(html.includes('Blocked 1')); assert.ok(html.includes('data-card="121"')); assert.ok(!html.includes('Due')); break;
}
case 'backlog': {
  const html = render([{...card,status:'backlog'}]); assert.ok(html.includes('<summary>Backlog (1)</summary>'));
  assert.ok(!html.includes('<details class="backlog" open')); assert.ok(html.indexOf('Backlog (1)') > html.indexOf('id="col-done"')); assert.ok(html.includes('href="#/card/121"')); break;
}
case 'assignee': {
  const html = b.addCardForm({people:[{ref:'user:leon',name:'Leon'}]}); assert.ok(html.includes('name="assignee"')); assert.ok(html.includes('value="user:leon"'));
  const calls = []; await b.submitCard({board, values:{title:'Test',assignee:'user:leon',body:'Exact\nwords',label:'copy'},api:async (...args) => {calls.push(args);return {number:1};}});
  assert.equal(calls.length,1); assert.equal(calls[0][0],'/api/boards/atlas/cards'); assert.equal(calls[0][1].body.assignee,'user:leon'); assert.equal(calls[0][1].body.body,'Exact\nwords'); assert.deepEqual(calls[0][1].body.labels,['copy']); break;
}
case 'filters': {
  const cards = [card,{...card,number:2,assignee:me,ask_to:me},{...card,number:3,assignee:'conn:Iris',status:'doing'}];
  assert.deepEqual(b.filterCards(cards,'mine',me).map(c=>c.number),[2]); assert.deepEqual(b.filterCards(cards,'agents',me).map(c=>c.number),[3]); assert.deepEqual(b.filterCards(cards,'blocked',me).map(c=>c.number),[121]); break;
}
case 'empty': assert.ok(render([]).includes('Add your first card')); assert.ok(render([]).includes('Connect an agent')); assert.ok(render([]).includes('Ask for something')); assert.ok(b.cardTags({status:'todo'}).includes('Available for an agent')); assert.ok(!b.cardTags({status:'doing',assignee:'conn:Iris',need:'idle'}).some(t=>t.startsWith('Working'))); break;
case 'escaping': {
  const html = render([{...card,title:'<script>bad</script>',ask:'<img>',labels:['<label>']}]); assert.ok(!html.includes('<script>')); assert.ok(html.includes('&lt;script&gt;')); assert.ok(html.includes('&lt;img&gt;')); break;
}
default: assert.fail('Unknown case');
}
