import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const source = name => readFileSync(new URL(`../commonplace/web/static/${name}.js`, import.meta.url), 'utf8');
const moduleURL = text => `data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
const apiURL = moduleURL(source('api'));
const load = name => import(moduleURL(source(name).replace(/(['"])\.\/api\.js\1/g, JSON.stringify(apiURL))));
const { createCoworkers, coworkerTarget, coworkerJobHTML } = await load('coworkers');
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const bot = { id: 'bot-1', name: 'Mira', instructions: 'Keep exact words', connection_id: null };
const conversation = (id, extra = {}) => ({ id, kind: 'dm', title: `Room ${id}`, member_ids: ['u-one'], bot_ids: [bot.id], board: null, ...extra });
const message = (id, room, text, extra = {}) => ({ id, conversation_id: room, actor: 'user:u-one', text, created_at: '2026-11-09T12:00:00Z', ...extra });
const job = (id, room, state = 'running', extra = {}) => ({ id, conversation_id: room, message_id: 1, bot_id: bot.id, state, code: '', ...extra });
const directory = (extra = {}) => ({ bots: [bot], conversations: [conversation('a'), conversation('b')], user_id: 'u-one', can_manage_bots: true, ...extra });
const detail = (id, extra = {}) => ({ conversation: conversation(id), messages: [], jobs: [], memories: [], ...extra });

// The host models only DOM operations used by this controller. Requests deliberately
// ignore AbortSignal so lifecycle assertions prove fencing as well as cancellation.
function fixture() {
  const listeners = new Map(), requests = [], timers = new Map();
  let user = { workspace: 'w-one', actor: 'user:u-one', people: { 'user:u-one': 'June' } }, timerId = 0;
  const doc = new EventTarget(); doc.hidden = false; doc.activeElement = null;
  const events = new EventTarget();
  let html = '', sendButton = null;
  const host = {
    get innerHTML() { return html; },
    set innerHTML(value) { html = value; const tag = /<button\b[^>]*data-cw-send[^>]*>/.exec(value)?.[0]; sendButton = tag ? { disabled: /\sdisabled(?:\s|>)/.test(tag) } : null; },
    addEventListener(kind, fn) { listeners.set(kind, fn); },
    removeEventListener(kind) { listeners.delete(kind); },
    contains() { return false; },
    querySelector(selector) { return selector === '[data-cw-send]' ? sendButton : null; },
    querySelectorAll() { return []; },
  };
  const api = (path, options = {}) => {
    let yes, no;
    const promise = new Promise((resolve, reject) => { yes = resolve; no = reject; });
    const request = { path, options, settled: false, resolve(value) { request.settled = true; yes(structuredClone(value)); }, reject(error) { request.settled = true; no(error); } };
    requests.push(request); return promise;
  };
  const time = { setTimeout(fn, ms) { const id = ++timerId; timers.set(id, { fn, ms }); return id; }, clearTimeout(id) { timers.delete(id); } };
  const navigation = [];
  const ui = createCoworkers({ mount: () => host, api, current: () => user, doc, events, timers: time, navigate: hash => navigation.push(hash) });
  const take = (path, method = 'GET') => {
    const request = requests.find(item => !item.settled && !item.taken && item.path === path && (item.options.method || 'GET') === method);
    assert.ok(request, `${method} ${path} was requested`); request.taken = true; return request;
  };
  const input = (field, value) => listeners.get('input')({ target: { dataset: { cwField: field }, value, closest: () => null } });
  const submit = (kind, entries = []) => {
    const form = { dataset: { cwForm: kind }, entries };
    listeners.get('submit')({ target: { closest: selector => selector === '[data-cw-form]' ? form : null }, preventDefault() {}, stopPropagation() {} });
  };
  const click = (action, id) => listeners.get('click')({ target: { closest: selector => selector === '[data-cw-action]' ? { dataset: { cwAction: action, id } } : null } });
  async function open(id = 'a', data = detail(id), list = directory()) {
    ui.show(id);
    take(`/api/coworkers/conversations/${id}`).resolve(data);
    await flush();
    assert.equal(sendButton, null, "Wait for bot directory before enabling conversation controls");
    take('/api/coworkers').resolve(list);
    await flush();
  }
  return { ui, host, requests, timers, doc, events, navigation, take, input, submit, click, open, setUser(value) { user = value; }, sendButton: () => sendButton };
}

const cases = {
  async escaping() {
    const f = fixture(), attack = '<img src=x onerror="alert(1)"> & <script>bad()</script>';
    f.setUser({ workspace: 'w-one', actor: 'user:u-one', people: { 'user:u-one': attack } });
    await f.open('a', detail('a', {
      conversation: conversation('a', { title: attack }),
      messages: [message(1, 'a', attack)],
      memories: [{ id: 'm-1', conversation_id: 'a', text: attack }],
      jobs: [job('j-1', 'a', 'needs_configuration', { code: attack, task: { number: 314 } })],
    }), directory({ bots: [{ ...bot, name: attack }] }));
    assert.doesNotMatch(f.host.innerHTML, /<img|<script>/);
    assert.match(f.host.innerHTML, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt; &amp;/);
    assert.match(f.host.innerHTML, /Needs configuration/);
    assert.doesNotMatch(f.host.innerHTML, />Completed</);
    assert.match(f.host.innerHTML, /href="#\/card\/314"/);
    assert.match(f.host.innerHTML, /href="#\/coworkers\/a\?message=1"/);
    assert.doesNotMatch(coworkerJobHTML(job('bad', 'a', 'failed', { task: 'javascript:alert(1)' })), /javascript:/);
    f.ui.stop();
  },

  async send() {
    const f = fixture();
    await f.open('a', detail('a', { jobs: [job('old', 'a', 'running')] }));
    f.input('text', 'A second question');
    assert.equal(f.sendButton().disabled, false, 'A running job must leave Send available');
    f.submit('message');
    const post = f.take('/api/coworkers/conversations/a/messages', 'POST');
    assert.deepEqual(Object.keys(post.options.body).sort(), ['bot_id', 'request_id', 'text']);
    assert.equal(post.options.body.bot_id, bot.id);
    assert.equal(f.sendButton().disabled, true, 'Only acknowledgement of this send disables the button');
    post.resolve({ message: message(4, 'a', post.options.body.text, { request_id: post.options.body.request_id }), job: job('next', 'a', 'pending') });
    await flush();
    f.input('text', 'I can keep talking');
    assert.equal(f.sendButton().disabled, false, 'Queued and running jobs must not lock the composer');
    f.submit('message');
    const uncertain = f.take('/api/coworkers/conversations/a/messages', 'POST');
    uncertain.reject(new Error('Connection lost'));
    await flush();
    assert.match(f.host.innerHTML, /Retry send/);
    f.input('text', 'A newer draft');
    f.submit('message');
    const retry = f.take('/api/coworkers/conversations/a/messages', 'POST');
    assert.deepEqual(retry.options.body, uncertain.options.body, 'Retry must use identical text, target and request ID');
    retry.resolve({ message: message(5, 'a', retry.options.body.text, { request_id: retry.options.body.request_id }), job: null });
    await flush();
    assert.match(f.host.innerHTML, />A newer draft<\/textarea>/, 'Acknowledgement must preserve a newer draft');
    assert.notEqual(post.options.body.request_id, retry.options.body.request_id);
    f.ui.stop();
  },

  async reconciliation() {
    const f = fixture();
    await f.open('a', detail('a', { messages: [message(1, 'a', 'FIRST_MESSAGE')] }));
    f.input('text', 'FOURTH_MESSAGE'); f.submit('message');
    const post = f.take('/api/coworkers/conversations/a/messages', 'POST');
    const pending = f.ui.refresh(); f.take('/api/coworkers').resolve(directory());
    f.take('/api/coworkers/conversations/a/messages?after=1&limit=100').resolve({
      messages: [message(2, 'a', 'SECOND_MESSAGE'), message(3, 'a', 'THIRD_MESSAGE'), message(4, 'a', 'FOURTH_MESSAGE', { request_id: post.options.body.request_id })],
      jobs: [job('new-job', 'a', 'completed')],
    });
    await flush(); f.take('/api/coworkers/conversations/a/memory').resolve({ memories: [] }); await pending;
    post.resolve({ message: message(4, 'a', 'FOURTH_MESSAGE', { request_id: post.options.body.request_id }), job: job('new-job', 'a', 'pending') });
    await flush();
    const positions = ['FIRST_MESSAGE', 'SECOND_MESSAGE', 'THIRD_MESSAGE', 'FOURTH_MESSAGE'].map(text => f.host.innerHTML.indexOf(text));
    assert.deepEqual([...positions].sort((a, b) => a - b), positions, 'Concurrent human posts must appear before the acknowledged message');
    assert.equal((f.host.innerHTML.match(/FOURTH_MESSAGE/g) || []).length, 1);
    assert.match(f.host.innerHTML, />Completed</); assert.doesNotMatch(f.host.innerHTML, />Queued</, 'A late acknowledgement must not regress a completed job');
    f.ui.stop();
  },

  async isolation() {
    const f = fixture();
    await f.open();
    f.input('text', 'PRIVATE_A_LATE_SEND'); f.submit('message');
    const post = f.take('/api/coworkers/conversations/a/messages', 'POST');
    const pending = f.ui.refresh();
    const oldDirectory = f.take('/api/coworkers');
    const read = f.take('/api/coworkers/conversations/a/messages?after=0&limit=100');
    f.ui.show('b');
    assert.ok(post.options.signal.aborted && read.options.signal.aborted && oldDirectory.options.signal.aborted, 'Navigation aborts reads and writes');
    f.take('/api/coworkers/conversations/b').resolve(detail('b', { messages: [message(20, 'b', 'Public to B')] }));
    f.take('/api/coworkers').resolve(directory());
    await flush();
    read.resolve({ messages: [message(8, 'a', 'PRIVATE_A_LATE_READ')], jobs: [] });
    post.resolve({ message: message(9, 'a', 'PRIVATE_A_LATE_SEND'), job: job('old', 'a') });
    oldDirectory.resolve(directory({ conversations: [conversation('a', { title: 'STALE_DIRECTORY' })] }));
    await pending; await flush();
    assert.match(f.host.innerHTML, /Public to B/);
    assert.doesNotMatch(f.host.innerHTML, /PRIVATE_A|STALE_DIRECTORY/);
    assert.ok(!f.requests.some(item => item.path === '/api/coworkers/conversations/a/memory'), 'An obsolete read must not fetch another page');
    f.ui.stop();
  },

  async lifecycle() {
    const f = fixture();
    await f.open();
    f.input('text', 'PRIVATE_EXPIRED_DRAFT');
    const pending = f.ui.refresh();
    const late = f.take('/api/coworkers/conversations/a/messages?after=0&limit=100');
    const list = f.take('/api/coworkers');
    f.doc.dispatchEvent(new Event('session-expired'));
    assert.equal(f.host.innerHTML, ''); assert.equal(f.timers.size, 0);
    assert.ok(late.options.signal.aborted && list.options.signal.aborted);
    late.resolve({ messages: [message(8, 'a', 'PRIVATE_EXPIRED_RESPONSE')], jobs: [] }); list.resolve(directory());
    await pending; await flush(); assert.equal(f.host.innerHTML, '');
    await f.open(); assert.doesNotMatch(f.host.innerHTML, /PRIVATE_EXPIRED/);
    f.events.dispatchEvent(new Event('pagehide'));
    assert.equal(f.host.innerHTML, ''); assert.equal(f.timers.size, 0);

    await f.open('a', detail('a', { memories: [{ id: 'secret', conversation_id: 'a', text: 'REVOKED_PRIVATE_MEMORY' }] }));
    const failed = f.ui.refresh();
    f.take('/api/coworkers').resolve(directory());
    f.take('/api/coworkers/conversations/a/messages?after=0&limit=100').reject(Object.assign(new Error('Not found'), { status: 404 }));
    await failed; await flush();
    assert.doesNotMatch(f.host.innerHTML, /REVOKED_PRIVATE_MEMORY/);
    assert.match(f.host.innerHTML, /no longer have access/);
    f.ui.stop();
  },

  async routing() {
    assert.equal(coworkerTarget('hello', '', [bot]).botId, bot.id);
    const other = { id: 'bot-2', name: 'Nia' };
    assert.equal(coworkerTarget('human discussion', '', [bot, other]).botId, null);
    assert.equal(coworkerTarget('Hi @Nia, can you help?', '', [bot, other]).botId, other.id);
    assert.ok(coworkerTarget('@Mira and @Nia', '', [bot, other]).error);
    assert.ok(coworkerTarget('@Mira', '', [bot, { id: 'duplicate', name: 'Mira' }]).error);
    assert.equal(coworkerTarget('@Mira and @Nia', bot.id, [bot, other]).botId, bot.id);
    assert.ok(coworkerTarget('hello', 'foreign-bot', [bot]).error);
    assert.equal(coworkerTarget('email@Nia.example', '', [bot, other]).botId, null);

    const { renderRail, renderPhoneTabs } = await load('rail');
    assert.match(renderRail({ view: 'coworkers' }), /href="#\/coworkers"[^>]*aria-current="page"/);
    assert.match(renderPhoneTabs({ board: { slug: 'demo' } }), /href="#\/board\/demo\/chat"/, 'Existing project chat remains in phone navigation');
    const palette = source('palette').replace(/import \{ cardId \} from '.\/board.js';/, 'const cardId = () => "";');
    const { paletteItems } = await import(moduleURL(palette.replace(/(['"])\.\/api\.js\1/g, JSON.stringify(apiURL))));
    assert.ok(paletteItems({ query: 'coworkers' }).some(item => item.href === '#/coworkers'), 'Phone project picker must find coworkers');

    const app = source('app'), slice = (start, end) => app.slice(app.indexOf(start), app.indexOf(end, app.indexOf(start)));
    let shared = 0, stops = 0; const shown = [];
    const state = { me: { workspace: 'w-one' }, screen: 0, route: '#/', base: '#/', boards: [], chatVisible: new Set() };
    const nodes = { '#content': { children: [], innerHTML: '' }, '#card-panel': { hidden: true }, '#shell-dialog': { close() {} } };
    const context = { state, root: { querySelector: selector => nodes[selector] || null }, location: { hash: '#/coworkers/a?message=4' }, history: { replaceState() {} }, localStorage: { getItem: () => null },
      remember() {}, resolveRoute: hash => hash, boardPreference: () => '', decideStart: () => undefined, clearAgentSecret() {}, voice: { stop() {} }, speech: { stop() {} }, markOpenCard() {}, showError() {}, message: value => value,
      coworkersUI: { show(...args) { shown.push(args); }, stop() { stops++; } }, adminUI: null, consoleUI: null, detailsUI: null, boardDispose: null, title() {}, navigation() {},
      async refresh() { shared++; }, api() { throw new Error('Coworker route must bypass shared prerequisites'); } };
    runInNewContext(slice('function coworkersScreen()', 'async function refresh(') + slice('async function route()', '// The current view'), context);
    await context.route();
    assert.equal(state.base, '#/coworkers/a?message=4'); assert.deepEqual(shown[0], ['a', '4']); assert.equal(shared, 0);
    context.location.hash = '#/board/demo'; await context.route(); assert.equal(stops, 1); assert.equal(shared, 1);
    context.state.route = '#/coworkers/b';
    runInNewContext(slice('async function refresh(', 'function decideStart('), context);
    await context.refresh({ background: true });
    assert.match(readFileSync(new URL('../commonplace/web/static/index.html', import.meta.url), 'utf8'), /coworkers\.css/);
  },

  async memory() {
    const f = fixture(), memory = { id: 'm-one', conversation_id: 'a', text: 'EXACT_SAVED_WORDS <safe>' };
    await f.open('a', detail('a', { memories: [memory], jobs: [job('j-one', 'a')] }));
    const refresh = f.ui.refresh();
    f.take('/api/coworkers').resolve(directory());
    const staleJobs = f.take('/api/coworkers/conversations/a/messages?after=0&limit=100');
    f.click('cancel-job', 'j-one');
    f.take('/api/coworkers/jobs/j-one/cancel', 'POST').resolve({ job: job('j-one', 'a', 'cancelled') });
    await flush();
    staleJobs.resolve({ messages: [], jobs: [job('j-one', 'a', 'running')] });
    await flush();
    const oldMemory = f.take('/api/coworkers/conversations/a/memory');
    f.click('hide-memory', 'm-one');
    const remove = f.take('/api/coworkers/conversations/a/memory/m-one', 'DELETE');
    assert.deepEqual(remove.options.body, {}, 'Memory removal must send JSON for the server write boundary');
    remove.resolve({ ok: true });
    await flush(); assert.doesNotMatch(f.host.innerHTML, /EXACT_SAVED_WORDS/);
    oldMemory.resolve({ memories: [memory] }); await refresh; await flush();
    assert.doesNotMatch(f.host.innerHTML, /EXACT_SAVED_WORDS/, 'An old read must not undo Hide');
    assert.match(f.host.innerHTML, />Cancelled</); assert.doesNotMatch(f.host.innerHTML, />Running</);
    f.input('memory', '  exact spacing\nand words  '); f.submit('memory');
    const save = f.take('/api/coworkers/conversations/a/memory', 'POST');
    assert.deepEqual(save.options.body, { text: '  exact spacing\nand words  ' });
    f.ui.show('b'); assert.ok(save.options.signal.aborted);
    f.take('/api/coworkers/conversations/b').resolve(detail('b')); f.take('/api/coworkers').resolve(directory()); await flush();
    save.resolve({ memory: { ...memory, text: 'PRIVATE_LATE_MEMORY' } }); await flush();
    assert.doesNotMatch(f.host.innerHTML, /PRIVATE_LATE_MEMORY|EXACT_SAVED_WORDS/);
    f.ui.stop();
  },

  async forms() {
    const original = globalThis.FormData;
    globalThis.FormData = class {
      constructor(form) { this.entries = form.entries; }
      [Symbol.iterator]() { return this.entries[Symbol.iterator](); }
      getAll(key) { return this.entries.filter(([name]) => name === key).map(([, value]) => value); }
    };
    try {
      const f = fixture(); await f.open();
      f.click('new-bot');
      f.submit('bot', [['name', 'Named coworker'], ['instructions', 'Exact instructions\nline two'], ['connection_id', 'conn:existing']]);
      const create = f.take('/api/coworkers/bots', 'POST');
      assert.deepEqual(create.options.body, { name: 'Named coworker', instructions: 'Exact instructions\nline two', connection_id: 'conn:existing' });
      create.resolve({ bot: { ...bot, id: 'bot-2', name: 'Named coworker' } }); await flush();
      assert.match(f.host.innerHTML, /Named coworker/);
      f.click('new-conversation');
      f.submit('conversation', [['kind', 'group'], ['title', 'Project channel'], ['member_ids', 'u-two, u-three\nu-two'], ['bot_ids', bot.id], ['bot_ids', 'bot-2'], ['board', 'demo']]);
      const group = f.take('/api/coworkers/conversations', 'POST');
      assert.deepEqual(group.options.body, { kind: 'group', title: 'Project channel', member_ids: ['u-two', 'u-three'], bot_ids: [bot.id, 'bot-2'], board: 'demo' });
      group.resolve({ conversation: conversation('new', { kind: 'group' }) }); await flush();
      assert.deepEqual(f.navigation, ['#/coworkers/new']); f.ui.stop();

      const member = fixture(); await member.open('a', detail('a'), directory({ can_manage_bots: false }));
      assert.doesNotMatch(member.host.innerHTML, /data-cw-action="new-bot"/);
      member.click('new-bot'); assert.doesNotMatch(member.host.innerHTML, /data-cw-form="bot"/);
      member.ui.stop();
    } finally { globalThis.FormData = original; }
  },
};

const selection = process.argv[2];
assert.ok(cases[selection], `Unknown coworker UI case: ${selection}`);
await cases[selection]();
console.log(`coworker UI: ${selection} passed`);
