import { escape as e } from "./api.js";

// The Compute overview (docs/system/compute-overview.md): every machine, runner and cloud workspace with a status in
// plain words, when Commonplace last heard from it, usage where something reported it, and what blocks new work.
// One shared module serves the Classic screen and the Deck Management tab: createCompute (compute.js) embeds
// html() and forwards clicks, changes and keys. This slice only reads. Every sentence comes from the server, which
// never says "running" or "online" without a fresh report; nothing here adds a claim of its own.
// Data refreshes repaint only this section, so typing in another section of the screen is never disturbed.

const BASE = "/api/compute/overview";
const enc = encodeURIComponent;
const KIND_WORDS = { runner: ["Runner", "Runners"], machine: ["Machine", "Machines"], workspace: ["Cloud workspace", "Cloud workspaces"] };
const KIND_ORDER = ["runner", "machine", "workspace"];
const esc = value => typeof CSS !== "undefined" && CSS.escape ? CSS.escape(value) : String(value).replace(/["\\]/g, "\\$&");
const LOAD_ERROR = "The overview could not be loaded. Nothing is shown rather than a guess.";
const DETAIL_ERROR = "The details could not be loaded.";
const DEFAULT_REFRESH = 30;

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
/** One plain sentence about everything, e.g. "2 runners, 1 machine, 3 cloud workspaces. 1 needs attention." */
export function summaryText(items) {
  if (!items.length) return "";
  const { kinds, attention } = filterCounts(items);
  const parts = KIND_ORDER.filter(kind => kinds[kind]).map(kind => plural(kinds[kind], KIND_WORDS[kind][0].toLowerCase(), KIND_WORDS[kind][1].toLowerCase()));
  const need = attention ? ` ${attention} ${attention === 1 ? "needs" : "need"} attention.` : " Nothing needs attention.";
  return `${parts.join(", ")}.${need}`;
}

const timeOf = iso => {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
};

const healthLine = item => {
  if (item.last_seen) return item.last_seen.label;
  if (item.kind === "runner") return "No signal yet: it has never contacted Commonplace.";
  if (item.kind === "machine") return "Not checked live: Commonplace does not probe target machines.";
  return "No report from a machine yet.";
};
// The detail view's own wording for the signal row and for what stops new work, by kind.
const seenRow = item => {
  if (item.last_seen?.when) return item.kind === "runner" ? ["Last seen", `${item.last_seen.when}. That is its last contact with Commonplace, not a live check.`] : ["Last report", item.last_seen.when];
  return [item.kind === "workspace" ? "Last report" : item.kind === "machine" ? "Live check" : "Last seen", healthLine(item)];
};
const BLOCK_LABEL = { runner: "Blocking new work", workspace: "What stops it starting", machine: "Blocking new work" };
const blockLine = item => {
  const n = item.health?.blockers?.length || 0;
  return n ? ` · ${plural(n, "thing blocks", "things block")} new work` : "";
};

export function rowHTML(item) {
  const status = item.status, name = e(item.name);
  return `<li class="compute-row compute-ov-row" data-ov-id="${e(item.id)}" data-ov-kind="${e(item.kind)}" data-status="${e(status.code)}">`
    + `<header><h4 class="compute-ov-name">${name}</h4><span class="compute-state compute-ov-chip" data-tone="${e(status.tone)}">${e(status.label)}</span></header>`
    + (item.kind === "workspace" && item.subtitle ? `<p class="compute-quiet compute-ov-sub">Image: ${e(item.subtitle)}</p>` : "")
    + `<p class="compute-ov-sentence">${e(status.detail)}</p>`
    + `<p class="compute-ov-health">${e(healthLine(item))}${e(blockLine(item))}</p>`
    + `<p class="compute-quiet compute-ov-usage">${e(item.usage?.summary || "")}</p>`
    + `<div class="compute-actions"><button type="button" class="btn" data-ov-action="detail" data-kind="${e(item.kind)}" data-id="${e(item.id)}" aria-label="Status and activity of ${name}">Status and activity</button></div></li>`;
}

const dl = pairs => `<dl>${pairs.filter(([, value]) => value !== "" && value != null).map(([key, value]) => `<dt>${e(key)}</dt><dd>${value}</dd>`).join("")}</dl>`;
const usageRows = item => {
  const u = item.usage || {};
  if (item.kind === "runner") return [["Runs", e(u.runs?.text)], ["Machine resources", e(u.resources?.text)], ["Spend", e(u.spend?.text)]];
  if (item.kind === "workspace") return [["Limits", e(u.limits?.text)], ["Measured use", e(u.measured?.text)]];
  return [["Cloud workspaces here", e(item.usage?.summary)], ["Measured use", e(u.measured?.text)]];
};

const chipHTML = item => `<span class="compute-state compute-ov-chip" data-tone="${e(item.status.tone)}" data-ov-chip>${e(item.status.label)}</span>`;
const closeButton = '<button type="button" class="btn" data-ov-action="close">Close</button>';
/** The panel: a heading that keeps its node (so focus on it is never disturbed) and a body that is repainted. */
export function detailHTML(item, { loading = false, failed = false, manage = false } = {}) {
  return `<section class="compute-editor compute-ov-panel" aria-labelledby="compute-ov-heading"${loading ? ' aria-busy="true"' : ""} data-ov-panel>`
    + `<header><h3 id="compute-ov-heading" tabindex="-1">${e(item.name)}</h3><span data-ov-chip-slot>${chipHTML(item)}</span></header><div data-ov-detail-body>${detailBody(item, { loading, failed, manage })}</div></section>`;
}
export function detailBody(item, { loading = false, failed = false, manage = false } = {}) {
  const close = closeButton;
  if (loading) return `<p class="compute-quiet">Loading details…</p><div class="compute-actions">${close}</div>`;
  const blockers = item.health?.blockers || [];
  const blocks = blockers.length ? `<ul class="compute-ov-list" role="list">${blockers.map(b => `<li>${e(b.text)}</li>`).join("")}</ul>` : "Nothing is blocking new work.";
  const issues = (item.health?.issues || []).length ? `<ul class="compute-ov-list" role="list">${item.health.issues.map(text => `<li>${e(text)}</li>`).join("")}</ul>` : "";
  const activity = (item.activity || []).length
    ? `<ul class="compute-ov-list compute-ov-activity" role="list">${item.activity.map(a => `<li>${e(a.text)}${a.age_s == null ? "" : ` <span class="compute-quiet">(${e(agoText(a.age_s))})</span>`}</li>`).join("")}</ul>`
    : '<p class="compute-quiet">No recent activity.</p>';
  const change = manage && item.kind === "workspace" ? '<button type="button" class="btn" data-ov-action="settings">Change settings in Cloud workspaces</button>' : "";
  return `<p class="compute-ov-sentence">${e(item.status.detail)}</p>`
    + (failed ? `<p class="compute-error compute-ov-stale">${e(DETAIL_ERROR)} Showing what the list knew.</p>` : "")
    + `<h4 class="compute-ov-sub-h">Health</h4>${dl([[seenRow(item)[0], e(seenRow(item)[1])], [BLOCK_LABEL[item.kind] || "Blocking new work", blocks], ["Needs a person", issues]])}`
    + `<h4 class="compute-ov-sub-h">Usage</h4>${dl(usageRows(item))}`
    + `<h4 class="compute-ov-sub-h">Recent activity</h4>${activity}`
    + ((item.facts || []).length ? `<h4 class="compute-ov-sub-h">More about it</h4>${dl(item.facts.map(f => [f.label, e(f.value)]))}` : "")
    + `<div class="compute-actions">${close}${change}</div>`;
}

const agoText = seconds => {
  if (seconds < 5) return "just now";
  const [n, unit] = seconds < 60 ? [seconds, "second"] : seconds < 3600 ? [Math.floor(seconds / 60), "minute"] : seconds < 172800 ? [Math.floor(seconds / 3600), "hour"] : [Math.floor(seconds / 86400), "day"];
  return `${n} ${unit}${n === 1 ? "" : "s"} ago`;
};

/**
 * ctx: { request, epoch, valid(page), denied(failure), host(), rerender(), doc, now(), timers: { setTimeout, clearTimeout } }
 * Owns its state; compute.js embeds html() and forwards events.
 */
export function createOverview(ctx) {
  const timers = ctx.timers || globalThis;
  let data = null, loadError = "", loading = false, fetchedAt = 0, refreshFailed = false;
  let kind = "all", status = "all", detail = null, detailBusy = false, detailFailed = false, opener = "", timer = null, previous = null;
  const find = id => data?.items?.find(item => item.id === id) || null;
  const items = () => data?.items || [];
  const interval = () => Math.max(15, Number(data?.refresh_seconds) || DEFAULT_REFRESH);
  const root = () => ctx.host()?.querySelector?.("[data-ov-root]");
  const live = () => ctx.host()?.querySelector?.("[data-ov-announce]");
  const announce = text => { const node = live(); if (node) node.textContent = text; };

  function filtersHTML() {
    const { kinds, statuses, attention } = filterCounts(items());
    const shownKinds = KIND_ORDER.filter(k => kinds[k]);
    const kindButtons = shownKinds.length > 1
      ? `<div class="compute-ov-kinds" role="group" aria-label="Show">${[["all", `All (${items().length})`], ...shownKinds.map(k => [k, `${KIND_WORDS[k][1]} (${kinds[k]})`])].map(([value, label]) => `<button type="button" class="btn compute-ov-kind" data-ov-kind-filter="${value}" aria-pressed="${kind === value}">${e(label)}</button>`).join("")}</div>` : "";
    const options = [["all", "All statuses"], ...(attention ? [["attention", `Needs attention (${attention})`]] : []), ...Object.entries(statuses).map(([code, v]) => [code, `${v.label} (${v.count})`])];
    const select = `<label class="compute-ov-status-filter" for="compute-ov-status">Filter by status</label><select id="compute-ov-status" data-ov-status>${options.map(([value, label]) => `<option value="${e(value)}"${status === value ? " selected" : ""}>${e(label)}</option>`).join("")}</select>`;
    const active = kind !== "all" || status !== "all";
    return `<div class="compute-ov-filters">${kindButtons}<div class="compute-ov-select">${select}</div>${active ? '<button type="button" class="btn" data-ov-action="clear">Clear filters</button>' : ""}</div>`;
  }

  function listHTML() {
    const shown = filterItems(items(), { kind, status });
    if (!shown.length) return `<p class="compute-empty">No machines, runners or cloud workspaces match these filters.</p><div class="compute-actions"><button type="button" class="btn" data-ov-action="clear">Clear filters</button></div>`;
    return KIND_ORDER.map(k => {
      const rows = shown.filter(item => item.kind === k);
      return rows.length ? `<section class="compute-ov-group" aria-labelledby="compute-ov-group-${k}"><h3 id="compute-ov-group-${k}">${e(KIND_WORDS[k][1])} <span class="compute-quiet">(${rows.length})</span></h3><ul class="compute-list compute-ov-list-rows">${rows.map(rowHTML).join("")}</ul></section>` : "";
    }).join("");
  }

  function emptyHTML() {
    return data.can_manage
      ? '<p class="compute-empty">Nothing here yet. Set up a runner machine, add a target machine under Advanced, or create a cloud workspace below, and it will show up here with its status.</p>'
      : '<p class="compute-empty">There are no cloud workspaces yet. The workspace owner can create one.</p>';
  }

  function inner() {
    if (data === null) {
      return loadError
        ? `<p class="compute-error" role="alert">${e(loadError)}</p><div class="compute-actions"><button type="button" class="btn" data-ov-action="refresh">Try again</button></div>`
        : '<p class="compute-empty">Loading the overview…</p>';
    }
    const stale = refreshFailed ? `<p class="compute-error compute-ov-stale">Could not refresh. Showing the data from ${e(timeOf(data.as_of))}; it may be out of date.</p>` : "";
    const hidden = data.hidden ? `<p class="compute-quiet compute-ov-hidden">${e(data.hidden.text)}</p>` : "";
    const notes = [data.executor?.available === false && data.can_manage ? data.executor.text : "", ...Object.entries(data.truncated || {}).map(([k, n]) => `Showing the newest ${items().filter(i => i.kind === k).length} of ${n} ${KIND_WORDS[k][1].toLowerCase()}.`)].filter(Boolean);
    const extra = notes.map(text => `<p class="compute-quiet">${e(text)}</p>`).join("");
    const panel = detail ? detailHTML(detail.item, { loading: detailBusy, failed: detailFailed, manage: data.can_manage }) : "";
    if (!items().length) return `${stale}${hidden}${extra}${emptyHTML()}`;
    const rest = detail ? "" : `${filtersHTML()}${listHTML()}`;
    return `${stale}<p class="compute-ov-summary">${e(summaryText(items()))}</p>${hidden}${extra}${panel}${rest}`;
  }

  function metaText() {
    if (data === null) return "";
    return `As of ${timeOf(data.as_of)}. It refreshes every ${interval()} seconds while this page is open, or press Update overview.`;
  }

  const topHTML = () => `<header><h2 id="compute-overview" tabindex="-1">Overview</h2><button type="button" class="btn" data-ov-action="refresh"${loading ? ' aria-disabled="true"' : ""}>${loading && data ? "Refreshing…" : "Update overview"}</button></header>`
    + '<p class="compute-plain">Every machine, runner and cloud workspace Compute knows about. “Last seen” is the last time something was heard from it, not a live check, and a number nobody reported says so instead of showing zero.</p>';

  function html() {
    return `<section class="compute-inventory compute-wide compute-ov" aria-labelledby="compute-overview" data-ov><div data-ov-top>${topHTML()}</div>`
      + `<p class="compute-quiet compute-ov-meta" data-ov-meta>${e(metaText())}</p><p class="compute-sr" role="status" aria-live="polite" data-ov-announce></p><div data-ov-root>${inner()}</div></section>`;
  }

  // Repaint this section only. Focus on a control inside it survives: the same control is found again by its data attributes.
  function paint(focus = "") {
    const host = ctx.host(), node = root();
    if (!node) { ctx.rerender(); return; }
    const control = host.querySelector?.(".compute-ov :focus");
    const key = control ? focusKey(control) : "";
    const top = host.querySelector("[data-ov-top]"), meta = host.querySelector("[data-ov-meta]");
    if (top) top.innerHTML = topHTML();
    if (meta) meta.textContent = metaText();
    node.innerHTML = inner();
    const target = focus ? focusTarget(host, focus) : key ? host.querySelector(key) : null;
    target?.focus?.({ preventScroll: !focus });
  }
  // Repaint only the open panel's body and chip, so a focused heading or Close button keeps its node.
  function paintDetail() {
    const host = ctx.host(), panel = host?.querySelector?.("[data-ov-panel]");
    const body = panel?.querySelector?.("[data-ov-detail-body]");
    if (!panel || !body || !detail) { paint(); return; }
    const control = body.querySelector?.(":focus"), key = control ? focusKey(control) : "";
    panel.setAttribute?.("aria-busy", String(detailBusy));
    const slot = panel.querySelector?.("[data-ov-chip-slot]");
    if (slot) slot.innerHTML = chipHTML(detail.item);
    body.innerHTML = detailBody(detail.item, { loading: detailBusy, failed: detailFailed, manage: data?.can_manage });
    if (key) host.querySelector(key)?.focus?.({ preventScroll: true });
  }
  function focusKey(control) {
    const d = control.dataset || {};
    if (control.id === "compute-ov-heading") return "#compute-ov-heading";
    if (control.id === "compute-overview") return "#compute-overview";
    if (control.id === "compute-ov-status") return "#compute-ov-status";
    if (d.ovKindFilter) return `[data-ov-kind-filter="${d.ovKindFilter}"]`;
    if (d.ovAction === "detail" && d.id) return `[data-ov-action="detail"][data-id="${esc(d.id)}"]`;
    if (d.ovAction) return `[data-ov-action="${d.ovAction}"]`;
    return "";
  }
  function focusTarget(host, focus) {
    if (focus === "detail") return host.querySelector("#compute-ov-heading");
    if (focus === "opener") return (opener && host.querySelector(`[data-ov-action="detail"][data-id="${esc(opener)}"]`)) || host.querySelector("#compute-overview");
    if (focus === "status") return host.querySelector("#compute-ov-status");
    return host.querySelector(`[data-ov-action="${focus}"]`);
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
    if (loading || (within && data && ctx.now() - fetchedAt < within)) return;
    loading = true;
    if (!quiet) paint();
    let result = null, failure = null;
    try { result = await ctx.request(BASE); } catch (error) { failure = error; }
    loading = false;
    if (!ctx.valid(page)) return;
    if (failure?.status === 401 && ctx.denied(failure)) return;
    if (!failure && Array.isArray(result?.items)) {
      const said = changes(result);
      data = result; fetchedAt = ctx.now(); refreshFailed = false; loadError = "";
      previous = new Map(result.items.map(item => [`${item.kind}:${item.id}`, item.status.code]));
      if (kind !== "all" && !result.items.some(item => item.kind === kind)) kind = "all";
      if (status !== "all" && status !== "attention" && !result.items.some(item => item.status.code === status)) status = "all";
      if (detail && !find(detail.id)) { announce("That item is no longer in the overview."); detail = null; detailBusy = false; }
      paint();
      if (said) announce(said); else if (say) announce(`Overview updated. ${summaryText(result.items) || "Nothing here yet."}`);
    } else if (data !== null) {
      refreshFailed = true; paint();
      announce("The overview could not be refreshed. The data shown is out of date.");
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
    if (!data && !loadError) return;
    timer = timers.setTimeout(tick, interval() * 1000);
  }
  function tick() {
    timer = null;
    if (ctx.doc?.hidden) { schedule(); return; }
    void load({ quiet: true });
  }
  function visibility() {
    if (ctx.doc?.hidden || !data || loading) return;
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

  function click(event) {
    const filter = event.target?.closest?.("[data-ov-kind-filter]");
    if (filter) { kind = filter.dataset.ovKindFilter; paint(); announce(`Showing ${filterItems(items(), { kind, status }).length} items.`); return true; }
    const target = event.target?.closest?.("[data-ov-action]");
    if (!target || !target.closest?.(".compute-ov")) return false;
    if (target.getAttribute?.("aria-disabled") === "true") return true;
    const action = target.dataset.ovAction;
    if (action === "refresh") { void load({ say: true }); return true; }
    if (action === "detail") { open(target.dataset.id, target.dataset.kind); return true; }
    if (action === "close") { close(); return true; }
    if (action === "clear") { kind = "all"; status = "all"; paint("status"); announce(`Filters cleared. Showing ${items().length} items.`); return true; }
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
    announce(`Showing ${filterItems(items(), { kind, status }).length} items.`);
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
  }
  function reset() {
    if (timer !== null) { timers.clearTimeout(timer); timer = null; }
    ctx.doc?.removeEventListener?.("visibilitychange", visibility);
    data = null; loadError = ""; loading = false; fetchedAt = 0; refreshFailed = false; kind = "all"; status = "all"; detail = null; detailBusy = false; detailFailed = false; opener = ""; previous = null;
  }
  return { html, load, start, click, change, keydown, reset, get state() { return { data, loadError, loading, kind, status, detail, refreshFailed, timer }; } };
}
