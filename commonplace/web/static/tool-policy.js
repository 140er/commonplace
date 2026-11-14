// Tool policy: the owner sets each tool action to Allowed, Ask first or Off (owners only).
const LABELS = { allowed: "Allowed", ask: "Ask first", off: "Off" };
const KINDS = { read: "Reads", write: "Changes things", unknown: "No longer served" };

/** Narrowed actions first, then by name; `filter` keeps names that contain it. */
export function sortActions(actions, filter = "") {
  const needle = filter.trim().toLowerCase();
  return actions
    .filter((a) => !needle || a.name.toLowerCase().includes(needle))
    .sort((a, b) => (a.mode === "allowed") - (b.mode === "allowed") || a.name.localeCompare(b.name));
}

export function policyRow(a, modes, e) {
  const options = modes.map((m) => `<option value="${e(m)}"${m === a.mode ? " selected" : ""}>${e(LABELS[m] || m)}</option>`).join("");
  return `<div class="setting-row" data-tool-policy-row="${e(a.name)}"><span>${e(a.name)}<br><small>${e(KINDS[a.kind] || a.kind)}</small></span><select data-tool-policy-mode="${e(a.name)}" aria-label="Policy for ${e(a.name)}">${options}</select></div>`;
}

export function summary(actions) {
  const count = (mode) => actions.filter((a) => a.mode === mode).length;
  const ask = count("ask"), off = count("off");
  return ask || off ? `${ask} ask first · ${off} off · the rest allowed` : "Every action is allowed";
}

export const saveMode = (api, name, mode) => api("/api/tool-policy", { method: "POST", body: { actions: { [name]: mode } } });

export function mountToolPolicy(host, { api, e, current = () => true, presentation = null }) {
  const active = () => current() && host.isConnected;
  let data = null, filter = "", status = "";
  async function load() {
    try {
      data = await api("/api/tool-policy");
    } catch (error) {
      if (active()) { if (error.status === 403) host.closest("section")?.remove(); else host.innerHTML = `<p class="message" role="alert">Could not load the tool policy</p>`; }
      return;
    }
    render();
  }
  function rows() {
    if (presentation) return presentation.rows(data.actions, data.modes, filter, e);
    return sortActions(data.actions, filter).map((a) => policyRow(a, data.modes, e)).join("") || '<p class="message">No action matches</p>';
  }
  function render() {
    if (!active()) return;
    const minutes = Math.round(data.approval.approval_ttl_s / 60);
    host.innerHTML = `<p>${presentation ? e(presentation.introduction(minutes)) : `What your agents may do, per action. <b>Allowed</b> is the default. <b>Ask first</b> holds the call and asks you in Needs me; your approval covers that agent and that exact call once, for ${e(minutes)} minutes. <b>Off</b> refuses the call. This never gives an agent more than its own access allows, and it does not apply to you in the app.`}</p><p role="status" data-tool-policy-status>${e(status || summary(data.actions))}</p><input type="search" data-tool-policy-filter aria-label="${e(presentation?.filterLabel || "Filter actions")}" placeholder="${e(presentation?.placeholder || "Filter actions")}" value="${e(filter)}" autocomplete="off"><div data-tool-policy-list>${rows()}</div>`;
    host.querySelector("[data-tool-policy-filter]").oninput = (event) => { filter = event.target.value; host.querySelector("[data-tool-policy-list]").innerHTML = rows(); presentation?.filterChanged?.(host); };
    host.querySelector("[data-tool-policy-list]").onchange = async (event) => {
      const select = event.target.closest?.("[data-tool-policy-mode]");
      if (!select) return;
      select.disabled = true;
      try { data = await saveMode(api, select.dataset.toolPolicyMode, select.value); status = ""; } catch (error) { status = error.message || "Request failed"; }
      render();
    };
  }
  load();
}
