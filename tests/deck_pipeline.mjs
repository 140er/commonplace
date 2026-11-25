// The Pipeline view over the fixed example (tests/pipeline_fixture.json, the server's own answer at 2026-11-15 12:00 UTC).
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { duration, clock, blockHTML, connectorHTML, prHTML, pageHTML, emptyHTML, EMPTY_COPY } from '../commonplace/web/static/deck-pipeline.js';
import { ART } from '../commonplace/web/static/deck-art-data.js';
import { shellTrees, renderShellMore, renderShellBar } from '../commonplace/web/static/deck-shell.js';
import { SCREENS } from '../commonplace/web/static/deck.js';

const fixture = JSON.parse(readFileSync(new URL('./pipeline_fixture.json', import.meta.url), 'utf8'));
const css = readFileSync(new URL('../commonplace/web/static/deck-pipeline.css', import.meta.url), 'utf8');
const pr = number => fixture.prs.find(row => row.pr === number);
const STEPS = ['card', 'builder', 'pr_opened', 'gate', 'check', 'merge', 'deploy_snapshot', 'deploy_rollback_image', 'deploy_smoke', 'deploy_health', 'backup_proof'];
const text = html => html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
const cells = html => [...html.matchAll(/<li class="pl-cell" data-step="([a-z_]+)" data-state="([a-z]+)"([^>]*)>([^]*?)<\/li>/g)].map(m => ({ step: m[1], state: m[2], attrs: m[3], html: m[4] }));

test('durations read like 3m 12s and 1h 04m, and times are Amsterdam time', () => {
  assert.deepEqual([0, 7, 59, 60, 192, 3599, 3600, 3840, 86400 + 7200].map(duration), ['0s', '7s', '59s', '1m 00s', '3m 12s', '59m 59s', '1h 00m', '1h 04m', '1d 02h']);
  assert.deepEqual([NaN, -1, null, undefined, 'x'].map(duration), ['', '', '', '', '']);
  assert.equal(clock('2026-11-15T06:28:50.000Z'), '07:28:50'); // winter: CET is UTC+1
  assert.equal(clock('2026-07-15T06:28:50.000Z'), '08:28:50'); // summer: CEST, still Amsterdam
  assert.equal(clock('2026-11-15T23:59:59Z'), '00:59:59');
  assert.equal(clock('nope'), '');
});

test('the blocks come in the order of the flow, whatever the PR', () => {
  for (const row of fixture.prs) assert.deepEqual(cells(prHTML(row)).map(c => c.step), STEPS, `PR ${row.pr}`);
});

test('every block shows its state in words, and not started steps are drawn as such', () => {
  const done = cells(prHTML(pr(101)));
  assert.ok(done.every(c => c.state === 'pass'));
  assert.ok(done.every(c => /<span>Passed<\/span>/.test(c.html)));
  const started = cells(prHTML(pr(103)));
  assert.deepEqual(started.slice(0, 3).map(c => c.state), ['pass', 'running', 'pending']);
  assert.match(started[1].html, /<span>Running<\/span>/);
  assert.match(started[2].html, /<span>Not started<\/span>/);
  assert.doesNotMatch(started[2].html, /<dl class="pl-times">/);
  assert.match(started[1].html, /So far<\/dt><dd><b>11m 45s<\/b>/);
  // A mark per state, never colour alone: the word and a glyph.
  assert.match(started[1].html, /class="pl-mark" data-state="running"/);
});

test('start, end and duration of a step are in Amsterdam time', () => {
  const gate = cells(prHTML(pr(101))).find(c => c.step === 'gate').html;
  assert.match(gate, /Start<\/dt><dd><time datetime="2026-11-15T06:28:50.000Z"[^>]*>07:28:50<\/time>/);
  assert.match(gate, /End<\/dt><dd><time datetime="2026-11-15T06:32:02.000Z"[^>]*>07:32:02<\/time>/);
  assert.match(gate, /Took<\/dt><dd><b>3m 12s<\/b>/);
  assert.match(gate, /title="[^"]*\(Amsterdam time\)"/);
});

test('the wait before a step is drawn on the connector after the previous block', () => {
  const row = pr(101), html = prHTML(row);
  const wires = [...html.matchAll(/<span class="pl-wire"([^>]*)>(?:<span class="pl-wait">([^<]*)<\/span>)?<\/span>/g)];
  assert.equal(wires.length, 10); // between eleven blocks
  const labelled = wires.filter(w => w[2]).map(w => [/data-wait="(\d+)"/.exec(w[1])[1], w[2]]);
  assert.ok(labelled.some(([seconds, label]) => seconds === '720' && label === '12m 00s'));
  const merge = wires[4]; // check -> merge
  assert.match(merge[1], /data-longest="true"/);
  assert.match(merge[1], /Waited 12m 00s before Merge into develop/);
  // The wait before the gate: 1m 15s, of which 40s queued.
  assert.match(wires[2][1], /Waited 1m 15s before Gate, 40s of it queued/);
  assert.equal(wires.filter(w => /data-longest/.test(w[1])).length, 1);
});

test('an overlap or a zero wait draws a plain connector with no label', () => {
  const row = { ...pr(101), steps: pr(101).steps.map(step => step.step === 'gate' ? { ...step, wait: { ...step.wait, overlap: true } } : step) };
  assert.equal(connectorHTML(row.steps[3], row), '<span class="pl-wire" aria-hidden="true"></span>');
  assert.equal(connectorHTML({ ...row.steps[3], wait: { seconds: 0 } }, row), '<span class="pl-wire" aria-hidden="true"></span>');
  assert.equal(connectorHTML({ step: 'gate' }, row), '<span class="pl-wire" aria-hidden="true"></span>');
});

test('a wait that is still growing says so', () => {
  const row = pr(102), next = { step: 'merge', label: 'Merge into develop', wait: { seconds: 95, ongoing: true, overlap: false } };
  const html = connectorHTML(next, row);
  assert.match(html, /data-ongoing="true"/);
  assert.match(html, /Waiting 1m 35s for Merge into develop so far/);
});

test('the slowest step is highlighted once, with a word as well as a colour', () => {
  for (const [number, step, label] of [[101, 'builder', 'Builder started'], [102, 'builder', 'Builder started'], [103, 'builder', 'Builder started']]) {
    const html = prHTML(pr(number)), hit = cells(html).filter(c => /data-slowest="true"/.test(c.attrs));
    assert.deepEqual(hit.map(c => c.step), [step]);
    assert.equal((html.match(/pl-chip pl-slowest">Slowest</g) || []).length, 1);
    assert.match(html, new RegExp(`data-stat="slowest"[^>]*><small>Slowest step</small><b>${label} · `));
    assert.match(hit[0].html, /the slowest step/);
  }
});

test('the header carries the total lead time, the longest wait and where the PR is now', () => {
  const finished = prHTML(pr(101)), retried = prHTML(pr(102));
  assert.match(finished, /data-stat="lead"[^>]*><small>Lead time<\/small><b>57m 14s<\/b>/);
  assert.match(finished, /data-stat="wait"[^>]*><small>Longest wait<\/small><b>12m 00s<\/b><i>before Merge into develop<\/i>/);
  assert.match(finished, /class="pl-pill" data-state="done">Deployed</);
  assert.doesNotMatch(finished, /data-stat="now"/);
  assert.match(retried, /<b>40m 00s<\/b><i>so far<\/i>/);
  assert.match(retried, /data-stat="now"[^>]*><small>Now<\/small><b>Check<\/b>/);
  assert.match(retried, /class="pl-pill" data-state="running">In progress</);
});

test('a retried step shows its last run and counts the attempts', () => {
  const gate = cells(prHTML(pr(102))).find(c => c.step === 'gate');
  assert.match(gate.html, /pl-chip" title="2 attempts, 10m 05s in all">Attempt 2</);
  assert.match(gate.html, /Took<\/dt><dd><b>3m 05s<\/b>/);
  assert.match(gate.html, /<span>Passed<\/span>/);
});

test('only https links open, and references that are not links stay as text', () => {
  const step = { step: 'gate', label: 'Gate', present: true, state: 'pass', attempts: 1, ref: 'javascript:alert(1)' };
  const base = pr(101);
  assert.doesNotMatch(blockHTML(step, base), /<a /);
  assert.match(blockHTML(step, base), /<code>javascript:alert\(1\)<\/code>/);
  assert.match(blockHTML({ ...step, ref: 'https://example.test/v?a=1&b="2"' }, base), /<a href="https:\/\/example.test\/v\?a=1&amp;b=&quot;2&quot;" target="_blank" rel="noopener noreferrer">Gate verdict<\/a>/);
  assert.match(blockHTML({ ...step, step: 'check', ref: 'https://example.test/log' }, base), />Check log</);
  assert.match(blockHTML({ ...step, step: 'card', ref: '#/card/568' }, base), /<a href="#\/card\/568">Card #568<\/a>/);
  assert.match(prHTML(base), /<a href="https:\/\/github.com\/example\/app\/pull\/101"/);
});

test('titles and details are escaped', () => {
  const row = { ...pr(101), title: '<img src=x onerror=alert(1)>' };
  row.steps = row.steps.map(step => step.step === 'gate' ? { ...step, detail: '<script>x</script>' } : step);
  const html = prHTML(row);
  assert.doesNotMatch(html, /<img|<script/);
  assert.match(html, /&lt;img src=x/);
});

test('with no pull requests the page explains where data comes from and offers the example', () => {
  const html = pageHTML({ prs: [] });
  assert.equal(EMPTY_COPY.body.startsWith('Pipeline events come from the deploy flow.'), true);
  assert.match(text(html), /No pull requests yet Pipeline events come from the deploy flow\. Each pull request shows up here as it goes from card to merge to deploy\. See an example/);
  assert.match(html, /<span class="cp-art" data-art="pipeline" aria-hidden="true"><svg /);
  assert.match(html, /data-pl="demo"/);
  assert.doesNotMatch(text(html), /API|endpoint|token|POST|JSON|webhook/i);
  assert.equal(emptyHTML(), emptyHTML());
  assert.ok(ART.pipeline);
});

test('loading, failure and the example banner have their own words', () => {
  assert.match(pageHTML({ prs: null }), /Loading…/);
  assert.match(pageHTML({ error: 'The pipeline could not be loaded.' }), /role="alert">The pipeline could not be loaded\. <button[^>]*data-pl="retry"/);
  const demo = pageHTML({ prs: fixture.prs, demo: true });
  assert.match(demo, /This is an example, not your data\./);
  assert.doesNotMatch(pageHTML({ prs: fixture.prs }), /example, not your data/);
});

test('the first three pull requests open, a choice is kept, and the newest comes first', () => {
  const html = pageHTML({ prs: fixture.prs });
  assert.deepEqual([...html.matchAll(/<details class="pl-pr" data-pr="example\/app#(\d+)"/g)].map(m => m[1]), ['102', '103', '101']);
  assert.equal((html.match(/<details[^>]* open>/g) || []).length, 3);
  const closed = pageHTML({ prs: fixture.prs, choices: new Map([['example/app#102', false]]) });
  assert.equal((closed.match(/<details[^>]* open>/g) || []).length, 2);
});

test('the phone stacks the blocks and connectors vertically, and reduced motion stops every animation', () => {
  const phone = /@media \(max-width: 699px\) \{([^]*?)\n\}/.exec(css)[1];
  assert.match(phone, /\.pl-flow \{[^}]*flex-direction: column/);
  assert.match(phone, /\.pl-cell \{[^}]*flex-direction: column/);
  assert.match(phone, /\.pl-block \{[^}]*width: 100%/);
  assert.match(phone, /\.pl-wire \{[^}]*width: 100%/);
  const reduced = /@media \(prefers-reduced-motion: reduce\) \{([^]*?)\n\}/.exec(css)[1];
  assert.match(reduced, /animation: none/);
  assert.match(reduced, /\.pl-wire\[data-ongoing="true"\] \.pl-wait::before/);
  // Nothing is loaded from another origin.
  assert.doesNotMatch(css, /url\(|@import/);
});

test('Pipeline is a Deck screen with an entry in the account menu and the phone More sheet', () => {
  assert.equal(typeof SCREENS.pipeline, 'function');
  const options = { hash: '', sections: [], section: '', can_admin: true, homeOn: true, bots: [], directory: {} };
  assert.match(renderShellMore(options), /<a href="#\/pipeline">Pipeline<\/a>/);
  assert.match(renderShellBar(options), /<a class="nav" href="#\/pipeline">[\s\S]*?Pipeline<\/a>/);
  assert.ok(shellTrees);
});
