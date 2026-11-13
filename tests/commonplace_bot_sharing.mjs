import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createBots, sheetHTML } from '../commonplace/web/static/bots.js';
import { escape as e } from '../commonplace/web/static/api.js';
import { sendMessage } from '../commonplace/web/static/botstream.js';

const settle = async () => { for (let i = 0; i < 25; i++) await new Promise(setImmediate); };
const owner = { id: 'owner', name: 'Workspace owner' }, person = { id: 'member', name: 'Workspace member' };
function harness({ member = false, frozen = false, deck = true, fail = false, lifecycle = false } = {}) {
  globalThis.document = { documentElement: { dataset: { ui: deck ? 'deck' : 'classic' } } };
  const bot = { id: 'bot', name: 'Team helper', kind: 'custom', owner, mine: !member, shared: member, frozen, frozen_reason: frozen ? 'owner_left' : null, perms: { talk: true, see_shared: false }, lifecycle };
  const regions = Object.fromEntries(['roster', 'model', 'head', 'messages', 'waiting', 'notice', 'composer', 'send', 'sheet'].map(name => [name, { innerHTML: '', querySelector() { return null; } }]));
  const root = { isConnected: true, setAttribute() {}, addEventListener() {} };
  const mount = { innerHTML: '', querySelector(q) { return q === '.bots' ? root : regions[/data-region="(\w+)"/.exec(q)?.[1]]; } };
  const calls = [], shares = member ? [{ user: person, talk: true, see_shared: false }] : [];
  const threads = [];
  let allowed = member;
  const api = async (path, options = {}) => {
    const method = options.method || 'GET'; calls.push([path, method, options.body]);
    if (path === '/api/bots') return { bots: [bot] };
    if (path === '/api/me') return { user: member ? person.id : owner.id, people: { 'user:owner': owner.name, 'user:member': person.name }, can_admin: !member };
    if (path === '/api/bots/settings') return { sharing: true };
    if (path.endsWith('/access/log?limit=50')) return { log: [] };
    if (path.endsWith('/access')) return { version: 0, entries: [] };
    if (path.includes('/shares')) {
      if (fail) throw new Error('Sharing unavailable');
      if (method === 'GET') return { shares: structuredClone(shares) };
      if (method === 'DELETE') { allowed = false; shares.splice(0); return { ok: true }; }
      allowed = true; bot.shared = true;
      shares.splice(0, shares.length, { user: person, talk: options.body.talk, see_shared: options.body.see_shared });
      return { share: shares[0] };
    }
    if (path.endsWith('/threads')) {
      if (method === 'POST') { const thread = { id: 'shared-one', visibility: 'shared', title: options.body.title }; threads.push(thread); return { thread }; }
      return { threads, own: null };
    }
    if (path.includes('/messages')) return { bot, messages: [], waiting: [] };
    if (path.endsWith('/adopt')) { bot.frozen = false; bot.mine = true; bot.lifecycle = false; return { ok: true }; }
    if (path === '/api/bots/bot/memory') return { memories: [] };
    throw new Error(`Unexpected request ${path}`);
  };
  const sent = [];
  const ui = createBots({ mount: () => mount, api, e, viewport: null, setTimer: () => 1, clearTimer() {}, send: async args => { sent.push(args); return { state: 'done' }; } });
  const click = async (action, extra = {}) => { ui.click({ dataset: { botsAction: action, ...extra } }); await settle(); };
  return { ui, regions, bot, calls, shares, click, sent, allowed: () => allowed };
}

test('owner grants changes and revokes access through a named picker', async () => {
  const h = harness(); await h.ui.show('bot'); await h.click('sheet');
  assert.match(h.regions.sheet.innerHTML, /Who can talk to this bot/);
  await h.click('sharing-picker'); assert.match(h.regions.sheet.innerHTML, /Choose a workspace person/);
  assert.match(h.regions.sheet.innerHTML, /Workspace member/);
  await h.click('sharing-select', { user: 'member' });
  assert.match(h.regions.sheet.innerHTML, /has no access/);
  h.ui.state.sheet.draft = { talk: true, see_shared: false };
  await h.ui.submit({ dataset: { botsForm: 'share' } });
  assert.equal(h.allowed(), true);
  assert.deepEqual(h.calls.find(([, method]) => method === 'POST'), ['/api/bots/bot/shares', 'POST', { user: 'member', talk: true, see_shared: false }]);
  await h.click('sharing-edit', { user: 'member' }); h.ui.state.sheet.draft = { talk: false, see_shared: true };
  await h.ui.submit({ dataset: { botsForm: 'share' } });
  assert.ok(h.calls.some(([path, method, body]) => path.endsWith('/shares/member') && method === 'PATCH' && body.see_shared));
  await h.click('sharing-edit', { user: 'member' }); await h.click('sharing-revoke');
  assert.equal(h.allowed(), true, 'revoke requires its named confirmation');
  await h.click('sharing-revoke'); assert.equal(h.allowed(), false);
  h.ui.stop();
});

test('member settings are read only and forged owner actions make no writes', async () => {
  const h = harness({ member: true }); await h.ui.show('bot'); await h.click('sheet');
  assert.match(h.regions.sheet.innerHTML, /You can talk privately/);
  assert.doesNotMatch(h.regions.sheet.innerHTML, /data-permission-editor|sharing-picker|data-bots-form="save"|data-bots-action="adopt"/);
  await h.click('sharing-picker'); await h.click('sharing-select', { user: 'owner' }); await h.click('adopt'); await h.click('bot-details');
  await h.ui.submit({ dataset: { botsForm: 'share' } });
  assert.ok(h.calls.every(([, method]) => method === 'GET'));
  h.ui.stop();
});

test('shared audience is previewed before creation and sending carries the selected thread', async () => {
  const h = harness({ member: true }); await h.ui.show('bot');
  assert.match(h.regions.head.innerHTML, /Private · only you/);
  await h.click('shared-thread');
  assert.match(h.regions.sheet.innerHTML, /Who can see this conversation/);
  assert.match(h.regions.sheet.innerHTML, /everyone allowed to see/);
  assert.ok(h.calls.every(([, method]) => method === 'GET'));
  await h.ui.submit({ dataset: { botsForm: 'shared-thread' }, elements: { title: { value: 'Planning' } } });
  assert.equal(h.ui.state.threadId, 'shared-one');
  await h.ui.submit({ dataset: { botsForm: 'send' }, elements: { text: { value: 'Hello together' } } });
  assert.equal(h.sent[0].threadId, 'shared-one');
  assert.ok(h.calls.some(([path]) => path.includes('/messages?limit=50&thread=shared-one')));
  h.ui.stop();
});

test('frozen owner gets adoption and no messages are requested', async () => {
  const h = harness({ frozen: true }); await h.ui.show('bot');
  assert.match(h.regions.sheet.innerHTML, /This bot needs a new owner/);
  assert.match(h.regions.sheet.innerHTML, /Adopt bot/);
  assert.ok(!h.calls.some(([path]) => path.includes('/messages')));
  h.ui.stop();
});

test('sharing errors remain visible and do not expose a permission editor', async () => {
  const h = harness({ fail: true }); await h.ui.show('bot'); await h.click('sheet');
  assert.match(h.regions.sheet.innerHTML, /Sharing unavailable/);
  assert.doesNotMatch(h.regions.sheet.innerHTML, /data-permission-editor/);
  h.ui.stop();
});

test('classic has no sharing UI and uses the original request paths', async () => {
  const h = harness({ deck: false }); await h.ui.show('bot'); await h.click('sheet');
  assert.match(h.regions.sheet.innerHTML, /data-bots-form="save"/);
  assert.doesNotMatch(h.regions.sheet.innerHTML, /Who can talk|data-member-access/);
  assert.ok(!h.calls.some(([path]) => /shares|access|threads|api\/me/.test(path)));
  h.ui.stop();
});

test('permission previews distinguish all combinations and escape names', async () => {
  const { permissionText, sharingHTML, accessLogText, audienceText } = await import('../commonplace/web/static/bot-sharing.js');
  assert.match(permissionText({ talk: true }), /cannot read other shared conversations/);
  assert.match(permissionText({ see_shared: true }), /cannot send messages/);
  assert.match(permissionText({ talk: true, see_shared: true }), /read and reply/);
  assert.match(permissionText({}), /no access/);
  const html = sharingHTML({ me: { people: { 'user:x': '<img>' } }, settings: { sharing: true }, shares: [], selected: 'x', draft: { talk: true } }, { mine: true, kind: 'custom', name: '<img>', owner }, e);
  assert.match(html, /&lt;img&gt;/); assert.doesNotMatch(html, /<img>/);
  assert.equal(accessLogText({ action: 'permissions', actor_id: 'owner', member_id: 'member' }, { 'user:owner': owner.name, 'user:member': person.name }), 'Workspace owner changed Workspace member’s permissions for this bot.');
  assert.match(audienceText({ owner }, [{ user: person, see_shared: true }], { mine: true }), /Workspace owner, Workspace member/);
});

test('learning share shows an explicit project audience and server preview before confirmation', () => {
  const html = sheetHTML({ kind: 'learned', deck: true, canShare: true, items: [{ id: 'note', group: 'person', kind: 'fact', text: 'Exact words' }], projects: [{ slug: 'work', name: 'Shared work' }], shareItem: 'note' }, { name: 'Helper' }, e);
  assert.match(html, /Project audience/); assert.match(html, /Everyone who can read this project/);
  assert.match(html, /Preview sharing/); assert.doesNotMatch(html, /learning-share-confirm/);
});

test('stream transport scopes shared requests and preserves the default URL', async () => {
  for (const threadId of [undefined, 'shared / one']) {
    let path;
    await sendMessage({ botId: 'bot', threadId, text: 'Hello', requestId: 'request-one', fetchImpl: async url => { path = url; return new Response('event: done\ndata: {}\n\n', { headers: { 'Content-Type': 'text/event-stream' } }); } });
    assert.equal(path, `/api/bots/bot/messages${threadId ? '?thread=shared%20%2F%20one' : ''}`);
  }
});

test('drafts from a private conversation never populate a shared conversation', async () => {
  const h = harness({ member: true }); await h.ui.show('bot');
  h.ui.state.drafts.set('bot', 'Private words must stay here');
  await h.click('shared-thread');
  await h.ui.submit({ dataset: { botsForm: 'shared-thread' }, elements: { title: { value: 'Team work' } } });
  assert.equal(h.ui.state.threadId, 'shared-one');
  assert.equal(h.ui.state.drafts.get('bot:shared-one'), undefined);
  assert.equal(h.ui.state.drafts.get('bot'), 'Private words must stay here');
  h.ui.stop();
});
