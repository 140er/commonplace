import { api, escape as e } from "./api.js";

// Owner view of each runner machine: enroll, attest, read wings and the write set of its worker.
// Credentials live only in the one-time reveal panel's inputs: never in storage, the URL, a log
// or any other render, and dismissing the panel removes them.
const live = (m) => !m.revoked_at;
const writeSet = (m) => m.write_set || (m.wings || []).slice(0, 1);
const list = (value) => String(value || "").split(",").map((item) => item.trim()).filter(Boolean);
const MODES = ["approval-required", "auto-accept-edits", "auto", "full-access"];
const choicesOf = (m, wings) => [...new Set([...(m.wings || []), ...wings])];

function addRunner(wings) {
  if (!wings.length) return '<p class="message">Save some memory first: a runner is granted existing wings.</p>';
  return `<details data-runner-add><summary>Add runner</summary><form class="agent-form" data-form="runner-enroll"><label>Label<input name="label" required maxlength="40" pattern="[a-z0-9][a-z0-9._-]{0,39}" autocomplete="off"></label><label>Projects (comma separated)<input name="projects" required autocomplete="off"></label><label>Topics (comma separated)<input name="topics" required autocomplete="off"></label><label>Writes to (first wing)<select name="primary">${wings.map((wing) => `<option value="${e(wing)}">${e(wing)}</option>`).join("")}</select></label><fieldset data-runner-enroll><legend>Also reads</legend>${wings.map((wing) => `<label><input type="checkbox" value="${e(wing)}">${e(wing)}</label>`).join("")}<small>The worker writes only to its first wing and reads the others. Extra write access comes later, per machine.</small></fieldset><label>Max runtime mode<select name="max_runtime_mode">${MODES.map((mode) => `<option value="${mode}">${mode}</option>`).join("")}</select></label><label>Max in flight<input name="max_in_flight" type="number" min="1" max="4" value="1"></label><button class="blockbtn" data-runner-mutation>Enroll runner</button></form></details>`;
}

function attestForm(m) {
  return `<details data-runner-attest><summary>Attest</summary><form class="agent-form" data-form="runner-attest" data-runner-id="${e(m.machine_id)}"><label>Topics (comma separated)<input name="topics" value="${e((m.topics || []).join(", "))}" required autocomplete="off"></label><label>Provider instance id<input name="provider_instance_id" value="${e(m.binding_provider_instance_id || "")}" required autocomplete="off"></label><label>Worker connection id<input name="worker_connection_id" value="${e(m.worker_connection_id)}" readonly></label><small>Records your statement about this machine. Commonplace does not check it.</small><button class="blockbtn" data-runner-mutation>Attest machine</button></form></details>`;
}

function readWings(m, wings) {
  const granted = m.wings || [];
  return `<details data-runner-read data-runner-wings="${e(JSON.stringify(granted))}"><summary>Read wings</summary>${choicesOf(m, wings).map((wing) => `<label><input type="checkbox" value="${e(wing)}" ${granted.includes(wing) ? "checked" : ""}>${e(wing)}</label>`).join("")}<small>Saving changed read wings resets the owner attestation: the binding returns to unbound, its provider and worker connection are cleared, and queued and leased jobs are cancelled on the next sweep. Extra write wings that are no longer granted are dropped. Attest the machine again before new starts. The worker writes to the first granted wing; unchecking it makes the next one first.</small><button type="button" data-action="runner-wings" data-runner-mutation data-runner-id="${e(m.machine_id)}">Save read wings</button></details>`;
}

function writeAccess(m, wings) {
  const primary = writeSet(m)[0];
  return `<details data-runner-write data-runner-wings="${e(JSON.stringify(m.wings || []))}"><summary>Write access</summary>${choicesOf(m, wings).map((wing) => `<label><input type="checkbox" value="${e(wing)}" ${writeSet(m).includes(wing) ? "checked" : ""} ${wing === primary ? "disabled" : ""}>${e(wing)}${wing === primary ? " (first granted wing, always writable)" : ""}</label>`).join("")}<small>Saving a changed write set resets the owner attestation. Attest the machine again before new starts.</small><button type="button" data-action="runner-write-wings" data-runner-mutation data-runner-id="${e(m.machine_id)}">Save write access</button></details>`;
}

const binding = (m) => m.binding_state === "owner_attested" ? "owner_attested (attested, not verified)" : "unbound";

function machineRows(machines, wings) {
  return machines.filter(live).map((m) => `<div class="setting-row" data-runner="${e(m.machine_id)}"><span>${e(m.label)}<br><small>Reads: ${e((m.wings || []).join(", "))}</small><br><small data-runner-writes>Writes: ${e(writeSet(m).join(", "))}</small><br><small data-runner-binding>Binding: ${e(binding(m))}</small></span>${attestForm(m)}${readWings(m, wings)}${writeAccess(m, wings)}</div>`).join("") || '<p class="message">No runner machines enrolled</p>';
}

export function runnersView(payload, wings = []) {
  return `<section class="settings-section runners-settings"><h2>Runner machines</h2><p>A runner worker writes to its first granted wing. Choose more wings it may write to; each one is also added to what it reads.</p><div data-runner-reveal></div>${addRunner(wings)}<div data-runner-list>${machineRows(payload.machines || [], wings)}</div><p role="status" data-runner-status></p></section>`;
}

async function reload(section) {
  const [runners, connections] = await Promise.all([api("/api/runners"), api("/api/connections")]);
  if (!section.isConnected) return false;
  section.querySelector("[data-runner-list]").innerHTML = machineRows(runners.machines || [], connections.wings || []);
  return true;
}

async function mutation(section, run) {
  if (section.dataset.busy) return;
  section.dataset.busy = "true";
  section.querySelectorAll("[data-runner-mutation]").forEach((b) => { b.disabled = true; });
  try { await run(); }
  finally {
    delete section.dataset.busy;
    section.querySelectorAll("[data-runner-mutation]").forEach((b) => { b.disabled = false; });
  }
}

const status = (section, text) => { section.querySelector("[data-runner-status]").textContent = text; };

function reveal(section, result) {
  const token = (kind) => `<label>${kind[0].toUpperCase()}${kind.slice(1)} token<input data-runner-token="${kind}" value="${e(result[`${kind}_token`])}" readonly autocomplete="off" spellcheck="false"></label><button type="button" data-action="runner-copy" data-runner-copy="${kind}">Copy ${kind} token</button>`;
  const region = section.querySelector("[data-runner-reveal]");
  region.innerHTML = `<div class="agent-secret" role="alert"><h3>Runner enrolled</h3><p>Copy both tokens now. They will not be shown again.</p>${token("machine")}${token("worker")}<p role="status" data-runner-copy-status></p><button type="button" data-action="runner-dismiss">Dismiss</button></div>`;
  region.querySelector("[data-runner-token]").focus();
}

export async function enrollRunner(form) {
  const section = form.closest(".runners-settings"), data = new FormData(form);
  const primary = data.get("primary");
  const wings = [primary, ...[...form.querySelectorAll("[data-runner-enroll] input:checked")].map((input) => input.value).filter((wing) => wing !== primary)];
  await mutation(section, async () => {
    const result = await api("/api/runners", { method: "POST", body: { label: data.get("label"), projects: list(data.get("projects")), topics: list(data.get("topics")), wings, max_runtime_mode: data.get("max_runtime_mode"), max_in_flight: Number(data.get("max_in_flight")) } });
    if (!section.isConnected) return;
    form.reset(); reveal(section, result);
    await reload(section);
    status(section, "Runner enrolled. Attest it before it takes starts.");
  });
}

export async function attestRunner(form) {
  const section = form.closest(".runners-settings"), data = new FormData(form);
  await mutation(section, async () => {
    const result = await api(`/api/runners/${encodeURIComponent(form.dataset.runnerId)}/attest`, { method: "POST", body: { topics: list(data.get("topics")), provider_instance_id: data.get("provider_instance_id"), worker_connection_id: data.get("worker_connection_id") } });
    if (await reload(section)) status(section, `${result.binding_state}: ${result.note}`);
  });
}

export async function runnerAction(button) {
  const section = button.closest(".runners-settings"), action = button.dataset.action;
  if (action === "runner-dismiss") { section.querySelector("[data-runner-reveal]").replaceChildren(); return; }
  if (action === "runner-copy") {
    try { await navigator.clipboard.writeText(section.querySelector(`[data-runner-token="${button.dataset.runnerCopy}"]`).value); }
    catch { throw new Error("Copy failed. Select the value and copy it manually."); }
    section.querySelector("[data-runner-copy-status]").textContent = "Copied";
    return;
  }
  const read = action === "runner-wings", box = button.closest(read ? "[data-runner-read]" : "[data-runner-write]");
  const granted = JSON.parse(box.dataset.runnerWings), checked = [...box.querySelectorAll('input[type="checkbox"]:checked:not(:disabled)')].map((input) => input.value);
  let body, done;
  if (read) {
    // Granted order is kept (the first wing is the write wing); new wings go last.
    const wings = [...granted.filter((wing) => checked.includes(wing)), ...checked.filter((wing) => !granted.includes(wing))];
    if (!wings.length) throw new Error("Choose at least one wing.");
    if (wings.length === granted.length && wings.every((wing, i) => wing === granted[i])) { status(section, "Read wings unchanged"); return; }
    body = { wings }; done = "Read wings saved. Attest the machine again before new starts.";
  } else {
    const wings = [...granted, ...checked.filter((wing) => !granted.includes(wing))];
    body = { ...(wings.length !== granted.length ? { wings } : {}), write_wings: checked }; done = "Write access saved";
  }
  await mutation(section, async () => {
    await api(`/api/runners/${encodeURIComponent(button.dataset.runnerId)}`, { method: "PATCH", body });
    if (await reload(section)) status(section, done);
  });
}

// Leaving the route or the page drops the tokens; the app shell needs no hook for this.
const clearSecret = () => document.querySelector("[data-runner-reveal]")?.replaceChildren();
window.addEventListener("hashchange", clearSecret);
window.addEventListener("pagehide", clearSecret);
