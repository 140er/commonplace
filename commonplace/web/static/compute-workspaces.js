import { escape as e } from "./api.js";

// Cloud workspaces on the Compute screen (docs/system/compute-workspaces.md). One shared module serves the
// Classic screen and the Deck Management tab: createCompute (compute.js) mounts the section and forwards
// clicks, inputs and submits. Nothing here starts a container; a record says "Not started yet" until the
// lifecycle card lands. The server validates everything again; client checks only explain early.

const BASE = "/api/compute/workspaces";
const enc = encodeURIComponent;
const FALLBACK = {
  images: [],
  limits: { cpu_cores: { min: 0.5, max: 32, step: 0.5, default: 2 }, memory_mib: { min: 512, max: 131072, step: 128, default: 4096 }, storage_gib: { min: 1, max: 2048, step: 1, default: 20 } },
  networks: ["outbound", "none"], default_network: "outbound", name_limit: 60,
};
const NETWORKS = { outbound: "Internet access out, nothing else", none: "No network at all" };
const FAILURES = {
  out_of_memory: memory => `It ran out of memory at ${memory}. Raise the memory limit and restart.`,
  storage_full: () => "Its storage is full. Raise the storage limit or free space, then restart.",
  image_pull_failed: () => "The machine could not download the image. Check the reference and the registry.",
  image_not_found: () => "The registry has no such image. Check the name and the tag.",
  registry_login_failed: () => "The registry refused the login. Bind a working registry login.",
  machine_too_small: () => "The machine is smaller than these limits. Lower them or choose a bigger machine.",
  limit_not_enforceable: () => "The machine cannot enforce these limits, so it was not started. Nothing ran unbounded.",
  machine_unreachable: () => "The machine could not be reached.",
  start_failed: () => "It did not start.",
};

export const gib = mib => mib % 1024 === 0 ? `${mib / 1024} GiB` : mib % 128 === 0 && mib > 1024 ? `${Number((mib / 1024).toFixed(3))} GiB` : `${mib} MiB`;
export const resourcePreview = (cpu, memoryMib, storage, machine) =>
  `${cpu} CPU, ${gib(memoryMib)} memory, ${storage} GiB storage${machine ? ` on ${machine}` : ". No machine chosen yet"}`;

export const REASONS = {
  name: { length: limit => `Give it a name of 1 to ${limit} characters.`, characters: () => "Use plain text, without line breaks or spaces at either end.", name_taken: () => "Another cloud workspace already uses this name." },
  image: {
    empty: () => "Choose an image, or enter your own.",
    too_long: () => "An image reference can be at most 255 characters.",
    characters: () => "Use only lowercase letters, digits and . _ - : / @ (no spaces or shell characters).",
    scheme: () => "Leave out https:// . An image reference is just the name, like ghcr.io/team/app:1.0.",
    registry: () => "The registry address before the first / is not valid.",
    name: () => "The image name must be lowercase letters and digits separated by . _ or -.",
    tag: () => "The tag after the colon is not valid.",
    digest: () => "A digest looks like @sha256: followed by 64 lowercase hex characters.",
    not_curated: () => "Pick one of the listed images, or choose My own image.",
  },
  cpu_cores: { range: l => `CPU must be between ${l.min} and ${l.max} cores.`, step: l => `CPU goes up in steps of ${l.step}.`, not_a_number: () => "Enter a number of CPU cores." },
  memory_mib: { range: l => `Memory must be between ${l.min / 1024} and ${l.max / 1024} GiB.`, step: l => `Memory goes up in steps of ${l.step / 1024} GiB.`, not_a_number: () => "Enter an amount of memory in GiB." },
  storage_gib: { range: l => `Storage must be between ${l.min} and ${l.max} GiB.`, step: () => "Storage is a whole number of GiB.", not_a_number: () => "Enter a whole number of GiB." },
  network: { choice: () => "Choose a network setting." },
  node_id: { unknown_machine: () => "That machine is no longer available. Reload and choose another.", machine_locked: () => "A workspace that has started cannot move to another machine yet.", choice: () => "Choose a machine from the list." },
  credential_id: { invalid: () => "Enter the reference name of a stored credential (letters, digits, - and _)." },
  credential_version: { invalid: () => "Enter the credential version, a whole number from 1." },
};
export const reasonText = (field, reason, options = FALLBACK) => {
  const spec = REASONS[field]?.[reason];
  if (!spec) return "";
  return spec(field === "name" ? options.name_limit : options.limits?.[field]);
};

const LABEL = "[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?";
const HOST = new RegExp(`^${LABEL}(?:\\.${LABEL})*(?::([0-9]{1,5}))?$`);
const COMPONENT = /^[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*$/;
const TAG = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/;
const DIGEST = /^sha256:[a-f0-9]{64}$/;
const SAFE = /^[A-Za-z0-9._:/@-]+$/;

/** Same grammar as the server: returns { reason } when refused, or the parts of a valid reference. */
export function parseImage(value) {
  if (typeof value !== "string" || !value) return { reason: "empty" };
  if (value.length > 255) return { reason: "too_long" };
  if (value.includes("://")) return { reason: "scheme" };
  if (!SAFE.test(value) || /^[-/.]/.test(value)) return { reason: "characters" };
  const at = value.indexOf("@");
  let name = at < 0 ? value : value.slice(0, at);
  const digest = at < 0 ? null : value.slice(at + 1);
  if (digest !== null && !DIGEST.test(digest)) return { reason: "digest" };
  if (name.includes("@")) return { reason: "characters" };
  let tag = null;
  const slash = name.lastIndexOf("/"), colon = name.lastIndexOf(":");
  if (colon > slash) { tag = name.slice(colon + 1); name = name.slice(0, colon); if (!TAG.test(tag)) return { reason: "tag" }; }
  let parts = name.split("/"), registry = "docker.io";
  if (parts.length > 1 && (/[.:]/.test(parts[0]) || parts[0] === "localhost")) {
    const match = HOST.exec(parts[0]);
    if (!match || (match[1] !== undefined && !(Number(match[1]) >= 1 && Number(match[1]) <= 65535))) return { reason: "registry" };
    registry = parts[0]; parts = parts.slice(1);
  }
  if (!parts.length || parts.some(part => !COMPONENT.test(part))) return { reason: "name" };
  return { registry, tag, digest, pinned: digest !== null };
}

export const blankValues = (options = FALLBACK, row = null) => row ? {
  ws_name: row.name, ws_image_source: row.image_source, ws_curated: row.image_source === "curated" ? row.image : "", ws_image: row.image_source === "custom" ? row.image : "",
  ws_private_registry: row.private_registry, ws_cpu_cores: String(row.cpu_cores), ws_memory_gib: String(row.memory_mib / 1024), ws_storage_gib: String(row.storage_gib),
  ws_network: row.network, ws_node_id: row.node_id || "",
} : {
  ws_name: "", ws_image_source: options.images?.length ? "curated" : "custom", ws_curated: options.images?.[0]?.reference || "", ws_image: "", ws_private_registry: false,
  ws_cpu_cores: String(options.limits.cpu_cores.default), ws_memory_gib: String(options.limits.memory_mib.default / 1024), ws_storage_gib: String(options.limits.storage_gib.default),
  ws_network: options.default_network, ws_node_id: "",
};

const number = text => typeof text === "string" && text.trim() !== "" && Number.isFinite(Number(text)) ? Number(text) : NaN;
const imageOf = values => values.ws_image_source === "curated" ? values.ws_curated : String(values.ws_image ?? "").trim();

/** Check one field (or all) of the form values; returns { field: message }. Empty object means it can be sent. */
export function validate(values, options = FALLBACK, only = null) {
  const errors = {}, limits = options.limits, want = field => !only || only === field;
  if (want("name")) {
    const name = String(values.ws_name ?? "");
    if (!name.trim() || name.length > options.name_limit) errors.name = reasonText("name", "length", options);
    else if (name !== name.trim() || /[\u0000-\u001f\u007f]/.test(name)) errors.name = reasonText("name", "characters", options);
  }
  if (want("image")) {
    if (values.ws_image_source === "curated" && !options.images.some(item => item.reference === values.ws_curated)) errors.image = reasonText("image", "not_curated");
    else { const parsed = parseImage(imageOf(values)); if (parsed.reason) errors.image = reasonText("image", parsed.reason); }
  }
  if (want("cpu_cores")) {
    const cpu = number(values.ws_cpu_cores), spec = limits.cpu_cores;
    if (Number.isNaN(cpu)) errors.cpu_cores = reasonText("cpu_cores", "not_a_number", options);
    else if (cpu < spec.min || cpu > spec.max) errors.cpu_cores = reasonText("cpu_cores", "range", options);
    else if ((cpu / spec.step) % 1) errors.cpu_cores = reasonText("cpu_cores", "step", options);
  }
  if (want("memory_mib")) {
    const memory = number(values.ws_memory_gib), spec = limits.memory_mib, mib = memory * 1024;
    if (Number.isNaN(memory)) errors.memory_mib = reasonText("memory_mib", "not_a_number", options);
    else if (mib < spec.min || mib > spec.max) errors.memory_mib = reasonText("memory_mib", "range", options);
    else if (mib % spec.step) errors.memory_mib = reasonText("memory_mib", "step", options);
  }
  if (want("storage_gib")) {
    const storage = number(values.ws_storage_gib), spec = limits.storage_gib;
    if (Number.isNaN(storage) || storage % 1) errors.storage_gib = reasonText("storage_gib", "not_a_number", options);
    else if (storage < spec.min || storage > spec.max) errors.storage_gib = reasonText("storage_gib", "range", options);
  }
  if (want("network") && !options.networks.includes(values.ws_network)) errors.network = reasonText("network", "choice");
  return errors;
}

/** The JSON body for create, or for an edit (every field plus expected_revision). */
export function workspacePayload(values, { revision } = {}) {
  const body = {
    name: values.ws_name, image_source: values.ws_image_source, image: imageOf(values),
    cpu_cores: number(values.ws_cpu_cores), memory_mib: Math.round(number(values.ws_memory_gib) * 1024), storage_gib: number(values.ws_storage_gib),
    network: values.ws_network, node_id: values.ws_node_id || null, private_registry: values.ws_image_source === "custom" && values.ws_private_registry === true,
  };
  return revision === undefined ? body : { ...body, expected_revision: revision };
}

export function statusView(row) {
  const memory = gib(row.memory_mib);
  switch (row.status) {
    case "no_machine": return { tone: "wait", label: "No machine yet", detail: "Choose a machine to run it on. Nothing is started." };
    case "needs_credential": return { tone: "wait", label: "No credential bound yet", detail: "This image is in a private registry. A registry login has to be bound before it can start." };
    case "pending": return { tone: "quiet", label: "Not started yet", detail: `The settings are saved on ${row.machine_name || "the machine"}. Starting cloud workspaces is not available yet, so nothing runs.` };
    case "ready": return { tone: "ok", label: "Ready", detail: "The container matches the saved settings." };
    case "failed": { const reason = FAILURES[row.status_reason]; return { tone: "bad", label: "Failed", detail: reason ? reason(memory) : "Failed: reason not recognised. Check the machine." }; }
    default: return { tone: "wait", label: "Status unknown", detail: "Reload to see the current status." };
  }
}
const restartNote = row => row.restart_needed ? "Restart needed to apply the new settings." : "";
export const imageNote = row => row.image_pinned ? `Pinned to digest ${row.image_digest.slice(0, 19)}…` : row.image_source === "curated" ? "Curated image. The exact version is recorded when the image is first downloaded." : "Not pinned: the tag can change. Add @sha256:… to pin it.";
const previewOf = row => resourcePreview(row.cpu_cores, row.memory_mib, row.storage_gib, row.machine_name);

export function rowHTML(row, { manage, busy }) {
  const status = statusView(row), note = restartNote(row);
  const dis = busy ? " disabled" : "";
  return `<li class="compute-row compute-ws-row" data-ws-id="${e(row.id)}" data-status="${e(row.status)}"><header><strong>${e(row.name)}</strong><span class="compute-state compute-ws-status" data-tone="${status.tone}">${e(status.label)}</span></header>`
    + `<p>${e(previewOf(row))}</p><p class="compute-quiet">${e(status.detail)}</p>${note ? `<p class="compute-ws-restart">${e(note)}</p>` : ""}`
    + `<p class="compute-quiet">Image ${e(row.image)} · ${e(imageNote(row))}</p>`
    + `<div class="compute-actions"><button type="button" class="btn" data-ws-action="detail" data-id="${e(row.id)}"${dis}>Details</button>${manage ? `<button type="button" class="btn" data-ws-action="edit" data-id="${e(row.id)}"${dis}>Change settings</button>${row.status === "needs_credential" ? `<button type="button" class="btn" data-ws-action="bind" data-id="${e(row.id)}"${dis}>Bind a registry login</button>` : ""}` : ""}</div></li>`;
}

export function detailHTML(row, { manage, busy }) {
  const status = statusView(row), machine = row.machine_name || "No machine chosen yet";
  const cred = !row.private_registry ? "Not needed (public image)" : row.credential_bound ? (manage ? `Bound (${e(row.registry_credential_id)}, version ${e(row.registry_credential_version)})` : "Bound") : "No credential bound yet";
  const rows = [
    ["Status", `${e(status.label)}. ${e(status.detail)}`], ["Resources", e(previewOf(row))], ["Image", `${e(row.image)}<br>${e(imageNote(row))}`],
    ["Registry", `${e(row.image_registry)}${row.private_registry ? " (private)" : ""}`], ["Registry login", cred], ["Network", e(NETWORKS[row.network] || row.network)], ["Machine", e(machine)],
    ["Settings version", `${e(row.revision)}${row.started ? `, last started with ${e(row.applied_revision)}` : ", not started yet"}`],
  ];
  const note = restartNote(row);
  return `<section class="compute-editor compute-ws-detail" aria-labelledby="compute-ws-heading"><header><h2 id="compute-ws-heading" tabindex="-1">${e(row.name)}</h2><button type="button" class="btn" data-ws-action="close">Close</button></header>`
    + (note ? `<p class="compute-ws-restart" role="status">${e(note)}</p>` : "")
    + `<dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`
    + (manage ? `<div class="compute-actions"><button type="button" class="btn primary" data-ws-action="edit" data-id="${e(row.id)}"${busy ? " disabled" : ""}>Change settings</button></div>` : `<p class="compute-quiet">Only the workspace owner can change cloud workspaces. You can look at the settings here.</p>`)
    + "</section>";
}

const fieldError = (name, errors) => `<small class="compute-ws-error" id="ws-error-${name}" data-ws-error="${name}" role="alert">${e(errors[name] || "")}</small>`;
const describe = (name, hint, errors) => ` aria-describedby="${hint ? `ws-hint-${name} ` : ""}ws-error-${name}"${errors[name] ? ' aria-invalid="true"' : ""}`;
const hint = (name, text) => `<small class="compute-quiet" id="ws-hint-${name}">${text}</small>`;

export function formHTML(ctx) {
  const { values, errors, options, machines, editing, row, busy, formError } = ctx;
  const heading = editing ? `Change ${e(row.name)}` : "New cloud workspace";
  const L = options.limits;
  const curated = options.images.map(item => `<option value="${e(item.reference)}"${values.ws_curated === item.reference && values.ws_image_source === "curated" ? " selected" : ""}>${e(item.label)}</option>`).join("");
  const choice = `${curated}<option value="custom"${values.ws_image_source === "custom" ? " selected" : ""}>My own image…</option>`;
  const selected = options.images.find(item => item.reference === values.ws_curated);
  const machineOptions = `<option value=""${values.ws_node_id ? "" : " selected"}>Choose later</option>` + machines.map(m => `<option value="${e(m.id)}"${values.ws_node_id === m.id ? " selected" : ""}>${e(m.name)}${m.state && m.state !== "verified" ? ` (${e(String(m.state).replaceAll("_", " "))})` : ""}</option>`).join("");
  const machineName = machines.find(m => m.id === values.ws_node_id)?.name || "";
  const lockedMachine = editing && row.started;
  const note = editing ? (row.started ? "This workspace has been started. A change to the image, limits or network needs a restart to apply; a new name does not." : "Not started yet, so changes apply when it starts.") : "Nothing starts when you save. This only records the settings.";
  const preview = ctx.previewText;
  return `<section class="compute-editor compute-ws-editor" aria-labelledby="compute-ws-heading"><header><h2 id="compute-ws-heading" tabindex="-1">${heading}</h2><button type="button" class="btn" data-ws-action="close"${busy ? " disabled" : ""}>Close</button></header>`
    + `<p class="compute-quiet">An isolated place where Coworkers run, with its own image and limits. ${e(note)}</p>`
    + `<form data-ws-form="${editing ? "edit" : "create"}" novalidate><fieldset${busy ? " disabled" : ""}><div class="compute-fields">`
    + `<label class="compute-wide">Name<input name="ws_name" value="${e(values.ws_name)}" maxlength="${options.name_limit}" autocomplete="off" required${describe("name", false, errors)}>${fieldError("name", errors)}</label>`
    + `<label class="compute-wide">Image<select name="ws_image_choice"${describe("image", true, errors)}>${choice}</select>${values.ws_image_source === "curated" ? hint("image", e(selected?.description || "A curated image.") + " The exact version is recorded when it is first downloaded.") : ""}${values.ws_image_source === "custom" ? "" : fieldError("image", errors)}</label>`
    + (values.ws_image_source === "custom" ? `<label class="compute-wide">Image reference<input name="ws_image" value="${e(values.ws_image)}" spellcheck="false" autocapitalize="none" autocomplete="off" placeholder="ghcr.io/team/app@sha256:…"${describe("image", true, errors)}>${hint("image", "Pin it with @sha256: so it cannot change under you (recommended); a tag like :1.2 can move.")}${fieldError("image", errors)}</label>`
      + `<label class="compute-check compute-wide"><input type="checkbox" name="ws_private_registry"${values.ws_private_registry ? " checked" : ""}><span class="compute-check-text">This image is in a private registry<small class="compute-quiet">Logins are never typed here. After saving you bind a stored credential by its reference.</small></span></label>` : "")
    + `<label>CPU (cores)<input name="ws_cpu_cores" type="number" inputmode="decimal" min="${L.cpu_cores.min}" max="${L.cpu_cores.max}" step="${L.cpu_cores.step}" value="${e(values.ws_cpu_cores)}"${describe("cpu_cores", true, errors)}>${hint("cpu_cores", `${L.cpu_cores.min} to ${L.cpu_cores.max} cores`)}${fieldError("cpu_cores", errors)}</label>`
    + `<label>Memory (GiB)<input name="ws_memory_gib" type="number" inputmode="decimal" min="${L.memory_mib.min / 1024}" max="${L.memory_mib.max / 1024}" step="${L.memory_mib.step / 1024}" value="${e(values.ws_memory_gib)}"${describe("memory_mib", true, errors)}>${hint("memory_mib", `${L.memory_mib.min / 1024} to ${L.memory_mib.max / 1024} GiB. A hard limit: the workspace is stopped if it goes over.`)}${fieldError("memory_mib", errors)}</label>`
    + `<label>Storage (GiB)<input name="ws_storage_gib" type="number" inputmode="numeric" min="${L.storage_gib.min}" max="${L.storage_gib.max}" step="${L.storage_gib.step}" value="${e(values.ws_storage_gib)}"${describe("storage_gib", true, errors)}>${hint("storage_gib", `${L.storage_gib.min} to ${L.storage_gib.max} GiB, kept in a volume only this workspace can see`)}${fieldError("storage_gib", errors)}</label>`
    + `<label>Network<select name="ws_network"${describe("network", false, errors)}>${options.networks.map(n => `<option value="${e(n)}"${values.ws_network === n ? " selected" : ""}>${e(NETWORKS[n] || n)}</option>`).join("")}</select>${fieldError("network", errors)}</label>`
    + `<label class="compute-wide">Machine<select name="ws_node_id"${lockedMachine ? " disabled" : ""}${describe("node_id", true, errors)}>${machineOptions}</select>${hint("node_id", lockedMachine ? "A workspace that has started cannot move to another machine yet." : machines.length ? "The machine it will run on. You can choose later." : "No machines are enrolled yet. Save now and choose a machine later; add one under Advanced → Targets.")}${fieldError("node_id", errors)}</label>`
    + `</div></fieldset>`
    + `<div class="compute-ws-preview" data-ws-preview role="status" aria-live="polite"><strong>${editing ? "After this change" : "What it will use"}</strong><p>${e(preview)}</p></div>`
    + `<p class="compute-error" data-ws-form-error role="alert">${e(formError)}</p>`
    + `<div class="compute-actions"><button class="btn primary" type="submit"${busy ? " disabled" : ""}>${busy ? "Saving…" : editing ? "Review the change" : "Review and create"}</button><button type="button" class="btn" data-ws-action="close"${busy ? " disabled" : ""}>Cancel</button></div></form></section>`;
}

export function reviewHTML(ctx) {
  const { values, editing, row, busy, previewText } = ctx;
  const image = imageOf(values), pinned = parseImage(image).pinned;
  const lines = [`<li>${e(previewText)}</li>`, `<li>Image ${e(image)}${pinned ? " (pinned to a digest)" : " (not pinned: the tag can change)"}</li>`, `<li>Network: ${e(NETWORKS[values.ws_network] || values.ws_network)}</li>`];
  if (values.ws_image_source === "custom" && values.ws_private_registry) lines.push("<li>Private registry: you bind a registry login by reference after saving.</li>");
  const restart = editing && row.started ? '<p class="compute-ws-restart">The running workspace keeps its old settings until it is restarted.</p>' : "";
  return `<section class="compute-editor compute-ws-editor" aria-labelledby="compute-ws-heading"><header><h2 id="compute-ws-heading" tabindex="-1">${editing ? `Confirm the change to ${e(values.ws_name)}` : `Create ${e(values.ws_name)}?`}</h2></header>`
    + `<ul class="compute-ws-review">${lines.join("")}</ul>${restart}<p class="compute-quiet">${editing ? "" : "Nothing starts yet: this saves the settings. "}The server checks them again when you confirm.</p>`
    + `<p class="compute-error" data-ws-form-error role="alert">${e(ctx.formError)}</p>`
    + `<div class="compute-actions"><button type="button" class="btn primary" data-ws-action="confirm"${busy ? " disabled" : ""}>${busy ? "Saving…" : editing ? "Confirm and save" : "Confirm and create"}</button><button type="button" class="btn" data-ws-action="back"${busy ? " disabled" : ""}>Back</button></div></section>`;
}

export function bindHTML(ctx) {
  const { row, values, errors, busy, formError } = ctx;
  return `<section class="compute-editor compute-ws-editor" aria-labelledby="compute-ws-heading"><header><h2 id="compute-ws-heading" tabindex="-1">Bind a registry login to ${e(row.name)}</h2><button type="button" class="btn" data-ws-action="close"${busy ? " disabled" : ""}>Close</button></header>`
    + `<p class="compute-quiet">Give the reference of a credential that is already stored for this workspace. No password, token or key is typed here, and a login can be bound only once. The registry login itself is used when starting arrives; until then this only records which one.</p>`
    + `<form data-ws-form="bind" novalidate><fieldset${busy ? " disabled" : ""}><div class="compute-fields">`
    + `<label>Credential reference<input name="ws_credential_id" value="${e(values.ws_credential_id)}" autocomplete="off" spellcheck="false"${describe("credential_id", false, errors)}>${fieldError("credential_id", errors)}</label>`
    + `<label>Version<input name="ws_credential_version" type="number" min="1" step="1" value="${e(values.ws_credential_version)}"${describe("credential_version", false, errors)}>${fieldError("credential_version", errors)}</label>`
    + `</div></fieldset><p class="compute-error" data-ws-form-error role="alert">${e(formError)}</p>`
    + `<div class="compute-actions"><button class="btn primary" type="submit"${busy ? " disabled" : ""}>${busy ? "Saving…" : "Bind this login"}</button><button type="button" class="btn" data-ws-action="close"${busy ? " disabled" : ""}>Cancel</button></div></form></section>`;
}

const SERVER_FIELD = { name: "name", image: "image", image_source: "image", cpu_cores: "cpu_cores", memory_mib: "memory_mib", storage_gib: "storage_gib", network: "network", node_id: "node_id", private_registry: "image" };
const LOAD_ERROR = "Cloud workspaces could not be loaded. Reload to try again.";

/**
 * ctx: { request, epoch, valid(page), denied(failure), rerender(), host(), machines(), manage() }
 * Owns its own state; compute.js only embeds html() and forwards events.
 */
export function createWorkspaces(ctx) {
  let rows = null, options = null, canManage = false, loadError = "", configError = "", loading = false;
  let mode = "", step = "edit", values = {}, errors = {}, selected = null, busy = false, formError = "", notice = "";
  const opts = () => options || FALLBACK;
  const find = id => rows?.find(row => row.id === id);
  const machines = () => (ctx.machines() || []).map(node => ({ id: node.id, name: node.name, state: node.state }));
  const previewText = () => {
    const cpu = number(values.ws_cpu_cores), memory = number(values.ws_memory_gib), storage = number(values.ws_storage_gib);
    const ok = x => Number.isFinite(x), machine = machines().find(m => m.id === values.ws_node_id)?.name || (values.ws_node_id ? "the chosen machine" : "");
    return `${ok(cpu) ? cpu : "?"} CPU, ${ok(memory) ? gib(Math.round(memory * 1024)) : "?"} memory, ${ok(storage) ? storage : "?"} GiB storage${machine ? ` on ${machine}` : ". No machine chosen yet"}`;
  };
  const context = () => ({ values, errors, options: opts(), machines: machines(), editing: mode === "edit", row: find(selected), busy, formError, previewText: previewText() });

  function painted() {
    const root = ctx.host()?.querySelector?.("[data-ws-root]");
    if (root) root.innerHTML = inner(); else ctx.rerender();
    return root;
  }
  function paint(focus = "") {
    const root = painted();
    const spot = focus ? root?.querySelector?.(`[name="${focus}"], [data-ws-action="${focus}"]`) : root?.querySelector?.("#compute-ws-heading");
    spot?.focus?.();
  }
  // Typing only refreshes the preview and its own error, so the field keeps its focus and caret.
  function live(field) {
    const host = ctx.host();
    const preview = host?.querySelector?.("[data-ws-preview] p");
    if (preview) preview.textContent = previewText();
    for (const name of field ? [field] : Object.keys(errors)) {
      const slot = host?.querySelector?.(`[data-ws-error="${name}"]`);
      if (slot) slot.textContent = errors[name] || "";
      const control = host?.querySelector?.(`[aria-describedby$="ws-error-${name}"]`);
      if (control) { if (errors[name]) control.setAttribute?.("aria-invalid", "true"); else control.removeAttribute?.("aria-invalid"); }
    }
  }

  function inner() {
    if (rows === null && !loadError) return '<p class="compute-empty" role="status">Loading cloud workspaces…</p>';
    if (rows === null) return `<p class="compute-error" role="alert">${e(loadError)}</p><div class="compute-actions"><button type="button" class="btn" data-ws-action="reload">Reload</button></div>`;
    let panel = "";
    if (mode === "create" || mode === "edit") panel = step === "review" ? reviewHTML(context()) : formHTML(context());
    else if (mode === "bind") panel = bindHTML({ ...context(), row: find(selected) });
    else if (mode === "detail" && find(selected)) panel = detailHTML(find(selected), { manage: canManage, busy });
    const list = rows.length
      ? `<ul class="compute-list compute-ws-list">${rows.map(row => rowHTML(row, { manage: canManage, busy })).join("")}</ul>`
      : `<p class="compute-empty">${canManage ? "No cloud workspaces yet. A cloud workspace is an isolated place where Coworkers run, with its own image and limits. Create one to get started." : "No cloud workspaces yet. The workspace owner can create one here."}</p>`;
    const unavailable = configError ? `<p class="compute-error" role="alert">${e(configError)}</p><div class="compute-actions"><button type="button" class="btn" data-ws-action="reload">Reload</button></div>` : "";
    return `${notice ? `<p class="compute-notice" role="status">${e(notice)}</p>` : ""}${unavailable}${panel}${list}`;
  }
  function html() {
    const create = canManage && rows !== null ? `<button type="button" class="btn primary" data-ws-action="create"${busy || mode || !options ? " disabled" : ""}>New cloud workspace</button>` : "";
    return `<section class="compute-inventory compute-wide compute-ws" aria-labelledby="compute-workspaces"><header><h2 id="compute-workspaces" tabindex="-1">Cloud workspaces</h2>${create}</header>`
      + `<p class="compute-plain">A cloud workspace is an isolated environment on one of your machines where Coworkers run, with its own image, CPU, memory and storage.</p>`
      + `<div data-ws-root>${inner()}</div></section>`;
  }

  async function load() {
    const page = ctx.epoch();
    if (loading) return;
    loading = true; loadError = "";
    const [list, config] = await Promise.allSettled([ctx.request(BASE), ctx.request(`${BASE}/images`)]);
    loading = false;
    if (!ctx.valid(page)) return;
    for (const result of [list, config]) if (result.status === "rejected" && result.reason?.status === 401 && ctx.denied(result.reason)) return;
    if (list.status === "rejected" || !Array.isArray(list.value?.workspaces)) { rows = null; loadError = LOAD_ERROR; }
    else { rows = list.value.workspaces; canManage = list.value.can_manage === true; }
    configError = "";
    if (config.status === "fulfilled" && config.value?.images && config.value?.limits) options = config.value;
    else { options = null; if (rows !== null && canManage) configError = "The image list could not be loaded, so new workspaces cannot be created right now. Reload to try again."; }
    if (mode && !find(selected) && mode !== "create") { mode = ""; step = "edit"; }
    ctx.rerender();
  }

  function open(kind, id) {
    if (busy) return;
    const row = id ? find(id) : null;
    if ((kind === "create" && (!canManage || !options)) || (kind !== "create" && kind !== "detail" && (!canManage || !row)) || (kind === "detail" && !row)) return;
    mode = kind; step = "edit"; selected = id || null; errors = {}; formError = ""; notice = "";
    values = kind === "create" ? blankValues(options) : kind === "edit" ? blankValues(options, row) : kind === "bind" ? { ws_credential_id: "", ws_credential_version: "1" } : {};
    ctx.rerender();
    ctx.host()?.querySelector?.("#compute-ws-heading")?.focus?.();
  }
  function close() {
    const from = mode, id = selected;
    mode = ""; step = "edit"; selected = null; errors = {}; formError = ""; values = {};
    ctx.rerender();
    const host = ctx.host();
    (id && host?.querySelector?.(`[data-ws-id="${id}"] [data-ws-action="${from === "bind" ? "bind" : "edit"}"]`) || host?.querySelector?.('[data-ws-action="create"]'))?.focus?.();
  }

  function fail(failure) {
    const payload = failure?.payload || {};
    const field = SERVER_FIELD[payload.field];
    if (failure?.status === 409 && payload.reason === "stale_revision") { formError = "Someone changed this workspace in the meantime. Reload, then make the change again."; return; }
    const text = field ? reasonText(payload.field === "image_source" || payload.field === "private_registry" ? "image" : payload.field, payload.reason, opts()) : "";
    if (field && text) { errors = { [field]: text }; step = "edit"; formError = "Fix the field below and try again."; return; }
    formError = ({ owner_required: "Only the workspace owner can change cloud workspaces.", not_found: "This cloud workspace is no longer available. Reload.", conflict: "The workspace changed or the request conflicts. Reload and check it.", credential_already_bound: "A registry login is already bound to this workspace.", invalid_request: "The server refused these values. Check the fields and try again." }[payload.error]) || (failure?.status ? "Saving failed. Try again." : "No answer from the server, so nothing is confirmed. Reload to check what was saved before trying again.");
  }

  async function save(kind) {
    if (busy) return;
    const page = ctx.epoch(), row = find(selected);
    let path, body;
    if (kind === "bind") { path = `${BASE}/${enc(selected)}/credential`; body = { credential_id: values.ws_credential_id.trim(), credential_version: number(values.ws_credential_version) }; }
    else if (kind === "edit") { path = `${BASE}/${enc(selected)}`; body = workspacePayload(values, { revision: row.revision }); }
    else { path = BASE; body = workspacePayload(values); }
    busy = true; formError = ""; ctx.rerender();
    try {
      const result = await ctx.request(path, { method: "POST", body });
      if (!ctx.valid(page)) return;
      const saved = result.workspace;
      rows = [saved, ...rows.filter(item => item.id !== saved.id)].sort((a, b) => b.created_at - a.created_at);
      notice = kind === "create" ? `Created ${saved.name}. ${statusView(saved).label}: ${statusView(saved).detail}` : kind === "bind" ? `Registry login bound to ${saved.name}.` : `Saved ${saved.name}.${saved.restart_needed ? " Restart needed to apply the new settings." : saved.started ? "" : " It has not started, so the change applies when it starts."}`;
      mode = ""; step = "edit"; selected = null; values = {}; errors = {};
      busy = false; ctx.rerender();
      ctx.host()?.querySelector?.("#compute-workspaces")?.focus?.();
    } catch (failure) {
      if (!ctx.valid(page) || (failure?.status === 401 && ctx.denied(failure))) return;
      fail(failure); busy = false; ctx.rerender(); ctx.host()?.querySelector?.("#compute-ws-heading")?.focus?.();
    }
  }

  function click(event) {
    const target = event.target?.closest?.("[data-ws-action]");
    if (!target) return false;
    const { wsAction: action, id } = target.dataset;
    if (target.disabled) return true;
    if (action === "reload") { rows = null; loadError = ""; ctx.rerender(); void load(); }
    else if (action === "create" || action === "edit" || action === "detail" || action === "bind") open(action, id);
    else if (action === "close") { close(); }
    else if (action === "back") { step = "edit"; formError = ""; paint(); }
    else if (action === "confirm") void save(mode);
    return true;
  }
  function input(event) {
    const field = event.target;
    if (!field?.name?.startsWith?.("ws_") || !mode) return false;
    const { name } = field;
    if (name === "ws_image_choice") {
      if (field.value === "custom") values.ws_image_source = "custom";
      else { values.ws_image_source = "curated"; values.ws_curated = field.value; }
      errors = {}; paint("ws_image_choice"); return true;
    }
    values[name] = field.type === "checkbox" ? field.checked === true : field.value;
    const key = { ws_name: "name", ws_image: "image", ws_cpu_cores: "cpu_cores", ws_memory_gib: "memory_mib", ws_storage_gib: "storage_gib", ws_network: "network" }[name];
    if (key) {
      // Show a problem as soon as a field has one; clear it the moment it is fixed.
      const found = validate(values, opts(), key);
      if (found[key]) errors[key] = found[key]; else delete errors[key];
      live(key);
    } else live("");
    return true;
  }
  function submit(event) {
    const form = event.target?.closest?.("[data-ws-form]");
    if (!form?.dataset?.wsForm) return false;
    event.preventDefault?.(); event.stopPropagation?.();
    if (busy || !mode) return true;
    if (mode === "bind") {
      errors = {};
      if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(String(values.ws_credential_id || "").trim())) errors.credential_id = reasonText("credential_id", "invalid");
      const version = number(values.ws_credential_version);
      if (!Number.isInteger(version) || version < 1) errors.credential_version = reasonText("credential_version", "invalid");
      if (Object.keys(errors).length) { formError = "Fix the fields below."; paint(Object.keys(errors)[0] === "credential_id" ? "ws_credential_id" : "ws_credential_version"); return true; }
      void save("bind"); return true;
    }
    errors = validate(values, opts());
    const first = Object.keys(errors)[0];
    if (first) {
      formError = "Fix the fields below, then review again.";
      paint({ name: "ws_name", image: values.ws_image_source === "custom" ? "ws_image" : "ws_image_choice", cpu_cores: "ws_cpu_cores", memory_mib: "ws_memory_gib", storage_gib: "ws_storage_gib", network: "ws_network" }[first]);
      return true;
    }
    formError = ""; step = "review"; paint();
    return true;
  }
  function reset() { rows = null; options = null; canManage = false; loadError = ""; configError = ""; loading = false; mode = ""; step = "edit"; values = {}; errors = {}; selected = null; busy = false; formError = ""; notice = ""; }
  return { html, load, click, input, submit, reset, get open() { return mode !== ""; }, get loaded() { return rows !== null; }, get state() { return { rows, mode, step, values, errors, canManage, notice, formError }; } };
}
