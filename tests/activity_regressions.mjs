import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, recapHTML, sinksHTML, traceHTML } from '../commonplace/web/static/activity.js';

const threads = Array.from({ length: 31 }, (_, n) => ({ id: `thread_${n.toString(16).padStart(24, '0')}`, model: n < 2 ? 'other' : 'gpt-6.1-sol', status: 'completed', creator: 'user', seconds: 3600 - n, runs: 1, last: 1795089600 }));
const snapshot = { coverage: {}, threads, models: [], daily: [], metrics: {}, sinks: [], spans: [], handoffs: [], collected_at: 1795089600, window_start: 1795003200, window_end: 1795089600 };

function harness() {
  let available = threads;
  const nodes = new Map();
  const node = name => {
    if (!nodes.has(name)) nodes.set(name, { innerHTML: '', value: '', dataset: {}, setAttribute() {}, focus() {}, querySelector() { return null; } });
    return nodes.get(name);
  };
  const listeners = new Map(), requests = [];
  const root = { classList: { add() {}, remove() {} }, contains: () => true, ownerDocument: { hidden: false, addEventListener() {}, removeEventListener() {} }, querySelector: selector => node(selector.match(/data-ca-([^\]=" ]+)/)?.[1]), querySelectorAll: () => [], addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener() {} };
  const controller = mount({ root, api: async url => {
    requests.push(url);
    if (url === '/api/compute/activity') return { hosts: [{ id: 'source', enabled: true }] };
    const query = new URL(url, 'https://example.test').searchParams;
    const selected = available.filter(row => !query.get('model') || row.model === query.get('model'));
    const offset = Number(query.get('offset'));
    return { snapshot: { ...snapshot, threads: selected.slice(offset, offset + 25) }, thread_models: ['other', 'gpt-6.1-sol'], page: { total: selected.length, next: offset + 25 < selected.length ? offset + 25 : null } };
  } });
  const settled = () => new Promise(resolve => setImmediate(resolve));
  return { node, requests, stop: () => controller.stop(), settled, retain: rows => { available = rows; },
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

test('phone fields carry visible token, time, p95 and call labels', () => {
  const html = recapHTML({ ...snapshot, models: [{ model: 'other', seconds: 60, tool_seconds: 20, runs: 1, input: 100, cached: 80, output: 20, complete_turns: 1, usage_turns: 1, inclusive_turns: 0 }] });
  for (const name of ['Run time', 'Tools', 'Input', 'Cached input', 'Output']) assert.ok(html.includes(`class="ca-field-label">${name}</span>`), name);
  const sinks = sinksHTML({ ...snapshot, sinks: [{ category: 'shell', seconds: 20, count: 1, timed: 1, failed: 0, p95: 20 }] });
  assert.ok(sinks.includes('class="ca-field-label">p95</span>'));
  const trace = traceHTML({ ...threads[0], phases: { tool: 20 }, buckets: [{ at: 1795089500, tool: 20, calls: 1 }] });
  assert.ok(trace.includes('class="ca-field-label">Recorded calls</span>'));
});
