import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = name => readFileSync(new URL(`../commonplace/web/static/${name}.js`, import.meta.url), 'utf8');
const moduleURL = text => `data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
const apiURL = moduleURL(source('api'));
const { listHTML, chainHTML, threadHTML, offerHTML, confirmHTML, resultHTML, failureHTML, refusal, mountHistory } =
  await import(moduleURL(source('history').replace(/(['"])\.\/api\.js\1/g, () => JSON.stringify(apiURL))));

const text = html => html.replace(/<\/?(?:code|time|strong|a|span)\b[^>]*>/g, '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const thread = { id: 'thr_new', message_count: 30, created_at: '2026-11-02T10:00:00Z', created_by: 'conn:agent-one' };
const chain = [thread, { id: 'thr_old', message_count: 12, created_at: '2026-11-01T10:00:00Z' }];
const ready = { thread: 'thr_new', state: 'ready', handoff_id: 'hof_1', continues: { thread_id: 'mcp:live', agent: 'codex:vm', task: '#142' } };
const preview = { preview: true, thread: 'thr_new', continues: ready.continues, destination: { provider: 'codex', model: 'gpt-6.1-sol', effort: 'high' }, note: 'Keep going.\n<b>exactly</b> $&', carried: { earlier_threads: 2, earlier_messages: 42, history: ['thr_new', 'thr_old'] } };
const apiError = (status, payload) => Object.assign(new Error(payload?.error || `Request failed (${status})`), { status, payload });

const cases = {
  list() {
    const rows = [{ stored_thread_id: 'thr_new', state: 'done', agent: 'codex:vm', task_ref: '#142', created_at: '2026-11-02T10:00:00Z' }, { stored_thread_id: 'thr_x', state: 'weird', created_at: '' }, { state: 'done' }];
    const html = listHTML(rows), plain = text(html);
    assert.match(html, /href="\/app\/history\.html\?thread=thr_new"/);
    for (const needle of ['thr_new', 'Handed off', 'codex:vm', 'card #142', '2026-11-02 10:00 UTC', 'State: weird', 'Time not recorded', 'Open a saved thread by id']) assert.ok(plain.includes(needle), needle);
    assert.ok(!plain.includes('undefined'));
    // Empty and unknown answers explain themselves.
    assert.match(text(listHTML([])), /No thread has been saved yet.*owner turns it on/);
    assert.match(text(listHTML(null)), /did not understand the answer.*Reload to try again/);
    assert.match(text(listHTML({})), /did not understand the answer/);
  },
  chain() {
    const plain = text(chainHTML(chain, thread));
    for (const needle of ['History this would use', 'these 2 saved threads (42 messages)', 'newest first', 'exact words', 'Nothing is summarised', 'thr_old', '12 messages', 'Read the messages', 'The chain ends here']) assert.ok(plain.includes(needle), needle);
    assert.match(chainHTML(chain, thread), /href="\/app\/threads\/thr_old"/);
    assert.match(chainHTML(chain, thread), /aria-current="true"/);
    assert.match(text(chainHTML(new Array(50).fill(thread), thread)), /newest 50 saved threads are used/);
    assert.match(text(chainHTML(chain.slice(0, 1), thread)), /1 saved thread \(30 messages\)/);
    for (const bad of [null, [], undefined, {}]) assert.match(text(chainHTML(bad, thread)), /chain could not be read/, JSON.stringify(bad));
    assert.match(text(threadHTML(thread)), /30 messages · saved 2026-11-02 10:00 UTC by agent-one/);
  },
  offer() {
    const html = offerHTML(ready), plain = text(html);
    assert.match(plain, /mcp:live \(agent codex:vm\), card #142/);
    assert.match(html, /data-history-handoff/);
    assert.match(html, /name="to"[^>]*required/);
    // Unavailable states say why and give a working next step; none offers the form.
    const cases = [
      [{ state: 'unavailable', reason: 'A later thread in this chain already continues from here.', next_step: 'Open the later thread and hand off from there.', later_thread: 'thr_next' }, /later thread[\s\S]*href="\/app\/history\.html\?thread=thr_next"/],
      [{ state: 'unavailable', reason: 'Handoff is switched off for this work (set at workspace level).', next_step: 'Turn Handoff on in Memory settings, then reload this page.', settings_link: '/app/settings/memory' }, /switched off[\s\S]*href="\/app\/settings\/memory"/],
      [{ state: 'unavailable', reason: 'The handoff saved this thread but the new thread did not start.', next_step: 'Retry the launch.', retry: true, handoff_id: 'hof_9' }, /data-history-relaunch="hof_9"/],
      [{ state: 'unavailable', reason: 'Starting a handoff needs a signed-in workspace member, or an agent connection. You can still read this history.', next_step: '', can_start: false }, /You can still read this history/],
    ];
    for (const [offer, pattern] of cases) {
      const out = offerHTML(offer);
      assert.match(out, pattern);
      assert.doesNotMatch(out, /data-history-handoff|<form|data-history-confirm/);
      assert.ok(text(out).length > 60);
    }
    // An unknown or malformed answer is explained, never rendered empty and never offers the action.
    for (const value of [undefined, null, {}, { state: 'mystery' }, 'text', { state: 'ready-ish' }]) {
      const out = offerHTML(value);
      assert.match(text(out), /did not understand the answer/, JSON.stringify(value));
      assert.doesNotMatch(out, /<form/);
    }
    // A failed read of the options is a failed read, not signed out and not "no action".
    const failed = text(offerHTML({ state: 'unknown', error: new Error('Request timed out') }));
    assert.match(failed, /could not be read just now.*not mean you are signed out/);
    assert.doesNotMatch(failed, /Sign in to see this/);
  },
  confirm() {
    const html = confirmHTML(preview), plain = text(html);
    for (const needle of ['Confirm the handoff', 'Destination codex / gpt-6.1-sol, effort high', 'Continues thread mcp:live', 'whole saved transcript of mcp:live, word for word', 'The 2 saved threads already in this chain (42 messages) stay linked', 'Your note, exactly as typed', 'stopped once the new one has started', 'old one keeps running']) assert.ok(plain.includes(needle), needle);
    // The note is shown verbatim and escaped, with no replacement-pattern expansion.
    assert.ok(html.includes('Keep going.\n&lt;b&gt;exactly&lt;/b&gt; $&amp;'));
    assert.doesNotMatch(html, /<b>/);
    assert.match(html, /data-history-confirm/);
    assert.match(html, /data-history-cancel/);
    assert.match(text(confirmHTML({ ...preview, note: '', carried: { earlier_threads: 0 } })), /No earlier saved thread exists.*No note\./);
    assert.match(confirmHTML(preview, { error: { message: 'T3 is not running.', next: 'Start it, then try again.' } }), /T3 is not running\.[\s\S]*Start it, then try again\./);
    assert.match(confirmHTML(preview, { busy: true }), /Starting…[\s\S]*disabled|disabled[\s\S]*Starting…/);
  },
  results() {
    const done = text(resultHTML({ state: 'done', stored_thread_id: 'thr_next', successor: { agent_ref: 't3:mcp:successor' }, source_settled: true }));
    assert.match(done, /Handoff started\. The old thread is stopped\. The new thread is t3:mcp:successor\. Open the saved thread/);
    const unsettled = text(resultHTML({ state: 'done', successor: { agent_ref: 't3:s' }, source_settled: false }));
    assert.match(unsettled, /started, but the old one could not be stopped.*Run the action again/);
  },
  escaping() {
    const hostile = '<img src=x onerror=alert(1)> $& $\' $` $1 "q"';
    const html = [listHTML([{ stored_thread_id: hostile, state: 'done', agent: hostile, task_ref: hostile, created_at: hostile }]),
      chainHTML([{ id: hostile, message_count: 1, created_at: hostile }], { id: hostile }), threadHTML({ id: hostile, created_at: hostile, created_by: hostile }),
      offerHTML({ state: 'unavailable', reason: hostile, next_step: hostile, later_thread: hostile, settings_link: hostile, retry: true, handoff_id: hostile }),
      offerHTML({ state: 'ready', continues: { thread_id: hostile, agent: hostile, task: hostile } }, { form: { to: hostile, effort: hostile, note: hostile } }),
      confirmHTML({ ...preview, note: hostile, continues: { thread_id: hostile }, destination: { provider: hostile, model: hostile, effort: hostile } }),
      resultHTML({ stored_thread_id: hostile, successor: { agent_ref: hostile } })].join('');
    assert.doesNotMatch(html, /<img/);
    assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt; $&amp; $&#39; $` $1 &quot;q&quot;'));
  },
  failures() {
    assert.match(failureHTML({ status: 401 }), /Sign in to see this[\s\S]*href="\/app\/"/);
    assert.match(text(failureHTML({ status: 404 })), /not available to you.*may not exist/);
    assert.doesNotMatch(failureHTML({ status: 404 }), /data-history-retry/);
    for (const error of [new Error('Request timed out: /api/threads/x'), { status: 500 }, { status: 503 }, undefined]) {
      const html = failureHTML(error, 'this thread');
      assert.match(text(html), /does not mean you are signed out/);
      assert.match(html, /data-history-retry/);
      assert.doesNotMatch(html, /Sign in to see this/);
    }
    assert.match(text(failureHTML({ status: 403 })), /does not include this/);
    assert.match(refusal(apiError(409, { error: 'handoff is off', next_step: 'Turn Handoff on.' })).next, /Turn Handoff on/);
    assert.match(refusal(apiError(403, {})).next, /owner/);
    assert.match(refusal(apiError(401, {})).message, /signed out/);
    const timeout = refusal(new Error('Request timed out'));
    assert.match(timeout.next, /does not mean you are signed out/);
    assert.doesNotMatch(timeout.message, /signed out/);
  },
  async mounting() {
    const calls = [];
    const handlers = {};
    const host = { innerHTML: '', attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }, addEventListener(kind, fn) { handlers[kind] = fn; } };
    let startAnswers = [preview];
    const answer = (path, options = {}) => {
      calls.push([path, options.method || 'GET', options.body]);
      if (path === '/api/threads/thr_new') return Promise.resolve(thread);
      if (path === '/api/threads/thr_new/chain') return Promise.resolve(chain);
      if (path === '/api/handoff/start?thread=thr_new') return Promise.resolve(ready);
      if (path === '/api/handoff/start') { const next = startAnswers.shift(); return next instanceof Error ? Promise.reject(next) : Promise.resolve(next); }
      return Promise.reject(new Error(`unexpected ${path}`));
    };
    const target = (selector, form) => ({ closest: (s) => (s === selector ? { getAttribute: () => null, dataset: {} , form } : null) });
    await mountHistory(host, { read: answer, location: { search: '?thread=thr_new' }, fields: () => ({ to: 'codex/gpt-6.1-sol', effort: 'high', note: 'Keep going.' }) });
    assert.match(host.innerHTML, /History this would use[\s\S]*Hand off/);
    assert.equal(host.attrs['aria-busy'], 'false');
    // Review: a preview, then nothing started yet.
    handlers.submit({ preventDefault() {}, target: target('[data-history-handoff]') });
    await flush();
    assert.deepEqual(calls.at(-1), ['/api/handoff/start', 'POST', { thread: 'thr_new', to: 'codex/gpt-6.1-sol', effort: 'high', note: 'Keep going.' }]);
    assert.match(host.innerHTML, /Confirm the handoff[\s\S]*codex \/ gpt-6.1-sol/);
    // A refusal keeps the confirmation open with the reason and a working next step; a retry works.
    startAnswers = [apiError(409, { error: 'The old thread is still running and nothing was lost.', next_step: 'Check T3, then try again.' }), { preview: false, state: 'done', stored_thread_id: 'thr_next', successor: { agent_ref: 't3:s' }, source_settled: true }];
    handlers.click({ target: target('[data-history-confirm]') });
    await flush();
    assert.deepEqual(calls.at(-1)[2], { thread: 'thr_new', to: 'codex/gpt-6.1-sol', effort: 'high', note: preview.note, confirm: true });
    assert.match(text(host.innerHTML), /still running and nothing was lost\. Check T3, then try again\./);
    assert.match(host.innerHTML, /data-history-confirm/);
    handlers.click({ target: target('[data-history-confirm]') });
    await flush();
    assert.match(text(host.innerHTML), /Handoff started/);
    assert.doesNotMatch(host.innerHTML, /<form/);
    // A timeout on the preview is a failed request: it never says signed out.
    await mountHistory(host, { read: answer, location: { search: '?thread=thr_new' }, fields: () => ({ to: 'codex/sol' }) });
    startAnswers = [new Error('Request timed out: /api/handoff/start')];
    handlers.submit({ preventDefault() {}, target: target('[data-history-handoff]') });
    await flush();
    assert.match(text(host.innerHTML), /nothing was changed.*not mean you are signed out/);
    assert.doesNotMatch(host.innerHTML, /Sign in/);
    // Cancel returns to the form with the typed values kept.
    startAnswers = [preview];
    handlers.submit({ preventDefault() {}, target: target('[data-history-handoff]') });
    await flush();
    handlers.click({ target: target('[data-history-cancel]') });
    assert.match(host.innerHTML, /data-history-handoff/);
    assert.match(host.innerHTML, /value="codex\/sol"/);
  },
  async denied() {
    const host = { innerHTML: '', setAttribute() {}, addEventListener() {} };
    const denied = { state: 'unavailable', can_start: false, reason: 'Starting a handoff needs a signed-in workspace member. You can still read this history.', next_step: '' };
    const read = (path) => path.endsWith('/chain') ? Promise.resolve(chain) : path.startsWith('/api/handoff/start') ? Promise.resolve(denied) : Promise.resolve(thread);
    await mountHistory(host, { read, location: { search: '?thread=thr_new' } });
    assert.match(text(host.innerHTML), /History this would use/);
    assert.match(text(host.innerHTML), /You can still read this history/);
    assert.doesNotMatch(host.innerHTML, /<form|data-history-confirm|data-history-handoff/);
    // A thread the person cannot read: not available, and no hint that it exists.
    await mountHistory(host, { read: () => Promise.reject(apiError(404, { error: 'not found' })), location: { search: '?thread=thr_x' } });
    assert.match(text(host.innerHTML), /not available to you/);
    assert.doesNotMatch(host.innerHTML, /<form/);
    // The list opens with no thread named.
    await mountHistory(host, { read: () => Promise.resolve([]), location: { search: '' } });
    assert.match(text(host.innerHTML), /History and handoff[\s\S]*No thread has been saved yet/);
  },
};

const name = process.argv[2];
assert.ok(cases[name], `unknown case ${name}`);
await cases[name]();
