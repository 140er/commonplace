import { escape as e } from "./api.js";

// Cloud workspaces on the Compute screen (docs/system/compute-workspaces.md). One shared module serves the
// Classic screen and the Deck Management tab: createCompute (compute.js) mounts the section and forwards
// clicks, inputs, submits and keys. Nothing here starts a container: a record says "Saved, not started" until the
// lifecycle card lands, and no sentence promises that anything runs or is enforced. A state only a start can produce
// (applied, failed, changes waiting) is worded as what a machine recorded, never as something this page did.
// The server validates everything again and serves the ranges and presets; client checks only explain early.

const BASE = "/api/compute/workspaces";
const enc = encodeURIComponent;
const NETWORKS = { outbound: "Internet access allowed", none: "No network" };
const REGISTRY_NAMES = {
  "docker.io": "Docker Hub", "ghcr.io": "GitHub Container Registry", "quay.io": "Quay", "gcr.io": "Google Container Registry",
  "public.ecr.aws": "Amazon ECR Public Gallery", "mcr.microsoft.com": "Microsoft Artifact Registry", "registry.gitlab.com": "GitLab Container Registry",
};
/** "GitHub Container Registry (ghcr.io)": the place an image is hosted, in words, with its address. */
export const hostedOn = host => !host ? "unknown" : REGISTRY_NAMES[host] ? `${REGISTRY_NAMES[host]} (${host})` : String(host);

// One vocabulary: "Saved, not started" until a start exists, "Changes waiting" for settings saved after a start.
// NOT_STARTED_NOTE follows a status label; NOT_STARTED is the whole sentence for screens without one.
export const NOT_STARTED_NOTE = "Starting is not available yet, so this only keeps your settings.";
export const NOT_STARTED = `Saved, not started. ${NOT_STARTED_NOTE}`;
export const STARTED = "A start was recorded for this workspace. Changes to the image, limits or network are saved and apply at its next start; a new name applies at once.";
const LAST_START_FAILED = "The last recorded start failed.";
// Said when no machine exists at all: there is nothing to choose, and no instruction that cannot be followed.
// Kept word for word with overview.py (NO_MACHINES_SENTENCE): a test compares the two, and the overview owns the sentence.
export const NO_MACHINES_SENTENCE = "No machine is set up yet, so this stays on Choose later. Starting will need one; that is not available yet.";
// The one sentence that says why a private image does not work; every place that mentions it uses this.
export const PRIVATE_IMAGE = "Private images need a sign-in, and Commonplace can't store sign-ins yet, so only public images work for now.";
const FAILURES = {
  out_of_memory: memory => `It ran out of memory at ${memory}. Raise the memory limit with Change settings; the new limit applies at its next start.`,
  storage_full: () => "Its storage is full. Raise the storage limit with Change settings; the change applies at its next start.",
  image_pull_failed: () => "The machine could not download the image. Check the image address with Change settings.",
  image_not_found: () => "No such image was found. Check the image name and version with Change settings.",
  registry_login_failed: () => `The image's website refused the sign-in. ${PRIVATE_IMAGE} Choose a public image with Change settings.`,
  machine_too_small: () => "The machine is smaller than these limits. Lower them or choose a bigger machine with Change settings.",
  limit_not_enforceable: () => "The machine cannot enforce these limits, so it did not start. Nothing ran without limits.",
  machine_unreachable: () => "The machine could not be reached.",
  start_failed: () => "The machine reported that it did not start.",
};
const UNKNOWN_FAILURE = manage => `Failed for a reason this page does not recognise. ${manage ? "Check the machine, then reload this list." : "The workspace owner can check the machine."}`;
const PREVIEW_INVALID = "Fix the highlighted fields to see what this will use.";
const STALE = "Someone else changed this workspace. Nothing was saved.";
const STALE_DISCARD = "This workspace changed after you opened this, so nothing was discarded. Reload the list and check it first.";
const STARTED_DISCARD = "It started after you opened this, so nothing was discarded. Keep it, or reload the list to see its current state.";
const NO_ANSWER = "No answer from the server, so nothing is confirmed. Reload to check what was saved before trying again.";
const LOAD_ERROR = "Cloud workspaces could not be loaded. Reload to try again.";
const DISCARD_NOTE = "A started cloud workspace can't be discarded yet.";
const quoted = name => `“${name}”`;

const cores = count => `${count} CPU ${count === 1 ? "core" : "cores"}`;
export const gib = mib => `${Number((mib / 1024).toFixed(3))} GiB`;
export const resourceLine = (cpu, memoryMib, storage) => `${cores(cpu)}, ${gib(memoryMib)} memory, ${storage} GiB storage`;
/** One label for the machine everywhere: "Machine: <name>", or "Machine: not chosen yet" (members: "assigned (name hidden)"). */
export const machineLine = machine => machine ? `Machine: ${machine}` : "Machine: not chosen yet";
export const resourcePreview = (cpu, memoryMib, storage, machine, none = "Machine: not chosen yet") =>
  `${resourceLine(cpu, memoryMib, storage)}. ${machine ? machineLine(machine) : none}`;

export const REASONS = {
  name: { length: limit => `Give it a name of 1 to ${limit} characters.`, characters: () => "Use letters, numbers and normal spaces. No spaces at the start or end.", no_letter: () => "Use at least one letter or digit in the name.", name_taken: () => "That name is taken. Choose another, for example Build box 2." },
  image: {
    empty: () => "Choose an image, or enter your own address, for example ubuntu:24.04.",
    too_long: () => "An image address can be at most 255 characters.",
    characters: () => "This doesn't look like an image address. Remove spaces and symbols such as ; and !. Example: ubuntu:24.04 or ghcr.io/acme/tools:1.2.",
    scheme: () => "Leave out https:// . An image address is just the name, like ghcr.io/acme/tools:1.2.",
    registry: () => "The website name before the first / is not valid. Example: ghcr.io/acme/tools:1.2.",
    registry_private: () => "That website is on a local or private network. Use an image from a public website such as ghcr.io.",
    registry_ip: () => "Use the website's name, not an IP address. Example: ghcr.io/acme/tools:1.2.",
    user_info: () => "Remove the sign-in (the part before @). Images must be public, and a password must never be typed into an address. Example: ghcr.io/acme/tools:1.2.",
    registry_no_path: () => "Add the image name after the website name, like ghcr.io/acme/tools:1.2.",
    name: () => "The image name must be lowercase letters and digits separated by . _ or -.",
    tag: () => "The version after the colon is not valid. Example: ubuntu:24.04.",
    digest: () => "A digest looks like @sha256: followed by 64 lowercase hex characters.",
    not_curated: () => "Pick one of the listed images, or choose My own image.",
  },
  private_registry: { unavailable: () => `${PRIVATE_IMAGE} Choose a public image.`, choice: () => "Choose whether the image is public." },
  cpu_cores: { range: l => `Enter a number of cores from ${l.min} to ${l.max}.`, step: l => `Use steps of ${l.step}, for example 2 or 2.5.`, not_a_number: () => "Enter a number of cores, for example 2." },
  memory_mib: { range: l => `Enter an amount from ${l.min / 1024} to ${l.max / 1024} GiB.`, step: l => `Use steps of ${l.step / 1024} GiB.`, not_a_number: () => "Enter an amount of memory in GiB, for example 4." },
  storage_gib: { range: l => `Enter a whole number of GiB from ${l.min} to ${l.max}.`, step: () => "Storage is a whole number of GiB.", not_a_number: () => "Enter a whole number of GiB, for example 20." },
  network: { choice: () => "Choose a network setting." },
  node_id: { unknown_machine: () => "That machine is no longer available. Reload and choose another.", machine_locked: () => "A started workspace can't move to another machine yet.", choice: () => "Choose a machine from the list." },
};
export const reasonText = (field, reason, options) => {
  const spec = REASONS[field]?.[reason];
  if (!spec) return "";
  return spec(field === "name" ? options.name_limit : options.limits?.[field]);
};
// Refusals about the form as a whole, not one field: worded in the summary.
const GENERAL = {
  stale_revision: () => STALE,
  limit_reached: (options, every) => `You have reached the limit of ${options.max_workspaces} cloud workspaces. ${every ? `All ${options.max_workspaces} have started, so none can be discarded yet.` : "Discard one that was never started to make room."}`,
  already_started: () => STARTED_DISCARD,
  registry_logins_unavailable: () => PRIVATE_IMAGE,
  request_pending: () => "A request for this workspace is waiting to be handled, so its image, limits, network and machine can't change yet. A new name can still be saved.",
  required: () => "A required value is missing. Fill in every field marked *.",
  no_changes: () => "You haven't changed anything yet.",
  request_reused: () => "This form was already sent with different values. Close it and start again.",
  request_discarded: () => "A workspace made from this form was discarded, so this form can't create it again. Close it and start a new one.",
  identifier: () => "This cloud workspace is no longer available. Reload the list.",
};
const ERROR_CODES = { owner_required: "You can read this workspace's settings. Only its owner can change them.", not_found: "This cloud workspace is no longer available. Reload the list.", sign_in: "Sign in again to continue." };

const LABEL = "[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?";
const HOST = new RegExp(`^${LABEL}(?:\\.${LABEL})*(?::([0-9]{1,5}))?$`);
const COMPONENT = /^[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*$/;
const TAG = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/;
const DIGEST = /^sha256:[a-f0-9]{64}$/;
const SAFE = /^[A-Za-z0-9._:/@-]+$/;
const NUMERIC_LABEL = /^(?:0x[0-9a-f]+|[0-9]+)$/;
const PORT_TAG = /^[0-9]{1,5}$/;
// The last label of a dotted name that is plainly a website; "node.js" and "my.image" are repositories (as for the server).
const REGISTRY_SUFFIXES = new Set("com io org net dev cloud co eu uk us de edu gov aws".split(" "));
const LOCAL_SUFFIX = /\.(?:local|localhost|internal|lan|home|corp|intranet|private|home\.arpa|localdomain|svc|ts\.net|consul)$/;
const LOCAL_LABEL = /^(?:localhost|localdomain)[0-9]*$/;
const DOTTED_QUAD = /^(?:0|[1-9][0-9]{0,2})(?:\.(?:0|[1-9][0-9]{0,2})){3}$/;
// Not public: "this" network, private, shared, loopback, link-local, protocol and documentation blocks, benchmarking, multicast and reserved.
const NON_PUBLIC = [[0, 8], [10, 8], [[100, 64], 10], [127, 8], [[169, 254], 16], [[172, 16], 12], [[192, 0, 0], 24], [[192, 0, 2], 24], [[192, 168], 16], [[198, 18], 15], [[198, 51, 100], 24], [[203, 0, 113], 24], [224, 3]];
const quad = parts => parts.reduce((sum, part) => sum * 256 + part, 0) * 256 ** (4 - parts.length);
// "registry_private" for a local or private name or address, "registry_ip" for a public IPv4 address, otherwise "".
// Any other numeric notation (0x7f.0.0.1, 8.8.8) could hide a private address and counts as private.
function hostRefusal(host) {
  const name = host.split(":")[0];
  // The dot goes in front so the bare suffix ("home.arpa") is refused too.
  if (!name.includes(".") || LOCAL_SUFFIX.test("." + name) || name.split(".").some(label => LOCAL_LABEL.test(label))) return "registry_private";
  if (!NUMERIC_LABEL.test(name.split(".").pop())) return "";
  const octets = name.split(".").map(Number);
  if (!DOTTED_QUAD.test(name) || octets.some(octet => octet > 255)) return "registry_private";
  const address = quad(octets);
  return NON_PUBLIC.some(([base, bits]) => Math.floor(address / 2 ** (32 - bits)) === Math.floor(quad([].concat(base)) / 2 ** (32 - bits))) ? "registry_private" : "registry_ip";
}

// A lone dotted component that is a website name with the image name left out ("ghcr.io:443", "ghcr.io"), not a repository.
function looksLikeHost(name, tag) {
  if (!name.includes(".") || !HOST.test(name)) return false;
  if (tag !== null) return PORT_TAG.test(tag);
  return REGISTRY_SUFFIXES.has(name.split(".").pop()) || hostRefusal(name) !== "";
}

/** Same grammar as the server: returns { reason } when refused, or the parts of a valid reference. */
export function parseImage(value) {
  if (typeof value !== "string" || !value) return { reason: "empty" };
  if (value.length > 255) return { reason: "too_long" };
  if (value.includes("://")) return { reason: "scheme" };
  if (!SAFE.test(value) || /^[-/.]/.test(value)) return { reason: "characters" };
  const at = value.indexOf("@");
  let name = at < 0 ? value : value.slice(0, at);
  const digest = at < 0 ? null : value.slice(at + 1);
  if (digest !== null && (digest.includes("/") || digest.includes(".") || (digest !== "" && !digest.includes(":")))) return { reason: "user_info" };
  if (digest !== null && !DIGEST.test(digest)) return { reason: "digest" };
  if (name.includes("@")) return { reason: "characters" };
  let tag = null;
  const slash = name.lastIndexOf("/"), colon = name.lastIndexOf(":");
  if (colon > slash) { tag = name.slice(colon + 1); name = name.slice(0, colon); if (!TAG.test(tag)) return { reason: "tag" }; }
  let parts = name.split("/"), registry = "docker.io";
  if (parts.length === 1 && looksLikeHost(parts[0], tag)) return { reason: "registry_no_path" };
  if (parts.length > 1 && (/[.:]/.test(parts[0]) || parts[0] === "localhost")) {
    const match = HOST.exec(parts[0]);
    if (!match || (match[1] !== undefined && !(Number(match[1]) >= 1 && Number(match[1]) <= 65535))) return { reason: "registry" };
    const refusal = hostRefusal(parts[0]);
    if (refusal) return { reason: refusal };
    registry = parts[0]; parts = parts.slice(1);
  }
  if (!parts.length || parts.some(part => !COMPONENT.test(part))) return { reason: "name" };
  return { registry, tag, digest, pinned: digest !== null };
}

// Names: NFC, counted in code points, nothing hidden or direction-changing (control and format characters,
// Hangul and braille fillers, combining joiner, variation selectors, the object replacement character, any space but U+0020), a letter or digit.
const BAD_NAME = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}]|[\u115f\u1160\u17b4\u17b5\u2800\u3164\uffa0\u034f\u180b-\u180f\ufe00-\ufe0f\u{E0100}-\u{E01EF}\ufffc]|(?! )\p{Zs}/u;
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
/** Edit body: only the fields that differ from the values the form opened with, plus the version it opened at. */
export function editPayload(values, baseValues, version) {
  const now = workspacePayload(values), before = workspacePayload(baseValues), body = {};
  for (const key of Object.keys(now)) if (now[key] !== before[key]) body[key] = now[key];
  if ("image" in body || "image_source" in body) { body.image = now.image; body.image_source = now.image_source; }
  return Object.keys(body).length ? { ...body, expected_revision: version } : null;
}

const machineOf = (row, manage) => manage ? (row.machine_name || (row.node_id ? "the chosen machine" : "")) : (row.machine_assigned ? "assigned" : "");
// One label for the machine on every screen: "Machine: <name>" for owners, "Machine: assigned (name hidden)" for members.
const machineText = (row, manage) => manage ? machineLine(machineOf(row, manage)) : row.machine_assigned ? "Machine: assigned (name hidden)" : "Machine: not chosen yet";
const previewOf = row => resourceLine(row.cpu_cores, row.memory_mib, row.storage_gib);

/**
 * One status per record: a tone, a short label and a sentence that says what is true and what to do now.
 * `action` names a button the owner may use ("image" opens the form on the image).
 * "ready" is only ever written by the start/stop lifecycle, which does not exist yet, so nothing on this screen
 * says "running" or "started" until a record really holds one.
 */
export function statusView(row, { manage = true, hasMachines = true } = {}) {
  const memory = gib(row.memory_mib);
  // One status, one tone: "Saved, not started" is quiet whether or not a machine is chosen.
  if (row.image_unreadable) return { tone: "wait", label: "Image unreadable", detail: manage ? "The saved image address can't be read. Choose the image again with Change settings." : "The saved image address can't be read. The workspace owner can choose the image again.", action: manage ? "image" : "" };
  switch (row.status) {
    case "no_machine": return { tone: "quiet", label: "Saved, not started", detail: !manage ? `${NOT_STARTED_NOTE} The workspace owner can choose a machine.` : hasMachines ? `${NOT_STARTED_NOTE} Choose a machine for it with Change settings.` : NO_MACHINES_SENTENCE };
    case "needs_credential": return { tone: "wait", label: "Private image", detail: manage ? `${PRIVATE_IMAGE} Choose a public image.` : `${PRIVATE_IMAGE} The workspace owner can choose a public image.`, action: manage ? "image" : "" };
    case "pending": return { tone: "quiet", label: "Saved, not started", detail: NOT_STARTED_NOTE };
    case "restart_needed": return { tone: "wait", label: "Changes waiting", detail: "The saved settings are newer than the ones a machine applied. Starting is not available yet, so they are kept for its next start." };
    case "ready": return { tone: "ok", label: "Settings applied", detail: "A machine recorded that it applied the saved settings." };
    case "failed": { const reason = FAILURES[row.status_reason]; return { tone: "bad", label: "Failed", detail: reason ? `${LAST_START_FAILED} ${reason(memory)}` : UNKNOWN_FAILURE(manage), reason: true }; }
    default: return { tone: "wait", label: "Status unknown", detail: "Reload to see the current status." };
  }
}
export const imageNote = row => row.image_pinned ? "Locked to one exact version." : row.image_source === "curated" ? "Official image. The publisher can update its version over time." : "The publisher can update this version over time.";
const deleteNote = row => row.discardable ? "You can change these settings later, or discard this workspace because it was never started." : `You can change these settings later. ${DISCARD_NOTE}`;
export const editsOf = row => { const n = Math.max(0, (row.version || 1) - 1); return n === 0 ? "Not edited since it was created" : n === 1 ? "Edited once" : `Edited ${n} times`; };
const historyOf = row => {
  const notes = [];
  if (row.started) {
    // Failed means the record says so; a start with nothing applied yet is a start under way or recorded, not a failure.
    const failed = row.status === "failed";
    if (failed) notes.push(LAST_START_FAILED);
    if (row.restart_needed) notes.push("Changes are waiting for the next start.");
    else if (!failed) notes.push(row.applied_revision == null ? "A start was recorded; no machine has recorded the saved settings as applied yet." : "A machine recorded the saved settings as applied.");
  }
  return [`${editsOf(row)}.`, ...notes].join(" ");
};
const imageLine = row => row.image_unreadable ? "not readable" : row.image_source === "curated" && row.curated_label ? `${row.curated_label} (${row.image})` : row.image;

const aria = (label, name) => ` aria-label="${e(`${label} ${name}`)}"`;

export function rowHTML(row, { manage, busy, confirm = "", hasMachines = true, changed = false }) {
  // A refused discard means the record has started since: its status says so in the same render as the refusal.
  const status = changed ? { tone: "wait", label: "Started since you opened this" } : statusView(row, { manage, hasMachines }), dis = busy ? ' aria-disabled="true"' : "";
  const head = `<header><h3 class="compute-ws-name" tabindex="-1">${e(row.name)}</h3><span class="compute-state compute-ws-status" data-tone="${status.tone}">${e(status.label)}</span></header>`;
  // A confirmation (discard) lives inside the record it is about, so it is never far from its name.
  if (confirm) return `<li class="compute-row compute-ws-row" data-ws-id="${e(row.id)}" data-status="${e(row.status)}">${head}${confirm}</li>`;
  const discard = manage && row.discardable ? `<button type="button" class="btn danger compute-ws-apart" data-ws-action="discard" data-id="${e(row.id)}"${aria("Discard", row.name)}${dis}>Discard</button>` : "";
  const change = manage ? `<button type="button" class="btn" data-ws-action="edit" data-id="${e(row.id)}"${aria("Change settings for", row.name)}${dis}>Change settings</button>${status.action ? `<button type="button" class="btn" data-ws-action="edit" data-focus="${status.action}" data-id="${e(row.id)}"${aria("Change image of", row.name)}${dis}>Change image</button>` : ""}` : "";
  return `<li class="compute-row compute-ws-row" data-ws-id="${e(row.id)}" data-status="${e(row.status)}">${head}`
    + `<p>${e(previewOf(row))}</p><p class="compute-quiet compute-ws-machine">${e(machineText(row, manage))}</p><p class="compute-quiet compute-ws-image">Image: ${e(imageLine(row))}</p>${status.reason ? `<p class="compute-ws-reason">${e(status.detail)}</p>` : ""}`
    + `<div class="compute-actions"><button type="button" class="btn" data-ws-action="detail" data-id="${e(row.id)}"${aria("Details for", row.name)}${dis}>Details</button>${change}${discard}</div></li>`;
}

export function detailHTML(row, { manage, busy, hasMachines = true }) {
  const status = statusView(row, { manage, hasMachines }), dis = busy ? ' aria-disabled="true"' : "";
  const rows = [
    ["Resources", e(previewOf(row))], ["Image", `${e(imageLine(row))}<br>${e(imageNote(row))}`],
    ["Comes from", `${e(hostedOn(row.image_registry))}${row.private_registry ? " (a private image)" : ""}`], ["Network", e(NETWORKS[row.network] || row.network)], ["Machine", e(machineText(row, manage).replace(/^(?:Planned machine|Machine): /, ""))],
    ["History", e(historyOf(row))],
  ];
  const actions = manage
    ? `<div class="compute-actions"><button type="button" class="btn primary" data-ws-action="edit" data-id="${e(row.id)}"${aria("Change settings for", row.name)}${dis}>Change settings</button>${status.action ? `<button type="button" class="btn" data-ws-action="edit" data-focus="${status.action}" data-id="${e(row.id)}"${aria("Change image of", row.name)}${dis}>Change image</button>` : ""}${row.discardable ? `<button type="button" class="btn danger compute-ws-apart" data-ws-action="discard" data-id="${e(row.id)}"${aria("Discard", row.name)}${dis}>Discard</button>` : ""}</div><p class="compute-quiet">${e(deleteNote(row))}</p>`
    : '<p class="compute-quiet">You can read this workspace\'s settings. Only its owner can change them.</p>';
  return `<section class="compute-editor compute-ws-detail" aria-labelledby="compute-ws-heading"><header><h3 id="compute-ws-heading" tabindex="-1">${e(row.name)}</h3><button type="button" class="btn" data-ws-action="close"${aria("Close details of", row.name)}>Close</button></header>`
    + `<p class="compute-ws-statusline"><strong class="compute-ws-status" data-tone="${status.tone}">${e(status.label)}.</strong> ${e(status.detail)}</p>`
    + `<dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>${actions}</section>`;
}

const REQUIRED = ' <span class="compute-req" aria-hidden="true">*</span>';
const controlAttrs = (key, id, ids, errors, extra = "") => ` id="${id}" data-ws-control="${key}" aria-describedby="${[...ids, `ws-error-${key}`].join(" ")}"${errors[key] ? ' aria-invalid="true"' : ""}${extra}`;
// The label closes before the hint and the error, so the accessible name is only the label. A field's error slot
// is a polite live region. Errors are only set when a field is committed (change or blur) or the form is
// submitted, never per keystroke; one painted with the form is read through aria-describedby on focus.
const field = (key, id, label, control, { hint = "", need = "", extra = "" } = {}, errors, { wide = false, required = false, hidden = false } = {}) =>
  `<div class="compute-field${wide ? " compute-wide" : ""}" data-ws-field="${key}"${hidden ? " hidden" : ""}><label for="${id}">${e(label)}${required ? REQUIRED : ""}</label>${control}${hint ? `<small class="compute-quiet" id="ws-hint-${key}">${e(hint)}</small>` : ""}${extra}${need ? `<small class="compute-quiet" id="ws-need-${key}"><strong>What do I need?</strong> ${e(need)}</small>` : ""}<small class="compute-ws-error" id="ws-error-${key}" data-ws-error="${key}" aria-live="polite">${e(errors[key] || "")}</small></div>`;
const hintIds = (key, hint, need) => [hint ? `ws-hint-${key}` : "", need ? `ws-need-${key}` : ""].filter(Boolean);

const FIELD_LABELS = { name: "Name", image: "Image", cpu_cores: "CPU", memory_mib: "Memory", storage_gib: "Storage", network: "Network", node_id: "Machine" };
// The id of each field's control (the image address box when it is shown, otherwise the choice list).
const FIELD_IDS = { name: "ws-field-name", image: "ws-field-image", cpu_cores: "ws-field-cpu", memory_mib: "ws-field-memory", storage_gib: "ws-field-storage", network: "ws-field-network", node_id: "ws-field-machine" };
// The control a field's error belongs to: its own key, except that a ready-made image has no address box, so its
// error belongs to the choice list the person can see.
const controlKeyOf = (key, values) => key === "image" && values.ws_image_source !== "custom" ? "image_choice" : key;

/** The summary at the top of the form: a list of links to the fields in error, or one form-level sentence. */
export function summaryHTML({ errors, formError, values, extra = "" }) {
  const keys = Object.keys(errors);
  if (formError) return `<p>${e(formError)}</p>${extra}`;
  if (!keys.length) return "";
  return `<p>${keys.length === 1 ? "Fix this field, then review again:" : `Fix these ${keys.length} fields, then review again:`}</p><ul role="list">${keys.map(key => `<li><a href="#${key === "image" && values.ws_image_source !== "custom" ? "ws-field-image-choice" : FIELD_IDS[key] || `ws-field-${key}`}" data-ws-jump="${key}">${e(FIELD_LABELS[key] || key)}: ${e(errors[key])}</a></li>`).join("")}</ul>`;
}
const summary = (ctx, extra = "") => `<div class="compute-ws-summary" id="ws-summary" data-ws-summary tabindex="-1">${summaryHTML({ errors: ctx.errors || {}, formError: ctx.formError, values: ctx.values || {}, extra })}</div>`;

const dis = busy => busy ? ' aria-disabled="true"' : "";
const busyLabel = (busy, text, working) => busy ? working : text;

function actionsHTML(ctx, { primaryLabel, primaryAction, secondary, secondaryLabel, busyText }) {
  const { busy, stale, leaving } = ctx;
  if (leaving) return `<div class="compute-actions compute-ws-actions compute-ws-leave compute-ws-wrap" role="group" aria-label="Leave without saving?"><p class="compute-ws-leave-text"><strong>Leave without saving?</strong> What you typed will be lost.</p><button type="button" class="btn primary" data-ws-action="keep-editing">Keep editing</button><button type="button" class="btn danger" data-ws-action="leave">Leave and lose changes</button></div>`;
  // A stale form has a real disabled button and the one thing that unblocks it beside it.
  if (stale) return `<div class="compute-actions compute-ws-actions compute-ws-stale compute-ws-wrap"><button class="btn primary" type="submit" disabled>${primaryLabel}</button><button type="button" class="btn" data-ws-action="reload-edit">Reload to continue</button><button type="button" class="btn" data-ws-action="${secondary}">${secondaryLabel}</button></div>`;
  const first = primaryAction === "submit"
    ? `<button class="btn primary" type="submit"${dis(busy)}>${busyLabel(busy, primaryLabel, busyText)}</button>`
    : `<button type="button" class="btn primary" data-ws-action="${primaryAction}"${dis(busy)}>${busyLabel(busy, primaryLabel, busyText)}</button>`;
  return `<div class="compute-actions compute-ws-actions">${first}<button type="button" class="btn" data-ws-action="${secondary}"${dis(busy)}>${secondaryLabel}</button></div>`;
}

const IMAGE_HELP = "An image is the ready-made software a workspace starts from.";
const CUSTOM_HELP = "The address of the image, for example ubuntu:24.04. Not sure? Pick a ready-made image above.";
const FIND_IT = "Open your image's page on the website that publishes it (for example Docker Hub or GitHub) and copy the text shown after “docker pull”. A registry is a website that stores images. Only public images work for now: anyone must be able to download them without signing in.";
const NEED = {
  cpu_cores: "1 core is enough for editing and light scripts; 4 or more helps with builds and tests. Examples: 1 for a small web app, 4 for a large test suite.",
  memory_mib: "2 GiB suits editing and small projects; 8 GiB or more suits builds and big test runs. 1 GiB is about 1.07 GB.",
  storage_gib: "Storage is the disk space for its files. 10 GiB fits a small project; 50 GiB or more fits large repositories and caches. 1 GiB is about 1.07 GB.",
};
const imageLabel = (options, reference) => options.images.find(item => item.reference === reference)?.label || "";

export function presetsHTML(ctx) {
  const { options, values } = ctx, presets = options.presets;
  if (!Array.isArray(presets) || !presets.length) return "";
  const match = item => String(item.cpu_cores) === String(values.ws_cpu_cores) && String(item.memory_mib / 1024) === String(values.ws_memory_gib) && String(item.storage_gib) === String(values.ws_storage_gib);
  return `<div class="compute-field compute-wide" role="group" aria-labelledby="ws-presets-label"><span class="compute-label" id="ws-presets-label">Start from a size</span><div class="compute-presets">${presets.map(item => `<button type="button" class="btn compute-preset" data-ws-action="preset" data-preset="${e(item.id)}"${match(item) ? ' aria-current="true"' : ""}>${e(item.label)}<span class="compute-preset-size">${e(`${cores(item.cpu_cores)}, ${gib(item.memory_mib)}, ${item.storage_gib} GiB`)}</span></button>`).join("")}</div><small class="compute-quiet">Fills the three fields below; you can still change any of them.</small></div>`;
}

export function formHTML(ctx) {
  const { values, options, machines, editing, row, busy } = ctx;
  const custom = values.ws_image_source === "custom";
  // A ready-made image has no address box: its error is shown on, and read from, the choice list the person can see.
  const errors = custom || !ctx.errors.image ? ctx.errors : { ...ctx.errors, image_choice: ctx.errors.image, image: "" };
  const heading = editing ? `Change ${e(quoted(row.name))}` : "New cloud workspace";
  const L = options.limits;
  const curated = options.images.map(item => `<option value="${e(item.reference)}"${values.ws_curated === item.reference && !custom ? " selected" : ""}>${e(item.label)}</option>`).join("");
  const choice = `${curated}<option value="custom"${custom ? " selected" : ""}>My own image…</option>`;
  // With no machine at all the field says so (and offers nothing to pick); otherwise "Choose later" and the machines.
  const noMachines = !machines.length && !values.ws_node_id;
  const machineOptions = `<option value=""${values.ws_node_id ? "" : " selected"}>${noMachines ? "No machines set up yet" : "Choose later"}</option>` + machines.map(m => `<option value="${e(m.id)}"${values.ws_node_id === m.id ? " selected" : ""}>${e(m.name)}</option>`).join("");
  const locked = editing && row.started;
  const note = editing && row.started ? STARTED : NOT_STARTED;
  const select = (key, id, name, inner, ids, extra = "") => `<select name="${name}"${controlAttrs(key, id, ids, errors, extra)}>${inner}</select>`;
  const num = (key, id, name, spec, scale, mode, ids) => `<input type="number" name="${name}"${controlAttrs(key, id, ids, errors)} inputmode="${mode}" min="${spec.min / scale}" max="${spec.max / scale}" step="${spec.step / scale}" value="${e(values[name])}">`;
  const range = (spec, scale = 1) => `Default ${spec.default / scale}. From ${spec.min / scale} to ${spec.max / scale}.`;
  // The address box is always in the form and only hidden while a ready-made image is chosen, so changing the
  // choice shows or hides it in place (no repaint, no scroll jump).
  const fields = [
    field("name", "ws-field-name", "Name", `<input name="ws_name"${controlAttrs("name", "ws-field-name", ["ws-hint-name"], errors, " required")} value="${e(values.ws_name)}" autocomplete="off">`, { hint: "Shown in lists, so pick something your team will recognise, for example Build box." }, errors, { wide: true, required: true }),
    field("image_choice", "ws-field-image-choice", "Image", select("image_choice", "ws-field-image-choice", "ws_image_choice", choice, ["ws-hint-image_choice"], " required"), { hint: imageChoiceHint(options, values) }, errors, { wide: true, required: true }),
    field("image", "ws-field-image", "Image address", `<input name="ws_image"${controlAttrs("image", "ws-field-image", ["ws-hint-image"], errors, " required")} value="${e(values.ws_image)}" spellcheck="false" autocapitalize="none" autocomplete="off">`, { hint: CUSTOM_HELP, extra: `<details class="compute-ws-help"><summary>Where do I find it?</summary><p>${e(FIND_IT)}</p></details>` }, errors, { wide: true, required: true, hidden: !custom }),
    presetsHTML(ctx),
    field("cpu_cores", "ws-field-cpu", "CPU cores", num("cpu_cores", "ws-field-cpu", "ws_cpu_cores", L.cpu_cores, 1, "decimal", hintIds("cpu_cores", true, true)), { hint: `${range(L.cpu_cores)} A core is one processor's worth of work at a time.`, need: NEED.cpu_cores }, errors),
    field("memory_mib", "ws-field-memory", "Memory (GiB)", num("memory_mib", "ws-field-memory", "ws_memory_gib", L.memory_mib, 1024, "decimal", hintIds("memory_mib", true, true)), { hint: range(L.memory_mib, 1024), need: NEED.memory_mib }, errors),
    field("storage_gib", "ws-field-storage", "Storage (GiB)", num("storage_gib", "ws-field-storage", "ws_storage_gib", L.storage_gib, 1, "numeric", hintIds("storage_gib", true, true)), { hint: range(L.storage_gib), need: NEED.storage_gib }, errors),
    field("network", "ws-field-network", "Network", select("network", "ws-field-network", "ws_network", options.networks.map(n => `<option value="${e(n)}"${values.ws_network === n ? " selected" : ""}>${e(NETWORKS[n] || n)}</option>`).join(""), ["ws-hint-network"]), { hint: "Whether the workspace can reach the internet once it starts." }, errors),
    field("node_id", "ws-field-machine", "Machine", select("node_id", "ws-field-machine", "ws_node_id", machineOptions, ["ws-hint-node_id"], locked || noMachines ? " disabled" : ""), { hint: locked ? "A started workspace can't move to another machine yet." : noMachines ? "No machine is set up yet, so none can be chosen." : "The machine it is planned for. You can choose later. Whether it is big enough is only checked when starting is available." }, errors, { wide: true }),
  ].join("");
  return `<section class="compute-editor compute-ws-editor" aria-labelledby="compute-ws-heading"${busy ? ' aria-busy="true"' : ""}><header><h3 id="compute-ws-heading" tabindex="-1">${heading}</h3></header>`
    + `<p class="compute-quiet">${e(note)} Fields marked * are required; the others start with a default.</p>`
    + `<form data-ws-form="${editing ? "edit" : "create"}" novalidate>${summary(ctx, ctx.stale ? staleExtra() : "")}${reapplyHTML(ctx)}<fieldset><legend class="compute-ws-legend">${editing ? "Settings" : "New workspace settings"}</legend><div class="compute-fields">${fields}</div></fieldset>`
    + `<div class="compute-ws-preview" data-ws-preview><strong>What it will use</strong><p>${e(ctx.previewText)}</p></div>`
    + `${actionsHTML(ctx, { primaryLabel: editing ? "Review the change" : "Review and create", primaryAction: "submit", secondary: "close", secondaryLabel: "Cancel", busyText: "Saving…" })}</form></section>`;
}
const imageChoiceHint = (options, values) => values.ws_image_source === "custom" ? "Paste the address of your own image below." : `${IMAGE_HELP} ${options.images.find(item => item.reference === values.ws_curated)?.description || ""}`.trim();

// The form below the banner still holds what the person typed; it stays that way until it is reloaded.
const staleExtra = () => "<p>What you typed is still in the form below. Reload to continue loads the current version.</p>";
// After a reload: the earlier typed values wait here until the person chooses to apply them.
function reapplyHTML(ctx) {
  if (!ctx.reapply?.length) return "";
  return `<div class="compute-ws-reapply" data-ws-reapply><p>Your earlier changes were not saved:</p><ul role="list" class="compute-ws-typed">${ctx.reapply.map(line => `<li>${e(line)}</li>`).join("")}</ul><button type="button" class="btn" data-ws-action="reapply">Apply my changes again</button></div>`;
}

// Each changed field as a short line, from the strings the person typed (never the normalised payload).
const SHOW_RAW = { ws_name: ["Name", v => v], ws_cpu_cores: ["CPU cores", v => v], ws_memory_gib: ["Memory (GiB)", v => v], ws_storage_gib: ["Storage (GiB)", v => v] };
function typedLines(values, baseValues, options, machines) {
  const lines = [], text = value => String(value ?? "").trim() === "" ? "(empty)" : String(value);
  for (const [name, [label]] of Object.entries(SHOW_RAW)) if (values[name] !== baseValues[name]) lines.push(`${label}: ${text(values[name])}`);
  if (imageOf(values) !== imageOf(baseValues)) lines.push(`Image: ${text(values.ws_image_source === "curated" ? imageLabel(options, values.ws_curated) || values.ws_curated : values.ws_image)}`);
  if (values.ws_network !== baseValues.ws_network) lines.push(`Network: ${NETWORKS[values.ws_network] || values.ws_network}`);
  if (values.ws_node_id !== baseValues.ws_node_id) lines.push(`Machine: ${values.ws_node_id ? machines.find(m => m.id === values.ws_node_id)?.name || "the chosen machine" : "not chosen yet"}`);
  return lines;
}

const SHOW = machines => ({ memory_mib: v => gib(v), storage_gib: v => `${v} GiB`, cpu_cores: v => cores(v), network: v => NETWORKS[v] || v, node_id: v => v ? (machines.find(m => m.id === v)?.name || "the chosen machine") : "not chosen yet" });
function changeLines(row, values, machines) {
  const now = workspacePayload(values), before = { name: row.name, image: row.image, cpu_cores: row.cpu_cores, memory_mib: row.memory_mib, storage_gib: row.storage_gib, network: row.network, node_id: row.node_id || null }, show = SHOW(machines);
  return Object.keys(FIELD_LABELS).filter(key => now[key] !== before[key]).map(key => ({ label: FIELD_LABELS[key], before: (show[key] || String)(before[key]), after: (show[key] || String)(now[key]) }));
}

export function reviewHTML(ctx) {
  const { values, editing, row, previewText, machines, options } = ctx;
  const image = imageOf(values), pinned = parseImage(image).pinned, friendly = values.ws_image_source === "curated" ? imageLabel(options, image) : "";
  const lines = (editing ? changeLines(row, values, machines) : []).map(line => `<li>${e(line.label)}: ${e(line.before)} → ${e(line.after)}</li>`);
  lines.push(`<li>${e(previewText)}</li>`);
  // The machine records whether it was verified; an unverified one is still a valid choice, so say what that means.
  const chosen = values.ws_node_id ? machines.find(m => m.id === values.ws_node_id) : null;
  if (chosen && chosen.state !== "verified") lines.push(`<li class="compute-ws-warn">${e(`${chosen.name} is not verified yet, so nothing can start on it. Your settings are still saved.`)}</li>`);
  lines.push(`<li>Image: ${e(friendly ? `${friendly} (${image})` : image)} — ${pinned ? "locked to one exact version" : "the publisher can update its version over time"}</li>`, `<li>Network: ${e(NETWORKS[values.ws_network] || values.ws_network)}</li>`);
  const started = editing && row.started;
  const next = started ? `${STARTED} You can change these settings later. ${DISCARD_NOTE}` : `Confirming saves these settings in Commonplace only. Starting is not available yet, so nothing is sent to a machine. You can change them later.`;
  return `<section class="compute-editor compute-ws-editor" aria-labelledby="compute-ws-heading"${ctx.busy ? ' aria-busy="true"' : ""}><header><h3 id="compute-ws-heading" tabindex="-1">${editing ? `Confirm the change to ${e(quoted(values.ws_name))}` : `Create ${e(quoted(values.ws_name))}?`}</h3></header>${summary(ctx, ctx.stale ? staleExtra() : "")}`
    + `<ul class="compute-ws-review" role="list">${lines.join("")}</ul><p class="compute-quiet" data-ws-honesty>${e(next)}</p>`
    + actionsHTML(ctx, { primaryLabel: editing ? "Confirm and save" : "Confirm and create", primaryAction: "confirm", secondary: "back", secondaryLabel: "Back", busyText: "Saving…" }) + "</section>";
}

/** The discard confirmation, rendered inside the record's own row. Fixed labels, the name sits in the sentence. */
export function discardHTML(ctx) {
  const { row, busy, refused, stale } = ctx;
  const extra = stale && !refused ? '<div class="compute-actions"><button type="button" class="btn primary" data-ws-action="reload">Reload the list</button></div>' : "";
  const body = refused ? `<p id="ws-discard-reason">${e(STARTED_DISCARD)}</p>`
    : `<p>This removes the saved settings for ${e(quoted(row.name))} from Commonplace. It was never started, so nothing on a machine is affected. You can't undo this, but you can create it again with the same settings.</p>`;
  const buttons = refused
    ? `<button type="button" class="btn primary" data-ws-action="close" aria-describedby="ws-discard-reason">Keep it</button><button type="button" class="btn" data-ws-action="reload">Reload the list</button>`
    : `<button type="button" class="btn danger" data-ws-action="confirm"${stale ? ' disabled aria-describedby="ws-summary"' : dis(busy)}>${busy ? "Discarding…" : "Discard workspace"}</button><button type="button" class="btn primary" data-ws-action="close"${dis(busy)}>Keep it</button>`;
  return `<section class="compute-editor compute-ws-editor compute-ws-discard" aria-labelledby="compute-ws-heading"${busy ? ' aria-busy="true"' : ""}><h4 id="compute-ws-heading" tabindex="-1">Discard ${e(quoted(row.name))}?</h4>${summary(ctx, extra)}${body}`
    + `<div class="compute-actions compute-ws-actions">${buttons}</div></section>`;
}

const SERVER_FIELD = { name: "name", image: "image", image_source: "image", cpu_cores: "cpu_cores", memory_mib: "memory_mib", storage_gib: "storage_gib", network: "network", node_id: "node_id", private_registry: "image" };
const FIELD_OF_INPUT = { ws_name: "name", ws_image: "image", ws_cpu_cores: "cpu_cores", ws_memory_gib: "memory_mib", ws_storage_gib: "storage_gib", ws_network: "network" };

/**
 * ctx: { request, epoch, valid(page), denied(failure), rerender(), host(), machines(), requestId() }
 * Owns its own state; compute.js only embeds html() and forwards events.
 */
export function createWorkspaces(ctx) {
  let rows = null, total = 0, options = null, canManage = false, loadError = "", configError = "", loading = false;
  let mode = "", step = "edit", values = {}, startValues = null, baseValues = null, base = null, errors = {}, selected = null, busy = false, formError = "", notice = "", requestId = "", opener = null;
  let stale = false, leaving = false, refused = false, reapply = null, pressed = false, unbind = null, shownTop = "";
  const held = new Set();
  const find = id => rows?.find(row => row.id === id);
  const machines = () => (ctx.machines() || []).map(node => ({ id: node.id, name: node.name, state: node.state }));
  const allStarted = () => rows?.length > 0 && rows.every(item => item.started);
  const full = () => options && rows !== null && Math.max(total, rows.length) >= options.max_workspaces;
  function previewText() {
    if (RESOURCE_KEYS.some(key => validate(values, options, key)[key])) return PREVIEW_INVALID;
    const machine = machines().find(m => m.id === values.ws_node_id)?.name || (values.ws_node_id ? "the chosen machine" : "");
    return resourcePreview(number(values.ws_cpu_cores), Math.round(number(values.ws_memory_gib) * 1024), number(values.ws_storage_gib), machine);
  }
  const dirty = () => startValues !== null && JSON.stringify(values) !== JSON.stringify(startValues);
  const context = () => ({ values, errors, options, machines: machines(), editing: mode === "edit", row: find(selected), busy, stale, leaving, refused, formError, reapply: reapply?.lines || null, previewText: options ? previewText() : "" });

  function topHTML() {
    const create = canManage && rows !== null && !mode && options && !full() ? `<button type="button" class="btn primary" data-ws-action="create"${busy ? ' aria-disabled="true"' : ""}>New cloud workspace</button>` : "";
    const cap = canManage && full() ? `<p class="compute-quiet" id="ws-create-reason">${e(GENERAL.limit_reached(options, allStarted()))}</p>` : "";
    return `<header><h2 id="compute-workspaces" tabindex="-1">Your cloud workspaces</h2>${create}</header>${cap}`
      + '<p class="compute-plain">A cloud workspace is meant to be a separate place on one of your machines where Coworkers can run. You choose its image (the software it starts from), CPU, memory and storage. For now this screen only saves those settings; nothing starts yet.</p>';
  }
  function inner() {
    if (rows === null && !loadError) return '<p class="compute-empty" role="status">Loading cloud workspaces…</p>';
    if (rows === null) return '<div class="compute-actions"><button type="button" class="btn" data-ws-action="reload">Reload</button></div>';
    let panel = "";
    const row = find(selected);
    if ((mode === "create" && options) || (mode === "edit" && row && options)) panel = step === "review" ? reviewHTML(context()) : formHTML(context());
    else if (mode === "detail" && row) panel = detailHTML(row, { manage: canManage, busy, hasMachines: machines().length > 0 });
    // The row a panel is about is replaced by the panel, so a workspace never appears twice.
    // A discard confirmation is the exception: it renders inside its own row, in place.
    const shown = rows.filter(item => !(mode && mode !== "create" && mode !== "discard" && item.id === selected));
    const more = total > rows.length ? `<p class="compute-quiet">Showing the newest ${rows.length} of ${total} cloud workspaces.</p>` : "";
    const list = more + (shown.length ? `<ul class="compute-list compute-ws-list" role="list">${shown.map(item => { const mine = mode === "discard" && item.id === selected; return rowHTML(item, { manage: canManage, busy, hasMachines: machines().length > 0, changed: mine && refused, confirm: mine ? discardHTML(context()) : "" }); }).join("")}</ul>`
      : rows.length || mode ? "" : `<p class="compute-empty">Nothing here yet. ${canManage ? "Choose New cloud workspace to create one. A machine can be chosen later." : "The workspace owner can create one here."}</p>`);
    const unavailable = configError ? '<div class="compute-actions"><button type="button" class="btn" data-ws-action="reload">Reload</button></div>' : "";
    return `${unavailable}${panel}${list}`;
  }
  function html() {
    shownTop = topHTML();
    return `<section class="compute-inventory compute-wide compute-ws" aria-labelledby="compute-workspaces"><div data-ws-top>${shownTop}</div>`
      + `<p class="compute-notice compute-ws-notice" data-ws-notice role="status">${e(notice)}</p>`
      + `<p class="compute-error compute-ws-problem" data-ws-problem role="alert">${e(problem())}</p><div data-ws-root>${inner()}</div></section>`;
  }
  // A failed load is one static alert node that is written only when its text changes, so a repaint never
  // re-announces it (and the status line says nothing about it: one voice, not two).
  const problem = () => rows === null ? loadError : configError;
  function writeProblem() {
    const node = ctx.host()?.querySelector?.("[data-ws-problem]"), text = problem();
    if (node && node.textContent !== text) node.textContent = text;
  }

  // Bring a field (its label, control, hint and error) fully into view above the sticky action bar, then focus it.
  function reveal(target) {
    if (!target) return;
    const wrapper = target.closest?.("[data-ws-field]") || target;
    try { wrapper.scrollIntoView?.({ block: "center", behavior: "instant" }); } catch { /* old engines take no options */ }
    target.focus?.({ preventScroll: true });
  }
  // Repaint only this section; the persistent status line outside it keeps its live region.
  // A repaint nobody asked focus for (a background reload) puts focus back where it was, in the top part too.
  function paint(focus = "") {
    const restore = focus ? null : holdFocus();
    const host = ctx.host(), root = host?.querySelector?.("[data-ws-root]");
    if (root) {
      const top = host.querySelector("[data-ws-top]"), next = topHTML();
      if (top && next !== shownTop) { top.innerHTML = next; shownTop = next; }
      root.innerHTML = inner();
      writeProblem();
    } else ctx.rerender();
    restore?.();
    if (!focus) return;
    const scope = ctx.host();
    // A step starts at its heading, at the top of the screen: the sticky action bar of a phone form then sits at the form's
    // end of the screen, not on top of its first fields (the heading used to take focus where it already was visible).
    if (focus === "heading") {
      const heading = scope?.querySelector?.("#compute-ws-heading");
      try { heading?.scrollIntoView?.({ block: "start", behavior: "instant" }); } catch { /* old engines take no options */ }
      heading?.focus?.({ preventScroll: true });
    }
    else if (focus === "summary") scope?.querySelector?.("[data-ws-summary]")?.focus?.();
    else if (focus === "keep") scope?.querySelector?.('.compute-ws-actions [data-ws-action="close"]')?.focus?.();
    else if (focus === "keep-editing") scope?.querySelector?.('[data-ws-action="keep-editing"]')?.focus?.();
    else reveal(scope?.querySelector?.(`[data-ws-control="${focus}"]`));
  }
  // The status line is a live region and never takes focus (it is read once; focus goes to a real control).
  function say(text) {
    notice = text;
    const node = ctx.host()?.querySelector?.("[data-ws-notice]");
    if (node && node.textContent !== text) node.textContent = text;
  }
  // After a save: the saved row's heading (or the way to make another when it is gone).
  function settle(id) {
    const host = ctx.host();
    const target = (id && host?.querySelector?.(`[data-ws-id="${id}"] .compute-ws-name`)) || host?.querySelector?.('[data-ws-action="create"]') || host?.querySelector?.("#compute-workspaces");
    target?.focus?.();
  }
  // A background repaint of the whole screen must not take focus (or a half-typed caret) away from this section.
  function holdFocus() {
    const control = ctx.host()?.querySelector?.(".compute-ws :focus");
    if (typeof control?.matches !== "function") return () => {};
    const row = control.closest?.("[data-ws-id]");
    const selector = control.matches("[data-ws-control]") ? `[data-ws-control="${control.dataset.wsControl}"]` : control.matches("[data-ws-notice]") ? "[data-ws-notice]" : control.matches("[data-ws-summary]") ? "[data-ws-summary]"
      : control.id === "compute-ws-heading" ? "#compute-ws-heading" : control.dataset?.wsAction ? `${row ? `[data-ws-id="${row.dataset.wsId}"] ` : ""}[data-ws-action="${control.dataset.wsAction}"]${control.dataset.focus ? `[data-focus="${control.dataset.focus}"]` : ""}${control.dataset.preset ? `[data-preset="${control.dataset.preset}"]` : ""}` : "";
    const caret = typeof control.selectionStart === "number" ? [control.selectionStart, control.selectionEnd] : null;
    return () => {
      const again = selector ? ctx.host()?.querySelector?.(selector) : null;
      if (!again) return;
      again.focus?.({ preventScroll: true });
      if (caret) try { again.setSelectionRange?.(...caret); } catch { /* number inputs have no caret */ }
    };
  }
  function restoreFocus(back) {
    const host = ctx.host(), row = back?.id ? host?.querySelector?.(`[data-ws-id="${back.id}"]`) : null;
    const target = row?.querySelector?.(`[data-ws-action="${back.action}"]`) || row?.querySelector?.("[data-ws-action]") || host?.querySelector?.('[data-ws-action="create"]') || host?.querySelector?.("#compute-workspaces");
    target?.focus?.();
  }
  // Typing only refreshes the preview, its own error and the summary, so the field keeps its focus and caret.
  function live(key) {
    const host = ctx.host();
    // A live region is only written when its text changes, so a keystroke that changes nothing there announces nothing.
    const preview = host?.querySelector?.("[data-ws-preview] p"), want = previewText();
    if (preview && preview.textContent !== want) preview.textContent = want;
    for (const name of key ? [key] : Object.keys(errors)) {
      const target = controlKeyOf(name, values);
      const slot = host?.querySelector?.(`[data-ws-error="${target}"]`);
      if (slot && slot.textContent !== (errors[name] || "")) slot.textContent = errors[name] || "";
      // The control that carries this field's error: the address box for a custom image, otherwise the choice list.
      const control = host?.querySelector?.(`[data-ws-control="${target}"]`);
      if (control) { if (errors[name]) control.setAttribute?.("aria-invalid", "true"); else control.removeAttribute?.("aria-invalid"); }
    }
    // A summary that is already showing follows the fields and goes away once every one is valid. Committing a
    // field never opens one: that would push the form down under a click that is already on its way.
    const box = host?.querySelector?.("[data-ws-summary]");
    if (box && !stale && !formError && box.textContent?.trim()) { const next = summaryHTML({ errors, formError: "", values }); if (box.innerHTML !== next) box.innerHTML = next; }
  }
  // Show saving without replacing the button that has focus: its label and state change in place, and the
  // section says it is busy. (Without a mounted section the whole screen repaints with the same markup.)
  function setBusy() {
    busy = true;
    const panel = ctx.host()?.querySelector?.("[data-ws-root]")?.querySelector?.(".compute-ws-editor");
    if (!panel?.setAttribute) { paint(); return; }
    panel.setAttribute("aria-busy", "true");
    for (const button of panel.querySelectorAll?.(".compute-ws-actions button") || []) {
      button.setAttribute("aria-disabled", "true");
      if (button.dataset?.wsAction === "confirm") button.textContent = mode === "discard" ? "Discarding…" : "Saving…";
    }
  }

  async function load({ announce = false } = {}) {
    const page = ctx.epoch();
    if (loading) return;
    track();
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
    if (mode && mode !== "create" && !find(selected)) { mode = ""; step = "edit"; selected = null; stale = false; leaving = false; notice = "That cloud workspace is no longer available."; }
    // The list moved on while a form was open: say so now, not only when the person tries to save.
    else if ((mode === "edit" || mode === "discard") && base && find(selected)?.version !== base.version) { stale = true; leaving = false; step = "edit"; refused = false; formError = mode === "discard" ? STALE_DISCARD : STALE; }
    paint();
    // Said once, through the status line (no focus is taken from whatever the person was doing).
    if (stale) { say("This workspace changed while you had it open. Nothing was saved or discarded; the message in the form says what to do."); ctx.host()?.querySelector?.("[data-ws-summary]")?.scrollIntoView?.({ block: "center" }); }
    else if (announce) {
      // A failed reload is spoken by the alert node alone; only a good one is said here.
      if (rows !== null && !configError) say("Cloud workspaces reloaded.");
      // A failed reload keeps the person on its Reload button; a good one on the section heading.
      ctx.host()?.querySelector?.(rows === null || configError ? '[data-ws-action="reload"]' : "#compute-workspaces")?.focus?.();
    }
  }

  function open(kind, id, focus = "heading") {
    if (busy) return;
    const row = id ? find(id) : null;
    if ((kind === "create" && (!canManage || !options || full())) || (kind === "edit" && (!canManage || !row || !options)) || (kind === "discard" && (!canManage || !row?.discardable)) || (kind === "detail" && !row)) return;
    if (mode !== "detail" || kind === "detail") opener = { id: id || "", action: kind };
    mode = kind; step = "edit"; selected = id || null; errors = {}; formError = ""; stale = false; leaving = false; refused = false; reapply = null; say("");
    values = kind === "create" ? blankValues(options) : kind === "edit" ? blankValues(options, row) : {};
    startValues = kind === "create" || kind === "edit" ? { ...values } : null;
    baseValues = kind === "edit" ? { ...values } : null;
    base = row ? { version: row.version, updated_at: row.updated_at } : null;
    requestId = kind === "create" ? ctx.requestId() : "";
    paint(kind === "discard" ? "keep" : kind === "edit" && focus === "image" ? "image" : "heading");
  }
  function close() {
    if (busy || !mode) return;
    const back = opener;
    mode = ""; step = "edit"; selected = null; errors = {}; formError = ""; values = {}; startValues = null; baseValues = null; base = null; requestId = ""; opener = null; stale = false; leaving = false; refused = false; reapply = null;
    paint(); restoreFocus(back);
  }
  // Cancel and Escape both ask first when typed values would be lost.
  function leave() {
    if (busy || !mode) return;
    if ((mode === "create" || mode === "edit") && step === "edit" && dirty() && !stale) { leaving = true; paint("keep-editing"); return; }
    close();
  }

  function fail(failure) {
    const payload = failure?.payload || {};
    if (!failure?.status) { formError = NO_ANSWER; return "summary"; }
    const key = SERVER_FIELD[payload.field], reason = payload.reason;
    const text = payload.field ? reasonText(payload.field === "image_source" ? "image" : payload.field, reason, options || {}) : "";
    // It started after the confirmation opened: the body is replaced by the refusal and Discard goes away.
    if (reason === "already_started" && mode === "discard") { refused = true; stale = false; formError = ""; errors = {}; return "keep"; }
    if (reason === "stale_revision") { stale = true; step = "edit"; formError = mode === "discard" ? STALE_DISCARD : STALE; return "summary"; }
    if (key && text && mode !== "discard") { errors = { [key]: text }; step = "edit"; formError = ""; return "summary"; }
    errors = {};
    formError = (GENERAL[reason]?.(options || {}, allStarted())) || ERROR_CODES[payload.error] || (payload.error === "invalid_request" ? "The server refused these values. Check the fields and try again." : "Saving failed. Try again.");
    if (mode !== "discard") step = "edit";
    return "summary";
  }

  async function save() {
    if (busy) return;
    const page = ctx.epoch(), id = selected, kind = mode, row = find(id);
    let path, body;
    if (kind === "discard") { path = `${BASE}/${enc(id)}/discard`; body = { expected_revision: row.version }; }
    else if (kind === "edit") {
      // The list may have been reloaded while this form was open: never overwrite a newer saved state.
      if (!row || row.version !== base.version || row.updated_at !== base.updated_at) { stale = true; formError = STALE; step = "edit"; paint("summary"); return; }
      body = editPayload(values, baseValues, base.version); path = `${BASE}/${enc(id)}`;
      if (!body) { formError = GENERAL.no_changes(); step = "edit"; paint("summary"); return; }
    } else { path = BASE; body = createPayload(values, requestId); }
    formError = ""; say(kind === "discard" ? "Discarding…" : "Saving…"); setBusy();
    try {
      const result = await ctx.request(path, { method: "POST", body });
      if (!ctx.valid(page)) return;
      let text;
      if (kind === "discard") { rows = rows.filter(item => item.id !== id); total = Math.max(0, total - 1); text = `Discarded ${quoted(row.name)}. Nothing on a machine was affected.`; }
      else {
        const saved = result.workspace;
        if (!rows.some(item => item.id === saved.id)) total += 1;
        rows = [saved, ...rows.filter(item => item.id !== saved.id)].sort((a, b) => b.created_at - a.created_at);
        const who = quoted(saved.name);
        text = kind === "create" ? `Saved ${who}, not started. Starting is not available yet.`
          : `Saved ${who}${saved.restart_needed ? ". Changes waiting: they apply the next time it starts, and starting is not available yet." : saved.started ? "." : ", not started."}`;
      }
      mode = ""; step = "edit"; selected = null; values = {}; startValues = null; baseValues = null; base = null; errors = {}; requestId = ""; busy = false; stale = false; leaving = false; refused = false; reapply = null; notice = text;
      paint(); say(text); settle(kind === "discard" ? "" : result.workspace.id);
    } catch (failure) {
      if (!ctx.valid(page) || (failure?.status === 401 && ctx.denied(failure))) return;
      busy = false; say("");
      const focus = fail(failure); paint(focus);
    }
  }

  // Preset sizes fill the three resource fields in place, so the focused button keeps focus.
  function applyPreset(id) {
    const item = options?.presets?.find(preset => preset.id === id);
    if (!item || !mode || step !== "edit") return;
    Object.assign(values, { ws_cpu_cores: String(item.cpu_cores), ws_memory_gib: String(item.memory_mib / 1024), ws_storage_gib: String(item.storage_gib) });
    for (const key of RESOURCE_KEYS) delete errors[key];
    const host = ctx.host();
    for (const key of RESOURCE_KEYS) {
      const slot = host?.querySelector?.(`[data-ws-error="${key}"]`);
      if (slot && slot.textContent) slot.textContent = "";
      host?.querySelector?.(`[data-ws-control="${key}"]`)?.removeAttribute?.("aria-invalid");
    }
    for (const [name, value] of [["cpu_cores", values.ws_cpu_cores], ["memory_mib", values.ws_memory_gib], ["storage_gib", values.ws_storage_gib]]) {
      const control = host?.querySelector?.(`[data-ws-control="${name}"]`);
      if (control) control.value = value;
    }
    markPresets();
    live("");
  }
  // The size that matches the three fields is marked as the current one (aria-current), not "pressed": a size is a start, not a switch.
  function markPresets() {
    for (const button of ctx.host()?.querySelectorAll?.("[data-preset]") || []) {
      const item = options.presets?.find(preset => preset.id === button.dataset.preset);
      const on = item && String(item.cpu_cores) === String(values.ws_cpu_cores) && String(item.memory_mib / 1024) === String(values.ws_memory_gib) && String(item.storage_gib) === String(values.ws_storage_gib);
      if (on) button.setAttribute?.("aria-current", "true"); else button.removeAttribute?.("aria-current");
    }
  }
  function jump(key) {
    // A ready-made image has no address box: its error belongs to the choice list.
    const target = key === "image" && values.ws_image_source !== "custom" ? "image_choice" : key;
    reveal(ctx.host()?.querySelector?.(`[data-ws-control="${target}"]`));
  }
  // Changing the image choice shows or hides the address box in place: no repaint, so nothing scrolls.
  function syncImageChoice() {
    const host = ctx.host();
    if (!host?.querySelector?.("[data-ws-root]")) { ctx.rerender(); return; }
    const wrap = host.querySelector('[data-ws-field="image"]');
    if (values.ws_image_source === "custom") wrap?.removeAttribute?.("hidden"); else wrap?.setAttribute?.("hidden", "");
    const hint = host.querySelector("#ws-hint-image_choice");
    if (hint) hint.textContent = imageChoiceHint(options, values);
    for (const name of ["image", "image_choice"]) {
      const slot = host.querySelector(`[data-ws-error="${name}"]`);
      if (slot && slot.textContent) slot.textContent = "";
      host.querySelector(`[data-ws-control="${name}"]`)?.removeAttribute?.("aria-invalid");
    }
    live("");
  }

  function click(event) {
    const link = event.target?.closest?.("[data-ws-jump]");
    if (link) { event.preventDefault?.(); jump(link.dataset.wsJump); return true; }
    const target = event.target?.closest?.("[data-ws-action]");
    if (!target) return false;
    const { wsAction: action, id } = target.dataset;
    if (target.disabled || target.getAttribute?.("aria-disabled") === "true") return true;
    if (action === "reload") { rows = null; loadError = ""; stale = false; paint(); void load({ announce: true }); }
    else if (action === "reload-edit") void startOver();
    else if (action === "reapply") applyAgain();
    else if (["create", "edit", "detail", "discard"].includes(action)) open(action, id, target.dataset.focus);
    else if (action === "close") leave();
    else if (action === "keep-editing") { leaving = false; paint("heading"); }
    else if (action === "leave") { leaving = false; close(); }
    else if (action === "preset") applyPreset(target.dataset.preset);
    else if (action === "back") { step = "edit"; formError = ""; paint("heading"); }
    else if (action === "confirm") void save();
    return true;
  }
  // From a changed record: load the current one and open a fresh form on it. What the person typed is kept
  // (as typed) and waits behind "Apply my changes again".
  async function startOver() {
    const id = selected, kind = mode, mine = kind === "edit" && baseValues ? { values: { ...values }, base: { ...baseValues } } : null;
    stale = false; formError = ""; say("Reloading…");
    await load();
    mode = ""; selected = null;
    const row = find(id);
    if (kind === "edit" && row && canManage && options) {
      open("edit", id);
      const lines = mine ? typedLines(mine.values, mine.base, options, machines()) : [];
      reapply = lines.length ? { ...mine, lines } : null;
      paint("heading");
      say(lines.length ? "Reloaded. Your earlier changes were not saved. Apply my changes again puts them back." : "Reloaded.");
    } else { paint(); restoreFocus(opener); say(row ? "Reloaded." : "That cloud workspace is no longer available."); }
  }
  // Put back only the fields the person had changed; the rest stay on the current saved values.
  function applyAgain() {
    if (!reapply || !mode) return;
    for (const name of Object.keys(values)) if (reapply.values[name] !== reapply.base[name]) values[name] = reapply.values[name];
    reapply = null; errors = {}; formError = "";
    paint("heading"); say("Your changes are back in the form. Review them before saving.");
  }
  function keydown(event) {
    if (event.key !== "Escape" || !mode || !event.target?.closest?.(".compute-ws")) return false;
    if (busy) return true;
    event.preventDefault?.();
    // One step back at a time: out of the review into the form, out of the form (asking first if it was changed).
    if (leaving) { leaving = false; paint("heading"); }
    else if (step === "review" && (mode === "create" || mode === "edit")) { step = "edit"; formError = ""; paint("heading"); }
    else leave();
    return true;
  }
  // Typing updates the values and the preview only. A field's own problem is shown when it is committed (change or
  // blur) or on submit, never per keystroke; fixing a shown problem clears it quietly.
  function input(event) {
    const control = event.target;
    if (!control?.name?.startsWith?.("ws_") || !mode) return false;
    const { name } = control, committed = event.type === "change";
    if (name === "ws_image_choice") {
      if (control.value === "custom") values.ws_image_source = "custom";
      else { values.ws_image_source = "curated"; values.ws_curated = control.value; }
      delete errors.image; syncImageChoice(); return true;
    }
    values[name] = control.value;
    const key = FIELD_OF_INPUT[name];
    if (key) {
      const found = validate(values, options, key);
      if (committed) { if (pressed) held.add(key); else commit(key); }
      else if (errors[key] && !found[key]) { delete errors[key]; live(key); }
      else live("");
    } else live("");
    markPresets();
    return true;
  }
  // Showing a problem makes the field's message appear and pushes the form down. A pointer that is still pressed
  // (the person is on their way to a button) would then release on something else, so the click would be lost:
  // committing is held until the pointer is released, after the click it belongs to has been handled.
  function commit(key) {
    const found = validate(values, options, key);
    if (found[key]) errors[key] = found[key]; else delete errors[key];
    live(key);
  }
  function track() {
    const doc = ctx.doc;
    if (unbind || !doc?.addEventListener) return;
    const down = () => { pressed = true; };
    const up = () => { pressed = false; setTimeout(() => { for (const key of held) if (mode) commit(key); held.clear(); }, 0); };
    doc.addEventListener("pointerdown", down, true); doc.addEventListener("pointerup", up, true); doc.addEventListener("pointercancel", up, true);
    unbind = () => { doc.removeEventListener("pointerdown", down, true); doc.removeEventListener("pointerup", up, true); doc.removeEventListener("pointercancel", up, true); unbind = null; pressed = false; held.clear(); };
  }
  function submit(event) {
    const form = event.target?.closest?.("[data-ws-form]");
    if (!form?.dataset?.wsForm) return false;
    event.preventDefault?.(); event.stopPropagation?.();
    if (busy || !mode || stale) return true;
    errors = validate(values, options);
    // The summary takes focus: it lists every problem, and each line jumps to its field.
    if (Object.keys(errors).length) { formError = ""; paint("summary"); return true; }
    if (mode === "edit" && !editPayload(values, baseValues, base.version)) { formError = GENERAL.no_changes(); paint("summary"); return true; }
    formError = ""; step = "review"; paint("heading");
    return true;
  }
  function reset() { unbind?.(); rows = null; total = 0; options = null; canManage = false; loadError = ""; configError = ""; loading = false; mode = ""; step = "edit"; values = {}; startValues = null; baseValues = null; base = null; errors = {}; selected = null; busy = false; formError = ""; notice = ""; requestId = ""; opener = null; stale = false; leaving = false; refused = false; reapply = null; }
  return { html, holdFocus, load, click, input, submit, keydown, reset, get open() { return mode !== ""; }, get loaded() { return rows !== null; }, get state() { return { rows, mode, step, values, errors, canManage, notice, formError, requestId, stale, leaving, busy }; } };
}
