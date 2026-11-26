import assert from 'node:assert/strict';
import { renderCardMenu, createCardMenu } from '../commonplace/web/static/cardmenu.js';
import { boardView as renderBoard } from '../commonplace/web/static/board.js';
import { renderDetails } from '../commonplace/web/static/details.js';
import { PRIORITIES, priorityInfo } from '../commonplace/web/static/card-priority.js';

const board = { slug:'studio', name:'Studio', coordinator:'conn:studio' }, me = 'user:maya';
const card = { number:21, title:'Check the invitation wording', body:'Keep the decision clear.', board:'studio', status:'todo', version:4, priority:'normal', priority_source:'default', can_prioritize:true, events:[] };
const ranked = { ...card, priority:'high', priority_source:'coworker', events:[{ kind:'edit', actor:'conn:studio', text:'Priority set to high by the Coworker: Blocks the invitation', at:'2030-01-01T12:00:00Z' }] };
const decode = value => value.replaceAll('&quot;', '"').replaceAll('&lt;', '<').replaceAll('&gt;', '>').replaceAll('&#39;', "'").replaceAll('&amp;', '&');

function fixture(value = card, options = {}) {
  const handlers = new Map(), calls = [], changes = [], targets = new Map();
  const doc = { activeElement:null, getElementById:id => targets.get(id) };
  const control = dataset => ({ dataset:dataset || {}, disabled:false, focus() { doc.activeElement = this; }, closest() { return this; } });
  const opener = control(); targets.set('more-S-21', opener); opener.focus();
  let html = '', dialog;
  const mount = { ownerDocument:doc,
    addEventListener:(key, fn) => handlers.set(key, fn), removeEventListener:key => handlers.delete(key),
    querySelector:() => dialog,
    get innerHTML() { return html; },
    set innerHTML(value) {
      html = value;
      if (!value) { dialog = null; return; }
      const controls = [...value.matchAll(/<(button|input)\b([^>]*)>/g)].map(([,tag,attrs]) => {
        const el = control(); el.disabled = /\bdisabled\b/.test(attrs); el.tag = tag;
        for (const [attr,key] of [['data-menu-priority','menuPriority'],['data-menu-action','menuAction'],['data-menu-move','menuMove']]) {
          const found = attrs.match(new RegExp(`${attr}="([^"]*)"`)); if (found) el.dataset[key] = found[1];
        }
        return el;
      });
      const alert = { textContent:'' };
      dialog = { controls, alert, open:false, showModal() { this.open = true; }, close() { this.open = false; }, addEventListener() {},
        querySelectorAll:selector => selector.includes(':disabled') ? controls.filter(el => !el.disabled) : controls,
        querySelector(selector) {
          if (selector === '[data-menu-error]') return alert;
          const action = selector.match(/data-menu-action="([^"]+)"/);
          return action ? controls.find(el => el.dataset.menuAction === action[1]) : null;
        },
      };
    },
  };
  const controller = createCardMenu({ mount,
    api:async (...args) => {
      calls.push(args);
      return options.api ? options.api(...args) : { ...value, ...args[1].body, priority_source:'person', version:5 };
    },
    onChange:async (result, meta) => { changes.push({ result, meta }); await options.onChange?.(result, meta); },
  });
  controller.show(value, { board, me, from:opener });
  return { controller, calls, changes, opener, doc, mount, get dialog() { return dialog; },
    async choose(priority) {
      const button = dialog.controls.find(el => el.dataset.menuPriority === priority);
      assert.ok(button, `Visible priority action ${priority} is missing`);
      assert.equal(button.disabled, false, `${priority} must be actionable`);
      await handlers.get('click')({ target:button, stopPropagation() {}, preventDefault() {} });
    },
  };
}

const cases = {
  menu() {
    const html = renderCardMenu(card, { board, me });
    assert.deepEqual([...html.matchAll(/data-menu-priority="([^"]+)"/g)].map(m => m[1]), ['urgent','high','normal','low'], 'Owner sees four priority choices');
    assert.ok(html.includes('Priority'), 'Menu names the action');
  },
  badges() {
    for (const deck of [false,true]) {
      const cards = [card, { ...card, number:22, priority_source:'person' }, { ...ranked, number:23, priority:'normal', status:'done' }];
      const html = renderBoard({ board, cards, me, deck });
      for (const source of ['default','person','coworker']) assert.ok(html.includes(`data-priority-source="${source}"`), `Every ${source} normal card is distinguished, including done`);
      for (const text of ['Unranked','Person','Coworker']) assert.ok(html.includes(text), `Visible ${text} badge`);
      assert.ok(html.includes('aria-describedby='), 'Attribution is keyboard accessible');
    }
  },
  filters() {
    const cards = [card, { ...ranked, number:22 }, { ...card, number:23, priority_source:'person' }];
    for (const deck of [false,true]) {
      const high = renderBoard({ board, cards, me, deck, priorityFilter:'high' });
      assert.ok(high.includes('data-card="22"'));
      assert.ok(!high.includes('data-card="21"'), 'High filter removes the unranked normal card');
      const unranked = renderBoard({ board, cards, me, deck, priorityFilter:'unranked' });
      assert.ok(unranked.includes('data-card="21"'));
      assert.ok(!unranked.includes('data-card="23"'), 'Unranked is separate from explicit normal');
      const normal = renderBoard({ board, cards, me, deck, priorityFilter:'normal' });
      assert.ok(normal.includes('data-card="21"') && normal.includes('data-card="23"'));
    }
  },
  details() {
    for (const deck of [false,true]) {
      const html = renderDetails(ranked, { board, me, deck });
      assert.ok(html.includes('data-priority-info'), 'Card view has priority attribution');
      assert.match(html, /Studio Coworker/);
      assert.match(html, /Blocks the invitation/);
    }
  },
  async normal() {
    for (const priority_source of ['default','coworker']) {
      const f = fixture({ ...card, priority_source }); await f.choose('normal');
      assert.deepEqual(f.calls[0], ['/api/cards/21', { method:'PATCH', body:{ version:4, priority:'normal' } }]);
      assert.equal(f.changes[0].result.priority_source, 'person');
    }
  },
  async write() {
    const f = fixture(); await f.choose('high');
    assert.deepEqual(f.calls, [['/api/cards/21', { method:'PATCH', body:{ version:4, priority:'high' } }]]);
    assert.equal(f.changes[0].result.priority, 'high');
    assert.equal(f.mount.innerHTML, ''); assert.equal(f.doc.activeElement, f.opener);
  },
  async failure() {
    for (const status of [403,409,500,0]) {
      const f = fixture(card, { api:async () => { throw Object.assign(new Error('private diagnostics'), { status }); } });
      await f.choose('urgent');
      assert.equal(f.dialog.open, true); assert.equal(f.changes.length, 0); assert.equal(card.priority, 'normal');
      assert.match(f.dialog.alert.textContent, /not changed|unchanged/i);
      assert.ok(!f.dialog.alert.textContent.includes('private diagnostics'));
      assert.ok(f.dialog.controls.some(el => el.dataset.menuPriority === 'urgent' && !el.disabled));
    }
  },
  member() {
    const value = { ...ranked, can_prioritize:false };
    const html = renderCardMenu(value, { board, me:'user:member' });
    assert.ok(!html.includes('data-menu-priority='), 'Member has no priority mutation controls');
    assert.match(html, /Priority/); assert.match(html, /Coworker/);
    const detail = renderDetails(value, { board, me:'user:member', deck:false });
    assert.ok(!detail.includes('id="card-priority"'));
    assert.ok(detail.includes('data-priority-info'));
  },
  async undo() {
    const f = fixture(); await f.choose('high');
    assert.equal(typeof f.changes[0].meta.priority.undo, 'function', 'Successful action offers Undo');
    await f.changes[0].meta.priority.undo();
    assert.deepEqual(f.calls[1], ['/api/cards/21', { method:'PATCH', body:{ version:5, priority:'normal' } }], 'Undo uses exactly the returned write version and prior level');
    assert.equal(f.calls.length, 2);
  },
  async stale() {
    let current = { ...card };
    const f = fixture(card, { api:async (_path, { body }) => {
      if (body.version !== current.version) throw Object.assign(new Error('stale'), { status:409 });
      return current = { ...current, ...body, version:current.version + 1, priority_source:'person' };
    } });
    await f.choose('high');
    current = { ...current, version:6, priority:'urgent' };
    await assert.rejects(f.changes[0].meta.priority.undo, { status:409 });
    assert.equal(current.priority, 'urgent'); assert.equal(f.calls.length, 2, 'No fresh-version retry');
  },
  async race() {
    let resolve;
    const f = fixture(card, { api:() => new Promise(done => { resolve = done; }) });
    const pending = f.choose('high');
    f.controller.close(); f.controller.show({ ...card, number:25 }, { board, me });
    resolve({ ...card, priority:'high', priority_source:'person', version:5 }); await pending;
    assert.equal(f.changes.length, 0, 'An old response cannot close or change a newer menu');
    assert.equal(f.dialog.open, true);
  },
  escaping() {
    const value = { ...ranked, events:[{ ...ranked.events[0], text:'Priority set to high by the Coworker: <img src=x onerror=bad> & reason' }] };
    const html = renderDetails(value, { board, me, deck:false });
    assert.ok(html.includes('data-priority-info'));
    assert.ok(!html.includes('<img src=x'));
    assert.ok(decode(html).includes('<img src=x'));
  },
};
for (const action of ['rename','move']) for (const priority of PRIORITIES) {
  cases[`creation_${action}_${priority}`] = () => {
    const created = { ...card, priority, priority_source:'person', events:[{ kind:'add', actor:me, text:null }] };
    const changed = { ...created, title:action === 'rename' ? 'New wording' : created.title,
      status:action === 'move' ? 'review' : created.status,
      events:[...created.events, { kind:action === 'rename' ? 'edit' : 'move', actor:'user:noah', text:null }] };
    for (const viewer of [me,'user:member']) {
      const options = { board, me:viewer, people:{ [me]:'Maya Chen' } };
      const before = priorityInfo(created, options);
      assert.ok(before.includes(`Set by ${viewer === me ? 'You' : 'Maya Chen'}.`), 'Creation actor is real, with the viewer-specific name');
      assert.equal(priorityInfo(changed, options), before, `An unrelated ${action} must retain the creation-time ${priority} setter`);
    }
  };
}
cases.creation_later_priority = () => {
  for (const kind of ['edit','move']) {
    const changed = { ...card, priority:'high', priority_source:'person', events:[
      { kind:'add', actor:me, text:null },
      { kind:'edit', actor:me, text:'Priority set to high' },
      { kind:'edit', actor:me, text:'Priority set to low' },
      { kind, actor:'user:noah', text:'Priority set to high' },
      { kind:'edit', actor:me, text:null },
      { kind:'move', actor:me, text:null },
    ] };
    assert.match(priorityInfo(changed, { board, me, people:{ 'user:noah':'Noah Li' } }), /Set by Noah Li\./, 'The latest actual priority change takes precedence over creation and unrelated events');
  }
};
cases.creation_default = () => {
  const changed = { ...card, events:[{ kind:'add', actor:me }, { kind:'edit', actor:me }, { kind:'move', actor:me }] };
  assert.equal(priorityInfo(changed, { board, me }), priorityInfo(card, { board, me }));
  assert.match(priorityInfo(changed, { board, me }), /Unranked · Normal by default/);
};
cases.creation_coworker = () => {
  const changed = { ...ranked, events:[{ kind:'add', actor:me }, ...ranked.events, { kind:'edit', actor:me }, { kind:'move', actor:me }] };
  assert.equal(priorityInfo(changed, { board, me }), priorityInfo(ranked, { board, me }));
  assert.match(priorityInfo(changed, { board, me }), /Studio Coworker\. Blocks the invitation/);
};
assert.ok(Object.hasOwn(cases, process.argv[2]), 'Unknown priority behavior');
await cases[process.argv[2]]();
console.log(`PASS priority ${process.argv[2]}`);
