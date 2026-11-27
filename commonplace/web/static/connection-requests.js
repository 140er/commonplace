import { escape as e } from './api.js';

export function connectionRequestHTML(item) {
  return `<article class="bt-card row-item" data-connection-request="${e(item.id)}"><div><header><b>${e(item.requester_name)}</b> requests a connection</header><h2>${e(item.name)}</h2>${item.note ? `<p class="quote">${e(item.note)}</p>` : ''}<p>Approve a connection for this person for 90 days. It can read shared memory and add work that needs release. Only this person can collect its token.</p><label>Reason if declined (optional)<textarea data-connection-reason maxlength="2000" rows="2"></textarea></label><div class="bt-actions"><button type="button" class="btn primary bt-primary" data-connection-action="approve">Approve</button><button type="button" class="btn bt-secondary" data-connection-action="decline">Decline</button></div><p role="alert" data-connection-error></p></div></article>`;
}

export async function connectionRequestClick(button, { items, api, done, stopped = () => false }) {
  const row = button.closest('[data-connection-request]');
  const item = items.find(item => item.id === row?.dataset.connectionRequest);
  const action = button.dataset.connectionAction;
  if (!item || button.disabled || row.dataset.connectionBusy || !['approve', 'decline'].includes(action)) return;
  row.dataset.connectionBusy = 'true';
  const buttons = [...row.querySelectorAll('button')];
  buttons.forEach(button => { button.disabled = true; });
  const error = row.querySelector('[data-connection-error]');
  error.textContent = '';
  try {
    await api(`/api/connection-requests/${encodeURIComponent(item.id)}/${action}`, {
      method: 'POST', body: action === 'decline' ? { reason: row.querySelector('[data-connection-reason]').value } : {},
    });
    if (!stopped()) done(item);
  } catch (failure) {
    if (!stopped()) error.textContent = failure.message || 'Could not send your decision. Refresh and try again.';
  } finally {
    delete row.dataset.connectionBusy;
    buttons.forEach(button => { button.disabled = false; });
  }
}
