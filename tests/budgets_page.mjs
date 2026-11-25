import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const root = new URL('../commonplace/web/static/', import.meta.url);
const source = name => readFileSync(new URL(`${name}.js`, root), 'utf8');
const moduleURL = text => `data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
const apiURL = moduleURL(source('api'));
const formatURL = moduleURL(source('budget-format'));
const pointed = text => text.replace(/(['"])\.\/api\.js\1/g, () => JSON.stringify(apiURL)).replace(/(['"])\.\/budget-format\.js\1/g, () => JSON.stringify(formatURL));
const { pageHTML, mountBudgets, parseDraft, writeError, scopeRows } = await import(moduleURL(pointed(source('budgets'))));
const { formatMicros, parseAmount, windowText, budgetNeed, scopeLabel } = await import(formatURL);
const { renderNeeds } = await import('../commonplace/web/static/needs.js');

const WINDOW = { start: '2026-11-01T00:00:00Z', end: '2026-12-01T00:00:00Z', window: 'calendar_month', days: null };
const group = (extra, over = {}) => ({ runs: 4, cost_known_runs: 3, unknown_cost_runs: 1, token_unreported_runs: 0, input_tokens: 0, output_tokens: 0, cost_micros: { USD: 42_000_000 }, ...extra, ...over });
const costs = (over = {}) => ({
  window: { ...WINDOW, custom: false }, scope: 'workspace',
  workspace: group({}, { runs: 6, cost_micros: { USD: 45_500_000, EUR: 3_000_000 } }),
  by_agent: [group({ agent: 'conn_a1' })], by_project: [group({ project: 'demo' }), group({ project: null }, { cost_micros: {} })], by_board: [], by_card: [],
  unknown_cost: { runs: 2, listed: [] }, ...over,
});
const budget = (kind, key, over = {}) => ({ id: `bud_${kind}`, scope: { kind, key: key || null }, limit_micros: 50_000_000, currency: 'USD', unknown_runs_warn: null, window: WINDOW, spent_micros: 42_000_000, percent: 84, state: 'warning', unknown_cost_runs: 1, unknown_runs_high: false, other_currency_runs: 0, running_runs: 0, marks: ['warning'], updated_at: '2026-11-18T10:00:00Z', ...over });
const owned = (list = [budget('project', 'demo')]) => ({ window: { window: 'calendar_month', days: null }, budgets: list });
const ready = (over = {}) => ({ costs: costs(), budgets: owned(), names: { conn_a1: 'Build agent' }, ...over });
const text = html => html.replace(/<[^>]*>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ').trim();
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const fail = (status, message = 'x') => Object.assign(new Error(message), { status });

// A host that records its listeners; `press` runs the click handler for a matching selector.
function host() {
  const handlers = {}, h = { innerHTML: '', attrs: {}, setAttribute(k, v) { h.attrs[k] = v; }, addEventListener(kind, fn) { handlers[kind] = fn; } };
  h.press = (selector, dataset = {}) => handlers.click({ target: { closest: (s) => s.includes(selector) ? { dataset } : null } });
  h.submit = () => handlers.submit({ target: { closest: () => ({}) }, preventDefault() {} });
  return h;
}
// A fake hub. `answers` maps "METHOD path" to a value or a function; every call is recorded.
function hub(answers) {
  const calls = [];
  const read = (path, options = {}) => {
    const id = `${options.method || 'GET'} ${path.split('?')[0]}`;
    calls.push({ id, path, body: options.body, method: options.method || 'GET' });
    const answer = answers[id];
    const value = typeof answer === 'function' ? answer(options) : answer;
    return value instanceof Error ? Promise.reject(value) : Promise.resolve(value);
  };
  return { read, calls };
}
const base = (over = {}) => ({ 'GET /api/costs': costs(), 'GET /api/settings/budgets': owned(), 'GET /api/connections': { connections: [{ id: 'conn_a1', name: 'a1', display_name: 'Build agent' }] }, ...over });

const cases = {
  format() {
    assert.equal(formatMicros(0), '0.00');
    assert.equal(formatMicros(1_500_000), '1.50');
    assert.equal(formatMicros(123_456), '0.123456');
    assert.equal(formatMicros(1_000_500), '1.0005');
    assert.equal(formatMicros(10 ** 15), '1000000000.00');
    assert.equal(formatMicros(-1), 'unknown');
    assert.equal(formatMicros(null), 'unknown');
    for (const [input, micros] of [['50', 50_000_000], ['12.5', 12_500_000], ['0.000001', 1], [' 7.25 ', 7_250_000]]) assert.equal(parseAmount(input), micros, input);
    for (const bad of ['', '0', '0.0', '-5', '1e3', '1,5', 'abc', '1.0000001', '1234567890', null, undefined]) assert.equal(parseAmount(bad), null, String(bad));
    assert.equal(windowText(WINDOW), 'November 2026 (UTC)');
    assert.equal(windowText({ ...WINDOW, window: 'calendar_week' }), 'The week from Monday 2026-11-01 (UTC)');
    assert.equal(windowText({ ...WINDOW, window: 'calendar_day' }), '2026-11-01 (UTC)');
    assert.equal(windowText({ window: 'rolling_days', days: 30, start: '2026-10-19T00:00:00Z' }), 'The last 30 days (UTC)');
    assert.match(windowText({ ...WINDOW, custom: true }), /2026-11-01 to 2026-12-01/);
    assert.equal(windowText(undefined), 'the current window');
    assert.equal(scopeLabel({ kind: 'workspace', key: null }), 'The workspace');
    assert.equal(scopeLabel({ kind: 'agent', key: 'c' }, { c: 'Build agent' }), 'Agent Build agent');
  },
  owner() {
    const html = pageHTML(ready()), plain = text(html);
    for (const needle of ['Window: November 2026 (UTC).', 'Workspace', 'Agent connections', 'Agent Build agent', 'Projects', 'Project demo',
      '3.00 EUR · 45.50 USD', '42.00 USD', '1 run without a cost, not counted', '2 runs without a cost, not counted',
      'Warning · 84%', '42.00 of 50.00 USD spent', 'Warning at 40.00, new starts refused at 50.00', 'No project recorded',
      'No budget. New starts are never refused on this scope.', 'no cost reported'])
      assert.ok(plain.includes(needle), needle);
    assert.match(html, /data-budget-edit data-kind="project" data-key="demo">Change budget/);
    assert.match(html, /data-budget-clear data-kind="project" data-key="demo">Clear budget/);
    assert.match(html, /data-budget-edit data-kind="agent" data-key="conn_a1">Set a budget/);
    assert.match(html, /data-budget-edit data-kind="workspace" data-key="">Set a budget/);
    // A scope with no name cannot take a budget.
    assert.ok(!/data-budget-edit data-kind="project" data-key="">/.test(html));
    assert.doesNotMatch(html, /\sstyle=/);
    assert.doesNotMatch(plain, /orchestrator/i);
  },
  badges() {
    for (const [state, percent, label] of [['ok', 10, 'OK · 10%'], ['warning', 85, 'Warning · 85%'], ['limit', 120, 'Over budget · 120%']]) {
      const html = pageHTML(ready({ budgets: owned([budget('workspace', '', { state, percent })]) }));
      assert.match(html, new RegExp(`data-budget-state="${state}">${label}</span>`));
    }
    const html = pageHTML(ready({ budgets: owned([budget('project', 'demo', { state: 'limit', percent: 100, running_runs: 2, other_currency_runs: 3, unknown_runs_warn: 2, unknown_runs_high: true, unknown_cost_runs: 2 })]) }));
    const plain = text(html);
    for (const needle of ['2 runs are still running and will finish', '3 runs in a currency other than USD, not counted', '2 runs without a cost, not counted', '2 or more runs have no cost'])
      assert.ok(plain.includes(needle), needle);
  },
  member() {
    const html = pageHTML({ costs: costs({ scope: 'readable' }), budgets: null, budgetsError: fail(404), names: {} }), plain = text(html);
    assert.ok(plain.includes('Only the workspace owner can see budgets.'));
    assert.ok(plain.includes('You see only the projects you can read'));
    assert.ok(plain.includes('45.50 USD'));
    assert.doesNotMatch(html, /data-budget-(edit|clear|new|form)|<input|<select/);
    assert.doesNotMatch(plain, /Sign in to see this/);
  },
  explained() {
    // Nothing read at all: each part says why, and a failed read is never "signed out" or "no budgets".
    for (const error of [fail(500), fail(503), new Error('Request timed out: /api/costs')]) {
      const html = pageHTML({ costs: null, costsError: error, budgets: null, budgetsError: error }), plain = text(html);
      assert.ok(plain.includes('Could not read the spend.') && plain.includes('Could not read the budgets.'), plain);
      assert.ok(plain.includes('does not mean you are signed out'));
      assert.ok(!plain.includes('Sign in to see this') && !plain.includes('No budget is set'));
      assert.equal((html.match(/data-budget-retry/g) || []).length, 2);
    }
    assert.match(text(pageHTML({ costs: null, costsError: fail(403), budgets: null, budgetsError: fail(404) })), /Your access does not include this/);
    // Costs unreadable but the budgets are: they are listed with an honest "not shown", never as zero.
    const partial = text(pageHTML({ costs: null, costsError: fail(500), budgets: owned(), names: {} }));
    assert.ok(partial.includes('Could not read the spend.') && partial.includes('Spend could not be read just now, so it is not shown. It is not zero.') && partial.includes('Warning · 84%'));
    assert.ok(!partial.includes('Only the workspace owner'));
    // Nothing yet: an owner with no runs and no budgets still gets an explanation and the way to start.
    const fresh = text(pageHTML({ costs: costs({ workspace: group({}, { runs: 0, unknown_cost_runs: 0, cost_micros: {} }), by_agent: [], by_project: [], unknown_cost: { runs: 0, listed: [] } }), budgets: owned([]), names: {} }));
    assert.ok(fresh.includes('No run started in this window.') && fresh.includes('No budget is set. Starts are never refused for cost until you set one.') && fresh.includes('Set a budget'));
    // A real 401 on either read is signed out; nothing else is.
    for (const state of [{ costsError: fail(401) }, { budgetsError: fail(401), costs: costs() }]) assert.match(pageHTML(state), /Sign in to see this[\s\S]*href="\/app\/"/);
    assert.doesNotMatch(pageHTML({ costs: null, costsError: fail(403), budgetsError: fail(500) }), /Sign in to see this/);
  },
  escaping() {
    const hostile = '<img src=x onerror=alert(1)> $& $\' $` $1 "q"';
    const html = pageHTML({ costs: costs({ by_project: [group({ project: hostile })], by_agent: [group({ agent: hostile })] }), budgets: owned([budget('project', hostile)]), names: { [hostile]: hostile },
      notice: { kind: 'ok', text: hostile }, draft: { step: 'form', kind: 'project', key: hostile, limit: hostile, currency: hostile, unknown_runs_warn: null, error: hostile, fixed: true } });
    assert.doesNotMatch(html, /<img/);
    assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt; $&amp; $&#39; $` $1 &quot;q&quot;'));
    assert.match(writeError({ status: 400, message: hostile }), /^Not saved\. The hub refused it: <img/);
  },
  form() {
    const html = pageHTML(ready({ draft: { step: 'form', kind: 'project', key: 'demo', limit: '50.00', currency: 'USD', unknown_runs_warn: 5, fixed: true, error: '' } }));
    assert.match(html, /<form class="budget-form" data-budget-form/);
    assert.ok(text(html).includes('Limit for November 2026 (UTC)'));
    assert.match(html, /name="limit"[^>]*value="50.00"/);
    assert.match(html, /name="unknown"[^>]*value="5"/);
    assert.ok(text(html).includes('At 100% new starts are refused; running work finishes.'));
    // Not fixed: the scope can be picked.
    assert.match(pageHTML(ready({ draft: { step: 'form', kind: 'project', key: '', limit: '', currency: 'USD', unknown_runs_warn: null, error: '' } })), /<select name="kind">/);
    for (const [values, message] of [[{ kind: 'project', key: '', limit: '5', currency: 'USD' }, 'Name the agent connection or project'], [{ kind: 'project', key: 'a', limit: '0', currency: 'USD' }, 'amount above zero'],
      [{ kind: 'project', key: 'a', limit: '5', currency: 'us' }, 'three-letter code'], [{ kind: 'project', key: 'a', limit: '5', currency: 'USD', unknown: '0' }, 'whole number'], [{ kind: 'nonsense', key: 'a', limit: '5', currency: 'USD' }, 'Choose what the budget is for']])
      assert.ok(parseDraft(values).error?.includes(message), JSON.stringify(values));
    const good = parseDraft({ kind: 'workspace', key: 'ignored', limit: '12.5', currency: 'eur', unknown: '3' });
    assert.deepEqual([good.error, good.kind, good.key, good.micros, good.currency, good.unknown_runs_warn], [undefined, 'workspace', '', 12_500_000, 'EUR', 3]);
  },
  confirm() {
    const html = pageHTML(ready({ draft: { step: 'confirm', kind: 'project', key: 'demo', micros: 50_000_000, currency: 'USD', unknown_runs_warn: null, replaces: true, error: '' } })), plain = text(html);
    for (const needle of ['Confirm this budget', 'Budget for project demo, November 2026 (UTC): 50.00 USD', 'At 80% (40.00 USD) the workspace owner gets a warning.', 'a hard stop applies. New starts are refused; running work finishes.',
      'never added to the spend', 'This replaces the current budget'])
      assert.ok(plain.includes(needle), needle);
    assert.match(html, /data-budget-confirm-save>Confirm and save/);
    const clear = text(pageHTML(ready({ draft: { step: 'clear', kind: 'project', key: 'demo' } })));
    assert.ok(clear.includes('Clear this budget?') && clear.includes('no longer limited') && clear.includes('Confirm and clear'));
  },
  async mounting() {
    const h = host(), { read, calls } = hub(base());
    await mountBudgets(h, { read, timeoutMs: 1234 });
    assert.deepEqual(calls.map(c => c.id).sort(), ['GET /api/connections', 'GET /api/costs', 'GET /api/settings/budgets']);
    assert.ok(text(h.innerHTML).includes('Agent Build agent') && text(h.innerHTML).includes('Warning · 84%'));
    assert.equal(h.attrs['aria-busy'], 'false');
    // A non-owner: the budget route answers 404 and the connection list 403; spend is still read.
    const member = host();
    await mountBudgets(member, { read: hub(base({ 'GET /api/settings/budgets': fail(404), 'GET /api/connections': fail(403) })).read });
    assert.ok(text(member.innerHTML).includes('Only the workspace owner can see budgets.') && text(member.innerHTML).includes('45.50 USD'));
    // Failed reads say so, and retry reads again.
    let broken = true;
    const flaky = host(), api = hub(base({ 'GET /api/costs': () => broken ? fail(500) : costs(), 'GET /api/settings/budgets': () => broken ? fail(500) : owned() }));
    await mountBudgets(flaky, { read: api.read });
    assert.match(text(flaky.innerHTML), /Could not read the spend/);
    broken = false; flaky.press('[data-budget-retry]'); await flush();
    assert.match(text(flaky.innerHTML), /Window: November 2026/);
    // A late answer to an earlier read cannot replace a newer one.
    let release; const slow = host(); let first = true;
    const racing = (path, options) => path === '/api/costs' && first ? (first = false, new Promise(r => { release = () => r(costs({ scope: 'readable' })); })) : hub(base()).read(path, options);
    const mounted = mountBudgets(slow, { read: racing }); await flush();
    slow.press('[data-budget-retry]'); await flush();
    release(); await mounted; await flush();
    assert.doesNotMatch(text(slow.innerHTML), /only the projects you can read/);
  },
  async save() {
    const answers = base(), saved = [];
    answers['PUT /api/settings/budgets'] = (options) => { saved.push(options.body); return budget('project', 'demo', { limit_micros: options.body.limit_micros }); };
    const h = host(), { read, calls } = hub(answers);
    await mountBudgets(h, { read });
    h.press('[data-budget-edit]', { kind: 'project', key: 'demo' });
    assert.match(h.innerHTML, /name="limit"[^>]*value="50.00"/);
    // Nothing is written until the owner confirms.
    const filled = { kind: 'project', key: 'demo', limit: '60', currency: 'usd', unknown: '' };
    const again = host();
    await mountBudgets(again, { read, formValues: () => filled });
    again.press('[data-budget-edit]', { kind: 'project', key: 'demo' });
    again.submit();
    assert.ok(text(again.innerHTML).includes('Confirm this budget') && text(again.innerHTML).includes('60.00 USD'));
    assert.equal(calls.filter(c => c.method === 'PUT').length, 0);
    again.press('[data-budget-confirm-save]'); await flush();
    assert.deepEqual(saved, [{ scope: { kind: 'project', key: 'demo' }, limit_micros: 60_000_000, currency: 'USD', unknown_runs_warn: null }]);
    assert.ok(text(again.innerHTML).includes('Budget saved.'));
    assert.equal(calls.filter(c => c.id === 'GET /api/settings/budgets').length, 3, 'the budgets were read again after the save');
    // Back from the confirm returns to the form without writing; a workspace scope has no key in the body.
    const second = host(); let wrote = 0, body;
    await mountBudgets(second, { read: hub(base({ 'PUT /api/settings/budgets': (options) => { wrote++; body = options.body; return {}; } })).read, formValues: () => ({ kind: 'workspace', key: '', limit: '100', currency: 'USD', unknown: '' }) });
    second.press('[data-budget-edit]', { kind: 'workspace', key: '' }); second.submit(); second.press('[data-budget-back]');
    assert.match(second.innerHTML, /data-budget-form/); assert.equal(wrote, 0);
    second.press('[data-budget-cancel]');
    assert.doesNotMatch(second.innerHTML, /data-budget-form/);
    second.press('[data-budget-edit]', { kind: 'workspace', key: '' }); second.submit(); second.press('[data-budget-confirm-save]'); await flush();
    assert.deepEqual(body, { scope: { kind: 'workspace' }, limit_micros: 100_000_000, currency: 'USD', unknown_runs_warn: null });
  },
  async refused() {
    // A refused write keeps the old budget on screen, says it was not saved, and keeps what was typed.
    for (const [error, expected] of [[fail(400, 'invalid currency'), 'The currency must be a three-letter code such as USD.'], [fail(400, 'unknown agent connection'), 'No agent connection has that key.'],
      [fail(404), 'Only the workspace owner can change budgets.'], [fail(409, 'busy'), 'The hub was busy.'], [fail(500, 'budget request failed'), 'The hub refused it: budget request failed.'], [new Error('Request timed out'), 'may or may not have changed']]) {
      const h = host(), filled = { kind: 'project', key: 'demo', limit: '60', currency: 'USD', unknown: '' };
      await mountBudgets(h, { read: hub(base({ 'PUT /api/settings/budgets': error })).read, formValues: () => filled });
      h.press('[data-budget-edit]', { kind: 'project', key: 'demo' }); h.submit(); h.press('[data-budget-confirm-save]'); await flush();
      const plain = text(h.innerHTML);
      assert.ok(plain.includes('Not saved.') && plain.includes(expected), plain);
      if (error.status) assert.ok(plain.includes('The budget is unchanged.'));
      assert.ok(plain.includes('42.00 of 50.00 USD spent'), 'the old values are still shown');
      assert.ok(plain.includes('Confirm and save'), 'the owner can retry');
      assert.ok(!plain.includes('Budget saved.') && !plain.includes('Sign in to see this'));
    }
    // A real 401 on the write is signed out.
    const h = host();
    await mountBudgets(h, { read: hub(base({ 'PUT /api/settings/budgets': fail(401) })).read, formValues: () => ({ kind: 'project', key: 'demo', limit: '60', currency: 'USD', unknown: '' }) });
    h.press('[data-budget-edit]', { kind: 'project', key: 'demo' }); h.submit(); h.press('[data-budget-confirm-save]'); await flush();
    assert.match(h.innerHTML, /Sign in to see this/);
    assert.equal(writeError(fail(404), 'clear'), 'Not cleared. Only the workspace owner can change budgets.');
  },
  async clearing() {
    const deleted = [], answers = base({ 'DELETE /api/settings/budgets': (options) => { deleted.push(options); return { removed: 'bud_project' }; } });
    const h = host(), { read, calls } = hub(answers);
    await mountBudgets(h, { read });
    h.press('[data-budget-clear]', { kind: 'project', key: 'demo' });
    assert.ok(text(h.innerHTML).includes('Clear this budget?'));
    assert.equal(calls.filter(c => c.method === 'DELETE').length, 0, 'nothing is cleared before the confirm');
    h.press('[data-budget-clear-confirm]'); await flush();
    assert.equal(calls.find(c => c.method === 'DELETE').path, '/api/settings/budgets?kind=project&key=demo');
    assert.ok(text(h.innerHTML).includes('Budget cleared.'));
    // A workspace budget has no key; a failed clear says so and leaves the budget listed.
    const w = host(), api = hub(base({ 'GET /api/settings/budgets': owned([budget('workspace', '')]), 'DELETE /api/settings/budgets': fail(500, 'budget request failed') }));
    await mountBudgets(w, { read: api.read });
    w.press('[data-budget-clear]', { kind: 'workspace', key: '' }); w.press('[data-budget-clear-confirm]'); await flush();
    assert.equal(api.calls.find(c => c.method === 'DELETE').path, '/api/settings/budgets?kind=workspace');
    assert.ok(text(w.innerHTML).includes('Not cleared.') && text(w.innerHTML).includes('The budget is unchanged.') && text(w.innerHTML).includes('Warning · 84%'));
    // Already gone (404 on the delete) refreshes the list and says so.
    const g = host();
    await mountBudgets(g, { read: hub(base({ 'DELETE /api/settings/budgets': fail(404) })).read });
    g.press('[data-budget-clear]', { kind: 'project', key: 'demo' }); g.press('[data-budget-clear-confirm]'); await flush();
    assert.ok(text(g.innerHTML).includes('That budget was already gone.'));
  },
  needs() {
    const item = { kind: 'budget', budget: 'bud_1', scope: { kind: 'project', key: 'demo' }, reasons: ['warning'], percent: 85, spent_micros: 42_500_000, limit_micros: 50_000_000, currency: 'USD', unknown_cost_runs: 1, running_runs: 0, window: WINDOW, why: 'The project demo budget has spent 85% of its limit in this window.' };
    const html = renderNeeds({ needs: { budget_warnings: [item] } }), plain = text(html);
    assert.ok(html.includes('data-budget-item="bud_1"'));
    assert.ok(plain.includes('Project demo is at 85% of its budget'));
    assert.ok(plain.includes('42.50 of 50.00 USD spent in November 2026 (UTC)') && plain.includes('1 run without a cost, not counted.'));
    assert.match(html, /<a class="btn" href="\/app\/budgets.html">Review budget<\/a>/);
    assert.ok(plain.includes('1 item, 0 projects') && !plain.includes('Nothing needs you'));
    assert.doesNotMatch(html, /\[object|undefined|NaN|\{"kind"/);
    const over = text(renderNeeds({ needs: { budget_warnings: [{ ...item, reasons: ['warning', 'limit'], percent: 100, running_runs: 2, scope: { kind: 'workspace', key: null } }] } }));
    assert.ok(over.includes('The workspace has used its whole budget') && over.includes('New starts are refused; running work finishes.') && over.includes('2 runs are still running and will finish.'));
    assert.ok(!renderNeeds({ needs: { budget_warnings: [item] }, filter: 'approvals' }).includes('data-budget-item'));
    assert.ok(renderNeeds({ needs: { budget_warnings: [item] }, filter: 'decisions' }).includes('data-budget-item'));
    // A malformed item is not drawn; a hostile one is escaped; none changes a clean Needs me.
    assert.ok(!renderNeeds({ needs: { budget_warnings: [{ kind: 'other' }, null, { kind: 'budget' }] } }).includes('data-budget-item'));
    const hostile = '<img src=x onerror=alert(1)>';
    assert.doesNotMatch(renderNeeds({ needs: { budget_warnings: [{ ...item, budget: hostile, scope: { kind: 'project', key: hostile }, currency: hostile }] } }), /<img/);
    assert.ok(renderNeeds({}).includes('Nothing needs you right now.') && !renderNeeds({}).includes('Spend'));
    const need = budgetNeed({ kind: 'budget', scope: { kind: 'card', key: 7 }, reasons: ['warning'], why: 'plain words' });
    assert.deepEqual([need.title, need.detail], ['Card #7 is at ?% of its budget', 'plain words']);
  },
  rows() {
    const rows = scopeRows(costs(), [budget('workspace', ''), budget('card', '12')], {});
    assert.deepEqual(rows.map(r => `${r.kind}:${r.key ?? '-'}`), ['workspace:', 'agent:conn_a1', 'project:demo', 'project:-', 'card:12']);
    assert.ok(rows[0].budget && rows[4].spend === null);
  },
};

const name = process.argv[2];
assert.ok(cases[name], `unknown case ${name}`);
await cases[name]();
