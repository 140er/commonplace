import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = name => readFileSync(new URL(`../commonplace/web/static/${name}.js`, import.meta.url), 'utf8');
const moduleURL = text => `data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
const apiURL = moduleURL(source('api'));
const { factsHTML, placesHTML, sourcesHTML, linksHTML, confirmHTML, replaceFormHTML, failureHTML, refusal, mountOrganise } =
  await import(moduleURL(source('organise').replace(/(['"])\.\/api\.js\1/g, () => JSON.stringify(apiURL))));

const text = html => html.replace(/<\/?(?:code|time|strong|a|span|pre)\b[^>]*>/g, '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const apiError = (status, payload) => Object.assign(new Error(payload?.error || `Request failed (${status})`), { status, payload });
const fact = { id: 't1', subject: 'Avery', predicate: 'works_at', object: 'Acme', valid_from: '2024-01-01', valid_to: null, wing: 'shared', sensitivity: 'internal', sources: [{ source_thread: 'mcp:t1', source_seq_start: 3, source_file: null }] };
const old = { ...fact, id: 't0', object: 'Initech', valid_to: '2024-01-01' };
const page = (facts, extra = {}) => ({ entity: null, facts, total: facts.length, offset: 0, limit: 100, ...extra });
const tunnel = { id: 'tun1', source: { wing: 'shared', room: 'alpha' }, target: { wing: 'shared', room: 'beta' }, label: 'same topic', access_count: 2, created_at: '2026-11-01T10:00:00Z' };

const cases = {
  facts() {
    const html = factsHTML(page([fact, old])), plain = text(html);
    for (const needle of ['Avery works at Acme', 'From 2024-01-01 · Current', 'Wing shared', 'internal', 'source thread: mcp:t1 · source seq start: 3', 'Avery works at Initech', 'From 2024-01-01 · Ended 2024-01-01', 'Showing 1–2 of 2']) assert.ok(plain.includes(needle), needle);
    // Only the current fact can be ended or replaced; an ended one stays as read-only history.
    assert.equal((html.match(/data-organise-end=/g) || []).length, 1);
    assert.equal((html.match(/data-organise-replace=/g) || []).length, 1);
    assert.match(text(factsHTML(page([{ ...fact, valid_from: null, sources: [] }]))), /Start date not recorded.*No source recorded for this fact/);
    // Empty and unknown answers say why; none renders empty.
    assert.match(text(factsHTML(page([]))), /No fact is stored yet, or none your access includes/);
    assert.match(text(factsHTML(page([], { entity: 'Nobody' }))), /No fact mentions Nobody that your access includes.*matched exactly/);
    for (const bad of [null, undefined, {}, { facts: 'x' }]) assert.match(text(factsHTML(bad)), /did not understand the answer/, JSON.stringify(bad));
    assert.match(factsHTML(page([fact], { total: 250, limit: 100 })), /data-organise-page="100"/);
  },
  sources() {
    const places = { wings: [{ wing: 'shared', rooms: [{ room: 'alpha', count: 2 }, { room: 'diary', count: 1 }] }] };
    const plain = text(placesHTML(places, 'shared', 'alpha'));
    assert.ok(plain.includes('shared') && plain.includes('alpha 2 stored texts') && plain.includes('Diary entries 1 stored text'));
    assert.match(placesHTML(places), /href="\/app\/organise\.html\?view=sources&amp;wing=shared&amp;room=diary"/);
    assert.match(text(placesHTML({ wings: [] })), /Nothing is stored yet/);
    assert.match(text(placesHTML(null)), /did not understand/);
    const data = { drawers: [{ id: 'd1', preview: 'Start of  the words\nline two', filed_at: '2026-11-02T10:00:00Z', added_by: 'agent', source_file: '/x.md' }], total: 1, offset: 0, limit: 20 };
    const html = sourcesHTML(places, data, 'shared', 'alpha');
    assert.ok(html.includes('Start of  the words\nline two'));
    assert.match(html, /data-organise-read="d1"/);
    assert.match(text(html), /saved 2026-11-02 10:00 UTC · by agent · from \/x\.md/);
    // The whole text replaces the preview, byte for byte, with no replacement-pattern expansion.
    const whole = 'All of it $& $1 <b>x</b>\n  indented';
    const opened = sourcesHTML(places, data, 'shared', 'alpha', { d1: { text: whole } });
    assert.ok(opened.includes('All of it $&amp; $1 &lt;b&gt;x&lt;/b&gt;\n  indented'));
    assert.doesNotMatch(opened, /data-organise-read|<b>/);
    assert.match(text(sourcesHTML(places, { drawers: [], total: 0, offset: 0, limit: 20 }, 'shared', 'empty')), /Nothing is stored in shared \/ empty that your access includes.*may be empty, or it may not exist/);
    assert.match(text(sourcesHTML(places, null, '', '')), /Pick a room above/);
    assert.match(text(sourcesHTML(places, { nope: 1 }, 'shared', 'alpha')), /did not understand/);
    assert.match(sourcesHTML(places, { ...data, total: 45, limit: 20 }, 'shared', 'alpha'), /Later/);
  },
  links() {
    const html = linksHTML({ tunnels: [tunnel], total: 1, shown: 1, hallways: { rows: [], total: 7 } }), plain = text(html);
    for (const needle of ['shared / alpha ↔ shared / beta', 'Label: same topic', 'Used 2 times', 'made 2026-11-01 10:00 UTC', '7 automatic links', 'made by memory itself', 'Review the link']) assert.ok(plain.includes(needle), needle);
    assert.match(html, /data-organise-unlink="0"/);
    assert.match(text(linksHTML({ tunnels: [], total: 0, shown: 0, hallways: null, hallway_note: 'Your access does not include this.' })), /No links between places have been made yet.*Your access does not include this/);
    assert.match(text(linksHTML(null)), /did not understand/);
  },
  confirm() {
    const preview = { action: 'supersede', fact, new_object: 'Init$&ech\n<b>', at: '2026-11-18T10:00:00Z', lines: ['Ends this fact on 2026-11-18.', 'No stored memory text is deleted or changed.'] };
    const html = confirmHTML({ preview }), plain = text(html);
    for (const needle of ['Replace this fact', 'Avery · works at · Acme', 'New value, exactly as typed', 'Ends this fact on 2026-11-18.', 'No stored memory text is deleted or changed.', 'Confirm: replace this fact', 'Cancel, change nothing']) assert.ok(plain.includes(needle), needle);
    assert.ok(html.includes('Init$&amp;ech\n&lt;b&gt;'));
    assert.match(html, /data-organise-confirm\b/);
    assert.match(html, /data-organise-cancel/);
    assert.match(confirmHTML({ preview, busy: true }), /Working…/);
    assert.match(text(confirmHTML({ preview, error: { message: 'No longer matches.', next: 'Reload.' } })), /No longer matches\. Reload\./);
    const link = confirmHTML({ preview: { action: 'link_remove', source: tunnel.source, target: tunnel.target, label: 'same topic', lines: ['Removes the link record.'] } });
    assert.match(text(link), /Remove this link.*shared \/ alpha ↔ shared \/ beta \(label: same topic\)/);
    assert.match(text(replaceFormHTML(fact, { message: 'Bad value', next: 'Fix it.' })), /Replace this fact.*Bad value Fix it\./);
  },
  escaping() {
    const bad = '<img src=x onerror=alert(1)> $& $\' $` $1 "q"';
    const hostile = { ...fact, subject: bad, predicate: bad, object: bad, wing: bad, sensitivity: bad, sources: [{ source_thread: bad }] };
    const html = [factsHTML(page([hostile], { entity: bad })), placesHTML({ wings: [{ wing: bad, rooms: [{ room: bad, count: 1 }] }] }, bad, bad),
      sourcesHTML({ wings: [] }, { drawers: [{ id: bad, preview: bad, filed_at: bad, added_by: bad, source_file: bad }], total: 1, offset: 0, limit: 20 }, bad, bad),
      linksHTML({ tunnels: [{ id: bad, source: { wing: bad, room: bad }, target: { wing: bad, room: bad }, label: bad }], total: 1, shown: 1, hallways: null, hallway_note: bad }, { form: { source_wing: bad, label: bad } }),
      confirmHTML({ preview: { action: 'invalidate', fact: hostile, lines: [bad] } }), replaceFormHTML(hostile)].join('');
    assert.doesNotMatch(html, /<img/);
    assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt; $&amp; $&#39; $` $1 &quot;q&quot;'));
  },
  failures() {
    assert.match(failureHTML({ status: 401 }), /Sign in to see this[\s\S]*href="\/app\/"/);
    assert.match(text(failureHTML({ status: 403, payload: { error: 'Your access does not include this: needs scope kg:read.' } })), /does not include this.*needs scope kg:read/);
    assert.doesNotMatch(failureHTML({ status: 403 }), /data-organise-retry/);
    for (const error of [new Error('Request timed out: /api/organise/facts'), { status: 500 }, { status: 503, payload: { error: 'Memory is busy or paused just now, so nothing was read or changed.' } }, undefined]) {
      const html = failureHTML(error, 'this view');
      assert.match(html, /data-organise-retry/);
      assert.doesNotMatch(html, /Sign in to see this/);
      assert.ok(text(html).length > 40);
    }
    assert.match(text(failureHTML(new Error('x'))), /does not mean you are signed out/);
    assert.match(refusal(apiError(409, { error: 'No longer matches. Nothing was changed.' })).next, /Reload/);
    assert.match(refusal(apiError(401, {})).message, /signed out, so nothing was changed/);
    const timeout = refusal(new Error('Request timed out'));
    assert.match(timeout.message, /cannot tell whether anything changed/);
    assert.match(timeout.next, /not mean you are signed out/);
  },
  async mounting() {
    const calls = [], handlers = {};
    const host = { innerHTML: '', attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }, addEventListener(kind, fn) { handlers[kind] = fn; } };
    let facts = [fact], answers = [];
    const read = (path, options = {}) => {
      calls.push([path, options.method || 'GET', options.body]);
      if (path.startsWith('/api/organise/facts?')) return Promise.resolve(page(facts));
      if (path.startsWith('/api/organise/facts/')) { const next = answers.shift(); return next instanceof Error ? Promise.reject(next) : Promise.resolve(next); }
      return Promise.reject(new Error(`unexpected ${path}`));
    };
    const target = (selector, attr) => ({ closest: (s) => (s === selector ? { getAttribute: (n) => (n === attr ? '0' : null), dataset: {} } : null) });
    await mountOrganise(host, { read, location: { search: '?view=facts' } });
    assert.match(text(host.innerHTML), /Avery works at Acme/);
    assert.equal(host.attrs['aria-busy'], 'false');
    // End: the preview is a request that changes nothing; cancel sends no further request.
    answers = [{ preview: { action: 'invalidate', fact, ended: '2026-11-18', lines: ['Marks this fact as no longer true from 2026-11-18.'] }, effect_id: 'e1' }];
    handlers.click({ target: target('[data-organise-end]', 'data-organise-end') });
    await flush();
    assert.deepEqual(calls.at(-1), ['/api/organise/facts/invalidate', 'POST', { subject: 'Avery', predicate: 'works_at', object: 'Acme', wing: 'shared' }]);
    assert.match(text(host.innerHTML), /End this fact.*Marks this fact as no longer true/);
    const sent = calls.length;
    handlers.click({ target: target('[data-organise-cancel]') });
    assert.equal(calls.length, sent);
    assert.doesNotMatch(host.innerHTML, /data-organise-confirm\b/);
    assert.match(text(host.innerHTML), /Avery works at Acme/);
    // A refused confirm keeps the old state on screen, says so, and a retry then works.
    answers = [{ preview: { action: 'invalidate', fact, ended: '2026-11-18', lines: ['x'] }, effect_id: 'e1' }, apiError(409, { error: 'This change no longer matches what was shown. Nothing was changed.' }), { done: true }];
    handlers.click({ target: target('[data-organise-end]', 'data-organise-end') });
    await flush();
    handlers.click({ target: target('[data-organise-confirm]') });
    await flush();
    assert.deepEqual(calls.at(-1)[2], { subject: 'Avery', predicate: 'works_at', object: 'Acme', wing: 'shared', confirm: true, effect_id: 'e1', ended: '2026-11-18' });
    assert.match(text(host.innerHTML), /no longer matches what was shown\. Nothing was changed\. Reload the page/);
    assert.match(host.innerHTML, /data-organise-confirm\b/);
    facts = [{ ...fact, valid_to: '2026-11-18' }];
    handlers.click({ target: target('[data-organise-confirm]') });
    await flush();
    assert.match(text(host.innerHTML), /End this fact: done\. Nothing was deleted/);
    assert.match(text(host.innerHTML), /Ended 2026-11-18/);
    assert.doesNotMatch(host.innerHTML, /data-organise-end/);
    // A timeout on a confirm never says signed out and never claims nothing changed.
    facts = [fact];
    await mountOrganise(host, { read, location: { search: '' } });
    answers = [{ preview: { action: 'invalidate', fact, ended: '2026-11-18', lines: ['x'] }, effect_id: 'e2' }, new Error('Request timed out: /api/organise/facts/invalidate')];
    handlers.click({ target: target('[data-organise-end]', 'data-organise-end') });
    await flush();
    handlers.click({ target: target('[data-organise-confirm]') });
    await flush();
    assert.match(text(host.innerHTML), /cannot tell whether anything changed.*Reload to see what is stored/);
    assert.doesNotMatch(host.innerHTML, /Sign in/);
  },
  async denied() {
    const host = { innerHTML: '', setAttribute() {}, addEventListener() {} };
    await mountOrganise(host, { read: () => Promise.reject(apiError(403, { error: 'Your access does not include this: needs scope kg:read.' })), location: { search: '?view=facts' } });
    assert.match(text(host.innerHTML), /Organise memory[\s\S]*does not include this.*needs scope kg:read/);
    assert.doesNotMatch(host.innerHTML, /data-organise-end|<form/);
    await mountOrganise(host, { read: () => Promise.reject(apiError(401, {})), location: { search: '?view=links' } });
    assert.match(text(host.innerHTML), /Sign in to see this/);
    // A busy memory is an error with a retry, not an empty list.
    await mountOrganise(host, { read: () => Promise.reject(apiError(503, { error: 'Memory is busy or paused just now, so nothing was read or changed. Try again shortly.' })), location: { search: '?view=sources&wing=shared&room=alpha' } });
    assert.match(text(host.innerHTML), /Memory is busy or paused just now/);
    assert.match(host.innerHTML, /data-organise-retry/);
    assert.doesNotMatch(text(host.innerHTML), /Nothing is stored/);
    // An unknown view falls back to facts; the links view opens with its own words.
    const links = { tunnels: [], total: 0, shown: 0, hallways: null };
    await mountOrganise(host, { read: () => Promise.resolve(links), location: { search: '?view=links' } });
    assert.match(text(host.innerHTML), /No links between places have been made yet/);
  },
};

const name = process.argv[2];
assert.ok(cases[name], `unknown case ${name}`);
await cases[name]();
