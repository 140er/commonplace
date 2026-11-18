import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = name => readFileSync(new URL(`../commonplace/web/static/${name}.js`, import.meta.url), 'utf8');
const moduleURL = text => `data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
const apiURL = moduleURL(source('api'));
const { statusHTML, sectionHTML, failureHTML, mountStatus } = await import(moduleURL(source('status').replace(/(['"])\.\/api\.js\1/g, () => JSON.stringify(apiURL))));

const cost = [{ currency: 'USD', micros: 123456, display: '0.123456' }];
const page = (over = {}) => ({
  generated_at: '2026-11-18T10:00:00Z', viewer: { role: 'owner' },
  skills: { state: 'ready', notice: 'Skill text is quoted data.', more: 0, items: [
    { id: 's1', name: 'Release checks', version: 2, description: 'Ship a release safely', scope: ['Whole workspace'] },
    { id: 's2', name: 'Bare', version: 1, description: '', description_note: 'This skill has no description line.', scope: ['Project work'] }] },
  bridge: { state: 'ready', last_seen_note: 'Last seen is when the bridge last received an event. It is not proof that anything is running now.', installs: [
    { id: 'b1', platform: 'fake', label: 'Team chat', enabled: true, reason: '', tested_at: '2026-11-17T09:00:00Z', mapped_channels: 2, last_seen_at: '2026-11-18T09:30:00Z', last_seen_text: 'x' },
    { id: 'b2', platform: 'fake', label: 'Other', enabled: false, reason: 'Switched off by the workspace owner.', tested_at: null, mapped_channels: 0, last_seen_at: null, last_seen_text: 'x' }] },
  meetings: { state: 'ready', overview: { states: { done: 1 }, meetings: 1, connections: 2, connections_enabled: 1 }, meetings: [{ id: 'm1', provider: 'fake', state: 'done', start_at: '2026-11-17T10:00:00Z', started_at: '2026-11-17T10:01:00Z', ended_at: '2026-11-17T10:30:00Z', counts: { notes: 3 } }] },
  costs: { state: 'ready', period: 'The current UTC calendar month, from 2026-11-01T00:00:00Z to 2026-12-01T00:00:00Z (end excluded)', scope: 'workspace', scope_text: 'Every run in this workspace.',
    totals: { runs: 3, cost, unknown_cost_runs: 1 }, unknown_text: 'A run whose machine reported no cost is counted as unknown and is not counted as zero.', by_project: [{ project: 'demo', runs: 3, unknown_cost_runs: 1, cost }] },
  ...over,
});
const text = html => html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

const cases = {
  ready() {
    const html = statusHTML(page()), plain = text(html);
    for (const needle of ['Release checks', 'Ship a release safely', 'Scope: Whole workspace', 'This skill has no description line.', 'Scope: Project work',
      'Team chat', 'Off. Switched off by the workspace owner.', 'last seen: no event yet', 'never tested', '2 mapped channels',
      'Done', '3 notes', 'Period: The current UTC calendar month', 'Scope: Every run in this workspace.', '0.123456 USD', '1 with unknown cost', 'not counted as zero', 'By project', 'demo'])
      assert.ok(plain.includes(needle), needle);
    assert.match(plain, /last seen: 2026-11-18 09:30 UTC/);
    assert.match(plain, /not that a process is running now/);
    assert.doesNotMatch(plain, /\b(online|connected|healthy|alive)\b/i);
    assert.doesNotMatch(html, /<(input|textarea|select|form)\b/);
    assert.doesNotMatch(html, /method=|data-status-save|PUT|PATCH/);
  },
  explained() {
    const reason = 'Because the owner has not switched this on, so there is nothing to show yet.';
    for (const name of ['skills', 'bridge', 'meetings', 'costs']) for (const state of ['empty', 'off', 'restricted', 'error']) {
      const html = sectionHTML(name, { state, reason });
      assert.ok(html.includes(reason), `${name}/${state}`);
      assert.ok(text(html).length > 40, `${name}/${state} is not an empty panel`);
      assert.match(html, new RegExp(`data-state="${state}"`));
    }
    // An empty cost summary still states the period and scope.
    const empty = sectionHTML('costs', { state: 'empty', reason, period: 'The current UTC calendar month, from a to b (end excluded)', scope: 'workspace', scope_text: 'Every run in this workspace.' });
    assert.ok(text(empty).includes('Period: The current UTC calendar month') && text(empty).includes('Scope: Every run in this workspace.'));
  },
  unknown() {
    for (const value of [undefined, null, {}, { state: 'mystery' }, 'text', { state: 'ready' }, { state: 'off' }]) {
      const html = sectionHTML('bridge', value), plain = text(html);
      assert.match(plain, /Reload to try again/, JSON.stringify(value));
      assert.ok(plain.length > 60, JSON.stringify(value));
    }
    assert.match(text(statusHTML(null)), /Skills[\s\S]*Chat bridge[\s\S]*Meeting notes[\s\S]*Cost summary/);
    assert.match(statusHTML({}), /unknown role/);
  },
  escaping() {
    const hostile = '<img src=x onerror=alert(1)> $& $\' $` $1 "q"';
    const data = page({ skills: { state: 'ready', notice: hostile, more: 0, items: [{ id: 'x', name: hostile, version: 1, description: hostile, scope: [hostile] }] },
      bridge: { state: 'off', reason: hostile }, costs: { state: 'error', reason: hostile } });
    const html = statusHTML(data);
    assert.doesNotMatch(html, /<img/);
    assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt; $&amp; $&#39; $` $1 &quot;q&quot;'));
  },
  failures() {
    assert.match(failureHTML({ status: 401 }), /Sign in to see this[\s\S]*href="\/app\/"/);
    for (const error of [new Error('Request timed out: /api/status'), { status: 500 }, { status: 503 }, undefined]) {
      const html = failureHTML(error);
      assert.match(text(html), /does not mean you are signed out/);
      assert.match(html, /data-status-retry/);
      assert.doesNotMatch(html, /Sign in to see this/);
    }
    assert.match(text(failureHTML({ status: 403 })), /does not include this page/);
  },
  async mounting() {
    const reads = [];
    let click, release;
    const host = { innerHTML: '', attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }, addEventListener(kind, fn) { if (kind === 'click') click = fn; } };
    let answer = () => Promise.reject(new Error('Request timed out: /api/status'));
    await mountStatus(host, { read: (path, options) => { reads.push([path, options]); return answer(); }, timeoutMs: 1234 });
    assert.deepEqual(reads[0], ['/api/status', { timeoutMs: 1234 }]);
    assert.match(host.innerHTML, /does not mean you are signed out/);
    assert.equal(host.attrs['aria-busy'], 'false');
    answer = () => Promise.resolve(page());
    click({ target: { closest: (selector) => selector === '[data-status-retry]' ? {} : null } });
    await flush();
    assert.match(host.innerHTML, /Capabilities and status/);
    assert.equal(reads.length, 2);
    // A late answer to an earlier read cannot replace a newer one.
    answer = () => new Promise((resolve) => { release = resolve; });
    click({ target: { closest: () => ({}) } });
    await flush();
    answer = () => Promise.resolve(page({ viewer: { role: 'member' } }));
    click({ target: { closest: () => ({}) } });
    await flush();
    release(page({ viewer: { role: 'owner' } }));
    await flush();
    assert.match(host.innerHTML, /Viewing as member/);
  },
};

const name = process.argv[2];
assert.ok(cases[name], `unknown case ${name}`);
await cases[name]();
