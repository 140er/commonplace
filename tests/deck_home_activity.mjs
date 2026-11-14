import assert from 'node:assert/strict';
import { test } from 'node:test';
import { escape as e } from '../commonplace/web/static/api.js';
import { activityEvent, groupActivity, activityHTML } from '../commonplace/web/static/deck-home-activity.js';

const bot = { id: 'one', board: 'work', name: 'work' }, at = '2026-11-14T10:00:00Z';
const request = (id, extra = {}) => ({ id, at, text: `work asked: Request ${id}`, title: `Request ${id}`, askBot: bot, ...extra });
test('one bot request burst becomes one expandable row with every original request', () => {
  const source = [request(3), request(2), request(1)], rows = groupActivity(source);
  assert.equal(rows.length, 1); assert.equal(rows[0].text, 'work asked 3 things');
  assert.deepEqual(rows[0].items, source); assert.equal(source[0].items, undefined);
  const html = activityHTML(rows[0], e);
  assert.match(html, /<details data-bt-group=/); assert.doesNotMatch(html, / open>/);
  for (const id of [1, 2, 3]) assert.match(html, new RegExp(`Request ${id}`));
  assert.match(activityHTML(rows[0], e, true), / open>/);
});
test('other bot identities, decisions and separate bursts remain individual rows', () => {
  const source = [request(1), request(2, { askBot: { ...bot, id: 'other' } }), request(3, { askBot: null, text: 'You approved a request' }), request(4, { at: '2026-11-14T10:00:06Z' })];
  assert.deepEqual(groupActivity(source), source);
  assert.equal(groupActivity([request(1), request(2, { at: 'invalid' })]).length, 2);
  const rows = groupActivity([request(1), request(2, { at: '2026-11-14T10:00:05Z' }), request(3, { at: '2026-11-14T10:00:10Z' })]);
  assert.equal(rows.length, 2, 'a chain cannot extend the initial five-second burst');
});
test('group identity survives a new request, and display copy remains escaped', () => {
  const first = groupActivity([request(2), request(1)])[0];
  assert.equal(groupActivity([request(3), request(2), request(1)])[0].id, first.id);
  const row = groupActivity([request(1, { title: '<img src=x>', askBot: { ...bot, name: '<Bot>' } }), request(2)])[0];
  const html = activityHTML(row, e);
  assert.match(html, /&lt;img src=x&gt;/); assert.match(html, /&lt;Bot&gt; asked 2 things/); assert.doesNotMatch(html, /<img/);
});
test('only an authorized roster match and actual request event enable bot grouping', () => {
  const event = { seq: 1, at, kind: 'card.created', data: { board: 'work', number: 7, title: 'Check release' } };
  assert.equal(activityEvent(event, [bot], {}).askBot, null);
  assert.equal(activityEvent(event, [], { plans: [{ number: 7 }] }).askBot, null);
  const row = activityEvent(event, [bot], { plans: [{ number: 7 }] });
  assert.deepEqual(row.askBot, { id: bot.id, name: bot.name }); assert.equal(row.text, 'work asked: Check release');
  assert.equal(activityEvent({ ...event, kind: 'card.updated' }, [bot], { plans: [{ number: 7 }] }).askBot, null);
  assert.deepEqual(activityEvent({ ...event, kind: 'card.review_needed' }, [bot], {}).askBot, row.askBot);
});

test('creation and review events for one request count only once', () => {
  const rows = groupActivity([request(3, { request: 7 }), request(2, { request: 8 }), request(1, { request: 7 })]);
  assert.equal(rows[0].text, 'work asked 2 things');
  assert.deepEqual(rows[0].items.map(item => item.request), [7, 8]);
  assert.equal(rows[0].id, groupActivity([request(2, { request: 8 }), request(1, { request: 7 })])[0].id);
  assert.equal(groupActivity([request(2, { request: 7 }), request(1, { request: 7 })])[0].items, undefined);
});
