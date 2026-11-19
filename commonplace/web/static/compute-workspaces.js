import { escape as e } from "./api.js";

// Cloud workspaces on the Compute screen (docs/system/compute-workspaces.md). One shared module serves the
// Classic screen and the Deck Management tab: createCompute (compute.js) mounts the section and forwards
// clicks, inputs, submits and keys. Nothing here starts a container: a record says "Not started yet" until the
// lifecycle card lands, and every sentence about running or enforcement is written as "once it can start".
// The server validates everything again and serves the ranges; client checks only explain early.

const BASE = "/api/compute/workspaces";
const enc = encodeURIComponent;
const NETWORKS = { outbound: "Internet access allowed", none: "No network" };
const MACHINE_STATES = { pending_verification: "not checked yet", unverified: "not checked yet" };
const FAILURES = {
  out_of_memory: memory => `It ran out of memory at ${memory}. Raise the memory limit; the new limit applies the next time it starts.`,
  storage_full: () => "Its storage is full. Raise the storage limit or free space; the change applies the next time it starts.",
  image_pull_failed: () => "The machine could not download the image. Check the image address.",
  image_not_found: () => "No such image was found. Check the image name and version.",
  registry_login_failed: () => "The registry refused the login. Registry logins can't be changed here yet.",
  machine_too_small: () => "The machine is smaller than these limits. Lower them or choose a bigger machine.",
  limit_not_enforceable: () => "The machine cannot enforce these limits, so it was not started. Nothing ran without limits.",
  machine_unreachable: () => "The machine could not be reached.",
  start_failed: () => "It did not start.",
};
const PREVIEW_INVALID = "Fix the highlighted fields to see what this will use.";
const STALE = "This workspace changed while you were editing. Close this form, reload the list, and make your change again.";
const NO_ANSWER = "No answer from the server, so nothing is confirmed. Reload to check what was saved before trying again.";
const LOAD_ERROR = "Cloud workspaces could not be loaded. Reload to try again.";
const DELETE_NOTE = "A cloud workspace that has been started can't be deleted yet.";

const cores = count => `${count} CPU ${count === 1 ? "core" : "cores"}`;
export const gib = mib => `${Number((mib / 1024).toFixed(3))} GiB`;
export const resourcePreview = (cpu, memoryMib, storage, machine, none = "No machine chosen yet") =>
  `${cores(cpu)}, ${gib(memoryMib)} memory, ${storage} GiB storage${machine ? ` on ${machine}` : `. ${none}`}`;

export const REASONS = {
  name: { length: limit => `Give it a name of 1 to ${limit} characters, for example Build box.`, characters: () => "Use visible characters only: no line breaks, hidden or direction-changing characters, or spaces at either end.", no_letter: () => "Use at least one letter or digit in the name.", name_taken: () => "Another cloud workspace already uses this name." },
  image: {
    empty: () => "Choose an image, or enter your own address, for example ghcr.io/acme/tools:1.2.",
    too_long: () => "An image address can be at most 255 characters.",
    characters: () => "This doesn't look like an image address. Remove spaces and symbols such as ; and !. Example: ghcr.io/acme/tools:1.2.",
    scheme: () => "Leave out https:// . An image address is just the name, like ghcr.io/acme/tools:1.2.",
    registry: () => "The registry address before the first / is not valid. Example: ghcr.io/acme/tools:1.2.",
    registry_private: () => "That registry address is on a local or private network. Use an image from a public registry such as ghcr.io.",
    registry_no_path: () => "Add the image name after the registry address, like ghcr.io/acme/tools:1.2.",
    name: () => "The image name must be lowercase letters and digits separated by . _ or -.",
    tag: () => "The version after the colon is not valid. Example: ghcr.io/acme/tools:1.2.",
    digest: () => "A digest looks like @sha256: followed by 64 lowercase hex characters.",
    not_curated: () => "Pick one of the listed images, or choose My own image.",
  },
  private_registry: { unavailable: () => "Images that need a registry login can't be used yet. Use a public image.", choice: () => "Choose whether the image is public." },
  request_id: { request_reused: () => "This form was already sent with different values. Close it and start again." },
  cpu_cores: { range: l => `Enter a number of cores from ${l.min} to ${l.max}.`, step: l => `Use steps of ${l.step}, for example 2 or 2.5.`, not_a_number: () => "Enter a number of cores, for example 2." },
  memory_mib: { range: l => `Enter an amount from ${l.min / 1024} to ${l.max / 1024} GiB.`, step: l => `Use steps of ${l.step / 1024} GiB.`, not_a_number: () => "Enter an amount of memory in GiB, for example 4." },
  storage_gib: { range: l => `Enter a whole number of GiB from ${l.min} to ${l.max}.`, step: () => "Storage is a whole number of GiB.", not_a_number: () => "Enter a whole number of GiB, for example 20." },
  network: { choice: () => "Choose a network setting." },
  node_id: { unknown_machine: () => "That machine is no longer available. Reload and choose another.", machine_locked: () => "A workspace that has started can't move to another machine yet.", choice: () => "Choose a machine from the list." },
};
export const reasonText = (field, reason, options) => {
  const spec = REASONS[field]?.[reason];
  if (!spec) return "";
  return spec(field === "name" ? options.name_limit : options.limits?.[field]);
};
const GENERAL = {
  stale_revision: () => STALE,
  limit_reached: options => `You have reached the limit of ${options.max_workspaces} cloud workspaces. Discard one that was never started to make room.`,
  already_started: () => "This workspace has been started, so it can't be discarded yet.",
  registry_logins_unavailable: () => "Registry logins can't be connected yet.",
  required: () => "A required value is missing. Fill in every field marked *.",
  no_changes: () => "You haven't changed anything yet.",
};
const ERROR_CODES = { owner_required: "Only the workspace owner can change cloud workspaces.", not_found: "This cloud workspace is no longer available. Reload the list.", sign_in: "Sign in again to continue." };

const LABEL = "[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?";
const HOST = new RegExp(`^${LABEL}(?:\\.${LABEL})*(?::([0-9]{1,5}))?$`);
const COMPONENT = /^[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*$/;
const TAG = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/;
const DIGEST = /^sha256:[a-f0-9]{64}$/;
const SAFE = /^[A-Za-z0-9._:/@-]+$/;
const NUMERIC_LABEL = /^(?:0x[0-9a-f]+|[0-9]+)$/;
const LOCAL_SUFFIX = /\.(?:local|localhost|internal|lan|home|corp|intranet|private|home\.arpa)$/;
// A host with no dot, a local suffix or an IP address in any notation names a private network.
const privateHost = host => { const name = host.split(":")[0]; return !name.includes(".") || LOCAL_SUFFIX.test(name) || NUMERIC_LABEL.test(name.split(".").pop()); };

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
  if (parts.length === 1 && parts[0].includes(".")) return { reason: "registry_no_path" };
  if (parts.length > 1 && (/[.:]/.test(parts[0]) || parts[0] === "localhost")) {
    const match = HOST.exec(parts[0]);
    if (!match || (match[1] !== undefined && !(Number(match[1]) >= 1 && Number(match[1]) <= 65535))) return { reason: "registry" };
    if (privateHost(parts[0])) return { reason: "registry_private" };
    registry = parts[0]; parts = parts.slice(1);
  }
  if (!parts.length || parts.some(part => !COMPONENT.test(part))) return { reason: "name" };
  return { registry, tag, digest, pinned: digest !== null };
}

// Names: NFC, counted in code points, no hidden or direction-changing characters, at least one letter or digit.
const BAD_NAME = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}]/u;
const nameOf = value => String(value ?? "").normalize("NFC");

export const blankValues = (options, row = null) => row ? {
  ws_name: row.name, ws_image_source: row.image_source, ws_curated: row.image_source === "curated" ? row.image : "", ws_image: row.image_source === "custom" ? row.image : "",
  ws_cpu_cores: String(row.cpu_cores), ws_memory_gib: String(row.memory_mib / 1024), ws_storage_gib: String(row.storage_gib),
  ws_network: row.network, ws_node_id: row.node_id || "",
} : {
  ws_name: "", ws_image_source: options.images?.length ? "curated" : "custom", ws_curated: options.images?.[0]?.reference || "", ws_image: "",
  ws_cpu_cores: String(options.limits.cpu_cores.default), ws_memory_gib: String(options.limits.memory_mib.default / 1024), ws_storage_gib: String(options.limits.storage_gib.default),
  ws_network: options.default_network, ws_node_id: "",
};

const number = text => typeof text === "string" && text.trim() !== "" && Number.isFinite(Number(text)) ? Number(text) : NaN;
const imageOf = values => values.ws_image_source === "curated" ? values.ws_curated : String(values.ws_image ?? "").trim();
const RESOURCE_KEYS = ["cpu_cores", "memory_mib", "storage_gib"];

/** Check one field (or all) of the form values; returns { field: message }. Empty object means it can be sent. */
export function validate(values, options, only = null) {
  const errors = {}, limits = options.limits, want = field => !only || only === field;
  if (want("name")) {
    const name = nameOf(values.ws_name);
    if (!name.trim() || Array.from(name).length > options.name_limit) errors.name = reasonText("name", "length", options);
    else if (name !== name.trim() || BAD_NAME.test(name)) errors.name = reasonText("name", "characters", options);
    else if (!/[\p{L}\p{N}]/u.test(name)) errors.name = reasonText("name", "no_letter", options);
  }
  if (want("image")) {
    if (values.ws_image_source === "curated" && !options.images.some(item => item.reference === values.ws_curated)) errors.image = reasonText("image", "not_curated", options);
    else { const parsed = parseImage(imageOf(values)); if (parsed.reason) errors.image = reasonText("image", parsed.reason, options); }
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
  if (want("network") && !options.networks.includes(values.ws_network)) errors.network = reasonText("network", "choice", options);
  return errors;
}

/** Every form value as the API takes it. */
export function workspacePayload(values) {
  return {
    name: nameOf(values.ws_name), image_source: values.ws_image_source, image: imageOf(values),
    cpu_cores: number(values.ws_cpu_cores), memory_mib: Math.round(number(values.ws_memory_gib) * 1024), storage_gib: number(values.ws_storage_gib),
    network: values.ws_network, node_id: values.ws_node_id || null,
  };
}
/** Create body: every value plus the request id that makes a retry after a lost answer safe. */
export const createPayload = (values, requestId) => requestId ? { ...workspacePayload(values), request_id: requestId } : workspacePayload(values);
/** Edit body: only the fields that differ from the values the form opened with, plus the revision it opened at. */
export function editPayload(values, baseValues, revision) {
  const now = workspacePayload(values), before = workspacePayload(baseValues), body = {};
  for (const key of Object.keys(now)) if (now[key] !== before[key]) body[key] = now[key];
  if ("image" in body || "image_source" in body) { body.image = now.image; body.image_source = now.image_source; }
  return Object.keys(body).length ? { ...body, expected_revision: revision } : null;
}

const machineOf = (row, manage) => manage ? (row.machine_name || (row.node_id ? "the chosen machine" : "")) : (row.machine_assigned ? "an assigned machine" : "");
const previewOf = (row, manage) => resourcePreview(row.cpu_cores, row.memory_mib, row.storage_gib, machineOf(row, manage), manage ? undefined : "No machine assigned yet");

export function statusView(row, { manage = true } = {}) {
  const memory = gib(row.memory_mib), machine = machineOf(row, manage);
  switch (row.status) {
    case "no_machine": return { tone: "wait", label: "No machine chosen", detail: "Saved in Commonplace. Choose a machine for it to run on; nothing has been sent to a machine yet." };
    case "needs_credential": return { tone: "wait", label: "Needs a registry login", detail: "This image needs a login to download. Registry logins can't be added yet, so it can't start." };
    case "pending": return { tone: "quiet", label: "Not started yet", detail: `Saved in Commonplace. Nothing has been sent to ${machine || "the machine"} yet. It will run there once starting is available.` };
    case "restart_needed": return { tone: "wait", label: "Restart needed", detail: "Its saved settings changed after it last started. The new settings apply when it is restarted." };
    case "ready": return { tone: "ok", label: "Ready", detail: "It is running with the saved settings." };
    case "failed": { const reason = FAILURES[row.status_reason]; return { tone: "bad", label: "Failed", detail: reason ? reason(memory) : "Failed: reason not recognised. Check the machine." }; }
    default: return { tone: "wait", label: "Status unknown", detail: "Reload to see the current status." };
  }
}
export const imageNote = row => row.image_pinned ? "Locked to one exact version." : row.image_source === "curated" ? "Official image. The publisher can update its version over time." : "The publisher can update this version over time.";
const deleteNote = row => row.discardable ? `You can change this later, or discard it because it was never started. ${DELETE_NOTE}` : `You can change this later. ${DELETE_NOTE}`;
const editsOf = row => { const n = Math.max(0, (row.revision || 1) - 1); return n === 0 ? "Not changed since it was created" : `Changed ${n} time${n === 1 ? "" : "s"} since it was created`; };

const aria = (label, name) => ` aria-label="${e(`${label} ${name}`)}"`;

export function rowHTML(row, { manage, busy }) {
  const status = statusView(row, { manage }), dis = busy ? " disabled" : "";
  return `<li class="compute-row compute-ws-row" data-ws-id="${e(row.id)}" data-status="${e(row.status)}"><header><h3 class="compute-ws-name">${e(row.name)}</h3><span class="compute-state compute-ws-status" data-tone="${status.tone}">${e(status.label)}</span></header>`
    + `<p>${e(previewOf(row, manage))}</p>`
    + `<div class="compute-actions"><button type="button" class="btn" data-ws-action="detail" data-id="${e(row.id)}"${aria("Details for", row.name)}${dis}>Details</button>${manage ? `<button type="button" class="btn" data-ws-action="edit" data-id="${e(row.id)}"${aria("Change settings for", row.name)}${dis}>Change settings</button>${row.discardable ? `<button type="button" class="btn" data-ws-action="discard" data-id="${e(row.id)}"${aria("Discard", row.name)}${dis}>Discard</button>` : ""}` : ""}</div></li>`;
}

export function detailHTML(row, { manage, busy }) {
  const status = statusView(row, { manage });
  const machine = manage ? (row.machine_name || (row.node_id ? "The chosen machine" : "No machine chosen yet")) : (row.machine_assigned ? "A machine is assigned" : "No machine assigned yet");
  const rows = [
    ["Resources", e(previewOf(row, manage))], ["Image", `${e(row.image)}<br>${e(imageNote(row))}`],
    ["Registry", `${e(row.image_registry)}${row.private_registry ? " (private: needs a login, which can't be added yet)" : ""}`], ["Network", e(NETWORKS[row.network] || row.network)], ["Machine", e(machine)],
    ["History", `${e(editsOf(row))}. ${row.started ? (row.restart_needed ? "The running copy has not picked up the latest change." : "The running copy has the latest settings.") : "Not started yet."}`],
  ];
  const actions = manage
    ? `<div class="compute-actions"><button type="button" class="btn primary" data-ws-action="edit" data-id="${e(row.id)}"${busy ? " disabled" : ""}>Change settings</button>${row.discardable ? `<button type="button" class="btn" data-ws-action="discard" data-id="${e(row.id)}"${busy ? " disabled" : ""}>Discard</button>` : ""}<button type="button" class="btn" data-ws-action="close">Close</button></div><p class="compute-quiet">${e(deleteNote(row))}</p>`
    : `<p class="compute-quiet">Only the workspace owner can change cloud workspaces. You can look at the settings here.</p><div class="compute-actions"><button type="button" class="btn" data-ws-action="close">Close</button></div>`;
  return `<section class="compute-editor compute-ws-detail" aria-labelledby="compute-ws-heading"><header><h3 id="compute-ws-heading" tabindex="-1">${e(row.name)}</h3></header>`
    + `<p class="compute-ws-statusline"><strong class="compute-ws-status" data-tone="${status.tone}">${e(status.label)}.</strong> ${e(status.detail)}</p>`
    + `<dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>${actions}</section>`;
}

const REQUIRED = ' <span class="compute-req" aria-hidden="true">*</span>';
const controlAttrs = (key, id, hasHint, errors, extra = "") => ` id="${id}" data-ws-control="${key}" aria-describedby="${hasHint ? `ws-hint-${key} ` : ""}ws-error-${key}"${errors[key] ? ' aria-invalid="true"' : ""}${extra}`;
// The label closes before the hint and the error, so the accessible name is only the label.
const field = (key, id, label, control, hint, errors, { wide = false, required = false } = {}) =>
  `<div class="compute-field${wide ? " compute-wide" : ""}"><label for="${id}">${e(label)}${required ? REQUIRED : ""}</label>${control}${hint ? `<small class="compute-quiet" id="ws-hint-${key}">${e(hint)}</small>` : ""}<small class="compute-ws-error" id="ws-error-${key}" data-ws-error="${key}">${e(errors[key] || "")}</small></div>`;
const summary = text => `<div class="compute-ws-summary" data-ws-summary role="alert" tabindex="-1">${e(text)}</div>`;

export function formHTML(ctx) {
  const { values, errors, options, machines, editing, row, busy, formError } = ctx;
  const heading = editing ? `Change ${e(row.name)}` : "New cloud workspace";
  const L = options.limits, custom = values.ws_image_source === "custom";
  const curated = options.images.map(item => `<option value="${e(item.reference)}"${values.ws_curated === item.reference && !custom ? " selected" : ""}>${e(item.label)}</option>`).join("");
  const choice = `${curated}<option value="custom"${custom ? " selected" : ""}>My own image…</option>`;
  const selected = options.images.find(item => item.reference === values.ws_curated);
  const stateLabel = state => state && state !== "verified" ? ` (${MACHINE_STATES[state] || String(state).replaceAll("_", " ")})` : "";
  const machineOptions = `<option value=""${values.ws_node_id ? "" : " selected"}>Choose later</option>` + machines.map(m => `<option value="${e(m.id)}"${values.ws_node_id === m.id ? " selected" : ""}>${e(m.name)}${e(stateLabel(m.state))}</option>`).join("");
  const locked = editing && row.started;
  const note = editing ? (row.started ? "This workspace has been started. A change to the image, limits or network applies when it is restarted; a new name applies at once." : "Not started yet, so changes apply when it first starts.") : "Saved in Commonplace. Nothing is sent to a machine yet.";
  const select = (key, id, name, inner, extra = "", hasHint = true) => `<select name="${name}"${controlAttrs(key, id, hasHint, errors, extra)}>${inner}</select>`;
  const num = (key, id, name, spec, scale, mode) => `<input type="number" name="${name}"${controlAttrs(key, id, true, errors)} inputmode="${mode}" min="${spec.min / scale}" max="${spec.max / scale}" step="${spec.step / scale}" value="${e(values[name])}">`;
  const imageKey = custom ? "image_choice" : "image";
  const fields = [
    field("name", "ws-field-name", "Name", `<input name="ws_name"${controlAttrs("name", "ws-field-name", true, errors, " required")} value="${e(values.ws_name)}" autocomplete="off" placeholder="Build box">`, "Shown in lists, so pick something your team will recognise, for example Build box.", errors, { wide: true, required: true }),
    field(imageKey, "ws-field-image-choice", "Image", select(imageKey, "ws-field-image-choice", "ws_image_choice", choice, " required"), `An image is the ready-made software a workspace starts from. ${custom ? "Paste the address of your own image below." : selected?.description || ""}`.trim(), errors, { wide: true, required: true }),
    custom ? field("image", "ws-field-image", "Image address", `<input name="ws_image"${controlAttrs("image", "ws-field-image", true, errors, " required")} value="${e(values.ws_image)}" spellcheck="false" autocapitalize="none" autocomplete="off" placeholder="ghcr.io/acme/tools:1.2">`, "Paste the address as its publisher gives it, for example ghcr.io/acme/tools:1.2. Only public images work for now.", errors, { wide: true, required: true }) : "",
    field("cpu_cores", "ws-field-cpu", "CPU cores", num("cpu_cores", "ws-field-cpu", "ws_cpu_cores", L.cpu_cores, 1, "decimal"), `Default ${L.cpu_cores.default}. From ${L.cpu_cores.min} to ${L.cpu_cores.max}. Once workspaces can start, it will be slowed down at this limit, not stopped.`, errors),
    field("memory_mib", "ws-field-memory", "Memory (GiB)", num("memory_mib", "ws-field-memory", "ws_memory_gib", L.memory_mib, 1024, "decimal"), `Default ${L.memory_mib.default / 1024}. From ${L.memory_mib.min / 1024} to ${L.memory_mib.max / 1024}. Once workspaces can start, it will be stopped if it goes over this.`, errors),
    field("storage_gib", "ws-field-storage", "Storage (GiB)", num("storage_gib", "ws-field-storage", "ws_storage_gib", L.storage_gib, 1, "numeric"), `Default ${L.storage_gib.default}. From ${L.storage_gib.min} to ${L.storage_gib.max}. Once workspaces can start, writes will fail when it is full.`, errors),
    field("network", "ws-field-network", "Network", select("network", "ws-field-network", "ws_network", options.networks.map(n => `<option value="${e(n)}"${values.ws_network === n ? " selected" : ""}>${e(NETWORKS[n] || n)}</option>`).join("")), "Default: internet access allowed. Applied when the workspace starts.", errors),
    field("node_id", "ws-field-machine", "Machine", select("node_id", "ws-field-machine", "ws_node_id", machineOptions, locked ? " disabled" : ""), locked ? "A started workspace can't move to another machine yet." : machines.length ? "The machine it will run on. You can choose later; machine sizes aren't checked until starting is available." : "No machines are set up yet. Save now and choose one later; machines are added under Advanced, in Targets.", errors, { wide: true }),
  ].join("");
  return `<section class="compute-editor compute-ws-editor" aria-labelledby="compute-ws-heading"><header><h3 id="compute-ws-heading" tabindex="-1">${heading}</h3></header>`
    + `<p class="compute-quiet">${e(note)} Fields marked * are required; the others start with a default. Sizes are in GiB (about 1.07 GB).</p>`
    + `<form data-ws-form="${editing ? "edit" : "create"}" novalidate>${summary(formError)}<fieldset${busy ? " disabled" : ""}><div class="compute-fields">${fields}</div></fieldset>`
    + `<div class="compute-ws-preview" data-ws-preview><strong>${editing ? "After this change" : "What it will use"}</strong><p>${e(ctx.previewText)}</p></div>`
    + `<div class="compute-actions compute-ws-actions"><button class="btn primary" type="submit"${busy ? " disabled" : ""}>${busy ? "Saving…" : editing ? "Review the change" : "Review and create"}</button><button type="button" class="btn" data-ws-action="close"${busy ? " disabled" : ""}>Cancel</button></div></form></section>`;
}

const FIELD_LABELS = { name: "Name", image: "Image", cpu_cores: "CPU", memory_mib: "Memory", storage_gib: "Storage", network: "Network", node_id: "Machine" };
function changeLines(row, values, machines) {
  const now = workspacePayload(values), before = { name: row.name, image: row.image, cpu_cores: row.cpu_cores, memory_mib: row.memory_mib, storage_gib: row.storage_gib, network: row.network, node_id: row.node_id || null };
  const show = { memory_mib: v => gib(v), storage_gib: v => `${v} GiB`, cpu_cores: v => cores(v), network: v => NETWORKS[v] || v, node_id: v => v ? (machines.find(m => m.id === v)?.name || "the chosen machine") : "No machine" };
  return Object.keys(FIELD_LABELS).filter(key => now[key] !== before[key]).map(key => `<li>${e(FIELD_LABELS[key])}: ${e((show[key] || String)(before[key]))} → ${e((show[key] || String)(now[key]))}</li>`);
}

export function reviewHTML(ctx) {
  const { values, editing, row, busy, previewText, machines } = ctx;
  const image = imageOf(values), pinned = parseImage(image).pinned;
  const lines = editing ? changeLines(row, values, machines) : [];
  lines.push(`<li>${e(previewText)}</li>`, `<li>Image ${e(image)}${pinned ? " (locked to one exact version)" : " (the publisher can update its version over time)"}</li>`, `<li>Network: ${e(NETWORKS[values.ws_network] || values.ws_network)}</li>`);
  const restart = editing && row.started ? '<p class="compute-ws-restart">The running workspace keeps its old settings until it is restarted.</p>' : "";
  const next = editing ? "The server checks them again when you confirm." : `This only saves the settings in Commonplace; nothing is sent to a machine yet. You can change them later. ${DELETE_NOTE} The server checks them again when you confirm.`;
  return `<section class="compute-editor compute-ws-editor" aria-labelledby="compute-ws-heading"><header><h3 id="compute-ws-heading" tabindex="-1">${editing ? `Confirm the change to ${e(values.ws_name)}` : `Create ${e(values.ws_name)}?`}</h3></header>${summary(ctx.formError)}`
    + `<ul class="compute-ws-review">${lines.join("")}</ul>${restart}<p class="compute-quiet">${e(next)}</p>`
    + `<div class="compute-actions compute-ws-actions"><button type="button" class="btn primary" data-ws-action="confirm"${busy ? " disabled" : ""}>${busy ? "Saving…" : editing ? "Confirm and save" : "Confirm and create"}</button><button type="button" class="btn" data-ws-action="back"${busy ? " disabled" : ""}>Back</button></div></section>`;
}

export function discardHTML(ctx) {
  const { row, busy, formError } = ctx;
  return `<section class="compute-editor compute-ws-editor" aria-labelledby="compute-ws-heading"><header><h3 id="compute-ws-heading" tabindex="-1">Discard ${e(row.name)}?</h3></header>${summary(formError)}`
    + `<p>This removes the saved settings for ${e(row.name)} from Commonplace. It was never started, so nothing on a machine is affected. You can't undo this, but you can create it again with the same settings.</p>`
    + `<div class="compute-actions compute-ws-actions"><button type="button" class="btn primary" data-ws-action="confirm"${busy ? " disabled" : ""}>${busy ? "Discarding…" : `Discard ${e(row.name)}`}</button><button type="button" class="btn" data-ws-action="close"${busy ? " disabled" : ""}>Keep it</button></div></section>`;
}

const SERVER_FIELD = { name: "name", image: "image", image_source: "image", cpu_cores: "cpu_cores", memory_mib: "memory_mib", storage_gib: "storage_gib", network: "network", node_id: "node_id", private_registry: "image", request_id: "name" };
const CONTROL_KEY = { name: "name", image: "image", cpu_cores: "cpu_cores", memory_mib: "memory_mib", storage_gib: "storage_gib", network: "network" };
const FIELD_OF_INPUT = { ws_name: "name", ws_image: "image", ws_cpu_cores: "cpu_cores", ws_memory_gib: "memory_mib", ws_storage_gib: "storage_gib", ws_network: "network" };

/**
 * ctx: { request, epoch, valid(page), denied(failure), rerender(), host(), machines(), requestId() }
 * Owns its own state; compute.js only embeds html() and forwards events.
 */
export function createWorkspaces(ctx) {
  let rows = null, total = 0, options = null, canManage = false, loadError = "", configError = "", loading = false;
  let mode = "", step = "edit", values = {}, baseValues = null, base = null, errors = {}, selected = null, busy = false, formError = "", notice = "", requestId = "", opener = null;
  const find = id => rows?.find(row => row.id === id);
  const machines = () => (ctx.machines() || []).map(node => ({ id: node.id, name: node.name, state: node.state }));
  const full = () => options && rows !== null && Math.max(total, rows.length) >= options.max_workspaces;
  function previewText() {
    if (RESOURCE_KEYS.some(key => validate(values, options, key)[key])) return PREVIEW_INVALID;
    const machine = machines().find(m => m.id === values.ws_node_id)?.name || (values.ws_node_id ? "the chosen machine" : "");
    return resourcePreview(number(values.ws_cpu_cores), Math.round(number(values.ws_memory_gib) * 1024), number(values.ws_storage_gib), machine);
  }
  const context = () => ({ values, errors, options, machines: machines(), editing: mode === "edit", row: find(selected), busy, formError, previewText: options ? previewText() : "" });

  function topHTML() {
    const create = canManage && rows !== null ? `<button type="button" class="btn primary" data-ws-action="create"${busy || mode || !options || full() ? " disabled" : ""}>New cloud workspace</button>` : "";
    const cap = canManage && full() ? `<p class="compute-quiet">${e(GENERAL.limit_reached(options))}</p>` : "";
    return `<header><h2 id="compute-workspaces" tabindex="-1">Cloud workspaces</h2>${create}</header>${cap}`
      + '<p class="compute-plain">A cloud workspace is an isolated environment on one of your machines where Coworkers run. You choose its image (the software it starts from), CPU, memory and storage. For now this screen only saves those settings; nothing starts yet.</p>';
  }
  function inner() {
    if (rows === null && !loadError) return '<p class="compute-empty" role="status">Loading cloud workspaces…</p>';
    if (rows === null) return `<p class="compute-error" role="alert">${e(loadError)}</p><div class="compute-actions"><button type="button" class="btn" data-ws-action="reload">Reload</button></div>`;
    let panel = "";
    const row = find(selected);
    if ((mode === "create" && options) || (mode === "edit" && row && options)) panel = step === "review" ? reviewHTML(context()) : formHTML(context());
    else if (mode === "discard" && row) panel = discardHTML(context());
    else if (mode === "detail" && row) panel = detailHTML(row, { manage: canManage, busy });
    // The row a panel is about is replaced by the panel, so a workspace never appears twice.
    const shown = rows.filter(item => !(mode && mode !== "create" && item.id === selected));
    const more = total > rows.length ? `<p class="compute-quiet">Showing the newest ${rows.length} of ${total} cloud workspaces.</p>` : "";
    const list = more + (shown.length ? `<ul class="compute-list compute-ws-list">${shown.map(item => rowHTML(item, { manage: canManage, busy })).join("")}</ul>`
      : rows.length || mode ? "" : `<p class="compute-empty">No cloud workspaces yet. ${canManage ? "Choose New cloud workspace to create one." : "The workspace owner can create one here."}</p>`);
    const unavailable = configError ? `<p class="compute-error" role="alert">${e(configError)}</p><div class="compute-actions"><button type="button" class="btn" data-ws-action="reload">Reload</button></div>` : "";
    return `${unavailable}${panel}${list}`;
  }
  function html() {
    return `<section class="compute-inventory compute-wide compute-ws" aria-labelledby="compute-workspaces"><div data-ws-top>${topHTML()}</div>`
      + `<p class="compute-notice compute-ws-notice" data-ws-notice role="status" tabindex="-1">${e(notice)}</p><div data-ws-root>${inner()}</div></section>`;
  }

  // Repaint only this section; the persistent status line outside it keeps its live region.
  function paint(focus = "") {
    const host = ctx.host(), root = host?.querySelector?.("[data-ws-root]");
    if (root) { const top = host.querySelector("[data-ws-top]"); if (top) top.innerHTML = topHTML(); root.innerHTML = inner(); } else ctx.rerender();
    if (!focus) return;
    const scope = ctx.host();
    const target = focus === "heading" ? scope?.querySelector?.("#compute-ws-heading") : focus === "summary" ? scope?.querySelector?.("[data-ws-summary]") : scope?.querySelector?.(`[data-ws-control="${focus}"]`);
    target?.focus?.();
  }
  function say(text, focus = false) {
    notice = text;
    const node = ctx.host()?.querySelector?.("[data-ws-notice]");
    if (node) { node.textContent = text; if (focus) node.focus?.(); }
  }
  // A background repaint of the whole screen must not take focus (or a half-typed caret) away from this section.
  function holdFocus() {
    const control = ctx.host()?.querySelector?.(".compute-ws :focus");
    if (typeof control?.matches !== "function") return () => {};
    const row = control.closest?.("[data-ws-id]");
    const selector = control.matches("[data-ws-control]") ? `[data-ws-control="${control.dataset.wsControl}"]` : control.matches("[data-ws-notice]") ? "[data-ws-notice]" : control.matches("[data-ws-summary]") ? "[data-ws-summary]"
      : control.id === "compute-ws-heading" ? "#compute-ws-heading" : control.dataset?.wsAction ? `${row ? `[data-ws-id="${row.dataset.wsId}"] ` : ""}[data-ws-action="${control.dataset.wsAction}"]` : "";
    const caret = typeof control.selectionStart === "number" ? [control.selectionStart, control.selectionEnd] : null;
    return () => {
      const again = selector ? ctx.host()?.querySelector?.(selector) : null;
      if (!again) return;
      again.focus?.();
      if (caret) try { again.setSelectionRange?.(...caret); } catch { /* number inputs have no caret */ }
    };
  }
  function restoreFocus(back) {
    const host = ctx.host(), row = back?.id ? host?.querySelector?.(`[data-ws-id="${back.id}"]`) : null;
    const target = row?.querySelector?.(`[data-ws-action="${back.action}"]`) || row?.querySelector?.("[data-ws-action]") || host?.querySelector?.('[data-ws-action="create"]:not([disabled])') || host?.querySelector?.("#compute-workspaces");
    target?.focus?.();
  }
  // Typing only refreshes the preview and its own error, so the field keeps its focus and caret.
  function live(key) {
    const host = ctx.host();
    const preview = host?.querySelector?.("[data-ws-preview] p");
    if (preview) preview.textContent = previewText();
    for (const name of key ? [key] : Object.keys(errors)) {
      const slot = host?.querySelector?.(`[data-ws-error="${name}"]`);
      if (slot) slot.textContent = errors[name] || "";
      const control = host?.querySelector?.(`[data-ws-control="${name}"]`);
      if (control) { if (errors[name]) control.setAttribute?.("aria-invalid", "true"); else control.removeAttribute?.("aria-invalid"); }
    }
  }

  async function load({ announce = false } = {}) {
    const page = ctx.epoch();
    if (loading) return;
    loading = true; loadError = "";
    const [list, config] = await Promise.allSettled([ctx.request(BASE), ctx.request(`${BASE}/images`)]);
    loading = false;
    if (!ctx.valid(page)) return;
    for (const result of [list, config]) if (result.status === "rejected" && result.reason?.status === 401 && ctx.denied(result.reason)) return;
    if (list.status === "rejected" || !Array.isArray(list.value?.workspaces)) { rows = null; loadError = LOAD_ERROR; }
    else { rows = list.value.workspaces; total = Number.isInteger(list.value.total) ? list.value.total : rows.length; canManage = list.value.can_manage === true; }
    configError = "";
    if (config.status === "fulfilled" && config.value?.images && config.value?.limits) options = config.value;
    else { options = null; if (rows !== null && canManage) configError = "The image list could not be loaded, so new workspaces cannot be created right now. Reload to try again."; }
    if (mode && mode !== "create" && !find(selected)) { mode = ""; step = "edit"; selected = null; notice = "That cloud workspace is no longer available."; }
    paint();
    if (announce) say(rows === null ? LOAD_ERROR : configError || "Cloud workspaces reloaded.", true);
  }

  function open(kind, id) {
    if (busy) return;
    const row = id ? find(id) : null;
    if ((kind === "create" && (!canManage || !options || full())) || (kind === "edit" && (!canManage || !row || !options)) || (kind === "discard" && (!canManage || !row?.discardable)) || (kind === "detail" && !row)) return;
    if (mode !== "detail" || kind === "detail") opener = { id: id || "", action: kind };
    mode = kind; step = "edit"; selected = id || null; errors = {}; formError = ""; say("");
    values = kind === "create" ? blankValues(options) : kind === "edit" ? blankValues(options, row) : {};
    baseValues = kind === "edit" ? { ...values } : null;
    base = row ? { revision: row.revision, updated_at: row.updated_at } : null;
    requestId = kind === "create" ? ctx.requestId() : "";
    paint("heading");
  }
  function close() {
    if (busy || !mode) return;
    const back = opener;
    mode = ""; step = "edit"; selected = null; errors = {}; formError = ""; values = {}; baseValues = null; base = null; requestId = ""; opener = null;
    paint(); restoreFocus(back);
  }

  function fail(failure) {
    const payload = failure?.payload || {};
    if (!failure?.status) { formError = NO_ANSWER; return "summary"; }
    const key = SERVER_FIELD[payload.field], reason = payload.reason;
    const text = payload.field ? reasonText(payload.field === "image_source" ? "image" : payload.field, reason, options || {}) : "";
    if (key && text && mode !== "discard") { errors = { [key]: text }; step = "edit"; formError = "Fix the highlighted field, then review again."; return key; }
    formError = (GENERAL[reason]?.(options || {})) || ERROR_CODES[payload.error] || (payload.error === "invalid_request" ? "The server refused these values. Check the fields and try again." : "Saving failed. Try again.");
    if (mode !== "discard") step = "edit";
    return "summary";
  }

  async function save() {
    if (busy) return;
    const page = ctx.epoch(), id = selected, kind = mode, row = find(id);
    let path, body;
    if (kind === "discard") { path = `${BASE}/${enc(id)}/discard`; body = { expected_revision: row.revision }; }
    else if (kind === "edit") {
      // The list may have been reloaded while this form was open: never overwrite a newer saved state.
      if (!row || row.revision !== base.revision || row.updated_at !== base.updated_at) { formError = STALE; step = "edit"; paint("summary"); return; }
      body = editPayload(values, baseValues, base.revision); path = `${BASE}/${enc(id)}`;
      if (!body) { formError = GENERAL.no_changes(); step = "edit"; paint("summary"); return; }
    } else { path = BASE; body = createPayload(values, requestId); }
    busy = true; formError = ""; paint();
    try {
      const result = await ctx.request(path, { method: "POST", body });
      if (!ctx.valid(page)) return;
      let text;
      if (kind === "discard") { rows = rows.filter(item => item.id !== id); total = Math.max(0, total - 1); text = `Discarded ${row.name}. Nothing on a machine was affected.`; }
      else {
        const saved = result.workspace;
        if (!rows.some(item => item.id === saved.id)) total += 1;
        rows = [saved, ...rows.filter(item => item.id !== saved.id)].sort((a, b) => b.created_at - a.created_at);
        const machine = machineOf(saved, true);
        text = kind === "create" ? `Saved ${saved.name} in Commonplace. Nothing has been sent to ${machine || "a machine"} yet.`
          : `Saved ${saved.name} in Commonplace.${saved.restart_needed ? " Restart needed: the running copy keeps its old settings until it is restarted." : saved.started ? "" : " It has not started, so the change applies when it first starts."}`;
      }
      mode = ""; step = "edit"; selected = null; values = {}; baseValues = null; base = null; errors = {}; requestId = ""; busy = false; notice = text;
      paint(); say(text, true);
    } catch (failure) {
      if (!ctx.valid(page) || (failure?.status === 401 && ctx.denied(failure))) return;
      const focus = fail(failure); busy = false; paint(focus);
    }
  }

  function click(event) {
    const target = event.target?.closest?.("[data-ws-action]");
    if (!target) return false;
    const { wsAction: action, id } = target.dataset;
    if (target.disabled) return true;
    if (action === "reload") { rows = null; loadError = ""; paint(); void load({ announce: true }); }
    else if (["create", "edit", "detail", "discard"].includes(action)) open(action, id);
    else if (action === "close") close();
    else if (action === "back") { step = "edit"; formError = ""; paint("heading"); }
    else if (action === "confirm") void save();
    return true;
  }
  function keydown(event) {
    if (event.key !== "Escape" || !mode || !event.target?.closest?.(".compute-ws")) return false;
    if (busy) return true;
    event.preventDefault?.(); close();
    return true;
  }
  function input(event) {
    const control = event.target;
    if (!control?.name?.startsWith?.("ws_") || !mode) return false;
    const { name } = control;
    if (name === "ws_image_choice") {
      if (control.value === "custom") values.ws_image_source = "custom";
      else { values.ws_image_source = "curated"; values.ws_curated = control.value; }
      delete errors.image; paint(values.ws_image_source === "custom" ? "image_choice" : "image"); return true;
    }
    values[name] = control.value;
    const key = FIELD_OF_INPUT[name];
    if (key) {
      // Show a problem as soon as a field has one; clear it the moment it is fixed.
      const found = validate(values, options, key);
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
    errors = validate(values, options);
    const first = Object.keys(errors)[0];
    if (first) { formError = "Fix the highlighted fields, then review again."; paint(CONTROL_KEY[first]); return true; }
    if (mode === "edit" && !editPayload(values, baseValues, base.revision)) { formError = GENERAL.no_changes(); paint("summary"); return true; }
    formError = ""; step = "review"; paint("heading");
    return true;
  }
  function reset() { rows = null; total = 0; options = null; canManage = false; loadError = ""; configError = ""; loading = false; mode = ""; step = "edit"; values = {}; baseValues = null; base = null; errors = {}; selected = null; busy = false; formError = ""; notice = ""; requestId = ""; opener = null; }
  return { html, holdFocus, load, click, input, submit, keydown, reset, get open() { return mode !== ""; }, get loaded() { return rows !== null; }, get state() { return { rows, mode, step, values, errors, canManage, notice, formError, requestId }; } };
}
