/** Browser-only setup consent. The server remains the authority for every action. */
import { escape as e } from './api.js';

export const consentStyle = `
.runner-consent { margin:12px 0; padding:16px; background:var(--surface); border:1px solid var(--line); border-radius:var(--r); }
.runner-consent h2 { font-size:24px; line-height:30px; margin:0 0 12px; }
.runner-consent p { margin:8px 0; line-height:1.5; overflow-wrap:anywhere; }
.runner-consent .fingerprint { font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
.runner-consent button,.runner-consent a { min-height:44px; }
.runner-consent label { display:flex; gap:8px; align-items:start; margin:12px 0; }
:root[data-ui="deck"] .runner-consent .btn { min-height:44px; }
.runner-consent output { display:block; overflow-wrap:anywhere; }
`;
const provider = name => ({ codex: 'Codex', claude: 'Claude' }[name] || 'Selected provider');
export function runnerConsent(item) {
  const title = item.approval_kind === 'login' ? `Sign in to ${provider(item.provider)}` : item.approval_kind === 'attest' ? 'Confirm this machine' : 'Approve machine access';
  const unsupported = item.unsupported;
  const description = unsupported ? 'Browser-only sign-in is unavailable for this provider. Setup stays incomplete. No terminal code or credential is requested.' :
    item.approval_kind === 'login' ? 'Allow this machine to start a subscription sign-in. Complete it on the provider’s own website. Your login stays on the machine.' :
    item.approval_kind === 'attest' ? 'Compare the fingerprint below with the one shown when you installed the machine. Confirm only if every group matches.' :
    'Allow this machine to use the projects and topics below, within the shown access limit. The private access file is delivered automatically.';
  const action = item.approval_kind === 'attest' ? 'Confirm matching machine' : item.approval_kind === 'login' ? 'Allow sign-in' : 'Approve access';
  const open = item.state === 'approved' && item.approval_kind === 'login';
  return `<article class="runner-consent" data-runner-consent-card="${e(item.id)}"><h2>${e(title)}</h2><p>${e(item.label)}</p><p>${e(description)}</p><p>Projects: ${e((item.projects || []).join(', '))}<br>Topics: ${e((item.topics || []).join(', '))}<br>Access limit: ${e(item.runtime === 'approval-required' ? 'Ask before each action' : item.runtime)}</p><p>Machine fingerprint</p><p class="fingerprint">${e((item.fingerprint || '').match(/.{1,8}/g)?.join(' ') || '')}</p>${unsupported ? '' : `<label><input type="checkbox" data-runner-fingerprint> I compared this fingerprint with the machine I enrolled.</label><button class="btn" data-runner-consent="${e(item.id)}" data-runner-consent-action="${open ? 'device' : item.approval_kind}">${e(open ? 'Show sign-in link and code' : action)}</button>`}<output data-runner-consent-status role="status"></output><p>This machine cannot run work yet.</p></article>`;
}

export async function decideRunner(api, item, action) {
  const root = `/api/runners/${encodeURIComponent(item.machine_id)}`;
  if (action === 'device') return api(`${root}/setup-requests/${encodeURIComponent(item.id)}/device`, { method: 'POST', body: {} });
  if (action === 'attest') return api(`${root}/attest`, { method: 'POST', body: { ...item.binding, setup_attestation: { request_id: item.id, scope_digest: item.scope_digest, fingerprint: item.fingerprint } } });
  return api(`${root}/setup-requests/${encodeURIComponent(item.id)}/decide`, { method: 'POST', body: { approve: true, scope_digest: item.scope_digest, fingerprint: item.fingerprint } });
}
