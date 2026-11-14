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
export function actionLimit(action) {
  const limit = action.limit_description ?? action.limit;
  return typeof limit === 'string' ? limit.trim() : '';
}
export function capyPermissionRows(actions, modes, filter, escape) {
  let html = permissionRows(actions, modes, filter, escape).replace(/<div class="setting-row" data-tool-policy-row="([^"]*)">[\s\S]*?<\/select><\/div>/g, (row, name) => {
    const action = actions.find(item => escape(item.name) === name);
    if (!action) return row;
    const selected = policyState(action.mode), limit = actionLimit(action);
    let select = row.match(/<select [\s\S]*?<\/select>/)[0];
    if (!modes.includes(action.mode)) select = select.replace('</select>', `<option value="${escape(action.mode)}" selected>${escape(action.mode)}</option></select>`);
    return `<div class="setting-row" data-tool-policy-row="${name}" title="${name}"><div class="deck-permission-label"><span>${escape(permissionLabel(action))}</span>${limit ? `<small class="capy-policy-limit">${escape(limit)}</small>` : ''}</div><div class="capy-policy" role="group" aria-label="Permission for ${escape(permissionLabel(action))}">${select.replace('<select ', '<select hidden tabindex="-1" aria-hidden="true" ')}${STATES.map(state => `<button type="button" data-policy-state="${state}" aria-pressed="${state === selected}"${policyValue(state, action.mode, modes) === null ? ' disabled' : ''}>${LABELS[state]}</button>`).join('')}<span class="sr" data-policy-stored>${escape(action.mode)}</span></div></div>`;
  });
  // A miscellaneous heading adds no information beside just one named group.
  if ((html.match(/class="deck-permission-group"/g) || []).length <= 2) {
    html = html.replace('</section><section class="deck-permission-group"><h3>Other actions</h3>', '');
    html = html.replace('<h3>Other actions</h3>', '');
  }
  return html;
}
export function policySummary(actions) {
  const ask = actions.filter(action => policyState(action.mode) === 'ask').length;
  const denied = actions.filter(action => policyState(action.mode) === 'deny').length;
  return ask || denied ? `${ask} ask first · ${denied} denied · the rest allowed` : 'Every action is allowed';
}
export function settingsSubtitle(me = {}) {
  return `For the ${me.workspace_name || 'current'} workspace. ${me.role === 'owner' ? 'Only you' : 'Only the owner'} can change these.`;
}
export const capyPolicyPresentation = {
  ...commonPolicy, rows: capyPermissionRows, summary: policySummary, placeholder: 'Search actions',
  filterChanged(host) {
    const frame = host.closest?.('.capy-settings'), field = host.querySelector('[data-tool-policy-filter]');
    if (!frame || !field) return;
    const top = field.getBoundingClientRect().top, edge = frame.getBoundingClientRect().top;
    if (top < edge || top > edge + frame.clientHeight / 2) frame.scrollBy({ top: top - edge - 12 });
  },
  introduction: () => 'Ask sends the request to Today first. Deny refuses it.',
  bind(host, data) {
    const readOnly = () => host.closest?.('.capy-settings')?.dataset?.settingsOwner === 'false';
    if (readOnly()) host.querySelectorAll?.('[data-policy-state], [data-tool-policy-mode]').forEach(control => { control.disabled = true; });
    host.querySelector('[data-tool-policy-list]').onclick = event => {
      const button = event.target.closest?.('[data-policy-state]');
      if (!button || button.disabled || readOnly()) return;
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
  const rank = id => ['look', 'deck-bot-sharing', 'tool-policy', 'deck-second-review', 'deck-connections'].indexOf(id);
  return [...sections].sort((a, b) => (rank(a.id) < 0 ? 5 : rank(a.id)) - (rank(b.id) < 0 ? 5 : rank(b.id)));
}
export const settingsLayout = width => width >= 1100 ? 'columns' : 'stack';

export function arrangeCapySettings(box, { api, context = () => ({}) } = {}) {
  const doc = box.ownerDocument;
  if (!doc) return;
  box.classList.add('capy-settings');
  const me = context()?.me || {}, owner = me.role === 'owner';
  if (box.dataset) box.dataset.settingsOwner = String(owner);
  const appearance = box.querySelector('[data-deck-appearance]');
  if (appearance) {
    const fill = input => input.style.setProperty('--range-fill', `${(Number(input.value) - Number(input.min)) / (Number(input.max) - Number(input.min)) * 100}%`);
    appearance.querySelectorAll('input[type="range"]').forEach(fill);
    if (!appearance.dataset.capySliders) {
      appearance.dataset.capySliders = 'true';
      appearance.addEventListener('input', event => { if (event.target.matches('input[type="range"]')) fill(event.target); });
    }
  }
  const main = box.querySelector('.deck-settings-main');
  if (!main) return;
  const subtitle = box.querySelector('.capy-settings-subtitle') || doc.createElement('p');
  subtitle.className = 'capy-settings-subtitle'; subtitle.textContent = settingsSubtitle(me);
  box.prepend?.(subtitle);
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
    sharing.innerHTML = '<h2>Bot sharing</h2><p>Private · Ask before sharing. Each person’s own permissions still apply.</p><div data-capy-sharing><p role="status">Loading sharing settings…</p></div><div class="capy-always"><h3>Always on</h3><div class="capy-setting-note"><b>New bots start private</b><small>Only you can talk to a new bot until you share it.</small></div><div class="capy-setting-note"><b>Keep an access log</b><small>Every grant and removal, with who and when.</small></div><div class="capy-setting-note"><b>Only a bot’s owner changes its access</b><small>Others can ask; the owner decides.</small></div></div><a class="capy-settings-link" href="#/bots">Manage who can use each bot<span aria-hidden="true">›</span></a>';
    main.prepend(sharing);
    mountSharing(sharing.querySelector('[data-capy-sharing]'), api, () => context()?.me?.role === 'owner');
  }
  if (!main.querySelector('#deck-second-review')) {
    const review = doc.createElement('section');
    review.className = 'settings-section'; review.id = 'deck-second-review';
    const boards = context()?.boards || [];
    review.innerHTML = `<h2>Second review</h2><p>Review settings belong to each project. Open a project’s settings to manage its reviewers and review changes.</p>${boards.length ? boards.map(board => `<button class="btn" type="button" data-action="coordinator" data-board="${e(board.slug)}">${e(board.name || board.slug)} review settings</button>`).join('') : '<p>No projects configured.</p>'}`;
    main.append(review);
  }
  settingsOrder([...main.children]).forEach(section => main.append(section));
  if (me.role && !owner) main.querySelectorAll?.('input, select, textarea, button').forEach(control => {
    // Session/navigation, personal notifications/webhooks and own bot grants keep their controllers.
    if (control.matches('[data-action="logout"], [data-action="agent-bots"]')) return;
    if (control.matches('[data-policy-state], [data-tool-policy-mode]') || control.closest('[data-workspace-orchestration], [data-agent-schedules], [data-voice-settings], [data-form="agent"], [data-agent-list]')) control.disabled = true;
  });
}

function mountSharing(host, api, owner) {
  if (!api) { host.textContent = 'Sharing settings unavailable.'; return; }
  let data, busy = false, error = '';
  function render() {
    if (!host.isConnected) return;
    host.innerHTML = data ? `<label class="capy-sharing-switch"><span>Allow bot sharing<small>Off pauses everyone’s access. Nothing is deleted.</small></span><input type="checkbox" role="switch" aria-label="Allow bot sharing"${data.sharing ? ' checked' : ''}${busy || !data.can_configure || !owner() ? ' disabled' : ''}></label>${(!data.can_configure || !owner()) ? '<p>Only a workspace owner can change this.</p>' : ''}` : '';
    if (error) host.insertAdjacentHTML('beforeend', `<p role="alert">${e(error)}</p><button type="button" data-sharing-retry>Try again</button>`);
    const retry = host.querySelector('[data-sharing-retry]');
    if (retry) retry.onclick = load;
    const input = host.querySelector('input');
    if (input) input.onchange = async () => {
      if (busy || !data.can_configure || !owner()) return;
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
