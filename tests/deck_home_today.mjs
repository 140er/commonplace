import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { escape as e } from '../commonplace/web/static/api.js';
import { placeMenu, todayKey, removeNeed, createNeedLedger, actionRequest, canApprove, actionRow, needsCards, runHTML, displayText, runUpdate, retryIntent, quietSelector } from '../commonplace/web/static/deck-home-today.js';
import { cardHTML, activityText } from '../commonplace/web/static/deck-today-capy.js';
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
  assert.match(html, /class="bt-approve bt-primary"[^>]*>Approve/);
  assert.match(html, /data-bt-action="hold"[^>]*>Hold/);
  assert.match(html, /aria-label="More"/);
  assert.match(html, /role="menuitem"[^>]*data-bt-action="reply"/);
  assert.match(html, /role="menuitem"[^>]*data-bt-action="reject"[^>]*>Turn down/);
  assert.doesNotMatch(actionRow({ ...card, actions: [] }, {}, e), /data-bt-action="approve"|data-bt-action="reject"/);
});
test('Approve visibility follows the offered action and choice without creating a request', () => {
  for (const [fields, visible] of [
    [{ actions: ['approve'] }, true],
    [{ actions: ['reject'] }, false],
    [{ actions: ['accept'], ask_options: [] }, true],
    [{ actions: ['accept'], ask_options: ['Accept', 'Hold'] }, true],
    [{ actions: ['accept'], ask_options: ['Blue', 'Orange'] }, false],
    [{ actions: ['answer'], ask_options: ['Ship it', 'Hold'] }, true],
    [{ actions: ['answer'], ask_options: ['Blue', 'Orange'] }, false],
    [{ actions: ['answer'], ask_options: [] }, false],
    [{ actions: [] }, false], [{ version: 0 }, false], [{ number: 0 }, false],
  ]) {
    const item = { ...card, ...fields };
    assert.equal(canApprove(item), visible);
    assert.equal(actionRow(item, {}, e).includes('data-bt-action="approve"'), visible);
  }
  const row = actionRow({ ...card, actions: [] }, {}, e);
  assert.match(row, /bt-desktop-reply/);
  assert.match(row, /role="menuitem"[^>]*data-bt-action="reply"/);
  assert.doesNotMatch(row, /Open card/);
});
test('run cards show only the sent run and its reported steps, at most three', () => {
  assert.doesNotMatch(runHTML({ text: 'Check this', state: 'running', steps: [] }, e), /bt-step/);
  const html = runHTML({ text: '<Check>', state: 'running', steps: [{ label: 'Read the change', state: 'done' }, { label: 'Check the preview', state: 'running' }] }, e);
  assert.match(html, /&lt;Check&gt;/); assert.match(html, /data-state="done"/); assert.match(html, /data-state="running"/);
  assert.equal(displayText('PR #123 Review the release ~/private/file'), 'Review the release');
});

test('failed run keeps the hub reason and settings link, and still exposes failed state with steps', () => {
  const run = { state: 'streaming', text: 'Check release', canConfigure: true, intent: {}, requestId: 'turn-1', steps: [{ label: 'Read release', state: 'done' }] };
  runUpdate(run, { state: 'failed:needs_configuration' }, { code: 'needs_configuration', text: 'No chat model is set up for this bot yet.' });
  const html = runHTML(run, e);
  assert.match(html, /bh-run-failed/); assert.match(html, /Could not complete/);
  assert.match(html, /No chat model is set up for this bot yet/); assert.match(html, /href="#\/settings">Choose one/);
  assert.match(html, /Try again/); assert.match(html, /Read release/);
  assert.doesNotMatch(runHTML({ ...run, canConfigure: false }, e), /Choose one/);
  assert.doesNotMatch(runHTML({ text: 'Check', state: 'running' }, e), /Try again|Choose one/);
});
test('retry preserves original recipient and model intent while retaining uncertain receipt identity', () => {
  const intent = { bot: { id: 'original' }, conversation: 'one', body: { text: 'Original', model: 'offered', reasoning_effort: 'high', bot_id: 'worker' } };
  const run = { intent, requestId: 'original-receipt', acked: false };
  assert.deepEqual(retryIntent(run), { ...intent, requestId: run.requestId });
  const retry = retryIntent({ ...run, acked: true });
  assert.notEqual(retry.requestId, run.requestId); assert.deepEqual(retry.body, intent.body); assert.equal(retry.conversation, 'one');
  retry.body.text = 'Changed'; assert.equal(intent.body.text, 'Original');
});
test('Today omits queue boilerplate and preserves an explicit bot fact', () => {
  const state = { busy: new Set(), errors: new Map(), held: new Set(), exiting: new Set() };
  const item = { ...card, why: 'Awaiting plan review. Approve or reject it.' };
  assert.doesNotMatch(cardHTML(item, [], state), /Awaiting plan review|bt-fact/);
  assert.match(cardHTML({ ...item, fact: 'Auto-deploy is off, so it waits for you' }, [], state), /Auto-deploy is off/);
});
test('Activity uses sentences and live bot names rather than event labels', () => {
  const event = { kind: 'card.created', data: { board: 'work', number: 7, title: 'Agree the action list' } }, bots = [{ board: 'work', name: 'work' }];
  assert.equal(activityText(event, bots, { plans: [card] }), 'work asked: Agree the action list');
  assert.equal(activityText({ ...event, kind: 'card.updated' }, bots), 'work updated Agree the action list');
  assert.equal(activityText({ kind: 'unrecognized', data: {} }, bots), '');
});
test('quiet selector keeps native keyboard behavior and an adjacent chevron', () => {
  const html = quietSelector('bot', 'Bot', 'bh-bot');
  assert.match(html, /class="bh-choice-text"/); assert.match(html, /viewBox="0 0 12 12"/);
  assert.match(html, /<select name="bot" aria-label="Bot">/);
});

const menu = (rect, parentTop = 400) => ({ style: {}, parentElement: { getBoundingClientRect: () => ({ top: parentTop }) },
  getBoundingClientRect() { const shift = Number.parseFloat(this.style.translate) || 0, flipped = this.style.bottom ? -(rect.bottom - rect.top + 50) : 0; return { left: rect.left + shift, right: rect.right + shift, top: rect.top + flipped, bottom: rect.bottom + flipped, height: rect.bottom - rect.top }; } });
test('a card menu is moved inside the viewport: right overhang, left overhang and a tab-bar collision', () => {
  const view = { innerWidth: 390, innerHeight: 844 };
  const left = menu({ left: -120, right: 100, top: 500, bottom: 600 });
  placeMenu(left, view);
  assert.equal(left.style.translate, '128px 0');
  const right = menu({ left: 250, right: 470, top: 500, bottom: 600 });
  placeMenu(right, view);
  assert.equal(right.style.translate, '-88px 0');
  const fine = menu({ left: 100, right: 320, top: 300, bottom: 400 });
  placeMenu(fine, view);
  assert.equal(fine.style.translate, '');
  const low = menu({ left: 100, right: 320, top: 780, bottom: 880 }, 740);
  placeMenu(low, view);
  assert.equal(low.style.bottom, '42px');
  const cramped = menu({ left: 100, right: 320, top: 780, bottom: 880 }, 60);
  placeMenu(cramped, view);
  assert.equal(cramped.style.bottom, '', 'no flip when there is no room above');
});
test('phone: Reply is an inline button and the menu only keeps what has no button', () => {
  const css = readFileSync(new URL('../commonplace/web/static/deck-home-today.css', import.meta.url), 'utf8');
  const phone = css.slice(css.indexOf('@media (max-width: 699px)'));
  assert.doesNotMatch(phone, /\.bt-desktop-reply\) \{ display: none/);
  assert.match(phone, /\.bt-more:not\(:has\(\[data-bt-action="reject"\]\)\) \{ display: none/);
  assert.match(phone, /\.bt-more \[role="menuitem"\]\[data-bt-action="reply"\] \{ display: none/);
});
