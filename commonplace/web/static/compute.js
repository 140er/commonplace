import { escape as e } from "./api.js";

const BASE = "/api/compute";
const enc = encodeURIComponent;
const algorithms = ["ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "ssh-rsa"];
const limits = { hosts: 4096, concurrency: 64, rate_per_second: 100, duration_seconds: 300, results: 4096 };
const same = (a, b) => String(a) === String(b);
const digestOK = value => /^[a-f0-9]{64}$/.test(String(value));
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
const states = { pending_verification: "Pending verification", awaiting_approval: "Awaiting approval", approved: "Approved · not dispatched", cancelled: "Cancelled before dispatch" };
const stateText = value => states[value] || value || "Unknown state";
const errorText = error => ({ sign_in: "Sign in again to use Compute.", owner_required: "Compute management requires the current workspace owner.", not_found: "This record is unavailable. Reload the inventory.", conflict: "The record changed or the request conflicts. Reload and check its current state.", invalid_request: "The server refused these values. Check the fields and bounds.", unsupported_operation: "This operation is unavailable in the current workspace.", transport_unavailable: "Transport is unavailable. No dispatch has been confirmed." }[error?.payload?.error] || "The request could not be confirmed. Reload the inventory before repeating an action.");
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
      credential_id: required(values.credential_id, "credential reference"), credential_version: integer(values.credential_version, "Credential version"), pin_confirmed: true,
    };
    return kind === "gateway" ? { board: required(values.board, "project slug"), ...payload } : { gateway_id: required(values.gateway_id, "gateway"), ...payload, interface: required(values.interface, "interface", 64) };
  }
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
const bindingHTML = row => row ? `<dl><dt>Address</dt><dd>${e(row.address)}:${e(row.port)}</dd><dt>Account</dt><dd>${e(row.account)}</dd><dt>Epoch</dt><dd>${e(row.epoch)}</dd><dt>Credential reference</dt><dd>${e(row.credential_id)} · version ${e(row.credential_version)}</dd></dl><p>Full public host key</p><pre>${e(row.host_key?.algorithm)} ${e(row.host_key?.key)}</pre>` : "<p>Binding unavailable. Reload before approving.</p>";

export function computeExecutionHTML(row, { busy = false } = {}) {
  const run = row.run;
  const evidence = run ? `<section aria-label="Execution evidence"><p>${e(run.reason)}</p>${run.result?.output ? `<pre>${e(run.result.output)}</pre>` : ""}${run.capacity_reserved ? '<p>Capacity remains reserved until the execution outcome is reconciled.</p>' : ""}</section>` : "";
  return `<li class="compute-row" data-state="${e(run?.phase || row.state)}"><header><strong>${e(row.operation)}</strong><span class="compute-state">${e(executionStatus(row))}</span></header><p>${e(row.board)} · ${taskHTML(row)} · Target ${e(row.node_id)}</p><p class="compute-digest">SHA256 <code>${e(row.digest)}</code></p>${row.reason ? `<p>${e(row.reason)}</p>` : ""}<details><summary>Proposal details</summary><dl><dt>Proposal</dt><dd>${e(row.id)}</dd><dt>Gateway</dt><dd>${e(row.gateway_id)}</dd><dt>Arguments</dt><dd><code>${e(JSON.stringify(row.arguments || {}))}</code></dd>${limitsHTML(row.limits)}<dt>Created</dt><dd>${e(row.created_at)}</dd><dt>Updated</dt><dd>${e(row.updated_at)}</dd></dl></details><div class="compute-actions">${!run && row.state === "awaiting_approval" ? button("approve", "Inspect approval", busy || !digestOK(row.digest), row.id) : ""}${cancellable(row) ? button("cancel", run ? "Request cancellation" : "Cancel proposal", busy, row.id) : ""}</div>${evidence}</li>`;
}

// One visit owns its DOM, session object and all requests. Even transports that
// ignore abort cannot publish after a different route/session has taken over.
export function createCompute({ mount, api, current, route = () => globalThis.location?.hash, onBoards = () => {}, doc = globalThis.document, events = globalThis.window, now = () => Date.now() / 1000, requestId = () => globalThis.crypto.randomUUID() }) {
  let host = null, session = null, workspace = null, identity = null, live = false, epoch = 0, revision = 0;
  let inventory = null, projects = [], reading = false, busy = false, uncertain = false, error = "", notice = "", boardError = "";
  let discoveries = [], discoveryAvailable = false, discoveryError = "";
  let scripts = [], scriptDetail = null, scriptsError = "";
  let editor = "", values = {}, selected = null, approvalDigest = "", attempt = null;
  const requests = new Set();
  const eligible = () => live && current() === session && current()?.workspace === workspace && current()?.actor === identity && route() === "#/compute";
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
      revision++; abort(); scripts = []; scriptDetail = null; inventory = null; projects = []; editor = ""; values = {}; selected = null; attempt = null; approvalDigest = "";
      reading = false; busy = false; error = errorText(failure); notice = ""; render(); return true;
    }
    return false;
  }
  const rowById = (name, id) => inventory?.[name]?.find(row => same(row.id, id));
  const manageable = () => inventory?.can_manage === true && eligible();
  const option = (value, label, field) => `<option value="${e(value)}"${same(values[field], value) ? " selected" : ""}>${e(label)}</option>`;
  const field = (name, label, { type = "text", max = 200, min, ceiling, optional = false, help = "" } = {}) => `<label>${label}<input name="${name}" type="${type}" value="${e(values[name] ?? "")}"${optional ? "" : " required"} maxlength="${max}"${min ? ` min="${min}"` : ""}${ceiling ? ` max="${ceiling}"` : ""} ${type === "number" ? 'step="1"' : 'autocomplete="off"'}>${help ? `<small>${help}</small>` : ""}</label>`;
  const select = (name, label, rows) => `<label>${label}<select name="${name}" required><option value="">Choose…</option>${rows.map(([value, text]) => option(value, text, name)).join("")}</select></label>`;
  const area = (name, label, max, help) => `<label class="compute-wide">${label}<textarea name="${name}" required maxlength="${max}" rows="3" spellcheck="false">${e(values[name] || "")}</textarea><small>${help}</small></label>`;
  const check = (name, label) => `<label class="compute-check compute-wide"><input type="checkbox" name="${name}" required${values[name] ? " checked" : ""}><span>${label}</span></label>`;
  const gatewaySelect = () => select("gateway_id", "Gateway and project", (inventory?.gateways || []).map(row => [row.id, `${row.name} · ${row.board}`]));

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
        + field("credential_id", "Existing credential reference", { help: "Reference only. Credential provisioning happens separately." })
        + field("credential_version", "Credential version", { type: "number", min: 1 })
        + select("host_key_algorithm", "Public host key algorithm", algorithms.map(item => [item, item]))
        + area("host_key_key", "Full public SSH host key (base64 bytes)", 5464, "Use the full public host key from an independent trusted source, not a display fingerprint.")
        + check("pin_confirmed", "I compared this full public host key with an independent trusted source for this exact gateway or target, address and account.")
        + '<p class="compute-wide">Saving records your confirmation. The connection remains pending verification and grants no execution access.</p>';
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
      fields = select("node_id", "Enrolled target", (inventory?.nodes || []).map(row => [row.id, `${row.name} · ${row.board}`]))
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
      fields = select("node_id", "Enrolled target", (inventory?.nodes || []).map(row => [row.id, `${row.name} · ${row.board}`]))
        + field("task_number", "Canonical task number in the target’s project", { type: "number", min: 1 })
        + '<p class="compute-wide">Operation: <code>system.info.v1</code>. Arguments: <code>{}</code>. This creates an approval request; it does not connect or dispatch.</p>';
    } else if (editor === "approve") {
      const execution = rowById("executions", selected);
      if (!execution) return "";
      const bundle = scripts.find(row => same(row.id, execution.arguments?.bundle_id));
      const target = rowById("nodes", execution.node_id), gateway = rowById("gateways", execution.gateway_id);
      heading = "Approve exact proposal"; submit = "Record approval without dispatch";
      fields = `<div class="compute-wide compute-snapshot"><p>${e(execution.operation)} · ${e(execution.board)} · ${taskHTML(execution)}</p><p>Arguments: <code>${e(JSON.stringify(execution.arguments || {}))}</code></p><p>Current target binding: ${e(target?.name || execution.node_id)}</p>${bindingHTML(target)}<p>Current gateway binding: ${e(gateway?.name || execution.gateway_id)}</p>${bindingHTML(gateway)}<dl>${limitsHTML(execution.limits)}</dl><p>Exact SHA256 digest</p><pre>${e(approvalDigest)}</pre><p>The server rechecks this digest and current bindings before recording approval.</p></div>`
        + (execution.operation === "workspace.script.v1" ? `<details class="compute-wide"><summary>Immutable script contract</summary><p>Bundle ${e(bundle?.id || execution.arguments?.bundle_id)} · SHA256 ${e(bundle?.digest || "Unavailable")}</p><pre>${e(JSON.stringify(bundle?.manifest || {}, null, 2))}</pre>${scriptDetail && same(scriptDetail.id, bundle?.id) && scriptDetail.digest === bundle?.digest ? `<pre aria-label="Approved script source">${e(scriptDetail.source)}</pre>` : "<p>Close this form and inspect the script source from Workspace scripts before confirming this digest.</p>"}</details>` : "")
        + field("expected_digest", "Type the full digest to confirm this exact proposal", { max: 64 })
        + field("expires_minutes", "Approval expires in minutes", { type: "number", min: 1, ceiling: 5 })
        + '<p class="compute-wide">Approval is stored locally. Approved does not mean dispatched, running or completed.</p>';
    }
    return `<section class="compute-editor" aria-labelledby="compute-editor-heading"><header><h2 id="compute-editor-heading" tabindex="-1">${heading}</h2>${button("close", "Close", busy)}</header><form data-compute-form="${editor}"><fieldset${busy || uncertain ? " disabled" : ""}><div class="compute-fields">${fields}</div></fieldset><p role="alert">${e(error)}</p><div class="compute-actions"><button class="btn primary" type="submit"${busy || uncertain || reading ? " disabled" : ""}>${busy ? "Saving…" : submit}</button>${button("close", "Cancel", busy)}</div>${uncertain ? '<p role="status">Receipt unavailable. Reload inventory to check what was stored before starting another action.</p>' : ""}</form></section>`;
  }
  function inventorySection(name, heading, rows, empty, action, label, enabled = true, explanation = "") {
    return `<section class="compute-inventory" aria-labelledby="compute-${name}"><header><h2 id="compute-${name}">${heading}</h2>${button(action, label, busy || reading || uncertain || !enabled)}</header>${explanation ? `<p class="compute-quiet">${explanation}</p>` : ""}${rows.length ? `<ul class="compute-list">${rows.join("")}</ul>` : `<p class="compute-empty">${empty}</p>`}</section>`;
  }
  function render() {
    if (!eligible() || !host) return;
    let body = "";
    if (inventory?.can_manage === true) {
      const gatewayRows = inventory.gateways.map(row => `<li class="compute-row"><header><strong>${e(row.name)}</strong><span class="compute-state">${e(stateText(row.state))}</span></header><p>${e(row.board)} · ${e(row.address)}:${e(row.port)}</p><details><summary>Public host key and binding</summary>${bindingHTML(row)}</details></li>`);
      const nodeRows = inventory.nodes.map(row => `<li class="compute-row"><header><strong>${e(row.name)}</strong><span class="compute-state">${e(stateText(row.state))}</span></header><p>${e(row.board)} · ${e(row.address)}:${e(row.port)} · ${e(row.interface)}</p><p>Gateway ${e(rowById("gateways", row.gateway_id)?.name || row.gateway_id)}</p><details><summary>Public host key and binding</summary>${bindingHTML(row)}</details></li>`);
      const scopeRows = inventory.scopes.map(row => `<li class="compute-row"><header><strong>${e(row.context?.project)} · ${e(row.context?.gateway)}</strong><span class="compute-state">${row.revoked ? "Revoked" : Number(row.expires_at) <= now() ? "Expired" : "Passive grant recorded"}</span></header><p>${e((row.cidrs || []).join(", "))} · ${e((row.interfaces || []).join(", "))}</p><p>Methods: ${e((row.methods || []).join(", "))} · ${taskHTML({ task_number: row.context?.task }) || `Task ${e(row.context?.task)}`} · Actor ${e(row.context?.agent)}</p><p>Expires: ${e(Number.isFinite(Number(row.expires_at)) ? new Date(Number(row.expires_at) * 1000).toLocaleString() : "Unknown")}</p><details><summary>Scope limits and context</summary><dl><dt>Scope</dt><dd>${e(row.id)}</dd><dt>User</dt><dd>${e(row.context?.user)}</dd>${limitsHTML(row.limits)}</dl></details>${!row.revoked && Number(row.expires_at) > now() ? button("revoke", "Revoke passive scope", busy || reading || uncertain, row.id) : ""}</li>`);
      body = `${editorHTML()}<div class="compute-inventories">`
        + inventorySection("gateways", "Gateways", gatewayRows, "No gateways. Add an existing project’s numeric management endpoint and explicitly confirm its full public host key.", "gateway", "Add gateway", projects.length > 0, boardError || (!projects.length ? "Create a project before adding a gateway." : ""))
        + inventorySection("nodes", "Targets", nodeRows, "No targets enrolled. Enrollment uses an existing gateway and a separately confirmed public host key.", "node", "Enroll target", inventory.gateways.length > 0, !inventory.gateways.length ? "Add a gateway before enrolling a target." : "")
        + inventorySection("scopes", "Passive scopes", scopeRows, "No passive scopes granted. Each scope binds exact networks, interfaces, actor, task, bounds and expiry.", "scope", "Grant scope", inventory.gateways.length > 0)
        + inventorySection("scripts", "Workspace scripts", scripts.map(row => `<li class="compute-row"><header><strong>${e(row.id)}</strong>${taskHTML(row)}</header><p>Target ${e(row.node_id)} · Bundle SHA256 <code>${e(row.digest)}</code></p>${button("inspect-script", "Inspect source and manifest", busy || reading || uncertain, row.id)}</li>`), "No immutable script bundles saved.", "script", "Add Python script", inventory.nodes.length > 0 && !scriptsError, scriptsError || (inventory.capabilities?.scripts === true ? "Scripts use the independently verified confined target runtime and require exact approval." : "Script execution is unavailable until the target runtime and confinement are independently configured and verified."))
        + inventorySection("discoveries", "Passive observations", discoveries.map(row => `<li class="compute-row"><header><strong>${e(row.board)} · ${taskHTML(row)}</strong><span class="compute-state">${e(stateText(row.state))}</span></header><p>Scope ${e(row.scope_id)} · ${e(row.reason || "")}</p><details><summary>${e(row.observations?.length || 0)} observations and usage</summary><pre>${e(JSON.stringify({ observations: row.observations || [], usage: row.usage || {} }, null, 2))}</pre></details>${["queued", "running"].includes(row.state) ? button("discovery-cancel", "Request cancellation", busy || reading || uncertain, row.id) : ""}${row.state === "uncertain" ? "<p>Outcome unknown. Capacity remains reserved; this attempt will not automatically repeat.</p>" : ""}</li>`), "No passive observations collected.", "discovery", "Collect observations", discoveryAvailable && inventory.scopes.some(row => !row.revoked && Number(row.expires_at) > now()), discoveryError || (discoveryAvailable ? "Collection reads approved gateway caches in the background. Reload to check progress." : "Passive collection is unavailable until its gateway and admission channel are configured."))
        + inventorySection("executions", "Execution proposals", inventory.executions.map(row => computeExecutionHTML(row, { busy: busy || reading || uncertain })), "No execution proposals. The fixed system information operation requires a target and a canonical task.", "proposal", "Propose system info", inventory.nodes.length > 0)
        + '</div><section class="compute-capabilities" aria-labelledby="compute-capabilities"><h2 id="compute-capabilities">Remote capabilities</h2><div><button class="btn" disabled aria-describedby="compute-transport-reason">Dispatch</button><p id="compute-transport-reason">Remote dispatch is unavailable in this control surface. Proposals and approvals do not establish verified transport.</p></div><div><button class="btn" disabled aria-describedby="compute-passive-reason">Collect observations</button><p id="compute-passive-reason">Collection availability is shown with Passive observations. A scope grant never enrolls devices.</p></div><div><button class="btn" disabled aria-describedby="compute-script-reason">Scripts</button><p id="compute-script-reason">Script availability is shown with Workspace scripts. Saving a bundle never runs it.</p></div></section>';
    } else if (inventory) body = '<p>Compute management requires the current workspace owner.</p>';
    host.innerHTML = `<div class="compute"><header class="compute-intro"><p>Set up pinned gateways and targets, grant bounded passive scopes, and approve exact task proposals.</p>${button("refresh", reading ? "Loading…" : "Reload inventory", busy || reading)}</header><p class="compute-notice" role="status">${e(notice || (reading ? "Loading Compute inventory…" : ""))}</p>${!editor && error ? `<p class="compute-error" role="alert">${e(error)}</p>` : ""}${body}</div>`;
  }
  async function refresh() {
    if (!eligible() || reading || busy) return;
    const page = epoch, read = ++revision;
    reading = true; error = ""; render();
    const results = await Promise.allSettled([request(BASE), request("/api/boards"), request(`${BASE}/discoveries`), request(`${BASE}/scripts`)]);
    if (!valid(page) || read !== revision) return;
    reading = false;
    const [data, boards, passive, bundles] = results;
    if (data.status === "rejected") { if (!denied(data.reason)) { error = errorText(data.reason); render(); } return; }
    // A failed board read does not replace the successful Compute inventories.
    if (boards.status === "rejected" && boards.reason?.status === 401) { stop(); return; }
    if (passive.status === "rejected" && [401, 403].includes(passive.reason?.status)) { denied(passive.reason); return; }
    discoveries = passive.status === "fulfilled" ? passive.value.discoveries : [];
    discoveryAvailable = passive.status === "fulfilled" && passive.value.capabilities?.passive_discovery === true;
    discoveryError = passive.status === "rejected" ? "Passive observations could not be loaded. Reload before collecting." : "";
    if (bundles.status === "rejected" && [401, 403].includes(bundles.reason?.status)) { denied(bundles.reason); return; }
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
  function open(kind, id) {
    if (!manageable() || reading || busy || uncertain) return;
    if (kind === "gateway" && !projects.length || ["node", "scope"].includes(kind) && !inventory.gateways.length || kind === "proposal" && !inventory.nodes.length) return;
    if (kind === "approve") {
      const row = rowById("executions", id);
      if (row?.state !== "awaiting_approval" || !digestOK(row.digest) || !rowById("nodes", row.node_id) || !rowById("gateways", row.gateway_id)) return;
      approvalDigest = row.digest;
    }
    if (kind === "discovery" && !discoveryAvailable) return;
    if (kind === "script" && (!inventory.nodes.length || scriptsError)) return;
    if (kind === "script-proposal" && !scripts.some(row => same(row.id, id))) return;
    editor = kind; selected = id; values = ["node", "gateway"].includes(kind) ? { port: "22" } : kind === "script" ? { arguments: "[]" } : kind === "script-proposal" ? { node_id: scripts.find(row => same(row.id, id)).node_id, task_number: scripts.find(row => same(row.id, id)).task_number, bundle_id: id } : {}; error = ""; notice = ""; uncertain = false; attempt = null;
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
      notice = collection === "scripts" ? "Immutable script saved. Inspect its source and manifest before proposing it for approval." : collection === "discoveries" ? `Passive collection: ${stateText(row.state)}. Reload to check progress. Cancellation does not confirm remote termination.` : ["approve", "cancel", "proposal", "script-proposal"].includes(kind) ? `Server state: ${executionStatus(row)}.` : kind === "revoke" ? "Passive scope revoked." : kind === "scope" ? "Passive scope recorded. No observations collected." : "Saved pending verification. No connection verified.";
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
      const payload = computePayload(kind, values, { now: now(), requestId: attempt?.id });
      if (kind === "approve" && (payload.expected_digest !== approvalDigest || rowById("executions", selected)?.digest !== approvalDigest || rowById("executions", selected)?.state !== "awaiting_approval")) throw new Error("The digest does not match the current proposal. Inspect and confirm it again.");
      const collection = { gateway: "gateways", node: "nodes", scope: "scopes", proposal: "executions", approve: "executions", discovery: "discoveries", script: "scripts", "script-proposal": "executions" }[kind];
      void mutate(`${BASE}/${collection}${kind === "approve" ? `/${enc(selected)}/approve` : ""}`, payload, collection, kind);
    } catch (failure) { error = failure.message; render(); }
  }
  function click(event) {
    const target = event.target.closest("[data-compute-action]");
    if (!target) return;
    const { computeAction: action, id } = target.dataset;
    if (target.disabled || !eligible()) return;
    if (action === "inspect-script") { void inspectScript(id); return; }
    if (action === "refresh") { void refresh(); return; }
    if (busy) return;
    if (action === "close") { editor = ""; values = {}; selected = null; approvalDigest = ""; error = ""; attempt = null; if (uncertain) notice = "Receipt unavailable. Reload inventory before starting another action."; render(); return; }
    if (uncertain) return;
    if (action === "discovery-cancel") {
      if (discoveries.some(row => same(row.id, id) && ["queued", "running"].includes(row.state))) void mutate(`${BASE}/discoveries/${enc(id)}/cancel`, {}, "discoveries", "discovery-cancel");
    } else if (action === "cancel") {
      if (cancellable(rowById("executions", id) || {})) void mutate(`${BASE}/executions/${enc(id)}/cancel`, {}, "executions", "cancel");
    } else if (action === "revoke") {
      const row = rowById("scopes", id);
      if (row && !row.revoked && Number(row.expires_at) > now()) void mutate(`${BASE}/scopes/${enc(id)}/revoke`, {}, "scopes", "revoke");
    } else if (["gateway", "node", "scope", "proposal", "approve", "discovery", "script", "script-proposal"].includes(action)) open(action, id);
  }
  function input(event) {
    const field = event.target;
    if (!editor || !eligible() || !field.name) return;
    values[field.name] = field.type === "checkbox" ? field.checked : field.value;
    if (["gateway", "node"].includes(editor) && field.name !== "pin_confirmed") {
      values.pin_confirmed = false;
      const confirmation = host.querySelector('[name="pin_confirmed"]');
      if (confirmation) confirmation.checked = false;
    }
  }
  const routeChanged = () => { if (live && !eligible()) stop(); };
  function stop() {
    live = false; epoch++; revision++; abort();
    host?.removeEventListener("click", click); host?.removeEventListener("submit", submit); host?.removeEventListener("input", input); host?.removeEventListener("change", input);
    events?.removeEventListener("hashchange", routeChanged); events?.removeEventListener("pagehide", stop); doc?.removeEventListener("session-expired", stop);
    if (host) host.innerHTML = "";
    host = null; session = null; workspace = null; identity = null; inventory = null; projects = []; editor = ""; values = {}; selected = null; approvalDigest = ""; attempt = null;
    discoveries = []; discoveryAvailable = false; discoveryError = "";
    scripts = []; scriptDetail = null; scriptsError = "";
    busy = false; reading = false; uncertain = false; notice = ""; error = ""; boardError = "";
  }
  function show() {
    stop();
    session = current();
    if (!session?.workspace || !session?.actor || route() !== "#/compute") return;
    workspace = session.workspace; identity = session.actor; host = mount();
    if (!host) return;
    live = true; epoch++;
    host.addEventListener("click", click); host.addEventListener("submit", submit); host.addEventListener("input", input); host.addEventListener("change", input);
    events?.addEventListener("hashchange", routeChanged); events?.addEventListener("pagehide", stop); doc?.addEventListener("session-expired", stop);
    render(); void refresh();
  }
  return { show, stop, refresh };
}
