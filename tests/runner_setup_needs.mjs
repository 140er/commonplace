import assert from 'node:assert/strict';
import { renderNeeds } from '../commonplace/web/static/needs.js';

const entry = { id: 'setup-fixture', kind: 'runner_settings', message: 'Review <runner> settings' };
const empty = renderNeeds();
assert.ok(!empty.includes('data-runner-setup'));
const shown = renderNeeds({ needs: { runner_setup: [entry] } });
assert.ok(shown.includes('data-runner-setup="setup-fixture"'));
assert.ok(shown.includes('Review &lt;runner&gt; settings'));
assert.ok(shown.includes('href="#/admin"'));
assert.ok(shown.includes('1 item, 0 projects'));
assert.ok(!shown.includes('Nothing needs you'));
assert.ok(!shown.includes('data-needs-approve'));
assert.ok(!renderNeeds({ needs: { runner_setup: [entry] }, filter: 'approvals' }).includes('data-runner-setup'));
assert.ok(renderNeeds({ needs: { runner_setup: [entry] }, filter: 'decisions' }).includes('data-runner-setup'));
assert.ok(!renderNeeds({ needs: { runner_setup: [{ ...entry, kind: 'unknown' }] } }).includes('data-runner-setup'));
