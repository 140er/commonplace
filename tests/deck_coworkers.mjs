import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { botGroups, overviewBots, capyRosterHTML, panelsHTML, workingSteps, ownedPaths, liveLog, renderLiveLog, logLine, workerModel, stateView, composerRequest, composerHTML, mount } from '../commonplace/web/static/deck-coworkers.js';

const conversation = { id: 'room', title: 'Release', bot_ids: ['bot'], board: 'product' };
const job = { id: 7, conversation_id: 'room', task_progress: { availability: 'available', controls: { can_follow_up: true } } };
const detail = () => ({ conversation, jobs: [job] });

test('overview groups one divided list per section, counts real coworkers and marks only private bots', () => {
  const bots = [{ id: 'a', name: 'Atlas', section: 'Workspace', state: 'working', unread: 2, private: true }, { id: 'b', name: 'Harbor', section: { name: 'Workspace' }, private: false }, { id: 'c', name: 'Archive', section: 'Research', coworker_count: 3 }];
  assert.deepEqual(botGroups(bots).map(group => [group.label, group.bots.length]), [['Workspace', 2], ['Research', 1]]);
  const html = capyRosterHTML({ bots, conversations: [{ bot_ids: ['a'] }] });
  assert.equal((html.match(/class="cb-folder"/g) || []).length, 2);
  assert.equal((html.match(/aria-label="Private bot"/g) || []).length, 1);
  assert.match(html, /1 coworker</); assert.match(html, /3 coworkers</); assert.match(html, /2<span class="sr"> unread/);
  assert.match(html, /href="#\/bots\/a"/); assert.doesNotMatch(html, /href="#\/bots\/a\/coworkers"/);
  assert.doesNotMatch(capyRosterHTML({ bots: [{ id: 'x', name: 'X' }] }), /Private bot|cb-unread/);
  assert.match(capyRosterHTML({ bots: [{ id: 'p', name: 'Project', board: 'p' }] }), /1 coworker</);
});
test('absent owns and log never produce simulated rows or panels', () => {
  assert.equal(ownedPaths(), null); assert.equal(liveLog(), null);
  const html = panelsHTML({}, {}, {});
  assert.match(html, />Working on</); assert.doesNotMatch(html, />Owns|>Live log/);
  assert.match(html, /data-cb-now[^>]*><b>Idle<\/b> <span>Nothing is running and no earlier work is recorded\.<\/span>/); assert.doesNotMatch(html, /No work recorded yet/);
  assert.doesNotMatch(panelsHTML({}, { owns: [] }), />Owns/);
});
test('present projections win over legacy data, including explicit empty trees and cleared ownership', () => {
  const checkpoint = { open: [{ kind: 'build', detail: 'Reading files', state: 'running' }] };
  assert.equal(workingSteps({}, {}, checkpoint)[0].summary, 'Reading files');
  assert.deepEqual(workingSteps({}, { working_on: [] }, checkpoint), []);
  assert.deepEqual(ownedPaths({ owns: ['stale.py'] }, { conversation: { owns: [] } }), []);
  assert.deepEqual(workingSteps({}, { conversation: { working_on: { name: 'Release', state: 'working', children: [{ name: 'Checks', state: 'done' }] } } })[0].children, [{ name: 'Checks', state: 'done' }]);
});
test('tree shows names, action summaries, one state without dots; owned paths have shared lock marks', () => {
  const html = panelsHTML({}, { working_on: [{ name: 'Release', summary: 'Checking the change', state: 'working', children: [{ name: 'Review', summary: 'Change accepted', state: 'done' }, { name: 'Publish', summary: 'Needs your go', state: 'waiting_on_owner' }] }], owns: [{ glob: 'src/*.py', shared_lock: true }, { path: 'tests/test_release.py' }], live_log: { tail: ['checks complete'] } });
  for (const text of ['Working on', 'Checking the change', 'Change accepted', 'Working', 'Done', 'Waiting on you', 'Owns', 'Shared lock', 'Live log']) assert.ok(html.includes(text), text);
  assert.match(html, /<code>src\/\*\.py<\/code>/);
  assert.match(html, /data-tone="waiting"/);
  assert.doesNotMatch(html, /cb-dot|Needs your go/);
  assert.doesNotMatch(panelsHTML({}, { working_on: [{ name: 'Release', summary: 'Waiting for approval', state: 'needs_you' }] }), /Waiting for approval/);
  assert.doesNotMatch(html, /checks complete/, 'log content is assigned through textContent only');
});
test('redacted tails are text, never HTML; a new tail scrolls to its end', () => {
  const node = { textContent: '', scrollTop: 0, scrollHeight: 400, set innerHTML(_) { throw new Error('HTML is forbidden'); } };
  const raw = '<img src=x onerror=alert(1)> [redacted:credential]';
  renderLiveLog(node, liveLog({}, { live_log: { tail: [{ text: raw }, { text: 'finished' }] } }));
  assert.deepEqual(logLine('12:34  checks  '+raw), {time:'12:34',source:'checks',message:raw,gaps:['  ','  ']});
  assert.equal(logLine(raw), null);
  assert.equal(node.textContent, raw + '\nfinished'); assert.equal(node.scrollTop, 400);
  renderLiveLog(node, ''); assert.equal(node.textContent, '');
});
test('model is selected once from the new field, with the existing project detail as fallback', () => {
  assert.equal(workerModel({}, {}, { models: { main: { model: 'local-model' } } }), 'local-model');
  assert.equal(workerModel({}, { conversation: { model: 'chosen-model' } }, { models: { main: { model: 'old-model' } } }), 'chosen-model');
  assert.equal(workerModel(), '');
});
test('amber represents waiting on the owner; generic blocked and unknown work stays neutral', () => {
  assert.equal(stateView('waiting_on_owner').tone, 'waiting'); assert.equal(stateView('blocked').tone, 'idle'); assert.equal(stateView('waiting').tone, 'idle');
  assert.equal(stateView('working').tone, 'working'); assert.equal(stateView('done').tone, 'live');
});
test('legacy composer switches Steer and Chat to their own authorized routes and preserves exact text', () => {
  const text = '  exact words\n<script>inert</script>  ';
  assert.deepEqual(composerRequest(detail(), 'steer', text, 'req'), { path: '/api/coworkers/jobs/7/controls', body: { text, request_id: 'req', operation: 'follow_up' } });
  assert.deepEqual(composerRequest(detail(), 'chat', text, 'req'), { path: '/api/coworkers/conversations/room/messages', body: { text, request_id: 'req', bot_id: 'bot' } });
  assert.match(composerHTML(detail(), { mode: 'chat', coworker: 'Release' }), /placeholder="Message Release"/);
  assert.match(composerHTML(detail(), { mode: 'steer', coworker: 'Release' }), /placeholder="Steer Release"/);
  const group = { conversation: { ...conversation, bot_ids: ['a', 'b'] } };
  assert.equal(Object.hasOwn(composerRequest(group, 'chat', text, 'req').body, 'bot_id'), false);
  for (const invalid of [{}, { conversation, jobs: [{ ...job, conversation_id: 'other' }] }, { conversation, jobs: [{ ...job, task_progress: { availability: 'unavailable', controls: { can_follow_up: true } } }] }]) assert.equal(composerRequest(invalid, 'steer', text, 'req'), null);
});
test('unified composer is opt in through the advertised capability and adds a mode only there', () => {
  const data = { conversation: { ...conversation, composer: { unified: true, modes: ['steer', 'chat'] } } };
  assert.deepEqual(composerRequest(data, 'steer', 'hello', 'req'), { path: '/api/coworkers/conversations/room/messages', body: { text: 'hello', request_id: 'req', mode: 'steer' } });
  assert.equal(composerRequest({ conversation }, 'steer', 'hello', 'req'), null);
  assert.match(composerHTML(detail(), { mode: 'steer' }), /data-cb-mode="steer" aria-pressed="true"/);
  assert.match(composerHTML(detail(), { mode: 'chat' }), /data-cb-mode="chat" aria-pressed="true"/);
});
test('copy is escaped, ticket numbers disappear, and optional labels never create executable markup', () => {
  const html = capyRosterHTML({ bots: [{ id: 'a', name: '<img>', summary: 'PR #45 shipped from /home/example/private', section: '<script>' }] });
  assert.match(html, /&lt;img&gt;/); assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /#45|\/home\/|<img>|<script>/);
  assert.doesNotMatch(panelsHTML({}, { working_on: [{ name: '<img>', summary: '<script>', state: 'x"' }], owns: [{ path: '<script>.py' }] }), /<img>|<script>/);
});

const settle = async () => { for (let i = 0; i < 30; i++) await new Promise(setImmediate); };
function fixture(api) {
  const events = new Map(), classes = new Set(), body = { innerHTML: '' }, header = { hidden: false };
  const root = { innerHTML: '', ownerDocument: { documentElement: { dataset: { ui: 'deck' } } }, parentElement: { querySelector: () => header }, querySelector: selector => selector === '[data-cb="body"]' ? body : null, classList: { add: x => classes.add(x), remove: x => classes.delete(x) }, addEventListener: (type, fn) => events.set(type, fn), removeEventListener: type => events.delete(type), contains: () => true };
  const calls = [];
  const ui = mount({ root, route: '#/bots/bot/coworkers', me: { workspace: 'w', actor: 'u' }, api: async (path, options) => {
    calls.push([path, options]);
    if (api) { const result = await api(path, options); if (result !== undefined) return result; }
    if (path === '/api/bots') return { bots: [{ id: 'bot', name: 'Atlas' }] };
    if (path === '/api/coworkers') return { bots: [], conversations: [conversation] };
    if (path.endsWith('/room')) return detail();
    throw new Error('unexpected request: ' + path);
  } });
  const mode = value => events.get('click')({ target: { closest: () => ({ dataset: { cbMode: value } }) } });
  const input = value => events.get('input')({ target: { id: 'cb-draft', value } });
  const send = () => events.get('submit')({ target: { matches: () => true }, preventDefault() {} });
  return { ui, body, events, classes, calls, header, mode, input, send };
}
test('real composer mode clicks keep separate drafts and send through the selected mode', async () => {
  const h = fixture(async (path, options) => options.method === 'POST' ? { receipt: { job_id: 7, request_id: options.body.request_id, text: options.body.text, operation: 'follow_up', state: 'completed' } } : undefined);
  await settle(); h.input('chat draft'); h.mode('steer'); h.input('  steer draft  '); h.mode('chat');
  assert.equal((h.body.innerHTML.match(/>Conversation<\/a>/g) || []).length, 1);
  assert.doesNotMatch(h.body.innerHTML, /Open conversation/);
  assert.match(h.body.innerHTML, /chat draft/); h.mode('steer'); assert.match(h.body.innerHTML, /  steer draft  /);
  h.send(); await settle(); const post = h.calls.find(([, options]) => options.method === 'POST');
  assert.equal(post[0], '/api/coworkers/jobs/7/controls'); assert.equal(post[1].body.text, '  steer draft  ');
  assert.match(h.body.innerHTML, /Worker application is unconfirmed/);
  h.ui.stop(); assert.equal(h.events.size, 0); assert.equal(h.classes.size, 0); assert.equal(h.header.hidden, false);
});
test('unconfirmed sends retry one request and cannot change mode or draft under that request', async () => {
  const h = fixture(async (_path, options) => { if (options.method === 'POST') throw new Error('connection lost'); });
  await settle(); h.input('exact chat draft'); h.send(); await settle(); h.mode('steer'); h.send(); await settle();
  const posts = h.calls.filter(([, options]) => options.method === 'POST');
  assert.equal(posts.length, 2); assert.deepEqual(posts[0], posts[1]);
  assert.match(h.body.innerHTML, /data-cb-mode="chat" aria-pressed="true" disabled/); h.ui.stop();
});
test('access revocation clears work, controls and log instead of leaving the old projection', async () => {
  let revoked = false;
  const h = fixture(async path => { if (revoked && path.endsWith('/room')) throw Object.assign(new Error('denied'), { status: 403 }); });
  await settle(); revoked = true; await h.ui.refresh();
  assert.doesNotMatch(h.body.innerHTML, /data-cb-mode="steer" aria-pressed="false">/);
  assert.match(h.body.innerHTML, /could not be loaded/); h.ui.stop();
});
test('departure aborts pending reads and prevents late content from repainting the screen', async () => {
  let resolve;
  const h = fixture(path => path === '/api/bots' ? new Promise(r => { resolve = r; }) : undefined);
  h.ui.stop(); resolve({ bots: [{ id: 'bot', name: 'Late' }] }); await settle();
  assert.equal(h.calls[0][1].signal.aborted, true); assert.equal(h.classes.size, 0); assert.equal(h.root?.innerHTML || '', '');
});
test('slice styles are Deck scoped, token based, and keep the phone composer in document flow', () => {
  const css = readFileSync(new URL('../commonplace/web/static/deck-coworkers.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  for (const match of css.matchAll(/([^{}]+)\{/g)) {
    if (match[1].trim().startsWith('@media')) continue;
    for (const selector of match[1].split(/,(?![^()]*\))/)) assert.match(selector.trim(), /^:root\[data-ui="deck"\]/);
  }
  // Explicit design feedback requests scroll masks and the Deck select fill.
  assert.doesNotMatch(css.replace(/mask-image:[^;]+;/g, '').replace('background: #1e1e1e;', ''), /#[\da-f]{3,8}\b|linear-gradient|backdrop-filter/i);
  assert.match(css, /resize: none/);
  assert.match(css, /260px minmax\(0, 1fr\)/); assert.match(css, /height: 176px/);
  assert.match(css.slice(css.indexOf('@media (max-width: 699px)')), /\[data-cb-composer\] \{ position: static/);
});

 test('both live Bot lists are included, with optional taxonomy joined only by the same ID', () => {
  const bots = overviewBots([{ id: 'a', name: 'Atlas', mine: true }], [{ id: 'a', name: 'Other label', section: 'Workspace', private: true }, { id: 'b', name: 'Harbor' }]);
  assert.equal(bots.length, 2); assert.equal(bots[0].name, 'Atlas'); assert.equal(bots[0].section, 'Workspace'); assert.equal(bots[0].mine, true);
  assert.equal(bots[1].coworkerOnly, true);
});
