// Card 551: in every state the Coworker page says plainly what the bot did last and what it is doing
// now, or why that cannot be known. Each case mounts the page; none may show a bare "No summary recorded".
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount } from '../commonplace/web/static/deck-coworkers.js';

const settle = async () => { for (let i = 0; i < 30; i++) await new Promise(setImmediate); };
const room = { id: 'room', title: 'Release', bot_ids: ['reg'], board: 'product' };
const progress = (extra = {}) => ({ availability: 'available', board: 'product', number: 12, status: 'doing', title: 'Ship the release', status_line: 'Running the checks', ...extra });
function page(api, route = '#/bots/bot/coworkers') {
  const body = { innerHTML: '' }, header = { hidden: false };
  const root = { innerHTML: '', ownerDocument: { documentElement: { dataset: { ui: 'deck' } } }, parentElement: { querySelector: () => header }, querySelector: selector => selector === '[data-cb="body"]' ? body : null, classList: { add() {}, remove() {} }, addEventListener() {}, removeEventListener() {} };
  const ui = mount({ root, route, me: { workspace: 'w', actor: 'u' }, api: async path => (await api(path)) });
  return { ui, body };
}
const now = html => /<p class="cb-now"[^>]*>([^]*?)<\/p>/.exec(html)?.[1] || '';
const text = html => now(html).replace(/<[^>]+>/g, '').replace(/&#39;/g, "'").replace(/\s+/g, ' ').trim();
const rowText = html => (/<button type="button" class="cb-worker"[^>]*>[^]*?<small>([^<]*)<\/small>/.exec(html) || [])[1] || '';
// A bot with no conversation: the page falls back to the project's main Coworker.
async function projectBot(bot, extra = {}) {
  const h = page(path => path === '/api/bots' ? { bots: [{ id: 'bot', name: 'Atlas', board: 'product', ...bot }], ...extra } : path === '/api/coworkers' ? { bots: [], conversations: [] } : { models: {} });
  await settle(); const html = h.body.innerHTML; h.ui.stop(); return html;
}
async function conversationBot(bot, detail, mid = path => path.endsWith('/room')) {
  const h = page(path => path === '/api/bots' ? { bots: [{ id: 'bot', name: 'Atlas', board: 'product', ...bot }] } : path === '/api/coworkers' ? { bots: [], conversations: [room] } : mid(path) ? detail(path) : { models: {} });
  await settle(); const html = h.body.innerHTML; h.ui.stop(); return html;
}
const never = html => assert.doesNotMatch(html, /No summary recorded|No work recorded yet/);

test('owner, bot waiting on you: says how many decisions wait and links Today', async () => {
  const html = await projectBot({ state: 'needs_you', waiting: 2 });
  assert.match(text(html), /^Waiting on you 2 decisions are waiting for your answer\. Open Today$/);
  assert.match(now(html), /<a href="#\/today">Open Today<\/a>/); assert.match(html, /data-tone="waiting"/); never(html);
  assert.match(rowText(html), /Waiting on you · 2 decisions are waiting for your answer\./);
});
test('owner, bot working on a card it filed: says so and links the project board', async () => {
  const html = await projectBot({ state: 'working' });
  assert.match(text(html), /^Working now A card it filed is in progress, or it is answering a message\. Open the board$/);
  assert.match(now(html), /<a href="#\/board\/product">Open the board<\/a>/); never(html);
});
test('owner, bot idle with a last reply: says what it replied and when', async () => {
  const html = await projectBot({ state: 'idle', last: { role: 'assistant', text: 'Merged the change', at: new Date(Date.now() - 3 * 3600e3).toISOString() } });
  assert.match(text(html), /^Last activity It replied 3h ago: Merged the change$/); never(html);
  assert.match(rowText(html), /Last activity · It replied 3h ago: Merged the change/);
});
test('owner, last message is the owner\'s own with no reply: says it was theirs', async () => {
  const html = await projectBot({ state: 'idle', last: { role: 'user', text: 'Please check the build', at: new Date().toISOString() } });
  assert.match(text(html), /^Last activity You wrote just now: Please check the build$/); never(html);
});
test('owner, bot never ran and has only owner-held cards: plainly nothing recorded, board one click away', async () => {
  const html = await projectBot({ state: 'idle', last: null });
  assert.match(text(html), /^Idle No run recorded for this bot yet\. Open the board$/); never(html);
  assert.match(rowText(html), /^Idle$/); // the reason is said once, in the Working on box
});
test('member of a shared bot, nothing of theirs recorded: says why the owner\'s work is not shown', async () => {
  const html = await projectBot({ state: 'idle', last: null, mine: false, perms: { talk: true, see_shared: false } });
  assert.match(text(html), /Nothing of yours is recorded with this bot yet\. Its work in the owner's other conversations is not shared with you\./); never(html);
  const seen = await projectBot({ state: 'idle', last: null, mine: false, perms: { talk: true, see_shared: true } });
  assert.match(text(seen), /Nothing of yours is recorded with this bot yet\.(?! Its work)/);
});
test('a disabled bot or one whose owner left says so instead of idling', async () => {
  assert.match(text(await projectBot({ state: 'idle', frozen: true, frozen_reason: 'disabled' })), /^Disabled It is switched off and does not run\./);
  assert.match(text(await projectBot({ state: 'idle', frozen: true, frozen_reason: 'owner_left' })), /^Needs a new owner Its owner left/);
});
test('a bot known only to the Coworker registry (no roster row) says nothing is recorded, not nothing', async () => {
  const h = page(path => path === '/api/bots' ? { bots: [] } : path === '/api/coworkers' ? { bots: [{ id: 'bot', name: 'Atlas' }], conversations: [{ ...room, bot_ids: ['bot'], board: null }] } : path.endsWith('/room') ? { conversation: { ...room, bot_ids: ['bot'], board: null }, jobs: [] } : { models: {} });
  await settle(); const html = h.body.innerHTML; h.ui.stop();
  assert.match(text(html), /^Idle No run recorded for this bot yet\.$/); never(html);
});

test('conversation row: the list reads the bot\'s state, not a hard-coded idle with no words', async () => {
  const html = await conversationBot({ state: 'needs_you', waiting: 1 }, () => ({ conversation: room, jobs: [] }));
  assert.match(html, /cb-dot" data-tone="waiting"/); never(html);
  assert.match(rowText(html), /Waiting on you · 1 decision is waiting for your answer\./);
  assert.match(text(html), /^Waiting on you 1 decision is waiting/);
});
test('conversation with a finished run: waiting on you wins over the old run, otherwise the run is reported', async () => {
  const jobs = [{ id: 2, conversation_id: 'room', state: 'done', updated_at: new Date(Date.now() - 2 * 3600e3).toISOString(), task_progress: progress({ status_line: 'Shipped it' }) }];
  const quiet = await conversationBot({ state: 'idle' }, () => ({ conversation: room, jobs }));
  assert.match(text(quiet), /^Last ran: finished · 2h ago Shipped it Open the card$/); never(quiet);
  const waiting = await conversationBot({ state: 'needs_you', waiting: 1 }, () => ({ conversation: room, jobs }));
  assert.match(text(waiting), /^Waiting on you /);
});
test('a finished run without a summary says so about the run; hidden progress says it is not shared', async () => {
  const bare = await conversationBot({}, () => ({ conversation: room, jobs: [{ id: 2, conversation_id: 'room', state: 'done' }] }));
  assert.match(text(bare), /^Last ran: finished No summary was recorded for that run\.$/);
  const hidden = await conversationBot({}, () => ({ conversation: room, jobs: [{ id: 2, conversation_id: 'room', state: 'done', task_progress: { availability: 'unavailable', title: 'Secret', number: 3 } }] }));
  assert.match(text(hidden), /The card it worked on is not shared with you\./); assert.doesNotMatch(hidden, /Secret|#\/card\/3/);
});
test('while the conversation loads the page says loading, and when it cannot load says not available, never idle', async () => {
  let release; const gate = new Promise(resolve => { release = resolve; });
  const h = page(async path => path === '/api/bots' ? { bots: [{ id: 'bot', name: 'Atlas', board: 'product' }] } : path === '/api/coworkers' ? { bots: [], conversations: [room] } : path.endsWith('/room') ? (await gate, { conversation: room, jobs: [] }) : { models: {} });
  await settle();
  assert.match(text(h.body.innerHTML), /^Loading Reading what this bot did last/); assert.doesNotMatch(h.body.innerHTML, /<b>Idle<\/b>/);
  release(); await settle();
  assert.match(text(h.body.innerHTML), /^Idle No run recorded/); h.ui.stop();
  const failed = page(path => { if (path === '/api/bots') return { bots: [{ id: 'bot', name: 'Atlas', board: 'product' }] }; if (path === '/api/coworkers') return { bots: [], conversations: [room] }; if (path.endsWith('/room')) throw Object.assign(new Error('boom'), { status: 500 }); return { models: {} }; });
  await settle();
  assert.match(text(failed.body.innerHTML), /^Not available This bot's work could not be read just now\./); failed.ui.stop();
});
test('a running job still wins over everything and links its card (no regression of the status line)', async () => {
  const html = await conversationBot({ state: 'needs_you', waiting: 3 }, () => ({ conversation: room, jobs: [{ id: 3, conversation_id: 'room', state: 'running', task_progress: progress() }] }));
  assert.match(now(html), /<b>Working now<\/b> <span>Running the checks<\/span> <a href="#\/card\/12">Open the card<\/a>/);
});
