import assert from 'node:assert/strict';
import { test } from 'node:test';
import { heatHTML, recapHTML, sinksHTML } from '../commonplace/web/static/activity.js';
import { workspaceRoute } from '../commonplace/web/static/routes.js';
import { SECTIONS } from '../commonplace/web/static/deck-shell.js';

test('Activity is nested inside Compute while the four global destinations stay unchanged', () => {
  assert.equal(SECTIONS.length, 4);
  assert.deepEqual(SECTIONS.map(row => row[0]), ['today', 'bots', 'compute', 'settings']);
  assert.equal(workspaceRoute('#/compute/activity').activity, true);
  assert.equal(workspaceRoute('#/compute/management').management, true);
});

test('zero heat stays zero and omitted metrics retain a reason', () => {
  const html = heatHTML([{ day: '2026-11-19', seconds: 0, runs: 0 }]);
  assert.match(html, /height="0"/);
  assert.doesNotMatch(html, / style=|<style/);
  assert.match(html, /0.00 h/);
  const recap = recapHTML({ coverage: {}, daily: [], models: [], metrics: {}, handoffs: [] });
  for (const label of ['Tasks finished', 'Tasks from agents', 'PRs merged', 'Thinking time', 'Cached input tokens', 'Context snapshots']) assert.ok(recap.includes(label), label);
  assert.match(recap, /n\/a/);
  assert.match(recap, /Unavailable in this snapshot/);
});

test('ranked rows state overlap, sample counts and unattributed fields', () => {
  const html = sinksHTML({ coverage: {}, spans: [], threads: [], sinks: [{category:'shell',seconds:60,count:2,timed:1,failed:1,p95:60,share:null,thread:null,project:null}] });
  assert.match(html, /0.02 h/);
  assert.match(html, /50.0% failed/);
  assert.match(html, /1 timed/);
  assert.match(html, /n\/a: no thread attribution/);
  assert.match(html, /overlap/);
  assert.match(html, /Build commands: n\/a/);
  assert.match(html, /Cards n\/a: no authoritative card-to-thread mapping/);
});
