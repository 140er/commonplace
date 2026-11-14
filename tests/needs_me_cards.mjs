import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { escape as e } from '../commonplace/web/static/api.js';
import { needsMeCount, setNeedsQueue, BUCKETS, previewText, tidyGaps } from '../commonplace/web/static/needs-me.js';
import { removeNeed, displayText, createNeedLedger } from '../commonplace/web/static/deck-home-today.js';
import { cardHTML, summaryLine } from '../commonplace/web/static/deck-today-capy.js';
import { waitingHint } from '../commonplace/web/static/deck-home.js';
import { boardView } from '../commonplace/web/static/board.js';
import { boardCopy } from '../commonplace/web/static/deck-board.js';
import { renderDeckTabs, renderDeckBar } from '../commonplace/web/static/deck.js';
import { needItems } from '../commonplace/web/static/home.js';

const state = { busy: new Set(), errors: new Map(), held: new Set(), exiting: new Set() };
const long = 'Commonplace is public software, so every release must prove that a brand-new user on a brand-new workspace can do the core journey without help. '.repeat(6) + 'See PR #77 and #78 for the evidence in ~/private/run.json.';
const approval = { number: 7, version: 4, title: 'Release gate (PR #7)', body: long, actions: ['approve', 'reject'], board: 'product' };
const question = { number: 8, version: 2, title: 'Which machine?', body: 'Pick one.', ask: 'Which machine?', actions: ['answer'], ask_options: ['Blue', 'Orange'], board: 'product' };
const review = { number: 9, version: 1, title: 'Look at this', actions: [], board: 'product' };
// Eight entries over seven buckets, one card listed twice: the queue holds seven distinct cards.
const queue = () => ({ approvals: [approval], asks: [question, { ...question, number: 10 }], plans: [review, { ...review, number: 11 }], stale: [{ ...review, number: 12 }], done_review: [{ ...review, number: 13 }], ready_to_merge: [question], backlog_from_agents: [], count: 99 });
const visibleText = html => html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');

test('the primary action is visible on the card without opening Reply', () => {
  const approve = cardHTML(approval, [], state);
  assert.match(approve, /class="bt-approve bt-primary"[^>]*data-bt-action="approve"[^>]*>Approve</);
  assert.match(approve, /data-bt-action="hold"[^>]*>Hold</);
  assert.match(approve, /class="bt-desktop-reply"[^>]*>Reply</);
  const answer = cardHTML(question, [], state);
  assert.match(answer, /class="bt-approve bt-primary"[^>]*data-bt-action="reply"[^>]*>Answer</);
  assert.doesNotMatch(answer, />Approve</);
  assert.match(cardHTML(review, [], state), /class="bt-approve bt-primary"[^>]*>Open</);
});

test('the preview is one or two short lines and ends cleanly', () => {
  const html = cardHTML(approval, [], state);
  const preview = html.match(/<p class="bt-body bt-preview">([^<]*)<\/p>/)[1];
  assert.ok(preview.length <= 141, `preview has ${preview.length} characters`);
  assert.ok(preview.endsWith('…'));
  assert.ok(!/\s…$/.test(preview) && !/#77|~\/private/.test(preview));
  assert.equal(previewText('Short words.'), 'Short words.');
  assert.equal(previewText('word '.repeat(100), 20).length <= 21, true);
});

test('Show full text keeps the exact stored words, and a short card has nothing to expand', () => {
  const html = cardHTML(approval, [], state);
  const full = html.match(/<details class="bt-full" data-bt-full="7"><summary>Show full text<\/summary><p class="bt-full-text">([^]*?)<\/p><\/details>/);
  assert.ok(full, 'a long body offers the expand control');
  assert.equal(full[1], e(approval.body), 'the escaped stored text, byte for byte, with references and paths still in it');
  assert.ok(full[1].includes('#77') && full[1].includes('~/private/run.json'));
  assert.match(cardHTML(approval, [], { ...state, open: new Set([7]) }), /<details class="bt-full" data-bt-full="7" open>/);
  assert.doesNotMatch(cardHTML({ ...approval, body: 'Short and plain.' }, [], state), /bt-full/);
  assert.doesNotMatch(cardHTML({ ...approval, body: undefined }, [], state), /bt-full|bt-preview/);
});

test('a removed reference leaves no blank gap behind it', () => {
  for (const [raw, shown] of [
    ['Fix login (PR #12) now', 'Fix login now'],
    ['Done in PR #5.', 'Done.'],
    ['Ship it, #9, then rest', 'Ship it, then rest'],
    ['Needs #1, #2 and #3 first', 'Needs first'],
    ['Result: #12', 'Result'],
    ['Check ~/private/file.json please', 'Check please'],
    ['— a leading dash stays', '— a leading dash stays'],
  ]) { assert.equal(displayText(raw), shown, raw); assert.equal(boardCopy(raw), shown, raw); }
  assert.equal(tidyGaps('plain'), 'plain');
  assert.doesNotMatch(visibleText(cardHTML({ ...approval, title: 'Gate (PR #7) run' }, [], state)), /\(\s*\)|\s,|\s{2}/);
});

test('Today, Home, the badges and the Board report the same count for the same queue', () => {
  const needs = queue();
  const expected = 7;
  assert.equal(needsMeCount(needs), expected, 'a stale count field never wins over the listed cards');
  assert.match(summaryLine(needs, new Date('2026-11-14T10:00:00Z')), new RegExp(`· ${expected} waiting on you$`));
  assert.match(waitingHint(needs), new RegExp(`>${expected} waiting on you in Today<`));
  const shown = { needs: needsMeCount(needs), bots: [{ id: 'a', name: 'A' }] };
  assert.match(renderDeckTabs(shown), new RegExp(`class="count need">${expected}`));
  assert.match(renderDeckBar(shown), new RegExp(`class="count need">${expected}`));
  assert.equal(needItems(needs).length, expected, 'the classic queue lists the same cards');
  // The Board lists every card of one project: its Needs you column is the queue, not each card's own flag.
  setNeedsQueue(needs);
  try {
    const numbers = [7, 8, 9, 10, 11, 12, 13];
    const cards = numbers.map(number => ({ number, version: 1, board: 'product', title: `Card ${number}`, status: 'review', need: number === 13 ? 'idle' : 'you', events: [] }))
      .concat([{ number: 20, version: 1, board: 'product', title: 'Flagged but not queued', status: 'todo', need: 'you', events: [] }]);
    const html = boardView({ board: { slug: 'product', name: 'Product' }, me: 'user:owner', bots: [], deck: true, cards });
    const column = html.match(/id="col-todo"[\s\S]*?<span class="n">(\d+)<\/span>/)[1];
    assert.equal(Number(column), expected, 'the Needs you column counts the queue');
    assert.doesNotMatch(html.split('id="col-doing"')[0], /data-card="20"/, 'a card the queue does not list is not "Needs you"');
    assert.doesNotMatch(html, /data-needs-note/, 'one project holding the whole queue needs no explanation');
    const other = boardView({ board: { slug: 'other', name: 'Other' }, me: 'user:owner', bots: [], deck: true, cards: cards.slice(0, 2) });
    assert.match(other, /data-needs-note>7 cards are waiting on you across all projects; 2 are in this one\./);
  } finally { setNeedsQueue(null); }
});

test('settling a card lowers every count together, and without a queue the board keeps the card flag', () => {
  const needs = queue(), next = removeNeed(needs, approval);
  assert.equal(needsMeCount(next), 6);
  assert.equal(next.count, 6);
  assert.equal(needsMeCount({ count: 3 }), 3, 'a member with no queue lists keeps the plain number');
  assert.equal(needsMeCount(undefined), 0);
  const html = boardView({ board: { slug: 'product', name: 'Product' }, me: 'user:owner', bots: [], deck: true, cards: [{ number: 1, version: 1, board: 'product', title: 'Flagged', status: 'todo', need: 'you', events: [] }] });
  assert.match(html.split('id="col-doing"')[0], /data-card="1"/);
});

test('every needs-me bucket list in the shell is the same set', () => {
  const needs = Object.fromEntries(BUCKETS.map((bucket, i) => [bucket, [{ number: i + 1, version: 1, need: 'you', title: bucket }]]));
  assert.equal(needsMeCount(needs), BUCKETS.length);
  assert.equal(needItems(needs).length, BUCKETS.length, 'home.js knows every bucket');
  const source = readFileSync(new URL('../commonplace/web/static/needs.js', import.meta.url), 'utf8');
  for (const bucket of BUCKETS) assert.ok(source.includes(`"${bucket}"`), `needs.js lists ${bucket}`);
});

test('the shell ledger derives the shell count from the listed cards and tells the Board', () => {
  const me = { actor: 'user:one', workspace: 'one' }, ledger = createNeedLedger();
  const next = ledger.apply(queue(), me);
  assert.equal(next.count, 7, 'the stale count field of 99 is replaced');
  assert.equal(ledger.apply({ count: 4 }, me).count, 4, 'a member without lists keeps the plain number');
  ledger.apply(queue(), me);
  const html = boardView({ board: { slug: 'product', name: 'Product' }, me: 'user:owner', bots: [], deck: true, cards: [{ number: 7, version: 1, board: 'product', title: 'Queued', status: 'review', need: 'idle', events: [] }, { number: 99, version: 1, board: 'product', title: 'Flagged', status: 'todo', need: 'you', events: [] }] });
  assert.match(html.split('id="col-doing"')[0], /data-card="7"/);
  assert.doesNotMatch(html.split('id="col-doing"')[0], /data-card="99"/);
  setNeedsQueue(null);
  ledger.apply({ count: 1 }, me);
});

test('no screen keeps a count of its own', () => {
  for (const file of ['deck.js', 'deck-home.js', 'deck-today-capy.js', 'deck-board.js']) {
    const source = readFileSync(new URL(`../commonplace/web/static/${file}`, import.meta.url), 'utf8');
    assert.doesNotMatch(source, /needs\??\.count|state\.needs\.count|\.needs\?\.count/, `${file} reads a separate needs count`);
  }
});
