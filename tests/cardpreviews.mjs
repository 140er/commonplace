import assert from 'node:assert/strict';
const load = (name) => import(new URL(`../commonplace/web/static/${name}`, import.meta.url));
const { cardPreviewsHTML, cardPreviewsSlot, attachCardPreviews, refusalWords, offlineHTML, STATE_WORDS } = await load('cardpreviews.js');
const { renderDetails, createDetails } = await load('details.js');
const card = { number: 7, board: 'atlas', version: 2, title: 'Offline queue', body: '', status: 'todo', events: [] };
const preview = (extra = {}) => ({
  id: 'pv_0123456789abcdef', card: 7, name: 'home', kind: 'static', placement: 'hub', revision: 2, state: 'ready',
  reason: 'published', reason_text: 'Published and open to people who can open this card.', next: '', availability: 'reachable',
  size_bytes: 2048, file_count: 3, pr: null, published_by: 'a1', by: 'coworker', published_at: '2026-11-19T10:00:00Z',
  stops_at: null, last_changed_at: '2026-11-19T10:00:00Z', url: '/p/pv_0123456789abcdef/', ...extra,
});
const found = (previews, extra = {}) => ({
  previews, usage: { ready: 1, bytes: 3 * 1024 * 1024 }, limits: { max_ready: 25, max_workspace_bytes: 500 * 1024 * 1024 },
  rule: { stop_days_after_pr: null, max_age_days: null, updated_at: null, text: 'Previews are served until a person or the Coworker that published them stops them.' },
  can_set_rule: false, ...extra,
});
const wait = () => new Promise((done) => setTimeout(done, 0));

function fakeSlot() {
  const handlers = {};
  const slot = {
    hidden: true, innerHTML: '', isConnected: true,
    addEventListener: (type, fn) => { handlers[type] = fn; },
    querySelector: (selector) => selector === '.run-note' ? slot.note : null,
    note: { textContent: '' },
  };
  return { slot, handlers, mount: { querySelector: (selector) => selector === '[data-card-previews]' ? slot : null } };
}
const clickOn = (attr, id) => {
  const button = { dataset: { [attr]: id }, closest: () => ({ querySelectorAll: () => [] }) };
  const name = `[data-${attr.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}]`;
  return { target: { closest: (selector) => selector === name ? button : null } };
};

const cases = {
  states() {
    // Every state in words, each with the server's reason and what to do next.
    const html = cardPreviewsHTML(found([
      preview(),
      preview({ id: 'pv_0000000000000001', name: 'docs', state: 'building', reason: 'storing', reason_text: 'The files are being saved.', next: 'Wait a moment.', url: null, revision: 0 }),
      preview({ id: 'pv_0000000000000002', name: 'old', state: 'stopped', reason: 'stopped_by_person', reason_text: 'Stopped by a person. The files are kept.', next: 'Use Open again to serve it.', url: '/p/pv_0000000000000002/' }),
      preview({ id: 'pv_0000000000000003', name: 'bad', state: 'failed', reason: 'no_index_html', reason_text: 'The bundle has no index.html at its top level.', next: 'Publish again with an index.html.', url: null, revision: 0 }),
    ]));
    for (const text of ['Ready', 'Being published', 'Stopped', 'Could not be published', 'The files are being saved.', 'Wait a moment.', 'Use Open again to serve it.', 'The bundle has no index.html', 'revision 2', 'by Coworker a1', '1 of 25 open previews', '3 MiB of 500 MiB used', 'served until a person']) {
      assert.ok(html.includes(text), `missing: ${text}`);
    }
    assert.deepEqual(Object.keys(STATE_WORDS), ['ready', 'building', 'stopped', 'failed']);
    // Open (new tab) and Stop only while ready; Open again only when stopped; nothing for the others.
    assert.deepEqual([...html.matchAll(/data-preview-stop="([^"]+)"/g)].map((m) => m[1]), ['pv_0123456789abcdef']);
    assert.deepEqual([...html.matchAll(/data-preview-reopen="([^"]+)"/g)].map((m) => m[1]), ['pv_0000000000000002']);
    const opens = [...html.matchAll(/<a [^>]*data-preview-open[^>]*>/g)].map((m) => m[0]);
    assert.equal(opens.length, 1);
    assert.ok(opens[0].includes('href="/p/pv_0123456789abcdef/"') && opens[0].includes('target="_blank"') && opens[0].includes('rel="noopener noreferrer"'));
  },
  empty() {
    const html = cardPreviewsHTML(found([], { usage: { ready: 0, bytes: 0 } }));
    assert.ok(html.includes('No preview yet. When a Coworker publishes one for this card, it appears here and stays after the work session ends.'));
    assert.ok(html.includes('0 KiB of 500 MiB used'));
    assert.ok(cardPreviewsHTML(found([], { usage: { ready: 0, bytes: 2048 } })).includes('2 KiB of 500 MiB used'));
    assert.ok(cardPreviewsHTML(found([], { usage: { ready: 0, bytes: 10 } })).includes('1 KiB of 500 MiB used'));
    assert.ok(!html.includes('data-preview-stop'));
  },
  escape() {
    const html = cardPreviewsHTML(found([preview({ name: '<img src=x onerror=alert(1)>', reason_text: '<b>x</b>', published_by: '"><script>' })]));
    assert.ok(!html.includes('<img src=x') && !html.includes('<b>x</b>') && !html.includes('"><script>'));
    assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  },
  url() {
    // Only the server's own preview address is ever a link: anything else shows no Open.
    for (const url of ['javascript:alert(1)', 'https://elsewhere.example/p/pv_0123456789abcdef/', '/p/pv_0123456789abcdef', '/p/../app/', '//evil/p/pv_0123456789abcdef/']) {
      const html = cardPreviewsHTML(found([preview({ url })]));
      assert.ok(!html.includes('data-preview-open'), url);
    }
  },
  stops() {
    const html = cardPreviewsHTML(found([preview({ stops_at: '2026-12-01T10:00:00Z' })]));
    assert.ok(html.includes('Stops being served on') && html.includes('under the workspace rule') && html.includes('The files are kept'));
    assert.ok(!cardPreviewsHTML(found([preview({ state: 'stopped', stops_at: '2026-12-01T10:00:00Z' })])).includes('Stops being served'));
  },
  rule() {
    // Members read the rule; only the owner gets the form, with the current values in it.
    assert.ok(!cardPreviewsHTML(found([])).includes('data-preview-rule-form'));
    const html = cardPreviewsHTML(found([], { can_set_rule: true, rule: { stop_days_after_pr: 7, max_age_days: null, text: 'A preview stops being served 7 days after its pull request is merged or closed. Its files are kept.' } }));
    assert.ok(html.includes('data-preview-rule-form') && html.includes('name="stop_days_after_pr"') && html.includes('value="7"'));
    assert.ok(html.includes('Stopping never deletes the files'));
  },
  async mount() {
    const { slot, mount } = fakeSlot();
    const calls = [];
    await attachCardPreviews(mount, card, { api: async (url) => { calls.push(url); return found([preview()]); } });
    assert.deepEqual(calls, ['/api/previews?card=7']);
    assert.equal(slot.hidden, false);
    assert.ok(slot.innerHTML.includes('Ready'));
    // No module-level cache: a fresh slot is always empty and hidden, whatever was shown before.
    assert.ok(!cardPreviewsSlot(card).includes('Ready') && cardPreviewsSlot(card).includes(' hidden'));
    assert.ok(cardPreviewsSlot(card).includes('data-card-key="atlas/7"'));
  },
  async refused() {
    // A card this person cannot see: the section stays away, as for a missing card.
    for (const status of [403, 404]) {
      const { slot, mount } = fakeSlot();
      await attachCardPreviews(mount, { ...card, number: 40 + status }, { api: async () => { throw Object.assign(new Error('no'), { status }); } });
      assert.equal(slot.hidden, true);
      assert.equal(slot.innerHTML, '');
    }
  },
  async offline() {
    // No list yet and the server cannot be reached: say so, never an empty panel, and offer a retry.
    const { slot, mount, handlers } = fakeSlot();
    let fail = true;
    await attachCardPreviews(mount, { ...card, number: 20 }, { api: async () => { if (fail) throw Object.assign(new Error('down'), { status: 0 }); return found([preview()]); } });
    assert.equal(slot.hidden, false);
    assert.ok(slot.innerHTML.includes('Could not load the previews') && slot.innerHTML.includes('data-preview-retry'));
    assert.equal(slot.innerHTML, offlineHTML());
    fail = false;
    handlers.click({ target: { closest: (selector) => selector === '[data-preview-retry]' ? {} : null } });
    await wait(); await wait();
    assert.ok(slot.innerHTML.includes('Ready'));
    // With a list shown, a failed action and refresh keep it and say so.
    fail = true;
    handlers.click({ target: { closest: (selector) => selector === '[data-preview-stop]' ? { dataset: { previewStop: 'pv_0123456789abcdef' }, closest: () => null } : null } });
    await wait(); await wait();
    assert.ok(slot.innerHTML.includes('Ready') && slot.innerHTML.includes('Could not refresh the previews'));
  },
  async stop() {
    const { slot, mount, handlers } = fakeSlot();
    const posts = [];
    let state = 'ready';
    const api = async (url, options) => {
      if (options?.method) { posts.push([url, options.method, options.body]); state = url.endsWith('/stop') ? 'stopped' : 'ready'; return preview({ state }); }
      return found([preview({ state, url: '/p/pv_0123456789abcdef/' })]);
    };
    await attachCardPreviews(mount, { ...card, number: 21 }, { api });
    handlers.click(clickOn('previewStop', 'pv_0123456789abcdef'));
    await wait(); await wait();
    assert.deepEqual(posts, [['/api/previews/pv_0123456789abcdef/stop', 'POST', {}]]);
    assert.ok(slot.innerHTML.includes('Stopped. The files are kept') && slot.innerHTML.includes('data-preview-reopen'));
    handlers.click(clickOn('previewReopen', 'pv_0123456789abcdef'));
    await wait(); await wait();
    assert.deepEqual(posts[1], ['/api/previews/pv_0123456789abcdef/open-again', 'POST', {}]);
    assert.ok(slot.innerHTML.includes('Served again.') && slot.innerHTML.includes('data-preview-stop'));
  },
  async refusal() {
    // The server's reason and next step, in its own words; a reload shows what is true now.
    assert.equal(refusalWords({ status: 409, payload: { error: 'This workspace is at its preview limit (25 open previews or 500 MiB).', reason: 'workspace_quota_full', next: 'Stop an older preview to free an open place.' } }), 'This workspace is at its preview limit (25 open previews or 500 MiB). Stop an older preview to free an open place.');
    assert.equal(refusalWords({ status: 404, payload: null }), 'This preview no longer exists, or you cannot open its card.');
    assert.equal(refusalWords(new Error('Failed to fetch')), 'That did not work. Check the connection and try again.');
    const { slot, mount, handlers } = fakeSlot();
    const api = async (url, options) => {
      if (options?.method) throw Object.assign(new Error('x'), { status: 409, payload: { error: 'This workspace is at its preview limit (25 open previews or 500 MiB).', next: 'Stop an older preview to free an open place.' } });
      return found([preview({ state: 'stopped', url: '/p/pv_0123456789abcdef/' })]);
    };
    await attachCardPreviews(mount, { ...card, number: 22 }, { api });
    handlers.click(clickOn('previewReopen', 'pv_0123456789abcdef'));
    await wait(); await wait();
    assert.ok(slot.innerHTML.includes('at its preview limit') && slot.innerHTML.includes('Stop an older preview'));
  },
  async rulesave() {
    const { slot, mount, handlers } = fakeSlot();
    const puts = [];
    const api = async (url, options) => { if (options?.method) { puts.push([url, options.method, options.body]); return {}; } return found([], { can_set_rule: true }); };
    await attachCardPreviews(mount, { ...card, number: 23 }, { api });
    const form = (stop, age) => ({ elements: { stop_days_after_pr: { value: stop }, max_age_days: { value: age } }, querySelectorAll: () => [], closest: () => null });
    const submit = (f) => { const event = { target: { closest: (s) => s === '[data-preview-rule-form]' ? f : null }, preventDefault() { event.prevented = true; } }; handlers.submit(event); return event; };
    // Bad numbers are caught before anything is sent, with the reason in words.
    for (const [stop, age] of [['0', ''], ['1.5', ''], ['', '-3'], ['x', '']]) {
      assert.equal(submit(form(stop, age)).prevented, true);
      assert.ok(slot.note.textContent.includes('whole days'));
    }
    await wait();
    assert.deepEqual(puts, []);
    submit(form(' 7 ', ''));
    await wait(); await wait();
    assert.deepEqual(puts, [['/api/previews/rules', 'PUT', { stop_days_after_pr: 7, max_age_days: null }]]);
    assert.ok(slot.innerHTML.includes('Saved the stop rule.'));
  },
  async redraw() {
    // A redraw of the same card repaints what the panel showed a moment ago, then refreshes;
    // another card's list is never shown, and nothing is kept between components.
    const shown = { key: 'atlas/30', html: cardPreviewsHTML(found([preview()])) };
    const first = fakeSlot();
    let release;
    const pending = attachCardPreviews(first.mount, { ...card, number: 30 }, { api: () => new Promise((done) => { release = done; }), previous: shown });
    assert.equal(first.slot.hidden, false);
    assert.ok(first.slot.innerHTML.includes('Ready'));
    release(found([]));
    await pending;
    assert.ok(first.slot.innerHTML.includes('No preview yet'));
    const other = fakeSlot();
    const later = attachCardPreviews(other.mount, { ...card, number: 31 }, { api: () => new Promise(() => {}), previous: shown });
    assert.equal(other.slot.hidden, true);
    assert.equal(other.slot.innerHTML, '');
    void later;
    // The repainted list stays, with a note, when the refresh cannot reach the server.
    const third = fakeSlot();
    third.slot.querySelector = (selector) => selector === '.run-note' ? third.slot.note : null;
    await attachCardPreviews(third.mount, { ...card, number: 30 }, { api: async () => { throw Object.assign(new Error('down'), { status: 0 }); }, previous: shown });
    assert.ok(third.slot.innerHTML.includes('Ready'));
    assert.equal(third.slot.note.textContent, 'Could not refresh the previews. Showing the last list.');
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
    const asking = cardPreviewsHTML(found(list, { can_delete: true }), '', 'pv_0123456789abcdef');
    assert.ok(asking.includes('data-preview-delete-confirm="pv_0123456789abcdef"') && asking.includes('data-preview-delete-cancel'));
    assert.ok(asking.includes('Delete the files of <b>home</b> (2 KiB, this card)?') && asking.includes('cannot be undone'));
    assert.ok(!asking.includes('data-preview-delete="pv_0123456789abcdef"'));
  },
  async deleteflow() {
    const { slot, mount, handlers } = fakeSlot();
    const sent = [];
    let deleted = false, left = false;
    const api = async (url, options) => {
      // The first delete leaves files behind; the second removes them.
      if (options?.method === 'DELETE') { sent.push([url, options.body]); deleted = true; left = sent.length === 1; return { files_remaining: left }; }
      return found([deleted ? preview({ state: 'stopped', reason: 'files_deleted', reason_text: 'A workspace owner deleted the files of this preview.', url: null, files_remaining: left }) : preview()], { can_delete: true });
    };
    await attachCardPreviews(mount, { ...card, number: 26 }, { api });
    handlers.click(clickOn('previewDelete', 'pv_0123456789abcdef'));
    assert.ok(slot.innerHTML.includes('cannot be undone'));
    handlers.click({ target: { closest: (selector) => selector === '[data-preview-delete-cancel]' ? {} : null } });
    assert.ok(!slot.innerHTML.includes('cannot be undone') && slot.innerHTML.includes('data-preview-delete="pv_0123456789abcdef"'));
    assert.deepEqual(sent, []);
    handlers.click(clickOn('previewDelete', 'pv_0123456789abcdef'));
    handlers.click(clickOn('previewDeleteConfirm', 'pv_0123456789abcdef'));
    await wait(); await wait();
    assert.deepEqual(sent, [['/api/previews/pv_0123456789abcdef', { confirm: 'pv_0123456789abcdef' }]]);
    assert.ok(slot.innerHTML.includes('Delete files again to finish.'));
    assert.ok(slot.innerHTML.includes('deleted the files') && !slot.innerHTML.includes('data-preview-open'));
    // The retry the note promises is there, and it finishes the job.
    assert.ok(slot.innerHTML.includes('>Finish deleting<') && slot.innerHTML.includes('data-preview-delete="pv_0123456789abcdef"'));
    handlers.click(clickOn('previewDelete', 'pv_0123456789abcdef'));
    assert.ok(slot.innerHTML.includes('that were left behind last time'));
    handlers.click(clickOn('previewDeleteConfirm', 'pv_0123456789abcdef'));
    await wait(); await wait();
    assert.equal(sent.length, 2);
    assert.ok(slot.innerHTML.includes('Deleted the files. People can no longer open this preview.'));
    assert.ok(!slot.innerHTML.includes('data-preview-delete='));
  },
  async stale() {
    const { slot, mount } = fakeSlot();
    const release = [];
    const pending = attachCardPreviews(mount, { ...card, number: 24 }, { api: () => new Promise((done) => { release.push(done); }) });
    slot.isConnected = false;
    release.forEach((done) => done(found([preview()])));
    await pending;
    assert.equal(slot.innerHTML, '');
  },
  panel() {
    // The details panel places the Previews section right after Runs, in both looks.
    for (const deck of [false, true]) {
      const html = renderDetails(card, { board: { slug: 'atlas' }, me: 'user:maya', deck });
      const runs = html.indexOf('data-card-runs'), previews = html.indexOf('data-card-previews');
      assert.ok(runs > 0 && previews > runs, `deck=${deck}`);
    }
  },
  async loads() {
    // Each show of the card loads its previews.
    const slot = { hidden: true, innerHTML: '', isConnected: true, addEventListener() {}, querySelector: () => null };
    const mount = { innerHTML: '', addEventListener() {}, removeEventListener() {}, querySelector: (selector) => selector === '[data-card-previews]' ? slot : null };
    const calls = [];
    const component = createDetails({ mount, api: async (url) => { calls.push(url); return url.startsWith('/api/previews') ? found([preview()]) : url.startsWith('/api/card-starts') ? [] : {}; } });
    await component.show({ ...card, number: 25 }, { board: { slug: 'atlas' }, me: 'user:maya' });
    await wait();
    assert.deepEqual(calls.filter((url) => url.startsWith('/api/previews')), ['/api/previews?card=25']);
    assert.ok(slot.innerHTML.includes('Ready'));
    component.stop();
  },
};
assert.ok(Object.hasOwn(cases, process.argv[2]), 'Unknown case');
await cases[process.argv[2]]();
console.log(`${process.argv[2]} PASS`);
