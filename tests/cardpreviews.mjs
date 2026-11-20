import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const load = (name) => import(new URL(`../commonplace/web/static/${name}`, import.meta.url));
const { cardPreviewsHTML, cardPreviewsSlot, attachCardPreviews, previousPreviews, focusedKey, refusalWords, offlineHTML, STATE_WORDS, FOCUS_KEYS } = await load('cardpreviews.js');
const { renderDetails, createDetails } = await load('details.js');
const { setPeople } = await load('api.js');
const card = { number: 7, board: 'atlas', version: 2, title: 'Offline queue', body: '', status: 'todo', events: [] };
const preview = (extra = {}) => ({
  id: 'pv_0123456789abcdef', card: 7, name: 'home', kind: 'static', placement: 'hub', revision: 2, state: 'ready',
  reason: 'published', reason_text: 'Published and open to people who can open this card.', next: '', availability: 'reachable',
  size_bytes: 2048, file_count: 3, pr: null, published_by: 'a1', by: 'coworker', published_at: '2026-11-19T10:00:00Z', started_at: null,
  stops_at: null, last_changed_at: '2026-11-19T10:00:00Z', url: '/p/pv_0123456789abcdef/', failure: null, ...extra,
});
const attempt = (extra = {}) => preview({ revision: 0, url: null, published_at: null, started_at: '2026-11-19T09:00:00Z', ...extra });
const found = (previews, extra = {}) => ({
  previews, usage: { ready: 1, bytes: 3 * 1024 * 1024 }, limits: { max_ready: 25, max_workspace_bytes: 500 * 1024 * 1024 },
  rule: { stop_days_after_pr: null, max_age_days: null, updated_at: null, text: 'Previews are served until a person or the Coworker that published them stops them.' },
  can_set_rule: false, ...extra,
});
const wait = () => new Promise((done) => setTimeout(done, 0));

// A small fake of the section: the body and the status line, and controls that can take focus.
globalThis.document = { activeElement: null };
function fakeSlot() {
  const handlers = {};
  const body = { innerHTML: '' }, status = { textContent: '' };
  const element = (name, value) => ({
    name, value, focus() { globalThis.document.activeElement = this; },
    hasAttribute: (attr) => attr === name, getAttribute: (attr) => attr === name ? value : null,
  });
  const slot = {
    hidden: true, isConnected: true, dataset: { cardKey: 'atlas/7' }, body, status,
    addEventListener: (type, fn) => { handlers[type] = fn; },
    contains: (el) => Boolean(el && el.inSlot === slot),
    querySelector(selector) {
      if (selector === '[data-preview-body]') return body;
      if (selector === '[data-preview-status]') return status;
      const match = selector.match(/^\[(data-preview-[a-z-]+)="([^"]*)"\]$/);
      if (!match) return null;
      const [, name, value] = match;
      if (name !== 'data-preview-heading' && !body.innerHTML.includes(`${name}="${value}"`)) return null;
      const el = element(name, value); el.inSlot = slot; return el;
    },
  };
  return { slot, handlers, body, status, mount: { querySelector: (selector) => selector === '[data-card-previews]' ? slot : null } };
}
const clickOn = (attr, id) => {
  const button = { dataset: { [attr]: id }, closest: () => ({ querySelectorAll: () => [] }) };
  const name = `[data-${attr.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}]`;
  return { target: { closest: (selector) => selector === name ? button : null } };
};
const focused = () => { const a = globalThis.document.activeElement; return a ? `${a.name}=${a.value}` : null; };

const cases = {
  states() {
    const html = cardPreviewsHTML(found([
      preview(),
      attempt({ id: 'pv_0000000000000001', name: 'docs', state: 'building', reason: 'storing', reason_text: 'The files are being saved.', next: 'This takes a few seconds; the card updates by itself.' }),
      preview({ id: 'pv_0000000000000002', name: 'old', state: 'stopped', reason: 'stopped_by_person', reason_text: 'Stopped by a person. The files are kept.', next: 'Use Open again on this card to serve it.', url: '/p/pv_0000000000000002/' }),
      attempt({ id: 'pv_0000000000000003', name: 'bad', state: 'failed', reason: 'no_index_html', reason_text: 'Nothing was published: the files had no index.html at the top.', next: 'Ask the Coworker that published it to publish again with an index.html at the top of the files. Reply on this card.' }),
    ]));
    for (const text of ['Ready', 'Being published', 'Stopped', 'Could not be published', 'The files are being saved.', 'the card updates by itself', 'Use Open again on this card', 'Nothing was published', 'Reply on this card.', 'revision 2', '1 of 25 previews served', '3 MiB of 500 MiB used (stopped previews still count)', 'served until a person']) {
      assert.ok(html.includes(text), `missing: ${text}`);
    }
    assert.deepEqual(Object.keys(STATE_WORDS), ['ready', 'building', 'stopped', 'failed']);
    // An attempt that was never published says when it started, never "published".
    const rows = html.split('<li ');
    const row = (id) => rows.find((r) => r.includes(`data-preview="${id}"`));
    assert.ok(row('pv_0000000000000001').includes(' · started ') && !row('pv_0000000000000001').includes('published <time'));
    assert.ok(row('pv_0000000000000003').includes(' · started ') && !row('pv_0000000000000003').includes('published <time'));
    assert.ok(row('pv_0123456789abcdef').includes(' · published '));
    // Actions only where they act; no "Try again" without a button.
    assert.deepEqual([...html.matchAll(/data-preview-stop="([^"]+)"/g)].map((m) => m[1]), ['pv_0123456789abcdef']);
    assert.deepEqual([...html.matchAll(/data-preview-reopen="([^"]+)"/g)].map((m) => m[1]), ['pv_0000000000000002']);
    assert.ok(!html.includes('Try again') && !html.toLowerCase().includes('bundle'));
    const opens = [...html.matchAll(/<a [^>]*data-preview-open[^>]*>/g)].map((m) => m[0]);
    assert.equal(opens.length, 1);
    assert.ok(opens[0].includes('href="/p/pv_0123456789abcdef/"') && opens[0].includes('target="_blank"') && opens[0].includes('rel="noopener noreferrer"'));
  },
  failure() {
    const html = cardPreviewsHTML(found([attempt({ state: 'failed', reason: 'contains_secret_like_text', reason_text: 'Nothing was published: a file looked like it holds a credential.', failure: { file: 'config/env.js', kind: 'github_token' } })]));
    assert.ok(html.includes('File: <code>config/env.js</code>, which looked like a GitHub token.'));
    assert.ok(cardPreviewsHTML(found([attempt({ state: 'failed', reason: 'bad_path', failure: { file: 'a\\b', kind: '' } })])).includes('File: <code>a\\b</code>.'));
  },
  labels() {
    // The visible words start every accessible name, and the publisher is named as on the card header.
    setPeople({ 'conn:a1': 'Release coworker' });
    const html = cardPreviewsHTML(found([preview(), preview({ id: 'pv_0000000000000002', state: 'stopped', url: '/p/pv_0000000000000002/' })]));
    assert.ok(html.includes('aria-label="Open again: serve preview home"'));
    assert.ok(html.includes('aria-label="Open preview home in a new tab"') && html.includes('aria-label="Stop preview home"'));
    assert.ok(html.includes('by Release coworker') && !html.includes('by Coworker a1'));
    setPeople({});
  },
  empty() {
    const html = cardPreviewsHTML(found([], { usage: { ready: 0, bytes: 0 } }));
    assert.ok(html.includes('No preview yet. When a Coworker publishes one for this card, it appears here and stays after the work session ends.'));
    assert.ok(html.includes('0 KiB of 500 MiB used'));
  },
  escape() {
    const html = cardPreviewsHTML(found([preview({ name: '<img src=x onerror=alert(1)>', reason_text: '<b>x</b>', published_by: '"><script>' })]));
    assert.ok(!html.includes('<img src=x') && !html.includes('<b>x</b>') && !html.includes('"><script>'));
  },
  url() {
    for (const url of ['javascript:alert(1)', 'https://elsewhere.example/p/pv_0123456789abcdef/', '/p/pv_0123456789abcdef', '/p/../app/']) {
      assert.ok(!cardPreviewsHTML(found([preview({ url })])).includes('data-preview-open'), url);
    }
  },
  rule() {
    assert.ok(!cardPreviewsHTML(found([])).includes('data-preview-rule-form'));
    const html = cardPreviewsHTML(found([], { can_set_rule: true, rule: { stop_days_after_pr: 7, max_age_days: null, text: 'x' } }));
    assert.ok(html.includes('Days after the card records the pull request as merged or closed') && html.includes('Days after it was last made live'));
    assert.ok(html.includes('value="7"') && !html.includes('open place'));
  },
  async mount() {
    const { slot, mount, body } = fakeSlot();
    await attachCardPreviews(mount, card, { api: async () => found([preview()]) });
    assert.equal(slot.hidden, false);
    assert.ok(body.innerHTML.includes('Ready'));
    // The slot carries one status line outside the redrawn list.
    const html = cardPreviewsSlot(card);
    assert.ok(html.includes('<div data-preview-body></div><p class="run-note" role="status" aria-live="polite" data-preview-status></p>'));
    assert.ok(html.includes('tabindex="-1" data-preview-heading="previews"') && html.includes(' hidden'));
  },
  async refused() {
    for (const status of [403, 404]) {
      const { slot, mount, body } = fakeSlot();
      await attachCardPreviews(mount, { ...card, number: 40 + status }, { api: async () => { throw Object.assign(new Error('no'), { status }); } });
      assert.equal(slot.hidden, true);
      assert.equal(body.innerHTML, '');
    }
  },
  async offline() {
    // Network failure: says so with a real Try again. A server error says "Try again" only.
    const net = fakeSlot();
    await attachCardPreviews(net.mount, { ...card, number: 20 }, { api: async () => { throw new TypeError('Failed to fetch'); } });
    assert.ok(net.body.innerHTML.includes('Check the connection and try again.') && net.body.innerHTML.includes('data-preview-retry'));
    const server = fakeSlot();
    await attachCardPreviews(server.mount, { ...card, number: 21 }, { api: async () => { throw Object.assign(new Error('x'), { status: 502 }); } });
    assert.ok(server.body.innerHTML.includes('Could not load the previews. Try again.') && !server.body.innerHTML.includes('connection'));
    assert.equal(server.body.innerHTML, offlineHTML({ status: 502 }));
  },
  async offlinepainted() {
    // A repainted panel that had no list (it was the offline view) never claims to show a list.
    const { body, status, mount } = fakeSlot();
    const previous = { key: 'atlas/22', html: offlineHTML(new TypeError('x')) };
    await attachCardPreviews(mount, { ...card, number: 22 }, { api: async () => { throw new TypeError('x'); }, previous });
    assert.ok(body.innerHTML.includes('Could not load the previews'));
    assert.ok(!status.textContent.includes('Showing the last list'));
    // With a real list repainted, the note is true and the list stays.
    const second = fakeSlot();
    const listed = { key: 'atlas/23', html: cardPreviewsHTML(found([preview()])) };
    await attachCardPreviews(second.mount, { ...card, number: 23 }, { api: async () => { throw new TypeError('x'); }, previous: listed });
    assert.ok(second.body.innerHTML.includes('Ready') && second.status.textContent === 'Could not refresh the previews. Showing the last list.');
  },
  async retry() {
    const { body, handlers, mount } = fakeSlot();
    let fail = true;
    await attachCardPreviews(mount, { ...card, number: 24 }, { api: async () => { if (fail) throw new TypeError('x'); return found([preview()]); } });
    fail = false;
    handlers.click({ target: { closest: (selector) => selector === '[data-preview-retry]' ? {} : null } });
    await wait(); await wait();
    assert.ok(body.innerHTML.includes('Ready'));
    assert.equal(focused(), 'data-preview-heading=previews');
  },
  async stop() {
    const { body, status, handlers, mount } = fakeSlot();
    let state = 'ready';
    const api = async (url, options) => {
      if (options?.method) { state = url.endsWith('/stop') ? 'stopped' : 'ready'; return preview({ state }); }
      return found([preview({ state })]);
    };
    await attachCardPreviews(mount, { ...card, number: 25 }, { api });
    handlers.click(clickOn('previewStop', 'pv_0123456789abcdef'));
    await wait(); await wait();
    assert.equal(status.textContent, 'Stopped. The files are kept; use Open again to serve it.');
    // Focus moves to the control that replaced Stop.
    assert.equal(focused(), 'data-preview-reopen=pv_0123456789abcdef');
    assert.ok(!body.innerHTML.includes('Stopped. The files'));  // the note is not in the list
    handlers.click(clickOn('previewReopen', 'pv_0123456789abcdef'));
    await wait(); await wait();
    assert.equal(status.textContent, 'Served again. Press Open to view it.');
    assert.equal(focused(), 'data-preview-open=pv_0123456789abcdef');
  },
  async stopwords() {
    // The note follows the state the server returned, not the button pressed.
    const { status, handlers, mount } = fakeSlot();
    const api = async (url, options) => options?.method ? preview({ state: 'building', reason_text: 'The files are being saved.' }) : found([preview()]);
    await attachCardPreviews(mount, { ...card, number: 26 }, { api, schedule: () => {} });
    handlers.click(clickOn('previewStop', 'pv_0123456789abcdef'));
    await wait(); await wait();
    assert.equal(status.textContent, 'Not stopped: The files are being saved.');
  },
  async refusal() {
    assert.equal(refusalWords({ status: 409, payload: { error: 'This workspace is at its preview limit.', next: 'Stop a preview that is no longer needed.' } }), 'This workspace is at its preview limit. Stop a preview that is no longer needed.');
    assert.equal(refusalWords(new TypeError('Failed to fetch')), 'That did not reach the server. Check the connection and try again.');
    assert.equal(refusalWords({ status: 500 }), 'That did not work on the server. Try again.');
    const { status, handlers, mount } = fakeSlot();
    const api = async (url, options) => {
      if (options?.method) throw Object.assign(new Error('x'), { status: 409, payload: { error: 'This workspace is at its preview limit.', next: 'Stop a preview that is no longer needed.' } });
      return found([preview({ state: 'stopped', url: '/p/pv_0123456789abcdef/' })]);
    };
    await attachCardPreviews(mount, { ...card, number: 27 }, { api });
    handlers.click(clickOn('previewReopen', 'pv_0123456789abcdef'));
    await wait(); await wait();
    assert.ok(status.textContent.includes('at its preview limit') && status.textContent.includes('Stop a preview'));
    assert.equal(focused(), 'data-preview-heading=previews');
  },
  async rulesave() {
    const { status, handlers, mount } = fakeSlot();
    const puts = [];
    const api = async (url, options) => { if (options?.method) { puts.push([url, options.method, options.body]); return {}; } return found([], { can_set_rule: true }); };
    await attachCardPreviews(mount, { ...card, number: 28 }, { api });
    const form = (stop, age) => ({ elements: { stop_days_after_pr: { value: stop }, max_age_days: { value: age } }, querySelectorAll: () => [], closest: () => null });
    const submit = (f) => { const event = { target: { closest: (s) => s === '[data-preview-rule-form]' ? f : null }, preventDefault() { event.prevented = true; } }; handlers.submit(event); return event; };
    for (const [stop, age] of [['0', ''], ['1.5', ''], ['', '-3'], ['x', '']]) {
      assert.equal(submit(form(stop, age)).prevented, true);
      assert.ok(status.textContent.includes('whole days'));
    }
    await wait();
    assert.deepEqual(puts, []);
    submit(form(' 7 ', ''));
    await wait(); await wait();
    assert.deepEqual(puts, [['/api/previews/rules', 'PUT', { stop_days_after_pr: 7, max_age_days: null }]]);
    assert.equal(status.textContent, 'Saved the stop rule.');
    assert.equal(focused(), 'data-preview-rule-summary=rule');
  },
  async redraw() {
    // A redraw of the same card repaints the list (not an old result) and keeps focus on the
    // same control; another card's list is never shown.
    const { slot, body, mount } = fakeSlot();
    await attachCardPreviews(mount, card, { api: async () => found([preview()]) });
    globalThis.document.activeElement = slot.querySelector('[data-preview-stop="pv_0123456789abcdef"]');
    slot.status.textContent = 'An old result.';
    const kept = previousPreviews(mount);
    assert.deepEqual(kept, { key: 'atlas/7', html: body.innerHTML, focus: '[data-preview-stop="pv_0123456789abcdef"]' });
    const next = fakeSlot();
    globalThis.document.activeElement = null;
    let release;
    const pending = attachCardPreviews(next.mount, card, { api: () => new Promise((done) => { release = done; }), previous: kept });
    assert.ok(next.body.innerHTML.includes('Ready') && next.status.textContent === '');
    assert.equal(focused(), 'data-preview-stop=pv_0123456789abcdef');
    release(found([preview()]));
    await pending;
    assert.equal(focused(), 'data-preview-stop=pv_0123456789abcdef');
    const other = fakeSlot();
    void attachCardPreviews(other.mount, { ...card, number: 31 }, { api: () => new Promise(() => {}), previous: kept });
    assert.equal(other.slot.hidden, true);
  },
  async poll() {
    // While a preview is being published the section asks again by itself, then stops asking.
    const { body, mount } = fakeSlot();
    const timers = [];
    let calls = 0;
    const api = async () => { calls += 1; return found([calls === 1 ? attempt({ state: 'building', reason: 'storing' }) : preview()]); };
    await attachCardPreviews(mount, { ...card, number: 32 }, { api, schedule: (fn, ms) => timers.push([fn, ms]) });
    assert.equal(timers.length, 1);
    assert.equal(timers[0][1], 3000);
    timers.shift()[0]();
    await wait(); await wait();
    assert.ok(body.innerHTML.includes('data-state="ready"'));
    assert.equal(timers.length, 0);
  },
  focuskeys() {
    // The shell restores focus by these keys too; its list must match this module's.
    const app = readFileSync(new URL('../commonplace/web/static/app.js', import.meta.url), 'utf8');
    const listed = JSON.parse(app.match(/const PREVIEW_FOCUS_KEYS = (\[[^\]]*\]);/)[1]);
    assert.deepEqual(listed, FOCUS_KEYS);
    const { slot } = fakeSlot();
    assert.equal(focusedKey(slot, { activeElement: null }), null);
  },
  deleteview() {
    // Only an owner sees Delete files, only where there are files, and never while publishing.
    const list = [
      preview(),
      preview({ id: 'pv_0000000000000002', state: 'stopped', url: '/p/pv_0000000000000002/' }),
      preview({ id: 'pv_0000000000000003', state: 'stopped', reason: 'files_deleted', url: null }),
      preview({ id: 'pv_0000000000000004', state: 'building', url: '/p/pv_0000000000000004/' }),
    ];
    assert.ok(!cardPreviewsHTML(found(list)).includes('data-preview-delete'));
    const html = cardPreviewsHTML(found(list, { can_delete: true }));
    assert.deepEqual([...html.matchAll(/data-preview-delete="([^"]+)"/g)].map((m) => m[1]), ['pv_0123456789abcdef', 'pv_0000000000000002']);
    // Confirming names the preview, its size and that it cannot be undone.
    const asking = cardPreviewsHTML(found(list, { can_delete: true }), 'pv_0123456789abcdef');
    assert.ok(asking.includes('data-preview-delete-confirm="pv_0123456789abcdef"') && asking.includes('data-preview-delete-cancel="pv_0123456789abcdef"'));
    assert.ok(asking.includes('Delete the files of <b>home</b> (2 KiB, this card)?') && asking.includes('cannot be undone'));
    assert.ok(!asking.includes('data-preview-delete="pv_0123456789abcdef"'));
  },
  async deleteflow() {
    const { body, status, mount, handlers } = fakeSlot();
    const sent = [];
    let deleted = false, left = false;
    const api = async (url, options) => {
      // The first delete leaves files behind; the second removes them.
      if (options?.method === 'DELETE') { sent.push([url, options.body]); deleted = true; left = sent.length === 1; return { files_remaining: left }; }
      return found([deleted ? preview({ state: 'stopped', reason: 'files_deleted', reason_text: 'A workspace owner deleted the files of this preview.', url: null, files_remaining: left }) : preview()], { can_delete: true });
    };
    await attachCardPreviews(mount, { ...card, number: 26 }, { api });
    handlers.click(clickOn('previewDelete', 'pv_0123456789abcdef'));
    assert.ok(body.innerHTML.includes('cannot be undone'));
    // Asking moves focus to Cancel; cancelling puts it back on Delete files.
    assert.equal(focused(), 'data-preview-delete-cancel=pv_0123456789abcdef');
    handlers.click(clickOn('previewDeleteCancel', 'pv_0123456789abcdef'));
    assert.ok(!body.innerHTML.includes('cannot be undone') && body.innerHTML.includes('data-preview-delete="pv_0123456789abcdef"'));
    assert.equal(focused(), 'data-preview-delete=pv_0123456789abcdef');
    assert.deepEqual(sent, []);
    handlers.click(clickOn('previewDelete', 'pv_0123456789abcdef'));
    handlers.click(clickOn('previewDeleteConfirm', 'pv_0123456789abcdef'));
    await wait(); await wait();
    assert.deepEqual(sent, [['/api/previews/pv_0123456789abcdef', { confirm: 'pv_0123456789abcdef' }]]);
    // The result is said in the one status line, not in the list.
    assert.equal(status.textContent, 'Some files could not be removed. Use Finish deleting to try again.');
    assert.ok(!body.innerHTML.includes('could not be removed'));
    assert.ok(body.innerHTML.includes('deleted the files') && !body.innerHTML.includes('data-preview-open'));
    // The retry the note promises is there, focus is on it, and it finishes the job.
    assert.ok(body.innerHTML.includes('>Finish deleting<') && body.innerHTML.includes('data-preview-delete="pv_0123456789abcdef"'));
    assert.equal(focused(), 'data-preview-delete=pv_0123456789abcdef');
    handlers.click(clickOn('previewDelete', 'pv_0123456789abcdef'));
    assert.ok(body.innerHTML.includes('that were left behind last time'));
    handlers.click(clickOn('previewDeleteConfirm', 'pv_0123456789abcdef'));
    await wait(); await wait();
    assert.equal(sent.length, 2);
    assert.equal(status.textContent, 'Deleted the files. People can no longer open this preview.');
    assert.ok(!body.innerHTML.includes('data-preview-delete='));
    // Nothing left to act on: focus falls back to the heading.
    assert.equal(focused(), 'data-preview-heading=previews');
  },
  async stale() {
    const { body, slot, mount } = fakeSlot();
    const release = [];
    const pending = attachCardPreviews(mount, { ...card, number: 33 }, { api: () => new Promise((done) => { release.push(done); }) });
    slot.isConnected = false;
    release.forEach((done) => done(found([preview()])));
    await pending;
    assert.equal(body.innerHTML, '');
  },
  panel() {
    for (const deck of [false, true]) {
      const html = renderDetails(card, { board: { slug: 'atlas' }, me: 'user:maya', deck });
      const runs = html.indexOf('data-card-runs'), previews = html.indexOf('data-card-previews');
      assert.ok(runs > 0 && previews > runs, `deck=${deck}`);
    }
  },
  async loads() {
    const body = { innerHTML: '' }, status = { textContent: '' };
    const slot = { hidden: true, isConnected: true, dataset: {}, addEventListener() {}, contains: () => false, querySelector: (s) => s === '[data-preview-body]' ? body : s === '[data-preview-status]' ? status : null };
    const mount = { innerHTML: '', addEventListener() {}, removeEventListener() {}, querySelector: (selector) => selector === '[data-card-previews]' ? slot : null };
    const calls = [];
    const component = createDetails({ mount, api: async (url) => { calls.push(url); return url.startsWith('/api/previews') ? found([preview()]) : url.startsWith('/api/card-starts') ? [] : {}; } });
    await component.show({ ...card, number: 34 }, { board: { slug: 'atlas' }, me: 'user:maya' });
    await wait();
    assert.deepEqual(calls.filter((url) => url.startsWith('/api/previews')), ['/api/previews?card=34']);
    assert.ok(body.innerHTML.includes('Ready'));
    component.stop();
  },
};
assert.ok(Object.hasOwn(cases, process.argv[2]), 'Unknown case');
await cases[process.argv[2]]();
console.log(`${process.argv[2]} PASS`);
