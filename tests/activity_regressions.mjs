import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, recapHTML, sinksHTML } from '../commonplace/web/static/activity.js';

const threads = Array.from({ length: 31 }, (_, n) => ({ id: `thread_${n.toString(16).padStart(24, '0')}`, model: n < 2 ? 'other' : 'gpt-6.1-sol', status: 'completed', creator: 'user', seconds: 3600 - n, runs: 1, last: 1795089600 }));
const snapshot = { coverage: {}, threads, models: [], daily: [], metrics: {}, sinks: [], spans: [], handoffs: [], collected_at: 1795089600, window_start: 1795003200, window_end: 1795089600 };

function harness() {
  let available = threads, sources = [{ id: 'source', enabled: true }], failure = null;
  const nodes = new Map();
  const node = name => {
    if (!nodes.has(name)) nodes.set(name, { innerHTML: '', value: '', dataset: {}, setAttribute() {}, focus() {}, querySelector() { return null; } });
    return nodes.get(name);
  };
  const listeners = new Map(), requests = [];
  const root = { classList: { add() {}, remove() {} }, contains: () => true, ownerDocument: { hidden: false, addEventListener() {}, removeEventListener() {} }, querySelector: selector => node(selector.match(/data-ca-([^\]=" ]+)/)?.[1]), querySelectorAll: () => [], addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener() {} };
  const controller = mount({ root, api: async url => {
    requests.push(url);
    if (url === '/api/compute/activity') return { hosts: sources };
    if (failure) throw failure;
    const query = new URL(url, 'https://example.test').searchParams;
    const selected = available.filter(row => !query.get('model') || row.model === query.get('model'));
    const offset = Number(query.get('offset'));
    return { snapshot: { ...snapshot, threads: selected.slice(offset, offset + 25) }, thread_models: ['other', 'gpt-6.1-sol'], page: { total: selected.length, next: offset + 25 < selected.length ? offset + 25 : null } };
  } });
  const settled = () => new Promise(resolve => setImmediate(resolve));
  return { node, requests, stop: () => controller.stop(), settled, retain: rows => { available = rows; },
    replaceSource: id => { sources = [{ id, enabled: true }]; }, fail: error => { failure = error; },
    click: (key, value = '') => listeners.get('click')({ target: { closest: () => ({ dataset: key === 'tab' ? { caTab: value } : {}, hasAttribute: attr => attr === `data-ca-${key}` }) } }),
    model: value => { node('model').value = value; listeners.get('change')({ target: node('model') }); },
  };
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
