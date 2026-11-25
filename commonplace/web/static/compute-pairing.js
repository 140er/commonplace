import { escape as e } from "./api.js";
import { wingLabel } from "./wing-label.js";

// Add a machine on the Compute screen (docs/system/compute-enroll.md). One shared module serves the Classic screen and
// the Deck Management tab: createCompute (compute.js) mounts the section and forwards clicks, inputs, submits and keys.
// The server owns every sentence about a state, a failed step and a refusal (GET /api/compute/pairings/options); this
// module only lays them out. Nothing here claims that anything runs: "Set up and checked" is said exactly as the server
// says it, and the page states plainly what this version does not do. The one-time command is held in memory for the
// pairing just created and is never written to storage, the address bar or a log.

const BASE = "/api/compute/pairings";
const SETTINGS = "/api/runners/install-settings";
const enc = encodeURIComponent;
export const POLL_MS = 3000;
// States the machine can still move out of by itself or by the owner: the page keeps checking only these.
export const LIVE = new Set(["waiting", "connected", "approved", "failed"]);
export const CLOSED = new Set(["expired", "cancelled", "declined", "abandoned", "revoked"]);
const STEP_LABELS = { prerequisites: "Checks on the machine", files: "Runner files installed", service: "Background service started", check: "Final check" };
const STEP_STATE = { pending: "Waiting", ok: "Done", failed: "Failed", not_run: "Not run" };
const MODES = {
  "approval-required": "Asks before each action",
  "auto-accept-edits": "Accepts file edits, asks before other actions",
  auto: "Runs without asking",
  "full-access": "Full access, runs without asking",
};
const QUIET_MODES = new Set(["auto", "full-access"]);
const AUTO_WARNING = "In a later version this setting would let agents on the machine run without asking before each action; in this version nothing runs on it. Only choose it for a machine you would trust with every project listed.";
const MEMBER_INTRO = "This is a read-only view of the machines being added. Only the workspace owner adds a machine and approves it.";
const OWNER_INTRO = "A runner machine is a computer for Coworkers. To add one you copy one command, run it on the machine, and approve it here. Nothing is copied by hand and no SSH keys are involved. Once it is added it is also listed under Runner machines in Admin, with the same state; cloud workspaces are below.";
const NO_OFF_SWITCH = "There is no switch here to turn machine setup off.";
const FORM_NOTE = "These settings are recorded for later; nothing uses them in this version.";
const GRANTS_NOTE = "Approving records the access listed above; nothing uses it in this version.";
const RUN_STEPS = ["Open a terminal on that machine. On Ubuntu: press the Super key, type Terminal and press Enter.", "Paste the command below (Ctrl+Shift+V) and press Enter. Do not type sudo in front of it.", "Wait for the steps below to turn to Done. This page updates by itself."];
const WAIT_FINGERPRINT = "Waiting for the machine to print its fingerprint.";
const CANNOT_RUN_YET = "Coworkers do not run on it in this version.";
export const LIMITS_NOTE = "This version does not install the runner program itself (the command checks that it is there). It only checks a machine: Coworkers do not run on it in this version.";
const PROGRAM_LIMITS_NOTE = "The command installs this site's pinned runner program and dependencies. Setup still only checks a machine: Coworkers do not run on it in this version.";
const LOAD_ERROR = "Machines being added could not be loaded. Reload to try again.";
const NO_ANSWER = "No answer from the server, so nothing is confirmed. Reload to check what happened before trying again.";
const MEMBER_NOTE = "Only the workspace owner can add a machine.";
const COMMAND_ONCE = "This command holds a one-time code. It stops working after one use or when its time is up, and this page shows it only now. Your terminal may keep this line in its history; that is harmless once the code has been used or has run out.";
const GONE_COMMAND = "The command was shown once, when it was made, and is not kept. Set it up again for a new one.";
const LABEL = /^[a-z0-9][a-z0-9._-]{0,39}$/;
const PROJECT = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const TOPIC = /^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$/;
const MACHINE_ID = /^mach_[0-9a-f]{16}$/;
// Field-level wording for the refusals the form can show; anything else falls back to the server's own sentence.
const FIELD_TEXT = {
  label: { invalid: "Use lowercase letters, numbers, dots and dashes, up to 40 characters. Example: build-box.", required: "Give the machine a name.", label_in_use: "A machine or a waiting setup already uses this name. Choose another name, or cancel the setup that uses it." },
  projects: { required: "Name at least one project the machine may work on.", invalid: "Use project names of lowercase letters, numbers, dots and dashes, separated by commas." },
  topics: { required: "Name at least one topic.", invalid: "Use topic names of letters, numbers, spaces, dots and dashes, separated by commas." },
  wings: { required: "Choose at least one wing the machine may read.", invalid: "One of the chosen wings does not exist or cannot be granted. Reload and choose again." },
  copy_from: { not_found: "That machine is no longer available to copy from. Choose another, or start from scratch." },
  requesters: { invalid: "Someone who may start work on the copied machine can no longer do so. Add this machine without copying, or fix that machine first." },
  kind: { kind_unavailable: "Script targets cannot be set up from the app yet. Choose a runner machine.", invalid: "Choose a runner machine." },
};
const FIELD_ORDER = ["label", "copy_from", "requesters", "projects", "topics", "wings", "max_runtime_mode", "max_in_flight"];
const FIELD_LABEL = { label: "Name", copy_from: "Same settings as", requesters: "Same settings as", projects: "Projects", topics: "Topics", wings: "Wings", max_runtime_mode: "What it may do without asking", max_in_flight: "Most cards at a time" };
const FIELD_CONTROL = { label: "pr_label", copy_from: "pr_copy_from", requesters: "pr_copy_from", projects: "pr_projects", topics: "pr_topics", wings: "pr_wings", max_runtime_mode: "pr_mode", max_in_flight: "pr_max_in_flight" };

export const listOf = text => [...new Set(String(text || "").split(",").map(part => part.trim()).filter(Boolean))];
/** The local clock time of an ISO stamp the server wrote, or "" when it is not one. */
export const clockText = stamp => { const t = Date.parse(stamp || ""); return Number.isFinite(t) ? new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : ""; };
const quoted = name => `“${name}”`;

/** What the form can say is wrong before anything is sent. The server checks again. */
export function validate(values, options) {
  const errors = {};
  const copying = Boolean(values.pr_copy_from);
  if (!String(values.pr_label || "").trim()) errors.label = FIELD_TEXT.label.required;
  else if (!LABEL.test(values.pr_label.trim()) || MACHINE_ID.test(values.pr_label.trim())) errors.label = FIELD_TEXT.label.invalid;
  const projects = listOf(values.pr_projects), topics = listOf(values.pr_topics), wings = values.wings || [];
  if (!projects.length) { if (!copying) errors.projects = FIELD_TEXT.projects.required; } else if (projects.length > 32 || projects.some(p => !PROJECT.test(p))) errors.projects = FIELD_TEXT.projects.invalid;
  if (!topics.length) { if (!copying) errors.topics = FIELD_TEXT.topics.required; } else if (topics.length > 32 || topics.some(t => !TOPIC.test(t))) errors.topics = FIELD_TEXT.topics.invalid;
  if (!wings.length && !copying) errors.wings = FIELD_TEXT.wings.required;
  return errors;
}

export function createPayload(values, requestId) {
  const body = { request_id: requestId, kind: "runner", label: String(values.pr_label || "").trim(), max_runtime_mode: values.pr_mode || "approval-required", max_in_flight: Number(values.pr_max_in_flight || 1) };
  if (values.pr_copy_from) body.copy_from = values.pr_copy_from;
  const projects = listOf(values.pr_projects), topics = listOf(values.pr_topics);
  if (projects.length) body.projects = projects;
  if (topics.length) body.topics = topics;
  if ((values.wings || []).length) body.wings = values.wings;
  // Set up again keeps who may start work on the machine (the form has no control for it, so the values carry it).
  if (!values.pr_copy_from && (values.requesters || []).length) body.requesters = values.requesters;
  return body;
}

export const blankValues = (retry = null) => ({
  pr_kind: "runner", pr_label: retry?.label || "", pr_copy_from: "", pr_projects: (retry?.projects || []).join(", "), pr_topics: (retry?.topics || []).join(", "),
  wings: [...(retry?.wings || [])], requesters: [...(retry?.requesters || [])], pr_mode: retry?.max_runtime_mode || "approval-required", pr_max_in_flight: String(retry?.max_in_flight || 1),
});

/** The plain-words sheet the owner approves: who the machine is and what it may do. */
export const finished = item => (item.steps || []).length > 0 && item.steps.every(step => step.state === "ok");
export function sheetHTML(item) {
  const grant = item.grant || {}, line = list => list?.length ? e(list.join(", ")) : "None";
  const who = grant.requesters?.length ? `${grant.requesters.length} trusted ${grant.requesters.length === 1 ? "agent" : "agents"}` : "nobody yet";
  const count = grant.max_in_flight ?? 1;
  const fingerprint = finished(item) && item.fingerprint
    ? `<code class="compute-pr-fingerprint">${e(item.fingerprint)}</code><small>The machine printed these same groups. If they differ, this is not your machine.</small>`
    : `<span data-pr-fingerprint-wait>${e(WAIT_FINGERPRINT)}</span>`;
  return `<dl class="compute-pr-sheet"><dt>Name</dt><dd>${e(item.label)}</dd>`
    + `<dt>Fingerprint</dt><dd>${fingerprint}</dd>`
    + `<dt>May read</dt><dd>${grant.wings?.length ? e(grant.wings.map(w => wingLabel(w)).join(", ")) : "Nothing"}</dd>`
    + `<dt>Projects</dt><dd>${line(grant.projects)}</dd><dt>Topics</dt><dd>${line(grant.topics)}</dd>`
    + `<dt>Who can start work on it</dt><dd>${e(who)}</dd>`
    + `<dt>What it may do without asking</dt><dd>${e(MODES[grant.max_runtime_mode] || grant.max_runtime_mode || "")}</dd>`
    + `<dt>Works on at most</dt><dd>${e(count)} ${count === 1 ? "card" : "cards"} at a time</dd></dl>`;
}

/** One mapping for owner and member: a step that never ran in a closed or failed setup is "Not run", not "Waiting". */
export const stepStateOf = (item, step) => step.state === "pending" && (CLOSED.has(item.state) || item.state === "failed" || item.stalled === true) ? "not_run" : step.state;
export function stepsHTML(item) {
  return `<ol class="compute-pr-steps" aria-label="Setup steps on the machine">${(item.steps || []).map(step => {
    const state = stepStateOf(item, step);
    return `<li data-state="${e(state)}"><span class="compute-pr-step-name">${e(STEP_LABELS[step.step] || step.step)}</span> <span class="compute-pr-step-state">${e(STEP_STATE[state] || state)}</span></li>`;
  }).join("")}</ol>`;
}

export function causeHTML(item) {
  const cause = item.cause;
  if (!cause) return "";
  return `<div class="compute-pr-cause"><p><strong>${e(STEP_LABELS[cause.step] || cause.step)} failed.</strong> ${e(cause.text)}</p><p>${e(cause.next)}</p></div>`;
}

export function createPairing(ctx) {
  let list = null, total = 0, options = null, setupEnabled = false, loadError = "", loading = false, serverTime = "", pollError = false;
  let mode = "", selected = null, setup = null, values = {}, errors = {}, formError = "", busy = false, confirm = "", copyNote = "", notice = "", requestId = "";
  let timer = null, lastSaid = "", machinesKnown = null;
  // The state each open pairing was last seen in, so a change is said once whichever read found it (a check or a reload).
  const observed = new Map();
  const find = id => list?.find(item => item.id === id);
  const manage = () => Array.isArray(options?.wings);
  const text = (group, key, fallback) => options?.[group]?.[key]?.text || fallback;
  const nextOf = (group, key) => options?.[group]?.[key]?.next || "";

  function say(value) {
    notice = value;
    const node = ctx.host()?.querySelector?.("[data-pr-notice]");
    if (node && node.textContent !== value) node.textContent = value;
  }
  // The persistent status line speaks only when the state a person is watching changes, never on every check.
  function note(item) {
    if (!item || item.id !== selected || mode !== "detail") return;
    const before = observed.get(item.id);
    observed.set(item.id, item.state);
    if (before !== undefined && before !== item.state) announce(item);
  }
  function announce(item) {
    const line = item ? `${item.label}: ${item.state_label}. ${item.next}` : "";
    if (line !== lastSaid) { lastSaid = line; say(line); }
  }

  function topHTML() {
    const add = manage() && setupEnabled && !mode && list !== null && !busy ? '<button type="button" class="btn primary" data-pr-action="add">Add a machine</button>' : "";
    const off = manage() && setupEnabled ? ` ${NO_OFF_SWITCH}` : "";
    return `<header><h2 id="compute-pairing" tabindex="-1">Add a machine</h2>${add}</header>`
      + `<p class="compute-plain">${e(manage() ? OWNER_INTRO : MEMBER_INTRO)}</p>`
      + `<p class="compute-plain" data-pr-limits>${e((options?.program_delivery ? PROGRAM_LIMITS_NOTE : LIMITS_NOTE) + off)}</p>`;
  }

  function setupOffHTML() {
    if (!manage()) return `<p class="compute-empty">Machine setup is off for this workspace. Ask the workspace owner to turn it on.</p>`;
    return '<p class="compute-empty">Machine setup is off for this workspace, so a one-time code cannot be made yet.</p>'
      + '<p class="compute-plain">Turning it on only lets you add machines with a code you make here. A machine added this way can be removed here again.</p>'
      + `<div class="compute-actions"><button type="button" class="btn primary" data-pr-action="enable"${busy ? ' aria-disabled="true"' : ""}>Turn on machine setup</button></div>`;
  }

  const err = key => `<span class="compute-ws-error compute-pr-error" data-pr-error="${key}" role="status">${e(errors[key] || "")}</span>`;
  const invalid = key => errors[key] ? ' aria-invalid="true"' : "";
  const required = ' <span class="compute-req" aria-hidden="true">*</span>';

  function summaryHTML() {
    const keys = FIELD_ORDER.filter(key => errors[key]);
    if (!keys.length && !formError) return "";
    const links = keys.map(key => `<li><a href="#${FIELD_CONTROL[key]}" data-pr-jump="${FIELD_CONTROL[key]}">${e(FIELD_LABEL[key])}: ${e(errors[key])}</a></li>`).join("");
    return `<p>${e(formError || (keys.length === 1 ? "One thing needs fixing before the command can be made." : `${keys.length} things need fixing before the command can be made.`))}</p>${links ? `<ul>${links}</ul>` : ""}`;
  }

  function formHTML() {
    const kinds = options?.kinds || [];
    const runner = kinds.find(k => k.id === "runner"), target = kinds.find(k => k.id === "target");
    const copying = values.pr_copy_from, source = options?.copy_from?.find(m => m.id === values.pr_copy_from);
    const optional = copying ? ' <span class="compute-quiet">(optional: copied from the machine above)</span>' : required;
    const wings = options?.wings || [];
    const wingBoxes = wings.length
      ? wings.map(wing => `<label class="compute-check"><input type="checkbox" name="wings" value="${e(wing)}" data-pr-control="pr_wings"${(values.wings || []).includes(wing) ? " checked" : ""}><span>${e(wingLabel(wing))}</span></label>`).join("")
      : '<p class="compute-quiet">No memory wings exist yet, so there is nothing to grant. <a href="#/memory">Open Memory</a> and save some notes first.</p>';
    return `<form class="compute-editor compute-pr-form" data-pr-form novalidate aria-labelledby="compute-pr-heading">`
      + '<h3 id="compute-pr-heading" tabindex="-1">What kind of machine?</h3>'
      + `<fieldset class="compute-pr-kinds"><legend class="compute-ws-legend">Kind of machine</legend>`
      + `<label class="compute-check"><input type="radio" name="pr_kind" value="runner" checked><span><strong>${e(runner?.label || "Runner machine")}</strong><small>${e(runner?.description || "A machine that runs Coworkers.")}</small></span></label>`
      + `<label class="compute-check"><input type="radio" name="pr_kind" value="target" disabled aria-describedby="pr-target-note"><span><strong>${e(target?.label || "Script target")}</strong><small id="pr-target-note">${e(target?.description || "A machine that runs approved scripts over SSH.")} Not available yet: ${e(text("reasons", "kind_unavailable", "Script targets cannot be set up from the app yet.").replace(/^Script targets cannot be set up from the app yet\.?$/, "it cannot be set up from the app yet."))}</small></span></label></fieldset>`
      + `<p class="compute-plain" data-pr-form-note>${e(FORM_NOTE)}</p>`
      + `<div class="compute-summary compute-ws-summary" data-pr-summary role="alert" tabindex="-1">${summaryHTML()}</div>`
      + `<div class="compute-fields">`
      + `<div class="compute-field"><label for="pr_label">Name${required}</label><input id="pr_label" name="pr_label" value="${e(values.pr_label)}" maxlength="40" autocomplete="off" autocapitalize="none" spellcheck="false" data-pr-control="pr_label" aria-describedby="pr-label-help pr-error-label"${invalid("label")}><small id="pr-label-help">Lowercase letters, numbers, dots and dashes. Example: build-box.</small><span id="pr-error-label">${err("label")}</span></div>`
      + `<div class="compute-field"><label for="pr_copy_from">Same settings as</label><select id="pr_copy_from" name="pr_copy_from" data-pr-control="pr_copy_from"${invalid("copy_from")}><option value="">Start from scratch</option>${(options?.copy_from || []).map(m => `<option value="${e(m.id)}"${m.id === values.pr_copy_from ? " selected" : ""}>${e(m.label)}</option>`).join("")}</select><small>${options?.copy_from?.length ? "Copies projects, topics, wings, what it may do without asking and who may start work. No sign-ins and no approvals are copied." : "No machine exists yet to copy from."}</small>${err("copy_from")}</div>`
      + (source ? `<p class="compute-quiet compute-wide" data-pr-source-note>Copied from ${e(quoted(source.label))}: projects ${e(source.projects.join(", "))}; topics ${e(source.topics.join(", "))}; ${source.requesters.length} trusted ${source.requesters.length === 1 ? "agent" : "agents"} may start work.</p>` : "")
      + `<div class="compute-field"><label for="pr_projects">Projects${optional}</label><input id="pr_projects" name="pr_projects" value="${e(values.pr_projects)}" autocomplete="off" spellcheck="false" data-pr-control="pr_projects" aria-describedby="pr-projects-help pr-error-projects"${invalid("projects")}><small id="pr-projects-help">The projects the machine may work on, separated by commas.</small><span id="pr-error-projects">${err("projects")}</span></div>`
      + `<div class="compute-field"><label for="pr_topics">Topics${optional}</label><input id="pr_topics" name="pr_topics" value="${e(values.pr_topics)}" autocomplete="off" spellcheck="false" data-pr-control="pr_topics" aria-describedby="pr-topics-help pr-error-topics"${invalid("topics")}><small id="pr-topics-help">The topics its agents answer to, separated by commas.</small><span id="pr-error-topics">${err("topics")}</span></div>`
      + `<fieldset class="compute-wide compute-pr-wings" id="pr_wings" tabindex="-1"><legend class="compute-label">Wings it may read${copying ? ' <span class="compute-quiet">(optional: copied)</span>' : required}</legend><small>A wing is a part of your memory. The machine reads only the wings you tick.</small>${wingBoxes}${err("wings")}</fieldset>`
      + `<div class="compute-field compute-wide"><label for="pr_mode">What it may do without asking</label><select id="pr_mode" name="pr_mode" data-pr-control="pr_mode" aria-describedby="pr-mode-warning">${Object.entries(MODES).map(([key, label]) => `<option value="${key}"${values.pr_mode === key ? " selected" : ""}>${e(label)}</option>`).join("")}</select><small id="pr-mode-warning" data-pr-warning>${QUIET_MODES.has(values.pr_mode) ? e(AUTO_WARNING) : ""}</small></div>`
      + `<details class="compute-field compute-wide"><summary>More limits</summary><label for="pr_max_in_flight">Most cards at a time</label><select id="pr_max_in_flight" name="pr_max_in_flight" data-pr-control="pr_max_in_flight">${[1, 2, 3, 4].map(n => `<option value="${n}"${String(values.pr_max_in_flight) === String(n) ? " selected" : ""}>${n}</option>`).join("")}</select><small>The most cards the machine works on at the same time.</small></details>`
      + `</div><div class="compute-actions compute-pr-actions"><button type="submit" class="btn primary"${busy ? ' aria-disabled="true"' : ""}>${busy ? "Creating…" : "Create the command"}</button><button type="button" class="btn" data-pr-action="cancel"${busy ? ' aria-disabled="true"' : ""}>Cancel</button></div></form>`;
  }

  function commandHTML(item, clock = true) {
    if (!setup || setup.id !== item.id) return `<p class="compute-pr-gone" data-pr-gone>${e(GONE_COMMAND)}</p>`;
    return '<div class="compute-pr-command"><h4 id="compute-pr-command-title">Run this on the machine</h4>'
      + `<ol class="compute-pr-run">${RUN_STEPS.map(step => `<li>${e(step)}</li>`).join("")}</ol>`
      + `<pre id="compute-pr-command" role="region" tabindex="0" data-pr-command aria-labelledby="compute-pr-command-title">${e(setup.command)}</pre>`
      + `<div class="compute-actions"><button type="button" class="btn primary" data-pr-action="copy">Copy the command</button></div>`
      // The copy note, like the clock line, is written in place and is not part of what decides a repaint.
      + `<p class="compute-quiet" data-pr-copy-note role="status">${clock ? e(copyNote) : ""}</p>`
      + `<p class="compute-plain">${e(COMMAND_ONCE)} The code works until ${e(clockText(setup.expires_at) || "it expires")}.</p>`
      + '<p class="compute-plain">On a machine that does not already keep your services running after you log out, the command asks for one administrator confirmation. That is the only question it asks.</p></div>';
  }

  function actionsHTML(item) {
    // A member has no control over a machine, but is never stranded in a detail: the way back to the list is theirs too.
    if (!manage()) return '<div class="compute-actions compute-pr-actions"><button type="button" class="btn" data-pr-action="close">Back to the list</button></div>';
    if (confirm) {
      const decline = confirm === "decline";
      // The safe answer comes first and takes focus: a second Enter must never remove access.
      return `<div class="compute-ws-discard compute-pr-confirm" role="group" aria-labelledby="compute-pr-confirm-title"><h4 id="compute-pr-confirm-title">${decline ? "Is this not your machine?" : "Remove this machine?"}</h4>`
        + `<p>${decline ? "Its access is removed and nothing it holds works any more. You can set it up again." : "Its access is removed and its credentials stop working. You can set it up again with the same settings."}</p>`
        + `<div class="compute-actions"><button type="button" class="btn" data-pr-action="keep">Go back</button><button type="button" class="btn danger" data-pr-action="confirm-close"${busy ? ' aria-disabled="true"' : ""}>${busy ? "Removing…" : decline ? "Yes, this is not my machine" : "Yes, remove it"}</button></div></div>`;
    }
    const buttons = [], again = (label, primary) => `<button type="button" class="btn${primary ? " primary" : ""}" data-pr-action="again" data-id="${e(item.id)}">${label}</button>`;
    const blocked = item.stalled === true || item.state === "failed";
    if (item.state === "connected" && !blocked) {
      const ready = finished(item), off = busy || !ready;
      buttons.push(`<button type="button" class="btn primary" data-pr-action="approve"${off ? ' aria-disabled="true"' : ""}${ready ? "" : ' aria-describedby="compute-pr-approve-note"'}>${busy ? "Approving…" : "Approve this machine"}</button>`, '<button type="button" class="btn" data-pr-action="decline">This is not my machine</button>');
    } else if (item.state === "waiting") buttons.push('<button type="button" class="btn" data-pr-action="cancel-setup">Cancel this setup</button>', again("Make a new command (this one stops working)", false));
    else if (blocked && item.can_close) buttons.push(again("Set up again", true), '<button type="button" class="btn danger" data-pr-action="remove">Remove this machine</button>');
    else if (["approved", "ready", "offline"].includes(item.state) && item.can_close) buttons.push('<button type="button" class="btn danger" data-pr-action="remove">Remove this machine</button>');
    else if (item.retry) buttons.push(again("Set up again", true));
    buttons.push('<button type="button" class="btn" data-pr-action="close">Back to the list</button>');
    const note = item.state === "connected" && !blocked && !finished(item) ? `<p class="compute-quiet" id="compute-pr-approve-note" data-pr-approve-note>You can approve once the machine has finished its setup and printed its fingerprint. This page updates by itself.</p>` : "";
    const leave = item.state === "waiting" && setup && setup.id === item.id ? '<p class="compute-quiet" data-pr-leave>If you leave this page you need a new command: this one is not kept.</p>' : "";
    return `${note}<div class="compute-actions compute-pr-actions">${buttons.join("")}</div>${leave}`;
  }

  // The clock line changes on every check; it is written in place and never decides whether the section repaints.
  function clockText_(item) {
    // While checks fail the status line says so once, in words; this line only says how old what is shown is.
    const checked = serverTime ? `${pollError ? "Last checked" : "Checked"} at ${clockText(serverTime)}.` : "";
    const heard = item.last_report_at ? ` Last seen at ${clockText(item.last_report_at)}.` : "";
    return checked + heard;
  }
  function detailHTML(item, clock = true) {
    const owner = manage();
    const body = item.state === "waiting" && owner ? commandHTML(item, clock) : "";
    const sheet = item.state === "connected" && owner && !item.stalled ? `${confirm ? "" : "<h4>Approve this machine?</h4>"}${sheetHTML(item)}${confirm ? "" : `<p class="compute-plain" data-pr-grants>${e(GRANTS_NOTE)}</p>`}` : "";
    return `<article class="compute-ws-detail compute-pr-detail" data-pr-id="${e(item.id)}" data-state="${e(item.state)}" aria-labelledby="compute-pr-heading">`
      + `<header><h3 id="compute-pr-heading" tabindex="-1">${e(item.label)}</h3><span class="compute-state compute-pr-state" data-tone="${e(toneOf(item))}">${e(item.state_label)}</span></header>`
      + `<p class="compute-pr-sentence">${e(item.sentence)}</p><p class="compute-pr-next">${e(item.next)}</p>${causeHTML(item)}`
      // What the person acts on comes first (the command, or the sheet and its buttons); the steps and the clock follow.
      + `${body}${sheet}${actionsHTML(item)}${stepsHTML(item)}`
      + `<p class="compute-quiet" data-pr-checked>${clock ? e(clockText_(item)) : ""}</p></article>`;
  }

  const toneOf = item => item.stalled === true || item.state === "failed" ? "bad" : ["connected", "waiting", "approved", "offline"].includes(item.state) ? "wait" : item.state === "ready" ? "ok" : "quiet";

  function rowHTML(item) {
    return `<li class="compute-row compute-pr-row" data-pr-id="${e(item.id)}" data-state="${e(item.state)}"><header><strong class="compute-ws-name">${e(item.label)}</strong><span class="compute-state compute-pr-state" data-tone="${e(toneOf(item))}">${e(item.state_label)}</span></header>`
      // A settled machine's next step only repeats the note at the top of the section, so its row leaves it out.
      + `<p>${e(item.sentence)}</p>${item.state === "ready" ? `<p class="compute-pr-next">${e(CANNOT_RUN_YET)}</p>` : `<p class="compute-pr-next">${e(item.next)}</p>`}${item.last_report_at ? `<p class="compute-quiet">Last seen at ${e(clockText(item.last_report_at))}.</p>` : ""}`
      + `<div class="compute-actions"><button type="button" class="btn" data-pr-action="open" data-id="${e(item.id)}" aria-label="Open ${e(item.label)}">Open</button></div></li>`;
  }

  function inner(clock = true) {
    if (list === null && !loadError) return '<p class="compute-empty" role="status">Loading…</p>';
    if (list === null) return '<div class="compute-actions"><button type="button" class="btn" data-pr-action="reload">Reload</button></div>';
    if (!setupEnabled && !list.length) return setupOffHTML();
    let panel = "";
    const item = find(selected);
    if (mode === "form" && manage()) panel = formHTML();
    else if (mode === "detail" && item) panel = detailHTML(item, clock);
    const shown = list.filter(row => !(mode === "detail" && row.id === selected));
    const more = total > list.length ? `<p class="compute-quiet">Showing the newest ${list.length} of ${total}.</p>` : "";
    const rows = shown.length ? `${more}<ul class="compute-list compute-pr-list" role="list" aria-label="Machines being added">${shown.map(rowHTML).join("")}</ul>` : (mode ? "" : `<p class="compute-empty">${manage() ? "No machine has been added this way yet. Choose Add a machine to start." : "No machine has been added yet. " + MEMBER_NOTE}</p>`);
    const off = !setupEnabled && list.length ? setupOffHTML() : "";
    return `${off}${panel}${rows}`;
  }

  const problem = () => list === null ? loadError : "";
  // What the root last showed, without the clock line: a check that finds nothing new changes the clock and nothing else.
  let shownRoot = "";
  function html() {
    shownRoot = inner(false);
    return `<section class="compute-inventory compute-wide compute-pr" aria-labelledby="compute-pairing" data-pr><div data-pr-top>${topHTML()}</div>`
      + `<p class="compute-notice compute-ws-notice" data-pr-notice role="status">${e(notice)}</p>`
      + `<p class="compute-error compute-ws-problem" data-pr-problem role="alert">${e(problem())}</p><div data-pr-root>${inner()}</div></section>`;
  }
  function writeProblem() {
    const node = ctx.host()?.querySelector?.("[data-pr-problem]"), value = problem();
    if (node && node.textContent !== value) node.textContent = value;
  }

  /**
   * The whole Compute screen is rebuilt whenever the app refreshes it (every few seconds, and on its own events). The
   * section as it stood is put back in place of the fresh copy, so the command keeps its node, its selection and its
   * focus. It is put back only when the state it was painted from is the state html() just rebuilt from; if the state
   * moved in between, the fresh copy stays.
   */
  function carry() {
    const old = ctx.host()?.querySelector?.("[data-pr]"), painted = shownRoot, kept = selectionOf(old);
    return () => {
      const fresh = ctx.host()?.querySelector?.("[data-pr]");
      if (old && fresh && old !== fresh && painted === shownRoot && typeof fresh.replaceWith === "function") { fresh.replaceWith(old); reselect(old, kept); }
    };
  }
  // A node that is taken out of the page and put back loses its selection, so the part of the command that was selected
  // is noted by text offsets and selected again.
  function selectionOf(section) {
    try {
      const node = section?.querySelector?.("[data-pr-command]"), selection = ctx.doc?.getSelection?.();
      if (!node || !selection?.rangeCount) return null;
      const range = selection.getRangeAt(0);
      if (!node.contains(range.startContainer) || !node.contains(range.endContainer)) return null;
      const offset = (container, at) => { const probe = ctx.doc.createRange(); probe.selectNodeContents(node); probe.setEnd(container, at); return probe.toString().length; };
      return [offset(range.startContainer, range.startOffset), offset(range.endContainer, range.endOffset)];
    } catch { return null; }
  }
  function reselect(section, kept) {
    try {
      const text = section?.querySelector?.("[data-pr-command]")?.firstChild, selection = ctx.doc?.getSelection?.();
      if (!kept || !text || text.nodeType !== 3 || !selection) return;
      const range = ctx.doc.createRange();
      range.setStart(text, Math.min(kept[0], text.length)); range.setEnd(text, Math.min(kept[1], text.length));
      selection.removeAllRanges(); selection.addRange(range);
    } catch { /* the text stays selectable by hand */ }
  }

  // A repaint nobody asked focus for (a check that found a change) puts focus back where it was.
  function holdFocus() {
    const control = ctx.host()?.querySelector?.(".compute-pr :focus");
    if (typeof control?.matches !== "function") return () => {};
    const row = control.closest?.("[data-pr-id]");
    const selector = control.dataset?.prControl ? `[data-pr-control="${control.dataset.prControl}"]${control.type === "checkbox" ? `[value="${control.value}"]` : ""}`
      : control.dataset?.prAction ? `${row ? `[data-pr-id="${row.dataset.prId}"] ` : ""}[data-pr-action="${control.dataset.prAction}"]` : control.id ? `#${control.id}` : "";
    const caret = typeof control.selectionStart === "number" ? [control.selectionStart, control.selectionEnd] : null;
    return () => {
      const again = selector ? ctx.host()?.querySelector?.(selector) : null;
      // The control went away because the state moved on (Cancel this setup once the machine connected): focus goes to
      // the heading of what the person was looking at, never to the page.
      if (!again) { (ctx.host()?.querySelector?.("#compute-pr-heading") || ctx.host()?.querySelector?.("#compute-pairing"))?.focus?.({ preventScroll: true }); return; }
      again.focus?.({ preventScroll: true });
      if (caret) try { again.setSelectionRange?.(...caret); } catch { /* some inputs have no caret */ }
    };
  }
  // The clock line is written in place: a check that changed nothing must not rebuild the command (its selection and
  // focus would be lost) or the buttons under the pointer.
  function writeClock() {
    const item = find(selected), node = ctx.host()?.querySelector?.("[data-pr-checked]");
    const want = item ? clockText_(item) : "";
    if (node && node.textContent !== want) node.textContent = want;
    const note = ctx.host()?.querySelector?.("[data-pr-copy-note]");
    if (note && note.textContent !== copyNote) note.textContent = copyNote;
  }
  function paint(focus = "") {
    const host = ctx.host(), root = host?.querySelector?.("[data-pr-root]");
    // A machine appearing, being approved or removed changes the overview's rows and sentences: it reads again now
    // instead of showing "Nothing here yet" beside a machine until its own next poll.
    const known = list === null ? null : list.map(item => `${item.id}:${item.state}`).join();
    if (known !== null && known !== machinesKnown) { const first = machinesKnown === null; machinesKnown = known; if (!first) ctx.changed?.(); }
    const signature = inner(false);
    if (root && !focus && signature === shownRoot) { writeClock(); writeProblem(); return; }
    const restore = focus ? null : holdFocus();
    if (root) {
      const top = host.querySelector("[data-pr-top]"), next = topHTML();
      if (top && top.innerHTML !== next) top.innerHTML = next;
      root.innerHTML = inner(true);
      shownRoot = signature;
      writeProblem();
    } else ctx.rerender();
    restore?.();
    if (!focus) return;
    const scope = ctx.host();
    if (focus === "heading") { const heading = scope?.querySelector?.("#compute-pr-heading") || scope?.querySelector?.("#compute-pairing"); try { heading?.scrollIntoView?.({ block: "start", behavior: "instant" }); } catch { /* old engines take no options */ } heading?.focus?.({ preventScroll: true }); }
    else if (focus === "summary") scope?.querySelector?.("[data-pr-summary]")?.focus?.();
    else if (focus === "top") scope?.querySelector?.("#compute-pairing")?.focus?.();
    else scope?.querySelector?.(`[data-pr-action="${focus}"]`)?.focus?.();
  }

  // ---- reading ----
  async function load({ announceDone = false } = {}) {
    const page = ctx.epoch();
    if (loading) return;
    loading = true; loadError = "";
    const [listed, opts] = await Promise.allSettled([ctx.request(BASE), ctx.request(`${BASE}/options`)]);
    loading = false;
    if (!ctx.valid(page)) return;
    for (const result of [listed, opts]) if (result.status === "rejected" && result.reason?.status === 401 && ctx.denied(result.reason)) return;
    if (listed.status === "rejected" || !Array.isArray(listed.value?.pairings)) { list = null; loadError = LOAD_ERROR; }
    else {
      list = listed.value.pairings; total = Number.isInteger(listed.value.total) ? listed.value.total : list.length;
      setupEnabled = listed.value.setup_enabled === true; serverTime = listed.value.server_time || serverTime; pollError = false;
    }
    options = opts.status === "fulfilled" && opts.value?.kinds ? opts.value : null;
    if (mode === "detail" && !find(selected)) { mode = ""; selected = null; setup = null; say("That setup is no longer available."); }
    else note(find(selected));
    paint();
    schedule();
    if (announceDone && list !== null) say("Machines being added reloaded.");
    if (announceDone) ctx.host()?.querySelector?.(list === null ? '[data-pr-action="reload"]' : "#compute-pairing")?.focus?.();
  }

  // One pairing is checked while it can still change; the page never polls a settled one.
  function schedule() {
    if (timer !== null) { ctx.cancel(timer); timer = null; }
    const item = mode === "detail" ? find(selected) : null;
    if (!item || !LIVE.has(item.state) || list === null) return;
    timer = ctx.schedule(() => { timer = null; void check(); }, POLL_MS);
  }
  async function check() {
    const page = ctx.epoch(), id = selected;
    if (!id || mode !== "detail" || busy) { schedule(); return; }
    if (ctx.doc?.hidden) { schedule(); return; }
    let result;
    try { result = await ctx.request(`${BASE}/${enc(id)}`); }
    catch (failure) {
      if (!ctx.valid(page) || id !== selected) return;
      if (failure?.status === 401 && ctx.denied(failure)) return;
      if (failure?.status === 404) { mode = ""; selected = null; setup = null; paint(); say("That setup is no longer available."); return; }
      if (!pollError) { pollError = true; paint(); say(`Could not check just now. Showing what was true at ${clockText(serverTime)}; trying again.`); }
      schedule(); return;
    }
    if (!ctx.valid(page) || id !== selected || busy) return;
    const next = result?.pairing;
    if (!next || !list) { schedule(); return; }
    list = list.map(item => item.id === id ? next : item);
    serverTime = result.server_time || serverTime;
    if (pollError) { pollError = false; lastSaid = ""; say(""); } // a recovered check clears what the failed one said
    note(next);
    paint();
    schedule();
  }

  // ---- doing ----
  function openForm(retry = null) {
    if (busy || !manage() || !setupEnabled) return;
    mode = "form"; selected = null; setup = null; values = blankValues(retry); errors = {}; formError = ""; confirm = ""; copyNote = "";
    requestId = ctx.requestId(); say(""); paint("heading");
  }
  function openDetail(id) {
    if (busy || !find(id)) return;
    mode = "detail"; selected = id; confirm = ""; copyNote = ""; pollError = false; announce(null); observed.set(id, find(id).state);
    if (setup && setup.id !== id) setup = null;
    paint("heading"); schedule();
  }
  function close() {
    if (busy) return;
    mode = ""; selected = null; confirm = ""; errors = {}; formError = ""; copyNote = ""; setup = null; values = {};
    if (timer !== null) { ctx.cancel(timer); timer = null; }
    // What was said about the pairing just left is stale on the list: the status line is cleared, and a later change is said afresh.
    lastSaid = ""; say("");
    paint("top");
  }
  const failText = failure => {
    const payload = failure?.payload || {};
    if (!failure?.status) return NO_ANSWER;
    const reason = payload.reason, entry = options?.reasons?.[reason];
    return entry ? `${entry.text} ${entry.next}` : (payload.error === "owner_required" ? MEMBER_NOTE : "The server refused this. Reload and try again.");
  };

  async function enable() {
    if (busy) return;
    const page = ctx.epoch();
    busy = true; say("Turning on machine setup…"); paint();
    try { await ctx.request(SETTINGS, { method: "PUT", body: { enabled: true } }); }
    catch (failure) {
      if (!ctx.valid(page)) return;
      busy = false; if (failure?.status === 401 && ctx.denied(failure)) return;
      formError = ""; say(failure?.status ? "Machine setup could not be turned on. Reload and try again." : NO_ANSWER); paint("enable"); return;
    }
    if (!ctx.valid(page)) return;
    busy = false; await load(); say("Machine setup is on. You can add a machine now."); ctx.host()?.querySelector?.('[data-pr-action="add"]')?.focus?.();
  }

  async function create() {
    if (busy) return;
    errors = validate(values, options); formError = "";
    if (Object.keys(errors).length) { paint("summary"); return; }
    const page = ctx.epoch();
    busy = true; say("Creating the command…"); paint();
    let result;
    try { result = await ctx.request(BASE, { method: "POST", body: createPayload(values, requestId) }); }
    catch (failure) {
      if (!ctx.valid(page)) return;
      busy = false; if (failure?.status === 401 && ctx.denied(failure)) return;
      const payload = failure?.payload || {}, key = payload.field;
      if (key && FIELD_ORDER.includes(key)) errors = { [key]: FIELD_TEXT[key]?.[payload.reason] || failText(failure) }; else formError = failText(failure);
      say(""); paint("summary"); return;
    }
    if (!ctx.valid(page)) return;
    busy = false;
    const item = result.pairing;
    list = [item, ...(list || []).filter(row => row.id !== item.id)]; total = Math.max(total, list.length);
    mode = "detail"; selected = item.id; errors = {}; formError = "";
    setup = result.setup ? { id: item.id, command: result.setup.command, expires_at: result.setup.expires_at } : null;
    lastSaid = ""; observed.set(item.id, item.state); say(result.setup ? "The command is ready. Copy it and run it on the machine." : "This setup already exists. Its command was shown once and is not kept.");
    paint("heading"); schedule();
  }

  async function act(path, body, { done, item }) {
    if (busy) return;
    const page = ctx.epoch(), id = item.id;
    busy = true; paint();
    try {
      const result = await ctx.request(`${BASE}/${enc(id)}/${path}`, { method: "POST", body: { request_id: ctx.requestId(), expected_version: item.version, ...body } });
      if (!ctx.valid(page)) return;
      busy = false; confirm = "";
      list = list.map(row => row.id === id ? result.pairing : row);
      lastSaid = ""; observed.set(id, result.pairing.state); announce(result.pairing); say(done(result.pairing));
      paint("heading"); schedule();
      return true;
    } catch (failure) {
      if (!ctx.valid(page)) return;
      busy = false; if (failure?.status === 401 && ctx.denied(failure)) return;
      confirm = "";
      const stale = ["stale_version", "identity_changed", "step_failed", "already_approved", "already_closed", "nothing_to_close"].includes(failure?.payload?.reason);
      formError = "";
      say(failText(failure));
      paint();
      if (stale) await reloadOne(id);
    }
  }
  async function reloadOne(id) {
    const page = ctx.epoch();
    try {
      const result = await ctx.request(`${BASE}/${enc(id)}`);
      if (!ctx.valid(page) || !list) return;
      list = list.map(row => row.id === id ? result.pairing : row); serverTime = result.server_time || serverTime; paint(); schedule();
    } catch { /* the next check or Reload shows what is true */ }
  }

  async function copyCommand() {
    if (!setup) return;
    const page = ctx.epoch();
    let note = "Copied. Paste it on the machine.";
    // No clipboard, or a copy that does not return a promise, is a failed copy: never claim "Copied." without proof.
    try { const done = ctx.copy(setup.command); if (typeof done?.then !== "function") throw new Error("no clipboard"); await done; }
    catch { note = "Copying is not available here. The command above is selected: copy it with your keyboard or the long-press menu."; selectCommand(); }
    if (!ctx.valid(page)) return;
    copyNote = note;
    // In place: the command keeps its node, focus and selection (the fallback selects it for the person to copy).
    if (ctx.host()?.querySelector?.("[data-pr-copy-note]")) writeClock(); else paint();
  }
  function selectCommand() {
    const node = ctx.host()?.querySelector?.("[data-pr-command]");
    // Focus first (a focus can clear a selection), then select the whole command.
    node?.focus?.({ preventScroll: true });
    try { const range = ctx.doc?.createRange?.(); range?.selectNodeContents?.(node); const selection = ctx.doc?.getSelection?.() || globalThis.getSelection?.(); selection?.removeAllRanges?.(); selection?.addRange?.(range); } catch { /* the text stays selectable by hand */ }
  }

  function click(event) {
    const jump = event.target.closest?.("[data-pr-jump]");
    if (jump) { event.preventDefault(); const target = ctx.host()?.querySelector?.(`#${jump.dataset.prJump}`); try { target?.scrollIntoView?.({ block: "center", behavior: "instant" }); } catch { /* old engines take no options */ } (target?.querySelector?.("input") || target)?.focus?.({ preventScroll: true }); return true; }
    const target = event.target.closest?.("[data-pr-action]");
    if (!target) return false;
    if (target.getAttribute("aria-disabled") === "true") return true;
    const action = target.dataset.prAction, item = find(selected);
    if (action === "enable") void enable();
    else if (action === "add") openForm();
    else if (action === "cancel" || action === "close") close();
    else if (action === "open") openDetail(target.dataset.id);
    else if (action === "reload") void load({ announceDone: true });
    else if (action === "copy") void copyCommand();
    else if (action === "approve" && item?.state === "connected") void act("accept", { identity_digest: item.identity_digest }, { item, done: p => `Approved ${quoted(p.label)}. ${p.next}` });
    // Asking changes nothing, and focus goes to the safe answer: a second Enter must never remove access.
    else if (action === "decline" && item) { confirm = "decline"; paint("keep"); }
    else if (action === "remove" && item) { confirm = "remove"; paint("keep"); }
    else if (action === "keep") { confirm = ""; paint(item?.state === "connected" ? "decline" : "remove"); }
    else if (action === "confirm-close" && item) void act("close", {}, { item, done: p => `${quoted(p.label)}: ${p.state_label}.` });
    else if (action === "cancel-setup" && item) void act("close", {}, { item, done: p => `Cancelled. ${quoted(p.label)} can be set up again.` });
    else if (action === "again") {
      const row = find(target.dataset.id);
      if (!row) return true;
      const retry = row.retry || { label: row.label, ...row.grant };
      // The old setup is cancelled first (its name and its machine would otherwise be in the way), then the form opens with the same values.
      if (row.can_close) void act("close", {}, { item: row, done: () => "The earlier setup is cancelled. The form below starts from the same settings." }).then(ok => { if (ok) openForm(retry); });
      else openForm(retry);
    }
    return true;
  }
  function submit(event) {
    const form = event.target.closest?.("[data-pr-form]");
    if (!form) return false;
    event.preventDefault(); event.stopPropagation();
    if (mode === "form" && !busy) void create();
    return true;
  }
  function keydown(event) {
    if (event.key !== "Escape" || !ctx.host()?.querySelector?.(".compute-pr :focus")) return false;
    if (confirm) { confirm = ""; paint(find(selected)?.state === "connected" ? "decline" : "remove"); return true; }
    if (mode) { close(); return true; }
    return false;
  }
  // A typed value is stored without a repaint, and a control that changes only its own hint repaints only that hint.
  function input(event) {
    const field = event.target;
    if (!field?.name?.startsWith?.("pr_") && field?.name !== "wings") return false;
    if (field.name === "wings") { const set = new Set(values.wings || []); if (field.checked) set.add(field.value); else set.delete(field.value); values.wings = [...set]; }
    else values[field.name] = field.value;
    if (field.name === "pr_copy_from") { paint(); return true; }
    if (field.name === "pr_mode") {
      const warning = ctx.host()?.querySelector?.("[data-pr-warning]"), want = QUIET_MODES.has(values.pr_mode) ? AUTO_WARNING : "";
      if (warning && warning.textContent !== want) warning.textContent = want;
      return true;
    }
    // An error shown for this field goes away when it is fixed; nothing is said again for each keystroke.
    const key = { pr_label: "label", pr_projects: "projects", pr_topics: "topics", wings: "wings" }[field.name];
    if (key && errors[key] && !validate(values, options)[key]) {
      delete errors[key];
      const slot = ctx.host()?.querySelector?.(`[data-pr-error="${key}"]`); if (slot) slot.textContent = "";
      field.removeAttribute?.("aria-invalid");
      const box = ctx.host()?.querySelector?.("[data-pr-summary]");
      if (box && !formError) box.innerHTML = summaryHTML();
    }
    return true;
  }

  function reset() {
    if (timer !== null) { ctx.cancel(timer); timer = null; }
    list = null; total = 0; options = null; setupEnabled = false; loadError = ""; loading = false; serverTime = ""; pollError = false; machinesKnown = null;
    observed.clear();
    mode = ""; selected = null; setup = null; values = {}; errors = {}; formError = ""; busy = false; confirm = ""; copyNote = ""; notice = ""; lastSaid = ""; requestId = "";
  }
  const state = { get list() { return list; }, get mode() { return mode; }, get setup() { return setup; } };
  return { html, load, click, submit, keydown, input, holdFocus, carry, reset, state, check };
}
