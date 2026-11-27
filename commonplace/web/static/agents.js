import { wingLabel } from "./wing-label.js";
import { connectionLabel, scopeHTML } from './deck-settings-capy.js';
import { api, escape as e, elapsed } from "./api.js";
import { agentTypesControl } from "./tasktypes.js";

const clients = [["claude-code", "Claude Code"], ["codex", "Codex"], ["cursor", "Cursor"], ["generic", "Generic MCP / Grok"]];
const importConsent = "Import your past AI conversations and memory files into this workspace, word for word. This import permission cannot read stored threads or change existing memories, rules or settings. Sending exactly the same import again adds no duplicates.";
const readerConsent = "Let this app read and search the conversations and memory files you have imported, past and future.";
const readerDetail = "Covers history imported through app connections in this workspace, including imports from your other connections. History imported while signed in, live session captures and other people's history are not included. Multipart conversations, imported facts and native memory files count too, even from wings this connection cannot otherwise read. To stop access, revoke this connection and reconnect with this box unticked.";
const details = (text) => `<details><summary>Details</summary><small>${e(text)}</small></details>`;
const deck = () => globalThis.document?.documentElement?.dataset?.ui === "deck";
const options = (selected) => clients.map(([id, name]) => `<option value="${id}" ${id === selected ? "selected" : ""}>${deck() ? connectionLabel(id) : name}</option>`).join("");
const active = (c) => c.auth === "pat" && !c.revoked_at;
const parents = (rows) => '<option value="">Top-level agent</option>' + rows.filter((c) => active(c) && (!c.expires_at || Date.parse(c.expires_at) > Date.now())).map((c) => `<option value="${e(c.id)}">${e(c.display_name || c.name)}</option>`).join("");

const nameRule = "Use 1–120 characters: letters A–Z, numbers, spaces or . _ : @ -, starting with a letter or number.";

export function agentName(value) {
  const displayName = value.trim();
  const identifier = displayName.toLowerCase().replace(/ +/g, "-");
  const error = !displayName || displayName.length > 120 || !/^[A-Za-z0-9 ._:@-]+$/.test(displayName) || !/^[A-Za-z0-9]/.test(displayName) ? nameRule : "";
  return { displayName, identifier, error };
}

export function validateAgentName(form) {
  const input = form.elements.namedItem("name"), result = agentName(input.value);
  const section = form.closest(".agents-settings");
  const duplicate = [...section.querySelectorAll("[data-agent-identifier]")].some(row => row.dataset.agentIdentifier === result.identifier);
  const error = result.error || (duplicate ? `A connection with identifier “${result.identifier}” already exists. Choose another name.` : "");
  input.setCustomValidity(error);
  input.setAttribute("aria-invalid", String(Boolean(error)));
  form.querySelector("[data-agent-name-error]").textContent = error;
  form.querySelector("[data-agent-name-preview]").textContent = result.identifier ? `Identifier: ${result.identifier}` : "Identifier appears as you type.";
  return !error;
}

globalThis.document?.addEventListener("input", event => {
  const form = event.target.closest?.('[data-form="agent"]');
  if (form && event.target.name === "name") validateAgentName(form);
});

export function usedText(value) {
  const age = elapsed(value);
  return age === "now" ? "Used just now" : `Used ${age} ago`;
}

function readAccess(c, wings) {
  if (!wings.length) return "";
  return `<details data-agent-access><summary>Read access</summary>${wings.map((wing) => `<label><input type="checkbox" value="${e(wing)}" ${(c.read_wings || []).some((grant) => grant === "*" || grant === wing) ? "checked" : ""}>${e(wingLabel(wing))}</label>`).join("")}<button type="button" data-action="agent-wings" data-agent-mutation data-agent-id="${e(c.id)}">Save read access</button></details>`;
}

// The consent copy comes from the server (one source); agents and seats are never grantable.
function decideAccess(c) {
  if (c.revoked_at) return "";
  if (c.decide) return `<small data-agent-decide="on">${deck() ? scopeHTML("cards:decide") : "Answers what is waiting on you"}</small><button type="button" data-action="agent-decide" data-decide="false" data-agent-mutation data-agent-id="${e(c.id)}" data-agent-name="${e(c.display_name || c.name)}">Stop answering</button>`;
  return c.decide_grantable ? `<button type="button" data-action="agent-decide" data-decide="true" data-agent-mutation data-agent-id="${e(c.id)}" data-agent-name="${e(c.display_name || c.name)}">Let it answer what is waiting</button>` : "";
}

// Bots access for the person's own app; the consent copy comes from the server.
function botsAccess(c) {
  if (c.revoked_at) return "";
  const button = (label, data) => `<button type="button" data-action="agent-bots" ${data} data-agent-mutation data-agent-id="${e(c.id)}" data-agent-name="${e(c.display_name || c.name)}">${label}</button>`;
  if (c.bots_chat) {
    const confirm = c.bots_confirm ? button("Stop confirming", 'data-bots-confirm="false"') : button("Allow confirming", 'data-bots-confirm="true"');
    const learned = c.bots_learned ? button("Stop reading learned items", 'data-bots-learned="false"') : button("Allow reading learned items", 'data-bots-learned="true"');
    return `<small data-agent-bots="on">${deck() ? ["bots:chat", ...(c.bots_confirm ? ["bots:confirm"] : []), ...(c.bots_learned ? ["bots:learned"] : [])].map(scopeHTML).join(" ") : `Uses your bots (bots:chat${c.bots_confirm ? ", bots:confirm" : ""}${c.bots_learned ? ", bots:learned" : ""})`}</small>${confirm}${learned}${button("Stop using bots", 'data-bots-chat="false"')}`;
  }
  return c.bots_grantable ? button("Allow using my bots", 'data-bots-chat="true"') : "";
}

const oauthNote = "Connected through sign-in (OAuth). It cannot be rotated here: revoke it and the client signs in again.";

// Rotate and Revoke cut a token off, so they sit in their own collapsed group, apart from the everyday toggles.
const tokenButton = (c, action, label) => `<button type="button" data-action="${action}" data-agent-mutation data-agent-id="${e(c.id)}" data-agent-name="${e(c.display_name || c.name)}">${label}</button>`;
const tokenGroup = (...buttons) => `<details class="agent-tokens" data-agent-tokens><summary>Access tokens</summary>${buttons.join("")}</details>`;

function oauthControls(c) {
  if (c.auth !== "oauth" || c.revoked_at) return "";
  return `<small data-agent-oauth>${e(oauthNote)}</small>${tokenGroup(tokenButton(c, "agent-revoke", "Revoke"))}`;
}

function agentRows(rows, wings = []) {
  return rows.map((c) => `<div class="setting-row"><span data-agent-identifier="${e(c.name)}">${e(c.display_name || c.name)}<br>${c.user_name ? `<small>${e(c.user_name)}</small><br>` : ""}${c.display_name && c.display_name !== c.name ? `<small>Identifier: ${e(c.name)}</small><br>` : ""}<small>${e(deck() ? connectionLabel(c.client) : c.client)} · ${c.revoked_at ? "Revoked" : c.expires_at && Date.parse(c.expires_at) <= Date.now() ? "Expired" : c.last_used_at ? usedText(c.last_used_at) : "Unused"}</small></span>${deck() && Array.isArray(c.scopes) ? c.scopes.map(scopeHTML).join(" ") : ""}${decideAccess(c)}${botsAccess(c)}${oauthControls(c)}${active(c) ? `${readAccess(c, wings)}${c.import_read ? `<small>Imported history: allowed.</small>${details(readerDetail)}` : ""}${agentTypesControl(c)}${tokenGroup(tokenButton(c, "agent-rotate", "Rotate"), tokenButton(c, "agent-revoke", "Revoke"))}` : ""}</div>`).join("") || '<p class="message">No apps connected yet</p>';
}

export function agentsView(payload) {
  if (payload.member_requests) return memberConnectionsView(payload);
  const rows = payload.connections || [];
  return `<section class="settings-section agents-settings" data-decide-consent="${e(payload.decide_consent || "")}" data-bots-chat-consent="${e(payload.bots_consent?.chat || "")}" data-bots-confirm-consent="${e(payload.bots_consent?.confirm || "")}" data-bots-learned-consent="${e(payload.bots_consent?.learned || "")}"><h2>Connected apps</h2><div data-agent-reveal></div><form class="agent-form" data-form="agent"><label>Name<input name="name" aria-label="Name" required maxlength="120" autocomplete="off" aria-describedby="agent-name-preview agent-name-error"><small id="agent-name-preview" data-agent-name-preview>Identifier appears as you type.</small><small id="agent-name-error" data-agent-name-error role="alert"></small></label><label>Client<select name="client">${options("claude-code")}</select></label><label class="agent-consent"><input type="checkbox" name="import">${e(importConsent)}</label><label class="agent-consent"><input type="checkbox" name="import_read">${e(readerConsent)}</label>${details(readerDetail)}${payload.decide_consent ? `<label class="agent-consent"><input type="checkbox" name="decide">${e(payload.decide_consent)}</label>${payload.decide_detail ? details(payload.decide_detail) : ""}` : ""}<details><summary>Restrict access or set a parent</summary><label>Read wings (comma separated)<input name="read_wings" autocomplete="off"></label><label>Parent<select name="parent">${parents(rows)}</select></label></details><button class="blockbtn" data-agent-mutation>Create token</button></form><div data-agent-list>${agentRows(rows, payload.wings || [])}</div></section>`;
}

export function clientConfig(client, url, token) {
  const headers = { Authorization: `Bearer ${token}` };
  if (client === "codex") return `[mcp_servers.commonplace]\nurl = ${JSON.stringify(url)}\nhttp_headers = { Authorization = ${JSON.stringify(headers.Authorization)} }\n`;
  if (client === "generic") return JSON.stringify({ url, headers }, null, 2);
  return JSON.stringify({ mcpServers: { commonplace: { ...(client === "claude-code" ? { type: "http" } : {}), url, headers } } }, null, 2);
}

const destinations = { "claude-code": ".mcp.json", codex: "config.toml", cursor: ".cursor/mcp.json", generic: "MCP URL and headers" };

function reveal(section, result) {
  const region = section.querySelector("[data-agent-reveal]");
  const client = clients.some(([id]) => id === result.connection.client) ? result.connection.client : "generic";
  region.dataset.agentId = result.connection.id;
  region.innerHTML = `<div class="agent-secret"><h3>${e(result.connection.display_name || result.connection.name)}</h3><p>Identifier: ${e(result.connection.name)}</p><p>Copy now. This token will not be shown again.</p><label>Token<input data-agent-token value="${e(result.token)}" readonly autocomplete="off" spellcheck="false"></label><button type="button" data-action="agent-copy" data-agent-copy="token">Copy token</button><label>MCP URL<input data-agent-url value="${e(result.mcp_url)}" readonly></label><button type="button" data-action="agent-copy" data-agent-copy="url">Copy URL</button><label>Authorization<input data-agent-header value="Bearer ${e(result.token)}" readonly autocomplete="off" spellcheck="false"></label><button type="button" data-action="agent-copy" data-agent-copy="header">Copy header</button><label>Client<select data-agent-client>${options(client)}</select></label><strong data-agent-destination>${destinations[client]}</strong><pre data-agent-config tabindex="0">${e(clientConfig(client, result.mcp_url, result.token))}</pre><button type="button" data-action="agent-copy" data-agent-copy="config">Copy config</button><button type="button" data-action="agent-dismiss">Done</button><span role="status" data-agent-copy-status></span></div>`;
  region.querySelector("[data-agent-token]").focus();
}

export function changeAgentClient(select) {
  const section = select.closest(".agents-settings");
  section.querySelector("[data-agent-destination]").textContent = destinations[select.value];
  section.querySelector("[data-agent-config]").textContent = clientConfig(select.value, section.querySelector("[data-agent-url]").value, section.querySelector("[data-agent-token]").value);
}

async function refresh(section) {
  if (section.dataset.memberRequests) {
    const payload = await api('/api/connection-requests');
    if (section.isConnected) section.querySelector('[data-agent-list]').innerHTML = memberRequestRows(payload.requests || []);
    return;
  }
  const payload = await api("/api/connections");
  if (!section.isConnected) return;
  section.querySelector("[data-agent-list]").innerHTML = agentRows(payload.connections, payload.wings || []);
  const select = section.querySelector('[name="parent"]'), selected = select.value;
  select.innerHTML = parents(payload.connections);
  select.value = selected;
}

async function mutation(section, run) {
  if (section.dataset.busy) return;
  section.dataset.busy = "true";
  section.querySelectorAll("[data-agent-mutation]").forEach((button) => { button.disabled = true; });
  try { await run(); }
  finally {
    delete section.dataset.busy;
    section.querySelectorAll("[data-agent-mutation]").forEach((button) => { button.disabled = false; });
  }
}

export async function createAgent(form) {
  const section = form.closest('.agents-settings');
  if (section.dataset.memberRequests) {
    const data = Object.fromEntries(new FormData(form));
    await mutation(section, async () => {
      await api('/api/connection-requests', { method: 'POST', body: data });
      if (!section.isConnected) return;
      form.reset();
      await refresh(section);
    });
    return;
  }
  if (!validateAgentName(form)) { form.reportValidity(); return; }
  const data = Object.fromEntries(new FormData(form));
  const name = agentName(data.name);
  data.name = name.identifier;
  data.display_name = name.displayName;
  data.import = form.elements.namedItem("import").checked;
  data.import_read = form.elements.namedItem("import_read").checked;
  data.decide = Boolean(form.elements.namedItem("decide")?.checked);
  if (!data.parent) delete data.parent;
  if (data.read_wings.trim()) data.read_wings = data.read_wings.split(",").map((wing) => wing.trim());
  else delete data.read_wings;
  await mutation(section, async () => {
    let result;
    try { result = await api("/api/connections", { method: "POST", body: data }); }
    catch (error) {
      if (section.isConnected) form.querySelector("[data-agent-name-error]").textContent = error.message || "Connection could not be created. Try again.";
      throw error;
    }
    if (!section.isConnected) return;
    form.reset();
    form.querySelector("[data-agent-name-preview]").textContent = "Identifier appears as you type.";
    reveal(section, result);
    await refresh(section);
  });
}

export async function agentAction(button) {
  const section = button.closest(".agents-settings"), action = button.dataset.action;
  if (action === "agent-dismiss") { section.querySelector("[data-agent-reveal]").replaceChildren(); return; }
  if (action === "agent-copy") {
    const kind = button.dataset.agentCopy;
    const node = section.querySelector(`[data-agent-${kind}]`), text = kind === "config" ? node.textContent : node.value;
    try { await navigator.clipboard.writeText(text); }
    catch { throw new Error("Copy failed. Select the value and copy it manually."); }
    section.querySelector("[data-agent-copy-status]").textContent = "Copied";
    return;
  }
  if (action === 'agent-request-refresh') { await mutation(section, () => refresh(section)); return; }
  if (['agent-request-cancel', 'agent-request-collect', 'agent-request-revoke'].includes(action)) {
    await mutation(section, async () => {
      const operation = action.slice('agent-request-'.length);
      const result = await api(`/api/connection-requests/${encodeURIComponent(button.dataset.requestId)}/${operation}`, { method: 'POST', body: {} });
      if (!section.isConnected) return;
      if (operation === 'collect') reveal(section, result);
      if (operation === 'revoke' && section.querySelector('[data-agent-reveal]').dataset.agentId === button.dataset.agentId) section.querySelector('[data-agent-reveal]').replaceChildren();
      await refresh(section);
    });
    return;
  }
  if (action === "agent-wings") {
    const read_wings = [...button.closest("[data-agent-access]").querySelectorAll('input[type="checkbox"]:checked')].map((input) => input.value);
    if (!read_wings.length) throw new Error("Choose at least one read wing.");
    await mutation(section, async () => {
      await api(`/api/connections/${encodeURIComponent(button.dataset.agentId)}`, { method: "PATCH", body: { read_wings } });
      await refresh(section);
    });
    return;
  }
  if (action === "agent-decide") {
    const decide = button.dataset.decide === "true";
    if (!confirm(decide ? `Let ${button.dataset.agentName} answer what is waiting on you?\n\n${section.dataset.decideConsent}` : `Stop ${button.dataset.agentName} answering what is waiting on you?`)) return;
    await mutation(section, async () => {
      await api(`/api/connections/${encodeURIComponent(button.dataset.agentId)}`, { method: "PATCH", body: { decide } });
      await refresh(section);
    });
    return;
  }
  if (action === "agent-bots") {
    const body = {};
    if (button.dataset.botsChat) body.bots_chat = button.dataset.botsChat === "true";
    if (button.dataset.botsConfirm) body.bots_confirm = button.dataset.botsConfirm === "true";
    if (button.dataset.botsLearned) body.bots_learned = button.dataset.botsLearned === "true";
    const on = body.bots_chat === true ? section.dataset.botsChatConsent : body.bots_confirm === true ? section.dataset.botsConfirmConsent : body.bots_learned === true ? section.dataset.botsLearnedConsent : "";
    if (!confirm(on ? `Let ${button.dataset.agentName} use your bots?\n\n${on}` : `Stop ${button.dataset.agentName} using your bots?`)) return;
    await mutation(section, async () => {
      await api(`/api/connections/${encodeURIComponent(button.dataset.agentId)}`, { method: "PATCH", body });
      await refresh(section);
    });
    return;
  }
  const rotate = action === "agent-rotate";
  if (!confirm(`${rotate ? "Rotate" : "Revoke"} ${button.dataset.agentName}? Its current token will stop working.`)) return;
  await mutation(section, async () => {
    const result = await api(`/api/connections/${encodeURIComponent(button.dataset.agentId)}${rotate ? "/rotate" : ""}`, { method: rotate ? "POST" : "DELETE", ...(rotate ? { body: {} } : {}) });
    if (!section.isConnected) return;
    if (rotate) reveal(section, result);
    else if (section.querySelector("[data-agent-reveal]").dataset.agentId === button.dataset.agentId) section.querySelector("[data-agent-reveal]").replaceChildren();
    await refresh(section);
  });
}

export function clearAgentSecret(root) {
  root.querySelector("[data-agent-reveal]")?.replaceChildren();
}

export function memberRequestRows(requests) {
  const button = (item, action, text) => `<button type="button" data-action="agent-request-${action}" data-request-id="${e(item.id)}" data-agent-id="${e(item.connection_id || '')}" data-agent-mutation>${text}</button>`;
  return requests.map(item => {
    const connection = item.connection;
    const ended = connection?.revoked_at || (connection?.expires_at && Date.parse(connection.expires_at) <= Date.now());
    const status = item.status === 'pending' ? 'Requested, waiting for the owner' : item.status === 'declined' ? 'Declined' : item.status === 'cancelled' ? 'Cancelled' : connection?.revoked_at ? 'Revoked' : ended ? 'Expired' : item.collected_at ? 'Connected' : 'Approved. Your connection is ready.';
    const actions = item.status === 'pending' ? button(item, 'cancel', 'Cancel request') : item.status === 'approved' && connection && !ended ? `${!item.collected_at ? button(item, 'collect', 'Collect token') : ''}${button(item, 'revoke', 'Revoke connection')}` : '';
    return `<div class="setting-row"><div><strong>${e(item.name)}</strong><p>${e(status)}</p>${item.note ? `<p class="quote">${e(item.note)}</p>` : ''}${item.reason ? `<p>Reason: ${e(item.reason)}</p>` : ''}${connection ? `<small>${e(item.requester_name)} · Expires ${e(connection.expires_at)}</small>` : ''}</div>${actions}</div>`;
  }).join('') || '<p class="message">No connection requests yet.</p>';
}

export function memberConnectionsView(payload) {
  return `<section class="settings-section agents-settings" data-member-requests="true"><h2>Connections</h2><p>Request a connection for your Coworker. The workspace owner approves it. It lasts 90 days; you or the owner can revoke it. After approval, collect the token here once.</p><div data-agent-reveal></div><form class="agent-form" data-form="agent" data-connection-request-form><label>Name or purpose<input name="name" required maxlength="120" autocomplete="off"></label><label>Note (optional)<textarea name="note" maxlength="2000" rows="3"></textarea></label><button class="blockbtn" data-agent-mutation>Request connection</button></form><button type="button" data-action="agent-request-refresh" data-agent-mutation>Refresh requests</button><div data-agent-list>${memberRequestRows(payload.requests || [])}</div></section>`;
}
