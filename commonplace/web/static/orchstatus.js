// Project settings > Orchestrator: who coordinates this project right now, and the owner's "End lease".
// Reads the orchestrator checkpoint (read-only: it claims and renews nothing) and calls force-release.
// Recovery view (G20): the restore status, the open findings and the steps of the role, all read-only, plus
// one safe owner action: acknowledge a restore (typed text, explicit preview). The page never claims a lease,
// sends a heartbeat, stamps a result, or voids, reassigns or retires anything; the server checks every call.

export const ORCH_ROLE = "main";
const LIST_CAP = 200; // the checkpoint lists at most this many entries of each kind

export const ORCH_COPY = Object.freeze({
  nobody: "Nobody is coordinating this project.",
  loadError: "Could not load the coordinator status.",
  endError: "Could not end the lease. Nothing was changed. Try again.",
  notOwner: "Only the workspace owner can end a lease.",
  gone: "This project is no longer available.",
  alreadyEnded: "The lease had already ended.",
  ended: "Lease ended. Unfinished work stays listed below for the next coordinator to settle.",
  confirm: "The agent can no longer start or change anything under this lease. Work it already started keeps running and stays listed below. The next coordinator can then claim the lease.",
});

export const RECOVERY_COPY = Object.freeze({
  restoreUnreadable: "Could not read the restore status. Nothing here says whether a restore is waiting.",
  restoreNone: "No restore is waiting.",
  restoreNeverRecorded: "No restore has been recorded on this workspace.",
  restoreOwnerOnly: "Only the workspace owner can acknowledge it.",
  ackError: "Could not acknowledge the restore. Nothing was changed. Try again.",
  ackNotOwner: "Only the workspace owner can acknowledge a restore. Nothing was changed.",
  ackText: "That text does not match the one shown here. Nothing was changed.",
  ackAlready: "This restore was already acknowledged.",
  ackGone: "This project is no longer available.",
  ackNote: "No lease was claimed and nothing was started.",
  findingsNone: "No open findings.",
  findingsUnknown: "Findings are not available from this server.",
  findingsCapped: "Only the newest are listed.",
  noOwner: "Nobody can acknowledge this one because no role owns it. It stays listed.",
  stepsNone: "No steps are recorded for the main role.",
  notOffered: "Voiding a step, reassigning it and retiring an experiment are not offered on this page yet. The owner does those through the orchestrator tools.",
});

const plural = (n, one, many) => `${n >= LIST_CAP ? `${LIST_CAP}+` : n} ${n === 1 ? one : many}`;
const person = name => String(name || "").replace(/^conn:/, "") || "an agent";

/** "3 min ago" / "in 2 min": the distance between an ISO time and now, in plain words. */
export function when(iso, now = Date.now()) {
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return "";
  const seconds = Math.round((at - now) / 1000), span = Math.abs(seconds);
  const [n, unit] = span < 60 ? [span, "s"] : span < 3600 ? [Math.round(span / 60), "min"] : span < 86400 ? [Math.round(span / 3600), "h"] : [Math.round(span / 86400), "d"];
  if (span < 5) return "just now";
  return seconds > 0 ? `in ${n} ${unit}` : `${n} ${unit} ago`;
}

/** One sentence for the lease: held, ran out, given up, or never claimed. */
export function leaseSentence(lease, now = Date.now()) {
  if (!lease) return ORCH_COPY.nobody;
  const name = person(lease.holder), at = Date.parse(lease.expires_at);
  if (lease.live) return `Held by ${name}. The lease runs out ${when(lease.expires_at, now)} unless the agent renews it.`;
  if (!Number.isNaN(at) && at <= now) return `${ORCH_COPY.nobody} ${name} held the lease until it ran out ${when(lease.expires_at, now)}.`;
  return `${ORCH_COPY.nobody} ${name} gave the lease up.`;
}

export function orchCounts(checkpoint) {
  const size = key => (checkpoint[key] || []).length;
  return {open: size("open"), inDoubt: size("in_doubt"), needsPerson: size("needs_person"), unstarted: size("unstarted")};
}

function stepLabel(step, e) {
  const what = step.card ? `${step.kind} for card #${step.card}` : `${step.kind} ${step.step_key}`;
  return `${e(what)}${step.detail ? ` — ${e(step.detail)}` : ""}`;
}

const STEP_STATES = {
  intent: "Planned: written before it was done, not started yet.",
  started: "Running under its Coworker's lease.",
  waiting: "Waiting for something outside the Coworker.",
  done: "Finished.",
  failed: "Failed; the failure is kept as history.",
  in_doubt: "In doubt: the lease it ran under ended before it said how it finished.",
  needs_person: "Needs a person: the Coworker could not tell whether this took effect. Check the card or pull request, then ask the Coworker or the owner's tools to settle it.",
};
const FINDING_REASONS = {
  no_owning_card: "this finding is not about a card, so no role owns it.",
  no_owning_session: "no role has started work on this card, so none owns the finding.",
  routing_off: "routing findings is switched off in the config.",
  owner_session_gone: "the role that owns this card has no live lease right now.",
  repair_exhausted: "this pull request used up its repair attempts.",
};
const STEP_REASONS = {...FINDING_REASONS, no_owning_card: "this step is not about a card, so no role owns it."};
const stateText = state => STEP_STATES[state] || `State this page does not know (${state}). Nothing can be done with it here.`;

/** One step with the run and version it belongs to; unknown states are named, never dropped. */
function stepItem(step, e, jobs = new Map()) {
  step = {...step, job_state: step.job_state || jobs.get(step.step_key)};
  const bits = [stateText(step.state)];
  if ((step.destination || {}).kind === "parked") bits.push(`Parked: ${STEP_REASONS[step.destination.reason] || `a reason this page does not know (${step.destination.reason})`}`);
  if (step.job_id) bits.push(`Run ${step.job_id}${step.job_state ? ` is ${step.job_state}` : ""}.`);
  if (step.created_epoch) bits.push(`Started in lease epoch ${step.created_epoch}${step.epoch && step.epoch !== step.created_epoch ? `, last under epoch ${step.epoch}` : ""}.`);
  return `<li>${stepLabel(step, e)} (${e(String(step.state).replace("_", " "))})<br><span class="set-note">${bits.map(e).join(" ")}</span></li>`;
}

/** Findings the board still has open, with where each went and why this page cannot acknowledge it. */
export function findingsHTML(findings, e, now = Date.now()) {
  if (!Array.isArray(findings)) return `<p class="set-note" data-orch-findings-unknown>${e(RECOVERY_COPY.findingsUnknown)}</p>`;
  if (!findings.length) return `<p class="set-note" data-orch-findings>${e(RECOVERY_COPY.findingsNone)}</p>`;
  const n = findings.length >= LIST_CAP ? `${LIST_CAP}+` : String(findings.length);
  const item = f => {
    const d = f.destination || {};
    const where = f.state === "routed" || f.state === "acked" ? `Routed to the ${f.owner_role || d.role || "owning"} role.`
      : f.state === "parked" ? (FINDING_REASONS[f.reason || d.reason] ? `Parked: ${FINDING_REASONS[f.reason || d.reason]}` : `Parked for a reason this page does not know (${f.reason || d.reason || "none given"}).`)
      : `State this page does not know (${f.state}).`;
    const who = f.owner_role ? `Only the ${f.owner_role} role can acknowledge it, with its own lease. This page cannot do that for it.` : RECOVERY_COPY.noOwner;
    return `<li>Finding ${e(f.id)}${f.card ? ` · card #${e(f.card)}` : ""} · ${e(f.detail)}<br><span class="set-note">${e(where)} ${e(who)}${f.created_at ? ` Reported ${e(when(f.created_at, now))}.` : ""}</span></li>`;
  };
  return `<details data-orch-findings open><summary>${e(`${n} open ${findings.length === 1 ? "finding" : "findings"}`)}</summary>${findings.length >= LIST_CAP ? `<p class="set-note">${e(RECOVERY_COPY.findingsCapped)}</p>` : ""}<ul>${findings.map(item).join("")}</ul></details>`;
}

const jobStates = view => new Map((view.jobs || []).map(j => [j.step_key, j.state]));

/** Steps of the role grouped by what a person can do about them; settle-first groups open by default. */
export function stepsHTML(view, e) {
  const jobs = jobStates(view);
  const groups = [["needs_person", "Needs a person"], ["in_doubt", "In doubt"], ["open", "Open"], ["holds", "On hold"], ["parked", "Parked"], ["finished", "Recently finished"]];
  const blocks = groups.map(([key, title]) => {
    const rows = view[key] || [];
    return rows.length ? `<details data-orch-steps-${key.replace("_", "-")} ${key === "finished" || key === "parked" ? "" : "open"}><summary>${e(`${title} (${rows.length >= LIST_CAP ? `${LIST_CAP}+` : rows.length})`)}</summary><ul>${rows.slice(0, 20).map(s => stepItem(s, e, jobs)).join("")}</ul></details>` : "";
  }).join("");
  return blocks || `<p class="set-note" data-orch-steps-none>${e(RECOVERY_COPY.stepsNone)}</p>`;
}

const validRestore = restore => Boolean(restore) && typeof restore === "object" && typeof restore.restore_pending === "boolean";

/** The restore status and, for the owner, the acknowledge flow (preview, typed text, confirm). */
export function restoreHTML(restore, e, {canAck = false, confirming = false, typed = "", busy = false, now = Date.now()} = {}) {
  if (!validRestore(restore)) return `<p class="set-note" data-orch-restore-state>${e(RECOVERY_COPY.restoreUnreadable)}</p>`;
  if (!restore.restore_pending) {
    const last = restore.acked_at ? `Last restore acknowledged by ${person(restore.acked_by)} ${when(restore.acked_at, now)}.` : RECOVERY_COPY.restoreNeverRecorded;
    return `<p class="set-note" data-orch-restore-state>${e(`${RECOVERY_COPY.restoreNone} ${last}`)}</p>`;
  }
  const taken = restore.after ? `taken at ${restore.after}` : "";
  const state = `<p data-orch-restore-state><span class="tag">Restore waiting</span> ${e(`This data was restored from a backup ${taken}`.trim())}. Until the owner acknowledges it, no Coworker can claim a lease or start work on any project.${restore.restored_at ? ` Restored ${e(when(restore.restored_at, now))}.` : ""}</p>`;
  if (!canAck) return `${state}<p class="set-note" data-orch-restore-owner-only>${e(RECOVERY_COPY.restoreOwnerOnly)}</p>`;
  if (!confirming) return `${state}<button type="button" class="btn sm" data-orch-restore ${busy ? "disabled" : ""}>Review and acknowledge…</button>`;
  const match = typeof restore.ack_text === "string" && typed === restore.ack_text;
  return `${state}<div class="callout wait" data-orch-restore-preview role="group" aria-label="Acknowledge the restore"><p>Acknowledge this restore? Every project with a Coworker config starts over from its Todo cards, so a Coworker may start work on cards again. This does not claim a lease, start a step or send a heartbeat; each Coworker claims its own lease afterwards.</p><p>Work that was running at the time of the backup may have run twice or not at all. Check the steps listed below first.</p><p>To confirm, type this text exactly:</p><p><code data-orch-restore-expected>${e(restore.ack_text || "")}</code></p><input data-orch-restore-text style="width:100%;box-sizing:border-box" value="${e(typed)}" aria-label="Acknowledgement text" autocomplete="off" spellcheck="false" ${busy ? "disabled" : ""}> <button type="button" class="btn sm" data-orch-restore-confirm ${busy || !match ? "disabled" : ""}>Acknowledge restore</button> <button type="button" class="btn ghost sm" data-orch-restore-cancel ${busy ? "disabled" : ""}>Not now</button></div>`;
}

/** The accepted config's merge policy ([factory]) and the board's gate counts; "" without one. */
export function factoryHTML(factory, e) {
  if (!factory) return "";
  const g = factory.gates || {}, n = key => Number(g[key]) || 0;
  const gates = `Gates on this board: ${n("gate_pass")} passed, ${n("gate_fail")} failed, ${n("gate_error")} with an error, ${n("gate_budget")} out of budget, ${n("open")} open.`;
  return `<details data-orch-factory><summary>Merge policy</summary><ul>${(factory.summary || []).map(line => `<li>${e(line)}</li>`).join("")}</ul><p class="set-note" data-orch-factory-gates>${e(gates)}</p></details>`;
}

/** Restore, findings and steps below the coordinator lines; the settle-first groups stay in "steps to settle". */
function recoveryHTML(view, e, {restore, restoreView, now}) {
  const rest = {...view, in_doubt: [], needs_person: []};
  const scope = `Showing the ${view.role || ORCH_ROLE} role on ${view.config ? `config version ${view.config.version}` : "no accepted config"}${view.lease ? `, lease epoch ${view.lease.epoch}` : ", no lease"}.`;
  return `<h3>Recovery</h3><p class="set-note" data-orch-scope>${e(scope)}</p><h4>Restore</h4>${restoreHTML(restore, e, {...restoreView, now})}<h4>Findings</h4>${findingsHTML(view.findings, e, now)}<h4>Steps</h4>${stepsHTML(rest, e)}<p class="set-note" data-orch-not-offered>${e(RECOVERY_COPY.notOffered)}</p>`;
}

export function orchStatusHTML(view, e, {canEnd = false, confirming = false, busy = false, status = "", now = Date.now(), restore, restoreView = {}} = {}) {
  if (!view) return `<p role="status" data-orch-status>${e(status || ORCH_COPY.loadError)}</p>`;
  const {lease, config} = view, counts = orchCounts(view), live = Boolean(lease?.live);
  const unsettled = [...(view.in_doubt || []), ...(view.needs_person || [])];
  const parts = [plural(counts.open, "step open", "steps open"), `${counts.inDoubt >= LIST_CAP ? `${LIST_CAP}+` : counts.inDoubt} in doubt`];
  if (counts.needsPerson) parts.push(plural(counts.needsPerson, "needs a person", "need a person"));
  parts.push(plural(counts.unstarted, "Todo card not started", "Todo cards not started"));
  const configLine = config ? `Accepted config: version ${e(config.version)}.` : "No Coworker config is accepted yet.";
  const stays = !live && counts.open ? '<p class="set-note" data-orch-stays>Unfinished work stays listed here until a new coordinator claims the lease and settles it.</p>' : "";
  const end = canEnd && live ? (confirming
    ? `<div class="callout wait" data-orch-confirm role="group" aria-label="End the lease"><p>End ${e(person(lease.holder))}'s lease? ${e(ORCH_COPY.confirm)}</p><button type="button" class="btn sm" data-orch-end-confirm ${busy ? "disabled" : ""}>End lease</button> <button type="button" class="btn ghost sm" data-orch-end-cancel ${busy ? "disabled" : ""}>Keep it</button></div>`
    : `<button type="button" class="btn sm" data-orch-end ${busy ? "disabled" : ""}>End lease…</button>`) : "";
  return `<h3>Coordinator</h3><p data-orch-lease><span class="tag ${live ? "ok" : ""}">${live ? "Live" : lease ? "Ended" : "None"}</span> ${e(leaseSentence(lease, now))}</p><p class="set-note" data-orch-counts>${e(parts.join(" · "))}. ${configLine}</p>${factoryHTML(view.factory, e)}${stays}${unsettled.length ? `<details data-orch-doubt><summary>${e(plural(unsettled.length, "step to settle", "steps to settle"))}</summary><ul>${unsettled.slice(0, 20).map(s => stepItem(s, e, jobStates(view))).join("")}</ul></details>` : ""}${end}${recoveryHTML(view, e, {restore, restoreView: {...restoreView, busy}, now})}<button type="button" class="btn ghost sm" data-orch-refresh ${busy ? "disabled" : ""}>Refresh</button><p role="status" data-orch-status>${e(status)}</p>`;
}

/** Mounts below `anchor` (a node inside the Orchestrator section). Shows nothing when the
 *  workspace does not use orchestration or the board's status cannot be read. */
export async function mountOrchStatus(anchor, {project, api, e, current = () => true, now = () => Date.now()}) {
  if (!anchor || !project.orchestration) return;
  let host = anchor.nextElementSibling?.hasAttribute?.("data-project-orch-status") ? anchor.nextElementSibling : null;
  if (!host) {
    host = anchor.ownerDocument.createElement("div");
    host.setAttribute("data-project-orch-status", "");
    anchor.after(host);
  }
  const active = () => current() && host.isConnected;
  const base = `/api/orch/boards/${encodeURIComponent(project.board)}/roles/${ORCH_ROLE}`;
  let view = null, restore = null, confirming = false, ackOpen = false, typed = "", busy = false, status = "";
  // false when the status is not for this person to see (hidden); a failed read shows the error.
  async function load() {
    try { view = await api(`${base}/checkpoint`); }
    catch (error) {
      if (error?.status === 404 || error?.status === 403) { host.replaceChildren(); host.hidden = true; return false; }
      view = null;
      status = status || ORCH_COPY.loadError;
    }
    // The restore status is read apart: when it fails the block says so instead of implying "no restore".
    try { restore = await api("/api/orch/restore"); } catch { restore = null; }
    host.hidden = false;
    return true;
  }
  function render() {
    if (!active()) return;
    host.innerHTML = orchStatusHTML(view, e, {canEnd: Boolean(project.can_configure), confirming, busy, status, now: now(), restore, restoreView: {canAck: Boolean(project.can_configure), confirming: ackOpen, typed}});
    const on = (name, fn, event = "onclick") => { const node = host.querySelector(`[data-orch-${name}]`); if (node) node[event] = fn; };
    on("end", () => { confirming = true; status = ""; render(); });
    on("end-cancel", () => { confirming = false; render(); });
    on("end-confirm", endLease);
    on("restore", () => { ackOpen = true; typed = ""; status = ""; render(); });
    on("restore-cancel", () => { ackOpen = false; typed = ""; render(); });
    on("restore-text", event => {
      typed = event.target.value;
      const confirm = host.querySelector("[data-orch-restore-confirm]");
      if (confirm) confirm.disabled = busy || typed !== restore?.ack_text;
    }, "oninput");
    on("restore-confirm", acknowledgeRestore);
    on("refresh", async () => { status = ""; if (await load() && active()) render(); });
  }
  async function acknowledgeRestore() {
    if (busy || !restore?.restore_pending || typed !== restore.ack_text) return;
    busy = true; status = ""; render();
    let message, done = false;
    try {
      const result = await api("/api/orch/restore/ack", {method: "POST", body: {text: typed}});
      const n = Array.isArray(result?.arming) ? result.arming.length : 0;
      message = `Restore acknowledged. ${n === 1 ? "1 project will" : `${n} projects will`} re-check ${n === 1 ? "its" : "their"} Todo cards. ${RECOVERY_COPY.ackNote}`;
      done = true;
    } catch (error) {
      const code = error?.payload?.code;
      message = code === "restore_not_pending" ? RECOVERY_COPY.ackAlready : code === "restore_text" ? RECOVERY_COPY.ackText : error?.status === 403 ? RECOVERY_COPY.ackNotOwner : error?.status === 404 ? RECOVERY_COPY.ackGone : RECOVERY_COPY.ackError;
      done = code === "restore_not_pending";
    }
    busy = false; status = message;
    if (done) { ackOpen = false; typed = ""; }
    if (await load() && active()) render();
  }
  async function endLease() {
    if (busy) return;
    busy = true; status = ""; render();
    let message;
    try {
      const result = await api(`${base}/force-release`, {method: "POST", body: {}});
      message = result?.released ? ORCH_COPY.ended : ORCH_COPY.alreadyEnded;
    } catch (error) {
      message = error?.status === 403 ? ORCH_COPY.notOwner : error?.status === 404 ? ORCH_COPY.gone : ORCH_COPY.endError;
    }
    busy = false; confirming = false; status = message;
    if (await load() && active()) render();
  }
  if (await load() && active()) render();
}
