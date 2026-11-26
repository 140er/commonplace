import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { releaseBlock, trustLine } from '../commonplace/web/static/release-block.js';
import { renderNeedDetail, needsItems } from '../commonplace/web/static/needs.js';

const waiting = { number: 7, board: 'atlas', title: 'Synthetic', status: 'backlog', version: 2, awaiting_release: true, can_approve: true,
  creator: { name: 'restricted', trust: 'untrusted', inbox_only: true } };

test('a waiting card opens with who made it, their trust, a reason field, Release and Hold', () => {
  const html = releaseBlock(waiting);
  assert.match(html, /Waiting for release/);
  assert.match(html, /Created by restricted · untrusted trust · writes to the inbox only/);
  assert.match(html, /<label>Release reason<input name="reason" required maxlength="500">/);
  assert.match(html, /data-release-origin>Release<\/button>/);
  assert.match(html, /data-action="close-panel"[^>]*>Hold<\/button>/);
  assert.doesNotMatch(html, /Approve|Release origin/);
});

test('only a person who may release sees the block', () => {
  assert.equal(releaseBlock({ ...waiting, can_approve: false }), '');
  assert.equal(releaseBlock({ ...waiting, awaiting_release: false }), '');
});

test('creator trust is spelled out, including when it is unknown', () => {
  assert.equal(trustLine({ name: 'bot', trust: 'trusted', inbox_only: false }), 'bot · trusted trust');
  assert.equal(trustLine({ name: 'bot', trust: null }), 'bot · trust unknown');
  assert.equal(trustLine(undefined), 'Creator not recorded');
});

test('the block sits at the top of the card body, outside the collapsed Edit card section', () => {
  const app = readFileSync(new URL('../commonplace/web/static/app.js', import.meta.url), 'utf8');
  assert.match(app, /querySelector\("\.ctx-body"\);\s*activity\?\.insertAdjacentHTML\("afterbegin", releaseBlock\(card\)\)/);
  assert.doesNotMatch(app, /data-deck-card-controls"\)[^\n]*releaseBlock/);
});

test('Today lists a waiting card and releases it with the one word Release', () => {
  const item = { ...waiting, approval_kind: 'origin', actions: ['approve'], need: 'release' };
  assert.equal(needsItems({ approvals: [item] }).length, 1);
  const html = renderNeedDetail(item);
  assert.match(html, />Release<\/button>/);
  assert.match(html, /Release reason/);
  assert.match(html, /Created by restricted · untrusted trust/);
  assert.doesNotMatch(html, /Origin release|Release origin|Approve/);
});

test('the Deck Today card says Release on its button, form and confirmation', async () => {
  const { primaryAction } = await import('../commonplace/web/static/deck-home-today.js');
  const { replyWords, sentLine, approveNote, verbOf } = await import('../commonplace/web/static/deck-today-capy.js');
  const item = { number: 7, version: 2, board: 'atlas', title: 'Synthetic', approval_kind: 'origin', actions: ['approve'], need: 'release' };
  assert.equal(primaryAction(item).label, 'Release');
  assert.equal(primaryAction({ ...item, approval_kind: undefined }).label, 'Approve');
  assert.deepEqual(replyWords('approve', false, true), { title: 'Release this card', label: 'Release reason', submit: 'Release' });
  assert.match(sentLine(item, 'approve', null, 'bot'), /^Released\./);
  assert.doesNotMatch(approveNote('bot', true), /Approve/);
  assert.equal(verbOf(item), 'is waiting for release');
});
