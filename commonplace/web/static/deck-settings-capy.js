// Settings presentation; the existing controllers and server remain the authority.
import { escape as e } from './api.js';
import { policyPresentation as commonPolicy, permissionRows, permissionLabel } from './deck-settings.js';

const STATES = ['allow', 'ask', 'deny'];
const LABELS = { allow: 'Allow', ask: 'Ask', deny: 'Deny' };
const VALUES = { allow: ['allowed', 'allow'], ask: ['ask', 'ask_first', 'approval_required'], deny: ['off', 'deny', 'denied', 'blocked'] };
export function policyState(value) {
  return STATES.find(state => VALUES[state].includes(value)) || 'ask';
}
// A label is a view of the stored value. Selecting it again must never normalize that value.
export function policyValue(state, stored, modes) {
  if (policyState(stored) === state) return stored;
  return VALUES[state]?.find(value => modes.includes(value)) ?? null;
}
export function capyPermissionRows(actions, modes, filter, escape) {
  return permissionRows(actions, modes, filter, escape).replace(/<select data-tool-policy-mode="[\s\S]*?<\/select>/g, (select) => {
    const name = select.match(/data-tool-policy-mode="([^"]*)"/)[1];
    const action = actions.find(item => escape(item.name) === name);
    const selected = policyState(action.mode);
    // Include even an unrecognized stored mode in the hidden, existing save control.
    const exact = modes.includes(action.mode) ? select : select.replace('</select>', `<option value="${escape(action.mode)}" selected>${escape(action.mode)}</option></select>`);
    return `<div class="capy-policy" role="group" aria-label="Permission for ${escape(permissionLabel(action))}">${exact.replace('<select ', '<select hidden tabindex="-1" aria-hidden="true" ')}${STATES.map(state => `<button type="button" data-policy-state="${state}" aria-pressed="${state === selected}"${policyValue(state, action.mode, modes) === null ? ' disabled' : ''}>${LABELS[state]}</button>`).join('')}<span class="sr" data-policy-stored>${escape(action.mode)}</span></div>`;
  });
}
export const capyPolicyPresentation = {
  ...commonPolicy, rows: capyPermissionRows,
  filterChanged(host) {
    const frame = host.closest?.('.capy-settings'), field = host.querySelector('[data-tool-policy-filter]');
    if (!frame || !field) return;
    const top = field.getBoundingClientRect().top, edge = frame.getBoundingClientRect().top;
    if (top < edge || top > edge + frame.clientHeight / 2) frame.scrollBy({ top: top - edge - 12 });
  },
  introduction: () => 'Allow runs without asking. Ask puts the action in Needs me. Deny refuses it. Each bot’s own permissions still apply.',
  bind(host, data) {
    host.querySelector('[data-tool-policy-list]').onclick = event => {
      const button = event.target.closest?.('[data-policy-state]');
      if (!button || button.disabled) return;
      const group = button.closest('.capy-policy'), select = group.querySelector('[data-tool-policy-mode]');
      const value = policyValue(button.dataset.policyState, select.value, data.modes);
      if (value === null || value === select.value || select.disabled) return;
      group.querySelectorAll('button').forEach(item => { item.disabled = true; });
      select.value = value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
    };
  },
};

const CLIENTS = { 'claude-code': 'Command-line app', codex: 'Coding app', cursor: 'Editor app', generic: 'Other app' };
export const connectionLabel = client => Object.hasOwn(CLIENTS, client) ? CLIENTS[client] : 'Connected app';
const SCOPES = { 'events:person': 'Your activity', 'cards:decide': 'Answer your questions', 'bots:chat': 'Talk to your bots', 'bots:confirm': 'Confirm bot actions', 'bots:learned': 'Read learned memories' };
export function scopeHTML(scope) {
  return `<span class="capy-scope">${e(Object.hasOwn(SCOPES, scope) ? SCOPES[scope] : 'App permission')} <code>${e(scope)}</code></span>`;
}

export function settingsOrder(sections) {
  const rank = id => ['deck-bot-sharing', 'tool-policy', 'deck-second-review', 'deck-connections'].indexOf(id);
  return [...sections].sort((a, b) => (rank(a.id) < 0 ? 4 : rank(a.id)) - (rank(b.id) < 0 ? 4 : rank(b.id)));
}
export const settingsLayout = width => width >= 1100 ? 'columns' : 'stack';

export function arrangeCapySettings(box, { api, context = () => ({}) } = {}) {
  const doc = box.ownerDocument;
  if (!doc) return;
  box.classList.add('capy-settings');
  const main = box.querySelector('.deck-settings-main');
  if (!main) return;
  // The old shortcuts still reach Notifications, Connections and Workspace, below the main sections.
  const nav = main.querySelector('#deck-settings-nav');
  if (nav) nav.setAttribute('aria-label', 'More settings sections');
  const policy = main.querySelector('#tool-policy h2');
  if (policy) policy.textContent = 'What bots may do without asking';
  const connections = main.querySelector('#deck-connections h2');
  if (connections) connections.textContent = 'Connected apps';
  if (!main.querySelector('#deck-bot-sharing')) {
    const sharing = doc.createElement('section');
    sharing.className = 'settings-section'; sharing.id = 'deck-bot-sharing';
    sharing.innerHTML = '<h2>Bot sharing</h2><p>Private · Ask before sharing. Each person’s own permissions still apply.</p><div data-capy-sharing><p role="status">Loading sharing settings…</p></div><div class="capy-setting-note"><b>New bots start private</b><small>Only you can talk to a new bot until you share it.</small></div><div class="capy-setting-note"><b>Keep an access log</b><small>Every grant and removal, with who and when.</small></div><div class="capy-setting-note"><b>Only a bot’s owner changes its access</b><small>Others can ask; the owner decides.</small></div><a class="capy-settings-link" href="#/bots">Manage who can use each bot</a>';
    main.prepend(sharing);
    mountSharing(sharing.querySelector('[data-capy-sharing]'), api);
  }
  if (!main.querySelector('#deck-second-review')) {
    const review = doc.createElement('section');
    review.className = 'settings-section'; review.id = 'deck-second-review';
    const boards = context()?.boards || [];
    review.innerHTML = `<h2>Second review</h2><p>Review settings belong to each project. Open a project’s settings to manage its reviewers and review changes.</p>${boards.length ? boards.map(board => `<button class="btn" type="button" data-action="coordinator" data-board="${e(board.slug)}">${e(board.name || board.slug)} review settings</button>`).join('') : '<p>No projects configured.</p>'}`;
    main.append(review);
  }
  settingsOrder([...main.children]).forEach(section => main.append(section));
}

function mountSharing(host, api) {
  if (!api) { host.textContent = 'Sharing settings unavailable.'; return; }
  let data, busy = false, error = '';
  function render() {
    if (!host.isConnected) return;
    host.innerHTML = data ? `<label class="capy-sharing-switch"><span>Allow bot sharing<small>Turning this off suspends access without deleting shares.</small></span><input type="checkbox" role="switch" aria-label="Allow bot sharing"${data.sharing ? ' checked' : ''}${busy || !data.can_configure ? ' disabled' : ''}></label>${!data.can_configure ? '<p>Only a workspace owner can change this.</p>' : ''}` : '';
    if (error) host.insertAdjacentHTML('beforeend', `<p role="alert">${e(error)}</p><button type="button" data-sharing-retry>Try again</button>`);
    const retry = host.querySelector('[data-sharing-retry]');
    if (retry) retry.onclick = load;
    const input = host.querySelector('input');
    if (input) input.onchange = async () => {
      if (busy || !data.can_configure) return;
      const sharing = input.checked; busy = true; error = ''; render();
      try { data = await api('/api/bots/settings', { method: 'PATCH', body: { sharing }, timeoutMs: 10000 }); }
      catch { error = 'Could not save bot sharing. Your previous setting is kept.'; }
      finally { busy = false; render(); }
    };
  }
  async function load() {
    try { data = await api('/api/bots/settings', { timeoutMs: 10000 }); error = ''; }
    catch { error = 'Could not load bot sharing.'; }
    render();
  }
  void load();
}
