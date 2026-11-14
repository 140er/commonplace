/** Owner invites on the Admin page, and the "Switch workspace" dialog. The invite link is held in memory only and shown once. */
const STATES = { pending: 'Open', accepted: 'Used', revoked: 'Revoked', expired: 'Expired' };

export function inviteBody(values) {
  const body = {}, email = String(values.email || '').trim(), days = Number(values.days);
  if (email) body.email = email;
  if (!Number.isInteger(days) || days < 1 || days > 30) throw new Error('Choose 1 to 30 days.');
  body.days = days;
  return body;
}

export function createInviteBlock({ api, e, copy = text => globalThis.navigator.clipboard.writeText(text) }) {
  let host = null, epoch = 0, invites = [], busy = false;
  const region = name => host?.querySelector(`[data-invite-${name}]`);
  const status = (text, bad = false) => {
    const node = region('status');
    if (node) { node.textContent = text; node.dataset.state = bad ? 'error' : 'ok'; }
  };
  const row = invite => `<tr><td data-label="For">${e(invite.email || 'Anyone with the link')}</td><td data-label="State">${e(STATES[invite.state] || invite.state)}${invite.accepted_by_email ? `<small>${e(invite.accepted_by_email)}</small>` : ''}</td><td data-label="Expires">${e(invite.expires_at)}</td><td data-label="Action">${invite.state === 'pending' ? `<button type="button" class="btn" data-invite-action="revoke" data-invite-id="${e(invite.id)}" aria-label="Revoke invite for ${e(invite.email || 'anyone with the link')}">Revoke</button>` : ''}</td></tr>`;
  const renderList = () => {
    const node = region('list');
    if (node) node.innerHTML = invites.length ? `<table><thead><tr>${['For', 'State', 'Expires', 'Action'].map(h => `<th scope="col">${h}</th>`).join('')}</tr></thead><tbody>${invites.map(row).join('')}</tbody></table>` : '<p>No invites yet.</p>';
  };
  const reveal = link => {
    region('reveal').innerHTML = `<div class="agent-secret" role="group" aria-label="New invite link"><p><strong>Copy this link now and send it yourself. It is not shown again.</strong></p><label>Invite link<input data-invite-link value="${e(link)}" readonly autocomplete="off" spellcheck="false"></label><button type="button" class="btn" data-invite-action="copy">Copy invite link</button><button type="button" class="btn" data-invite-action="dismiss">Done</button></div>`;
  };

  async function load() {
    const mine = epoch;
    try {
      const result = await api('/api/invites');
      if (mine !== epoch) return;
      invites = result.invites || [];
      renderList();
    } catch (error) {
      if (mine === epoch) status(error.message || 'Invites unavailable. Reload to try again.', true);
    }
  }

  async function run(work, done) {
    if (busy) return;
    busy = true; status('Saving…');
    const mine = epoch;
    try {
      const result = await work();
      if (mine !== epoch) return;
      status(done(result));
      await load();
    } catch (error) {
      if (mine === epoch) status(error.message || 'Request failed', true);
    } finally {
      busy = false;
    }
  }

  function onSubmit(event) {
    const form = event.target.closest?.('[data-invite-form]');
    if (!form) return;
    event.preventDefault();
    const values = { email: form.elements.email?.value, days: form.elements.days?.value };
    void run(() => api('/api/invites', { method: 'POST', body: inviteBody(values) }), result => {
      reveal(result.link);
      if (form.elements.email) form.elements.email.value = '';
      return 'Invite made. Copy the link below; it is not shown again.';
    });
  }

  async function onClick(event) {
    const button = event.target.closest?.('[data-invite-action]');
    if (!button) return;
    const action = button.dataset.inviteAction;
    if (action === 'dismiss') { region('reveal').replaceChildren(); status(''); return; }
    if (action === 'copy') {
      try { await copy(region('reveal').querySelector('[data-invite-link]').value); status('Copied.'); }
      catch { status('Copy failed. Select the link and copy it manually.', true); }
      return;
    }
    if (action === 'revoke') void run(() => api(`/api/invites/${encodeURIComponent(button.dataset.inviteId)}/revoke`, { method: 'POST' }), () => 'Invite revoked. Its link no longer works.');
  }

  function show(container) {
    stop();
    host = container;
    host.innerHTML = `<section class="settings-section invite-block" aria-labelledby="invite-heading"><h2 id="invite-heading">Invite people</h2>
      <p>An invite link lets one person join this workspace as a member. It works once and can be revoked until it is used. Members cannot read private wings, manage agents or open Admin.</p>
      <div data-invite-reveal></div><p role="status" aria-live="polite" data-invite-status></p>
      <form class="agent-form" data-invite-form><label>Email (optional; only this account can use the link)<input name="email" type="email" maxlength="254" autocomplete="off"></label>
      <label>Valid for (days)<input name="days" type="number" min="1" max="30" value="7" required></label>
      <button type="submit" class="btn">Make invite link</button></form>
      <div data-invite-list><p>Loading invites…</p></div></section>`;
    host.addEventListener('submit', onSubmit);
    host.addEventListener('click', onClick);
    void load();
  }

  function stop() {
    epoch++; busy = false; invites = [];
    if (host) {
      host.removeEventListener('submit', onSubmit);
      host.removeEventListener('click', onClick);
      host.innerHTML = '';
    }
    host = null;
  }

  return { show, stop };
}

// Lists only the caller's own workspaces; creation never switches without a separate click.
export async function openWorkspaces({ api, e, onError = () => {}, doc = globalThis.document, reload = () => globalThis.location.assign('/app/') }) {
  let result;
  try { result = await api('/api/workspaces'); }
  catch (error) { onError(error.message || 'Workspaces unavailable.'); return null; }
  doc.querySelector('dialog.workspace-switch')?.remove();
  const dialog = doc.createElement('dialog');
  const deck = doc.documentElement?.dataset.ui === 'deck';
  dialog.className = 'sheet menu-sheet workspace-switch';
  dialog.setAttribute('aria-labelledby', 'workspace-switch-h');
  const workspaces = result.workspaces || [];
  const items = workspaces.map(w => deck
    ? `<li><button type="button" class="workspace-row" data-workspace="${e(w.id)}"${w.current ? ' aria-current="true" aria-disabled="true"' : ''}><span class="workspace-label">${e(w.name)}</span><span class="workspace-role">${e(w.role)}</span><span class="workspace-current" aria-hidden="true">${w.current ? '✓' : ''}</span></button></li>`
    : `<li><button type="button" class="btn" data-workspace="${e(w.id)}"${w.current ? ' disabled aria-current="true"' : ''}>${e(w.name)} · ${e(w.role)}${w.current ? ' · current' : ''}</button></li>`).join('');
  let stage = 'list', name = '', requestId = '', busy = false, created = null, refusal = '';
  const button = (attribute, text, primary = false) => `<button type="button" class="btn${primary ? ' primary' : ''}" ${attribute}>${text}</button>`;
  function nameIssue(value) {
    const candidate = value.trim();
    if (!candidate) return 'Enter a workspace name.';
    if (candidate.length > 128) return 'Use 128 characters or fewer.';
    if (workspaces.some(w => w.name.trim().toLocaleLowerCase() === candidate.toLocaleLowerCase())) return 'You already have a workspace with this name. Choose a different name.';
    return '';
  }
  function updateName(form, showEmpty = false) {
    const field = form.elements.name, issue = nameIssue(field.value);
    const error = dialog.querySelector('[data-workspace-name-error]');
    if (error) error.textContent = field.value || showEmpty ? issue : '';
    field.setAttribute?.('aria-invalid', String(Boolean(issue && (field.value || showEmpty))));
    const submit = dialog.querySelector('[data-workspace-name] button[type="submit"]');
    if (submit) submit.disabled = Boolean(issue);
    return issue;
  }
  function render() {
    let title, content;
    if (stage === 'name') {
      title = 'Create workspace';
      content = `<form data-workspace-name><label for="workspace-name">Workspace name</label><input id="workspace-name" name="name" ${deck ? 'aria-describedby="workspace-name-error"' : 'maxlength="128"'} required autocomplete="off" value="${e(name)}">${deck ? '<p id="workspace-name-error" data-workspace-name-error role="status"></p>' : ''}<div class="workspace-actions"><button class="btn primary" type="submit"${deck && nameIssue(name) ? ' disabled' : ''}>Continue</button>${button('data-workspace-cancel', 'Cancel')}</div></form>`;
    } else if (stage === 'confirm') {
      title = deck ? `Create "${e(name)}"?` : 'Create this workspace?';
      content = deck
        ? `<p>This makes a new, empty workspace that only you can see. Your current workspace does not change.</p><div class="workspace-actions">${button('data-workspace-confirm', 'Create workspace', true)}${button('data-workspace-cancel', 'Cancel')}</div>`
        : `<p class="workspace-name">${e(name)}</p><p>This creates an empty isolated workspace owned by you. Your current workspace is unaffected.</p><p>Its data and members stay separate. No one else is added.</p><div class="workspace-actions">${button('data-workspace-confirm', 'Create workspace', true)}${button('data-workspace-cancel', 'Cancel')}</div>`;
    } else if (stage === 'result') {
      title = 'Workspace created';
      content = deck
        ? `<p>"${e(created.name)}" is ready. It is empty and only you are in it.</p><div class="workspace-actions">${button(`data-workspace="${e(created.id)}"`, 'Open workspace', true)}${button('data-workspace-close', 'Stay here')}</div>`
        : `<p class="workspace-name">${e(created.name)}</p><p>Your new workspace is empty and you are its only member and owner. Your current workspace is unaffected.</p><div class="workspace-actions">${button(`data-workspace="${e(created.id)}"`, 'Open workspace', true)}${button('data-workspace-close', 'Stay here')}</div>`;
    } else if (stage === 'refused') {
      title = 'Workspace not created';
      content = `<p data-workspace-refusal>${e(refusal)}</p><div class="workspace-actions workspace-close-row">${button('data-workspace-close', 'Close')}</div>`;
    } else {
      title = 'Switch workspace';
      content = deck
        ? `<ul class="workspace-list">${items}</ul>${button('data-workspace-create aria-label="Create workspace"', '+ Create workspace')}<div class="workspace-actions workspace-close-row">${button('data-workspace-close', 'Close')}</div>`
        : `<ul class="workspace-list">${items}</ul>${button('data-workspace-create', 'Create workspace', true)}${button('data-workspace-close', 'Close')}`;
    }
    dialog.setAttribute('data-workspace-stage', stage);
    dialog.innerHTML = `<div class="menu-body"><h2 id="workspace-switch-h" tabindex="-1">${title}</h2>${content}<p role="alert" data-workspace-error></p><p role="status" aria-live="polite" data-workspace-status></p></div>`;
    dialog.querySelector(stage === 'name' ? '[name="name"]' : '#workspace-switch-h')?.focus?.();
  }
  function lock(value) {
    busy = value;
    for (const control of dialog.querySelectorAll?.('button, input') || []) {
      if (!control.hasAttribute('aria-current')) control.disabled = value;
    }
    const status = dialog.querySelector('[data-workspace-status]');
    if (status) status.textContent = value ? 'Saving…' : '';
  }
  render();
  dialog.addEventListener('close', () => dialog.remove());
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  dialog.addEventListener('input', event => {
    const form = event.target.closest?.('[data-workspace-name]');
    if (deck && form) updateName(form);
  });
  dialog.addEventListener('submit', event => {
    const form = event.target.closest?.('[data-workspace-name]');
    if (!form) return;
    event.preventDefault();
    if (busy || stage !== 'name') return;
    if (deck && updateName(form, true)) return;
    name = form.elements.name.value.trim();
    if (!name) return;
    requestId = globalThis.crypto.randomUUID();
    stage = 'confirm'; render();
  });
  dialog.addEventListener('click', async event => {
    if (busy) return;
    const target = event.target;
    if (target.closest?.('[data-workspace-close]')) { dialog.close(); return; }
    if (target.closest?.('[data-workspace-create]')) { stage = 'name'; name = ''; render(); return; }
    if (target.closest?.('[data-workspace-cancel]')) { stage = 'list'; requestId = ''; render(); return; }
    const confirm = target.closest?.('[data-workspace-confirm]');
    const selected = target.closest?.('[data-workspace]');
    if (!(confirm && stage === 'confirm') && !selected) return;
    if (deck && selected && workspaces.some(w => w.id === selected.dataset.workspace && w.current)) return;
    lock(true);
    dialog.querySelector('[data-workspace-error]').textContent = '';
    try {
      if (confirm && stage === 'confirm') {
        const response = await api('/api/workspaces', { method: 'POST', body: { name, request_id: requestId } });
        created = response.workspace;
        stage = 'result'; render();
      } else {
        await api('/api/workspaces/switch', { method: 'POST', body: { workspace: selected.dataset.workspace } });
        reload();
      }
    } catch (error) {
      if (deck && confirm && error.status >= 400 && error.status < 500) {
        const reason = error.payload?.error || error.message || 'Could not create workspace.';
        refusal = ({
          'This instance is closed to new workspaces.': 'New workspaces are turned off on this server.',
          'You have created five workspaces recently. Try again in an hour.': 'Too many new workspaces. Try again in 60 minutes.',
        })[reason] || reason;
        stage = 'refused'; render();
      } else dialog.querySelector('[data-workspace-error]').textContent = error.message || (confirm ? 'Could not create workspace. Try again.' : 'Could not switch workspace.');
    } finally { lock(false); }
  });
  doc.body.append(dialog);
  dialog.showModal();
  return dialog;
}
