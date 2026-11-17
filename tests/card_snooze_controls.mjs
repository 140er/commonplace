import assert from 'node:assert/strict';
import { test } from 'node:test';
import { renderDetails, renderCardPreview } from '../commonplace/web/static/details.js';
import { boardView } from '../commonplace/web/static/board.js';
import { snoozeChoice, snoozedText, saveCardControl, loadCardGoals } from '../commonplace/web/static/card-controls.js';

const card = {number:7, board:'work', title:'Return to this', body:'Exact words', status:'todo', events:[], version:1};
const options = {board:{slug:'work'}, me:'user:owner', canRemove:true};
test('card scheduling and goal controls are directly reachable in both drawers', () => {
  for (const deck of [true, false]) {
    const html = renderDetails(card, {...options, deck});
    for (const text of ['Snooze', 'Later today', 'Tomorrow 09:00', 'Next Monday 09:00', 'Pick a date/time', 'Goal']) assert.ok(html.includes(text), text);
    assert.ok(html.indexOf('data-card-controls') < html.indexOf('data-deck-card-controls') || !deck);
    assert.doesNotMatch(renderCardPreview(7, card, options), /data-card-controls/);
  }
});
test('snooze is a plain line on each board and drawer, with Wake now', () => {
  const snoozed = {...card, waiting:{code:'snoozed',text:'Snoozed until an older timezone'}, snoozed:{until:'2030-01-08T08:00:00Z', timezone:'Europe/Amsterdam', text:'Snoozed until 2030-01-08 09:00 Europe/Amsterdam'}};
  for (const deck of [true, false]) {
    assert.match(renderDetails(snoozed, {...options, deck}), /Snoozed until/);
    assert.match(renderDetails(snoozed, {...options, deck}), /Wake now/);
    assert.equal((renderDetails(snoozed, {...options, deck}).match(/Snoozed until/g) || []).length, 1);
    assert.doesNotMatch(renderDetails(snoozed, {...options, deck}), /older timezone/);
    assert.match(boardView({...options, deck, cards:[snoozed]}), /Snoozed until/);
  }
});
test('non-owners can read their goal and return time without mutation controls', () => {
  const html = renderDetails({...card, snoozed:{until:'2030-01-08T08:00Z'}, goals:{chain:[{id:2,title:'A literal $& goal'}]}}, {...options, canRemove:false, deck:true});
  assert.match(html, /Goal/);
  assert.match(html, /Snoozed until/);
  assert.match(html, /A literal \$&amp; goal/);
  assert.doesNotMatch(html, /data-card-snooze-choice|data-card-goal-save|data-card-wake/);
});

test('shortcuts use the person’s calendar across DST, month, year and Monday boundaries', () => {
  const zone = 'Europe/Amsterdam';
  assert.deepEqual(snoozeChoice('tomorrow', new Date('2030-03-30T08:00Z'), zone), {until:'2030-03-31T09:00', timezone:zone});
  assert.equal(snoozeChoice('tomorrow', new Date('2030-10-26T07:00Z'), zone).until, '2030-10-27T09:00');
  assert.equal(snoozeChoice('monday', new Date('2030-03-30T08:00Z'), zone).until, '2030-04-01T09:00');
  assert.equal(snoozeChoice('monday', new Date('2030-04-01T06:00Z'), zone).until, '2030-04-08T09:00');
  assert.equal(snoozeChoice('tomorrow', new Date('2030-12-31T23:30Z'), 'America/New_York').until, '2031-01-01T09:00');
  assert.equal(snoozeChoice('tomorrow', new Date('2030-01-31T20:00Z'), 'Asia/Kolkata').until, '2030-02-02T09:00');
  assert.equal(snoozeChoice('later', new Date('2030-03-31T00:30Z'), zone).until, '2030-03-31T03:30:00.000Z');
  assert.equal(snoozeChoice('later', new Date('2030-03-30T21:00Z'), zone).until, '2030-03-30T23:59');
  assert.throws(() => snoozeChoice('later', new Date('2030-03-30T22:59:30Z'), zone), /no time left today/);
  assert.throws(() => snoozeChoice('other'), /Choose a return date/);
  assert.equal(snoozedText({...card, snoozed:{until:'2030-03-31T07:00Z'}}, zone), 'Snoozed until 2030-03-31 09:00 Europe/Amsterdam');
});

test('writes reuse the existing snooze, cancel and goal routes; local input stays a wall time', async () => {
  const calls = [], api = async (...args) => { calls.push(args); return card; };
  const button = (key, value) => ({hasAttribute:name => name === key, dataset:{cardSnoozeChoice:value}});
  const root = {querySelector:() => ({value:'2030-03-31T02:30'})};
  await saveCardControl(button('data-card-snooze-save'), root, card, api);
  assert.equal(calls[0][0], '/api/card-snooze/7');
  assert.equal(calls[0][1].body.until, '2030-03-31T02:30', 'the server must refuse a missing DST hour');
  await saveCardControl(button('data-card-wake'), root, card, api);
  assert.deepEqual(calls[1], ['/api/card-snooze/7/cancel', {method:'POST',body:{}}]);
  await saveCardControl(button('data-card-goal-save'), {querySelector:() => ({value:'2'})}, card, api);
  assert.deepEqual(calls[2], ['/api/goals/card', {method:'POST',body:{task:7,goal:2}}]);
  assert.equal(calls[3][0], '/api/cards/7');
  await saveCardControl(button('data-card-goal-save'), {querySelector:() => ({value:''})}, card, api);
  assert.equal(calls[4][1].body.goal, null);
  await assert.rejects(saveCardControl(button('data-card-snooze-save'), {querySelector:() => ({value:''})}, card, api), /Choose a return date/);
  await assert.rejects(saveCardControl(button('data-card-wake'), root, card, async () => { throw new Error('this card is not snoozed'); }), /this card is not snoozed/);
});

test('goal loading retains an archived current goal, escapes text and keeps failures read-only', async () => {
  const select = {disabled:true}, save = {disabled:true}, error = {};
  const root = {querySelector:key => key.includes('error') ? error : key.includes('save') ? save : select};
  await loadCardGoals(root, {...card, goals:{chain:[{id:2,title:'Keep $& <words>'}]}}, async () => ({goals:[{id:1,title:'Active'},{id:2,title:'Keep $& <words>',archived:true},{id:3,title:'Other archive',archived:true}]}));
  assert.equal(select.value, '2');
  assert.match(select.innerHTML, /Keep \$&amp; &lt;words&gt;/);
  assert.doesNotMatch(select.innerHTML, /Other archive/);
  assert.equal(save.disabled, false);
  select.disabled = save.disabled = true;
  await loadCardGoals(root, card, async () => { throw new Error('Sign in as a workspace owner to set goals'); });
  assert.equal(select.disabled, true);
  assert.equal(save.disabled, true);
  assert.equal(error.textContent, 'Sign in as a workspace owner to set goals');
});
