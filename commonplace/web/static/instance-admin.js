/** Metadata-only instance management; each section settles and retries independently. */
import { boundedRequest } from './admin.js';

const DENIAL = 'Instance administrator access required';
const LABELS = { people: 'People', connections: 'Connections', spaces: 'Spaces/projects', settings: 'Settings/limits', audit: 'Audit' };
const FIELDS = {
  people: [['user_id', 'User ID'], ['role', 'Role'], ['disabled', 'Disabled']],
  connections: [['user_id', 'User ID'], ['state', 'State']],
  audit: [['actor_user_id', 'Actor user ID'], ['actor_connection_id', 'Actor connection ID'], ['action', 'Action'], ['decision', 'Decision'], ['from', 'From (UTC)'], ['to', 'To (UTC)']],
};

export function createInstanceAdmin({ mount, api, e, current, timers = globalThis, denied = () => {} }) {
  let host, live = false, epoch = 0, target = '', session = '';
  const sections = new Map(), requests = new Map(), generations = new Map(), filters = new Map(), drafts = new Map();
  const eligible = () => live && current()?.can_manage_instance === true && current()?.workspace === session;
  const value = v => e(v == null || v === '' ? '—' : String(v));
  const table = (heads, rows) => rows.length ? `<div class="instance-table"><table><thead><tr>${heads.map(h => `<th scope="col">${e(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map((v, i) => `<td data-label="${e(heads[i])}">${value(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : '<p>No matching metadata.</p>';

  function content(key, data) {
    if (key === 'spaces' && data.state === 'unknown') return '<p>Not configured. No store has been created.</p>';
    if (key === 'people') return table(['Person', 'Email', 'Workspace', 'Role', 'State', 'Instance admin'], data.items.map(r => [r.name || r.id, r.email, r.workspace_id, r.role, r.disabled_at ? 'Disabled' : 'Active', r.is_admin ? 'Yes' : 'No']));
    if (key === 'connections') return table(['Connection', 'User', 'Workspace', 'Auth', 'State', 'Trust', 'Created', 'Last use'], data.items.map(r => [r.name, r.user_id, r.workspace_id, r.auth, r.state, r.trust, r.created_at, r.last_used_at]));
    if (key === 'spaces') return table(['Space/project', 'State', 'Coordinator', 'Configured cap', 'Effective cap', 'Source'], data.items.map(r => [r.name, r.archived ? 'Archived' : 'Active', r.coordinator, r.configured_max_agents ?? 'Unlimited', r.effective_max_agents ?? 'Unlimited', r.cap_source]));
    if (key === 'audit') return table(['Time', 'Action', 'Decision', 'Actor', 'Workspace'], data.items.map(r => [r.at, r.action, r.decision, r.actor_user_id || r.actor_connection_id, r.workspace_id]));
    if (key === 'settings') return table(['Operator setting', 'Effective value'], [['Plans enabled', data.plans_enabled ? 'Yes' : 'No'], ['Rate limits enabled', data.rate_limits_enabled ? 'Yes' : 'No'], ['Signed-in requests / window', `${data.authenticated_requests} / ${data.authenticated_window_seconds} seconds`], ['Request body bytes', data.request_bytes]]);
    const limits = data.limits;
    return table(['Workspace setting', 'Effective value', 'Source'], [['Workspace ID', data.workspace?.id, 'Workspace'], ['Workspace name', data.workspace?.name, 'Workspace'], ['Personal account', data.workspace?.personal_of, 'Workspace'], ['Account', limits.account_id, limits.source], ['Plan', limits.effective_plan || 'No account-plan limit', limits.source], ['Storage bytes', limits.storage_bytes ?? 'Unlimited', limits.source], ['Transcription seconds/day', limits.transcription_seconds ?? 'Unlimited', limits.source], ['Orchestration', data.orchestration_enabled ? 'Enabled' : 'Disabled', 'Workspace']]);
  }

  function panel(key) {
    const item = sections.get(key), busy = requests.has(key);
    if (!item) return '';
    const button = (action, text) => `<button type="button" class="btn" data-instance-section="${key}" data-instance-action="${action}"${busy ? ' disabled' : ''}>${e(text)}</button>`;
    return `<div data-instance-panel="${key}" aria-busy="${busy}">${item.data ? content(key, item.data) : ''}${busy ? '<p role="status">Loading…</p>' : ''}${item.error ? `<p role="status">${e(item.error)}</p>${button('retry', 'Retry')}` : ''}${item.data ? `<p class="quiet">${key === 'workspace_settings' || item.data.count == null ? '' : `${e(item.data.items?.length || 0)} of ${e(item.data.count)} · `}Recorded ${value(item.data.recorded_at)}</p>${button('refresh', 'Refresh')}${key !== 'workspace_settings' && item.data.has_more ? button('more', 'Load more') : ''}` : ''}</div>`;
  }

  function render() {
    if (!live || !host) return;
    for (const form of host.querySelectorAll?.('[data-instance-filter]') || []) {
      drafts.set(form.dataset.instanceFilter, Object.fromEntries(new FormData(form)));
    }
    const list = sections.get('workspaces'), spaces = list?.data?.items || [];
    host.innerHTML = `<div class="instance-admin settings" data-ui><p>Management metadata only; viewing grants no workspace membership or content access.</p><label>Workspace <select data-instance-workspace><option value="">All workspaces</option>${spaces.map(w => `<option value="${e(w.id)}"${w.id === target ? ' selected' : ''}>${e(w.name)}</option>`).join('')}${target && !spaces.some(w => w.id === target) ? `<option selected value="${e(target)}">${e(target)}</option>` : ''}</select></label>${requests.has('workspaces') ? '<p role="status">Loading workspaces…</p>' : ''}${list?.error ? `<p role="status">Workspaces unavailable</p><button class="btn" data-instance-section="workspaces" data-instance-action="retry">Retry workspaces</button>` : ''}${list?.data?.has_more ? '<button class="btn" data-instance-section="workspaces" data-instance-action="more">More workspaces</button>' : ''}<p class="quiet">People, connection, space and setting changes are not available in this read-only console.</p><div class="instance-grid">${Object.entries(LABELS).map(([key, title]) => `<section class="settings-section" data-instance-area="${key}"><h2>${e(title)}</h2>${FIELDS[key] ? `<form data-instance-filter="${key}">${FIELDS[key].map(([field, label]) => `<label>${e(label)}<input name="${field}" value="${e(drafts.get(key)?.[field] ?? filters.get(key)?.[field] ?? '')}" autocomplete="off"></label>`).join('')}<button class="btn" type="submit">Apply filters</button></form>` : ''}${key === 'settings' ? '<h3>Instance · operator configured</h3>' : ''}${key === 'spaces' && !target ? '<p>Select a workspace to inspect its spaces/projects.</p>' : panel(key)}${key === 'settings' ? '<h3>Workspace</h3>' + (target ? panel('workspace_settings') : '<p>Select a workspace to inspect its limits.</p>') : ''}</section>`).join('')}</div></div>`;
  }

  function stop() {
    live = false; epoch++;
    for (const controller of requests.values()) controller.abort();
    requests.clear(); sections.clear(); generations.clear(); filters.clear(); drafts.clear();
    host?.removeEventListener('click', click);
    host?.removeEventListener('change', change);
    host?.removeEventListener('submit', submit);
    globalThis.document?.removeEventListener('session-expired', stop);
    globalThis.document?.removeEventListener('signout', stop);
    globalThis.addEventListener && globalThis.removeEventListener('pagehide', stop);
    if (host) host.innerHTML = '';
    target = ''; session = '';
  }

  function restrict() {
    stop();
    if (host) host.innerHTML = `<p role="status">${DENIAL}</p>`;
    denied();
  }

  function refreshSection(key, { more = false } = {}) {
    if (!eligible()) { if (live) restrict(); return; }
    if (requests.has(key) || !sections.has(key)) return;
    if (['spaces', 'workspace_settings'].includes(key) && !target) return;
    const item = sections.get(key), cursor = more ? item.retryCursor || item.data?.next_cursor : null;
    if (more && !cursor) return;
    const generation = (generations.get(key) || 0) + 1, pageEpoch = epoch, selected = target;
    generations.set(key, generation);
    const controller = new AbortController();
    requests.set(key, controller); item.error = ''; item.retryCursor = cursor;
    const query = new URLSearchParams(key === 'settings' ? {} : { limit: '20', ...(filters.get(key) || {}) });
    if (selected && ['people', 'connections', 'audit'].includes(key)) query.set('workspace_id', selected);
    if (cursor) query.set('cursor', cursor);
    const path = ['spaces', 'workspace_settings'].includes(key) ? `workspaces/${encodeURIComponent(selected)}/${key === 'spaces' ? 'spaces' : 'settings'}` : key;
    const valid = () => {
      if (live && !eligible()) restrict();
      return eligible() && epoch === pageEpoch && target === selected && generations.get(key) === generation;
    };
    render();
    boundedRequest(api, `/api/instance-admin/${path}?${query}`, { timers, signal: controller.signal }).then(data => {
      if (!valid()) return;
      item.data = more ? { ...data, items: [...(item.data?.items || []), ...(data.items || [])] } : data;
      item.retryCursor = null;
    }).catch(error => {
      if (!valid()) return;
      if (error.status === 403) { restrict(); return; }
      if (error.status === 401) { stop(); return; }
      if (error.status === 400 && cursor) {
        item.retryCursor = null;
        if (item.data) item.data = { ...item.data, has_more: false, next_cursor: null };
        item.error = 'Page expired. Refresh to load current metadata.';
        return;
      }
      if (error.name !== 'AbortError') item.error = 'Metadata unavailable; try again';
    }).finally(() => {
      if (!valid()) return;
      requests.delete(key); render();
    });
  }

  function load() {
    for (const key of ['workspaces', ...Object.keys(LABELS), 'workspace_settings']) {
      sections.set(key, { data: null, error: '', retryCursor: null });
      refreshSection(key);
    }
  }

  function selectWorkspace(value) {
    if (!eligible()) return;
    epoch++;
    for (const request of requests.values()) request.abort();
    requests.clear();
    const workspaceList = sections.get('workspaces');
    sections.clear(); filters.clear(); drafts.clear(); host.innerHTML = ''; target = value;
    load();
    if (workspaceList?.data) sections.get('workspaces').data = workspaceList.data;
    render();
  }

  function click(event) {
    const button = event.target.closest('[data-instance-action]');
    if (!button) return;
    const key = button.dataset.instanceSection;
    refreshSection(key, { more: button.dataset.instanceAction === 'more' || button.dataset.instanceAction === 'retry' && !!sections.get(key)?.retryCursor });
  }
  function change(event) { if (event.target.matches('[data-instance-workspace]')) selectWorkspace(event.target.value); }
  function submit(event) {
    const form = event.target.closest('[data-instance-filter]');
    if (!form) return;
    event.preventDefault();
    if (!eligible()) { if (live) restrict(); return; }
    const key = form.dataset.instanceFilter;
    requests.get(key)?.abort(); requests.delete(key);
    generations.set(key, (generations.get(key) || 0) + 1);
    filters.set(key, Object.fromEntries([...new FormData(form)].filter(([, v]) => v !== '')));
    sections.set(key, { data: null, error: '', retryCursor: null });
    refreshSection(key);
  }

  function show() {
    stop(); host = mount();
    if (!host) return;
    if (current()?.can_manage_instance !== true) { host.innerHTML = `<p role="status">${DENIAL}</p>`; return; }
    live = true; session = current().workspace; target = session;
    host.addEventListener('click', click); host.addEventListener('change', change); host.addEventListener('submit', submit);
    globalThis.document?.addEventListener('session-expired', stop);
    globalThis.document?.addEventListener('signout', stop);
    globalThis.addEventListener?.('pagehide', stop);
    load(); render();
  }
  return { show, stop, selectWorkspace, refreshSection };
}
