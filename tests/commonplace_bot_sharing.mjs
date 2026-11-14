import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createBots, sheetHTML, messageHTML } from '../commonplace/web/static/bots.js';
import { escape as e } from '../commonplace/web/static/api.js';
import { sendMessage } from '../commonplace/web/static/botstream.js';

const settle = async () => { for (let i = 0; i < 25; i++) await new Promise(setImmediate); };
const owner = { id: 'owner', name: 'Workspace owner' }, person = { id: 'member', name: 'Workspace member' };
function harness({ member = false, frozen = false, deck = true, fail = false, lifecycle = false, failRevoke = false } = {}) {
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
      if (method === 'DELETE') {
        if (failRevoke && path.endsWith('/reader')) throw new Error('Removal unavailable');
        allowed = false;
        const index = shares.findIndex(row => path.endsWith('/' + row.user.id));
        if (index >= 0) shares.splice(index, 1);
        return { ok: true };
      }
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


test('shared human messages name their author without changing private or classic bubbles', () => {
  const message = { id: 1, role: 'user', text: 'Team message', author: { id: 'member', name: '<Workspace member>' } };
  assert.match(messageHTML(message, { deck: true, shared: true }, e), /bm-author.*&lt;Workspace member&gt;/);
  assert.doesNotMatch(messageHTML(message, { deck: true, shared: false }, e), /bm-author/);
  assert.doesNotMatch(messageHTML(message, { deck: false, shared: true }, e), /bm-author/);
});


test('settings design has one four-section row and scopes saved feedback to its card', async () => {
  const h = harness(); await h.ui.show('bot'); await h.click('sheet');
  let html = h.regions.sheet.innerHTML;
  assert.equal((html.match(/data-bots-action="sharing-tab"/g) || []).length, 4, 'all four sections belong to one row');
  assert.equal((html.match(/<nav/g) || []).length, 1);
  assert.doesNotMatch(html, /data-bots-action="bot-details"|data-bots-action="learned-sheet"|data-bots-action="disable"/);
  await h.click('sharing-select', { user: 'member' }); h.ui.state.sheet.draft = { talk: true, see_shared: false };
  await h.ui.submit({ dataset: { botsForm: 'share' } });
  html = h.regions.sheet.innerHTML;
  assert.match(html, /data-people-access><div[^>]*><div><h3>People with access<\/h3><p role="status" data-access-saved>Access saved/);
  assert.match(html, /<b>Workspace member<\/b><p>Can talk privately and start shared conversations\./);
  await h.click('sharing-edit', { user: 'member' });
  assert.doesNotMatch(h.regions.sheet.innerHTML, /Access saved/);
  assert.match(h.regions.sheet.innerHTML, /Workspace member can talk privately/);
  await h.click('sharing-tab', { tab: 'log' });
  assert.doesNotMatch(h.regions.sheet.innerHTML, /Access saved|data-bots-action="disable"/);
  await h.click('sharing-tab', { tab: 'details' });
  assert.match(h.regions.sheet.innerHTML, /bot-danger.*data-bots-action="disable"/);
  assert.equal((h.regions.sheet.innerHTML.match(/data-bots-action="sharing-tab"/g) || []).length, 4);
  const writes = () => h.calls.filter(([, method]) => method !== 'GET').length;
  const count = writes(); await h.click('disable');
  assert.equal(writes(), count, 'opening the explanation never disables the bot');
  assert.match(h.regions.sheet.innerHTML, /Conversations and access will be kept/);
  assert.match(h.regions.sheet.innerHTML, /disable-confirm/);
  await h.click('disable');
  assert.equal(writes(), count, 'repeating the first action still requires the separate confirmation');
  await h.click('disable-cancel');
  assert.equal(writes(), count); assert.doesNotMatch(h.regions.sheet.innerHTML, /disable-confirm/);
  h.ui.stop();
});

test('local access times use calendar words and keep the exact UTC title', async () => {
  const { accessLogTime, sharingHTML } = await import('../commonplace/web/static/bot-sharing.js');
  const timezone = process.env.TZ;
  process.env.TZ = 'Europe/Amsterdam';
  try {
    const now = new Date('2026-11-16T12:00:00Z');
    assert.equal(accessLogTime('2026-11-16T21:04:25Z', now), 'Today 22:04');
    assert.equal(accessLogTime('2026-11-15T08:12:00Z', now), 'Yesterday 09:12');
    assert.equal(accessLogTime('2026-11-13T21:04:25Z', now), '13 Nov 2026, 22:04');
    assert.equal(accessLogTime('2026-11-15T23:30:00Z', now), 'Today 00:30', 'local midnight decides the calendar day');
    assert.equal(accessLogTime('2026-10-25T00:30:00Z', new Date('2026-10-26T12:00:00Z')), 'Yesterday 02:30', 'a clock change does not change yesterday');
    assert.equal(accessLogTime('2025-12-31T08:12:00Z', new Date('2026-01-01T12:00:00Z')), 'Yesterday 09:12');
    const at = '2026-11-13T21:04:25.123Z';
    const html = sharingHTML({ tab: 'log', log: [{ action: 'share', at }] }, { mine: true }, e);
    assert.match(html, new RegExp(`<time datetime="${at}" title="${at}">`));
  } finally { if (timezone === undefined) delete process.env.TZ; else process.env.TZ = timezone; }
});

test('person summaries start with the permission while confirmations retain the full name', async () => {
  const { permissionSummary, permissionText } = await import('../commonplace/web/static/bot-sharing.js');
  assert.match(permissionSummary({ talk: true }), /^Can talk privately and start shared conversations\./);
  assert.match(permissionSummary({ talk: true, see_shared: true }), /^Can talk privately,/);
  assert.match(permissionSummary({ see_shared: true }), /^Can read shared conversations/);
  assert.equal(permissionSummary({}), 'Has no access to this bot.');
  assert.match(permissionText({ talk: true }, 'Syntheticlonggivenname Syntheticlongfamilyname'), /^Syntheticlonggivenname Syntheticlongfamilyname can talk/);
});


test('compact sharing uses inline permissions and confirms stopping through existing routes', async () => {
  const h = harness(); await h.ui.showSharing(h.bot);
  assert.match(h.regions.sheet.innerHTML, /placeholder="Add a person by name"/);
  assert.match(h.regions.sheet.innerHTML, /Only you\./);
  await h.click('sharing-select', { user: 'member' });
  h.ui.state.sheet.draft = {talk: true, see_shared: false};
  await h.ui.submit({dataset:{botsForm:'share'}});
  assert.match(h.regions.sheet.innerHTML, /data-capy-permission="member"/);
  await h.click('sharing-stop');
  assert.ok(h.calls.every(([,method])=>method!=='DELETE'));
  assert.match(h.regions.sheet.innerHTML, /Remove access for Workspace member/);
  await h.click('sharing-stop-cancel');
  assert.equal(h.ui.state.sheet.confirmStop, false);
  await h.click('sharing-stop'); await h.click('sharing-stop');
  assert.equal(h.calls.filter(([path, method]) => path === '/api/bots/bot/shares/member' && method === 'DELETE').length, 1);
  assert.match(h.regions.sheet.innerHTML, /Only you\./);
  h.ui.stop();
});
test('compact stop-sharing rejects forged member, lifecycle and frozen actions', async () => {
  for (const options of [{member:true},{frozen:true},{lifecycle:true}]) {
    const h=harness(options); await h.ui.showSharing(h.bot);
    await h.click('sharing-stop'); await h.click('sharing-stop');
    assert.ok(h.calls.every(([, method])=>method==='GET'));
    h.ui.stop();
  }
});

test('compact stop-sharing keeps remaining access visible after a partial failure', async () => {
  const h=harness({failRevoke:true});
  h.shares.push({user:person,talk:true,see_shared:false}, {user:{id:'reader',name:'Workspace reader'},talk:false,see_shared:true});
  await h.ui.showSharing(h.bot);
  await h.click('sharing-stop'); await h.click('sharing-stop');
  assert.deepEqual(h.ui.state.sheet.shares.map(row=>row.user.id), ['reader']);
  assert.match(h.regions.sheet.innerHTML, /Removal unavailable/);
  assert.doesNotMatch(h.regions.sheet.innerHTML, /data-access-saved/);
  h.ui.stop();
});
