import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, recapHTML, sinksHTML } from '../commonplace/web/static/activity.js';

const threads = Array.from({ length: 31 }, (_, n) => ({ id: `thread_${n.toString(16).padStart(24, '0')}`, model: n < 2 ? 'other' : 'gpt-6.1-sol', status: 'completed', creator: 'user', seconds: 3600 - n, runs: 1, last: 1795089600 }));
const snapshot = { coverage: {}, threads, models: [], daily: [], metrics: {}, sinks: [], spans: [], handoffs: [], collected_at: 1795089600, window_start: 1795003200, window_end: 1795089600 };

function harness({ api = null } = {}) {
  let available = threads, sources = [{ id: 'source', enabled: true }], failure = null, listingFailure = null;
  const nodes = new Map();
  const node = name => {
    if (!nodes.has(name)) nodes.set(name, { innerHTML: '', value: '', dataset: {}, disabled: false, attributes: {}, setAttribute(key, value) { this.attributes[key] = value; }, focus() {}, querySelector() { return null; } });
    return nodes.get(name);
  };
  const listeners = new Map(), requests = [];
  const root = { classList: { add() {}, remove() {} }, contains: () => true, ownerDocument: { hidden: false, addEventListener() {}, removeEventListener() {} }, querySelector: selector => node(selector.match(/data-ca-([^\]=" ]+)/)?.[1]), querySelectorAll: () => [], addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener() {} };
  const controller = mount({ root, api: async (url, options) => {
    requests.push(url);
    if (api) return api(url, options);
    if (url === '/api/compute/activity') {
      if (listingFailure) throw listingFailure;
      return { hosts: sources };
    }
    if (url.endsWith('/consent')) {
      sources = sources.map(row => ({ ...row, enabled: options.body.enabled }));
      return { enabled: options.body.enabled };
    }
    if (failure) throw failure;
    const query = new URL(url, 'https://example.test').searchParams;
    const selected = available.filter(row => !query.get('model') || row.model === query.get('model'));
    const offset = Number(query.get('offset'));
    return { snapshot: { ...snapshot, threads: selected.slice(offset, offset + 25) }, thread_models: ['other', 'gpt-6.1-sol'], page: { total: selected.length, next: offset + 25 < selected.length ? offset + 25 : null } };
  } });
  const settled = () => new Promise(resolve => setImmediate(resolve));
  return { node, requests, stop: () => controller.stop(), settled, retain: rows => { available = rows; },
    replaceSource: id => { sources = [{ id, enabled: true }]; }, fail: error => { failure = error; },
    consentEnabled: enabled => { sources = sources.map(row => ({ ...row, enabled })); }, failListing: error => { listingFailure = error; },
    click: (key, value = '') => listeners.get('click')({ target: { closest: () => ({ dataset: key === 'tab' ? { caTab: value } : {}, hasAttribute: attr => attr === `data-ca-${key}` }) } }),
    model: value => { node('model').value = value; listeners.get('change')({ target: node('model') }); },
  };
}

function consentHarness() {
  const state = { enabled: false, writes: [], commits: [], reads: [], holdReload: false };
  const h = harness({ api: async (url, options) => {
    if (url.endsWith('/consent')) return new Promise((resolve, reject) => {
      const enabled = options.body.enabled;
      state.writes.push({ enabled, commit() { state.enabled = enabled; state.commits.push(enabled); resolve({ enabled }); }, fail: reject });
    });
    if (url === '/api/compute/activity') {
      const result = () => ({ hosts: [{ id: 'source', enabled: state.enabled }] });
      if (state.holdReload) return new Promise((resolve, reject) => state.reads.push({ release: () => resolve(result()), fail: reject }));
      return result();
    }
    return state.enabled ? { state: 'ready', snapshot, thread_models: ['other', 'gpt-6.1-sol'], page: { total: threads.length, next: 25 } } : { state: 'not_enabled', snapshot: null };
  } });
  return { ...h, state };
}

test('a delayed overlapping Allow cannot commit after an acknowledged Revoke', async () => {
  const h = consentHarness(), { state } = h;
  try {
    await h.settled();
    assert.match(h.node('body').innerHTML, /Allow aggregate reporting/);
    h.click('consent'); h.click('consent'); await h.settled();
    const allowDisabled = /data-ca-consent[^>]*disabled/.test(h.node('body').innerHTML);
    const postBusy = h.node('body').attributes['aria-busy'];
    const first = state.writes[0]; assert.ok(first);
    state.holdReload = true; first.commit(); await h.settled();
    const allowReloadDisabled = h.node('disable').disabled;
    const allowReload = state.reads.at(-1); assert.ok(allowReload);
    state.holdReload = false; allowReload.release(); await h.settled();
    h.click('disable'); await h.settled();
    const revoke = state.writes.find(row => row.enabled === false); assert.ok(revoke);
    const revokePostDisabled = h.node('disable').disabled;
    state.holdReload = true; revoke.commit(); await h.settled();
    const revokeReloadDisabled = h.node('disable').disabled;
    const revokeReload = state.reads.at(-1); assert.notEqual(revokeReload, allowReload);
    state.holdReload = false; revokeReload.release(); await h.settled();
    for (const late of state.writes.filter(row => row.enabled && row !== first)) { late.commit(); await h.settled(); }
    h.click('refresh'); await h.settled();
    assert.deepEqual(state.commits, [true, false], 'An older Allow wrote after acknowledged revocation');
    assert.equal(state.enabled, false);
    assert.match(h.node('body').innerHTML, /Allow aggregate reporting/);
    assert.equal(allowDisabled, true);
    assert.equal(postBusy, 'true');
    assert.equal(allowReloadDisabled, true);
    assert.equal(revokePostDisabled, true);
    assert.equal(revokeReloadDisabled, true);
    assert.equal(h.node('body').attributes['aria-busy'], 'false');
  } finally { h.stop(); }
});

for (const stage of ['POST', 'reload']) {
  for (const [name, error] of [['503', { status: 503 }], ['network', new TypeError('Network unavailable')]]) {
    test(`consent ${stage} ${name} failure releases pending controls and preserves the acknowledged state`, async () => {
      const h = consentHarness(), { state } = h;
      try {
        await h.settled(); h.click('consent'); await h.settled();
        const first = state.writes[0]; assert.ok(first);
        if (stage === 'POST') first.fail(error);
        else { state.holdReload = true; first.commit(); await h.settled(); state.reads.at(-1).fail(error); }
        await h.settled();
        assert.equal(state.enabled, stage === 'reload');
        assert.equal(h.node('body').attributes['aria-busy'], 'false');
        assert.equal(h.node('disable').disabled, false);
        state.holdReload = false; h.click('refresh'); await h.settled();
        if (stage === 'POST') { h.click('consent'); await h.settled(); state.writes.at(-1).commit(); await h.settled(); }
        h.click('disable'); await h.settled(); state.writes.at(-1).commit(); await h.settled();
        assert.deepEqual(state.commits, [true, false]);
        assert.equal(state.enabled, false);
        assert.match(h.node('body').innerHTML, /Allow aggregate reporting/);
        assert.equal(h.node('body').attributes['aria-busy'], 'false');
      } finally { h.stop(); }
    });
  }
}

test('Time sinks retains global leaders after Trace pagination and model filtering', async () => {
  const h = harness();
  try {
    await h.settled(); h.click('tab', 'trace'); await h.settled();
    h.click('next'); await h.settled(); h.click('tab', 'sinks'); await h.settled();
    assert.ok(h.node('body').innerHTML.includes(threads[0].id), 'Global leader lost after Trace Next');
    h.click('tab', 'trace'); await h.settled(); h.model('gpt-6.1-sol'); await h.settled();
    h.click('tab', 'sinks'); await h.settled();
    assert.ok(h.node('body').innerHTML.includes(threads[0].id), 'Global leader lost after Trace model filter');
  } finally { h.stop(); }
});

test('Trace scope remains coherent after Recap Refresh', async () => {
  const h = harness();
  try {
    await h.settled(); h.click('tab', 'trace'); await h.settled(); h.model('gpt-6.1-sol'); await h.settled();
    h.click('tab', 'recap'); await h.settled(); h.click('refresh'); await h.settled();
    h.click('tab', 'trace'); await h.settled();
    const query = new URL(h.requests.at(-1), 'https://example.test').searchParams;
    assert.equal(query.get('model'), 'gpt-6.1-sol');
    assert.match(h.node('body').innerHTML, /of 29 retained/);
    assert.equal(h.node('model').value, 'gpt-6.1-sol');
  } finally { h.stop(); }
});

test('a shrinking retained result resets an invalid offset and its range together', async () => {
  const h = harness();
  try {
    await h.settled(); h.click('tab', 'trace'); await h.settled();
    h.click('next'); await h.settled();
    h.retain(threads.slice(0, 5)); h.click('refresh'); await h.settled();
    assert.match(h.node('body').innerHTML, /Threads 1–5 of 5 retained/);
    assert.equal(new URL(h.requests.at(-1), 'https://example.test').searchParams.get('offset'), '0');
  } finally { h.stop(); }
});

test('PR state counts disclose retained provenance beside the count', () => {
  const html = recapHTML({ ...snapshot, coverage: { pr_first: 1794000000, pr_last: 1795089600 }, metrics: { prs_merged: { value: 17, reason: null } } });
  const metric = html.match(/<dt>[^<]*PRs merged[^<]*<\/dt>.*?<\/div>/)?.[0];
  assert.ok(metric);
  assert.match(metric, /Retained linked state/i);
  assert.match(metric, /As of/);
  assert.doesNotMatch(metric, /Recorded within the selected window/);
  assert.match(html, /Merge events in this window/);
});

for (const [name, error] of [['503', { status: 503 }], ['network', new TypeError('Network unavailable')]]) {
  test(`automatic source replacement clears the previous snapshot and page before a ${name} failure`, async () => {
    const h = harness();
    try {
      await h.settled(); h.click('tab', 'trace'); await h.settled();
      h.model('gpt-6.1-sol'); await h.settled(); h.click('next'); await h.settled();
      assert.match(h.node('body').innerHTML, /Threads 26–29 of 29 retained/);
      h.replaceSource('replacement'); h.fail(error); h.click('refresh'); await h.settled();
      assert.equal(h.node('host').value, 'replacement');
      assert.match(h.node('body').innerHTML, /Activity is unavailable/);
      assert.doesNotMatch(h.node('body').innerHTML, /data-ca-thread|Threads 26/);
      assert.equal(h.node('fresh').textContent, 'No aggregate snapshot available.');
      const failed = new URL(h.requests.at(-1), 'https://example.test');
      assert.equal(failed.pathname, '/api/compute/activity/replacement');
      assert.equal(failed.searchParams.get('offset'), '0');
      assert.equal(failed.searchParams.get('model'), '');
      const replacement = { ...threads[0], id: 'replacement-thread' };
      h.retain([replacement]); h.fail(null); h.click('refresh'); await h.settled();
      assert.match(h.node('body').innerHTML, /Threads 1–1 of 1 retained/);
      assert.ok(h.node('body').innerHTML.includes(replacement.id));
      assert.equal(h.node('model').value, '');
    } finally { h.stop(); }
  });

  test(`a same-source ${name} failure keeps the retained snapshot, query and stale disclosure`, async () => {
    const h = harness();
    try {
      await h.settled(); h.click('tab', 'trace'); await h.settled();
      h.model('gpt-6.1-sol'); await h.settled(); h.click('next'); await h.settled();
      h.fail(error); h.click('refresh'); await h.settled();
      assert.equal(h.node('host').value, 'source');
      assert.match(h.node('body').innerHTML, /Threads 26–29 of 29 retained/);
      assert.ok(h.node('body').innerHTML.includes(threads[27].id));
      assert.match(h.node('problem').textContent, /Retained values below may be stale/);
      const query = new URL(h.requests.at(-1), 'https://example.test').searchParams;
      assert.equal(query.get('offset'), '25');
      assert.equal(query.get('model'), 'gpt-6.1-sol');
    } finally { h.stop(); }
  });
}

for (const [name, error] of [['503', { status: 503 }], ['network', new TypeError('Network unavailable')]]) {
  for (const mode of ['ack-list-failure', 'ack-read-failure', 'external-read-failure']) {
    test(`${mode}: inactive consent evicts cached aggregates before a ${name} failure`, async () => {
      const h = harness();
      try {
        await h.settled(); h.click('tab', 'trace'); await h.settled();
        h.model('gpt-6.1-sol'); await h.settled(); h.click('next'); await h.settled();
        assert.match(h.node('body').innerHTML, /Threads 26–29 of 29 retained/);
        if (mode === 'ack-list-failure') h.failListing(error); else h.fail(error);
        if (mode === 'external-read-failure') { h.consentEnabled(false); h.click('refresh'); }
        else h.click('disable');
        await h.settled();
        assert.match(h.node('body').innerHTML, /Activity is unavailable/);
        assert.doesNotMatch(h.node('body').innerHTML, /data-ca-thread|Threads 26/);
        assert.equal(h.node('fresh').textContent, 'No aggregate snapshot available.');
        assert.equal(h.node('disable').hidden, true);
        assert.doesNotMatch(h.node('problem').textContent, /Retained values below/);
        h.fail(null); h.failListing(null); h.consentEnabled(true); h.click('refresh'); await h.settled();
        assert.match(h.node('body').innerHTML, /Threads 1–25 of 31 retained/);
        const query = new URL(h.requests.at(-1), 'https://example.test').searchParams;
        assert.equal(query.get('offset'), '0');
        assert.equal(query.get('model'), '');
        assert.equal(h.node('model').value, '');
      } finally { h.stop(); }
    });
  }
}
