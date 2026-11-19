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
const FIRST_RUN = "Nothing here yet. Create a cloud workspace below. A machine can be chosen later.";
const DEFAULT_REFRESH = 30;
const CLOCK_SECONDS = 10;
const NARROW = "(max-width: 699px)";
// One short name per blocker code, so a row names what blocks new work instead of only counting it.
const BLOCK_SHORT = {
  no_machine: "no machine chosen", machine_not_verified: "its machine is not verified", private_image: "private image", not_available: "starting is not available yet",
  revoked: "removed", disabled: "turned off", not_approved: "not approved", draining: "draining", never_connected: "never connected", offline: "offline", full: "may be full",
  hourly_cap: "hourly limit reached", not_verified: "not verified", verification_expired: "verification expired",
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
  const { nowS = null, thresholds = {}, failedAt = "" } = clock;
  let status = item.status, health = item.health;
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
      status = { code: "unknown", tone: "quiet", label: "Unknown", detail: `Its last report said ${word} ${agoText(age)} and nothing newer has arrived, so it may not be ${word.toLowerCase()} any more.`, next: "Nothing newer has been reported. Check the machine that carries it." };
      overridden = true;
    } else if (item.kind === "runner" && ["online", "busy"].includes(status.code)) {
      const was = status.label;
      if (age > (thresholds.red_after_s || 900)) { status = { code: "offline", tone: "bad", label: "Offline", detail: `Nothing has been heard from it for ${agoText(age).replace(" ago", "")}. It read ${was} when this was last checked; runs it started may still be going and new card runs wait.`, next: status.next }; overridden = true; }
      else if (age > (thresholds.green_within_s || 180)) { status = { code: "slow", tone: "wait", label: "Slow to report", detail: `Later than usual: its last contact was ${agoText(age)}. It read ${was} when this was last checked.`, next: status.next }; overridden = true; }
    }
  }
  if (overridden) health = { ...health, state: "attention" };
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
  return `${parts.join(", ")}.${need}`;
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
  const list = item.health?.blockers || [];
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
  const blocks = blockers.length ? `<ul class="compute-ov-list" role="list">${blockers.map(b => `<li>${e(b.text)}</li>`).join("")}</ul>` : "Nothing is blocking new work.";
  const issues = (v.health?.issues || []).length ? `<ul class="compute-ov-list" role="list">${v.health.issues.map(text => `<li>${e(text)}</li>`).join("")}</ul>` : "";
  const activity = (item.activity || []).length
    ? `<ul class="compute-ov-list compute-ov-activity" role="list">${item.activity.map(a => `<li>${e(a.text)}${a.age_s == null ? "" : ` <span class="compute-quiet">(${e(agoText(a.age_s))})</span>`}</li>`).join("")}</ul>`
    : '<p class="compute-quiet">No recent activity.</p>';
  const change = manage && item.kind === "workspace" ? '<div class="compute-actions"><button type="button" class="btn" data-ov-action="settings">Change settings in Cloud workspaces</button></div>' : "";
  const [seenLabel, seenValue] = seenRow(item, v.seen);
  return `<p class="compute-ov-sentence">${e(v.status.detail)}</p>`
    + (v.status.next ? `<p class="compute-ov-next">Next step: ${e(v.status.next)}</p>` : "")
    + (failed ? `<p class="compute-error compute-ov-stale">${e(DETAIL_ERROR)} Showing what the list knew.</p>` : "")
    + `<h4 class="compute-ov-sub-h">Health</h4>${dl([[seenLabel, `<span data-ov-seen>${e(seenValue)}</span>`], ["Blocking new work", blocks], ["Needs a person", issues]])}`
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
  let data = null, loadError = "", loading = false, fetchedAt = 0, refreshFailed = false, failedSaid = false, paused = false, foldOpen = null;
  let kind = "all", status = "all", detail = null, detailBusy = false, detailFailed = false, opener = "", timer = null, clockTimer = null, previous = null, seenSignature = "";
  // What each slot of the section holds right now, so a repaint writes only what changed.
  let painted = {}, announced = "";
  const find = id => data?.items?.find(item => item.id === id) || null;
  const root = () => ctx.host()?.querySelector?.("[data-ov-root]");
  const slot = name => ctx.host()?.querySelector?.(`[data-ov-slot="${name}"]`);
  const live = () => ctx.host()?.querySelector?.("[data-ov-announce]");
  const announce = text => { announced = text; const node = live(); if (node) node.textContent = text; };
  const interval = () => Math.max(15, Number(data?.refresh_seconds) || DEFAULT_REFRESH);
  const clock = () => ({ nowS: ctx.now() / 1000, thresholds: data?.thresholds || {}, failedAt: refreshFailed && data ? timeOf(data.as_of) : "" });
  const items = () => (data?.items || []).map(item => withView(item, clock()));
  const narrow = () => ctx.doc?.defaultView?.matchMedia?.(NARROW)?.matches === true;
  const folded = () => foldOpen === null ? !narrow() : foldOpen;

  function filtersHTML(all) {
    const { kinds, statuses, attention } = filterCounts(all);
    const shownKinds = KIND_ORDER.filter(k => kinds[k]);
    const kindButtons = shownKinds.length > 1
      ? `<div class="compute-ov-kinds" role="group" aria-label="Filter by kind">${[["all", `All (${all.length})`], ...shownKinds.map(k => [k, `${KIND_WORDS[k][1]} (${kinds[k]})`])].map(([value, label]) => `<button type="button" class="btn compute-ov-kind" data-ov-kind-filter="${value}" aria-pressed="${kind === value}">${e(label)}</button>`).join("")}</div>` : "";
    const needs = attention ? `<button type="button" class="btn compute-ov-kind" data-ov-action="attention" aria-pressed="${status === "attention"}">${attention} ${attention === 1 ? "needs" : "need"} attention</button>` : "";
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

  function emptyHTML() {
    if (!data.can_manage) return '<p class="compute-empty">There are no cloud workspaces yet. The workspace owner can create one.</p>';
    // The workspaces list is the source of truth for cloud workspaces: while it holds records this overview has not caught up.
    return ctx.workspaceSignature?.() ? '<p class="compute-empty">Your cloud workspaces are listed below. The overview is catching up.</p>' : `<p class="compute-empty">${FIRST_RUN}</p>`;
  }

  const stateHTML = () => data === null
    ? (loadError ? `<p class="compute-error" role="alert">${e(loadError)}</p><div class="compute-actions"><button type="button" class="btn" data-ov-action="refresh">Try again</button></div>` : '<p class="compute-empty">Loading the overview…</p>')
    : (!data.items.length ? emptyHTML() : "");

  function noteText() {
    if (data === null) return "";
    const hidden = data.hidden ? data.hidden.text : "";
    const notes = [data.executor?.available === false && data.can_manage ? data.executor.text : "", ...Object.entries(data.truncated || {}).map(([k, n]) => `Showing the newest ${data.items.filter(i => i.kind === k).length} of ${n} ${KIND_WORDS[k][1].toLowerCase()}.`)].filter(Boolean);
    return [hidden, ...notes].join("\n");
  }
  const notesHTML = () => noteText().split("\n").filter(Boolean).map(text => `<p class="compute-quiet">${e(text)}</p>`).join("");

  function summaryLine() {
    if (data === null) return loadError ? "The overview could not be loaded." : "Loading the overview…";
    return summaryText(items()) || (ctx.workspaceSignature?.() ? "The overview is catching up." : data.can_manage ? "Nothing here yet." : "No cloud workspaces yet.");
  }

  function metaText() {
    if (data === null) return "";
    const as = epochOf(data.as_of);
    const age = as === null ? "" : `Updated ${agoText(Math.max(0, ctx.now() / 1000 - as))} (as of ${timeOf(data.as_of)}). `;
    return paused ? `${age}Updates are paused. Use Refresh above to read again.` : `${age}It refreshes every ${interval()} seconds while this tab is visible, or use Refresh above.`;
  }

  const staleText = () => refreshFailed && data ? `Could not refresh. Showing the data from ${timeOf(data.as_of)}; it may be out of date.` : "";
  const topHTML = () => `<header><h2 id="compute-overview" tabindex="-1">Overview</h2><button type="button" class="btn compute-ov-pause" data-ov-action="pause">${paused ? "Resume updates" : "Pause updates"}</button></header>`
    + '<p class="compute-plain">Every machine, runner and cloud workspace Compute knows about. “Last seen” is the last time something was heard from it, not a live check, and a number nobody reported says so instead of showing zero.</p>';

  function panelKey() { return detail ? `${detail.kind}:${detail.id}` : ""; }
  function slots() {
    const all = data ? items() : [];
    const open = Boolean(detail) && data !== null;
    return {
      stale: staleText(),
      state: stateHTML(),
      notes: notesHTML(),
      panel: open ? "keep" : "",
      filters: data && all.length && !open ? filtersHTML(all) : "",
      list: data && all.length && !open ? listHTML(all) : "",
    };
  }
  function inner(values = slots()) {
    painted = { ...values, panelKey: panelKey() };
    const opts = { loading: detailBusy, failed: detailFailed, manage: data?.can_manage, clock: clock() };
    const panel = detail && data ? detailHTML(detail.item, opts) : "";
    if (detail && data) { painted.chip = chipHTML(viewOf(detail.item, opts.clock).status); painted.body = detailBody(detail.item, opts); }
    return `<p class="compute-error compute-ov-stale" role="alert" data-ov-stale${values.stale ? "" : " hidden"}>${e(values.stale)}</p>`
      + `<div data-ov-slot="state">${values.state}</div><div data-ov-slot="notes">${values.notes}</div>`
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
      if (old && fresh && old !== fresh && typeof fresh.replaceWith === "function") fresh.replaceWith(old);
    };
  }

  function html() {
    return `<section class="compute-inventory compute-wide compute-ov" aria-labelledby="compute-overview" data-ov><div data-ov-top>${topHTML()}</div>`
      + `<details class="compute-ov-fold" data-ov-fold${folded() ? " open" : ""}><summary data-ov-fold-summary><span data-ov-summary>${e(summaryLine())}</span></summary>`
      + `<p class="compute-quiet compute-ov-meta" data-ov-meta>${e(metaText())}</p><p class="compute-sr" role="status" aria-live="polite" data-ov-announce>${e(announced)}</p><div data-ov-root>${inner()}</div></details></section>`;
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
    if (!host || !root()) { ctx.rerender(); return; }
    const control = host.querySelector?.(".compute-ov :focus");
    const key = control ? focusKey(control) : "";
    const values = slots();
    setText(host.querySelector("[data-ov-summary]"), summaryLine());
    setText(host.querySelector("[data-ov-meta]"), metaText());
    const top = host.querySelector("[data-ov-top]");
    if (top && painted.top !== topHTML()) { painted.top = topHTML(); top.innerHTML = topHTML(); }
    const banner = host.querySelector("[data-ov-stale]");
    if (banner) { setText(banner, values.stale); banner.hidden = !values.stale; }
    painted.stale = values.stale;
    setHTML("state", values.state); setHTML("notes", values.notes);
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
    setHTML("list", values.list);
    const target = focus ? focusTarget(host, focus) : key ? host.querySelector(key) : null;
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
    if (loading || (within && data && (ctx.now() - fetchedAt < within || ctx.doc?.hidden))) return; // the screen's own reloads never read for a hidden tab
    loading = true;
    if (!quiet) paint();
    let result = null, failure = null;
    try { result = await ctx.request(BASE); } catch (error) { failure = error; }
    loading = false;
    if (!ctx.valid(page)) return;
    if (failure?.status === 401 && ctx.denied(failure)) return;
    if (!failure && Array.isArray(result?.items)) {
      const said = changes(result);
      data = result; fetchedAt = ctx.now(); refreshFailed = false; failedSaid = false; loadError = "";
      seenSignature = ctx.workspaceSignature?.() || "";
      previous = new Map(result.items.map(item => [`${item.kind}:${item.id}`, item.status.code]));
      if (kind !== "all" && !result.items.some(item => item.kind === kind)) kind = "all";
      if (status !== "all" && status !== "attention" && !result.items.some(item => item.status.code === status)) status = "all";
      if (detail && !find(detail.id)) { announce("That item is no longer in the overview."); detail = null; detailBusy = false; }
      paint();
      if (said) announce(said); else if (say) announce(`Overview updated. ${summaryText(items()) || "Nothing here yet."}`);
    } else if (data !== null) {
      refreshFailed = true; paint();
      // Said once when the failure starts, not at every poll while it lasts.
      if (!failedSaid) { failedSaid = true; announce("The overview could not be refreshed. The rows now say when they were last read."); }
    } else {
      loadError = LOAD_ERROR; paint();
    }
    if (detail && data && !detailBusy && !failure) void loadDetail(detail.kind, detail.id, { quiet: true });
    schedule();
  }

  async function loadDetail(detailKind, id, { quiet = false } = {}) {
    const page = ctx.epoch();
    if (!quiet) { detailBusy = true; detailFailed = false; paint("detail"); }
    let result = null, failure = null;
    try { result = await ctx.request(`${BASE}/${enc(detailKind)}/${enc(id)}`); } catch (error) { failure = error; }
    if (!ctx.valid(page) || !detail || detail.id !== id) return;
    if (failure?.status === 401 && ctx.denied(failure)) return;
    detailBusy = false;
    if (!failure && result?.item) { detail = { kind: detailKind, id, item: result.item }; detailFailed = false; }
    else if (failure?.status === 404) { detail = null; paint("opener"); announce("That item is no longer available."); return; }
    else detailFailed = true;
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
    if (ctx.now() - fetchedAt >= interval() * 1000) { if (timer !== null) { timers.clearTimeout(timer); timer = null; } void load({ quiet: true }); }
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
    if (summary && summary.closest?.(".compute-ov")) { foldOpen = !folded(); return true; }
    const filter = event.target?.closest?.("[data-ov-kind-filter]");
    if (filter) { kind = filter.dataset.ovKindFilter; paint(); showing(); return true; }
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
      if (paused) { if (timer !== null) { timers.clearTimeout(timer); timer = null; } announce("Automatic updates paused."); }
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
    data = null; loadError = ""; loading = false; fetchedAt = 0; refreshFailed = false; failedSaid = false; paused = false; foldOpen = null; kind = "all"; status = "all"; detail = null; detailBusy = false; detailFailed = false; opener = ""; previous = null; seenSignature = ""; painted = {}; announced = "";
  }
  return { html, holdFocus, carry, load, start, click, change, keydown, reset, get state() { return { data, loadError, loading, kind, status, detail, refreshFailed, paused, timer }; } };
}
