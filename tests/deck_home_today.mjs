import assert from 'node:assert/strict';
import { test } from 'node:test';
import { escape as e } from '../commonplace/web/static/api.js';
import { todayKey, removeNeed, createNeedLedger, actionRequest, actionRow, needsCards, runHTML, displayText } from '../commonplace/web/static/deck-home-today.js';
import { modelControls } from '../commonplace/web/static/deck-home.js';

const card = { number: 7, version: 4, title: 'Review the release', actions: ['approve', 'reject'] };
test('keys respect editing, modifiers, repeats, dialogs and the single-key preference', () => {
  for (const [key, action] of [['A', 'approve'], ['h', 'hold'], ['r', 'reply'], ['j', 'next'], ['k', 'previous']]) assert.equal(todayKey({ key }), action);
  for (const event of [{ key: 'a', ctrlKey: true }, { key: 'a', metaKey: true }, { key: 'a', altKey: true }, { key: 'a', repeat: true }, { key: 'x' }, { key: 'a', defaultPrevented: true }, { key: 'a', target: { closest: () => ({}) } }]) assert.equal(todayKey(event), null);
  assert.equal(todayKey({ key: 'a' }, false), null);
});
test('one successful version removes all duplicate bucket entries and decrements once', () => {
  const needs = { count: 2, asks: [card], ready_to_merge: [card], approvals: [{ ...card, number: 8 }] };
  const next = removeNeed(needs, card);
  assert.equal(next.count, 1); assert.deepEqual(needsCards(next).map(c => c.number), [8]);
  assert.equal(removeNeed(next, card).count, 1);
  assert.equal(removeNeed(needs, { ...card, version: 3 }).count, 2);
  assert.equal(needs.asks.length, 1, 'never mutates the shell snapshot');
});
test('a stale read cannot restore a settled count, and a workspace switch clears receipts', () => {
  const ledger = createNeedLedger(), me = { actor: 'user:one', workspace: 'one' }, needs = { count: 1, approvals: [card] };
  ledger.settle(card, me);
  assert.equal(ledger.apply(needs, me).count, 0);
  assert.equal(ledger.apply({ ...needs, approvals: [{ ...card, version: 5 }] }, me).count, 1);
  assert.equal(ledger.apply(needs, { ...me, workspace: 'two' }).count, 1);
});
test('model and effort choices require the live additive contract and configured provider', () => {
  const catalog = [{ provider: 'local', model: 'small' }, { provider: 'other', model: 'large' }];
  assert.deepEqual(modelControls({ id: 'old' }, catalog), { models: [], efforts: [] });
  const offered = modelControls({ model: 'current', reasoning_effort: null }, catalog, { ready: true, label: 'local/current' });
  assert.deepEqual(offered.models, ['current', 'small']);
  assert.deepEqual(offered.efforts, ['low', 'medium', 'high', 'xhigh', 'max']);
});
test('writes use only offered actions, exact versions and a stable idempotency key', () => {
  assert.deepEqual(actionRequest(card, 'approve', { key: 'attempt-1' }), {
    path: '/api/cards/7/approve', options: { method: 'POST', headers: { 'Idempotency-Key': 'attempt-1' }, body: { version: 4, approve: true } },
  });
  const ask = { ...card, actions: ['answer'], ask_options: ['Ship it', 'Hold'] };
  assert.deepEqual(actionRequest(ask, 'approve', { key: 'k' }).options.body, { version: 4, choice: 'Ship it' });
  assert.equal(actionRequest({ ...ask, ask_options: ['Blue', 'Orange'] }, 'approve'), null);
  assert.equal(actionRequest({ ...card, actions: [] }, 'approve'), null);
  assert.equal(actionRequest({ ...card, version: 0 }, 'approve'), null);
  assert.equal(actionRequest(ask, 'reject'), null);
  assert.equal(actionRequest(ask, 'reply', { reply: 'Please check the preview', key: 'r' }).options.body.reply, 'Please check the preview');
  assert.equal(actionRequest(ask, 'reply', { reply: ' ', key: 'r' }), null);
});
test('phone row has one primary, Hold and More; Reply and Turn down are in the menu', () => {
  const html = actionRow(card, {}, e);
  assert.match(html, /class="bt-approve"[^>]*>Approve/);
  assert.match(html, /data-bt-action="hold"[^>]*>Hold/);
  assert.match(html, /aria-label="More"/);
  assert.match(html, /role="menuitem"[^>]*data-bt-action="reply"/);
  assert.match(html, /role="menuitem"[^>]*data-bt-action="reject"[^>]*>Turn down/);
  assert.doesNotMatch(actionRow({ ...card, actions: [] }, {}, e), /data-bt-action="approve"|data-bt-action="reject"/);
});
test('run cards show only the sent run and its reported steps, at most three', () => {
  assert.doesNotMatch(runHTML({ text: 'Check this', state: 'running', steps: [] }, e), /bt-step/);
  const html = runHTML({ text: '<Check>', state: 'running', steps: [{ label: 'Read the change', state: 'done' }, { label: 'Check the preview', state: 'running' }] }, e);
  assert.match(html, /&lt;Check&gt;/); assert.match(html, /data-state="done"/); assert.match(html, /data-state="running"/);
  assert.equal(displayText('PR #123 Review the release ~/private/file'), 'Review the release');
});
