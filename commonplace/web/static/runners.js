import { api, escape as e } from "./api.js";

// Owner view of each runner worker's write set: the first granted wing always, plus chosen extras.
const live = (m) => !m.revoked_at;
const writeSet = (m) => m.write_set || (m.wings || []).slice(0, 1);

function writeAccess(m, wings) {
  const primary = writeSet(m)[0], choices = [...new Set([...(m.wings || []), ...wings])];
  return `<details data-runner-write data-runner-wings="${e(JSON.stringify(m.wings || []))}"><summary>Write access</summary>${choices.map((wing) => `<label><input type="checkbox" value="${e(wing)}" ${writeSet(m).includes(wing) ? "checked" : ""} ${wing === primary ? "disabled" : ""}>${e(wing)}${wing === primary ? " (first granted wing, always writable)" : ""}</label>`).join("")}<small>Saving a changed write set resets the owner attestation. Attest the machine again before new starts.</small><button type="button" data-action="runner-write-wings" data-runner-mutation data-runner-id="${e(m.machine_id)}">Save write access</button></details>`;
}

function machineRows(machines, wings) {
  return machines.filter(live).map((m) => `<div class="setting-row" data-runner="${e(m.machine_id)}"><span>${e(m.label)}<br><small>Reads: ${e((m.wings || []).join(", "))}</small><br><small data-runner-writes>Writes: ${e(writeSet(m).join(", "))}</small></span>${writeAccess(m, wings)}</div>`).join("") || '<p class="message">No runner machines enrolled</p>';
}

export function runnersView(payload, wings = []) {
  return `<section class="settings-section runners-settings"><h2>Runner machines</h2><p>A runner worker writes to its first granted wing. Choose more wings it may write to; each one is also added to what it reads.</p><div data-runner-list>${machineRows(payload.machines || [], wings)}</div><p role="status" data-runner-status></p></section>`;
}

export async function runnerAction(button) {
  const section = button.closest(".runners-settings");
  if (section.dataset.busy) return;
  const box = button.closest("[data-runner-write]");
  const write_wings = [...box.querySelectorAll('input[type="checkbox"]:checked:not(:disabled)')].map((input) => input.value);
  const granted = JSON.parse(box.dataset.runnerWings);
  const wings = [...granted, ...write_wings.filter((wing) => !granted.includes(wing))];
  section.dataset.busy = "true";
  section.querySelectorAll("[data-runner-mutation]").forEach((b) => { b.disabled = true; });
  try {
    await api(`/api/runners/${encodeURIComponent(button.dataset.runnerId)}`, { method: "PATCH", body: { ...(wings.length !== granted.length ? { wings } : {}), write_wings } });
    const [runners, connections] = await Promise.all([api("/api/runners"), api("/api/connections")]);
    if (!section.isConnected) return;
    section.querySelector("[data-runner-list]").innerHTML = machineRows(runners.machines || [], connections.wings || []);
    section.querySelector("[data-runner-status]").textContent = "Write access saved";
  } finally {
    delete section.dataset.busy;
    section.querySelectorAll("[data-runner-mutation]").forEach((b) => { b.disabled = false; });
  }
}
