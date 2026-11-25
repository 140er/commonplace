import assert from 'node:assert/strict';
import { createDeckShell, shellTrees } from '../commonplace/web/static/deck-shell.js';

const mode = process.argv[2];
const nodes = Object.fromEntries(['[data-deck-bots]', '[data-deck-threads]', '[data-deck-rail-error]'].map(key => [key, { innerHTML: '' }]));
let failure = mode === 'error';
const shell = createDeckShell({ root: { querySelector: key => nodes[key], addEventListener() {} },
  on: () => true, context: () => ({ me: { actor: 'user:reader', workspace: 'workspace' } }),
  api: async path => {
    if (failure) throw Object.assign(new Error('network unavailable'), { status: mode === 'denied' ? 403 : undefined });
    return path.endsWith('/bots') ? { bots: mode === 'empty' ? [] : [{ id: 'team', name: 'Team bot' }] } : { conversations: [] };
  },
});
if (mode === 'loading') {
  const initial = shellTrees(shell.options());
  assert.match(initial, /Still loading bots/);
  assert.doesNotMatch(initial, /No bots yet/);
} else {
  shell.roster(['stale', 'denied'].includes(mode) ? [{ id: 'personal', name: 'Personal bot' }] : []);
  await shell.sync();
  if (mode === 'empty') assert.match(nodes['[data-deck-bots]'].innerHTML, /No bots yet/);
  else if (mode === 'error') {
    assert.doesNotMatch(nodes['[data-deck-bots]'].innerHTML, /No bots yet/);
    assert.match(nodes['[data-deck-bots]'].innerHTML, /Could not load/);
    assert.match(nodes['[data-deck-rail-error]'].innerHTML, /Retry/);
  } else {
    assert.match(nodes['[data-deck-bots]'].innerHTML, /Team bot/);
    failure = true;
    await shell.sync();
    if (mode === 'denied') assert.doesNotMatch(nodes['[data-deck-bots]'].innerHTML, /Team bot/);
    else assert.match(nodes['[data-deck-bots]'].innerHTML, /Team bot/);
    assert.match(nodes['[data-deck-bots]'].innerHTML, /Personal bot/);
    assert.match(nodes['[data-deck-rail-error]'].innerHTML, /Retry/);
  }
}
