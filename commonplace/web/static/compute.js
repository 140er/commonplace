import { escape as e } from "./api.js";
import { createWorkspaces } from "./compute-workspaces.js";

const BASE = "/api/compute";
const intro = "The machines agents can reach, what they may look at, and tasks waiting for your approval.";
const parts = (...values) => values.filter(value => value !== undefined && value !== null && String(value).trim()).join(" · ");
const endpoint = row => row.address ? [row.address, row.port].filter(value => value !== undefined && value !== null && value !== "").join(":") : "";
const enc = encodeURIComponent;
const algorithms = ["ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "ssh-rsa"];
const limits = { hosts: 4096, concurrency: 64, rate_per_second: 100, duration_seconds: 300, results: 4096 };
// Technical settings sit behind one "Advanced" label; each section opens with a one-line plain explanation.
export const ADVANCED = {
  label: "Advanced",
  intro: "Most people never need these. They are the technical settings behind how agents reach your machines.",
  gateways: "A gateway is the door into one project’s network; agents reach your machines through it.",
  nodes: "A target is one machine behind a gateway that agents may work on.",
  scopes: "A passive scope is a time-limited permission to look at a network, never to change it.",
};
// The one documented way to create a credential (docs/compute/RUNNING.md, "Provision a credential"); the form
// only displays it, filling in values that match a strict safe pattern. No key is ever typed or shown here.
const PROVISION = `uv run python - <<'PY'
from pathlib import Path
from commonplace.compute.credentials import (
    CredentialBinding,
    CredentialStore,
    initialize_wrapping_key,
)

root = Path("WORKSPACE_DATA_DIR")
database, key = root / "compute-credentials.sqlite3", Path("WRAPPING_KEY_FILE")
NODE_ID = ""
if database.exists():
    store = CredentialStore(database, key, root, lambda *_: True)
else:
    if not key.exists():
        initialize_wrapping_key(key, root, database_path=database)
    store = CredentialStore.initialize(database, key, root, lambda *_: True)
binding = CredentialBinding(
    "WORKSPACE_ID", "PERSON_ID", "GATEWAY_ID", NODE_ID, "ACCOUNT", "PURPOSE", VERSION
)
made = store.generate(binding, object())
store.close()
print(made.credential_id, made.public_key)
PY`;
const safeName = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/, safeAccount = /^[A-Za-z_][A-Za-z0-9_.-]{0,63}$/;
export function provisionCommand({ workspace, purpose, account, version, gateway, node } = {}) {
  let text = PROVISION;
  const fill = (token, value, ok) => { if (ok) text = text.replace(token, () => value); };
  const target = purpose === "target", number = String(version ?? "").trim();
  fill('"WORKSPACE_ID"', `"${workspace}"`, safeName.test(String(workspace ?? "")));
  fill('"GATEWAY_ID"', `"${gateway}"`, safeName.test(String(gateway ?? "")));
  fill('"ACCOUNT"', `"${account}"`, safeAccount.test(String(account ?? "")));
  fill('"PURPOSE"', `"${target ? "target" : "gateway"}"`, purpose === "gateway" || target);
  fill("VERSION\n", `${number}\n`, /^[1-9]\d{0,9}$/.test(number) && Number(number) <= 2 ** 31 - 1);
  fill('NODE_ID = ""', 'NODE_ID = "TARGET_NODE_ID"', target);
  fill("TARGET_NODE_ID", String(node), target && safeName.test(String(node ?? "")));
  return text;
}
const credentialStates = { pending: "Created, not switched on yet", active: "Active", revoked: "Revoked", expired: "Expired" };
const credentialLabel = row => `${row.id} · version ${row.version} · account ${row.account} · ${credentialStates[row.state] || row.state}`;
const same = (a, b) => String(a) === String(b);
// A record saved without a credential carries null; anything that is neither a reference nor null is unknown, never blank.
const credentialState = row => typeof row?.credential_id === "string" && row.credential_id ? "bound" : row?.credential_id === null ? "unbound" : "unknown";
const credentialNote = row => ({ bound: '<p class="compute-credential-state" data-credential="bound">Credential bound</p>', unbound: '<p class="compute-credential-state" data-credential="unbound"><strong>No credential bound yet.</strong> Not usable for runs or verification until a credential is bound.</p>', unknown: '<p class="compute-credential-state" data-credential="unknown">Credential state unavailable. Reload the inventory.</p>' }[credentialState(row)] || "");
const credentialLine = row => ({ bound: `${e(row.credential_id)} · version ${e(row.credential_version)}`, unbound: "No credential bound yet", unknown: "Credential state unavailable" }[credentialState(row)]);
const bindButton = (row, kind, disabled) => credentialState(row) === "unbound" ? button(`bind-${kind}`, "Bind a credential", disabled, row.id) : "";
const digestOK = value => /^[a-f0-9]{64}$/.test(String(value));
const discoveryCancellable = row => row && ["queued", "claimed", "dispatching", "uncertain"].includes(row.state) && row.reason !== "cancellation_requested";
const predispatch = row => !row.run && ["awaiting_approval", "approved"].includes(row.state);
const cancellable = row => predispatch(row) || row.run && row.run.phase !== "terminal" && row.run.cancel_requested_at == null;
const executionStatus = row => {
  const run = row.run;
  if (!run) return stateText(row.state);
  if (run.phase === "terminal") return run.reason === "cancelled_before_start" ? "Cancelled before start" : run.exit_code != null ? `Exit observed · code ${run.exit_code}` : "Execution did not start";
  if (run.cancel_requested_at != null) return "Cancellation requested · termination unconfirmed";
  return { admitted: "Execution admitted", dispatching: "Dispatch requested", running: "Execution running", uncertain: "Outcome unknown · capacity reserved" }[run.phase] || "Execution state unavailable";
};
const button = (action, label, disabled = false, id = "") => `<button type="button" class="btn" data-compute-action="${action}" data-id="${e(id)}"${disabled ? " disabled" : ""}>${label}</button>`;
const states = { pending_verification: "Pending verification", awaiting_approval: "Awaiting approval", approved: "Approved · automatic execution authorized", cancelled: "Cancelled before dispatch" };
const stateText = value => states[value] || value || "Unknown state";
const errorText = error => ({ sign_in: "Sign in again to use Compute.", owner_required: "Compute management requires the current workspace owner.", not_found: "This record is unavailable. Reload the inventory.", conflict: "The record changed or the request conflicts. Reload and check its current state.", invalid_request: "The server refused these values. Check the fields and bounds.", unsupported_operation: "This operation is unavailable in the current workspace.", transport_unavailable: "Transport is unavailable. No dispatch has been confirmed.", credential_not_bound: "No credential is bound to this target or its gateway yet. Bind one from its row under Gateways or Targets, then try again.", credential_already_bound: "This record already has a credential. Reload the inventory to see it." }[error?.payload?.error] || "The request could not be confirmed. Reload the inventory before repeating an action.");
const required = (value, label, max = 200) => {
  if (typeof value !== "string" || !value.trim() || value.length > max || /[\u0000-\u001f\u007f]/.test(value)) throw new Error(`Enter a valid ${label} (up to ${max} characters).`);
  return value.trim();
};
const integer = (value, label, max = Number.MAX_SAFE_INTEGER) => {
  if (!/^[1-9]\d*$/.test(String(value)) || !Number.isSafeInteger(Number(value)) || Number(value) > max) throw new Error(`${label} must be a whole number from 1 to ${max}.`);
  return Number(value);
};
const list = (value, label) => {
  const rows = String(value || "").trim().split(/[\s,]+/).filter(Boolean);
  if (!rows.length || rows.length > 16 || new Set(rows).size !== rows.length) throw new Error(`Enter 1–16 distinct ${label}.`);
  return rows;
};
const utf8 = value => {
  if (typeof value !== "string" || value.includes("\0") || Array.from(value).some(char => /^[\uD800-\uDFFF]$/.test(char))) throw new Error("Use valid UTF-8 text without NUL bytes.");
  return new TextEncoder().encode(value).length;
};

// These checks shape the finite form payload. The server remains authoritative
// for address, canonical network, current ownership and task/connection validation.
export function computePayload(kind, values, { now = Date.now() / 1000, requestId } = {}) {
  if (!Number.isFinite(now)) throw new Error("The current time is unavailable.");
  const expiry = max => now + integer(values.expires_minutes, "Expiry in minutes", max) * 60;
  if (kind === "script") {
    if (!utf8(values.source) || utf8(values.source) > 32768) throw new Error("Python source must contain 1–32768 UTF-8 bytes.");
    const args = JSON.parse(values.arguments || "[]");
    if (!Array.isArray(args) || args.length > 16 || args.some(value => utf8(value) > 256)) throw new Error("Use at most 16 string arguments of 256 UTF-8 bytes each.");
    const paths = String(values.output_paths || "").split(/[\n,]/).map(value => value.trim()).filter(Boolean);
    if (paths.length > 8 || new Set(paths).size !== paths.length || paths.some(path => path.length > 192 || !/^[A-Za-z0-9_][A-Za-z0-9_.-]*(?:\/[A-Za-z0-9_][A-Za-z0-9_.-]*){0,3}$/.test(path) || path.split("/").some(part => part.length > 64) || ["script.py", ".compute"].includes(path.split("/")[0])) || paths.some(a => paths.some(b => a !== b && a.startsWith(b + "/")))) throw new Error("Use up to eight distinct, non-overlapping relative output paths.");
    const payload = { node_id: required(values.node_id, "target"), task_number: integer(values.task_number, "Task number"), request_id: required(requestId, "request ID"), source: values.source, arguments: args, output_paths: paths };
    if (utf8(JSON.stringify(payload)) > 65536) throw new Error("The complete script request must fit within 64 KiB.");
    return payload;
  }
  if (kind === "script-proposal") return { node_id: required(values.node_id, "target"), task_number: integer(values.task_number, "Task number"), request_id: required(requestId, "request ID"), operation: "workspace.script.v1", arguments: { bundle_id: required(values.bundle_id, "script bundle") } };
  if (["gateway", "node"].includes(kind)) {
    if (values.pin_confirmed !== true && values.pin_confirmed !== "on") throw new Error("Compare and explicitly confirm the full public host key first.");
    const algorithm = required(values.host_key_algorithm, "public host key algorithm");
    const key = required(values.host_key_key, "base64 public host key", 5464);
    if (!algorithms.includes(algorithm) || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(key) || key.length < 24 || key.length / 4 * 3 - (key.endsWith("==") ? 2 : key.endsWith("=") ? 1 : 0) > 4096) throw new Error("Enter the full base64 public SSH host key and its supported algorithm.");
    const address = required(values.address, "numeric address", 64);
    if (!/^[0-9a-fA-F:.]+$/.test(address) || (!address.includes(":") && !/^(?:\d{1,3}\.){3}\d{1,3}$/.test(address))) throw new Error("Use a numeric IP address. Hostnames are not supported.");
    const payload = {
      name: required(values.name, "name"), address, port: integer(values.port, "SSH port", 65535),
      account: required(values.account, "SSH account", 64), host_key: { algorithm, key },
      ...(String(values.credential_id ?? "").trim() || String(values.credential_version ?? "").trim() ? { credential_id: required(values.credential_id, "credential reference"), credential_version: integer(values.credential_version, "Credential version") } : {}), pin_confirmed: true,
    };
    return kind === "gateway" ? { board: required(values.board, "project slug"), ...payload } : { gateway_id: required(values.gateway_id, "gateway"), ...payload, interface: required(values.interface, "interface", 64) };
  }
  if (kind === "bind") return { credential_id: required(values.credential_id, "credential reference"), credential_version: integer(values.credential_version, "Credential version") };
  if (kind === "scope") {
    if (values.scope_confirmed !== true && values.scope_confirmed !== "on") throw new Error("Explicitly confirm this bounded passive scope.");
    if (values.methods && values.methods !== "passive") throw new Error("Only passive scope grants are available.");
    const cidrs = list(values.cidrs, "canonical private CIDRs"), interfaces = list(values.interfaces, "interfaces");
    if (cidrs.some(cidr => !/^[0-9a-fA-F:.]+\/\d{1,3}$/.test(cidr)) || interfaces.some(item => !/^[A-Za-z0-9_.:-]{1,64}$/.test(item))) throw new Error("Use canonical private CIDRs and exact interface names.");
    const bounds = Object.fromEntries(Object.entries(limits).map(([name, max]) => [name, integer(values[name], name.replaceAll("_", " "), max)]));
    if (bounds.concurrency > bounds.hosts) throw new Error("Concurrency cannot exceed the host limit.");
    return { gateway_id: required(values.gateway_id, "gateway"), task_number: integer(values.task_number, "Task number"), ...(values.agent_id?.trim() ? { agent_id: required(values.agent_id, "connection ID") } : {}), cidrs, interfaces, methods: ["passive"], expires_at: expiry(60), limits: bounds };
  }
  if (kind === "discovery") {
    const sources = list(values.sources, "cache sources");
    if (sources.some(source => !["linux_arp", "ip_neigh"].includes(source))) throw new Error("Choose supported passive cache sources.");
    return { scope_id: required(values.scope_id, "scope"), sources, request_id: required(requestId, "request ID") };
  }
  if (kind === "proposal") {
    if (values.operation && values.operation !== "system.info.v1") throw new Error("Only system.info.v1 is available.");
    return { node_id: required(values.node_id, "target"), task_number: integer(values.task_number, "Task number"), operation: "system.info.v1", arguments: {}, request_id: required(requestId, "request ID") };
  }
  if (kind === "approve") {
    if (!digestOK(values.expected_digest)) throw new Error("Enter the complete, exact SHA256 digest shown for this proposal.");
    return { expected_digest: values.expected_digest, expires_at: expiry(5) };
  }
  throw new Error("Unsupported Compute form.");
}

const taskHTML = row => /^[1-9]\d*$/.test(String(row.task_number)) ? `<a href="#/card/${enc(row.task_number)}">Task #${e(row.task_number)}</a>` : "";
const limitsHTML = value => Object.entries(value || {}).map(([key, val]) => `<dt>${e(key.replaceAll("_", " "))}</dt><dd>${e(val)}</dd>`).join("");
const bindingHTML = row => row ? `<dl><dt>Address</dt><dd>${e(row.address)}:${e(row.port)}</dd><dt>Account</dt><dd>${e(row.account)}</dd><dt>Epoch</dt><dd>${e(row.epoch)}</dd><dt>Credential reference</dt><dd>${credentialLine(row)}</dd></dl><p>Full public host key</p><pre>${e(row.host_key?.algorithm)} ${e(row.host_key?.key)}</pre>` : "<p>Binding unavailable. Reload before approving.</p>";

export function computeExecutionHTML(row, { busy = false } = {}) {
  const run = row.run;
  const evidence = run ? `<section aria-label="Execution evidence"><p>${e(run.reason)}</p>${run.result?.output ? `<pre>${e(run.result.output)}</pre>` : ""}${run.capacity_reserved ? '<p>Capacity remains reserved until the execution outcome is reconciled.</p>' : ""}</section>` : "";
  return `<li class="compute-row" data-state="${e(run?.phase || row.state)}"><header><strong>${e(row.operation)}</strong><span class="compute-state">${e(executionStatus(row))}</span></header><p>${parts(e(row.board), taskHTML(row), row.node_id ? `Target ${e(row.node_id)}` : "")}</p><p class="compute-digest">SHA256 <code>${e(row.digest)}</code></p>${row.reason ? `<p>${e(row.reason)}</p>` : ""}<details><summary>Proposal details</summary><dl><dt>Proposal</dt><dd>${e(row.id)}</dd><dt>Gateway</dt><dd>${e(row.gateway_id)}</dd><dt>Arguments</dt><dd><code>${e(JSON.stringify(row.arguments || {}))}</code></dd>${limitsHTML(row.limits)}<dt>Created</dt><dd>${e(row.created_at)}</dd><dt>Updated</dt><dd>${e(row.updated_at)}</dd></dl></details><div class="compute-actions">${!run && row.state === "awaiting_approval" ? button("approve", "Inspect approval", busy || !digestOK(row.digest), row.id) : ""}${cancellable(row) ? button("cancel", run ? "Request cancellation" : "Cancel proposal", busy, row.id) : ""}</div>${evidence}</li>`;
}

// One visit owns its DOM, session object and all requests. Even transports that
// ignore abort cannot publish after a different route/session has taken over.
export function createCompute({ mount, api, current, route = () => globalThis.location?.hash, expectedRoute = "#/compute", title = "", onBoards = () => {}, doc = globalThis.document, events = globalThis.window, now = () => Date.now() / 1000, requestId = () => globalThis.crypto.randomUUID(), copy = text => globalThis.navigator?.clipboard?.writeText(text) }) {
  let host = null, session = null, workspace = null, identity = null, live = false, epoch = 0, revision = 0;
  let inventory = null, projects = [], reading = false, busy = false, uncertain = false, error = "", notice = "", boardError = "";
  let discoveries = [], discoveryAvailable = false, discoveryError = "";
  let scripts = [], scriptDetail = null, scriptsError = "";
  let credentials = [], credentialStatus = "unknown", copyNote = "";
  let advancedOpen = false, memberView = false;
  let editor = "", values = {}, selected = null, approvalDigest = "", attempt = null, bindCollection = "gateways";
  const requests = new Set();
  const eligible = () => live && current() === session && current()?.workspace === workspace && current()?.actor === identity && route() === expectedRoute;
  function valid(page) {
    if (live && !eligible()) stop();
    return eligible() && epoch === page;
  }
  function abort() { for (const controller of requests) controller.abort(); requests.clear(); }
  async function request(path, options = {}) {
    const controller = new AbortController(); requests.add(controller);
    try { return await api(path, { ...options, signal: controller.signal, timeoutMs: 10000 }); }
    finally { requests.delete(controller); }
  }
  function denied(failure) {
    if (failure?.status === 401) { stop(); return true; }
    if ([403, 404].includes(failure?.status)) {
      // A member is refused the owner inventory but may still read cloud workspaces, so keep that section.
      memberView = failure.status === 403 && failure.payload?.error === "owner_required";
      if (!memberView) workspaces.reset();
      revision++; abort(); scripts = []; scriptDetail = null; inventory = null; projects = []; editor = ""; values = {}; selected = null; attempt = null; approvalDigest = "";
      credentials = []; credentialStatus = "unknown"; reading = false; busy = false; error = errorText(failure); notice = ""; render(); return true;
    }
    return false;
  }
  const workspaces = createWorkspaces({ request, epoch: () => epoch, valid, denied, rerender: () => render(), host: () => host, machines: () => inventory?.nodes || [] });
  const rowById = (name, id) => inventory?.[name]?.find(row => same(row.id, id));
  const executionAvailability = () => inventory?.capabilities?.transport === true
    ? "The automatic execution worker is available. Approval permits automatic background execution over SSH without another dispatch confirmation. Each attempt still requires valid authority, bindings and target verification."
    : "The automatic execution worker is currently unavailable. Approval still permits automatic background execution over SSH if the worker becomes available before approval expires; no additional dispatch confirmation is required.";
  const manageable = () => inventory?.can_manage === true && eligible();
  const option = (value, label, field) => `<option value="${e(value)}"${same(values[field], value) ? " selected" : ""}>${e(label)}</option>`;
  const field = (name, label, { type = "text", max = 200, min, ceiling, optional = false, help = "" } = {}) => `<label>${label}<input name="${name}" type="${type}" value="${e(values[name] ?? "")}"${optional ? "" : " required"} maxlength="${max}"${min ? ` min="${min}"` : ""}${ceiling ? ` max="${ceiling}"` : ""} ${type === "number" ? 'step="1"' : 'autocomplete="off"'}>${help ? `<small>${help}</small>` : ""}</label>`;
  const select = (name, label, rows) => `<label>${label}<select name="${name}" required><option value="">Choose…</option>${rows.map(([value, text]) => option(value, text, name)).join("")}</select></label>`;
  const area = (name, label, max, help) => `<label class="compute-wide">${label}<textarea name="${name}" required maxlength="${max}" rows="3" spellcheck="false">${e(values[name] || "")}</textarea><small>${help}</small></label>`;
  const check = (name, label) => `<label class="compute-check compute-wide"><input type="checkbox" name="${name}" required${values[name] ? " checked" : ""}><span>${label}</span></label>`;
  const targetLabel = row => `${row.name} · ${row.board}${credentialState(row) === "bound" ? "" : " · no credential bound yet"}`;
  const gatewaySelect = () => select("gateway_id", "Gateway and project", (inventory?.gateways || []).map(row => [row.id, `${row.name} · ${row.board}`]));

  // Reference-only credential guidance for the gateway/target form. Saving stays possible for any typed reference.
  const credentialForm = () => ["gateway", "node", "bind"].includes(editor);
  const credentialPurpose = () => editor === "node" || editor === "bind" && bindCollection === "nodes" ? "target" : "gateway";
  const bindRow = () => editor === "bind" ? rowById(bindCollection, selected) : null;
  // The values the documented step is filled with: this form's, or the saved record's own IDs when binding.
  function provisionArgs() {
    const row = bindRow();
    return row ? { workspace, purpose: credentialPurpose(), account: row.account, version: values.credential_version, gateway: bindCollection === "nodes" ? row.gateway_id : row.id, node: bindCollection === "nodes" ? row.id : undefined }
      : { workspace, purpose: credentialPurpose(), account: values.account, version: values.credential_version, gateway: values.gateway_id };
  }
  function credentialCheck() {
    const purpose = credentialPurpose(), id = String(values.credential_id ?? "").trim(), version = String(values.credential_version ?? "").trim(), account = String(bindRow()?.account ?? values.account ?? "").trim();
    const choices = credentials.filter(row => row.purpose === purpose), listed = credentialStatus === "ready" || credentialStatus === "unprovisioned";
    if (!listed) return { problem: true, tone: "warn", text: "The list of credentials could not be read right now, so a reference cannot be checked here. You can still type one by hand; it is checked when verification runs." };
    const row = id ? credentials.find(item => item.id === id) : null, name = e(id);
    const later = editor === "bind" ? "" : " Or leave it empty and bind one right after saving.";
    if (!id) return choices.length ? { tone: "info", text: `Choose a credential above, or type a reference by hand.${later}` } : { problem: true, tone: "warn", text: (credentials.length ? `None of this workspace’s credentials is for a ${purpose}.` : "No credentials exist in this workspace yet.") + later };
    if (!row) return { problem: true, tone: "warn", text: `No credential named ${name} exists in this workspace. Pick one from the list or create one with the steps below. You can still save this reference, but it cannot be verified until that credential exists.` };
    if (row.purpose !== purpose) return { problem: true, tone: "warn", text: `${name} is for a ${e(row.purpose)}, not a ${purpose}. Pick a ${purpose} credential or create one with the steps below.` };
    if (row.state === "revoked") return { problem: true, tone: "warn", text: `${name} was revoked and can no longer be used. Pick another or create a new one with the steps below.` };
    if (row.state === "expired") return { problem: true, tone: "warn", text: `${name} expired and can no longer be used. Pick another or create a new one with the steps below.` };
    if (version && version !== String(row.version)) return { tone: "warn", text: `${name} is version ${e(row.version)}, not ${e(version)}. Change the version below.` };
    if (account && account !== row.account) return { tone: "warn", text: `${name} is for the account ${e(row.account)}, but this form uses ${e(account)}. Use the matching account or pick another credential.` };
    return row.state === "pending" ? { tone: "info", text: `${name} exists but has not been switched on yet. You can save now; it must be switched on before it can be used.` } : { tone: "ok", text: `${name} is active and ready for verification.` };
  }
  function credentialStatusHTML() {
    const check = credentialCheck(), choices = credentials.filter(row => row.purpose === credentialPurpose());
    const showStep = editor === "bind" || check.problem || (!String(values.credential_id ?? "").trim() && !choices.length);
    const command = provisionCommand(provisionArgs());
    return `<p class="compute-credential-status" data-tone="${check.tone}" role="status">${check.text}</p>`
      + (showStep ? `<div class="compute-credential-next"><p><strong>How to create one</strong></p><p>Credentials are not created in the browser, so no secret passes through this page. Run this on the server as the service user, or ask the person who runs it. It prints only the new credential’s name and public key. Then choose “Check again” and pick it from the list. ${editor === "bind" ? "This record’s ID is already filled in. " : "The step needs this record’s ID, which exists once it is saved, so run it after saving. "}Replace the capitalised placeholders; the full steps are in docs/compute/RUNNING.md under “Provision a credential”.</p><pre data-compute-provision tabindex="0">${e(command)}</pre><div class="compute-actions">${button("copy-provision", "Copy these steps")}${button("refresh", reading ? "Checking…" : "Check again", reading)}</div>${copyNote ? `<p role="status">${e(copyNote)}</p>` : ""}</div>` : "");
  }
  function credentialSectionHTML() {
    const choices = credentials.filter(row => row.purpose === credentialPurpose());
    const chosen = choices.find(row => row.id === String(values.credential_id ?? "").trim());
    const chooser = choices.length ? `<label>Choose an existing credential<select name="credential_choice"><option value="">Choose…</option>${choices.map(row => `<option value="${e(row.id)}"${chosen === row ? " selected" : ""}>${e(credentialLabel(row))}</option>`).join("")}</select><small>Only the name, version, account and state are shown. The key itself is never shown or typed here.</small></label>` : "";
    return `<div class="compute-wide compute-credential"><h3>Credential</h3><p class="compute-quiet">A credential is the SSH identity Commonplace uses to reach this machine. ${editor === "bind" ? "Pick one that exists in this workspace, or create one with the step below." : "You can save without a credential: it is bound to this record’s ID, which Commonplace assigns when you save, so you attach it right after saving. Until then the record shows “No credential bound yet” and cannot be verified or used until one is bound. If you already have one for this machine, pick it or type its reference below."}</p>${chooser}<div data-compute-credential-status>${credentialStatusHTML()}</div></div>`;
  }
  function editorHTML() {
    if (!editor || !manageable()) return "";
    let fields = "", heading = "", submit = "";
    if (["gateway", "node"].includes(editor)) {
      const gateway = editor === "gateway";
      heading = gateway ? "Add gateway" : "Enroll target"; submit = gateway ? "Save pending gateway" : "Save pending target";
      fields = (gateway ? select("board", "Existing project", projects.map(row => [row.slug, row.name || row.slug])) : gatewaySelect())
        + field("name", "Name") + field("address", gateway ? "Numeric management address" : "Private numeric target address", { max: 64 })
        + field("port", "SSH port", { type: "number", min: 1, ceiling: 65535 }) + field("account", "SSH account", { max: 64 })
        + (gateway ? "" : field("interface", "Exact gateway interface", { max: 64 }))
        + credentialSectionHTML()
        + field("credential_id", "Existing credential reference (optional)", { optional: true, help: "Pick one above, type the reference by hand, or leave empty and bind one right after saving. Only a name is entered here, never a key." })
        + field("credential_version", "Credential version (optional)", { type: "number", min: 1, optional: true })
        + select("host_key_algorithm", "Public host key algorithm", algorithms.map(item => [item, item]))
        + area("host_key_key", "Full public SSH host key (base64 bytes)", 5464, "Use the full public host key from an independent trusted source, not a display fingerprint.")
        + '<p class="compute-wide compute-verification"><strong>What “verification” means.</strong> Saving this form does not connect to the machine and cannot run anything. It only records the address, account, host key and credential reference you entered, marked Pending verification. A separate verification step later checks that the machine shows exactly this full public host key and that the credential is switched on for this account. If either differs, Commonplace refuses to connect instead of trusting a new key.</p>'
        + check("pin_confirmed", "I compared this full public host key with an independent trusted source for this exact gateway or target, address and account.")
        + '<p class="compute-wide">Saving records your confirmation. The connection remains pending verification and grants no execution access.</p>';
    } else if (editor === "bind") {
      const row = bindRow();
      if (!row || credentialState(row) !== "unbound") return "";
      heading = `Bind a credential to ${row.name}`; submit = "Bind credential";
      fields = `<p class="compute-wide compute-verification"><strong>No credential bound yet.</strong> This ${bindCollection === "nodes" ? "target" : "gateway"} was saved without one, so it cannot be verified or used for runs until one is bound. A credential belongs to this record’s ID (<code>${e(row.id)}</code>), which exists now. Binding only records the reference; nothing connects and no key is entered here.</p>`
        + credentialSectionHTML()
        + field("credential_id", "Credential reference", { help: "Pick one above or type the reference by hand. Only a name is entered here, never a key." })
        + field("credential_version", "Credential version", { type: "number", min: 1 });
    } else if (editor === "scope") {
      heading = "Grant passive scope"; submit = "Grant bounded passive scope";
      fields = gatewaySelect() + field("task_number", "Canonical task number in the gateway’s project", { type: "number", min: 1 })
        + field("agent_id", "Existing connection ID (optional)", { optional: true, help: "Leave empty to bind the scope to you. No connection directory is created here." })
        + area("cidrs", "Canonical private CIDRs", 1024, "One per line or separated by commas; at most 16. Exact networks only.")
        + area("interfaces", "Exact interfaces", 1024, "One per line or separated by commas; at most 16.")
        + '<p class="compute-wide">Method: passive only. Collection requires a separately configured and verified gateway.</p>'
        + Object.entries(limits).map(([name, ceiling]) => field(name, { hosts: "Host ceiling", concurrency: "Concurrent hosts", rate_per_second: "Hosts per second", duration_seconds: "Duration in seconds", results: "Result ceiling" }[name], { type: "number", min: 1, ceiling })).join("")
        + field("expires_minutes", "Expires in minutes", { type: "number", min: 1, ceiling: 60 })
        + check("scope_confirmed", "Grant only these passive networks, interfaces and limits for this task and actor until expiry. Enrollment and execution require separate authority.");
    } else if (editor === "script") {
      heading = "Save immutable Python script"; submit = "Save script bundle";
      fields = select("node_id", "Enrolled target", (inventory?.nodes || []).map(row => [row.id, targetLabel(row)]))
        + field("task_number", "Canonical task number", { type: "number", min: 1 })
        + area("source", "Exact Python source", 32768, "Saved verbatim, up to 32768 UTF-8 bytes. Changes require a new bundle and approval.")
        + area("arguments", "Arguments as a JSON string array", 8192, "Up to 16 literal arguments, each at most 256 UTF-8 bytes.")
        + `<label class="compute-wide">Output paths, one per line (optional)<textarea name="output_paths" rows="2" maxlength="1600">${e(values.output_paths || "")}</textarea><small>Up to eight relative paths inside the confined job. Input uploads are unavailable.</small></label>`
        + '<p class="compute-wide">Only unprivileged Python in the designated confined runtime is supported. Saving a bundle does not execute it. Runtime verification and exact approval are separate requirements.</p>';
    } else if (editor === "script-detail") {
      const row = scriptDetail;
      if (!row || !same(row.id, selected)) return "";
      return `<section class="compute-editor" aria-label="Immutable script"><header><h2>Inspect immutable script</h2>${button("close", "Close", busy)}</header><p>${taskHTML(row)} · Target ${e(row.node_id)}</p><p>Bundle SHA256 <code>${e(row.digest)}</code></p><details><summary>Exact manifest</summary><pre>${e(JSON.stringify(row.manifest, null, 2))}</pre></details><pre aria-label="Exact script source">${e(row.source)}</pre>${button("script-proposal", "Propose this script", busy || reading || uncertain, row.id)}</section>`;
    } else if (editor === "script-proposal") {
      const row = scripts.find(item => same(item.id, selected));
      if (!row) return "";
      heading = "Propose immutable script"; submit = "Create script proposal";
      fields = `<div class="compute-wide"><p>${taskHTML(row)} · Target ${e(row.node_id)}</p><p>Bundle SHA256 <code>${e(row.digest)}</code></p><pre>${e(JSON.stringify(row.manifest, null, 2))}</pre><p>The server binds this exact bundle and verified runtime to the approval digest. Approval does not prove dispatch or confinement.</p></div>`;
    } else if (editor === "discovery") {
      heading = "Collect passive observations"; submit = "Queue passive collection";
      fields = select("scope_id", "Current passive scope", inventory.scopes.filter(row => !row.revoked && Number(row.expires_at) > now()).map(row => [row.id, `${row.context?.project} · task ${row.context?.task} · ${(row.cidrs || []).join(", ")}`]))
        + select("sources", "Existing gateway cache", [["linux_arp", "Linux ARP cache"], ["ip_neigh", "IPv6 neighbor cache"]])
        + '<p class="compute-wide">Reads existing cache observations only. Each admitted collection permanently charges its reserved scope ceilings, even if cancelled or unused. Unknown attempts retain capacity. Observations do not enroll targets.</p>';
    } else if (editor === "proposal") {
      heading = "Propose system information"; submit = "Create proposal for approval";
      fields = select("node_id", "Enrolled target", (inventory?.nodes || []).map(row => [row.id, targetLabel(row)]))
        + field("task_number", "Canonical task number in the target’s project", { type: "number", min: 1 })
        + '<p class="compute-wide">Operation: <code>system.info.v1</code>. Arguments: <code>{}</code>. This creates an approval request; it does not connect or dispatch.</p>';
    } else if (editor === "approve") {
      const execution = rowById("executions", selected);
      if (!execution) return "";
      const bundle = scripts.find(row => same(row.id, execution.arguments?.bundle_id));
      const target = rowById("nodes", execution.node_id), gateway = rowById("gateways", execution.gateway_id);
      heading = "Approve exact proposal"; submit = "Approve automatic execution";
      fields = `<div class="compute-wide compute-snapshot"><p>${parts(e(execution.operation), e(execution.board), taskHTML(execution))}</p><p>Arguments: <code>${e(JSON.stringify(execution.arguments || {}))}</code></p><p>Current target binding: ${e(target?.name || execution.node_id)}</p>${bindingHTML(target)}<p>Current gateway binding: ${e(gateway?.name || execution.gateway_id)}</p>${bindingHTML(gateway)}<dl>${limitsHTML(execution.limits)}</dl><p>Exact SHA256 digest</p><pre>${e(approvalDigest)}</pre><p>The server rechecks this digest and current bindings before recording approval.</p></div>`
        + (execution.operation === "workspace.script.v1" ? `<details class="compute-wide"><summary>Immutable script contract</summary><p>Bundle ${e(bundle?.id || execution.arguments?.bundle_id)} · SHA256 ${e(bundle?.digest || "Unavailable")}</p><pre>${e(JSON.stringify(bundle?.manifest || {}, null, 2))}</pre>${scriptDetail && same(scriptDetail.id, bundle?.id) && scriptDetail.digest === bundle?.digest ? `<pre aria-label="Approved script source">${e(scriptDetail.source)}</pre>` : "<p>Close this form and inspect the script source from Workspace scripts before confirming this digest.</p>"}</details>` : "")
        + field("expected_digest", "Type the full digest to confirm this exact proposal", { max: 64 })
        + field("expires_minutes", "Approval expires in minutes", { type: "number", min: 1, ceiling: 5 })
        + '<p class="compute-wide">Approving permits the executor to run this exact operation automatically in the background over SSH. No additional dispatch confirmation is required. Approval does not confirm that execution started or completed.</p>';
    }
    return `<section class="compute-editor" aria-labelledby="compute-editor-heading"><header><h2 id="compute-editor-heading" tabindex="-1">${heading}</h2>${button("close", "Close", busy)}</header><form data-compute-form="${editor}"><fieldset${busy || uncertain ? " disabled" : ""}><div class="compute-fields">${fields}</div></fieldset><p role="alert">${e(error)}</p><div class="compute-actions"><button class="btn primary" type="submit"${busy || uncertain || reading ? " disabled" : ""}>${busy ? "Saving…" : submit}</button>${button("close", "Cancel", busy)}</div>${uncertain ? '<p role="status">Receipt unavailable. Reload inventory to check what was stored before starting another action.</p>' : ""}</form></section>`;
  }
  function inventorySection(name, heading, rows, empty, action, label, enabled = true, explanation = "", security = "", plain = "") {
    return `<section class="compute-inventory" aria-labelledby="compute-${name}"><header><h2 id="compute-${name}">${heading}</h2>${button(action, label, busy || reading || uncertain || !enabled)}</header>${plain ? `<p class="compute-plain">${plain}</p>` : ""}${explanation ? `<p class="compute-quiet">${explanation}</p>` : ""}${rows.length ? `<ul class="compute-list">${rows.join("")}</ul>` : `<p class="compute-empty">${empty}</p>`}${security ? `<details class="compute-guidance"><summary>Access and approval details</summary><p>${security}</p></details>` : ""}</section>`;
  }
  function render() {
    if (!eligible() || !host) return;
    let body = "";
    if (inventory?.can_manage === true) {
      const gatewayRows = inventory.gateways.map(row => `<li class="compute-row"><header><strong>${e(row.name)}</strong><span class="compute-state">${e(stateText(row.state))}</span></header><p>${parts(e(row.board), e(endpoint(row)))}</p>${credentialNote(row)}${bindButton(row, "gateway", busy || reading || uncertain)}<details><summary>Public host key and binding</summary>${bindingHTML(row)}</details></li>`);
      const nodeRows = inventory.nodes.map(row => `<li class="compute-row"><header><strong>${e(row.name)}</strong><span class="compute-state">${e(stateText(row.state))}</span></header><p>${parts(e(row.board), e(endpoint(row)), e(row.interface))}</p><p>Gateway ${e(rowById("gateways", row.gateway_id)?.name || row.gateway_id)}</p>${credentialNote(row)}${bindButton(row, "node", busy || reading || uncertain)}<details><summary>Public host key and binding</summary>${bindingHTML(row)}</details></li>`);
      const scopeRows = inventory.scopes.map(row => `<li class="compute-row"><header><strong>${parts(e(row.context?.project), e(row.context?.gateway))}</strong><span class="compute-state">${row.revoked ? "Revoked" : Number(row.expires_at) <= now() ? "Expired" : "Passive grant recorded"}</span></header><p>${parts(e((row.cidrs || []).join(", ")), e((row.interfaces || []).join(", ")))}</p><p>${parts(row.methods?.length ? `Methods: ${e(row.methods.join(", "))}` : "", taskHTML({ task_number: row.context?.task }), row.context?.agent ? `Actor ${e(row.context.agent)}` : "")}</p><p>Expires: ${e(Number.isFinite(Number(row.expires_at)) ? new Date(Number(row.expires_at) * 1000).toLocaleString() : "Unknown")}</p><details><summary>Scope limits and context</summary><dl><dt>Scope</dt><dd>${e(row.id)}</dd><dt>User</dt><dd>${e(row.context?.user)}</dd>${limitsHTML(row.limits)}</dl></details>${!row.revoked && Number(row.expires_at) > now() ? button("revoke", "Revoke passive scope", busy || reading || uncertain, row.id) : ""}</li>`);
      body = `${editorHTML()}<div class="compute-inventories">${workspaces.html()}<details class="compute-advanced compute-wide" data-compute-advanced${advancedOpen ? " open" : ""}><summary class="compute-advanced-summary">${ADVANCED.label}</summary><p class="compute-quiet">${ADVANCED.intro}</p><div class="compute-inventories">`
        + inventorySection("gateways", "Gateways", gatewayRows, "No gateways added yet.", "gateway", "Add gateway", projects.length > 0, boardError || (!projects.length ? "Create a project before adding a gateway." : ""), "Add an existing project’s numeric management endpoint and explicitly confirm its full public host key.", ADVANCED.gateways)
        + inventorySection("nodes", "Targets", nodeRows, "No targets enrolled yet.", "node", "Enroll target", inventory.gateways.length > 0, !inventory.gateways.length ? "Add a gateway before enrolling a target." : "", "Enrollment uses an existing gateway and a separately confirmed public host key.", ADVANCED.nodes)
        + inventorySection("scopes", "Passive scopes", scopeRows, "No scopes granted yet.", "scope", "Grant scope", inventory.gateways.length > 0, "", "Each scope binds exact networks, interfaces, actor, task, bounds and expiry.", ADVANCED.scopes)
        + "</div></details>"
        + inventorySection("scripts", "Workspace scripts", scripts.map(row => `<li class="compute-row"><header><strong>${e(row.id)}</strong>${taskHTML(row)}</header><p>${parts(row.node_id ? `Target ${e(row.node_id)}` : "", row.digest ? `Bundle SHA256 <code>${e(row.digest)}</code>` : "")}</p>${button("inspect-script", "Inspect source and manifest", busy || reading || uncertain, row.id)}</li>`), "No scripts saved yet.", "script", "Add Python script", inventory.nodes.length > 0 && !scriptsError, scriptsError, (inventory.capabilities?.scripts === true ? "Scripts use the independently verified confined target runtime and require exact approval." : "Script execution is unavailable until the target runtime and confinement are independently configured and verified."))
        + inventorySection("discoveries", "Passive observations", discoveries.map(row => `<li class="compute-row"><header><strong>${parts(e(row.board), taskHTML(row))}</strong><span class="compute-state">${e(stateText(row.state))}</span></header><p>${parts(row.scope_id ? `Scope ${e(row.scope_id)}` : "", e(row.reason))}</p><details><summary>${e(row.observations?.length || 0)} observations and usage</summary><pre>${e(JSON.stringify({ observations: row.observations || [], usage: row.usage || {} }, null, 2))}</pre></details>${discoveryCancellable(row) ? button("discovery-cancel", "Request cancellation", busy || reading || uncertain, row.id) + "<p>A cancellation request does not confirm remote termination.</p>" : ""}${row.reason === "cancellation_requested" ? "<p>Cancellation requested; remote termination is unconfirmed.</p>" : ""}${row.state === "uncertain" ? "<p>Outcome unknown. Capacity remains reserved; this attempt will not automatically repeat.</p>" : ""}</li>`), "No observations collected yet.", "discovery", "Collect observations", discoveryAvailable && inventory.scopes.some(row => !row.revoked && Number(row.expires_at) > now()), discoveryError || (!discoveryAvailable ? "Observation collection is not available yet." : ""), (discoveryAvailable ? "Collection reads approved gateway caches in the background. Reload to check progress." : "Passive collection is unavailable until its gateway and admission channel are configured."))
        + inventorySection("executions", "Execution proposals", inventory.executions.map(row => computeExecutionHTML(row, { busy: busy || reading || uncertain })), "No tasks waiting for approval.", "proposal", "Propose system info", inventory.nodes.length > 0, "", "The fixed system information operation requires a target and a canonical task.")
        + '</div><details class="compute-capabilities" aria-labelledby="compute-capabilities"><summary id="compute-capabilities">Access and execution details</summary><div><h3>Automatic execution</h3><p id="compute-transport-reason">' + e(executionAvailability()) + '</p></div><div><button class="btn" disabled aria-describedby="compute-passive-reason">Collect observations</button><p id="compute-passive-reason">Collection availability is shown with Passive observations. A scope grant never enrolls devices.</p></div><div><button class="btn" disabled aria-describedby="compute-script-reason">Scripts</button><p id="compute-script-reason">Script availability is shown with Workspace scripts. Saving a bundle never runs it.</p></div></details>';
    } else if (inventory) body = `<p>Compute management requires the current workspace owner.</p><div class="compute-inventories">${workspaces.html()}</div>`;
    else if (memberView) body = `<div class="compute-inventories">${workspaces.html()}</div>`;
    host.innerHTML = `<div class="compute"><header class="compute-intro"><div class="compute-title">${title ? `<h1>${e(title)}</h1>` : ""}<p>${intro}</p></div>${button("refresh", reading ? "Loading…" : "Reload inventory", busy || reading)}</header><p class="compute-notice" role="status">${e(notice || (reading ? "Loading Compute inventory…" : ""))}</p>${!editor && error && !memberView ? `<p class="compute-error" role="alert">${e(error)}</p>` : ""}${memberView ? '<p class="compute-notice">Compute management requires the current workspace owner. You can still look at the cloud workspaces below.</p>' : ""}${body}</div>`;
  }
  async function refresh() {
    if (!eligible() || reading || busy) return;
    const page = epoch, read = ++revision;
    reading = true; error = ""; memberView = false; render();
    void workspaces.load();
    const results = await Promise.allSettled([request(BASE), request("/api/boards"), request(`${BASE}/discoveries`), request(`${BASE}/scripts`), request(`${BASE}/credentials`)]);
    if (!valid(page) || read !== revision) return;
    reading = false;
    const [data, boards, passive, bundles, refs] = results;
    if (data.status === "rejected") { if (!denied(data.reason)) { error = errorText(data.reason); render(); } return; }
    // A failed board read does not replace the successful Compute inventories.
    if (boards.status === "rejected" && boards.reason?.status === 401) { stop(); return; }
    if (passive.status === "rejected" && [401, 403].includes(passive.reason?.status)) { denied(passive.reason); return; }
    discoveries = passive.status === "fulfilled" ? passive.value.discoveries : [];
    discoveryAvailable = passive.status === "fulfilled" && passive.value.capabilities?.passive_discovery === true;
    discoveryError = passive.status === "rejected" ? "Passive observations could not be loaded. Reload before collecting." : "";
    if (bundles.status === "rejected" && [401, 403].includes(bundles.reason?.status)) { denied(bundles.reason); return; }
    if (refs.status === "rejected" && [401, 403].includes(refs.reason?.status)) { denied(refs.reason); return; }
    credentials = refs.status === "fulfilled" && Array.isArray(refs.value.credentials) ? refs.value.credentials : [];
    credentialStatus = refs.status === "fulfilled" && ["ready", "unprovisioned"].includes(refs.value.status) ? refs.value.status : "unavailable";
    scripts = bundles.status === "fulfilled" ? bundles.value.scripts : [];
    scriptsError = bundles.status === "rejected" ? "Script bundles could not be loaded. Reload before creating or approving scripts." : "";
    inventory = data.value;
    projects = boards.status === "fulfilled" ? boards.value.boards : [];
    boardError = boards.status === "rejected" ? "Projects could not be loaded. Reload before adding a gateway." : "";
    if (editor === "approve" && (rowById("executions", selected)?.digest !== approvalDigest || rowById("executions", selected)?.state !== "awaiting_approval")) {
      editor = ""; values = {}; selected = null; approvalDigest = ""; notice = "The proposal changed. Inspect its current digest before approving.";
    }
    if (uncertain) { editor = ""; values = {}; selected = null; approvalDigest = ""; attempt = null; uncertain = false; notice = "Inventory reloaded. Check the stored records before starting another action."; }
    if (boards.status === "fulfilled") onBoards(projects);
    render();
  }
  function open(kind, id, collection) {
    if (!manageable() || reading || busy || uncertain) return;
    if (kind === "bind") {
      if (credentialState(rowById(collection, id)) !== "unbound") return;
      bindCollection = collection;
    }
    if (kind === "gateway" && !projects.length || ["node", "scope"].includes(kind) && !inventory.gateways.length || kind === "proposal" && !inventory.nodes.length) return;
    if (kind === "approve") {
      const row = rowById("executions", id);
      if (row?.state !== "awaiting_approval" || !digestOK(row.digest) || !rowById("nodes", row.node_id) || !rowById("gateways", row.gateway_id)) return;
      approvalDigest = row.digest;
    }
    if (kind === "discovery" && !discoveryAvailable) return;
    if (kind === "script" && (!inventory.nodes.length || scriptsError)) return;
    if (kind === "script-proposal" && !scripts.some(row => same(row.id, id))) return;
    editor = kind; selected = id; values = ["node", "gateway"].includes(kind) ? { port: "22" } : kind === "script" ? { arguments: "[]" } : kind === "script-proposal" ? { node_id: scripts.find(row => same(row.id, id)).node_id, task_number: scripts.find(row => same(row.id, id)).task_number, bundle_id: id } : {}; error = ""; notice = ""; copyNote = ""; uncertain = false; attempt = null;
    render(); host.querySelector("#compute-editor-heading")?.focus();
  }
  async function inspectScript(id) {
    if (!manageable() || reading || busy || uncertain) return;
    const page = epoch, read = ++revision;
    reading = true; error = ""; render();
    try {
      const data = await request(`${BASE}/scripts/${enc(id)}`);
      if (!valid(page) || read !== revision) return;
      if (!same(data.script?.id, id) || typeof data.script.source !== "string") throw new Error("Invalid script response.");
      scriptDetail = data.script;
      reading = false; open("script-detail", id);
    } catch (failure) {
      if (valid(page) && read === revision && !denied(failure)) error = errorText(failure);
    } finally { if (valid(page) && read === revision) { reading = false; render(); } }
  }
  async function mutate(path, payload, collection, kind) {
    if (!manageable() || reading || busy || uncertain) return;
    const page = epoch; busy = true; error = ""; notice = ""; revision++; render();
    try {
      const result = await request(path, { method: "POST", body: payload });
      if (!valid(page)) return;
      const name = { gateways: "gateway", nodes: "node", scopes: "scope", executions: "execution", discoveries: "discovery", scripts: "script" }[collection], row = result[name];
      if (collection === "scripts") scripts = [row, ...scripts.filter(item => !same(item.id, row.id))];
      else if (collection === "discoveries") discoveries = [row, ...discoveries.filter(item => !same(item.id, row.id))];
      else inventory[collection] = [row, ...inventory[collection].filter(item => !same(item.id, row.id))];
      editor = ""; selected = null; values = {}; attempt = null; approvalDigest = "";
      const unbound = ["gateway", "node"].includes(kind) && credentialState(row) === "unbound";
      if (unbound) { editor = "bind"; selected = row.id; bindCollection = collection; copyNote = ""; }
      notice = unbound ? "Saved without a credential. No credential bound yet: bind one below before this can be verified or used. No connection verified." : kind === "bind" ? "Credential bound. Still pending verification; nothing connected." : collection === "scripts" ? "Immutable script saved. Inspect its source and manifest before proposing it for approval." : collection === "discoveries" ? `Passive collection: ${stateText(row.state)}. Reload to check progress. Cancellation does not confirm remote termination.` : ["approve", "cancel", "proposal", "script-proposal"].includes(kind) ? `Server state: ${executionStatus(row)}.` : kind === "revoke" ? "Passive scope revoked." : kind === "scope" ? "Passive scope recorded. No observations collected." : "Saved pending verification. No connection verified.";
    } catch (failure) {
      if (!valid(page) || denied(failure)) return;
      error = errorText(failure);
      // Replay-safe creation keeps its exact request key after an ambiguous
      // response. Other writes wait for inventory reconciliation.
      if (!failure.status || failure.status >= 500) {
        uncertain = !["proposal", "discovery", "script", "script-proposal"].includes(kind);
        if (["proposal", "discovery", "script", "script-proposal"].includes(kind)) error += " Retry the unchanged request to reuse its original request ID.";
      }
    } finally { if (valid(page)) { busy = false; render(); } }
  }
  function submit(event) {
    if (workspaces.submit(event)) return;
    const form = event.target.closest("[data-compute-form]");
    if (!form) return;
    event.preventDefault(); event.stopPropagation();
    if (!manageable() || busy || reading || uncertain || form.dataset.computeForm !== editor) return;
    values = Object.fromEntries(new FormData(form));
    try {
      const kind = editor;
      if (kind === "script-proposal") {
        const bundle = scripts.find(row => same(row.id, selected));
        if (!bundle) throw new Error("Reload and inspect the immutable script before proposing it.");
        values = { node_id: bundle.node_id, task_number: bundle.task_number, bundle_id: bundle.id };
      }
      if (["proposal", "discovery", "script", "script-proposal"].includes(kind)) {
        const signature = JSON.stringify([kind, values.node_id, values.task_number, values.scope_id, values.sources, values.source, values.arguments, values.output_paths, values.bundle_id]);
        if (!attempt || attempt.signature !== signature) attempt = { signature, id: requestId() };
      }
      if (kind === "bind" && credentialState(rowById(bindCollection, selected)) !== "unbound") throw new Error("This record already has a credential. Reload the inventory.");
      const payload = computePayload(kind, values, { now: now(), requestId: attempt?.id });
      if (kind === "approve" && (payload.expected_digest !== approvalDigest || rowById("executions", selected)?.digest !== approvalDigest || rowById("executions", selected)?.state !== "awaiting_approval")) throw new Error("The digest does not match the current proposal. Inspect and confirm it again.");
      const collection = { gateway: "gateways", node: "nodes", bind: bindCollection, scope: "scopes", proposal: "executions", approve: "executions", discovery: "discoveries", script: "scripts", "script-proposal": "executions" }[kind];
      if (kind === "bind") void mutate(`${BASE}/${bindCollection}/${enc(selected)}/credential`, payload, bindCollection, kind);
      else void mutate(`${BASE}/${collection}${kind === "approve" ? `/${enc(selected)}/approve` : ""}`, payload, collection, kind);
    } catch (failure) { error = failure.message; render(); }
  }
  function click(event) {
    if (eligible() && workspaces.click(event)) return;
    // The native toggle opens or closes the section; remember it so a re-render keeps it as the person left it.
    if (event.target.closest("[data-compute-advanced] > summary")) { advancedOpen = !advancedOpen; return; }
    const target = event.target.closest("[data-compute-action]");
    if (!target) return;
    const { computeAction: action, id } = target.dataset;
    if (target.disabled || !eligible()) return;
    if (action === "inspect-script") { void inspectScript(id); return; }
    if (action === "refresh") { void refresh(); return; }
    if (action === "copy-provision") { void copyProvision(); return; }
    if (busy) return;
    if (action === "close") { editor = ""; values = {}; selected = null; approvalDigest = ""; error = ""; attempt = null; if (uncertain) notice = "Receipt unavailable. Reload inventory before starting another action."; render(); return; }
    if (uncertain) return;
    if (action === "discovery-cancel") {
      if (discoveries.some(row => same(row.id, id) && discoveryCancellable(row))) void mutate(`${BASE}/discoveries/${enc(id)}/cancel`, {}, "discoveries", "discovery-cancel");
    } else if (action === "cancel") {
      if (cancellable(rowById("executions", id) || {})) void mutate(`${BASE}/executions/${enc(id)}/cancel`, {}, "executions", "cancel");
    } else if (action === "revoke") {
      const row = rowById("scopes", id);
      if (row && !row.revoked && Number(row.expires_at) > now()) void mutate(`${BASE}/scopes/${enc(id)}/revoke`, {}, "scopes", "revoke");
    } else if (action === "bind-gateway" || action === "bind-node") open("bind", id, action === "bind-node" ? "nodes" : "gateways");
    else if (["gateway", "node", "scope", "proposal", "approve", "discovery", "script", "script-proposal"].includes(action)) open(action, id);
  }
  async function copyProvision() {
    if (!credentialForm()) return;
    const page = epoch;
    let note = "Copied.";
    // No clipboard, or a copy that does not return a promise, is a failed copy: never claim "Copied." without proof.
    try { const done = copy(provisionCommand(provisionArgs())); if (typeof done?.then !== "function") throw new Error("no clipboard"); await done; }
    catch { note = "Copying is not available here. Select the text above and copy it."; }
    if (!valid(page) || !credentialForm()) return;
    copyNote = note; refreshCredentialStatus();
  }
  // Update only the guidance so typing keeps its focus; fall back to a full render when it is not mounted.
  function refreshCredentialStatus() {
    const spot = host?.querySelector?.("[data-compute-credential-status]");
    if (spot) spot.innerHTML = credentialStatusHTML(); else render();
  }
  function input(event) {
    if (eligible() && workspaces.input(event)) return;
    const field = event.target;
    if (!editor || !eligible() || !field.name) return;
    values[field.name] = field.type === "checkbox" ? field.checked : field.value;
    if (["gateway", "node"].includes(editor) && field.name !== "pin_confirmed") {
      values.pin_confirmed = false;
      const confirmation = host.querySelector('[name="pin_confirmed"]');
      if (confirmation) confirmation.checked = false;
    }
    if (credentialForm() && field.name === "credential_choice") {
      const row = credentials.find(item => item.id === field.value && item.purpose === credentialPurpose());
      if (row) { values.credential_id = row.id; values.credential_version = String(row.version); }
      copyNote = ""; render(); return;
    }
    if (credentialForm() && ["credential_id", "credential_version", "account", "gateway_id"].includes(field.name)) { copyNote = ""; refreshCredentialStatus(); }
  }
  const routeChanged = () => { if (live && !eligible()) stop(); };
  function stop() {
    live = false; epoch++; revision++; abort();
    host?.removeEventListener("click", click); host?.removeEventListener("submit", submit); host?.removeEventListener("input", input); host?.removeEventListener("change", input);
    events?.removeEventListener("hashchange", routeChanged); events?.removeEventListener("pagehide", stop); doc?.removeEventListener("session-expired", stop);
    if (host) host.innerHTML = "";
    workspaces.reset(); memberView = false;
    host = null; session = null; workspace = null; identity = null; inventory = null; projects = []; editor = ""; values = {}; selected = null; approvalDigest = ""; attempt = null;
    discoveries = []; discoveryAvailable = false; discoveryError = "";
    scripts = []; scriptDetail = null; scriptsError = "";
    credentials = []; credentialStatus = "unknown"; copyNote = "";
    busy = false; reading = false; uncertain = false; notice = ""; error = ""; boardError = "";
  }
  function show() {
    stop();
    session = current();
    if (!session?.workspace || !session?.actor || route() !== expectedRoute) return;
    workspace = session.workspace; identity = session.actor; host = mount();
    if (!host) return;
    live = true; epoch++;
    host.addEventListener("click", click); host.addEventListener("submit", submit); host.addEventListener("input", input); host.addEventListener("change", input);
    events?.addEventListener("hashchange", routeChanged); events?.addEventListener("pagehide", stop); doc?.addEventListener("session-expired", stop);
    render(); void refresh();
  }
  return { show, stop, refresh };
}
