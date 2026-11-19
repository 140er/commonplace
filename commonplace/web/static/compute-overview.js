import { escape as e } from "./api.js";

// The Compute overview (docs/system/compute-overview.md): every machine, runner and cloud workspace with a status in
// plain words, when Commonplace last heard from it, usage where something reported it, and what blocks new work.
// One shared module serves the Classic screen and the Deck Management tab: createCompute (compute.js) embeds
// html() and forwards clicks, changes and keys. This slice only reads. Every sentence comes from the server, which
// never says "running" or "online" without a fresh report; the clock only ever makes a claim older, never newer:
// "Last seen" is recomputed from the report time, a running workspace with no report for 600 s reads Unknown and a
// runner past its thresholds reads Slow or Offline, and after a failed refresh every row says "as of HH:MM:SS".
// A refresh repaints the rows (and the filter counts when they changed) and nothing else: an open detail keeps its
// nodes, an open status list is not closed under the pointer and typing in another section is never disturbed.

const BASE = "/api/compute/overview";
const enc = encodeURIComponent;
const KIND_WORDS = { runner: ["Runner", "Runners"], machine: ["Machine", "Machines"], workspace: ["Cloud workspace", "Cloud workspaces"] };
const KIND_ORDER = ["runner", "machine", "workspace"];
const esc = value => typeof CSS !== "undefined" && CSS.escape ? CSS.escape(value) : String(value).replace(/["\\]/g, "\\$&");
const LOAD_ERROR = "The overview could not be loaded. Nothing is shown rather than a guess.";
const DETAIL_ERROR = "The details could not be loaded.";
const FIRST_RUN = "No machine is set up yet. You can still create a cloud workspace below; it waits for a machine.";
const MORE_THAN = 5; // above this many items the overview starts folded to its summary, so what follows it is within reach
const DEFAULT_REFRESH = 30;
const AGE_PENDING = "…"; // what a row's health line says until refreshAges fills in the age
const UNKNOWN_STEP = "Nothing to do yet; this clears when the machine reports again."
const CLOCK_SECONDS = 10;
const NARROW = "(max-width: 699px)";
// One short name per blocker code, so a row names what blocks new work instead of only counting it.
const BLOCK_SHORT = {
  no_machine: "machine not chosen yet", machine_not_verified: "its machine is not verified", private_image: "private image", not_available: "starting is not available yet",
  revoked: "removed", disabled: "turned off", not_approved: "not approved", draining: "draining", never_connected: "never connected", offline: "offline", full: "full",
  hourly_cap: "hourly limit reached", not_verified: "not verified", verification_expired: "verification expired", unknown: "not known until the next report",
};

const epochOf = at => {
  if (typeof at === "number") return Number.isFinite(at) ? at : null;
  const parsed = typeof at === "string" ? Date.parse(at) : NaN;
  return Number.isNaN(parsed) ? null : parsed / 1000;
};
const agoText = seconds => {
  if (seconds < 5) return "just now";
  const [n, unit] = seconds < 60 ? [Math.floor(seconds), "second"] : seconds < 3600 ? [Math.floor(seconds / 60), "minute"] : seconds < 172800 ? [Math.floor(seconds / 3600), "hour"] : [Math.floor(seconds / 86400), "day"];
  return `${n} ${unit}${n === 1 ? "" : "s"} ago`;
};
const timeOf = iso => {
  const date = typeof iso === "number" ? new Date(iso * 1000) : new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
};

/**
 * What to show for an item now. The server's words are kept; the clock may only make a claim older:
 * { status, health, seen, stale } where seen is the recomputed "Last seen ..." text and stale says the row is
 * marked "as of" because the last refresh failed. `clock` is { nowS, thresholds, failedAt } (all optional).
 */
export function viewOf(item, clock = {}) {
  const { nowS = null, thresholds = {}, failedAt = "", drainScope = "" } = clock;
  let status = item.status, health = item.health, blockers = item.health?.blockers || [];
  const last = item.last_seen;
  let seen = last?.label || "", age = null;
  const at = last ? epochOf(last.at) : null;
  if (at !== null && nowS !== null) {
    age = Math.max(0, nowS - at);
    seen = `${last.prefix || "Last seen"} ${agoText(age)}${last.suffix || ""}`;
  }
  let overridden = false;
  if (age !== null) {
    if (item.kind === "workspace" && ["running", "starting", "stopping"].includes(status.code) && age > (thresholds.report_stale_s || 600)) {
      const word = status.label;
      status = { code: "unknown", tone: "quiet", label: "Unknown", detail: `Its last report said ${word} ${agoText(age)} and nothing newer has arrived, so it may not be ${word.toLowerCase()} any more.`, next: UNKNOWN_STEP };
      blockers = [...blockers, { code: "unknown", text: "Not known until the next report: its last report is too old to trust." }];
      overridden = true;
    } else if (item.kind === "runner" && ["online", "busy", "full", "draining", "drained", "slow"].includes(status.code)) {
      const was = status.label, drain = item.draining ? ` It is also set to drain. ${drainScope || "No new card runs start here."}` : "";
      if (age > (thresholds.red_after_s || 900)) { blockers = [...blockers.filter(b => b.code !== "offline"), { code: "offline", text: `Offline: nothing heard for ${agoText(age).replace(" ago", "")}, so new card runs would wait.` }]; status = { code: "offline", tone: "bad", label: "Offline", detail: `Nothing has been heard from it for ${agoText(age).replace(" ago", "")}. It read ${was} when this was last checked; runs it started may still be going and new card runs wait.${drain}`, next: status.next }; overridden = true; }
      else if (status.code !== "slow" && age > (thresholds.green_within_s || 180)) { status = { code: "slow", tone: "wait", label: "Slow to report", detail: `Later than usual: its last contact was ${agoText(age)}. It read ${was} when this was last checked.${drain}`, next: status.next }; overridden = true; }
    }
  }
  // Only Offline asks something of a person (it is something to check); Slow to report and Unknown only wait.
  if (overridden) health = { ...health, state: status.code === "offline" ? "attention" : health.state, blockers };
  const stale = Boolean(failedAt) && !overridden;
  if (stale) status = { ...status, label: status.code === "unknown" ? status.label : `Last known: ${status.label}`, detail: `As of ${failedAt}: ${status.detail}` };
  return { status, health, seen, stale };
}

const withView = (item, clock) => { const v = viewOf(item, clock); return { ...item, status: v.status, health: v.health, seenText: v.seen, stale: v.stale }; };

/** Items after the kind and status filters. status is "all", "attention" or a status code. */
export function filterItems(items, { kind = "all", status = "all" } = {}) {
  return items.filter(item => (kind === "all" || item.kind === kind)
    && (status === "all" || (status === "attention" ? item.health?.state === "attention" : item.status?.code === status)));
}

/** Counts for the filter controls, from every item (not the filtered ones). */
export function filterCounts(items) {
  const kinds = {}, statuses = {};
  let attention = 0;
  for (const item of items) {
    kinds[item.kind] = (kinds[item.kind] || 0) + 1;
    const code = item.status.code;
    statuses[code] ||= { label: item.status.label, count: 0 };
    statuses[code].count++;
    if (item.health?.state === "attention") attention++;
  }
  return { kinds, statuses, attention };
}

const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;
const NAMES_SHOWN = 3;
/** One plain sentence about everything, naming what needs attention: "2 runners, 3 cloud workspaces. 1 needs attention: Build box." */
export function summaryText(items) {
  if (!items.length) return "";
  const { kinds, attention } = filterCounts(items);
  const parts = KIND_ORDER.filter(kind => kinds[kind]).map(kind => plural(kinds[kind], KIND_WORDS[kind][0].toLowerCase(), KIND_WORDS[kind][1].toLowerCase()));
  const names = items.filter(item => item.health?.state === "attention").map(item => item.name);
  const named = names.length > NAMES_SHOWN ? `${names.slice(0, NAMES_SHOWN).join(", ")} and ${names.length - NAMES_SHOWN} more` : names.join(", ");
  const need = attention ? ` ${attention} ${attention === 1 ? "needs" : "need"} attention: ${named}.` : " Nothing needs attention.";
  // A workspace with no machine needs nothing from anyone, but the sentence says what it is waiting for.
  const waiting = items.filter(item => item.kind === "workspace" && item.placement?.assigned === false).length;
  const wait = waiting ? ` ${plural(waiting, "cloud workspace is", "cloud workspaces are")} waiting for a machine.` : "";
  return `${parts.join(", ")}.${need}${wait}`;
}

const healthLine = (item, seen) => {
  if (seen) return seen;
  if (item.kind === "runner") return "No signal yet: it has never contacted Commonplace.";
  if (item.kind === "machine") return "Not checked live: Commonplace does not probe target machines.";
  return "No report from a machine yet.";
};
// The detail view's own wording for the signal row, by kind.
const seenRow = (item, seen) => {
  if (item.last_seen?.when) return item.kind === "runner" ? ["Last seen", `${seen.replace(/^Last (?:seen|contact) /, "")}. That is its last contact with Commonplace, not a live check.`] : ["Last report", seen.replace(/^Last reported /, "")];
  return [item.kind === "workspace" ? "Last report" : item.kind === "machine" ? "Live check" : "Last seen", healthLine(item, "")];
};
const blockLine = item => {
  // "Starting is not available" is said once for the page (above the list), not on every row; the detail still lists it.
  const list = (item.health?.blockers || []).filter(b => b.code !== "not_available");
  if (!list.length) return "";
  const names = list.map(b => BLOCK_SHORT[b.code]).filter(Boolean);
  return ` · ${plural(list.length, "thing blocks", "things block")} new work${names.length === list.length ? `: ${names.join(", ")}` : ""}`;
};

/** One row. ``item`` is what withView made of it (status, health and ``seenText`` already follow the clock); a bare server item reads as the server said it. */
export function rowHTML(item) {
  const status = item.status, name = e(item.name), seen = item.seenText ?? item.last_seen?.label ?? "";
  return `<li class="compute-row compute-ov-row" data-ov-id="${e(item.id)}" data-ov-kind="${e(item.kind)}" data-status="${e(status.code)}">`
    + `<header><h4 class="compute-ov-name">${name}</h4><span class="compute-state compute-ov-chip" data-tone="${e(status.tone)}" data-ov-chip>${e(status.label)}</span></header>`
    + (item.kind === "workspace" && item.subtitle ? `<p class="compute-quiet compute-ov-sub">Image: ${e(item.subtitle)}</p>` : "")
    + `<p class="compute-ov-sentence" data-ov-sentence>${e(status.detail)}</p>`
    + (status.next ? `<p class="compute-ov-next" data-ov-next>Next step: ${e(status.next)}</p>` : "")
    + `<p class="compute-ov-health" data-ov-health>${e(healthLine(item, seen))}${e(blockLine(item))}</p>`
    + `<p class="compute-quiet compute-ov-usage">${e(item.usage?.summary || "")}</p>`
    + `<div class="compute-actions"><button type="button" class="btn" data-ov-action="detail" data-kind="${e(item.kind)}" data-id="${e(item.id)}" aria-label="Status and activity of ${name}">Status and activity</button></div></li>`;
}

const dl = pairs => `<dl>${pairs.filter(([, value]) => value !== "" && value != null && value !== "undefined").map(([key, value]) => `<dt>${e(key)}</dt><dd>${value}</dd>`).join("")}</dl>`;
const usageRows = item => {
  const u = item.usage || {};
  // A usage field the server did not send is left out, never printed as the word "undefined".
  const text = value => e(value ?? "");
  if (item.kind === "runner") return [["Runs", text(u.runs?.text)], ["Machine resources", text(u.resources?.text)], ["Spend", text(u.spend?.text)]];
  if (item.kind === "workspace") return [["Limits", text(u.limits?.text)], ["Measured use", text(u.measured?.text)]];
  return [["Cloud workspaces here", text(u.summary)], ["Measured use", text(u.measured?.text)]];
};

const chipHTML = status => `<span class="compute-state compute-ov-chip" data-tone="${e(status.tone)}" data-ov-chip>${e(status.label)}</span>`;
/** The panel: a heading that keeps its node (so focus on it is never disturbed), Close in the header and a body that is repainted only when it changed. */
export function detailHTML(item, { loading = false, failed = false, manage = false, clock } = {}) {
  const v = clock === undefined ? { status: item.status, health: item.health, seen: item.last_seen?.label || "" } : viewOf(item, clock);
  return `<section class="compute-editor compute-ov-panel" aria-labelledby="compute-ov-heading"${loading ? ' aria-busy="true"' : ""} data-ov-panel>`
    + `<header><div class="compute-ov-panel-title"><p class="compute-ov-eyebrow">Status and activity</p><h3 id="compute-ov-heading" tabindex="-1">${e(item.name)}</h3><span data-ov-chip-slot>${chipHTML(v.status)}</span></div>`
    + `<button type="button" class="btn" data-ov-action="close">Close</button></header><div data-ov-detail-body>${detailBody(item, { loading, failed, manage, clock })}</div></section>`;
}
export function detailBody(item, { loading = false, failed = false, manage = false, clock } = {}) {
  if (loading) return '<p class="compute-quiet">Loading details…</p>';
  const v = clock === undefined ? { status: item.status, health: item.health, seen: item.last_seen?.label || "" } : viewOf(item, clock);
  const blockers = v.health?.blockers || [];
  const blocks = blockers.length ? `<ul class="compute-ov-list" role="list">${blockers.map(b => `<li>${e(b.text)}</li>`).join("")}</ul>` : item.kind === "machine" ? "Nothing known to block new work (not checked live)." : "Nothing is blocking new work.";
  const issues = (v.health?.issues || []).length ? `<ul class="compute-ov-list" role="list">${v.health.issues.map(text => `<li>${e(text)}</li>`).join("")}</ul>` : "";
  const activity = (item.activity || []).length
    ? `<ul class="compute-ov-list compute-ov-activity" role="list">${item.activity.map(a => `<li>${e(a.text)}${a.age_s == null ? "" : ` <span class="compute-quiet">(${e(agoText(a.age_s))})</span>`}</li>`).join("")}</ul>`
    : '<p class="compute-quiet">No recent activity.</p>';
  const change = manage && item.kind === "workspace" ? '<div class="compute-actions"><button type="button" class="btn" data-ov-action="settings">Change settings in Cloud workspaces</button></div>' : "";
  const [seenLabel, seenValue] = seenRow(item, v.seen);
  return `<p class="compute-ov-sentence">${e(v.status.detail)}</p>`
    + (v.status.next ? `<p class="compute-ov-next">Next step: ${e(v.status.next)}</p>` : "")
    + (failed ? `<p class="compute-error compute-ov-stale">${e(DETAIL_ERROR)} Showing what the list knew.</p>` : "")
    + `<h4 class="compute-ov-sub-h">Health</h4>${dl([[seenLabel, `<span data-ov-seen>${e(seenValue)}</span>`], ["Blocking new work", blocks], [v.health?.issue_label || "Needs a person", issues]])}`
    + `<h4 class="compute-ov-sub-h">Usage</h4>${dl(usageRows(item))}`
    + `<h4 class="compute-ov-sub-h">Recent activity</h4>${activity}`
    + ((item.facts || []).length ? `<h4 class="compute-ov-sub-h">More about it</h4>${dl(item.facts.map(f => [f.label, e(f.value)]))}` : "")
    + change;
}

/**
 * ctx: { request, epoch, valid(page), denied(failure), host(), rerender(), doc, now() (ms), timers: { setTimeout, clearTimeout },
 *        workspaceSignature?() }
 * Owns its state; compute.js embeds html() and forwards events.
 */
export function createOverview(ctx) {
  const timers = ctx.timers || globalThis;
  let data = null, loadError = "", loading = false, fetchedAt = 0, fetchedMono = 0, tries = 0, refreshFailed = false, failedSaid = false, paused = false, foldOpen = null;
  let kind = "all", status = "all", detail = null, detailBusy = false, detailFailed = false, opener = "", timer = null, clockTimer = null, previous = null, seenSignature = "";
  // What each slot of the section holds right now, so a repaint writes only what changed.
  let painted = {}, announced = "";
  const find = id => data?.items?.find(item => item.id === id) || null;
  const root = () => ctx.host()?.querySelector?.("[data-ov-root]");
  const slot = name => ctx.host()?.querySelector?.(`[data-ov-slot="${name}"]`);
  const live = () => ctx.host()?.querySelector?.("[data-ov-announce]");
  const announce = text => { announced = text; const node = live(); if (node) node.textContent = text; };
  const interval = () => Math.max(15, Number(data?.refresh_seconds) || DEFAULT_REFRESH);
  // "Now" is the SERVER's time at the last read plus the time that has passed here since: every timestamp on screen is the
  // server's, so comparing it with this browser's own clock would make a skewed clock read a healthy runner as Offline
  // (or a stale one as fresh). Only the elapsed time is taken from the browser.
  // Elapsed time comes from the monotonic clock (``performance.now``), not the wall clock: a clock that is stepped (an
  // NTP correction, a laptop waking, a person changing the time) between two reads must not move any age.
  const mono = () => (typeof ctx.mono === "function" ? ctx.mono() : ctx.now());
  const sinceRead = () => Math.max(0, (mono() - fetchedMono) / 1000);
  const serverNow = () => { const read = data ? epochOf(data.as_of) : null; return read === null ? null : read + sinceRead(); };
  const clock = () => ({ nowS: serverNow(), thresholds: data?.thresholds || {}, failedAt: refreshFailed && data ? timeOf(data.as_of) : "", drainScope: data?.drain_scope || "" });
  const items = () => (data?.items || []).map(item => withView(item, clock()));
  const narrow = () => ctx.doc?.defaultView?.matchMedia?.(NARROW)?.matches === true;
  // Closed on a phone and above a handful of items (the summary names what needs attention); open for the person's own choice,
  // and while a failed first read has a "Try again" inside it.
  // Open on a desktop; on a phone open for a short list and closed above a handful of items. With nothing to list the
  // summary is plain text (no disclosure) and closed.
  const isOpen = () => foldOpen !== null ? foldOpen : plain() ? false : narrow() ? data.items.length <= MORE_THAN : true;

  function filtersHTML(all) {
    const { kinds } = filterCounts(all);
    // Statuses are those of the chosen kind, so no combination can match nothing ("Cloud workspaces" and "Offline").
    const { statuses, attention } = filterCounts(all.filter(item => kind === "all" || item.kind === kind));
    const shownKinds = KIND_ORDER.filter(k => kinds[k]);
    const kindButtons = shownKinds.length > 1
      ? `<div class="compute-ov-kinds" role="group" aria-label="Filter by kind">${[["all", `All (${all.length})`], ...shownKinds.map(k => [k, `${KIND_WORDS[k][1]} (${kinds[k]})`])].map(([value, label]) => `<button type="button" class="btn compute-ov-kind" data-ov-kind-filter="${value}" aria-pressed="${kind === value}">${e(label)}</button>`).join("")}</div>` : "";
    // The count follows the kind filter, so it names its scope ("2 runners need attention") instead of changing meaning.
    const scope = kind === "all" ? "" : `${KIND_WORDS[kind][attention === 1 ? 0 : 1].toLowerCase()} `;
    const needs = attention ? `<button type="button" class="btn compute-ov-kind" data-ov-action="attention" aria-pressed="${status === "attention"}">${attention} ${scope}${attention === 1 ? "needs" : "need"} attention</button>` : "";
    const options = [["all", "All statuses"], ...(attention ? [["attention", `Needs attention (${attention})`]] : []), ...Object.entries(statuses).map(([code, v]) => [code, `${v.label} (${v.count})`])];
    const select = `<label class="compute-ov-status-filter" for="compute-ov-status">Filter by status</label><select id="compute-ov-status" data-ov-status>${options.map(([value, label]) => `<option value="${e(value)}"${status === value ? " selected" : ""}>${e(label)}</option>`).join("")}</select>`;
    const active = kind !== "all" || status !== "all";
    const shown = filterItems(all, { kind, status }).length;
    return `<div class="compute-ov-filters">${kindButtons}${needs}<div class="compute-ov-select">${select}</div>${active ? '<button type="button" class="btn" data-ov-action="clear">Clear filters</button>' : ""}</div>`
      + (active ? `<p class="compute-quiet compute-ov-showing">Showing ${shown} of ${all.length}</p>` : "");
  }

  function listHTML(all) {
    const shown = filterItems(all, { kind, status });
    if (!shown.length) return '<p class="compute-empty">No machines, runners or cloud workspaces match these filters. Use Clear filters above.</p>';
    return KIND_ORDER.map(k => {
      const rows = shown.filter(item => item.kind === k);
      return rows.length ? `<section class="compute-ov-group" aria-labelledby="compute-ov-group-${k}"><h3 id="compute-ov-group-${k}">${e(KIND_WORDS[k][1])} <span class="compute-quiet">(${rows.length})</span></h3><ul class="compute-list compute-ov-list-rows" role="list">${rows.map(rowHTML).join("")}</ul></section>` : "";
    }).join("");
  }

  // The headline (summary line) says what state this is; the body says only what to do next, never the same words twice.
  function emptyHTML() {
    if (!data.can_manage) return '<p class="compute-empty">The workspace owner can create one.</p>';
    // The workspaces list is the source of truth for cloud workspaces: while it holds records this overview has not caught up.
    return ctx.workspaceSignature?.() ? '<p class="compute-empty">Your cloud workspaces are listed below.</p>' : `<p class="compute-empty">${FIRST_RUN}</p>`;
  }

  const stateHTML = () => data === null
    ? (loadError ? `<p class="compute-error" role="alert" data-ov-alert>${e(loadError)}</p><p class="compute-quiet" data-ov-tries></p><div class="compute-actions"><button type="button" class="btn" data-ov-action="refresh">Try again</button></div>` : "")
    : (!data.items.length ? emptyHTML() : "");

  function noteText() {
    if (data === null) return "";
    const hidden = data.hidden ? data.hidden.text : "";
    const notes = [data.executor?.available === false && data.can_manage ? data.executor.text : "", ...Object.entries(data.truncated || {}).map(([k, n]) => `Showing the newest ${data.items.filter(i => i.kind === k).length} of ${n} ${KIND_WORDS[k][1].toLowerCase()}.`)].filter(Boolean);
    return [hidden, ...notes].join("\n");
  }
  const notesHTML = () => noteText().split("\n").filter(Boolean).map(text => `<p class="compute-quiet">${e(text)}</p>`).join("");

  function summaryLine() {
    if (data === null) return loadError ? (loading ? "Trying again…" : "Overview not loaded.") : "Loading the overview…";
    const said = summaryText(items()) || (ctx.workspaceSignature?.() ? "The overview is catching up." : data.can_manage ? "Nothing here yet." : "No cloud workspaces yet.");
    // A closed fold must not read as current after a failed refresh.
    return refreshFailed ? `${said} Could not refresh: data from ${timeOf(data.as_of)}.` : said;
  }

  function metaText() {
    if (data === null) return "";
    const as = epochOf(data.as_of);
    if (refreshFailed) return ""; // the alert above says it once (and the rows say "as of"); a second sentence is the same news
    const age = as === null ? "" : `Updated ${agoText(sinceRead())} (as of ${timeOf(data.as_of)}). `;
    return paused ? `${age}Automatic updates are paused; statuses and ages keep counting from the last read. Use Refresh above to read again.` : `${age}It refreshes every ${interval()} seconds while this tab is visible, or use Refresh above.`;
  }

  const staleText = () => refreshFailed && data ? `Could not refresh. Showing the data from ${timeOf(data.as_of)}; it may be out of date.` : "";
  const topHTML = () => `<header><h2 id="compute-overview" tabindex="-1">Overview</h2><button type="button" class="btn compute-ov-pause" data-ov-action="pause">${paused ? "Resume automatic updates" : "Pause automatic updates"}</button></header>`
    + '<p class="compute-plain">Every machine, runner and cloud workspace in this workspace. A runner is a machine that runs Coworkers; a machine is where a cloud workspace is placed. “Last seen” is the last time something was heard from it, not a live check, and a number nobody reported says so instead of showing zero.</p>';

  function panelKey() { return detail ? `${detail.kind}:${detail.id}` : ""; }
  function slots(stable = true) {
    const all = data ? items() : [];
    const open = Boolean(detail) && data !== null;
    return {
      stale: staleText(),
      state: stateHTML(),
      notes: notesHTML(),
      panel: open ? "keep" : "",
      filters: data && all.length && !open ? filtersHTML(all) : "",
      // The list's markup does not carry the ages (they move every second and are patched in place by refreshAges), so a
      // refresh rewrites it only when a row, a status or a name changed: a focused row is not rebuilt under the person.
      list: data && all.length && !open ? listHTML(stable ? all.map(item => ({ ...item, seenText: item.last_seen ? AGE_PENDING : undefined })) : all) : "",
    };
  }
  // The state sentence (loading, failed with "Try again", empty with what to do) sits outside the fold: on a phone a closed
  // fold must not hide the one thing the person can do. While there is nothing to fold the summary shows no disclosure marker.
  const plain = () => data === null || !data.items.length;
  function inner() {
    // The DOM gets the real ages; what is remembered as painted is the stable markup, so the next refresh compares like with like.
    const values = slots(false);
    painted = { ...slots(true), panelKey: panelKey() };
    const opts = { loading: detailBusy, failed: detailFailed, manage: data?.can_manage, clock: clock() };
    const panel = detail && data ? detailHTML(detail.item, opts) : "";
    if (detail && data) { painted.chip = chipHTML(viewOf(detail.item, opts.clock).status); painted.body = detailBody(detail.item, opts); }
    return `<div data-ov-slot="notes">${values.notes}</div>`
      + `<div data-ov-slot="panel">${panel}</div><div data-ov-slot="filters">${values.filters}</div><div data-ov-slot="list">${values.list}</div>`;
  }

  /** For the page's own re-render, which replaces this section's nodes: take the focused control's key now, restore it after. */
  function holdFocus() {
    const control = ctx.host()?.querySelector?.(".compute-ov :focus");
    const key = control ? focusKey(control) : "";
    return () => { if (key) ctx.host()?.querySelector?.(key)?.focus?.({ preventScroll: true }); };
  }

  /**
   * For the page's own re-render (any app event reloads the Compute screen): the section's nodes are lifted out before the page
   * is rebuilt and put back in place of the fresh copy, so an open detail, an open status list, the fold and the focus all
   * keep their nodes. The state they were painted from is the state html() just rebuilt, so nothing goes stale.
   */
  function carry() {
    const old = ctx.host()?.querySelector?.("[data-ov]");
    return () => {
      const fresh = ctx.host()?.querySelector?.("[data-ov]");
      // The old section is put back only if it has the same shape as the one just built (a fold, or plain text).
      const same = old?.querySelector?.("[data-ov-fold-wrap]")?.dataset?.foldKey === foldKey();
      if (old && fresh && old !== fresh && same && typeof fresh.replaceWith === "function") fresh.replaceWith(old);
    };
  }

  // With nothing to fold (loading, a failed first read, an empty overview) the headline is plain text: no operable summary
  // that opens nothing. Otherwise it is the summary of the fold that holds the list.
  const foldKey = () => (plain() ? "plain" : "fold");
  function foldHTML() {
    return plain()
      ? `<p class="compute-ov-headline" data-ov-summary>${e(summaryLine())}</p>`
      : `<details class="compute-ov-fold" data-ov-fold${isOpen() ? " open" : ""}><summary data-ov-fold-summary><span data-ov-summary>${e(summaryLine())}</span></summary><div data-ov-root>${inner()}</div></details>`;
  }
  function html() {
    return `<section class="compute-inventory compute-wide compute-ov" aria-labelledby="compute-overview" data-ov><div data-ov-top>${topHTML()}</div>`
      // The refresh state sits above the fold, so a closed fold (a phone) cannot hide a failed refresh.
      + `<p class="compute-quiet compute-ov-meta" data-ov-meta>${e(metaText())}</p>`
      + `<p class="compute-error compute-ov-stale" role="alert" data-ov-stale${staleText() ? "" : " hidden"}>${e(staleText())}</p>`
      + `<p class="compute-sr" role="status" aria-live="polite" data-ov-announce>${e(announced)}</p>`
      + `<div data-ov-fold-wrap data-fold-key="${foldKey()}">${foldHTML()}</div><div data-ov-slot="state">${slots().state}</div></section>`;
  }

  const setText = (node, text) => { if (node && node.textContent !== text) node.textContent = text; };
  const setHTML = (name, markup) => {
    if (painted[name] === markup) return false;
    const node = slot(name);
    if (!node) return false;
    painted[name] = markup;
    node.innerHTML = markup;
    return true;
  };
  function focusKey(control) {
    const d = control.dataset || {};
    if (control.id === "compute-ov-heading") return "#compute-ov-heading";
    if (control.id === "compute-overview") return "#compute-overview";
    if (control.id === "compute-ov-status") return "#compute-ov-status";
    if (d.ovKindFilter) return `[data-ov-kind-filter="${d.ovKindFilter}"]`;
    if (d.ovAction === "detail" && d.id) return `[data-ov-action="detail"][data-id="${esc(d.id)}"]`;
    if (d.ovAction) return `[data-ov-action="${d.ovAction}"]`;
    if (d.ovFoldSummary !== undefined) return "[data-ov-fold-summary]";
    return "";
  }
  function focusTarget(host, focus) {
    if (focus === "detail") return host.querySelector("#compute-ov-heading");
    if (focus === "opener") return (opener && host.querySelector(`[data-ov-action="detail"][data-id="${esc(opener)}"]`)) || host.querySelector("#compute-overview");
    if (focus === "status") return host.querySelector("#compute-ov-status");
    return host.querySelector(`[data-ov-action="${focus}"]`);
  }

  // Repaint what changed, slot by slot. A control inside the section that has focus is found again by its data attributes.
  function paint(focus = "") {
    const host = ctx.host();
    if (!host || !host.querySelector("[data-ov]") && !root()) { ctx.rerender(); return; }
    const wrap = host.querySelector("[data-ov-fold-wrap]");
    if (wrap && wrap.dataset && wrap.dataset.foldKey !== foldKey()) {
      // The shape changed (a first read arrived, or the list emptied): rebuild the fold or the plain headline, once.
      wrap.dataset.foldKey = foldKey();
      wrap.innerHTML = foldHTML();
    }
    const control = host.querySelector?.(".compute-ov :focus");
    const key = control ? focusKey(control) : "";
    const values = slots();
    setText(host.querySelector("[data-ov-summary]"), summaryLine());
    setText(host.querySelector("[data-ov-meta]"), metaText());
    // Until the person chooses, the fold follows what it holds: closed above a handful of items, open for a failed first read.
    if (foldOpen === null) { const fold = host.querySelector("[data-ov-fold]"), want = isOpen(); if (fold && fold.open !== want) fold.open = want; }
    const top = host.querySelector("[data-ov-top]");
    if (top && painted.top !== topHTML()) { painted.top = topHTML(); top.innerHTML = topHTML(); }
    const banner = host.querySelector("[data-ov-stale]");
    if (banner) { setText(banner, values.stale); banner.hidden = !values.stale; }
    painted.stale = values.stale;
    setText(host.querySelector("[data-ov-tries]"), loadError && tries > 1 ? `Tried ${tries} times.` : "");
    setHTML("state", values.state); setHTML("notes", values.notes);
    // "Try again" is marked busy by attribute, not by markup: rewriting the state slot would speak the alert again.
    const retry = host.querySelector('[data-ov-slot="state"] [data-ov-action="refresh"]');
    if (retry) { if (loading) retry.setAttribute?.("aria-busy", "true"); else retry.removeAttribute?.("aria-busy"); }
    // The open panel keeps its nodes: it is built when it opens (or changes) and its body repainted in place by paintDetail.
    if (painted.panelKey !== panelKey()) {
      painted.panelKey = panelKey();
      const node = slot("panel");
      const opts = { loading: detailBusy, failed: detailFailed, manage: data?.can_manage, clock: clock() };
      if (node) node.innerHTML = detail && data ? detailHTML(detail.item, opts) : "";
      if (detail && data) { painted.chip = chipHTML(viewOf(detail.item, opts.clock).status); painted.body = detailBody(detail.item, opts); }
    }
    // An open status list is never rewritten under the pointer: new counts wait until it closes.
    const selectOpen = host.querySelector?.("#compute-ov-status")?.matches?.(":focus") === true;
    if (!selectOpen || focus) setHTML("filters", values.filters);
    if (setHTML("list", values.list)) refreshAges();
    let target = focus ? focusTarget(host, focus) : key ? host.querySelector(key) : null;
    // The focused control went away (a "Try again" that worked): focus lands on the section's heading, not on the page.
    if (!target && key) target = host.querySelector("#compute-overview");
    if (target && target !== host.ownerDocument?.activeElement) target.focus?.({ preventScroll: !focus });
  }
  // Repaint only the open panel's body and chip, and only when what they say changed, so a focused heading or Close button keeps its node.
  function paintDetail() {
    const host = ctx.host(), panel = host?.querySelector?.("[data-ov-panel]");
    const body = panel?.querySelector?.("[data-ov-detail-body]");
    if (!panel || !body || !detail) { paint(); return; }
    panel.setAttribute?.("aria-busy", String(detailBusy));
    const opts = { loading: detailBusy, failed: detailFailed, manage: data?.can_manage, clock: clock() };
    const v = viewOf(detail.item, opts.clock);
    const chip = panel.querySelector?.("[data-ov-chip-slot]");
    const chipMarkup = chipHTML(v.status);
    if (chip && painted.chip !== chipMarkup) { painted.chip = chipMarkup; chip.innerHTML = chipMarkup; }
    const markup = detailBody(detail.item, opts);
    if (painted.body === markup) return;
    painted.body = markup;
    const control = body.querySelector?.(":focus"), key = control ? focusKey(control) : "";
    body.innerHTML = markup;
    if (key) host.querySelector(key)?.focus?.({ preventScroll: true });
  }

  // A change worth saying aloud, once: a status that changed since the last read. The first read says nothing.
  function changes(next) {
    if (!previous) return "";
    const said = [];
    for (const item of next.items) {
      const before = previous.get(`${item.kind}:${item.id}`);
      if (before && before !== item.status.code) said.push(`${item.name} is now ${item.status.label}.`);
    }
    return said.slice(0, 3).join(" ");
  }

  // ``within`` skips a read when the data is younger than that (milliseconds): the screen's own reloads (a Deck
  // refresh after every app event) must not turn into a read each; a button press never passes it.
  async function load({ quiet = false, say = false, within = 0 } = {}) {
    const page = ctx.epoch();
    if (loading || (within && data && (mono() - fetchedMono < within || ctx.doc?.hidden))) return; // the screen's own reloads never read for a hidden tab
    loading = true;
    if (say && data === null && loadError) announce("Trying again…"); // the one thing said at the start of a retry
    if (!quiet) paint();
    let result = null, failure = null;
    try { result = await ctx.request(BASE); } catch (error) { failure = error; }
    loading = false;
    if (!ctx.valid(page)) return;
    if (failure?.status === 401 && ctx.denied(failure)) return;
    if (!failure && Array.isArray(result?.items)) {
      const said = changes(result);
      data = result; fetchedAt = ctx.now(); fetchedMono = mono(); tries = 0; refreshFailed = false; failedSaid = false; loadError = "";
      seenSignature = ctx.workspaceSignature?.() || "";
      previous = new Map(result.items.map(item => [`${item.kind}:${item.id}`, item.status.code]));
      if (kind !== "all" && !result.items.some(item => item.kind === kind)) kind = "all";
      if (status !== "all" && status !== "attention" && !result.items.some(item => item.status.code === status)) status = "all";
      // The attention filter with nothing left to show would read "All statuses" over an empty list: back to all.
      if (status === "attention" && !items().some(item => item.health?.state === "attention")) status = "all";
      const gone = detail && !find(detail.id);
      if (gone) { announce("That item is no longer in the overview."); detail = null; detailBusy = false; }
      paint(gone ? "opener" : "");
      if (said) announce(said); else if (say) announce(`Overview updated. ${summaryText(items()) || "Nothing here yet."}`);
    } else if (data !== null) {
      refreshFailed = true; paint();
      // Said once when the failure starts, not at every poll while it lasts.
      // The alert above the fold says it (once, when its text appears); a second spoken line would be the same news twice.
      failedSaid = true;
    } else {
      tries++;
      // Said again each time, so pressing "Try again" visibly does something even when it fails again.
      loadError = LOAD_ERROR; paint();  // the alert speaks (once: its text never changes); the count is plain text beside it
    }
    if (detail && data && !detailBusy && !failure) void loadDetail(detail.kind, detail.id, { quiet: true });
    schedule();
  }

  async function loadDetail(detailKind, id, { quiet = false } = {}) {
    const page = ctx.epoch();
    if (!quiet) { detailBusy = true; detailFailed = false; paint("detail"); announce("Loading details…"); }
    let result = null, failure = null;
    try { result = await ctx.request(`${BASE}/${enc(detailKind)}/${enc(id)}`); } catch (error) { failure = error; }
    if (!ctx.valid(page) || !detail || detail.id !== id) return;
    if (failure?.status === 401 && ctx.denied(failure)) return;
    detailBusy = false;
    if (!failure && result?.item) { detail = { kind: detailKind, id, item: result.item }; detailFailed = false; }
    else if (failure?.status === 404) { detail = null; paint("opener"); announce("That item is no longer available."); return; }
    else {
      // Said once when it starts failing, not at every poll while it lasts.
      if (!detailFailed) announce(DETAIL_ERROR);
      detailFailed = true;
    }
    paintDetail();
  }

  function schedule() {
    if (timer !== null) { timers.clearTimeout(timer); timer = null; }
    if (paused || (!data && !loadError)) return;
    timer = timers.setTimeout(tick, interval() * 1000);
  }
  function tick() {
    timer = null;
    if (ctx.doc?.hidden) { schedule(); return; }
    void load({ quiet: true });
  }
  // The clock: ages and the "as of" line move on their own, in place, so a row never shows an age the clock has passed.
  function scheduleClock() {
    if (clockTimer !== null) timers.clearTimeout(clockTimer);
    clockTimer = timers.setTimeout(tickClock, CLOCK_SECONDS * 1000);
  }
  function tickClock() {
    clockTimer = null;
    if (!ctx.doc?.hidden && data) {
      const host = ctx.host();
      setText(host?.querySelector?.("[data-ov-meta]"), metaText());
      setText(host?.querySelector?.("[data-ov-summary]"), summaryLine());
      refreshAges();
      // The workspaces list moved (created, renamed, discarded): read the overview again, once.
      const signature = ctx.workspaceSignature?.() || "";
      if (signature !== seenSignature && !loading) { seenSignature = signature; void load({ quiet: true }); }
    }
    scheduleClock();
  }
  function refreshAges() {
    const host = ctx.host();
    if (!host || !data) return;
    const now = clock();
    let rebuilt = false;
    for (const row of host.querySelectorAll?.("[data-ov-slot='list'] .compute-ov-row") || []) {
      const item = find(row.dataset.ovId);
      if (!item) continue;
      const v = viewOf(item, now);
      if (row.dataset.status !== v.status.code) { rebuilt = true; break; }
      setText(row.querySelector("[data-ov-health]"), `${healthLine(item, v.seen)}${blockLine({ ...item, health: v.health })}`);
    }
    if (rebuilt) { paint(); return; }
    if (detail && painted.panelKey === panelKey()) paintDetail();
  }
  function visibility() {
    if (ctx.doc?.hidden || !data || loading || paused) return;
    if (mono() - fetchedMono >= interval() * 1000) { if (timer !== null) { timers.clearTimeout(timer); timer = null; } void load({ quiet: true }); }
  }

  function open(id, itemKind) {
    const item = find(id);
    if (!item) return;
    opener = id;
    detail = { kind: itemKind || item.kind, id, item };
    void loadDetail(detail.kind, id);
  }
  function close() {
    detail = null; detailBusy = false; detailFailed = false;
    paint("opener");
  }
  const showing = () => announce(`Showing ${filterItems(items(), { kind, status }).length} of ${items().length}.`);

  function click(event) {
    const summary = event.target?.closest?.("[data-ov-fold-summary]");
    if (summary && summary.closest?.(".compute-ov")) { foldOpen = !isOpen(); return true; }
    const filter = event.target?.closest?.("[data-ov-kind-filter]");
    if (filter) {
      kind = filter.dataset.ovKindFilter;
      // A status that does not exist for this kind would show nothing: back to all statuses.
      const here = items().filter(item => kind === "all" || item.kind === kind);
      if (status !== "all" && !here.some(item => status === "attention" ? item.health?.state === "attention" : item.status.code === status)) status = "all";
      paint(); showing(); return true;
    }
    const target = event.target?.closest?.("[data-ov-action]");
    if (!target || !target.closest?.(".compute-ov")) return false;
    if (target.getAttribute?.("aria-disabled") === "true") return true;
    const action = target.dataset.ovAction;
    if (action === "refresh") { void load({ say: true }); return true; }
    if (action === "detail") { open(target.dataset.id, target.dataset.kind); return true; }
    if (action === "close") { close(); return true; }
    if (action === "clear") { kind = "all"; status = "all"; paint("status"); announce(`Filters cleared. Showing ${items().length} of ${items().length}.`); return true; }
    if (action === "attention") { status = status === "attention" ? "all" : "attention"; paint(); showing(); return true; }
    if (action === "pause") {
      paused = !paused;
      paint();
      if (paused) { if (timer !== null) { timers.clearTimeout(timer); timer = null; } announce("Automatic updates paused. Statuses and ages keep counting from the last read."); }
      else { announce("Automatic updates resumed."); void load({ quiet: true }); }
      return true;
    }
    if (action === "settings") {
      const heading = ctx.host()?.querySelector?.("#compute-workspaces");
      heading?.scrollIntoView?.({ block: "start" });
      heading?.focus?.();
      return true;
    }
    return false;
  }
  function change(event) {
    const control = event.target;
    if (!control?.matches?.("[data-ov-status]")) return false;
    status = control.value;
    paint("status");
    showing();
    return true;
  }
  function keydown(event) {
    if (event.key !== "Escape" || !detail || !event.target?.closest?.(".compute-ov")) return false;
    event.preventDefault?.();
    close();
    return true;
  }
  function start() {
    ctx.doc?.addEventListener?.("visibilitychange", visibility);
    schedule();
    scheduleClock();
  }
  function reset() {
    if (timer !== null) { timers.clearTimeout(timer); timer = null; }
    if (clockTimer !== null) { timers.clearTimeout(clockTimer); clockTimer = null; }
    ctx.doc?.removeEventListener?.("visibilitychange", visibility);
    data = null; loadError = ""; loading = false; fetchedAt = 0; fetchedMono = 0; tries = 0; refreshFailed = false; failedSaid = false; paused = false; foldOpen = null; kind = "all"; status = "all"; detail = null; detailBusy = false; detailFailed = false; opener = ""; previous = null; seenSignature = ""; painted = {}; announced = "";
  }
  return { html, holdFocus, carry, load, start, click, change, keydown, reset, get state() { return { data, loadError, loading, kind, status, detail, refreshFailed, paused, timer }; } };
}
