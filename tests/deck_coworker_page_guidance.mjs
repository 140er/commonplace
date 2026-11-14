// Browser-free checks for the Coworker page guidance (card 526): what it does now, Send off with a
// reason, one page per bot from every entry point, plain mode explanations, a concrete model next step.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { capyRosterHTML, overviewBots, workStatus, panelsHTML, composerHTML, sendBlock, MODE_HELP, mount } from '../commonplace/web/static/deck-coworkers.js';
import { shellTrees } from '../commonplace/web/static/deck-shell.js';
import { setupText } from '../commonplace/web/static/bot-setup.js';
import { setupHTML, modelNoteHTML } from '../commonplace/web/static/bots.js';
import { escape as e } from '../commonplace/web/static/api.js';

const progress = (extra = {}) => ({ availability: 'available', board: 'product', number: 12, status: 'doing', title: 'Ship the release', status_line: 'Running the checks', ...extra });
const conversation = { id: 'room', title: 'Release', bot_ids: ['bot'], board: 'product' };

// ---- 1. what it is doing now / last did / idle, with a link to the card ----
test('a running job says it is working now, quotes the status line and links the card', () => {
  const status = workStatus({}, { conversation, jobs: [{ id: 3, conversation_id: 'room', state: 'running', task_progress: progress() }] });
  assert.deepEqual([status.headline, status.text, status.href, status.tone], ['Working now', 'Running the checks', '#/card/12', 'working']);
  const html = panelsHTML({}, { conversation, jobs: [{ id: 3, conversation_id: 'room', state: 'running', task_progress: progress() }] });
  assert.match(html, /<b>Working now<\/b> <span>Running the checks<\/span> <a href="#\/card\/12">Open the card<\/a>/);
});
test('a finished job says what it last did, preferring a running job and the newest finished one', () => {
  const jobs = [{ id: 1, conversation_id: 'room', state: 'done', task_progress: progress({ number: 5, status_line: 'Old work' }) }, { id: 2, conversation_id: 'room', state: 'failed', task_progress: progress({ number: 6, status_line: 'Newest work' }) }];
  const last = workStatus({}, { conversation, jobs });
  assert.deepEqual([last.headline, last.text, last.href], ['Last ran: failed', 'Newest work', '#/card/6']);
  assert.equal(workStatus({}, { conversation, jobs: [...jobs, { id: 0, conversation_id: 'room', state: 'pending', task: { board: 'product', number: 9 } }] }).headline, 'Queued');
  assert.equal(workStatus({}, { conversation, jobs: [{ id: 4, conversation_id: 'room', state: 'running', task: { board: 'product', number: 9 } }, ...jobs] }).href, '#/card/9');
});
test('jobs of other conversations and unavailable progress never leak into the line or invent a link', () => {
  const other = { id: 8, conversation_id: 'elsewhere', state: 'running', task: { board: 'product', number: 4 } };
  assert.equal(workStatus({}, { conversation, jobs: [other] }).headline, 'Idle');
  const hidden = workStatus({}, { conversation, jobs: [{ id: 9, conversation_id: 'room', state: 'done', task_progress: { availability: 'unavailable', title: 'Secret', number: 3 } }] });
  assert.equal(hidden.href, ''); assert.doesNotMatch(JSON.stringify(hidden), /Secret/);
});
test('without jobs the Coworker state decides: working, waiting on you, last activity or plainly idle', () => {
  assert.equal(workStatus({ state: 'working', summary: 'Reading files' }).headline, 'Working now');
  assert.equal(workStatus({ state: 'needs_you' }).headline, 'Waiting on you');
  assert.deepEqual([workStatus({ state: 'idle', summary: 'Merged the change' }).headline, workStatus({ state: 'idle', summary: 'Merged the change' }).text], ['Last activity', 'Merged the change']);
  const idle = workStatus({ state: 'idle' }, {}, {});
  assert.deepEqual([idle.headline, idle.href], ['Idle', '']); assert.match(idle.text, /Nothing is running/);
  assert.equal(workStatus({}, {}, { finished: [{ name: 'Build', state: 'done' }] }).headline, 'Last activity');
});
test('the panel always carries the status line and never the old empty sentence; copy is escaped', () => {
  for (const html of [panelsHTML({}, {}, {}), panelsHTML({ summary: '<img src=x>' }, {}, {})]) { assert.match(html, /data-cb-now/); assert.doesNotMatch(html, /No work recorded yet|<img/); }
  assert.match(panelsHTML({ state: 'working' }, { working_on: [{ name: 'Release', state: 'working' }] }), /<b>Working now<\/b>[^]*cb-tree/);
});

// ---- 2. Send is disabled, with a reason, until a conversation is open ----
test('with no conversation Send and the draft are disabled and a visible reason names the next step', () => {
  const html = composerHTML({}, { text: 'typed anyway' });
  assert.match(html, /<button type="submit" class="cb-send"[^>]* disabled>Send<\/button>/);
  assert.match(html, /<textarea[^>]* disabled>/);
  assert.match(html, /id="cb-reason" role="status">Send is off until a conversation is open\. <a href="#\/bots\/-\/coworkers\/conversations">Open a conversation<\/a>/);
  assert.match(html, /<textarea[^>]*aria-describedby="cb-reason"/); assert.match(html, /class="cb-send" aria-describedby="cb-reason" disabled/);
});
test('with a conversation Send is enabled only for a non-empty draft and no reason is shown', () => {
  const ready = { conversation };
  assert.doesNotMatch(composerHTML(ready, { text: 'hi' }), /cb-reason|class="cb-send"[^>]* disabled/);
  assert.match(composerHTML(ready, { text: '   ' }), /class="cb-send" disabled/);
  assert.equal(sendBlock(ready, 'chat'), '');
});
test('Steer without a running task is blocked with its own reason', () => {
  assert.match(sendBlock({ conversation }, 'steer'), /Steer needs a task that is running now/);
  const html = composerHTML({ conversation }, { mode: 'steer', text: 'go' });
  assert.match(html, /id="cb-reason"[^>]*>Steer needs a task/); assert.match(html, /class="cb-send"[^>]* disabled/);
});

// ---- 3. one page per bot from every entry point ----
test('the Bots list and the rail open the same page for every bot', () => {
  const personal = [{ id: 'personal', name: 'Personal bot' }], directory = { bots: [{ id: 'team', name: 'Team bot' }], conversations: [] };
  const rail = [...shellTrees({ bots: personal, directory, hash: '' }).matchAll(/class="deck-bot-row" href="([^"]+)"[^>]*>[^]*?deck-bot-name">([^<]+)</g)].map(m => [m[2], m[1]]);
  const list = [...capyRosterHTML({ bots: overviewBots(personal, directory.bots) }).matchAll(/cb-bot-row" href="([^"]+)"[^]*?<b>([^<]+)<\/b>/g)].map(m => [m[2], m[1]]);
  assert.deepEqual(rail, [['Personal bot', '#/bots/personal'], ['Team bot', '#/bots/team/coworkers']]);
  assert.deepEqual(list, rail);
});
test('the rail marks the open bot page for both registries, including its Coworkers view', () => {
  const html = (hash) => shellTrees({ bots: [{ id: 'p', name: 'P' }], directory: { bots: [{ id: 't', name: 'T' }], conversations: [] }, hash });
  assert.match(html('#/bots/p/coworkers'), /href="#\/bots\/p" aria-current="page"/);
  assert.match(html('#/bots/t/coworkers'), /href="#\/bots\/t\/coworkers" aria-current="page"/);
  assert.doesNotMatch(html('#/bots/p'), /href="#\/bots\/t\/coworkers" aria-current/);
});

// ---- 4. the three modes explained ----
test('Steer, Chat and Private each get one plain line, shown and attached to the controls', () => {
  assert.deepEqual(Object.keys(MODE_HELP), ['steer', 'chat', 'private']);
  const html = composerHTML({ conversation }, {});
  for (const [key, line] of Object.entries(MODE_HELP)) {
    assert.ok(!line.includes('\n') && line.startsWith(key[0].toUpperCase() + key.slice(1) + ':'), key);
    assert.ok(html.includes(`<li>${e(line)}</li>`), key);
  }
  assert.match(html, /title="Steer: send a follow-up[^"]*" data-cb-mode="steer"/); assert.match(html, /title="Chat: talk with[^"]*" data-cb-mode="chat"/);
});

// ---- 5. no chat model: a concrete next step ----
test('the no-model message names what to set and where, and members are told who can', () => {
  const owner = setupText('needs_configuration', true), member = setupText('needs_configuration', false);
  for (const part of ['Workspace settings', "project's settings", 'Chat answers', 'local model', 'hosted provider']) assert.ok(owner.includes(part), part);
  assert.match(member, /Ask the workspace owner to choose a chat model/);
  assert.match(setupText('key_missing', true), /Chat answers[^]*key/);
  assert.match(setupHTML({ code: 'needs_configuration', can_configure: true }, e), /Chat answers[^]*href="#\/settings"/);
  assert.match(modelNoteHTML({ model: { ready: false, code: 'needs_configuration' }, canConfigure: true }, e), /Chat answers[^]*Open settings/);
});
test('a bot row that says it has no chat model gets the same next step beneath it', () => {
  const setup = setupText('needs_configuration', true);
  const html = capyRosterHTML({ bots: [{ id: 'g', name: 'General', summary: 'No chat model is set up for this bot yet.' }, { id: 'h', name: 'Harbor', summary: 'Shipped the fix' }], setup });
  assert.equal((html.match(/class="cb-next"/g) || []).length, 1);
  assert.match(html, /class="cb-next">Bots need a chat model[^<]*Chat answers/);
  assert.doesNotMatch(capyRosterHTML({ bots: [{ id: 'g', name: 'General', summary: 'No chat model is set up for this bot yet.' }] }), /cb-next/);
});

// ---- mounted page ----
const settle = async () => { for (let i = 0; i < 30; i++) await new Promise(setImmediate); };
function page(api, route = '#/bots/bot/coworkers') {
  const body = { innerHTML: '' }, header = { hidden: false }, events = new Map();
  const root = { innerHTML: '', ownerDocument: { documentElement: { dataset: { ui: 'deck' } } }, parentElement: { querySelector: () => header }, querySelector: selector => selector === '[data-cb="body"]' ? body : null, classList: { add() {}, remove() {} }, addEventListener: (name, fn) => events.set(name, fn), removeEventListener: name => events.delete(name) };
  const ui = mount({ root, route, me: { workspace: 'w', actor: 'u' }, api: async path => (await api(path)) });
  return { ui, body };
}
test('mounted: a bot whose Coworker has no conversation shows idle, a disabled Send and the reason', async () => {
  const h = page(path => path === '/api/bots' ? { bots: [{ id: 'bot', name: 'Atlas', board: 'product' }] } : path === '/api/coworkers' ? { bots: [], conversations: [] } : { models: {} });
  await settle();
  assert.match(h.body.innerHTML, /<b>Idle<\/b>/); assert.doesNotMatch(h.body.innerHTML, /No work recorded yet/);
  assert.match(h.body.innerHTML, /class="cb-send"[^>]* disabled/); assert.match(h.body.innerHTML, /Send is off until a conversation is open/);
  h.ui.stop();
});
test('mounted: a running job is shown on the page with its card link', async () => {
  const detail = { conversation, jobs: [{ id: 3, conversation_id: 'room', state: 'running', task_progress: progress() }] };
  const h = page(path => path === '/api/bots' ? { bots: [{ id: 'bot', name: 'Atlas' }] } : path === '/api/coworkers' ? { bots: [], conversations: [{ ...conversation, state: 'working' }] } : path.endsWith('/room') ? detail : { models: {} });
  await settle();
  assert.match(h.body.innerHTML, /<b>Working now<\/b> <span>Running the checks<\/span> <a href="#\/card\/12">Open the card<\/a>/);
  h.ui.stop();
});
test('mounted: a missing chat model is explained on the page for owners and members', async () => {
  for (const [can, text, link] of [[true, /Chat answers/, /Open settings/], [false, /Ask the workspace owner/, null]]) {
    const h = page(path => path === '/api/bots' ? { bots: [{ id: 'bot', name: 'Atlas' }], model: { ready: false, code: 'needs_configuration' }, can_configure: can } : path === '/api/coworkers' ? { bots: [], conversations: [conversation] } : path.endsWith('/room') ? { conversation } : { models: {} });
    await settle();
    const setup = /class="cb-setup" role="status">([^]*?)<\/p>/.exec(h.body.innerHTML)?.[1] || '';
    assert.match(setup, text); if (link) assert.match(setup, link); else assert.doesNotMatch(setup, /Open settings/);
    h.ui.stop();
  }
});
