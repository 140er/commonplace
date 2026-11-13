/** Person decisions and machine-reported sign-in, within the shared Needs me shell. */
import { escape as e } from './api.js';

export const consentStyle = `
.runner-consent { box-sizing:border-box; width:100%; max-width:720px; margin:20px 0; padding:0; color:#FFF; }
.runner-consent h2 { font-size:24px; line-height:30px; font-weight:600; margin:6px 0 12px; }
.runner-consent p { margin:8px 0; line-height:1.5; overflow-wrap:anywhere; }
.runner-consent .runner-step,.runner-consent .runner-status { color:#949494; font-size:13px; }
.runner-consent .runner-name { color:#FFF; font-size:16px; font-weight:600; margin:0; }
.runner-consent .runner-instruction { color:#B4B4B4; margin:20px 0; line-height:1.5; }
.runner-consent dl { display:grid; grid-template-columns:100px minmax(0,1fr); gap:12px 16px; margin:24px 0; }
.runner-consent dt { color:#949494; font-size:13px; }
.runner-consent dd { color:#FFF; font-size:15px; margin:0; overflow-wrap:anywhere; }
.runner-consent .fingerprint { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; padding:16px; border-radius:12px; background:#1E1E1E; color:#FFF; font:16px/24px ui-monospace,SFMono-Regular,Consolas,monospace; user-select:text; }
.runner-consent .runner-check { display:flex; gap:12px; align-items:center; margin:20px 0; min-height:44px; font-size:15px; cursor:pointer; }
.runner-consent .runner-check input[type=checkbox] { box-sizing:border-box; padding:0; min-height:20px; max-height:20px; appearance:none; flex:0 0 20px; width:20px; height:20px; border:1px solid #949494; border-radius:6px; margin:0; background:#1E1E1E; display:grid; place-content:center; }
.runner-consent input[type=checkbox]:checked { background:#FFF; border-color:#FFF; }
.runner-consent input[type=checkbox]:checked::after { content:''; width:9px; height:5px; border:solid #000; border-width:0 0 2px 2px; transform:translateY(-1px) rotate(-45deg); }
.runner-consent .runner-actions { display:flex; align-items:center; flex-wrap:wrap; gap:12px; }
.runner-consent button { min-height:44px; padding:10px 20px; border:0; font:inherit; cursor:pointer; }
.runner-consent .runner-primary { border-radius:999px; background:#FFF; color:#000; font-size:15px; font-weight:600; }
.runner-consent .runner-primary:disabled { background:#1E1E1E; color:#949494; cursor:default; opacity:1; }
.runner-consent .runner-refuse { color:#F0708A; background:transparent; padding-inline:0; font-size:15px; }
.runner-consent .runner-cancel { color:#FFF; background:transparent; }
.runner-consent .runner-confirm { margin-top:20px; }
.runner-consent output.runner-error { color:#F0708A; }
.runner-consent .runner-private-note { color:#949494; font-size:13px; }
.runner-consent .runner-instruction ol { padding-left:22px; margin:0; }
.runner-consent .runner-instruction li + li { margin-top:8px; }
.runner-consent .runner-machine-command { margin-top:24px; }
.runner-consent .runner-machine-command > p { margin:0 0 8px; }
:root[data-ui="deck"] .needs-me:has(.runner-consent) > .bar > [data-needs-chat]:empty { display:none; }
.runner-consent output { display:block; margin-top:16px; color:#B4B4B4; overflow-wrap:anywhere; }
.runner-consent .runner-command { display:block; padding:16px; border-radius:12px; background:#1E1E1E; color:#FFF; font-size:15px; white-space:pre-wrap; overflow-wrap:anywhere; user-select:text; }
.runner-consent a { color:#FFF; text-decoration:underline; min-height:44px; display:inline-flex; align-items:center; }
.runner-consent button:focus-visible,.runner-consent input:focus-visible { outline:2px solid #FFF; outline-offset:4px; }
@media(max-width:600px) { .runner-consent .fingerprint { grid-template-columns:repeat(2,minmax(0,1fr)); } }
`;
const provider = name => ({ codex: 'Codex', claude: 'Claude' }[name] || 'Selected provider');
export function runnerStep(item) {
  const login = item.approval_kind === 'login', manual = login && item.manual_login;
  const key = login ? (manual || item.state === 'approved' ? item.provider : 'check') : item.approval_kind;
  const titles = { check:'Check this machine', codex:'Sign in to Codex', claude:'Sign in to Claude', grant:'Approve machine access', attest:'Confirm this machine' };
  const fallback = [...(login ? (manual ? ['claude'] : ['check','codex']) : []),'grant','attest'];
  const supplied = item.setup_steps;
  const steps = Array.isArray(supplied) && supplied.includes(key) && new Set(supplied).size === supplied.length && supplied.every(step => Object.hasOwn(titles,step)) ? supplied : fallback;
  return { key, title:titles[key], number:steps.indexOf(key)+1, total:steps.length };
}
export function runnerConsent(item) {
  const login = item.approval_kind === 'login', device = login && item.state === 'approved';
  const manual = login && item.manual_login, final = item.approval_kind === 'attest';
  const step = runnerStep(item);
  const description = manual ? '<ol><li>Run the command below on the machine.</li><li>Open the link it prints, on any device.</li><li>Type the code from that page into the machine.</li></ol><p class="runner-private-note">Commonplace never sees this code.</p>' : e(
    login ? (device ? 'Open the provider’s sign-in page on any device. Enter the short code only on that page. The machine receives the login by itself.' : 'Compare the fingerprint with the one printed by the installer, then allow this machine to start a subscription sign-in. Your login stays on the machine.') :
    final ? 'Compare the fingerprint below with the one printed by the installer. Confirm only if every group matches. Provider sign-ins can finish separately.' :
    'Compare the fingerprint with the installer. Review the projects, topics and access limit before approving. The private access file is delivered automatically.');
  const action = device ? `Sign in to ${provider(item.provider)}` : login ? 'Check this machine' : final ? 'Confirm matching machine' : 'Approve access';
  const refuse = `<button class="runner-refuse" data-runner-consent="${e(item.id)}" data-runner-consent-action="refuse-start">This is not my machine</button>`;
  let form;
  if (item.ui_result) {
    form = `<output role="status">${item.ui_result === 'refused' ? 'Refused. Setup has ended for this machine.' : `${item.approval_kind === 'grant' ? 'Approved.' : 'Confirmed.'} The machine continues setup by itself.`}</output>`;
    if (item.ui_device) form += `<div class="runner-device"><a href="https://auth.openai.com/codex/device" target="_blank" rel="noopener noreferrer">Open provider sign-in</a><p>One-time code: <strong>${e(item.ui_device)}</strong>. Enter it only on the provider’s website. This code is shown once.</p></div>`;
  } else if (item.ui_refusing) {
    form = `<div class="runner-confirm" role="group" aria-label="Refuse this machine"><p>Refuse this machine? Setup ends and its access is revoked.</p><div class="runner-actions"><button class="runner-refuse" data-runner-consent="${e(item.id)}" data-runner-consent-action="refuse">Yes, refuse this machine</button><button class="runner-cancel" data-runner-consent="${e(item.id)}" data-runner-consent-action="refuse-cancel">Cancel</button></div></div>`;
  } else {
    form = `${manual ? '<div class="runner-machine-command"><p>Run on the machine</p><code class="runner-command">sh "$HOME/.commonplace-runner/sign-in" claude</code></div>' : `<label class="runner-check"><input type="checkbox" data-runner-fingerprint> I compared every group with the machine I enrolled.</label>`}<div class="runner-actions">${manual ? '' : `<button class="runner-primary" disabled data-runner-consent="${e(item.id)}" data-runner-consent-action="${device ? 'device' : item.approval_kind}">${action}</button>`}${refuse}</div>`;
  }
  return `<article class="runner-consent" data-runner-consent-card="${e(item.id)}"><p class="runner-step">Step ${step.number} of ${step.total}</p><h2>${e(step.title)}</h2><p class="runner-name">${e(item.label)}</p>${item.ui_result ? '' : `<p class="runner-status">${manual ? 'Waiting for sign-in on the machine' : 'This machine cannot run work yet.'}</p>`}<div class="runner-instruction">${description}</div><dl><dt>Projects</dt><dd>${e((item.projects || []).join(', '))}</dd><dt>Topics</dt><dd>${e((item.topics || []).join(', '))}</dd><dt>Access limit</dt><dd>${e(item.runtime === 'approval-required' ? 'Ask before each action' : item.runtime)}</dd></dl><p>Machine fingerprint</p><div class="fingerprint">${((item.fingerprint || '').match(/.{1,8}/g) || []).map(group => `<span>${e(group)}</span>`).join(' ')}</div><div data-runner-consent-form>${form}</div>${item.ui_result ? '' : `<output class="${item.ui_error ? 'runner-error' : ''}" data-runner-consent-status role="status">${e(item.ui_error || '')}</output>`}</article>`;
}

export async function decideRunner(api, item, action) {
  const root = `/api/runners/${encodeURIComponent(item.machine_id)}`;
  if (action === 'device') return api(`${root}/setup-requests/${encodeURIComponent(item.id)}/device`, { method: 'POST', body: {} });
  if (action === 'refuse') return api(`${root}/setup-requests/${encodeURIComponent(item.id)}/refuse`, { method: 'POST', body: { scope_digest: item.scope_digest, fingerprint: item.fingerprint } });
  if (action === 'attest') return api(`${root}/attest`, { method: 'POST', body: { ...item.binding, setup_attestation: { request_id: item.id, scope_digest: item.scope_digest, fingerprint: item.fingerprint } } });
  return api(`${root}/setup-requests/${encodeURIComponent(item.id)}/decide`, { method: 'POST', body: { approve: true, scope_digest: item.scope_digest, fingerprint: item.fingerprint } });
}
